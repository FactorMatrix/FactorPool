"""Read-only loading optimizations retain the immutable matrix contract."""
from contextlib import nullcontext
from datetime import date, timedelta
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from factorpool import market_store as store


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, *args):
        pass

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, matrix):
        self.matrix = matrix
        self.symbols = ["A", "B", "C", "D"]
        self.dates = [(date(2024, 1, 1) + timedelta(days=n)).isoformat()
                      for n in range(matrix.shape[1])]
        self.blocks = []
        for start in range(0, matrix.shape[1], 2):
            payload, checksum = store.pack_block(matrix[:, start:start+2])
            self.blocks.append((start, min(2, matrix.shape[1]-start), payload, checksum))

    def transaction(self):
        return nullcontext()

    def execute(self, query, *args):
        return self

    def fetchone(self):
        return ({"format": store.FORMAT,
                 "instrument_lifetimes": [[20200101, 20990101]] * 4},
                self.symbols, self.dates, list(store.FIELDS))

    def cursor(self, **kwargs):
        return _Cursor(self.blocks)


def _serial_blocks(rows, symbol_count, fields, columns):
    for start, count, payload, checksum in rows:
        decoded = store.decode_block(payload, checksum, count, symbol_count, fields)
        yield start, count, decoded[:, :, columns]


def _scalar_rows(values, symbols):
    rows = {}
    for n, symbol in enumerate(symbols):
        if not np.any(np.isfinite(values[:7, n])):
            continue
        row = {field: (float(values[k, n]) if np.isfinite(values[k, n]) else None)
               for k, field in enumerate(store.FIELDS) if field not in ("member", "return")}
        row["value"] = row["close"]
        rows[symbol] = row
    return rows


class MatrixLoadingTests(unittest.TestCase):
    def test_complete_payload_matches_scalar_reference_for_both_axes(self):
        rng = np.random.default_rng(924)
        matrix = rng.normal(size=(len(store.FIELDS), 9, 4))
        matrix[8] = 1
        matrix[8, 1:4, 1] = 0
        matrix[:7, 3, 2] = np.nan
        matrix[0, 4, 0], matrix[9, 5, 1], matrix[10, 6, 3] = np.inf, -np.inf, np.nan
        for pasteurization in ("ON", "OFF"):
            for universe in (["D", "A"], "all_a_dynamic"):
                with self.subTest(pasteurization=pasteurization, universe=universe):
                    connection = _Connection(matrix)
                    data = dict(snapshot_id="synthetic", factor_start="2024-01-03",
                                factor_end="2024-01-07", universe=universe)
                    with patch.object(store, "_selected_blocks", _serial_blocks), \
                            patch.object(store, "_bar_rows", _scalar_rows):
                        expected = store.load(data, 3, 2, connection=connection,
                                              pasteurization=pasteurization)
                    actual = store.load(data, 3, 2, connection=connection,
                                        pasteurization=pasteurization)
                    np.testing.assert_array_equal(actual.pop("returns"), expected.pop("returns"))
                    self.assertEqual(actual, expected)

    def test_parallel_decode_is_bounded_and_keeps_order(self):
        lock = threading.Lock()
        active = peak = 0
        rows = [(n, 1, b"", "") for n in range(13)]

        def decode(row, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.004 if row[0] % 2 else .012)
            with lock:
                active -= 1
            return row[0], row[1], np.array([row[0]])

        with patch.object(store, "_decode_selected", decode), \
                patch.object(store, "_DECODE_WORKERS", 3), patch.object(store.os, "cpu_count", return_value=8):
            actual = list(store._selected_blocks(iter(rows), 1, store.FIELDS, np.array([0])))
        self.assertEqual([row[0] for row in actual], list(range(13)))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 3)

    def test_single_block_avoids_threads_and_corruption_is_rejected(self):
        matrix = np.arange(len(store.FIELDS)*3*4).reshape(len(store.FIELDS), 3, 4)
        payload, checksum = store.pack_block(matrix)
        with patch.object(store, "ThreadPoolExecutor", side_effect=AssertionError("unneeded worker")):
            actual = list(store._selected_blocks([(0, 3, payload, checksum)], 4, store.FIELDS, np.array([3, 0])))
        np.testing.assert_array_equal(actual[0][2], matrix[:, :, [3, 0]])
        with self.assertRaises(ValueError):
            list(store._selected_blocks([(0, 3, payload, checksum), (3, 3, payload, "0"*64)],
                                        4, store.FIELDS, np.array([0])))

    def test_metadata_is_rejected_before_decode_and_memory_budget_bounds_read_ahead(self):
        matrix = np.ones((len(store.FIELDS), 9, 4))
        connection = _Connection(matrix)
        start, _, payload, checksum = connection.blocks[0]
        connection.blocks = [(start, 10000, payload, checksum)]
        with patch.object(store, "decode_block", side_effect=AssertionError("invalid metadata reached decode")):
            with self.assertRaisesRegex(ValueError, "overlap or have missing"):
                store.load(dict(snapshot_id="synthetic", factor_start="2024-01-03",
                                factor_end="2024-01-07", universe=["A"]),
                           3, 2, connection=connection)
        active = peak = 0
        lock = threading.Lock()

        def decode(row, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.004)
            with lock:
                active -= 1
            return row[0], row[1], np.array([row[0]])

        with patch.object(store, "_decode_selected", decode), \
                patch.object(store, "_DECODE_BYTES", len(store.FIELDS)*8), \
                patch.object(store, "_DECODE_WORKERS", 4):
            actual = list(store._selected_blocks([(n, 1, b"", "") for n in range(9)],
                                                1, store.FIELDS, np.array([0])))
        self.assertEqual([row[0] for row in actual], list(range(9)))
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
