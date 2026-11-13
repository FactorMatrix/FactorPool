"""Standalone HTTP-controlled research worker; no database or shared-filesystem IPC."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import stat
import tempfile
import threading
import time
import uuid

from .process_manager import LogTail, ProcessManager, TaskStopped
from .task_client import LeaseLost, TaskClient


LOGGER = logging.getLogger("factorpool.worker")


class _ProcessStop:
    """Task-local aborts must not stop the worker's other executions."""
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


class LeaseHeartbeat:
    """Renew throughout compute and upload; disconnects cannot outlive the lease."""
    def __init__(self, client, task, stopping, logs, *, interval=5):
        seconds = float(task["lease_seconds"])
        if seconds < 2 or interval <= 0:
            raise ValueError("Invalid task lease or heartbeat interval")
        self.client, self.task = client, task
        self.stopping, self.logs = stopping, logs
        self.interval = min(interval, seconds / 3)
        self.deadline = time.monotonic() + seconds
        self.task_deadline = time.monotonic() + float(task["timeout_seconds"])
        self.cancelling = threading.Event()
        self.lost = threading.Event()
        self.timed_out = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._loop, name=f"lease-{task['task_id']}", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.done.set()
        self.thread.join(timeout=max(1, self.client.timeout + 1))

    def interrupted(self):
        return (self.stopping.is_set() or self.cancelling.is_set()
                or self.lost.is_set() or self.timed_out.is_set())

    def check(self):
        if self.lost.is_set():
            raise TaskStopped("LEASE_LOST", "Execution lease was lost")
        if self.cancelling.is_set():
            raise TaskStopped("CANCELLED", "Task cancelled")
        if self.stopping.is_set():
            raise TaskStopped("INTERRUPTED", "Worker stopped")
        if self.timed_out.is_set():
            raise TaskStopped("FAILED", "Task timed out during result publication")

    def _loop(self):
        while not self.done.is_set():
            now = time.monotonic()
            if now >= self.task_deadline:
                self.timed_out.set()
            if now >= self.deadline - .5:
                self.lost.set()
                return
            try:
                answer = self.client.heartbeat(self.task)
                self.deadline = time.monotonic() + float(answer["lease_seconds"])
                if answer["status"] == "CANCELLING":
                    self.cancelling.set()
                elif answer["status"] != "RUNNING":
                    self.lost.set()
                    return
                # A failed logs upload must not invalidate a successful renewal.
                try:
                    self.client.logs(self.task, self.logs.text())
                except LeaseLost:
                    self.lost.set()
                    return
                except Exception:
                    LOGGER.warning("Log upload unavailable for task %s", self.task["task_id"])
            except LeaseLost:
                self.lost.set()
                return
            except Exception:
                LOGGER.warning("Heartbeat unavailable for task %s", self.task["task_id"])
            wait = min(self.interval, max(.05, self.deadline - time.monotonic() - .5))
            self.done.wait(wait)


