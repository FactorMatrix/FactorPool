"""Remote matrix assembly preserves the complete independent scalar contract."""
from datetime import date, timedelta
import unittest
from unittest.mock import patch

import numpy as np

from factorpool import market_loader as store
from factorpool.data_client.contract import FIELDS, FORMAT, decode_block, market_field, pack_block
from market_fixtures import field_snapshot


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
        metadata = dict(snapshot_id=identity, manifest=manifest, symbols=symbols, dates=dates, fields=fields,
            blocks=[dict(start_index=start, row_count=count, sha256=checksum)
                    for start, count, _, checksum in self.blocks])
        self.columns = field_snapshot(metadata, self.matrix)
        return metadata

    def selected_columns(self, snapshot, initial, stop, columns, *, fields):
        covered = initial
        for start, count, payload, checksum in self.blocks:
            if start+count <= initial:
                continue
            if not start <= covered < start+count or start+count > len(self.dates):
                raise ValueError("Market matrix blocks overlap or have missing dates")
            original = decode_block(payload, checksum, count, len(self.symbols))
            values = {field: (market_field(original, field)[:, columns] if field in FIELDS or
                             field in ('__present__', '__member__') else
                             self.columns[field][start:start+count, columns]) for field in fields}
            yield start, count, values
            covered = min(stop, start+count)
            if covered == stop:
                break
        if covered != stop:
            raise ValueError("Market matrix snapshot has missing blocks")



class MatrixLoadingTests(unittest.TestCase):
    def test_retired_storage_and_return_policies_fail_before_market_reads(self):
        class UnsupportedSource(_Connection):
            def __init__(self, matrix, key, value):
                super().__init__(matrix)
                self.key, self.value = key, value

            def snapshot(self, identity):
                metadata = super().snapshot(identity)
                metadata['manifest'][self.key] = self.value
                return metadata

        for key, value in (('storage_format', None), ('return_policy', None),
                           ('return_policy', {'version': 'legacy-price-ratio-v1'})):
            source = UnsupportedSource(np.ones((len(FIELDS), 3, 4)), key, value)
            with self.subTest(key=key, value=value), \
                    patch.object(source, 'selected_columns', side_effect=AssertionError('retired input was read')), \
                    self.assertRaisesRegex(ValueError, 'current'):
                store.load(dict(snapshot_id='retired', factor_start='2024-01-01',
                    factor_end='2024-01-03', universe=['A']), 1, 0,
                    source=source, fields=['close'])

    def test_lifetime_boundaries_follow_the_full_calendar_and_selected_columns(self):
        matrix = np.ones((len(FIELDS), 9, 4))
        calendar = ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04',
                    '2024-01-05', '2024-01-08', '2024-01-09', '2024-01-10',
                    '2024-01-11']
        # B ends on a Saturday. D's known end is beyond this task's loaded
        # dates, but remains inside the source snapshot and must be retained.
        lifetimes = [[20200101, 20240105], [20200101, 20240106],
                     [20200101, 20990101], [20200101, 20240110]]
        data = dict(snapshot_id='lifetime-calendar', factor_start='2024-01-03',
                    factor_end='2024-01-04', universe=['D', 'B', 'A', 'C'])
        for pasteurization in ('ON', 'OFF'):
            for projected in (False, True):
                with self.subTest(pasteurization=pasteurization, projected=projected):
                    source = _Connection(matrix)
                    source.dates = calendar
                    source.fetchone = lambda: (
                        {'format': FORMAT, 'instrument_lifetimes': lifetimes},
                        source.symbols, source.dates, list(FIELDS))
                    kwargs = dict(fields=['total_turnover']) if projected else {}
                    payload = store.load(data, 2, 0, source=source,
                                         pasteurization=pasteurization, **kwargs)
                    self.assertEqual(payload['symbols'], ['A', 'B', 'C', 'D'])
                    self.assertEqual([frame['date'] for frame in payload['frames']],
                                     calendar[1:4])
                    self.assertEqual(payload['terminal_exit_rows'], [3, 4, -1, 6])
                    # Adding lifecycle metadata never repairs or fills R.
                    np.testing.assert_array_equal(payload['returns'], np.ones((3, 4)))

    def test_lifetime_before_the_warmup_is_clamped_and_missing_metadata_stays_optional(self):
        matrix = np.ones((len(FIELDS), 9, 4))
        data = dict(snapshot_id='old-lifetime', factor_start='2024-01-05',
                    factor_end='2024-01-06', universe=['A', 'C'])
        source = _Connection(matrix)
        lifetimes = [[20200101, 20240101], [20200101, 20990101],
                     [20200101, 0], [20200101, 20990101]]
        source.fetchone = lambda: (
            {'format': FORMAT, 'instrument_lifetimes': lifetimes},
            source.symbols, source.dates, list(FIELDS))
        payload = store.load(data, 2, 0, source=source)
        self.assertEqual(payload['terminal_exit_rows'], [0, -1])
        source.fetchone = lambda: ({'format': FORMAT}, source.symbols,
                                  source.dates, list(FIELDS))
        self.assertNotIn('terminal_exit_rows', store.load(data, 2, 0, source=source))

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
                    source = _Connection(matrix)
                    actual = store.load(data, 3, 2, source=source, pasteurization=pasteurization)
                    dynamic = universe == 'all_a_dynamic'
                    selected = source.symbols if dynamic else sorted(universe)
                    calculation = source.symbols if pasteurization == 'OFF' else selected
                    research_columns = [source.symbols.index(symbol) for symbol in selected]
                    calculation_columns = [source.symbols.index(symbol) for symbol in calculation]
                    expected_quotes = np.asarray([[[matrix[k, t, n] for n in calculation_columns]
                        for t in range(7)] for k in range(len(FIELDS))])
                    expected_returns = np.asarray([[matrix[7, t, n] for n in research_columns]
                                                   for t in range(9)])
                    np.testing.assert_array_equal(actual['quotes']['values'], expected_quotes)
                    np.testing.assert_array_equal(actual['returns'], expected_returns)
                    self.assertEqual(actual['symbols'], selected)
                    self.assertEqual(actual['quotes']['symbols'], calculation)
                    for t, frame in enumerate(actual['frames']):
                        active = [symbol for symbol in calculation if matrix[8, t, source.symbols.index(symbol)] > 0]
                        research = [symbol for symbol in selected if matrix[8, t, source.symbols.index(symbol)] > 0]
                        expected_axis = active if dynamic or pasteurization == 'OFF' else calculation
                        self.assertEqual(frame['symbols'], expected_axis if t < 7 else [])
                        self.assertEqual(frame['research_symbols'], (research if dynamic else selected) if t < 7 else [])
                        self.assertEqual(frame['rows'], {})
                    self.assertFalse(actual['quotes']['values'].flags.writeable)

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
