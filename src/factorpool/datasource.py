"""Read-only inputs. Fixture data is explicitly synthetic."""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path


def fingerprint(root):
    root = Path(root)
    files = sorted(root.glob("*.mmap"))
    if len(files) < 14:
        raise ValueError("Incomplete FactorTrader mmap snapshot")
    identity = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    manifest = root / "build-manifest.json"
    if manifest.exists():
        identity.append(("manifest_sha256", hashlib.sha256(manifest.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def fixture():
    frames = []
    day = date(2024, 1, 2)
    for i in range(240):
        while day.weekday() > 4:
            day += timedelta(days=1)
        rows = {}
        for symbol, volume, base, growth in (("A", 10, 10, 1.1), ("B", 20, 20, 1), ("C", 30, 30, .9)):
            price = base * growth ** i
            rows[symbol] = dict(open=price, high=price, low=price, close=price, value=price, volume=volume)
        frames.append(dict(date=day.isoformat(), symbols=["A", "B", "C"], rows=rows))
        day += timedelta(days=1)
    return dict(provider="fixture", adjustment="raw", symbols=["A", "B", "C"], frames=frames,
                snapshot_id="synthetic-ohlcv-v2", synthetic=True)
