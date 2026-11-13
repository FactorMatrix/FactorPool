"""Explicit, atomic import of a frozen daily bundle into PostgreSQL matrices."""
import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import pickle
import stat
import time

import h5py
import numpy as np

from factorpool.data_client.contract import FIELDS, FORMAT, pack_block
from ..postgres import transaction
from factorpool.market_returns import daily_returns, return_policy


SOURCE_FILES = ("stocks.h5", "instruments.pk", "trading_dates.npy",
                "st_stock_days.h5", "suspended_days.h5")
PRICE_FIELDS = FIELDS[:7]


def source_manifest(root, block_days=64):
    files = []
    for name in SOURCE_FILES:
        info = (root / name).lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Expected regular daily bundle file: {name}")
        files.append(dict(name=name, bytes=info.st_size, mtime_ns=info.st_mtime_ns,
                          ctime_ns=info.st_ctime_ns, inode=info.st_ino, device=info.st_dev))
    return dict(format=FORMAT, source_files=files, fields=list(FIELDS),
                dtype="<f8", layout="field,date,symbol", block_days=block_days,
                return_formula="close/prev_close-1",
                return_policy=return_policy(),
                membership="CS: listed_date <= date < de_listed_date",
                source_scope="daily stocks and metadata only")


def _source_unchanged(root, manifest):
    return all(manifest.get(key) == value
               for key, value in source_manifest(root, manifest["block_days"]).items())


class _PlainUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError("Instrument metadata must contain plain values")

    def persistent_load(self, pid):
        raise ValueError("Instrument metadata cannot contain persistent references")


def _instruments(path):
    stream = io.BytesIO(path.read_bytes())
    items = _PlainUnpickler(stream).load()
    if stream.read(1) or not isinstance(items, list):
        raise ValueError("Expected one plain instrument list")
    output = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("order_book_id"), str):
            raise ValueError("Invalid instrument metadata")
        if item.get("type") != "CS":
            continue
        symbol = item["order_book_id"]
        if not symbol or symbol in output:
            raise ValueError("Duplicate or empty CS instrument")
        output[symbol] = item
    if not output:
        raise ValueError("Bundle contains no CS instruments")
    return output


def _day(value, fallback):
    if value is None or str(value) == "" or str(value).startswith("0000-00-00"):
        return fallback
    text = str(value)
    result = (date.fromisoformat(text[:10]) if len(text) >= 10 and text[4] == "-"
              else date(int(text[:4]), int(text[4:6]), int(text[6:8])))
    return int(result.strftime("%Y%m%d"))


