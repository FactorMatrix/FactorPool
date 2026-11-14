"""Market bytes are checked at startup, rather than on every research read."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.contract import (PREFIX, FIELDS, axis_digest, decode_tile,
    encode_tile, json_bytes, tile_key, validate_snapshot)
from services.data_service.prepared_matrices import PreparedMatrices
from test_data_cache import fixture, MemoryRedis
from test_prepared_matrices import OriginalBlocks


class MarketReadPolicyTests(unittest.TestCase):
    def test_warm_market_read_decodes_without_hashing_the_data_again(self):
        metadata, values = fixture()
        block = metadata['blocks'][0]
        axes = axis_digest(metadata)
        original = encode_tile(metadata, block, 'close', 0, values[FIELDS.index('close'), :2, :2], axes=axes)
        size = struct.unpack('!I', original[4:8])[0]
        header = json.loads(original[8:8+size])
        header['sha256'] = '0'*64
        encoded = json_bytes(header)
        payload = PREFIX+struct.pack('!I', len(encoded))+encoded+original[8+size:]
        with self.assertRaisesRegex(ValueError, 'integrity'):
            decode_tile(payload, metadata, block, 'close', 0, axes=axes)
        cache = MemoryRedis()
        key = tile_key(metadata, block, 'close', 0)
        cache.data[key] = payload
        with DataClient(stub=object(), cache=cache) as client, \
                patch('factorpool.data_client.client.axis_digest', return_value=axes), \
                patch('factorpool.data_client.contract.hashlib.sha256', side_effect=AssertionError('Repeated data hash')):
            result = client._tiles(metadata, [(block, 'close', 0)])[key]
        np.testing.assert_array_equal(result, values[FIELDS.index('close'), :2, :2])
        self.assertFalse(result.flags.writeable)

    def test_task_metadata_does_not_repeat_the_complete_field_index_check(self):
        metadata, _ = fixture()
        with patch('factorpool.data_client.contract._validate_field_catalog',
                   side_effect=AssertionError('Full field-index check')):
            self.assertIs(validate_snapshot(metadata, 'cache-test', verify=False), metadata)
            with self.assertRaisesRegex(AssertionError, 'Full field-index'):
                validate_snapshot(metadata, 'cache-test')

    def test_prepared_data_is_checked_once_then_read_without_payload_hashes(self):
        source = OriginalBlocks()
        snapshot = source.snapshot('prepared-fixture')
        with tempfile.TemporaryDirectory() as directory:
            PreparedMatrices(directory).prepare(snapshot, source,
                fields=('return',), start=snapshot['dates'][0], end=snapshot['dates'][-1])
            cache = PreparedMatrices(directory, verify_reads=False)
            cache.validate(snapshot)
            self.assertEqual(cache.stats['field_reads'], 3)
            import hashlib
            with patch('services.data_service.prepared_matrices.hashlib.sha256', wraps=hashlib.sha256) as hashes:
                result = cache.tiles(snapshot, snapshot['blocks'][0], [('return', 0)])['return', 0]
            self.assertTrue(all(not isinstance(call.args[0], memoryview) for call in hashes.call_args_list))
            np.testing.assert_array_equal(result, source.matrix[FIELDS.index('return'), :3, :2])
            self.assertFalse(result.flags.writeable)

    def test_bad_optional_prepared_data_is_skipped_after_startup_check(self):
        source = OriginalBlocks()
        snapshot = source.snapshot('prepared-fixture')
        with tempfile.TemporaryDirectory() as directory:
            PreparedMatrices(directory).prepare(snapshot, source,
                fields=('return',), start=snapshot['dates'][0], end=snapshot['dates'][-1])
            path = Path(directory)/snapshot['snapshot_id']/'return.npy'
            values = np.load(path, mmap_mode='r+')
            values[0, 0] = 123
            values.flush()
            del values
            cache = PreparedMatrices(directory, verify_reads=False)
            cache.validate(snapshot)
            self.assertEqual(cache.stats['corrupt'], 1)
            self.assertEqual(cache.tiles(snapshot, snapshot['blocks'][0], [('return', 0)]), {})
