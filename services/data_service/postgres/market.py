"""Authoritative immutable snapshot reads and original-block integrity checks."""
from datetime import datetime
import os

from factorpool.data_client.contract import (CACHE_VERSION, FIELDS, SERVED_FIELDS,
                                            decode_block, market_field, validate_snapshot)
from . import transaction


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
            blocks = conn.execute("SELECT start_index,row_count,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index", (identity,)).fetchall()
        self.stats["pg_snapshots"] += 1
        result = dict(snapshot_id=identity, manifest=manifest, symbols=symbols, dates=dates, fields=fields,
                      created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
                      blocks=[dict(start_index=start, row_count=count, sha256=sha) for start, count, sha in blocks],
                      cache=dict(version=CACHE_VERSION, codec=os.environ.get("FACTORPOOL_CACHE_CODEC", "raw"),
                                 chunk_max_bytes=int(os.environ.get("FACTORPOOL_CACHE_CHUNK_MAX_BYTES", "4194304")),
                                 tile_symbols=int(os.environ.get("FACTORPOOL_CACHE_TILE_SYMBOLS", "1024"))))
        return validate_snapshot(result, snapshot_id)

    def original_block(self, snapshot, block):
        with transaction(self.connection, readonly=True) as conn:
            row = conn.execute("SELECT row_count,payload,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s AND start_index=%s",
                               (snapshot["snapshot_id"], block["start_index"])).fetchone()
        if row is None or row[0] != block["row_count"] or row[2] != block["sha256"]:
            raise ValueError("Market block index differs from pinned snapshot")
        self.stats["pg_blocks"] += 1
        return decode_block(row[1], row[2], row[0], len(snapshot["symbols"]), snapshot["fields"])

    def selected_blocks(self, snapshot, initial, stop, columns, *, fields=None):
        import numpy as np
        fields = FIELDS if fields is None else tuple(fields)
        if not fields or len(set(fields)) != len(fields) or any(field not in SERVED_FIELDS for field in fields):
            raise ValueError("Invalid requested market fields")
        for block in snapshot["blocks"]:
            if block["start_index"] < stop and block["start_index"] + block["row_count"] > initial:
                original = self.original_block(snapshot, block)
                projected = np.stack([market_field(original, field)[:, columns] for field in fields])
                yield block["start_index"], block["row_count"], projected
