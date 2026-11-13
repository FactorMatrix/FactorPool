"""Bounded internal HTTP control plane; workers never connect to PostgreSQL."""
import json
import os
import urllib.error
import urllib.request


class LeaseLost(RuntimeError):
    """The server rejected an expired or superseded execution lease."""


class TaskClient:
    def __init__(self, base=None, token=None, *, timeout=5):
        self.base = (base or os.getenv("FACTORPOOL_BACKEND_URL", "http://backend:8080")).rstrip("/")
        self.token = token if token is not None else os.environ.get("FACTORPOOL_INTERNAL_TOKEN", "")
        if not self.token:
            raise ValueError("FACTORPOOL_INTERNAL_TOKEN is required")
        if not self.base.startswith(("http://", "https://")) or timeout <= 0:
            raise ValueError("Invalid backend URL or HTTP timeout")
        self.timeout = timeout
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, action, body):
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > 128 * 1024 * 1024:
            raise ValueError("Worker control request exceeds 128 MiB")
        request = urllib.request.Request(
            self.base + "/internal/worker/" + action, data=encoded, method="POST",
            headers={"Content-Type": "application/json", "X-FactorPool-Internal-Token": self.token})
        try:
            with self.http.open(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("Worker control response exceeds 16 MiB")
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 409:
                raise LeaseLost("Execution lease is no longer active") from error
            # Avoid including server error bodies, source code or credentials.
            raise RuntimeError(f"Worker {action} returned HTTP {error.code}") from error

    def claim(self, worker_id):
        return self.post("claim", {"worker_id": worker_id})

    @staticmethod
    def identity(task):
        return {key: task[key] for key in ("task_id", "attempt", "lease_token", "worker_id")}

    def heartbeat(self, task):
        return self.post("heartbeat", self.identity(task))

    def logs(self, task, text):
        return self.post("logs", dict(self.identity(task), text=text))

    def fail(self, task, status, error=None):
        return self.post("fail", dict(self.identity(task), status=status, error=error))

    def complete(self, task, result, receipt):
        publication = receipt if isinstance(receipt, dict) else {"receipt": receipt}
        return self.post("complete", dict(self.identity(task), result=result, **publication))
