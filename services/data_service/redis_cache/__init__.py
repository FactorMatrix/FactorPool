"""Disposable bounded cache with short failure circuit and scoped leases."""
import os
import time


class Cache:
    def __init__(self, client=None):
        self.enabled = os.environ.get("FACTORPOOL_CACHE_ENABLED", "true").lower() == "true"
        self.ttl = int(os.environ.get("FACTORPOOL_CACHE_TTL_SECONDS", "86400"))
        self.batch_bytes = int(os.environ.get("FACTORPOOL_CACHE_BATCH_MAX_BYTES", "33554432"))
        if self.ttl < 1 or not 8192 <= self.batch_bytes <= 128 * 1024 * 1024:
            raise ValueError("Invalid Redis TTL or batch bounds")
        self.failed_until = 0.
        self.errors = 0
        if client is None and self.enabled:
            import redis
            client = redis.Redis.from_url(os.environ.get("FACTORPOOL_REDIS_URL", "redis://redis:6379/0"),
                socket_connect_timeout=float(os.environ.get("FACTORPOOL_REDIS_CONNECT_TIMEOUT_SECONDS", ".2")),
                socket_timeout=float(os.environ.get("FACTORPOOL_REDIS_TIMEOUT_SECONDS", ".5")),
                max_connections=int(os.environ.get("FACTORPOOL_REDIS_POOL_SIZE", "16")), retry_on_timeout=False,
                retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0),
                decode_responses=False)
        self.client = client

    def call(self, method, *args, **kwargs):
        if not self.enabled or self.client is None or time.monotonic() < self.failed_until:
            return None
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except Exception:
            self.errors += 1
            self.failed_until = time.monotonic() + 2
            return None

    def store(self, entries):
        if not self.enabled or self.client is None or time.monotonic() < self.failed_until:
            return
        batch, size = [], 0
        def flush():
            if time.monotonic() < self.failed_until:
                return
            try:
                with self.client.pipeline(transaction=False) as pipe:
                    for key, value in batch:
                        pipe.set(key, value, ex=self.ttl)
                    pipe.execute()
            except Exception:
                self.errors += 1
                self.failed_until = time.monotonic() + 2
        for key, value in entries:
            if batch and size + len(value) > self.batch_bytes:
                flush()
                batch, size = [], 0
            batch.append((key, value))
            size += len(value)
        if batch:
            flush()
