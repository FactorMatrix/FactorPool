"""Authoritative immutable snapshot reads and original-block integrity checks."""
import os

from factorpool.data_client.contract import (CACHE_VERSION, FIELDS, SERVED_FIELDS,
                                            decode_block, market_field, validate_snapshot)
from . import transaction
from ..market_store import (STORAGE_FORMAT, checked_block, checked_catalog, decode_field)


class MarketSnapshotNotFound(LookupError):
    pass


class PostgresMarketSource:
    def __init__(self, connection=None):
        self.connection = connection
        self.stats = dict(pg_blocks=0, pg_snapshots=0)

    def snapshot(self, snapshot_id=None):
        with transaction(self.connection, readonly=True) as conn:
            query = "SELECT id,manifest,symbols,dates,fields,created_at FROM market_snapshots"
            args = ()
            if snapshot_id:
                query += " WHERE id=%s"
                args = (snapshot_id,)
            else:
                query += " ORDER BY created_at DESC,id DESC LIMIT 1"
            row = conn.execute(query, args).fetchone()
            if row is None:
                raise MarketSnapshotNotFound("Market snapshot is unavailable; import it explicitly first")
            identity, manifest, symbols, dates, fields, created = row
            if manifest.get("storage_format") == STORAGE_FORMAT:
                indexed = manifest.get("field_blocks")
                if not isinstance(indexed, list):
                    raise ValueError("Invalid immutable market field-block index")
                try:
                    blocks = [(item["start_index"], item["row_count"], item["sha256"])
                              for item in indexed]
                except (TypeError, KeyError) as exc:
                    raise ValueError("Invalid immutable market field-block index") from exc
            else:
                blocks = conn.execute("SELECT start_index,row_count,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index", (identity,)).fetchall()
        self.stats["pg_snapshots"] += 1
        result = dict(snapshot_id=identity, manifest=manifest, symbols=symbols, dates=dates, fields=fields,
                      created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
                      blocks=[dict(start_index=start, row_count=count, sha256=sha) for start, count, sha in blocks],
                      cache=dict(version=CACHE_VERSION, codec=os.environ.get("FACTORPOOL_CACHE_CODEC", "raw"),
                                 chunk_max_bytes=int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304")),
                                 tile_symbols=int(os.environ.get("FACTORPOOL_CACHE_TILE_SYMBOLS", "1024"))))
        if manifest.get("storage_format") == STORAGE_FORMAT:
            result.update(field_catalog=manifest.get("field_catalog"),
                          field_blocks=manifest.get("field_blocks"))
            checked_catalog(result)
            for block in result["blocks"]:
                checked_block(result, block)
        return validate_snapshot(result, snapshot_id)

    def original_block(self, snapshot, block):
        if snapshot["manifest"].get("storage_format") == STORAGE_FORMAT:
            import numpy as np
            values = self.read_fields(snapshot, block, FIELDS)
            return np.stack([values[field] for field in FIELDS])
        with transaction(self.connection, readonly=True) as conn:
            row = conn.execute("SELECT row_count,payload,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s AND start_index=%s",
                               (snapshot["snapshot_id"], block["start_index"])).fetchone()
        if row is None or row[0] != block["row_count"] or row[2] != block["sha256"]:
            raise ValueError("Market block index differs from pinned snapshot")
        self.stats["pg_blocks"] += 1
        return decode_block(row[1], row[2], row[0], len(snapshot["symbols"]), snapshot["fields"])

    def read_fields(self, snapshot, block, fields):
        """Fetch precisely the requested immutable fields, preserving their dtype."""
        fields = tuple(fields)
        if not fields or len(set(fields)) != len(fields):
            raise ValueError("Invalid requested market fields")
        if snapshot["manifest"].get("storage_format") != STORAGE_FORMAT:
            if any(field not in SERVED_FIELDS for field in fields):
                raise ValueError("Requested market input was not supplied in this snapshot")
            original = self.original_block(snapshot, block)
            result = {}
            for field in fields:
                value = market_field(original, field)
                value.setflags(write=False)
                result[field] = value
            return result
        catalog = checked_catalog(snapshot)
        if any(field not in catalog for field in fields):
            raise ValueError("Requested market input was not supplied in this snapshot")
        indexed = checked_block(snapshot, block)
        grouped = {}
        for field in fields:
            grouped.setdefault(catalog[field]["table"], []).append(field)
        found = {}
        with transaction(self.connection, readonly=True) as conn:
            for table, requested in grouped.items():
                rows = conn.execute(
                    f"SELECT field,row_count,dtype,payload,sha256 FROM {table} "
                    "WHERE snapshot_id=%s AND start_index=%s AND field=ANY(%s)",
                    (snapshot["snapshot_id"], block["start_index"], requested)).fetchall()
                for field, count, dtype, payload, checksum in rows:
                    descriptor = indexed["fields"].get(field)
                    if (field not in requested or field in found or count != block["row_count"]
                            or descriptor != dict(dtype=dtype, sha256=checksum)
                            or dtype != catalog[field]["dtype"]):
                        raise ValueError("Market field-block metadata differs from pinned snapshot")
                    found[field] = decode_field(payload, checksum, count, len(snapshot["symbols"]), dtype)
        if set(found) != set(fields):
            raise ValueError("Market field-block is missing from the pinned snapshot")
        self.stats["pg_blocks"] += 1
        self.stats["pg_fields"] = self.stats.get("pg_fields", 0) + len(found)
        return {field: found[field] for field in fields}

    def selected_columns(self, snapshot, initial, stop, columns, *, fields=None):
        fields = FIELDS if fields is None else tuple(fields)
        for block in snapshot["blocks"]:
            if block["start_index"] < stop and block["start_index"] + block["row_count"] > initial:
                values = self.read_fields(snapshot, block, fields)
                yield block["start_index"], block["row_count"], {
                    field: value[:, columns] for field, value in values.items()}

    def selected_blocks(self, snapshot, initial, stop, columns, *, fields=None):
        import numpy as np
        fields = FIELDS if fields is None else tuple(fields)
        for start, count, values in self.selected_columns(snapshot, initial, stop, columns, fields=fields):
            yield start, count, np.stack([values[field] for field in fields])
