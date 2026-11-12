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
