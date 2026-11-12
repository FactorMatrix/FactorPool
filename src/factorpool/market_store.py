"""Immutable PostgreSQL daily matrices; research tasks only read this store."""
from contextlib import contextmanager
from datetime import date
import hashlib
import os
import zlib

import numpy as np


FIELDS = ("open", "high", "low", "close", "prev_close", "volume",
          "total_turnover", "return", "member", "is_st", "is_suspended")
FORMAT = "factorpool-market-matrix-v1"
MAX_CELLS = 2_000_000


def connect():
    import psycopg
    return psycopg.connect(
        host=os.environ.get("FACTORPOOL_DB_HOST", "db"),
        port=int(os.environ.get("FACTORPOOL_DB_PORT", "5432")),
        dbname=os.environ.get("FACTORPOOL_DB_NAME", "factorpool"),
        user=os.environ.get("FACTORPOOL_DB_USER", "factorpool"),
        password=os.environ["FACTORPOOL_DB_PASSWORD"],
        connect_timeout=10,
    )


@contextmanager
def transaction(connection=None, *, readonly=False):
    owned = connection is None
    conn = connect() if owned else connection
    try:
        with conn.transaction():
            if readonly:
                conn.execute("SET TRANSACTION READ ONLY")
            yield conn
    finally:
        if owned:
            conn.close()


def pack_block(values):
    values = np.ascontiguousarray(values, dtype="<f8")
    if values.ndim != 3 or values.shape[0] != len(FIELDS):
        raise ValueError("Expected field x date x symbol market block")
    raw = values.tobytes(order="C")
    return zlib.compress(raw, level=3), hashlib.sha256(raw).hexdigest()


def decode_block(payload, sha256, row_count, symbol_count, fields=FIELDS):
    if tuple(fields) != FIELDS or row_count < 1 or symbol_count < 1:
        raise ValueError("Unsupported market block shape or fields")
    expected = len(FIELDS) * row_count * symbol_count * 8
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(payload), expected + 1)
    if (len(raw) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or hashlib.sha256(raw).hexdigest() != sha256):
        raise ValueError("Market matrix block integrity check failed")
    return np.frombuffer(raw, dtype="<f8").reshape(len(FIELDS), row_count, symbol_count)


def _axis(values, label):
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or not value for value in values)
            or any(a >= b for a, b in zip(values, values[1:]))):
        raise ValueError(f"Invalid market {label} axis")
    return values