class Worker:
    def __init__(self, client=None, *, concurrency=None, root=None, poll_seconds=None,
                 heartbeat_seconds=None, data_factory=None, process_manager=None):
        self.client = client or TaskClient(timeout=float(os.getenv("FACTORPOOL_WORKER_HTTP_TIMEOUT", "3")))
        self.concurrency = int(concurrency if concurrency is not None
                               else os.getenv("FACTORPOOL_WORKER_CONCURRENCY", "1"))
        self.poll_seconds = float(poll_seconds if poll_seconds is not None
                                  else os.getenv("FACTORPOOL_WORKER_POLL_SECONDS", ".5"))
        self.heartbeat_seconds = float(heartbeat_seconds if heartbeat_seconds is not None
                                       else os.getenv("FACTORPOOL_WORKER_HEARTBEAT_SECONDS", "5"))
        if not 1 <= self.concurrency <= 8 or self.poll_seconds <= 0 or self.heartbeat_seconds <= 0:
            raise ValueError("Invalid worker capacity or intervals")
        self.root = Path(root or os.getenv("FACTORPOOL_WORKER_ROOT", "/tmp/factorpool-worker"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker_id = os.getenv("FACTORPOOL_WORKER_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.worker_id):
            raise ValueError("Invalid worker id")
        self.data_factory = data_factory
        self.process_manager = process_manager or ProcessManager()
        self.stopping = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="research-task")
        self.active = {}
        self.lock = threading.Lock()
        self.last_poll = time.monotonic()
        self.started = False

    def health(self):
        with self.lock:
            active = len(self.active)
        healthy = (self.started and not self.stopping.is_set()
                   and time.monotonic() - self.last_poll < max(15, self.client.timeout * 2 + self.poll_seconds))
        return {"status": "UP" if healthy else "DOWN", "worker_id": self.worker_id,
                "active": active, "capacity": self.concurrency}

    def run(self):
        self.started = True
        try:
            while not self.stopping.is_set():
                self.last_poll = time.monotonic()
                with self.lock:
                    completed = [(key, future) for key, future in self.active.items() if future.done()]
                    for key, future in completed:
                        del self.active[key]
                        try:
                            future.result()
                        except Exception:
                            LOGGER.exception("Task control failed for %s", key)
                    capacity = self.concurrency - len(self.active)
                for _ in range(capacity):
                    if self.stopping.is_set():
                        break
                    try:
                        task = self.client.claim(self.worker_id)
                        if task is None:
                            break
                        if task.get("worker_id") != self.worker_id:
                            raise ValueError("Claim returned a different worker identity")
                        with self.lock:
                            if task["task_id"] in self.active:
                                raise ValueError("Duplicate task claimed by one worker")
                            self.active[task["task_id"]] = self.executor.submit(self.execute, task)
                    except Exception:
                        LOGGER.exception("Task claim unavailable")
                        break
                self.stopping.wait(self.poll_seconds)
        finally:
            self.stopping.set()
            self.executor.shutdown(wait=True, cancel_futures=True)

    def _data_client(self):
        if self.data_factory is not None:
            return self.data_factory()
        from factorpool.data_client import DataClient
        return DataClient()

    def _complete(self, task, result, receipt, heartbeat):
        # Transport failure after server commit is retried with the same receipt.
        # The API fences duplicate completion by attempt/token and returns it idempotently.
        retrying = False
        while True:
            try:
                heartbeat.check()
            except TaskStopped as error:
                # A heartbeat can observe SUCCEEDED before the completion HTTP
                # response arrives. Re-submit once to confirm the idempotent
                # receipt; the API still rejects genuinely superseded attempts.
                if not retrying or error.status != "LEASE_LOST":
                    raise
            try:
                return self.client.complete(task, result, receipt)
            except LeaseLost:
                raise
            except Exception:
                if time.monotonic() >= heartbeat.deadline - .5:
                    raise LeaseLost("Completion could not be confirmed before lease expiry")
                retrying = True
                LOGGER.warning("Completion acknowledgement unavailable for task %s", task["task_id"])
                if heartbeat.done.wait(min(1, self.poll_seconds)):
                    raise LeaseLost("Worker heartbeat stopped before completion")

    @staticmethod
    def _values_ready(directory):
        try:
            descriptor = os.open(directory / "values-ready.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("Invalid research values marker file") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Research values marker must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(4097)
        finally:
            os.close(descriptor)
        if len(raw) > 4096:
            raise ValueError("Research values marker exceeds 4 KiB")
        marker = json.loads(raw)
        if (not isinstance(marker, dict) or type(marker.get("version")) is not int
                or marker["version"] != 1 or marker.get("artifact") != "values.matrix"
                or type(marker.get("value_count")) is not int or marker["value_count"] < 0
                or not isinstance(marker.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", marker["sha256"])
                or (directory / "values.matrix").is_symlink()
                or not (directory / "values.matrix").is_file()):
            raise ValueError("Invalid research values marker")
        return marker

    def execute(self, task):
        logs = LogTail()
        heartbeat = LeaseHeartbeat(self.client, task, self.stopping, logs, interval=self.heartbeat_seconds)
        heartbeat.start()
        data = None
        process_done, process_stop = threading.Event(), threading.Event()
        process_errors = []

        def check_execution():
            heartbeat.check()
            if process_done.is_set() and process_errors:
                error = process_errors[0]
                if isinstance(error, (TaskStopped, LeaseLost)):
                    raise error
                raise TaskStopped("FAILED", str(error)) from error

        def upload_cancelled():
            return heartbeat.interrupted() or bool(process_done.is_set() and process_errors)

        def upload(path, count):
            nonlocal data
            check_execution()
            data = self._data_client()
            started = time.perf_counter()
            try:
                receipt = data.upload_values(task["task_id"], task["attempt"], task["lease_token"],
                                             path, count, cancelled=upload_cancelled)
            except InterruptedError as error:
                # Freeze the real interruption before local process cleanup can
                # introduce its own TaskStopped(INTERRUPTED) into process_errors.
                check_execution()
                raise TaskStopped("FAILED", "Result upload was interrupted") from error
            check_execution()
            return receipt, time.perf_counter() - started

        try:
            with tempfile.TemporaryDirectory(prefix=f"{task['task_id']}-{task['attempt']}-", dir=self.root) as tmp:
                directory = Path(tmp)
                (directory / "request.json").write_text(json.dumps(task["snapshot"], ensure_ascii=False, allow_nan=False))
                heartbeat.check()

                def run_process():
                    try:
                        self.process_manager.run(directory, float(task["timeout_seconds"]),
                            stopping=_ProcessStop(self.stopping, process_stop, heartbeat.timed_out),
                            cancelling=heartbeat.cancelling, lost=heartbeat.lost, logs=logs)
                    except BaseException as error:
                        process_errors.append(error if isinstance(error, Exception)
                                              else RuntimeError("Research process control failed"))
                    finally:
                        process_done.set()

                process = threading.Thread(target=run_process,
                    name=f"research-{task['task_id']}-{task['attempt']}")
                process.start()
                try:
                    marker, receipt, upload_seconds = None, None, None
                    while True:
                        check_execution()
                        marker = self._values_ready(directory)
                        if marker is not None:
                            receipt, upload_seconds = upload(directory / "values.matrix", marker["value_count"])
                            if (not isinstance(receipt, dict) or type(receipt.get("count")) is not int
                                    or receipt["count"] != marker["value_count"]
                                    or receipt.get("sha256") != marker["sha256"]):
                                raise ValueError("Uploaded values differ from research marker")
                            break
                        if process_done.is_set():
                            # The marker may have been atomically published
                            # between the preceding read and process completion.
                            if self._values_ready(directory) is None:
                                break
                            continue
                        process_done.wait(.02)
                    # A receipt alone cannot publish success: the full diagnostics
                    # process must still succeed and bind its report to these bytes.
                    while not process_done.wait(.02):
                        check_execution()
                    check_execution()
                    result_path = directory / "result.json"
                    if result_path.stat().st_size > 128 * 1024 * 1024:
                        raise ValueError("Research summary exceeds 128 MiB")
                    result = json.loads(result_path.read_text())
                    if (not isinstance(result, dict)
                            or not all(key in result for key in ("metadata", "summary", "daily"))
                            or not isinstance(result["metadata"], dict)):
                        raise ValueError("Incomplete research result")
                    count = result["metadata"].get("value_count")
                    if type(count) is not int or count < 0:
                        raise ValueError("Invalid research factor value count")
                    if marker is not None:
                        artifact = result["metadata"].get("value_artifact")
                        if (count != marker["value_count"] or not isinstance(artifact, dict)
                                or type(artifact.get("count")) is not int or artifact["count"] != count
                                or artifact.get("sha256") != marker["sha256"]):
                            raise ValueError("Research report differs from uploaded values")
                    else:
                        # Legacy/custom processes and explicit JSONL exports have
                        # no early-ready marker and retain their serial behavior.
                        values_path = directory / "values.matrix"
                        if not values_path.exists():
                            values_path = directory / "values.jsonl"
                        receipt, upload_seconds = upload(values_path, count)
                    result["metadata"].setdefault("stage_seconds", {})["upload_values"] = upload_seconds
                    check_execution()
                    self.client.logs(task, logs.text())
                    self._complete(task, result, receipt, heartbeat)
                    LOGGER.info("Task %s attempt %s succeeded", task["task_id"], task["attempt"])
                finally:
                    process_stop.set()
                    process.join()
        except (LeaseLost, TaskStopped, InterruptedError) as error:
            if isinstance(error, InterruptedError):
                try:
                    heartbeat.check()
                except TaskStopped as stopped:
                    error = stopped
                else:
                    error = TaskStopped("FAILED", "Result upload was interrupted")
            status = error.status if isinstance(error, TaskStopped) else "LEASE_LOST"
            if status == "LEASE_LOST":
                # Cancellation can win between data finalize and API complete.
                # A current CANCELLING lease can still be acknowledged; stale
                # identities remain rejected by the heartbeat and fail APIs.
                try:
                    current = self.client.heartbeat(task)
                    if current["status"] == "CANCELLING":
                        self._fail(task, "CANCELLED", None, logs)
                except Exception:
                    pass
            elif not heartbeat.lost.is_set():
                self._fail(task, status, str(error), logs)
        except Exception as error:
            LOGGER.exception("Task %s attempt %s failed", task["task_id"], task["attempt"])
            if not heartbeat.lost.is_set():
                self._fail(task, "FAILED", str(error), logs)
        finally:
            heartbeat.close()
            if data is not None and hasattr(data, "close"):
                data.close()

    def _fail(self, task, status, error, logs):
        try:
            self.client.logs(task, logs.text())
        except Exception:
            pass
        try:
            self.client.fail(task, status, error[:4000] if error else None)
        except LeaseLost:
            pass
        except Exception:
            LOGGER.exception("Could not report task %s failure; lease recovery will fence it", task["task_id"])


def health_server(worker, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            health = worker.health()
            body = json.dumps(health).encode()
            self.send_response(200 if health["status"] == "UP" else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="worker-health", daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    port = int(os.getenv("FACTORPOOL_WORKER_HEALTH_PORT", "8091"))
    if args.healthcheck:
        import urllib.request
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/health", timeout=3) as answer:
            if json.load(answer).get("status") != "UP":
                raise SystemExit(1)
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stopping.set())
    server = health_server(worker, port)
    try:
        worker.run()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
