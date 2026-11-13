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
FORMAT = "factorpool-market-matrix-v1"
CACHE_VERSION = "matrix-v2"
PREFIX = b"FPC2"


def json_bytes(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


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
    raw = decompress(payload, expected)
    if hashlib.sha256(raw).hexdigest() != sha256:
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


def validate_snapshot(value, requested=None):
    if not isinstance(value, dict) or (requested and value.get("snapshot_id") != requested):
        raise ValueError("Market snapshot identity mismatch")
    if (not isinstance(value.get("manifest"), dict) or value["manifest"].get("format") != FORMAT
            or not isinstance(value.get("fields"), list) or tuple(value["fields"]) != FIELDS):
        raise ValueError("Unsupported market snapshot format")
    for name in ("symbols", "dates"):
        axis = value.get(name)
        if (not isinstance(axis, list) or not axis or any(not isinstance(x, str) or not x for x in axis)
                or any(a >= b for a, b in zip(axis, axis[1:]))):
            raise ValueError(f"Invalid market {name} axis")
    covered = 0
    if not isinstance(value.get("blocks"), list):
        raise ValueError("Invalid market block index")
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
    cache = value.get("cache", {})
    if not isinstance(cache, dict):
        raise ValueError("Invalid cache metadata object")
    if cache.get("version") != CACHE_VERSION or cache.get("codec") not in ("raw", "zlib"):
        raise ValueError("Unsupported cache format")
    if (type(cache.get("tile_symbols")) is not int or cache["tile_symbols"] < 1
            or type(cache.get("chunk_max_bytes")) is not int or cache["chunk_max_bytes"] < 8):
        raise ValueError("Invalid cache tile bounds")
    return value


def tile_width(metadata, block):
    cache = metadata["cache"]
    return max(1, min(cache["tile_symbols"], cache["chunk_max_bytes"] // (block["row_count"] * 8)))


def tile_key(metadata, block, field, symbol_start):
    if field not in FIELDS:
        raise ValueError("Unknown market field")
    snapshot = metadata["snapshot_id"]
    if not isinstance(snapshot, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot):
        raise ValueError("Invalid snapshot identifier")
    width = tile_width(metadata, block)
    if type(symbol_start) is not int or symbol_start < 0 or symbol_start >= len(metadata["symbols"]) or symbol_start % width:
        raise ValueError("Invalid market symbol tile")
    cache = metadata["cache"]
    return f"fp:{CACHE_VERSION}:{cache['codec']}:{width}:{snapshot}:{field}:{block['start_index']}:{symbol_start}"


def encode_tile(metadata, block, field, symbol_start, values, *, axes=None):
    array = np.ascontiguousarray(values, dtype="<f8")
    raw = array.tobytes()
    if len(raw) > max(metadata["cache"]["chunk_max_bytes"], block["row_count"] * 8):
        raise ValueError("Market cache chunk exceeds configured size")
    header = dict(version=CACHE_VERSION, snapshot_id=metadata["snapshot_id"], axes=axes or axis_digest(metadata),
                  field=field, start_index=block["start_index"], row_count=block["row_count"],
                  symbol_start=symbol_start, symbol_count=array.shape[1], dtype="<f8",
                  source_sha256=block["sha256"], sha256=hashlib.sha256(raw).hexdigest(),
                  codec=metadata["cache"]["codec"])
    data = raw if header["codec"] == "raw" else zlib.compress(raw, 1)
    header = json_bytes(header)
    return PREFIX + struct.pack("!I", len(header)) + header + data


def decode_tile(payload, metadata, block, field, symbol_start, *, axes=None):
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
    expected = dict(version=CACHE_VERSION, snapshot_id=metadata["snapshot_id"], axes=axes or axis_digest(metadata),
                    field=field, start_index=block["start_index"], row_count=block["row_count"],
                    symbol_start=symbol_start, symbol_count=symbols, dtype="<f8",
                    source_sha256=block["sha256"], codec=metadata["cache"]["codec"])
    if any(header.get(key) != value for key, value in expected.items()):
        raise ValueError("Market cache block axes or metadata mismatch")
    expected_bytes = block["row_count"] * symbols * 8
    raw = payload[8+size:]
    if header["codec"] == "zlib":
        raw = decompress(raw, expected_bytes)
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != header.get("sha256"):
        raise ValueError("Market cache block integrity check failed")
    return np.frombuffer(raw, dtype="<f8").reshape(block["row_count"], symbols)


def metadata_key(snapshot, layout=None):
    layout = layout or dict(version=CACHE_VERSION, codec=os.environ.get("FACTORPOOL_CACHE_CODEC", "raw"),
        chunk_max_bytes=int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304")),
        tile_symbols=int(os.environ.get("FACTORPOOL_CACHE_TILE_SYMBOLS", "1024")))
    fingerprint = hashlib.sha256(json_bytes(layout)).hexdigest()[:16]
    return f"fp:{CACHE_VERSION}:metadata:{fingerprint}:{snapshot}"


def pack_metadata(value):
    raw = json_bytes(value)
    return hashlib.sha256(raw).hexdigest().encode() + b"\n" + raw


def unpack_metadata(payload, snapshot):
    checksum, raw = bytes(payload).split(b"\n", 1)
    if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest().encode() != checksum:
        raise ValueError("Market metadata integrity check failed")
    return validate_snapshot(json.loads(raw), snapshot)


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
