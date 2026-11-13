"""Exercise the worker control plane over real isolated HTTP sockets."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

from services.worker_service.process_manager import ProcessManager
from services.worker_service.task_client import TaskClient
from services.worker_service.worker import Worker, health_server


class ControlPlane:
    def __init__(self, *, cancelling=False):
        self.cancelling = cancelling
        self.claimed = False
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.requests = []
        self.result = None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get("X-FactorPool-Internal-Token") != "isolated-test-token":
                    self.send_error(403)
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                action = self.path.rsplit("/", 1)[-1]
                with owner.lock:
                    owner.requests.append((action, request))
                    if action == "claim":
                        if owner.claimed:
                            self.send_response(204)
                            self.end_headers()
                            return
                        owner.claimed = True
                        answer = {"task_id": "12345678-1234-1234-1234-123456789abc", "attempt": 3,
                                  "lease_token": "87654321-4321-4321-4321-cba987654321",
                                  "worker_id": request["worker_id"], "snapshot": {"test": "fixture"},
                                  "lease_seconds": 30, "timeout_seconds": 10}
                    elif action == "heartbeat":
                        answer = {"status": "CANCELLING" if owner.cancelling else "RUNNING", "lease_seconds": 30}
                    elif action == "complete":
                        owner.result = request
                        owner.finished.set()
                        answer = {"status": "SUCCEEDED"}
                    elif action == "fail":
                        owner.result = request
                        owner.finished.set()
                        answer = {"status": request["status"]}
                    else:
                        answer = {}
                raw = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RunnerProcess(ProcessManager):
    def __init__(self, *, sleep=False):
        super().__init__(terminate_seconds=.1, poll_seconds=.01)
        self.sleep = sleep

    def run(self, directory, timeout, **controls):
        code = ("import json,pathlib,time; "
                f"d=pathlib.Path({str(directory)!r}); "
                "assert json.loads((d/'request.json').read_text())=={'test':'fixture'}; "
                "print('independent worker',flush=True); "
                + ("time.sleep(30); " if self.sleep else "")
                + "(d/'values.jsonl').write_text('{\"value\":1}\\n'); "
                "(d/'result.json').write_text(json.dumps({'metadata':{'value_count':1},'summary':{},'daily':[]}))")
        return super().run(directory, timeout, **controls, command=[sys.executable, "-u", "-c", code])


class LocalDataSink:
    def __init__(self):
        self.uploads = []

    def upload_values(self, task_id, attempt, token, path, expected_count, *, cancelled):
        if cancelled():
            raise InterruptedError()
        self.uploads.append((task_id, attempt, token, Path(path).read_text(), expected_count))
        return {"receipt": "isolated-receipt", "sha256": "isolated-sha", "count": expected_count}

    def close(self):
        pass


class ServiceBoundaryTests(unittest.TestCase):
    def run_worker(self, *, cancelling=False):
        control = ControlPlane(cancelling=cancelling)
        data = LocalDataSink()
        url = control.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                client = TaskClient(url, "isolated-test-token", timeout=1)
                worker = Worker(client, root=tmp, poll_seconds=.01, heartbeat_seconds=.02,
                                data_factory=lambda: data, process_manager=RunnerProcess(sleep=cancelling))
                live = health_server(worker, 0)
                thread = threading.Thread(target=worker.run)
                thread.start()
                try:
                    self.assertTrue(control.finished.wait(timeout=5), "Worker did not complete control-plane task")
                    response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f"http://127.0.0.1:{live.server_port}/health", timeout=1)
                    with response:
                        self.assertEqual(json.load(response)["status"], "UP")
                finally:
                    worker.stopping.set()
                    thread.join(timeout=5)
                    live.shutdown()
                    live.server_close()
                self.assertFalse(thread.is_alive())
                self.assertEqual(list(Path(tmp).iterdir()), [])
            return control, data
        finally:
            control.close()

    def test_real_http_claim_process_upload_complete(self):
        control, data = self.run_worker()
        self.assertEqual(control.result["receipt"], "isolated-receipt")
        self.assertEqual(control.result["sha256"], "isolated-sha")
        self.assertEqual(control.result["attempt"], 3)
        self.assertEqual(len(data.uploads), 1)
        self.assertTrue(any(action == "logs" and "independent worker" in body["text"]
                            for action, body in control.requests))
        self.assertFalse(any(action == "fail" for action, _ in control.requests))

    def test_real_http_cancel_never_uploads_or_completes(self):
        control, data = self.run_worker(cancelling=True)
        self.assertEqual(control.result["status"], "CANCELLED")
        self.assertEqual(data.uploads, [])
        self.assertFalse(any(action == "complete" for action, _ in control.requests))

    def test_internal_control_requires_token(self):
        control = ControlPlane()
        url = control.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                TaskClient(url, "wrong-test-token", timeout=1).claim("worker-test")
            self.assertEqual(control.requests, [])
        finally:
            control.close()

    def test_worker_stop_terminates_process_and_reports_interrupted(self):
        control = ControlPlane()
        url = control.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                worker = Worker(TaskClient(url, "isolated-test-token", timeout=1), root=tmp,
                                poll_seconds=.01, heartbeat_seconds=.02,
                                process_manager=RunnerProcess(sleep=True), data_factory=LocalDataSink)
                thread = threading.Thread(target=worker.run)
                thread.start()
                try:
                    deadline = time.monotonic() + 3
                    while not control.claimed and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(control.claimed)
                    worker.stopping.set()
                    self.assertTrue(control.finished.wait(timeout=3))
                finally:
                    worker.stopping.set()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(control.result["status"], "INTERRUPTED")
                self.assertEqual(list(Path(tmp).iterdir()), [])
        finally:
            control.close()


if __name__ == "__main__":
    unittest.main()
