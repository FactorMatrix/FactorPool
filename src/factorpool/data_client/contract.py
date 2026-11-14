"""Shared binary wire/cache contract; no service or database dependencies."""
import hashlib
import json
import os
import re
import struct
import zlib

import numpy as np

FIELDS = ("open", "high", "low", "close", "prev_close", "volume",
          "total_turnover", "return", "member", "is_st", "is_suspended")
PRESENT_FIELD = "__present__"
MEMBERSHIP_FIELD = "__member__"
BOOLEAN_FIELDS = (PRESENT_FIELD, MEMBERSHIP_FIELD)
SERVED_FIELDS = FIELDS + BOOLEAN_FIELDS
ADJUSTMENT_FIELDS = ("__q__", "__segment__", "__adj_factor__", "__adj_known__")
FIELD_BLOCK_FORMAT = "field-blocks-v1"
_TYPED_FIELDS = {**{field: "<f8" for field in FIELDS},
                 PRESENT_FIELD: "|u1", MEMBERSHIP_FIELD: "|u1",
                 "__q__": "<f8", "__segment__": "<i8", "__adj_factor__": "<f8", "__adj_known__": "|u1"}
_MARKET_TABLES = {"cn_stock_daily_bar", "cn_stock_daily_return", "cn_stock_daily_adj_factor", "cn_stock_factor_input"}
FORMAT = "factorpool-market-matrix-v1"
CACHE_VERSION = "matrix-v2"
PREFIX = b"FPC2"


def field_catalog(metadata):
    """Only the current published field catalog is addressable."""
    manifest = metadata.get("manifest", {})
    if manifest.get("storage_format") != FIELD_BLOCK_FORMAT:
        raise ValueError("Market reads require a current field-blocks-v1 snapshot")
    catalog = manifest.get("field_catalog")
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("Invalid market field catalog")
    return catalog


def field_dtype(metadata, field):
    descriptor = field_catalog(metadata).get(field)
    if not isinstance(descriptor, dict) or descriptor.get("dtype") not in ("<f8", "<i8", "|u1"):
        raise ValueError("Unknown market field or unsupported dtype")
    return np.dtype(descriptor["dtype"])


def _validate_field_catalog(value):
    manifest = value["manifest"]
    storage = manifest.get("storage_format")
    if storage is None:
        return
    if storage != FIELD_BLOCK_FORMAT:
        raise ValueError("Unsupported market storage format")
    catalog = field_catalog(value)
    if value.get("field_catalog", catalog) != catalog:
        raise ValueError("Market field catalog metadata mismatch")
    if not set(SERVED_FIELDS + ADJUSTMENT_FIELDS) <= set(catalog):
        raise ValueError("Market field catalog is incomplete")
    for field, descriptor in catalog.items():
        table = ('cn_stock_daily_return' if field == 'return' else
                 'cn_stock_daily_adj_factor' if field in ADJUSTMENT_FIELDS else
                 'cn_stock_daily_bar' if field in SERVED_FIELDS else 'cn_stock_factor_input')
        if (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", field)
                or field not in _TYPED_FIELDS and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", field)
                or not isinstance(descriptor, dict) or descriptor.get("table") != table
                or descriptor.get("dtype") != _TYPED_FIELDS.get(field, "<f8")):
            raise ValueError("Invalid market field catalog descriptor")
    indexed = manifest.get("field_blocks")
    if not isinstance(indexed, list) or len(indexed) != len(value["blocks"]):
        raise ValueError("Invalid market field block index")
    if value.get("field_blocks", indexed) != indexed:
        raise ValueError("Market field block metadata mismatch")
    for block, entry in zip(value["blocks"], indexed):
        if (not isinstance(entry, dict)
                or any(entry.get(key) != block[key] for key in ("start_index", "row_count", "sha256"))
                or not isinstance(entry.get("fields"), dict) or set(entry["fields"]) != set(catalog)):
            raise ValueError("Market field block metadata mismatch")
        for field, descriptor in entry["fields"].items():
            if (not isinstance(descriptor, dict) or descriptor.get("dtype") != catalog[field]["dtype"]
                    or not re.fullmatch(r"[0-9a-f]{64}", descriptor.get("sha256", ""))):
                raise ValueError("Invalid market field checksum or dtype")
        digest = hashlib.sha256(json_bytes({key: entry[key] for key in ('start_index', 'row_count', 'fields')})).hexdigest()
        if digest != entry['sha256']:
            raise ValueError("Market field block aggregate checksum mismatch")


