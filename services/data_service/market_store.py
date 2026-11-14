"""Explicit preparation of four authoritative, immutable market field tables.

The PostgreSQL TOAST layer compresses each field block with LZ4. Python owns
axes, buffer transport and publication; all R/Q arithmetic uses the prebuilt
C++ module. Importing this module never prepares or updates a dataset.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np

from factorpool.data_client.contract import (BOOLEAN_FIELDS, FIELDS, FORMAT,
    axis_digest, json_bytes, market_field)
from factorpool.market_adjustment import adjustment_policy, derive_adjustment
from factorpool.market_returns import daily_returns, return_policy
from .postgres import transaction


STORAGE_FORMAT = "field-blocks-v1"
# Bump this ABI whenever native R/Q output semantics change. Old IDs stay immutable.
KERNEL_ABI = "factorpool-market-cpp-v1"
BAR_TABLE = "cn_stock_daily_bar"
RETURN_TABLE = "cn_stock_daily_return"
ADJUSTMENT_TABLE = "cn_stock_daily_adj_factor"
INPUT_TABLE = "cn_stock_factor_input"
TABLES = (BAR_TABLE, RETURN_TABLE, ADJUSTMENT_TABLE, INPUT_TABLE)
ADJUSTMENT_DTYPES = {"__adj_factor__": "<f8", "__q__": "<f8",
                     "__segment__": "<i8", "__adj_known__": "|u1"}
BASE_DTYPES = {field: "<f8" for field in FIELDS}
BASE_DTYPES.update({field: "|u1" for field in BOOLEAN_FIELDS})
BASE_DTYPES.update(ADJUSTMENT_DTYPES)
_EXTRA_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def field_table(field):
    if field == "return":
        return RETURN_TABLE
    if field in ADJUSTMENT_DTYPES:
        return ADJUSTMENT_TABLE
    if field in FIELDS or field in BOOLEAN_FIELDS:
        return BAR_TABLE
    if isinstance(field, str) and _EXTRA_NAME.fullmatch(field):
        return INPUT_TABLE
    raise ValueError("Invalid supplied factor-input field name")


def field_catalog(extra_fields=()):
    catalog = {field: dict(table=field_table(field), dtype=dtype)
               for field, dtype in BASE_DTYPES.items()}
    for field in extra_fields:
        if field in catalog or field_table(field) != INPUT_TABLE:
            raise ValueError("Supplied factor-input field shadows a market field")
        catalog[field] = dict(table=INPUT_TABLE, dtype="<f8")
    return catalog


def block_digest(start, count, fields):
    return hashlib.sha256(json_bytes(dict(start_index=start, row_count=count,
                                         fields=fields))).hexdigest()


def checked_catalog(snapshot):
    catalog = snapshot.get("field_catalog", snapshot.get("manifest", {}).get("field_catalog"))
    if not isinstance(catalog, dict) or not set(BASE_DTYPES).issubset(catalog):
        raise ValueError("Incomplete market field catalog")
    for field, descriptor in catalog.items():
        if not isinstance(descriptor, dict) or descriptor != dict(
                table=field_table(field), dtype=BASE_DTYPES.get(field, "<f8")):
            raise ValueError("Invalid market field ownership or dtype")
    if snapshot.get("field_catalog", catalog) != snapshot["manifest"].get("field_catalog"):
        raise ValueError("Market field catalog differs from immutable manifest")
    return catalog


def checked_block(snapshot, block):
    records = snapshot.get("field_blocks", snapshot.get("manifest", {}).get("field_blocks", []))
    if not isinstance(records, list):
        raise ValueError("Invalid market field-block index")
    record = next((value for value in records if isinstance(value, dict)
                   and value.get("start_index") == block.get("start_index")), None)
    if (record is None or record.get("row_count") != block.get("row_count")
            or record.get("sha256") != block.get("sha256")
            or not isinstance(record.get("fields"), dict)
            or block_digest(record["start_index"], record["row_count"], record["fields"]) != record["sha256"]):
        raise ValueError("Market field-block index differs from pinned snapshot")
    return record


def decode_field(payload, sha256, count, symbols, dtype, *, verify=True):
    dtype = np.dtype(dtype)
    if dtype.str not in ("<f8", "<i8", "|u1") or count < 1 or symbols < 1:
        raise ValueError("Invalid market field-block shape or dtype")
    raw = bytes(payload)
    if (len(raw) != count * symbols * dtype.itemsize
            or verify and hashlib.sha256(raw).hexdigest() != sha256):
        raise ValueError("Market field-block integrity check failed")
    values = np.frombuffer(raw, dtype=dtype).reshape(count, symbols)
    if verify and dtype == np.dtype("|u1") and np.any(values > 1):
        raise ValueError("Market status block must be boolean")
    return values


def _input_arrays(inputs, shape):
    arrays, digests = {}, {}
    for field, values in (inputs or {}).items():
        if field in BASE_DTYPES or field_table(field) != INPUT_TABLE:
            raise ValueError("Supplied factor-input field shadows a market field")
        value = np.asarray(values)
        if value.shape != shape or value.dtype.kind not in "fiu":
            raise ValueError("Supplied factor inputs require numeric date-by-symbol arrays on the snapshot axes")
        # File mmap inputs retain bounded memory. Conversion is done per time block.
        arrays[field] = value
        digests[field] = _input_digest(value)
    return arrays, digests


def _input_digest(values):
    digest = hashlib.sha256()
    for start in range(0, values.shape[0], 64):
        digest.update(memoryview(np.ascontiguousarray(values[start:start+64], dtype="<f8")))
    return digest.hexdigest()


def _existing_index(conn, identity, catalog):
    output = {}
    for table in TABLES:
        rows = conn.execute(
            f"SELECT field,start_index,row_count,dtype,sha256 FROM {table} WHERE snapshot_id=%s ORDER BY field,start_index",
            (identity,)).fetchall()
        for field, start, count, dtype, checksum in rows:
            if field not in catalog or catalog[field] != dict(table=table, dtype=dtype):
                raise ValueError("Existing market field ownership differs")
            output[field, start] = (count, dtype, checksum)
    return output


def prepare_snapshot(source_snapshot_id, *, connection=None, source=None, factor_inputs=None, progress=None):
    """Publish a new immutable dataset derived only from explicitly supplied data.

    ``source`` uses a separate read connection when PostgreSQL is involved. This
    keeps each normal source read read-only while publication owns one atomic
    write transaction. Existing raw snapshots and research histories are never
    updated. Repeating this explicit operation validates and reuses its result.
    """
    from psycopg.types.json import Jsonb
    from .postgres.market import PostgresMarketSource
    source = source or PostgresMarketSource()
    if connection is not None and getattr(source, "connection", None) is connection:
        raise ValueError("Market preparation needs separate source/read and publication/write connections")
    started = time.perf_counter()
    parent = source.snapshot(source_snapshot_id)
    if parent["manifest"].get("storage_format") == STORAGE_FORMAT:
        inherited = [field for field, descriptor in checked_catalog(parent).items()
                     if descriptor["table"] == INPUT_TABLE]
    else:
        inherited = []
    supplied, input_digests = _input_arrays(factor_inputs,
        (len(parent["dates"]), len(parent["symbols"])))
    if set(inherited) & set(supplied):
        raise ValueError("Supplied factor inputs cannot replace fields in an immutable source")
    extras = sorted(set(inherited) | set(supplied))
    catalog = field_catalog(extras)
    identity_description = dict(storage_format=STORAGE_FORMAT, kernel_abi=KERNEL_ABI,
        parent_snapshot_id=parent["snapshot_id"], parent_axes=axis_digest(parent),
        parent_blocks=parent["blocks"], return_policy=return_policy(),
        adjustment_policy=adjustment_policy(), factor_inputs=input_digests)
    identity = hashlib.sha256(json_bytes(identity_description)).hexdigest()
    manifest = deepcopy(parent["manifest"])
    # A previous derived source's block index belongs to that source, never this publication.
    manifest.update(format=FORMAT, storage_format=STORAGE_FORMAT,
        parent_snapshot_id=parent["snapshot_id"], kernel_abi=KERNEL_ABI,
        derivation_identity=identity_description, field_catalog=catalog, field_blocks=[],
        return_policy=return_policy(), adjustment_policy=adjustment_policy(),
        layout="field,date,symbol", dtype="per-field", fields=list(FIELDS))
    if not supplied and parent["manifest"].get("storage_format") == STORAGE_FORMAT:
        previous_identity = parent["manifest"].get("derivation_identity", {})
        if (previous_identity.get("kernel_abi") == KERNEL_ABI
                and previous_identity.get("return_policy") == return_policy()
                and previous_identity.get("adjustment_policy") == adjustment_policy()):
            # Avoid making endless identical generations when pointed at the prepared dataset.
            identity = parent["snapshot_id"]
            manifest = deepcopy(parent["manifest"])
    written_bytes = 0
    count = 0
    with transaction(connection) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (identity,))
        existing = conn.execute("SELECT manifest,symbols,dates,fields FROM market_snapshots WHERE id=%s",
                                (identity,)).fetchone()
        if existing is not None:
            old_manifest, symbols, dates, fields = existing
            if (symbols != parent["symbols"] or dates != parent["dates"] or fields != list(FIELDS)
                    or old_manifest.get("derivation_identity") != manifest.get("derivation_identity")
                    or old_manifest.get("field_catalog") != catalog):
                raise ValueError("Existing derived market snapshot identity differs")
            records = old_manifest.get("field_blocks", [])
            expected = {(field, block["start_index"]): (block["row_count"], descriptor["dtype"], descriptor["sha256"])
                        for block in records for field, descriptor in block.get("fields", {}).items()}
            if (len(records) != len(parent["blocks"]) or set(expected) != {
                    (field, block["start_index"]) for field in catalog for block in parent["blocks"]}
                    or _existing_index(conn, identity, catalog) != expected):
                raise ValueError("Existing derived market snapshot has missing or inconsistent field blocks")
            for block, expected_block in zip(records, parent["blocks"]):
                if (block["start_index"] != expected_block["start_index"]
                        or block["row_count"] != expected_block["row_count"]
                        or block_digest(block["start_index"], block["row_count"], block["fields"]) != block["sha256"]):
                    raise ValueError("Existing derived market snapshot block index differs")
            return dict(snapshot_id=identity, parent_snapshot_id=old_manifest["parent_snapshot_id"],
                        dates=len(dates), symbols=len(symbols), blocks=len(records),
                        field_blocks=len(expected), created=False, elapsed=time.perf_counter()-started)
        conn.execute("INSERT INTO market_snapshots(id,manifest,symbols,dates,fields) VALUES(%s,%s,%s,%s,%s)",
            (identity, Jsonb(manifest), Jsonb(parent["symbols"]), Jsonb(parent["dates"]), Jsonb(list(FIELDS))))
        state = None
        raw_fields = tuple(field for field in FIELDS if field != "return")
        for block in parent["blocks"]:
            start, rows = block["start_index"], block["row_count"]
            requested = raw_fields + tuple(BOOLEAN_FIELDS) + tuple(inherited)
            if hasattr(source, "read_fields"):
                values = source.read_fields(parent, block, requested)
            else:
                original = source.original_block(parent, block)
                values = {field: market_field(original, field) for field in requested}
            values = dict(values)
            values["return"] = daily_returns(*(values[field] for field in
                ("close", "prev_close", "volume", "total_turnover", "member")))
            dates = np.array([int(day.replace("-", "")) for day in parent["dates"][start:start+rows]], dtype=np.int64)
            adjustment = derive_adjustment(*(values[field] for field in
                ("close", "prev_close", "volume", "total_turnover", "member", "is_suspended")),
                dates=dates, state=state)
            state = adjustment.state
            values.update(__adj_factor__=adjustment.factor, __q__=adjustment.q,
                          __segment__=adjustment.segment, __adj_known__=adjustment.known)
            values.update({field: array[start:start+rows] for field, array in supplied.items()})
            descriptors = {}
            for field, description in catalog.items():
                array = np.ascontiguousarray(values[field], dtype=description["dtype"])
                if array.shape != (rows, len(parent["symbols"])):
                    raise ValueError("Prepared market field axes differ from the source")
                if description["dtype"] == "|u1" and np.any(array > 1):
                    raise ValueError("Prepared market status must be boolean")
                payload = array.tobytes()
                checksum = hashlib.sha256(payload).hexdigest()
                descriptors[field] = dict(dtype=description["dtype"], sha256=checksum)
                conn.execute(f"INSERT INTO {description['table']}(snapshot_id,field,start_index,row_count,dtype,payload,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (identity, field, start, rows, description["dtype"], payload, checksum))
                written_bytes += len(payload)
                count += 1
            manifest["field_blocks"].append(dict(start_index=start, row_count=rows,
                fields=descriptors, sha256=block_digest(start, rows, descriptors)))
            if progress:
                progress(dict(snapshot_id=identity, completed_dates=start+rows,
                    total_dates=len(parent["dates"]), field_blocks=count,
                    raw_bytes=written_bytes, elapsed=time.perf_counter()-started))
        if any(_input_digest(supplied[field]) != digest for field, digest in input_digests.items()):
            raise ValueError("Supplied factor inputs changed during preparation")
        conn.execute("UPDATE market_snapshots SET manifest=%s WHERE id=%s", (Jsonb(manifest), identity))
    return dict(snapshot_id=identity, parent_snapshot_id=parent["snapshot_id"],
                dates=len(parent["dates"]), symbols=len(parent["symbols"]), blocks=len(parent["blocks"]),
                field_blocks=count, raw_bytes=written_bytes, created=True,
                elapsed=time.perf_counter()-started)


def main():
    parser = argparse.ArgumentParser(description="Explicitly derive and publish immutable C++ R/Q field tables")
    parser.add_argument("--source-snapshot-id", required=True)
    parser.add_argument("--input", action="append", default=[], metavar="FIELD=NUMPY_FILE",
                        help="An explicitly supplied numeric date-by-symbol .npy input on the source axes")
    args = parser.parse_args()
    inputs = {}
    for specification in args.input:
        field, separator, path = specification.partition("=")
        if not separator or not path or field in inputs:
            parser.error("--input requires a unique FIELD=NUMPY_FILE")
        inputs[field] = np.load(Path(path), allow_pickle=False, mmap_mode="r")
    report = lambda value: print(json.dumps(value, allow_nan=False), flush=True)
    report(prepare_snapshot(args.source_snapshot_id, factor_inputs=inputs, progress=report))


if __name__ == "__main__":
    main()
