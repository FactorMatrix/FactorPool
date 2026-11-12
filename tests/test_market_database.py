"""Real PostgreSQL transactions, opt-in only inside the isolated test Compose."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import h5py
import numpy as np
from factorpool.bundle_import import import_bundle, iter_blocks
from factorpool.market_store import connect, load, transaction
from test_market_store import synthetic_bundle


@unittest.skipUnless(os.environ.get('FACTORPOOL_TEST_DB') == '1', 'Requires the isolated test PostgreSQL service')
class MarketDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fail before connecting or issuing any DDL if this is not the disposable
        # Compose service. Opt-in alone must never permit a production truncate.
        if (os.environ.get('FACTORPOOL_DB_HOST') != 'test-db'
                or os.environ.get('FACTORPOOL_DB_NAME') != 'factorpool'):
            raise RuntimeError('Database integration tests require host=test-db and dbname=factorpool')
        import psycopg
        cls.psycopg = psycopg
        cls.connection = connect()
        cls.connection.autocommit = True
        schema = Path('/opt/factorpool/schema.sql').read_text()
        with cls.connection.transaction():
            cls.connection.execute(schema)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'connection'):
            cls.connection.close()

    def setUp(self):
        self.connection.execute('TRUNCATE market_matrix_blocks, market_snapshots, research_tasks CASCADE')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        synthetic_bundle(self.root)

    def request(self, snapshot):
        return dict(provider='market_matrix', snapshot_id=snapshot,
                    universe='all_a_dynamic', factor_start='2024-01-02', factor_end='2024-01-04')

    def counts(self):
        return self.connection.execute(
            'SELECT (SELECT count(*) FROM market_snapshots), (SELECT count(*) FROM market_matrix_blocks)'
        ).fetchone()

    def test_atomic_import_read_and_idempotent_snapshot(self):
        first = import_bundle(self.root, connection=self.connection, block_days=2)
        self.assertTrue(first['created'])
        self.assertEqual(first['dates'], 3)
        self.assertEqual(first['symbols'], 3)
        self.assertEqual(first['blocks'], 2)
        self.assertEqual(self.counts(), (1, 2))
        second = import_bundle(self.root, connection=self.connection, block_days=2)
        self.assertFalse(second['created'])
        self.assertEqual(second['snapshot_id'], first['snapshot_id'])
        self.assertEqual(self.counts(), (1, 2))
        payload = load(self.request(first['snapshot_id']), 1, 0, connection=self.connection)
        self.assertEqual(payload['symbols'], ['A', 'B', 'C'])
        np.testing.assert_allclose(payload['returns'][[0, 2], 0], [.25, .5])
        self.assertTrue(np.isnan(payload['returns'][1, 0]))
        self.assertTrue(np.isnan(payload['returns'][:, 2]).all())
        self.assertEqual([frame['symbols'] for frame in payload['frames']],
                         [['A', 'C'], ['A', 'B', 'C'], ['A', 'C']])
        self.assertFalse(payload['synthetic'])
        self.assertEqual(payload['snapshot_id'], first['snapshot_id'])
        middle = self.request(first['snapshot_id'])
        middle.update(factor_start='2024-01-03', factor_end='2024-01-03')
        padded = load(middle, 2, 1, connection=self.connection)
        self.assertEqual([frame['date'] for frame in padded['frames']],
                         ['2024-01-02', '2024-01-03', '2024-01-04'])

    def test_new_snapshot_preserves_old_task_identity_and_blocks(self):
        from psycopg.types.json import Jsonb
        old = import_bundle(self.root, connection=self.connection, block_days=2)['snapshot_id']
        old_blocks = self.connection.execute(
            'SELECT start_index,payload,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index', (old,)
        ).fetchall()
        task = uuid.uuid4()
        self.connection.execute(
            'INSERT INTO research_tasks(id,name,snapshot,status) VALUES(%s,%s,%s,%s)',
            (task, 'synthetic fixed snapshot', Jsonb(dict(data=self.request(old))), 'QUEUED'),
        )
        with h5py.File(self.root/'stocks.h5', 'a') as stocks:
            row = stocks['A'][1]
            row['close'] = 8
            stocks['A'][1] = row
        new = import_bundle(self.root, connection=self.connection, block_days=2)['snapshot_id']
        self.assertNotEqual(old, new)
        self.assertEqual(self.counts(), (2, 4))
        persisted = self.connection.execute('SELECT snapshot FROM research_tasks WHERE id=%s', (task,)).fetchone()[0]
        self.assertEqual(persisted['data']['snapshot_id'], old)
        self.assertEqual(self.connection.execute(
            'SELECT start_index,payload,sha256 FROM market_matrix_blocks WHERE snapshot_id=%s ORDER BY start_index', (old,)
        ).fetchall(), old_blocks)
        old_data = load(persisted['data'], 1, 0, connection=self.connection)
        new_data = load(self.request(new), 1, 0, connection=self.connection)
        self.assertAlmostEqual(old_data['returns'][2, 0], .5)
        self.assertAlmostEqual(new_data['returns'][2, 0], 1)

    def test_mid_import_failure_rolls_back_metadata_and_all_blocks(self):
        def interrupted(plan, block_days=64):
            for index, block in enumerate(iter_blocks(plan, block_days)):
                if index == 1:
                    raise RuntimeError('Synthetic import interruption')
                yield block
        with patch('factorpool.bundle_import.iter_blocks', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic import interruption'):
                import_bundle(self.root, connection=self.connection, block_days=1)
        self.assertEqual(self.counts(), (0, 0))
        # The connection must be usable after rollback, and retry is explicit.
        complete = import_bundle(self.root, connection=self.connection, block_days=1)
        self.assertTrue(complete['created'])
        self.assertEqual(self.counts(), (1, 3))

    def test_reader_transaction_is_read_only_and_cannot_delete_market(self):
        import_bundle(self.root, connection=self.connection, block_days=2)
        with self.assertRaises(self.psycopg.errors.ReadOnlySqlTransaction):
            with transaction(self.connection, readonly=True) as reader:
                self.assertEqual(reader.execute('SHOW transaction_read_only').fetchone()[0], 'on')
                reader.execute('DELETE FROM market_snapshots')
        self.assertEqual(self.counts(), (1, 2))
        self.assertEqual(self.connection.execute('SHOW transaction_read_only').fetchone()[0], 'off')

    def test_corrupt_block_is_rejected_before_a_payload_is_returned(self):
        snapshot = import_bundle(self.root, connection=self.connection, block_days=2)['snapshot_id']
        self.connection.execute(
            'UPDATE market_matrix_blocks SET sha256=%s WHERE snapshot_id=%s AND start_index=0', ('0'*64, snapshot)
        )
        with self.assertRaisesRegex(ValueError, 'integrity'):
            load(self.request(snapshot), 1, 0, connection=self.connection)
