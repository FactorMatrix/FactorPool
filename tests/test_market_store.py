"""Storage contract tests use only temporary synthetic HDF5 inputs."""
import hashlib
import os
from pathlib import Path
import pickle
import tempfile
import unittest
import zlib

import h5py
import numpy as np
from factorpool.market_store import FIELDS, pack_block, decode_block
from factorpool.bundle_import import scan_bundle, iter_blocks


STOCK_DTYPE = np.dtype([('datetime', '<i8'), ('open', '<f8'), ('close', '<f8'),
                        ('high', '<f8'), ('low', '<f8'), ('prev_close', '<f8'),
                        ('limit_up', '<f8'), ('limit_down', '<f8'), ('volume', '<f8'),
                        ('total_turnover', '<f8')])


def stock_rows(records):
    rows = np.zeros(len(records), dtype=STOCK_DTYPE)
    for i, (day, close, previous) in enumerate(records):
        rows[i]['datetime'] = day*1000000
        for field in ('open', 'close', 'high', 'low'):
            rows[i][field] = close
        rows[i]['prev_close'] = previous
        rows[i]['volume'] = 5
        rows[i]['total_turnover'] = 50
    return rows


def synthetic_bundle(root):
    np.save(root/'trading_dates.npy', np.array([20240101, 20240102, 20240103, 20240104, 20240105], dtype='<i8'))
    instruments = [dict(order_book_id='A', type='CS', listed_date='2024-01-02', de_listed_date='0000-00-00'),
                   dict(order_book_id='B', type='CS', listed_date='2024-01-03', de_listed_date='2024-01-04'),
                   dict(order_book_id='C', type='CS', listed_date='2024-01-02', de_listed_date='0000-00-00')]
    with (root/'instruments.pk').open('wb') as handle:
        pickle.dump(instruments, handle)
    with h5py.File(root/'stocks.h5', 'w') as stocks:
        stocks['A'] = stock_rows([(20240102, 10, 8), (20240104, 6, 4)])
        stocks['B'] = stock_rows([(20240102, 20, 20), (20240103, 20, 20), (20240104, 20, 20)])
    for name in ('st_stock_days.h5', 'suspended_days.h5'):
        with h5py.File(root/name, 'w') as status:
            status['A'] = np.array([20240103], dtype='<i8')
            status['B'] = np.array([], dtype='<f8')  # Existing bundles may have empty float arrays.


def no_trade_bundle(root):
    """Existing bars with no-trade conflicts, plus a metadata-only security."""
    synthetic_bundle(root)
    with h5py.File(root/'stocks.h5', 'a') as stocks:
        del stocks['A']
        rows = stock_rows([(20240102, 10, 8), (20240103, 12, 10), (20240104, 6, np.nan)])
        rows['volume'] = [5, 0, 0]
        rows['total_turnover'] = [50, 0, 0]
        stocks['A'] = rows
        rows = stocks['B'][:]
        rows['volume'] = [0, 0, 5]
        rows['total_turnover'] = [0, 1, np.nan]
        rows['prev_close'][-1] = 10
        stocks['B'][:] = rows
    with h5py.File(root/'suspended_days.h5', 'a') as status:
        del status['B']
        status['B'] = np.array([20240102, 20240103, 20240104], dtype='<i8')
        status['C'] = np.array([20240103], dtype='<i8')


def bundle_sha256(root):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir() if path.is_file()}


def decoded_bundle(plan, block_days):
    return np.concatenate([decode_block(payload, checksum, count, len(plan.symbols))
                           for _, count, payload, checksum in iter_blocks(plan, block_days)], axis=1)


