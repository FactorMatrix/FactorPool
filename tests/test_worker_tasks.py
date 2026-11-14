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
        marker=dict(version=1,artifact="values.matrix",value_count=1,sha256="a"*64)
        (directory/"values.matrix").write_text("immutable-matrix")
        (directory/"values-ready.json").write_text(json.dumps(marker))
        (directory / "result.json").write_text(json.dumps({
            "metadata": {"value_count": 1,"value_artifact":{"count":1,"sha256":marker["sha256"]}},
            "summary": {}, "daily": []}))
        controls["logs"].append(b"finished\n")


class FakeData:
    def __init__(self):
        self.uploads = []
        self.closed = False

    def upload_values(self, tid, attempt, token, path, count, *, cancelled):
        if cancelled():
            raise InterruptedError()
        self.uploads.append((tid, attempt, token, Path(path).read_text(), count))
        return {"receipt": "test-receipt", "sha256": "a"*64, "count": count}

    def close(self):
        self.closed = True


class ReadyProcess:
    """Hold diagnostics open after publishing one complete immutable artifact."""
    sha256 = "a" * 64

    def __init__(self, *, marker=None, raw_marker=None, failure=None, before_marker=False,
                 result_count=1, artifact_count=1, artifact_sha=None, matrix=True,
                 symlink=False, raw_result=None):
        self.marker = dict(version=1, artifact="values.matrix", value_count=1, sha256=self.sha256)
        if marker is not None:
            self.marker.update(marker)
        self.raw_marker, self.failure, self.before_marker = raw_marker, failure, before_marker
        self.result_count, self.artifact_count = result_count, artifact_count
        self.artifact_sha = self.sha256 if artifact_sha is None else artifact_sha
        self.matrix, self.symlink, self.raw_result = matrix, symlink, raw_result
        self.allow_finish, self.ready, self.exited = threading.Event(), threading.Event(), threading.Event()

    def run(self, directory, timeout, **controls):
        self.directory = Path(directory)
        try:
            if self.before_marker:
                raise self.failure
            if self.matrix:
                target = self.directory / ("values-target.matrix" if self.symlink else "values.matrix")
                target.write_text("immutable-matrix")
                if self.symlink:
                    (self.directory / "values.matrix").symlink_to(target.name)
            temporary = self.directory / "values-ready.tmp.json"
            temporary.write_text(self.raw_marker if self.raw_marker is not None else json.dumps(self.marker))
            temporary.replace(self.directory / "values-ready.json")
            self.ready.set()
            while not self.allow_finish.wait(.005):
                for name, status in (("lost", "LEASE_LOST"), ("cancelling", "CANCELLED"),
                                     ("stopping", "INTERRUPTED")):
                    if controls[name].is_set():
                        raise TaskStopped(status, "Research execution stopped")
            if self.failure is not None:
                raise self.failure
            result = dict(metadata=dict(value_count=self.result_count,
                value_artifact=dict(count=self.artifact_count, sha256=self.artifact_sha)), summary={}, daily=[])
            (self.directory / "result.json").write_text(
                json.dumps(result) if self.raw_result is None else self.raw_result)
        finally:
            self.exited.set()


class ReadyData(FakeData):
    def __init__(self, *, count=None, sha256=None):
        super().__init__()
        self.count, self.sha256 = count, sha256
        self.finalized = threading.Event()

    def upload_values(self, *args, **kwargs):
        receipt = super().upload_values(*args, **kwargs)
        receipt.update(count=args[4] if self.count is None else self.count,
                       sha256=ReadyProcess.sha256 if self.sha256 is None else self.sha256)
        self.finalized.set()
        return receipt


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
    def test_process_without_matrix_marker_is_rejected(self):
        class MissingMarkerProcess:
            def run(self, directory, timeout, **controls):
                path=Path(directory)
                (path/'values.jsonl').write_text('{"value":1}\n')
                (path/'result.json').write_text(json.dumps(dict(metadata=dict(value_count=1),summary={},daily=[])))
        control,data=FakeControl(),FakeData()
        with tempfile.TemporaryDirectory() as tmp:
            worker=Worker(control,root=tmp,process_manager=MissingMarkerProcess(),data_factory=lambda:data)
            worker.execute(task())
            worker.executor.shutdown()
        self.assertEqual(data.uploads,[])
        self.assertEqual(control.completions,[])
        self.assertIn('matrix marker',control.failures[0][2])

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


