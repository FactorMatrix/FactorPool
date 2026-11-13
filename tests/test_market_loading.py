"""Remote matrix assembly preserves the complete independent scalar contract."""
from datetime import date, timedelta
import unittest
from unittest.mock import patch

import numpy as np

from factorpool import market_loader as store
from factorpool.data_client.contract import FIELDS, FORMAT, decode_block, pack_block


class _Connection:
    """Deterministic in-memory source, with no database emulation in the loader."""
    def __init__(self, matrix):
        self.matrix = matrix
        self.symbols = ["A", "B", "C", "D"]
        self.dates = [(date(2024, 1, 1) + timedelta(days=n)).isoformat()
                      for n in range(matrix.shape[1])]
        self.blocks = []
        for start in range(0, matrix.shape[1], 2):
            payload, checksum = pack_block(matrix[:, start:start+2])
            self.blocks.append((start, min(2, matrix.shape[1]-start), payload, checksum))

    def fetchone(self):
        return ({"format": FORMAT, "instrument_lifetimes": [[20200101, 20990101]] * 4},
                self.symbols, self.dates, list(FIELDS))

    def snapshot(self, identity):
        manifest, symbols, dates, fields = self.fetchone()
        return dict(snapshot_id=identity, manifest=manifest, symbols=symbols, dates=dates, fields=fields)

    def selected_blocks(self, snapshot, initial, stop, columns):
        covered = initial
        for start, count, payload, checksum in self.blocks:
            if not start <= covered < start + count or start + count > len(self.dates):
                raise ValueError("Market matrix blocks overlap or have missing dates")
            yield start, count, decode_block(payload, checksum, count, len(self.symbols))[:, :, columns]
            covered = min(stop, start + count)
            if covered == stop:
                break
        if covered != stop:
            raise ValueError("Market matrix snapshot has missing blocks")


def _scalar_rows(values, symbols):
    rows = {}
    for n, symbol in enumerate(symbols):
        if not np.any(np.isfinite(values[:7, n])):
            continue
        row = {field: (float(values[k, n]) if np.isfinite(values[k, n]) else None)
               for k, field in enumerate(FIELDS) if field not in ("member", "return")}
        row["value"] = row["close"]
        rows[symbol] = row
    return rows


class MatrixLoadingTests(unittest.TestCase):
    def test_complete_payload_matches_scalar_reference_for_both_axes(self):
        rng = np.random.default_rng(924)
        matrix = rng.normal(size=(len(FIELDS), 9, 4))
        matrix[8] = 1
        matrix[8, 1:4, 1] = 0
        matrix[:7, 3, 2] = np.nan
        matrix[0, 4, 0], matrix[9, 5, 1], matrix[10, 6, 3] = np.inf, -np.inf, np.nan
        for pasteurization in ("ON", "OFF"):
            for universe in (["D", "A"], "all_a_dynamic"):
                with self.subTest(pasteurization=pasteurization, universe=universe):
                    data = dict(snapshot_id="synthetic", factor_start="2024-01-03",
                                factor_end="2024-01-07", universe=universe)
                    with patch.object(store, "_bar_rows", _scalar_rows):
                        expected = store.load(data, 3, 2, source=_Connection(matrix), pasteurization=pasteurization)
                    actual = store.load(data, 3, 2, source=_Connection(matrix), pasteurization=pasteurization)
                    np.testing.assert_array_equal(actual.pop("returns"), expected.pop("returns"))
                    self.assertEqual(actual, expected)

    def test_invalid_metadata_rejected_before_decode(self):
        matrix = np.ones((len(FIELDS), 9, 4))
        connection = _Connection(matrix)
        start, _, payload, checksum = connection.blocks[0]
        connection.blocks = [(start, 10000, payload, checksum)]
        with patch('test_market_loading.decode_block', side_effect=AssertionError("invalid metadata reached decode")):
            with self.assertRaisesRegex(ValueError, "overlap or have missing"):
                store.load(dict(snapshot_id="synthetic", factor_start="2024-01-03",
                                factor_end="2024-01-07", universe=["A"]), 3, 2, source=connection)

    def test_corruption_is_not_an_empty_market(self):
        matrix = np.ones((len(FIELDS), 9, 4))
        source = _Connection(matrix)
        source.blocks[0] = (*source.blocks[0][:3], "0" * 64)
        with self.assertRaisesRegex(ValueError, "integrity"):
            store.load(dict(snapshot_id="synthetic", factor_start="2024-01-03",
                            factor_end="2024-01-07", universe=["A"]), 3, 2, source=source)


if __name__ == "__main__":
    unittest.main()