def market_field(values, field):
    """Project stored fields or the exact original missing-bar predicate."""
    if field == PRESENT_FIELD:
        return np.any(np.isfinite(values[:7]), axis=0)
    if field == MEMBERSHIP_FIELD:
        return values[FIELDS.index("member")] > 0
    if field not in FIELDS:
        raise ValueError("Unknown market field")
    return values[FIELDS.index(field)]


def json_bytes(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def pack_block(values):
    values = np.ascontiguousarray(values, dtype="<f8")
    if values.ndim != 3 or values.shape[0] != len(FIELDS):
        raise ValueError("Expected field x date x symbol market block")
    raw = values.tobytes(order="C")
    return zlib.compress(raw, level=3), hashlib.sha256(raw).hexdigest()


def decode_block(payload, sha256, row_count, symbol_count, fields=FIELDS, *, verify=True):
    if tuple(fields) != FIELDS or row_count < 1 or symbol_count < 1:
        raise ValueError("Unsupported market block shape or fields")
    expected = len(FIELDS) * row_count * symbol_count * 8
    raw = decompress(payload, expected)
    if verify and hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Market matrix block integrity check failed")
    return np.frombuffer(raw, dtype="<f8").reshape(len(FIELDS), row_count, symbol_count)


def decompress(payload, expected):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(payload), expected + 1)
    if (len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
        raise ValueError("Market matrix block integrity check failed")
    return raw


def axis_digest(metadata):
    return hashlib.sha256(json_bytes([metadata["dates"], metadata["symbols"], metadata["fields"]])).hexdigest()


def validate_snapshot(value, requested=None, *, verify=True):
    if not isinstance(value, dict) or (requested and value.get("snapshot_id") != requested):
        raise ValueError("Market snapshot identity mismatch")
    if (not isinstance(value.get("manifest"), dict) or value["manifest"].get("format") != FORMAT
            or not isinstance(value.get("fields"), list) or tuple(value["fields"]) != FIELDS):
        raise ValueError("Unsupported market snapshot format")
    for name in ("symbols", "dates"):
        axis = value.get(name)
        if (not isinstance(axis, list) or not axis or verify and (any(not isinstance(x, str) or not x for x in axis)
                or any(a >= b for a, b in zip(axis, axis[1:])))):
            raise ValueError(f"Invalid market {name} axis")
    covered = 0
    if not isinstance(value.get("blocks"), list):
        raise ValueError("Invalid market block index")
    if verify:
        for block in value["blocks"]:
            if not isinstance(block, dict):
                raise ValueError("Invalid market block metadata object")
            start, count = block.get("start_index"), block.get("row_count")
            if type(start) is not int or type(count) is not int or start != covered or count < 1:
                raise ValueError("Market matrix blocks overlap or have missing dates")
            if not re.fullmatch(r"[0-9a-f]{64}", block.get("sha256", "")):
                raise ValueError("Invalid original market block checksum")
            covered += count
        if covered != len(value["dates"]):
            raise ValueError("Market matrix snapshot has missing blocks")
        _validate_field_catalog(value)
    cache = value.get("cache", {})
    if not isinstance(cache, dict):
        raise ValueError("Invalid cache metadata object")
    if cache.get("version") != CACHE_VERSION or cache.get("codec") not in ("raw", "zlib", "lz4"):
        raise ValueError("Unsupported cache format")
    if (type(cache.get("tile_symbols")) is not int or cache["tile_symbols"] < 1
            or type(cache.get("chunk_max_bytes")) is not int or cache["chunk_max_bytes"] < 8):
        raise ValueError("Invalid cache tile bounds")
    return value


def tile_width(metadata, block):
    cache = metadata["cache"]
    return max(1, min(cache["tile_symbols"], cache["chunk_max_bytes"] // (block["row_count"] * 8)))


def tile_key(metadata, block, field, symbol_start):
    field_dtype(metadata, field)
    snapshot = metadata["snapshot_id"]
    if not isinstance(snapshot, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot):
        raise ValueError("Invalid snapshot identifier")
    width = tile_width(metadata, block)
    if type(symbol_start) is not int or symbol_start < 0 or symbol_start >= len(metadata["symbols"]) or symbol_start % width:
        raise ValueError("Invalid market symbol tile")
    cache = metadata["cache"]
    return f"fp:{CACHE_VERSION}:{cache['codec']}:{width}:{snapshot}:{field}:{block['start_index']}:{symbol_start}"


def encode_tile(metadata, block, field, symbol_start, values, *, axes=None):
    dtype = field_dtype(metadata, field).str
    if dtype == '<i8' and np.asarray(values).dtype.kind != 'i':
        raise ValueError("Market segment cells must be signed integers")
    if dtype == "|u1" and not np.all((np.asarray(values) == 0) | (np.asarray(values) == 1)):
        raise ValueError("Market status cells must be boolean")
    array = np.ascontiguousarray(values, dtype=dtype)
    expected_shape = (block["row_count"], min(tile_width(metadata, block), len(metadata["symbols"])-symbol_start))
    if array.shape != expected_shape:
        raise ValueError("Market tile shape mismatch")
    raw = array.tobytes()
    if len(raw) > max(metadata["cache"]["chunk_max_bytes"], block["row_count"] * 8):
        raise ValueError("Market cache chunk exceeds configured size")
    header = dict(version=CACHE_VERSION, snapshot_id=metadata["snapshot_id"], axes=axes or axis_digest(metadata),
                  field=field, start_index=block["start_index"], row_count=block["row_count"],
                  symbol_start=symbol_start, symbol_count=array.shape[1], dtype=dtype,
                  source_sha256=block["sha256"], sha256=hashlib.sha256(raw).hexdigest(),
                  codec=metadata["cache"]["codec"])
    if header["codec"] == "lz4":
        import lz4.frame
        data = lz4.frame.compress(raw, compression_level=0, content_checksum=True, store_size=True)
    elif header["codec"] == "zlib":
        data = zlib.compress(raw, 1)
    elif header["codec"] == "raw":
        data = raw
    else:
        raise ValueError("Unsupported market cache codec")
    header = json_bytes(header)
    return PREFIX + struct.pack("!I", len(header)) + header + data


def decode_tile(payload, metadata, block, field, symbol_start, *, axes=None, verify=True):
    payload = bytes(payload)
    if len(payload) < 8 or payload[:4] != PREFIX:
        raise ValueError("Invalid market cache version")
    size = struct.unpack("!I", payload[4:8])[0]
    if not 1 <= size <= 4096 or len(payload) < 8 + size:
        raise ValueError("Invalid market cache header")
    try:
        header = json.loads(payload[8:8+size])
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid market cache header") from exc
    if not isinstance(header, dict):
        raise ValueError("Invalid market cache header object")
    width = tile_width(metadata, block)
    symbols = min(width, len(metadata["symbols"]) - symbol_start)
    dtype = field_dtype(metadata, field).str
    expected = dict(version=CACHE_VERSION, snapshot_id=metadata["snapshot_id"], axes=axes or axis_digest(metadata),
                    field=field, start_index=block["start_index"], row_count=block["row_count"],
                    symbol_start=symbol_start, symbol_count=symbols, dtype=dtype,
                    source_sha256=block["sha256"], codec=metadata["cache"]["codec"])
    if any(header.get(key) != value for key, value in expected.items()):
        raise ValueError("Market cache block axes or metadata mismatch")
    expected_bytes = block["row_count"] * symbols * np.dtype(dtype).itemsize
    raw = payload[8+size:]
    if header["codec"] == "zlib":
        raw = decompress(raw, expected_bytes)
    elif header["codec"] == "lz4":
        import lz4.frame
        try:
            if lz4.frame.get_frame_info(raw)["content_size"] != expected_bytes:
                raise ValueError("Market LZ4 frame size differs from bounded tile")
            decoder = lz4.frame.LZ4FrameDecompressor()
            raw = decoder.decompress(raw, max_length=expected_bytes+1)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("Market cache block integrity check failed")
        except RuntimeError as exc:
            raise ValueError("Market cache block integrity check failed") from exc
    elif header["codec"] != "raw":
        raise ValueError("Unsupported market cache codec")
    if len(raw) != expected_bytes or verify and hashlib.sha256(raw).hexdigest() != header.get("sha256"):
        raise ValueError("Market cache block integrity check failed")
    result = np.frombuffer(raw, dtype=dtype).reshape(block["row_count"], symbols)
    if verify and dtype == "|u1" and np.any(result > 1):
        raise ValueError("Market status cells must be boolean")
    return result


def metadata_key(snapshot, layout=None):
    layout = layout or dict(version=CACHE_VERSION, codec=os.environ.get("FACTORPOOL_CACHE_CODEC", "raw"),
        chunk_max_bytes=int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304")),
        tile_symbols=int(os.environ.get("FACTORPOOL_CACHE_TILE_SYMBOLS", "1024")))
    fingerprint = hashlib.sha256(json_bytes(layout)).hexdigest()[:16]
    return f"fp:{CACHE_VERSION}:metadata:{fingerprint}:{snapshot}"


def pack_metadata(value):
    raw = json_bytes(value)
    return hashlib.sha256(raw).hexdigest().encode() + b"\n" + raw


def unpack_metadata(payload, snapshot, *, verify=True):
    checksum, raw = bytes(payload).split(b"\n", 1)
    if len(raw) > 4 * 1024 * 1024 or verify and hashlib.sha256(raw).hexdigest().encode() != checksum:
        raise ValueError("Market metadata integrity check failed")
    return validate_snapshot(json.loads(raw), snapshot, verify=verify)


def pack_value_page(task, attempt, offset, limit, payload):
    header = json_bytes(dict(version="values-v1", task_id=task, attempt=attempt, offset=offset, limit=limit,
                             sha256=hashlib.sha256(payload).hexdigest()))
    return b"FPV1" + struct.pack("!I", len(header)) + header + payload


def unpack_value_page(cached, task, attempt, offset, limit):
    cached = bytes(cached)
    if len(cached) < 8 or cached[:4] != b"FPV1":
        raise ValueError("Invalid factor-value cache version")
    size = struct.unpack("!I", cached[4:8])[0]
    if not 1 <= size <= 4096 or len(cached) > 4 * 1024 * 1024 or len(cached) < size + 8:
        raise ValueError("Invalid factor-value cache size")
    metadata, payload = json.loads(cached[8:8+size]), cached[8+size:]
    if not isinstance(metadata, dict):
        raise ValueError("Invalid factor-value cache header object")
    expected = dict(version="values-v1", task_id=task, attempt=attempt, offset=offset, limit=limit)
    if (any(metadata.get(key) != value for key, value in expected.items())
            or metadata.get("sha256") != hashlib.sha256(payload).hexdigest()):
        raise ValueError("Factor-value cache identity/checksum mismatch")
    return payload