class WorkerPipeliningTests(unittest.TestCase):
    def execute(self, process, data, *, control=None, configure=None, while_running=None):
        control = control or FakeControl()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(control, root=directory, process_manager=process, data_factory=lambda: data,
                            heartbeat_seconds=.01, poll_seconds=.01)
            identity = task()
            if configure is not None:
                configure(worker, identity)
            running = threading.Thread(target=worker.execute, args=(identity,))
            running.start()
            try:
                if while_running is not None:
                    while_running(worker, control)
                else:
                    running.join(timeout=3)
            finally:
                process.allow_finish.set()
                running.join(timeout=4)
                worker.executor.shutdown()
            self.assertFalse(running.is_alive(), "Execution thread survived task cleanup")
            self.assertTrue(process.exited.is_set())
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertTrue(data.closed or not data.uploads)
        return control

    def test_early_finalize_overlaps_diagnostics_but_cannot_complete_before_process_success(self):
        process, data = ReadyProcess(), ReadyData()

        def during(worker, control):
            self.assertTrue(data.finalized.wait(2), "Upload waited for diagnostics to finish")
            self.assertFalse(process.exited.is_set())
            self.assertFalse((process.directory / "result.json").exists())
            self.assertEqual(control.completions, [])
            self.assertFalse(worker.stopping.is_set())

        control = self.execute(process, data, while_running=during)
        self.assertEqual(len(data.uploads), 1)
        self.assertEqual(len(control.completions), 1)
        self.assertEqual(control.failures, [])
        metadata = control.completions[0][1]["metadata"]
        self.assertEqual(metadata["value_artifact"]["sha256"], ReadyProcess.sha256)
        self.assertGreaterEqual(metadata["stage_seconds"]["upload_values"], 0)

    def test_diagnostics_failure_after_early_finalize_never_completes(self):
        process = ReadyProcess(failure=TaskStopped("FAILED", "diagnostics failed"))
        data = ReadyData()
        control = self.execute(process, data, while_running=lambda *_: self.assertTrue(data.finalized.wait(2)))
        self.assertEqual(len(data.uploads), 1)
        self.assertEqual(control.completions, [])
        self.assertEqual([failure[1:] for failure in control.failures], [("FAILED", "diagnostics failed")])

    def test_process_failure_interrupts_active_upload_and_keeps_original_failure(self):
        process = ReadyProcess(failure=TaskStopped("FAILED", "process exited during upload"))

        class InterruptedData(ReadyData):
            def upload_values(self, *args, **kwargs):
                process.allow_finish.set()
                if not process.exited.wait(2):
                    raise AssertionError("Process failed to exit")
                deadline = time.monotonic() + 1
                while not kwargs["cancelled"]() and time.monotonic() < deadline:
                    time.sleep(.005)
                if not kwargs["cancelled"]():
                    raise AssertionError("Process failure did not cancel upload")
                raise InterruptedError()

        data = InterruptedData()
        control = self.execute(process, data)
        self.assertEqual(control.completions, [])
        self.assertEqual([failure[1:] for failure in control.failures],
                         [("FAILED", "process exited during upload")])

    def test_failure_before_marker_does_not_create_upload(self):
        process = ReadyProcess(before_marker=True, failure=TaskStopped("FAILED", "no values"))
        data = ReadyData()
        control = self.execute(process, data)
        self.assertEqual(data.uploads, [])
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1:], ("FAILED", "no values"))

    def test_invalid_markers_abort_only_the_current_process_without_upload(self):
        variants = [dict(marker={"version": True}), dict(marker={"artifact": "../values.matrix"}),
                    dict(marker={"value_count": True}), dict(marker={"value_count": -1}),
                    dict(marker={"sha256": "bad"}), dict(matrix=False), dict(symlink=True),
                    dict(raw_marker="x" * 4097), dict(raw_marker="null")]
        for variant in variants:
            with self.subTest(variant=tuple(variant)):
                process, data = ReadyProcess(**variant), ReadyData()

                def during(worker, control):
                    self.assertTrue(process.exited.wait(2), "Invalid marker did not stop this process")
                    self.assertFalse(worker.stopping.is_set())

                control = self.execute(process, data, while_running=during)
                self.assertEqual(data.uploads, [])
                self.assertEqual(control.completions, [])
                self.assertEqual(control.failures[0][1], "FAILED")

    def test_marker_fifo_symlink_and_directory_are_rejected_without_blocking(self):
        code = ("import sys; from pathlib import Path; "
                "from services.worker_service.worker import Worker\n"
                "try: Worker._values_ready(Path(sys.argv[1]))\n"
                "except ValueError: pass\n"
                "else: raise AssertionError('Non-regular marker accepted')\n")
        for kind in ("fifo", "symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "values-ready.json"
                (root / "values.matrix").write_text("immutable-matrix")
                if kind == "fifo":
                    os.mkfifo(marker)
                elif kind == "directory":
                    marker.mkdir()
                else:
                    target = root / "marker-target.json"
                    target.write_text(json.dumps(ReadyProcess().marker))
                    marker.symlink_to(target.name)
                subprocess.run([sys.executable, "-c", code, directory], timeout=2, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_receipt_count_and_hash_must_match_marker_before_diagnostics_finish(self):
        for changes in (dict(count=2), dict(count=True), dict(sha256="b" * 64)):
            with self.subTest(changes=changes):
                process, data = ReadyProcess(), ReadyData(**changes)

                def during(worker, control):
                    self.assertTrue(process.exited.wait(2), "Bad receipt did not stop diagnostics")
                    self.assertFalse(worker.stopping.is_set())

                control = self.execute(process, data, while_running=during)
                self.assertEqual(control.completions, [])
                self.assertEqual(control.failures[0][1], "FAILED")

    def test_complete_report_count_and_artifact_hash_are_bound_to_early_publication(self):
        for changes in (dict(result_count=2), dict(artifact_count=2),
                        dict(artifact_count=True), dict(artifact_sha="b" * 64), dict(raw_result="{")):
            with self.subTest(changes=changes):
                process, data = ReadyProcess(**changes), ReadyData()
                control = self.execute(process, data,
                    while_running=lambda *_: self.assertTrue(data.finalized.wait(2)))
                self.assertEqual(len(data.uploads), 1)
                self.assertEqual(control.completions, [])
                self.assertEqual(control.failures[0][1], "FAILED")

    def test_upload_failure_is_not_replaced_by_process_cleanup_interruption(self):
        class RejectingData(ReadyData):
            def upload_values(self, *args, **kwargs):
                raise ValueError("upload checksum failed")

        process, data = ReadyProcess(), RejectingData()
        control = self.execute(process, data)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1:], ("FAILED", "upload checksum failed"))

    def test_upload_interrupted_error_remains_failed_after_local_process_cleanup(self):
        class InterruptedData(ReadyData):
            def upload_values(self, *args, **kwargs):
                raise InterruptedError("upload transport interrupted")

        process, data = ReadyProcess(), InterruptedData()
        control = self.execute(process, data)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1:], ("FAILED", "Result upload was interrupted"))

    def test_lease_loss_after_early_finalize_cannot_publish_or_fail_new_attempt(self):
        class ExpiringControl(FakeControl):
            lost = False

            def heartbeat(self, identity):
                if self.lost:
                    raise LeaseLost("execution superseded")
                return super().heartbeat(identity)

        process, data, control = ReadyProcess(), ReadyData(), ExpiringControl()

        def during(worker, current):
            self.assertTrue(data.finalized.wait(2))
            current.lost = True
            self.assertTrue(process.exited.wait(2))

        self.execute(process, data, control=control, while_running=during)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures, [])

    def test_worker_stop_after_early_finalize_joins_only_this_execution(self):
        process, data = ReadyProcess(), ReadyData()

        def during(worker, control):
            self.assertTrue(data.finalized.wait(2))
            worker.stopping.set()
            self.assertTrue(process.exited.wait(2))

        control = self.execute(process, data, while_running=during)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1], "INTERRUPTED")

    def test_cancel_after_early_finalize_stops_diagnostics_without_success(self):
        process, data = ReadyProcess(), ReadyData()

        def during(worker, control):
            self.assertTrue(data.finalized.wait(2))
            control.cancel = True
            self.assertTrue(process.exited.wait(2))

        control = self.execute(process, data, while_running=during)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1], "CANCELLED")

    def test_timeout_after_early_finalize_stops_diagnostics_without_success(self):
        process, data = ReadyProcess(), ReadyData()

        def during(worker, control):
            self.assertTrue(data.finalized.wait(2))
            self.assertTrue(process.exited.wait(2))

        control = self.execute(process, data,
            configure=lambda worker, identity: identity.update(timeout_seconds=.2), while_running=during)
        self.assertEqual(control.completions, [])
        self.assertEqual(control.failures[0][1], "FAILED")
        self.assertIn("timed out", control.failures[0][2])


if __name__ == "__main__":
    unittest.main()
