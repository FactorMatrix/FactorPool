"""Explicit, rebuildable column tables over immutable PostgreSQL snapshots."""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import argparse
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import stat
import threading
import uuid

import numpy as np

from factorpool.data_client.contract import (BOOLEAN_FIELDS, axis_digest, field_catalog, field_dtype,
    json_bytes, market_field, tile_key, tile_width, validate_snapshot)


VERSION = "prepared-columns-v1"
_MANIFEST_LIMIT = 4 * 1024**2
_FLUSH_BYTES = 32 * 1024**2


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PreparedMatrices:
    """The data service owns these files; workers only use the existing RPC."""
    def __init__(self, root=None):
        self.root = Path(root if root is not None else os.environ.get(
            "FACTORPOOL_MATRIX_DIR", "/data/matrices"))
        self._manifests = OrderedDict()
        self._lock = threading.Lock()
        self.stats = dict(hits=0, misses=0, corrupt=0, field_reads=0)

    def _directory(self, snapshot):
        identity = snapshot["snapshot_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
            raise ValueError("Invalid prepared snapshot identity")
        directory = self.root / identity
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Prepared snapshot directory escapes its owner")
        return directory

    def _manifest(self, snapshot):
        path = self._directory(snapshot) / "manifest.json"
        if path.is_symlink():
            raise ValueError("Prepared manifest must be an owned regular file")
        with path.open("rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MANIFEST_LIMIT:
                raise ValueError("Invalid prepared manifest length")
            axes = axis_digest(snapshot)
            key = (str(path), _signature(info), axes)
            with self._lock:
                cached = self._manifests.get(key)
                if cached is not None:
                    self._manifests.move_to_end(key)
                    return cached
            value = json.loads(source.read(_MANIFEST_LIMIT + 1))
        expected_shape = [len(snapshot["dates"]), len(snapshot["symbols"])]
        if (not isinstance(value, dict) or value.get("version") != VERSION
                or value.get("snapshot_id") != snapshot["snapshot_id"]
                or value.get("axes") != axes or value.get("shape") != expected_shape
                or not isinstance(value.get("fields"), dict)):
            raise ValueError("Prepared manifest identity or axes mismatch")
        with self._lock:
            self._manifests[key] = value
            while len(self._manifests) > 16:
                self._manifests.popitem(last=False)
        return value

    def _read(self, snapshot, block, field, descriptor, starts=None):
        if (field not in field_catalog(snapshot) or not isinstance(descriptor, dict)
                or descriptor.get("file") != field + ".npy"
                or descriptor.get("dtype") != field_dtype(snapshot, field).str
                or not isinstance(descriptor.get("blocks"), dict)):
            raise ValueError("Invalid prepared field description")
        record = descriptor["blocks"].get(str(block["start_index"]))
        if (not isinstance(record, dict) or record.get("row_count") != block["row_count"]
                or record.get("source_sha256") != block["sha256"]
                or not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", ""))):
            raise ValueError("Prepared block is missing or differs from source")
        path = self._directory(snapshot) / descriptor["file"]
        if path.is_symlink():
            raise ValueError("Prepared field must be an owned regular file")
        mapped = values = section = None
        try:
            with path.open("rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Prepared field is not a regular file")
                version = np.lib.format.read_magic(source)
                if version == (1, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_1_0(source, max_header_size=4096)
                elif version == (2, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_2_0(source, max_header_size=4096)
                else:
                    raise ValueError("Unsupported prepared array format")
                expected_shape = (len(snapshot["dates"]), len(snapshot["symbols"]))
                if (tuple(shape) != expected_shape or fortran or dtype != field_dtype(snapshot, field)
                        or before.st_size != source.tell() + int(np.prod(shape)) * dtype.itemsize):
                    raise ValueError("Prepared field shape, dtype or length mismatch")
                mapped = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
                values = np.ndarray(shape, dtype=dtype, buffer=mapped, offset=source.tell())
                start, count = block["start_index"], block["row_count"]
                # Verify an owned block copy on every prepared-cache read.
                # mmap writes and storage corruption need not change stat times.
                section = np.array(values[start:start+count], copy=True, order="C")
                if hashlib.sha256(memoryview(section)).hexdigest() != record["sha256"]:
                    raise ValueError("Prepared field block checksum mismatch")
                if dtype == np.dtype('|u1') and np.any(section > 1):
                    raise ValueError("Prepared status block is not boolean")
                self.stats["field_reads"] += 1
                if starts is None:
                    result = section
                else:
                    width = tile_width(snapshot, block)
                    result = {column: np.array(section[:, column:column+width], copy=True, order="C")
                              for column in starts}
                    for tile in result.values():
                        tile.setflags(write=False)
                if _signature(os.fstat(source.fileno())) != _signature(before):
                    raise ValueError("Prepared field changed during its read")
                return result
        finally:
            section = values = None
            if mapped is not None:
                mapped.close()

    def tiles(self, snapshot, block, requests):
        """Missing or damaged optional columns fall back to PostgreSQL exactly."""
        requested = list(requests)
        if not requested:
            return {}
        try:
            manifest = self._manifest(snapshot)
        except (OSError, ValueError, TypeError, KeyError, EOFError):
            self.stats["misses"] += len(requested)
            return {}
        grouped = {}
        for field, column in requested:
            tile_key(snapshot, block, field, column)
            grouped.setdefault(field, []).append(column)
        output = {}
        for field, columns in grouped.items():
            descriptor = manifest["fields"].get(field)
            if descriptor is None or (isinstance(descriptor, dict)
                    and isinstance(descriptor.get("blocks"), dict)
                    and str(block["start_index"]) not in descriptor["blocks"]):
                self.stats["misses"] += len(columns)
                continue
            try:
                arrays = self._read(snapshot, block, field, descriptor, columns)
                output.update({(field, column): array for column, array in arrays.items()})
                self.stats["hits"] += len(arrays)
            except (OSError, ValueError, TypeError, KeyError, EOFError, BufferError):
                self.stats["misses"] += len(columns)
                self.stats["corrupt"] += 1
        return output

    @contextmanager
    def _writer(self, snapshot):
        directory = self._directory(snapshot)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / ".prepare.lock"
        if path.is_symlink():
            raise ValueError("Prepared writer lock must be owned")
        with path.open("a+b") as lock:
            os.chmod(path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield directory
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def prepare(self, snapshot, source, *, fields, start, end):
        """Explicitly materialize a bounded date range; never rewrite PG data."""
        validate_snapshot(snapshot, snapshot["snapshot_id"])
        first, last = date.fromisoformat(start).isoformat(), date.fromisoformat(end).isoformat()
        if first > last:
            raise ValueError("Prepared start must not follow end")
        requested = tuple(dict.fromkeys(fields))
        if not requested or any(field not in field_catalog(snapshot) for field in requested):
            raise ValueError("Specify supported prepared fields")
        targets = [block for block in snapshot["blocks"]
                   if snapshot["dates"][block["start_index"]] <= last
                   and snapshot["dates"][block["start_index"]+block["row_count"]-1] >= first]
        if not targets:
            raise ValueError("Prepared range contains no snapshot dates")
        shape = (len(snapshot["dates"]), len(snapshot["symbols"]))
        stats = dict(snapshot_id=snapshot["snapshot_id"], fields=list(requested),
                     start=first, end=last, pg_block_reads=0, blocks_prepared=0, reused_blocks=0,
                     prepared_bytes=0, published_fields=[])
        with self._writer(snapshot) as directory:
            try:
                existing = self._manifest(snapshot)
            except (OSError, ValueError, TypeError, KeyError, EOFError):
                existing = dict(version=VERSION, snapshot_id=snapshot["snapshot_id"],
                                axes=axis_digest(snapshot), shape=list(shape), fields={})
            publication = deepcopy(existing)
            pending, matrices, paths = {}, {}, {}
            try:
                for field in requested:
                    previous = existing["fields"].get(field)
                    missing = []
                    for block in targets:
                        try:
                            self._read(snapshot, block, field, previous)
                            stats["reused_blocks"] += 1
                        except (OSError, ValueError, TypeError, KeyError, EOFError, BufferError):
                            missing.append(block["start_index"])
                    if not missing:
                        continue
                    pending[field] = set(missing)
                    path = directory / ("." + field + "." + uuid.uuid4().hex + ".npy.tmp")
                    matrices[field] = np.lib.format.open_memmap(path, mode="w+", dtype=field_dtype(snapshot, field), shape=shape)
                    os.chmod(path, 0o600)
                    paths[field] = path
                    descriptor = dict(file=field+".npy", dtype=field_dtype(snapshot, field).str, blocks={})
                    publication["fields"][field] = descriptor
                    # Copy only checksum-verified old coverage. Unprepared file
                    # holes are never advertised by the publication marker.
                    copied = 0
                    for block in snapshot["blocks"]:
                        try:
                            old = self._read(snapshot, block, field, previous)
                        except (OSError, ValueError, TypeError, KeyError, EOFError, BufferError):
                            continue
                        row, count = block["start_index"], block["row_count"]
                        matrices[field][row:row+count] = old
                        descriptor["blocks"][str(row)] = deepcopy(previous["blocks"][str(row)])
                        copied += old.nbytes
                        if copied >= _FLUSH_BYTES:
                            matrices[field].flush()
                            copied = 0
                    matrices[field].flush()
                dirty = 0
                for block in targets:
                    wanted = [field for field, missing in pending.items() if block["start_index"] in missing]
                    if not wanted:
                        continue
                    if hasattr(source, 'read_fields'):
                        original = source.read_fields(snapshot, block, wanted)
                    else:
                        original = source.original_block(snapshot, block)
                    stats["pg_block_reads"] += 1
                    row, count = block["start_index"], block["row_count"]
                    for field in wanted:
                        column = original[field] if isinstance(original, dict) else market_field(original, field)
                        values = np.ascontiguousarray(column, dtype=field_dtype(snapshot, field))
                        matrices[field][row:row+count] = values
                        publication["fields"][field]["blocks"][str(row)] = dict(
                            row_count=count, source_sha256=block["sha256"],
                            sha256=hashlib.sha256(memoryview(values)).hexdigest())
                        stats["blocks_prepared"] += 1
                        stats["prepared_bytes"] += values.nbytes
                        dirty += values.nbytes
                    if dirty >= _FLUSH_BYTES:
                        for matrix in matrices.values():
                            matrix.flush()
                        dirty = 0
                if not matrices:
                    return stats
                for matrix in matrices.values():
                    matrix.flush()
                    matrix._mmap.close()
                matrices.clear()
                marker = directory / (".manifest." + uuid.uuid4().hex + ".tmp")
                paths["manifest"] = marker
                with marker.open("wb") as output:
                    output.write(json_bytes(publication))
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(marker, 0o600)
                for field, path in paths.items():
                    if field != "manifest":
                        os.replace(path, directory / (field + ".npy"))
                        stats["published_fields"].append(field)
                os.replace(marker, directory / "manifest.json")
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return stats
            finally:
                for matrix in matrices.values():
                    matrix._mmap.close()
                for path in paths.values():
                    path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--fields", nargs="+", required=True)
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    from .postgres.market import PostgresMarketSource
    source = PostgresMarketSource()
    snapshot = source.snapshot(args.snapshot_id)
    print(json.dumps(PreparedMatrices(args.root).prepare(snapshot, source, fields=args.fields,
          start=args.start, end=args.end), ensure_ascii=False))


if __name__ == "__main__":
    main()