def load(data, warmup, future, *, connection=None):
    snapshot_id = data.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("A fixed market snapshot_id is required")
    if (type(warmup) is not int or not 1 <= warmup <= 10000
            or type(future) is not int or not 0 <= future <= 2521):
        raise ValueError("Invalid warmup or future date count")
    first = date.fromisoformat(data["factor_start"]).isoformat()
    last = date.fromisoformat(data["factor_end"]).isoformat()
    if last < first:
        raise ValueError("End precedes start")
    with transaction(connection, readonly=True) as conn:
        row = conn.execute(
            "SELECT manifest,symbols,dates,fields FROM market_snapshots WHERE id=%s",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Market snapshot is unavailable; import it explicitly first")
        manifest, all_symbols, dates, fields = row
        if (manifest.get("format") != FORMAT or tuple(fields) != FIELDS):
            raise ValueError("Unsupported market snapshot format")
        all_symbols = _axis(all_symbols, "symbol")
        dates = _axis(dates, "date")
        date_axis = np.asarray(dates)
        begin = int(np.searchsorted(date_axis, first, side="left"))
        finish = int(np.searchsorted(date_axis, last, side="right"))
        if begin == finish:
            raise ValueError("No market dates in requested range")
        initial = max(0, begin - warmup + 1)
        stop = min(len(dates), finish + future)
        universe = data.get("universe")
        dynamic = universe == "all_a_dynamic"
        if not dynamic:
            if (not isinstance(universe, list) or not universe
                    or any(not isinstance(s, str) or not s for s in universe)
                    or len(set(universe)) != len(universe)):
                raise ValueError("Specify unique symbols or all_a_dynamic")
            selected = sorted(universe)
            lookup = {symbol: i for i, symbol in enumerate(all_symbols)}
            if any(symbol not in lookup for symbol in selected):
                raise ValueError("Universe includes a symbol absent from this snapshot")
            columns = np.array([lookup[symbol] for symbol in selected], dtype=np.intp)
        else:
            lifetimes = manifest.get("instrument_lifetimes")
            if (not isinstance(lifetimes, list) or len(lifetimes) != len(all_symbols)
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or any(type(day) is not int for day in pair) for pair in lifetimes)):
                raise ValueError("Snapshot is missing historical instrument lifetimes")
            lifetimes = np.asarray(lifetimes, dtype=np.int64)
            day_keys = np.array([int(day.replace("-", "")) for day in dates])
            listed = np.searchsorted(day_keys, lifetimes[:, 0], side="left")
            delisted = np.searchsorted(day_keys, lifetimes[:, 1], side="left")
            # The same [listed, delisted) boundary as stored membership. Only
            # dates actually sent to the Indicator can add a symbol to its axis.
            mask = np.maximum(listed, initial) < np.minimum(delisted, finish)
            columns = np.flatnonzero(mask)
            selected = [symbol for symbol, keep in zip(all_symbols, mask) if keep]
            if not selected:
                raise ValueError("Universe has no instruments in the requested range")
        if len(columns) * (stop - initial) > MAX_CELLS:
            raise ValueError("Research input exceeds 2,000,000 cells; narrow dates/universe")
        matrices = np.full((len(FIELDS), stop - initial, len(columns)), np.nan)
        covered = initial
        seen = False
        with conn.cursor(name="factorpool_market_blocks") as blocks:
            blocks.itersize = 1
            blocks.execute(
                "SELECT start_index,row_count,payload,sha256 FROM market_matrix_blocks "
                "WHERE snapshot_id=%s AND start_index < %s "
                "AND start_index + row_count > %s ORDER BY start_index",
                (snapshot_id, stop, initial),
            )
            for start, count, payload, checksum in blocks:
                end = start + count
                if (start < 0 or count < 1 or end > len(dates)
                        or (start != covered if seen else not start <= covered < end)):
                    raise ValueError("Market matrix blocks overlap or have missing dates")
                block = decode_block(payload, checksum, count, len(all_symbols), fields)
                left, right = max(initial, start), min(stop, end)
                matrices[:, left - initial:right - initial] = block[:, left - start:right - start, :][:, :, columns]
                covered = right
                seen = True
        if covered != stop:
            raise ValueError("Market matrix snapshot has missing blocks")
    if dynamic:
        membership = matrices[FIELDS.index("member"), :finish - initial]
        mask = np.any(membership > 0, axis=0)
        if not np.any(mask):
            raise ValueError("Universe has no instruments in the requested range")
        matrices = np.ascontiguousarray(matrices[:, :, mask])
        selected = [symbol for symbol, keep in zip(selected, mask) if keep]
    frames = []
    for t, day in enumerate(dates[initial:stop]):
        if day > last:
            frames.append(dict(date=day, symbols=[], rows={}))
            continue
        axis = ([s for n, s in enumerate(selected) if matrices[8, t, n] > 0]
                if dynamic else selected.copy())
        rows = {}
        for n, symbol in enumerate(selected):
            if not np.any(np.isfinite(matrices[:7, t, n])):
                continue
            row = {field: (float(matrices[k, t, n]) if np.isfinite(matrices[k, t, n]) else None)
                   for k, field in enumerate(FIELDS) if field not in ("member", "return")}
            row["value"] = row["close"]
            rows[symbol] = row
        frames.append(dict(date=day, symbols=axis, rows=rows))
    return dict(provider="market_matrix", adjustment="raw", symbols=selected,
                frames=frames, returns=np.ascontiguousarray(matrices[7]),
                snapshot_id=snapshot_id, synthetic=False)