def _days(values):
    values = np.asarray(values)
    if values.size == 0:
        return np.empty(0, dtype=np.int64)
    if values.dtype.kind not in "iu" or values.ndim != 1:
        raise ValueError("Expected integer daily date keys")
    values = values.astype(np.int64, copy=False)
    days = np.where(values > 99_999_999, values // 1_000_000, values)
    if np.any(days < 10_000_101) or np.any(days > 99_991_231):
        raise ValueError("Invalid daily date key")
    return days


def _statuses(handle, symbol):
    values = handle[symbol][:] if symbol in handle else np.empty(0, dtype=np.int64)
    if values.dtype.kind == "f" and values.size:
        if (not np.all(np.isfinite(values)) or np.any(values != np.trunc(values))
                or np.any(np.abs(values) > 99_991_231_999_999)):
            raise ValueError("Status date keys must be finite exact integers")
        values = values.astype(np.int64)
    values = _days(values)
    # Status datasets are date sets; the source does not promise storage order.
    return np.unique(values)


@dataclass
class BundlePlan:
    root: Path
    snapshot_id: str
    manifest: dict
    symbols: list
    dates: list
    calendar: np.ndarray
    indices: dict
    instruments: dict


def scan_bundle(root, block_days=64):
    if type(block_days) is not int or not 1 <= block_days <= 512:
        raise ValueError("block_days must be an integer in 1..512")
    root = Path(root)
    manifest = source_manifest(root, block_days)
    instruments = _instruments(root / "instruments.pk")
    calendar = _days(np.load(root / "trading_dates.npy", allow_pickle=False))
    if not len(calendar) or np.any(calendar[:-1] >= calendar[1:]):
        raise ValueError("Trading calendar must be unique and sorted")
    indices = {}
    with h5py.File(root / "stocks.h5", "r") as stocks:
        for symbol in stocks:
            if symbol not in instruments:
                raise ValueError("Stock dataset has no CS instrument metadata")
            dataset = stocks[symbol]
            if (dataset.ndim != 1 or dataset.dtype.names is None
                    or not set(("datetime", *PRICE_FIELDS)).issubset(dataset.dtype.names)):
                raise ValueError("Daily stock schema is missing required fields")
            days = _days(dataset.fields("datetime")[:])
            if len(days) > 1 and np.any(days[:-1] >= days[1:]):
                raise ValueError("Stock date keys must be unique and sorted")
            locations = np.searchsorted(calendar, days)
            if (np.any(locations >= len(calendar))
                    or not np.array_equal(calendar[np.minimum(locations, len(calendar)-1)], days)):
                raise ValueError("Stock date is absent from the trading calendar")
            indices[symbol] = days
    populated = [days for days in indices.values() if len(days)]
    if not populated:
        raise ValueError("Bundle contains no daily stock rows")
    earliest = min(int(days[0]) for days in populated)
    latest = max(int(days[-1]) for days in populated)
    calendar = calendar[(calendar >= earliest) & (calendar <= latest)]
    dates = [f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}" for day in calendar]
    for text in dates:
        date.fromisoformat(text)
    symbols = sorted(instruments)
    manifest["instrument_lifetimes"] = [
        [_day(instruments[symbol].get("listed_date"), 19900101),
         _day(instruments[symbol].get("de_listed_date"), 29991231)]
        for symbol in symbols
    ]
    if not _source_unchanged(root, manifest):
        raise ValueError("Daily bundle changed while scanning")
    snapshot_id = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return BundlePlan(root, snapshot_id, manifest, symbols, dates,
                      calendar, indices, instruments)


def iter_blocks(plan, block_days=64):
    if type(block_days) is not int or not 1 <= block_days <= 512:
        raise ValueError("block_days must be an integer in 1..512")
    if not _source_unchanged(plan.root, plan.manifest):
        raise ValueError("Daily bundle changed before import")
    with h5py.File(plan.root / "stocks.h5", "r") as stocks, \
            h5py.File(plan.root / "st_stock_days.h5", "r") as st, \
            h5py.File(plan.root / "suspended_days.h5", "r") as suspended:
        status = {symbol: (_statuses(st, symbol), _statuses(suspended, symbol))
                  for symbol in plan.symbols}
        lifetimes = plan.manifest["instrument_lifetimes"]
        for start in range(0, len(plan.calendar), block_days):
            calendar = plan.calendar[start:start + block_days]
            block = np.full((len(FIELDS), len(calendar), len(plan.symbols)), np.nan, dtype="<f8")
            for n, symbol in enumerate(plan.symbols):
                listed, delisted = lifetimes[n]
                block[8, :, n] = (calendar >= listed) & (calendar < delisted)
                st_days, suspended_days = status[symbol]
                block[9, :, n] = np.isin(calendar, st_days)
                block[10, :, n] = np.isin(calendar, suspended_days)
                if symbol not in plan.indices:
                    continue
                days = plan.indices[symbol]
                left, right = np.searchsorted(days, (calendar[0], calendar[-1]), side="left")
                right = np.searchsorted(days, calendar[-1], side="right")
                if left == right:
                    continue
                rows = stocks[symbol][left:right]
                positions = np.searchsorted(calendar, days[left:right])
                for k, field in enumerate(PRICE_FIELDS):
                    block[k, positions, n] = rows[field]
            block[7] = daily_returns(block[3], block[4], block[5], block[6], block[8])
            payload, checksum = pack_block(block)
            yield start, len(calendar), payload, checksum
    if not _source_unchanged(plan.root, plan.manifest):
        raise ValueError("Daily bundle changed during import")


def import_bundle(root, *, connection=None, block_days=64, progress=None):
    from psycopg.types.json import Jsonb
    started = time.perf_counter()
    plan = scan_bundle(root, block_days)
    count = 0
    with transaction(connection) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (plan.snapshot_id,))
        existing = conn.execute("SELECT manifest,symbols,dates,fields FROM market_snapshots WHERE id=%s", (plan.snapshot_id,)).fetchone()
        if existing is not None:
            if existing != (plan.manifest, plan.symbols, plan.dates, list(FIELDS)):
                raise ValueError("Existing market snapshot identity differs")
            block_rows = conn.execute(
                "SELECT start_index,row_count FROM market_matrix_blocks "
                "WHERE snapshot_id=%s ORDER BY start_index", (plan.snapshot_id,),
            ).fetchall()
            expected = [(start, min(block_days, len(plan.dates) - start))
                        for start in range(0, len(plan.dates), block_days)]
            if block_rows != expected:
                raise ValueError("Existing market snapshot has missing or unexpected blocks")
            if not _source_unchanged(plan.root, plan.manifest):
                raise ValueError("Daily bundle changed before existing snapshot validation")
            return dict(snapshot_id=plan.snapshot_id, dates=len(plan.dates),
                        symbols=len(plan.symbols), blocks=len(block_rows), created=False)
        conn.execute(
            "INSERT INTO market_snapshots(id,manifest,symbols,dates,fields) VALUES(%s,%s,%s,%s,%s)",
            (plan.snapshot_id, Jsonb(plan.manifest), Jsonb(plan.symbols), Jsonb(plan.dates), Jsonb(list(FIELDS))),
        )
        for start, rows, payload, checksum in iter_blocks(plan, block_days):
            conn.execute(
                "INSERT INTO market_matrix_blocks(snapshot_id,start_index,row_count,payload,sha256) "
                "VALUES(%s,%s,%s,%s,%s)",
                (plan.snapshot_id, start, rows, payload, checksum),
            )
            count += 1
            if progress is not None:
                progress(dict(snapshot_id=plan.snapshot_id, dates=min(start + rows, len(plan.dates)),
                              symbols=len(plan.symbols), blocks=count,
                              elapsed=time.perf_counter() - started))
        if not _source_unchanged(plan.root, plan.manifest):
            raise ValueError("Daily bundle changed before publication")
    return dict(snapshot_id=plan.snapshot_id, dates=len(plan.dates), symbols=len(plan.symbols),
                blocks=count, created=True, elapsed=time.perf_counter() - started)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--block-days", type=int, default=64)
    args = parser.parse_args()
    report = lambda value: print(json.dumps(value, allow_nan=False), flush=True)
    report(import_bundle(args.root, block_days=args.block_days, progress=report))


if __name__ == "__main__":
    main()
