"""Task-local quote matrices keep R and frame axes independent of bar dicts."""
import unittest
from unittest.mock import patch

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

    def test_full_returns_and_frame_axes_match_without_materializing_bars(self):
        for pasteurization in ("ON", "OFF"):
            for universe in (["D", "A"], "all_a_dynamic"):
                with self.subTest(pasteurization=pasteurization, universe=universe):
                    data = dict(self.data, universe=universe)
                    expected = store.load(data, 3, 2, source=_Connection(self.matrix),
                                          pasteurization=pasteurization)
                    with patch.object(store, "_bar_rows", side_effect=AssertionError("bar dicts constructed")):
                        actual = store.load(data, 3, 2, source=_Connection(self.matrix),
                                            pasteurization=pasteurization, matrix_only=True)
                    self.assertNotIn("quotes", expected)
                    self.assertEqual(actual["symbols"], expected["symbols"])
                    self.assertEqual(actual["return_policy"], expected["return_policy"])
                    np.testing.assert_array_equal(actual["returns"], expected["returns"])
                    for frame, original in zip(actual["frames"], expected["frames"]):
                        for key in ("date", "session_index", "symbols"):
                            self.assertEqual(frame[key], original[key])
                        self.assertEqual(frame["research_symbols"],
                                         original.get("research_symbols", original["symbols"]))
                        self.assertEqual(frame["rows"], {})
                    quotes = actual["quotes"]
                    self.assertEqual(quotes["fields"], store.FIELDS)
                    self.assertEqual(quotes["dates"], [f["date"] for f in expected["frames"][:7]])
                    source_columns = [_Connection(self.matrix).symbols.index(s) for s in quotes["symbols"]]
                    np.testing.assert_array_equal(quotes["values"], self.matrix[:, :7, source_columns])
                    self.assertEqual(quotes["research_columns"].tolist(),
                                     [quotes["symbols"].index(s) for s in actual["symbols"]])
                    self.assertEqual(actual["returns"].shape[0], 9)
                    self.assertEqual(quotes["values"].shape[1], 7)
                    self.assertTrue(all(not f["symbols"] for f in actual["frames"][7:]))

    def test_quote_view_is_read_only_and_cannot_be_modified_via_returns(self):
        for pasteurization in ("ON", "OFF"):
            with self.subTest(pasteurization=pasteurization):
                payload = store.load(dict(self.data, universe=["A", "D"]), 3, 2,
                                     source=_Connection(self.matrix),
                                     pasteurization=pasteurization, matrix_only=True)
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
                             pasteurization="OFF", matrix_only=True)
        self.assertEqual(payload["symbols"], ["A", "D"])
        self.assertEqual(payload["quotes"]["symbols"], ["A", "B", "C"])
        self.assertEqual(payload["quotes"]["research_columns"].tolist(), [0, -1])
        np.testing.assert_array_equal(payload["returns"], self.matrix[7, :, [0, 3]].T)

    def test_matrix_only_requires_an_explicit_boolean(self):
        for value in (None, 0, 1, "True", []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "matrix_only must be bool"):
                    store.load({}, 1, 1, matrix_only=value)


if __name__ == "__main__":
    unittest.main()