class MarketBlockTests(unittest.TestCase):
    def test_compressed_block_roundtrip_shape_and_integrity(self):
        matrix = np.arange(len(FIELDS)*3*4, dtype='<f8').reshape(len(FIELDS), 3, 4)
        matrix[0, 0, 0] = np.nan
        matrix[1, 1, 1] = 0
        payload, digest = pack_block(matrix)
        self.assertEqual(hashlib.sha256(zlib.decompress(payload)).hexdigest(), digest)
        decoded = decode_block(payload, digest, 3, 4)
        np.testing.assert_array_equal(decoded, matrix)
        self.assertEqual(decoded.dtype, np.dtype('<f8'))
        with self.assertRaises((ValueError, zlib.error)):
            decode_block(payload[:-3], digest, 3, 4)
        with self.assertRaises(ValueError):
            decode_block(payload, '0'*64, 3, 4)
        with self.assertRaises(ValueError):
            decode_block(payload, digest, 4, 4)

    def test_bundle_uses_stored_previous_close_and_preserves_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            plan = scan_bundle(root)
            self.assertEqual(list(plan.symbols), ['A', 'B', 'C'])
            self.assertEqual(list(plan.dates), ['2024-01-02', '2024-01-03', '2024-01-04'])
            self.assertEqual(scan_bundle(root).snapshot_id, plan.snapshot_id)
            chunks = list(iter_blocks(plan, block_days=2))
            self.assertEqual([(start, count) for start, count, _, _ in chunks], [(0, 2), (2, 1)])
            values = np.concatenate([decode_block(payload, digest, count, len(plan.symbols))
                                     for _, count, payload, digest in chunks], axis=1)
            field = {name: values[i] for i, name in enumerate(FIELDS)}
            # Raw close changes 10 -> 6, but the supplied current-day reference is 4.
            np.testing.assert_allclose(field['return'][[0, 2], 0], [.25, .5])
            self.assertTrue(np.isnan(field['return'][1, 0]))
            self.assertTrue(np.isnan(field['close'][1, 0]))
            np.testing.assert_allclose(field['prev_close'][[0, 2], 0], [8, 4])
            np.testing.assert_array_equal(field['member'][:, 1], [0, 1, 0])
            np.testing.assert_array_equal(field['member'][:, 2], [1, 1, 1])
            self.assertTrue(np.isnan(field['close'][:, 2]).all())
            self.assertTrue(np.isnan(field['return'][:, 2]).all())
            self.assertEqual(field['is_st'][1, 0], 1)
            self.assertEqual(field['is_suspended'][1, 0], 1)
            np.testing.assert_array_equal(field['return'][:, 1], [0, 0, 0])

    def test_no_trade_cleaning_changes_only_derived_returns_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            no_trade_bundle(root)
            original_files = bundle_sha256(root)
            plan = scan_bundle(root)
            values = decoded_bundle(plan, 2)
            fields = {name: values[i] for i, name in enumerate(FIELDS)}
            np.testing.assert_allclose(fields['return'][:, 0], [.25, 0, 0], rtol=0, atol=1e-15)
            self.assertTrue(np.isnan(fields['prev_close'][2, 0]))
            # Positive volume remains a price return even with a suspension flag,
            # unknown amount and an already-delisted membership boundary.
            self.assertEqual(fields['is_suspended'][2, 1], 1)
            self.assertEqual(fields['member'][2, 1], 0)
            self.assertEqual(fields['return'][2, 1], 1)
            # Pre-listing zero-volume quotes and conflicting amount stay unknown.
            self.assertEqual(fields['member'][0, 1], 0)
            self.assertTrue(np.isnan(fields['return'][:2, 1]).all())
            # A suspension marker cannot invent a bar or a zero return for C.
            self.assertEqual(fields['is_suspended'][1, 2], 1)
            self.assertTrue(np.isnan(fields['return'][:, 2]).all())
            with h5py.File(root/'stocks.h5', 'r') as stocks:
                for n, symbol in enumerate(plan.symbols):
                    if symbol not in stocks:
                        continue
                    rows = stocks[symbol][:]
                    positions = np.searchsorted(plan.calendar, rows['datetime']//1_000_000)
                    for k, field in enumerate(FIELDS[:7]):
                        np.testing.assert_array_equal(values[k, positions, n], rows[field])
            self.assertEqual(bundle_sha256(root), original_files)

    def test_cleaning_is_identical_across_date_block_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            no_trade_bundle(root)
            with h5py.File(root/'stocks.h5', 'a') as stocks:
                rows = stocks['B'][:]
                rows['volume'] = 0
                rows['total_turnover'] = 0
                stocks['B'][:] = rows
            expected = decoded_bundle(scan_bundle(root, 64), 64)
            # B lists on Jan 3 and delists on Jan 4; even explicit suspension
            # cannot produce no-trade zeros outside that half-open lifetime.
            np.testing.assert_allclose(expected[FIELDS.index('return'), :, 1], [np.nan, 0, np.nan], equal_nan=True)
            for block_days in (1, 2):
                with self.subTest(block_days=block_days):
                    actual = decoded_bundle(scan_bundle(root, block_days), block_days)
                    np.testing.assert_array_equal(actual, expected)

    def test_return_policy_is_part_of_the_snapshot_identity(self):
        from factorpool.market_returns import return_policy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            plan = scan_bundle(root)
            self.assertEqual(plan.manifest['return_policy'], return_policy())
            self.assertEqual(plan.manifest['return_policy']['version'], 'volume-zero-v1')
            self.assertEqual(plan.manifest['return_formula'], 'close/prev_close-1')
            import json
            legacy = dict(plan.manifest)
            del legacy['return_policy']
            legacy_id = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertNotEqual(plan.snapshot_id, legacy_id)
            changed = dict(plan.manifest, return_policy=dict(return_policy(), version='future-test-rule'))
            changed_id = hashlib.sha256(json.dumps(changed, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertNotEqual(plan.snapshot_id, changed_id)

    def test_source_change_rejects_the_planned_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            plan = scan_bundle(root)
            path = root/'stocks.h5'
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000_000))
            self.assertNotEqual(scan_bundle(root).snapshot_id, plan.snapshot_id)
            with self.assertRaises((ValueError, RuntimeError)):
                list(iter_blocks(plan, block_days=2))

    def test_status_dates_are_sets_with_arbitrary_order_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            with h5py.File(root/'st_stock_days.h5', 'a') as status:
                del status['A']
                status['A'] = np.array([20240104, 20240103, 20240103], dtype='<i8')
            plan = scan_bundle(root)
            chunks = list(iter_blocks(plan, block_days=2))
            values = np.concatenate([decode_block(payload, digest, count, len(plan.symbols))
                                     for _, count, payload, digest in chunks], axis=1)
            np.testing.assert_array_equal(values[FIELDS.index('is_st'), :, 0], [0, 1, 1])
            np.testing.assert_array_equal(values[FIELDS.index('is_suspended'), :, 0], [0, 1, 0])

    def test_status_dates_accept_integral_float_storage_and_reject_fractions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            with h5py.File(root/'suspended_days.h5', 'a') as status:
                del status['A']
                status['A'] = np.array([20240103.0], dtype='<f8')
            plan = scan_bundle(root)
            chunks = list(iter_blocks(plan))
            values = decode_block(chunks[0][2], chunks[0][3], chunks[0][1], len(plan.symbols))
            np.testing.assert_array_equal(values[FIELDS.index('is_suspended'), :, 0], [0, 1, 0])
            with h5py.File(root/'suspended_days.h5', 'a') as status:
                status['A'][0] = 20240103.5
            with self.assertRaises(ValueError):
                list(iter_blocks(scan_bundle(root)))

    def test_missing_previous_close_is_rejected_without_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_bundle(root)
            with h5py.File(root/'stocks.h5', 'a') as stocks:
                records = stocks['A'][:]
                incomplete = np.empty(records.shape, dtype=np.dtype([pair for pair in STOCK_DTYPE.descr if pair[0] != 'prev_close']))
                for name in incomplete.dtype.names:
                    incomplete[name] = records[name]
                del stocks['A']
                stocks['A'] = incomplete
            with self.assertRaises(ValueError):
                list(iter_blocks(scan_bundle(root)))
