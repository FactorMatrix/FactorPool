"""One isolated process group per task, with bounded logs and deterministic stop."""
from collections import deque
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


class TaskStopped(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class LogTail:
    def __init__(self, limit=65536):
        self.limit = limit
        self._chunks = deque()
        self._size = 0
        self._lock = threading.Lock()

    def append(self, data):
        with self._lock:
            self._chunks.append(data[-self.limit:])
            self._size += len(self._chunks[-1])
            while self._size > self.limit:
                excess = self._size - self.limit
                head = self._chunks.popleft()
                self._size -= len(head)
                if len(head) > excess:
                    tail = head[excess:]
                    self._chunks.appendleft(tail)
                    self._size += len(tail)

    def text(self):
        with self._lock:
            return b"".join(self._chunks).decode("utf-8", errors="replace")


class ProcessManager:
    def __init__(self, *, terminate_seconds=3, poll_seconds=.1):
        self.terminate_seconds = terminate_seconds
        self.poll_seconds = poll_seconds

    @staticmethod
    def _signal_group(process, signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    def stop(self, process):
        self._signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=self.terminate_seconds)
        except subprocess.TimeoutExpired:
            pass
        # The leader may have exited while a descendant ignores TERM.
        self._signal_group(process, signal.SIGKILL)
        process.wait(timeout=max(1, self.terminate_seconds))

    def run(self, directory, timeout, *, stopping, cancelling, lost, logs, command=None):
        if timeout <= 0:
            raise ValueError("Task timeout must be positive")
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("FACTORPOOL_DB_")}
        environment["PYTHONUNBUFFERED"] = "1"
        command = command or [sys.executable, "-m", "factorpool.runner", str(Path(directory))]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True, env=environment)

        def drain():
            try:
                while chunk := os.read(process.stdout.fileno(), 8192):
                    logs.append(chunk)
            finally:
                process.stdout.close()

        reader = threading.Thread(target=drain, name=f"task-log-{process.pid}", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            while True:
                if lost.is_set():
                    raise TaskStopped("LEASE_LOST", "Execution lease was lost")
                if cancelling.is_set():
                    raise TaskStopped("CANCELLED", "Task cancelled")
                if stopping.is_set():
                    raise TaskStopped("INTERRUPTED", "Worker stopped")
                if time.monotonic() >= deadline:
                    raise TaskStopped("FAILED", f"Task timed out after {timeout:g} seconds")
                result = process.poll()
                if result is not None:
                    if result:
                        raise TaskStopped("FAILED", f"Research process exited {result}")
                    return
                time.sleep(self.poll_seconds)
        finally:
            self.stop(process)
            reader.join(timeout=max(1, self.terminate_seconds))
