"""Task-local quote matrices keep R and frame axes independent of bar dicts."""
import unittest

import numpy as np

from factorpool import market_loader as store
from factorpool.data_client.contract import FIELDS
store.FIELDS = FIELDS
from test_market_loading import _Connection


class MatrixProjectionTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.arange(len(store.FIELDS)*9*4, dtype=float).reshape(len(store.FIELDS), 9, 4)
        self.matrix[8] = 1
        self.matrix[8, :2, 1] = 0
        self.matrix[:7, 3, 2] = np.nan
        self.matrix[0, 4, 0], self.matrix[9, 5, 1] = np.inf, np.nan
        self.data = dict(snapshot_id="synthetic", factor_start="2024-01-03",
                         factor_end="2024-01-07")

    def test_quote_view_is_read_only_and_cannot_be_modified_via_returns(self):
        for pasteurization in ("ON", "OFF"):
            with self.subTest(pasteurization=pasteurization):
                payload = store.load(dict(self.data, universe=["A", "D"]), 3, 2,
                                     source=_Connection(self.matrix),
                                     pasteurization=pasteurization)
                quotes = payload["quotes"]["values"]
                original = quotes.copy()
                self.assertFalse(quotes.flags.writeable)
                self.assertFalse(np.shares_memory(quotes, payload["returns"]))
                with self.assertRaises(ValueError):
                    quotes[0, 0, 0] = 10
                payload["returns"][0, 0] = -123
                np.testing.assert_array_equal(quotes, original)

    def test_unpasteurized_inactive_research_symbol_has_no_quote_column(self):
        class InactiveConnection(_Connection):
            def fetchone(self):
                manifest, symbols, dates, fields = super().fetchone()
                manifest["instrument_lifetimes"][-1] = [20300101, 20990101]
                return manifest, symbols, dates, fields

        payload = store.load(dict(self.data, universe=["D", "A"]), 3, 2,
                             source=InactiveConnection(self.matrix),
                             pasteurization="OFF")
        self.assertEqual(payload["symbols"], ["A", "D"])
        self.assertEqual(payload["quotes"]["symbols"], ["A", "B", "C"])
        self.assertEqual(payload["quotes"]["research_columns"].tolist(), [0, -1])
        np.testing.assert_array_equal(payload["returns"], self.matrix[7, :, [0, 3]].T)



if __name__ == "__main__":
    unittest.main()
