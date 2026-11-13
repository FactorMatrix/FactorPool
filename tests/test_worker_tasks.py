"""Worker fault boundaries using real process groups and isolated HTTP fakes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from services.worker_service.process_manager import LogTail, ProcessManager, TaskStopped
from services.worker_service.task_client import LeaseLost, TaskClient
from services.worker_service.worker import LeaseHeartbeat, Worker


def task():
    return dict(task_id="12345678-1234-1234-1234-123456789abc", attempt=2,
                lease_token="87654321-4321-4321-4321-cba987654321", worker_id="worker-test",
                snapshot={"source_code": "test-only"}, lease_seconds=30, timeout_seconds=10)


class FakeControl:
    timeout = .05

    def __init__(self, *, cancel=False, complete_response_loss=False):
        self.cancel = cancel
        self.complete_response_loss = complete_response_loss
        self.completions = []
        self.failures = []
        self.tails = []

    def heartbeat(self, _):
        return dict(status="CANCELLING" if self.cancel else "RUNNING", lease_seconds=30)

    def logs(self, _, text):
        self.tails.append(text)

    def complete(self, identity, result, receipt):
        self.completions.append((identity, result, receipt))
        if self.complete_response_loss and len(self.completions) == 1:
            raise OSError("simulated response loss after publication")
        return {"status": "SUCCEEDED"}

    def fail(self, identity, status, error):
        self.failures.append((identity, status, error))


class PreparedProcess:
    def run(self, directory, timeout, **controls):
        directory = Path(directory)
        if controls["cancelling"].is_set():
            raise TaskStopped("CANCELLED", "Task cancelled")
        self.request = json.loads((directory / "request.json").read_text())
        (directory / "result.json").write_text(json.dumps({
            "metadata": {"value_count": 1}, "summary": {}, "daily": []}))
        (directory / "values.jsonl").write_text('{"value":1}\n')
        controls["logs"].append(b"finished\n")


class FakeData:
    def __init__(self):
        self.uploads = []
        self.closed = False

    def upload_values(self, tid, attempt, token, path, count, *, cancelled):
        if cancelled():
            raise InterruptedError()
        self.uploads.append((tid, attempt, token, Path(path).read_text(), count))
        return {"receipt": "test-receipt", "sha256": "test-checksum", "count": count}

    def close(self):
        self.closed = True


class WorkerProcessTests(unittest.TestCase):
    def controls(self):
        return dict(stopping=threading.Event(), cancelling=threading.Event(),
                    lost=threading.Event(), logs=LogTail())

    def test_log_tail_has_byte_bound(self):
        tail = LogTail(8)
        tail.append(b"123456")
        tail.append(b"7890")
        self.assertEqual(tail.text(), "34567890")
        tail.append(b"abcdefghijkl")
        self.assertEqual(tail.text(), "efghijkl")

    def test_child_environment_excludes_postgres_credentials(self):
        manager, control = ProcessManager(), self.controls()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FACTORPOOL_DB_PASSWORD": "test-secret"}):
            manager.run(tmp, 5, **control, command=[sys.executable, "-c",
                "import os; assert not any(x.startswith('FACTORPOOL_DB_') for x in os.environ); print('isolated')"])
        self.assertEqual(control["logs"].text().strip(), "isolated")

    def test_timeout_stops_real_process(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskStopped) as error:
                ProcessManager(terminate_seconds=.1, poll_seconds=.01).run(
                    tmp, .1, **self.controls(), command=[sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertEqual(error.exception.status, "FAILED")
        self.assertIn("timed out", str(error.exception))
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_kills_term_ignoring_descendant(self):
        control = self.controls()
        with tempfile.TemporaryDirectory() as tmp:
            child_file = Path(tmp) / "child.pid"
            child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
            parent = ("import pathlib,signal,subprocess,sys,time; "
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
                      f"pathlib.Path({str(child_file)!r}).write_text(str(p.pid)); time.sleep(30)")

            def cancel():
                deadline = time.monotonic() + 3
                while not child_file.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                control["cancelling"].set()

            thread = threading.Thread(target=cancel)
            thread.start()
            with self.assertRaises(TaskStopped) as error:
                ProcessManager(terminate_seconds=.1, poll_seconds=.01).run(
                    tmp, 5, **control, command=[sys.executable, "-c", parent])
            thread.join(timeout=3)
            self.assertEqual(error.exception.status, "CANCELLED")
            pid = int(child_file.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = Path(f"/proc/{pid}/status")
                if not status.exists() or "State:\tZ" in status.read_text():
                    break
                time.sleep(.01)
            else:
                os.kill(pid, signal.SIGKILL)
                self.fail("Task descendant survived process group cancellation")

    def test_lease_loss_aborts_before_success(self):
        controls = self.controls()
        controls["lost"].set()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(TaskStopped) as error:
            ProcessManager().run(tmp, 5, **controls,
                                 command=[sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertEqual(error.exception.status, "LEASE_LOST")

    def test_small_log_lines_are_visible_before_exit(self):
        control = self.controls()
        saw_live_log = threading.Event()

        def cancel_when_logged():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if "running" in control["logs"].text():
                    saw_live_log.set()
                    break
                time.sleep(.01)
            control["cancelling"].set()

        thread = threading.Thread(target=cancel_when_logged)
        thread.start()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(TaskStopped):
            ProcessManager(poll_seconds=.01).run(tmp, 5, **control, command=[sys.executable, "-u", "-c",
                "import time; print('running'); time.sleep(30)"])
        thread.join(timeout=4)
        self.assertTrue(saw_live_log.is_set())


class WorkerProtocolTests(unittest.TestCase):
    def test_publication_response_loss_retries_same_attempt_once(self):
        control = FakeControl(complete_response_loss=True)
        data, process = FakeData(), PreparedProcess()
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(control, root=tmp, process_manager=process, data_factory=lambda: data,
                            heartbeat_seconds=.02, poll_seconds=.01)
            worker.execute(task())
            worker.executor.shutdown()
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(process.request, task()["snapshot"])
        self.assertEqual(len(data.uploads), 1)
        self.assertEqual(len(control.completions), 2)
        self.assertEqual(control.completions[0], control.completions[1])
        self.assertEqual(control.failures, [])
        self.assertTrue(data.closed)

    def test_upload_cancellation_cannot_publish(self):
        control = FakeControl()

        class CancellingData(FakeData):
            def upload_values(self, *args, **kwargs):
                control.cancel = True
                deadline = time.monotonic() + 2
                while not kwargs["cancelled"]() and time.monotonic() < deadline:
                    time.sleep(.01)
                raise InterruptedError()

        data = CancellingData()
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(control, root=tmp, process_manager=PreparedProcess(), data_factory=lambda: data,
                            heartbeat_seconds=.02)
            worker.execute(task())
            worker.executor.shutdown()
        self.assertEqual(control.completions, [])
        self.assertEqual([row[1] for row in control.failures], ["CANCELLED"])

    def test_stale_upload_never_reports_failure_over_new_attempt(self):
        control = FakeControl()

        class StaleData(FakeData):
            def upload_values(self, *args, **kwargs):
                raise LeaseLost("stale attempt")

        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(control, root=tmp, process_manager=PreparedProcess(), data_factory=StaleData)
            worker.execute(task())
            worker.executor.shutdown()
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures, [])

    def test_cancel_between_data_finalize_and_complete_is_acknowledged(self):
        class CancelOnComplete(FakeControl):
            def complete(self, *args):
                self.cancel = True
                raise LeaseLost("Cancellation won before complete")

        control, data = CancelOnComplete(), FakeData()
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(control, root=tmp, process_manager=PreparedProcess(), data_factory=lambda: data)
            worker.execute(task())
            worker.executor.shutdown()
        self.assertEqual(len(data.uploads), 1)
        self.assertEqual([row[1] for row in control.failures], ["CANCELLED"])

    def test_dead_heartbeat_has_bounded_lease(self):
        class OfflineControl(FakeControl):
            def heartbeat(self, _):
                raise OSError("offline")

        heartbeat = LeaseHeartbeat(OfflineControl(), task(), threading.Event(), LogTail(), interval=.02)
        heartbeat.deadline = time.monotonic() + .55
        heartbeat.start()
        self.assertTrue(heartbeat.lost.wait(timeout=1))
        heartbeat.close()

    def test_http_completion_flattens_publication_receipt(self):
        client = TaskClient("http://unused", "test-token")
        with patch.object(client, "post", return_value={}) as call:
            client.complete(task(), {"summary": {}}, {"receipt": "r", "sha256": "s", "count": 1})
        body = call.call_args.args[1]
        self.assertEqual(body["receipt"], "r")
        self.assertEqual(body["sha256"], "s")
        self.assertEqual(body["attempt"], 2)

    def test_worker_has_no_fake_health_before_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(FakeControl(), root=tmp)
            self.assertEqual(worker.health()["status"], "DOWN")
            worker.started = True
            self.assertEqual(worker.health()["status"], "UP")
            worker.stopping.set()
            self.assertEqual(worker.health()["status"], "DOWN")
            worker.executor.shutdown()


if __name__ == "__main__":
    unittest.main()
