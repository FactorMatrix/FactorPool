"""Optional LZ4 market tiles retain typed bytes and bounded cache validation."""
from copy import deepcopy
import gc
import hashlib
import json
import os
import struct
import unittest
from unittest.mock import patch

import lz4.frame
import numpy as np

from factorpool.data_client.contract import (ADJUSTMENT_FIELDS, MEMBERSHIP_FIELD,
    PRESENT_FIELD, decode_tile, encode_tile, json_bytes, metadata_key, tile_key,
    validate_snapshot)
import test_data_service_boundary as grpc_cases
from test_typed_market_bridge import ColumnSource


def replace_body(payload, body, **updates):
    size = struct.unpack('!I', payload[4:8])[0]
    header = json.loads(payload[8:8+size])
    header.update(updates)
    encoded = json_bytes(header)
    return payload[:4] + struct.pack('!I', len(encoded)) + encoded + body


class MarketTileLz4Tests(unittest.TestCase):
    def setUp(self):
        self.source = ColumnSource()
        self.metadata = deepcopy(self.source.metadata)
        self.metadata['cache']['codec'] = 'lz4'
        self.block = self.metadata['blocks'][0]

    def test_typed_round_trip_preserves_bits_and_owned_readonly_alignment(self):
        floating = np.arange(18, dtype='<f8').reshape(9, 2)
        floating.view('<u8')[0] = (0x8000000000000000, 0x7ff8000000000042)
        for field, values in [('close', floating),
                              ('__segment__', np.full((9, 2), 2**53+7, dtype='<i8')),
                              (PRESENT_FIELD, np.ones((9, 2), dtype='|u1'))]:
            with self.subTest(field=field):
                payload = bytearray(encode_tile(self.metadata, self.block, field, 0, values))
                result = decode_tile(memoryview(payload).toreadonly(), self.metadata, self.block, field, 0)
                payload[:] = b'X'*len(payload)
                del payload
                gc.collect()
                self.assertEqual(result.dtype, values.dtype)
                self.assertEqual(result.tobytes(), values.tobytes())
                self.assertTrue(result.flags.c_contiguous)
                self.assertTrue(result.flags.aligned)
                self.assertFalse(result.flags.writeable)
                with self.assertRaises(ValueError):
                    result.setflags(write=True)

    def test_lz4_frames_reject_truncation_trailing_data_checksum_and_wrong_size(self):
        values = self.source.columns['close'][:, :2]
        payload = encode_tile(self.metadata, self.block, 'close', 0, values)
        size = struct.unpack('!I', payload[4:8])[0]
        frame = payload[8+size:]
        corrupted = bytearray(frame)
        corrupted[-1] ^= 1
        for body in (b'', frame[:-1], frame+frame, frame+b'garbage', corrupted,
                     lz4.frame.compress(values.tobytes()+values.tobytes(), store_size=True),
                     lz4.frame.compress(values.tobytes(), store_size=False)):
            with self.subTest(length=len(body)), self.assertRaises(ValueError):
                decode_tile(replace_body(payload, body), self.metadata, self.block, 'close', 0)

    def test_oversized_frame_is_rejected_before_decompression(self):
        values = self.source.columns['close'][:, :2]
        payload = encode_tile(self.metadata, self.block, 'close', 0, values)
        oversized = lz4.frame.compress(values.tobytes()*2, store_size=True)
        with patch('lz4.frame.LZ4FrameDecompressor', side_effect=AssertionError('unbounded allocation')):
            with self.assertRaisesRegex(ValueError, 'size differs'):
                decode_tile(replace_body(payload, oversized), self.metadata, self.block, 'close', 0)

    def test_numeric_sha_and_boolean_validation_remain_authoritative(self):
        values = self.source.columns['close'][:, :2]
        payload = encode_tile(self.metadata, self.block, 'close', 0, values)
        changed = values.copy()
        changed[0, 0] += 1
        frame = lz4.frame.compress(changed.tobytes(), content_checksum=True, store_size=True)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            decode_tile(replace_body(payload, frame), self.metadata, self.block, 'close', 0)

        valid = np.ones((9, 2), dtype='|u1')
        payload = encode_tile(self.metadata, self.block, PRESENT_FIELD, 0, valid)
        valid[0, 0] = 2
        raw = valid.tobytes()
        frame = lz4.frame.compress(raw, content_checksum=True, store_size=True)
        with self.assertRaisesRegex(ValueError, 'boolean'):
            decode_tile(replace_body(payload, frame, sha256=hashlib.sha256(raw).hexdigest()),
                        self.metadata, self.block, PRESENT_FIELD, 0)

    def test_incompressible_tile_fits_existing_transport_limit(self):
        metadata = deepcopy(self.metadata)
        metadata['symbols'] = [f'S{n:04}' for n in range(1024)]
        metadata['cache'].update(tile_symbols=1024, chunk_max_bytes=4*1024**2)
        block = dict(start_index=0, row_count=64, sha256='0'*64)
        values = np.random.default_rng(749).random((64, 1024))
        payload = encode_tile(metadata, block, 'close', 0, values)
        self.assertLess(len(payload), values.nbytes+4096)
        self.assertEqual(decode_tile(payload, metadata, block, 'close', 0).tobytes(), values.tobytes())

    def test_optional_codec_namespaces_preserve_existing_raw_default(self):
        tiles, manifests = set(), set()
        for codec in ('raw', 'zlib', 'lz4'):
            metadata = deepcopy(self.metadata)
            metadata['cache']['codec'] = codec
            validate_snapshot(metadata, metadata['snapshot_id'])
            tiles.add(tile_key(metadata, self.block, 'close', 0))
            manifests.add(metadata_key(metadata['snapshot_id'], metadata['cache']))
        self.assertEqual(len(tiles), 3)
        self.assertEqual(len(manifests), 3)
        with patch.dict(os.environ):
            os.environ.pop('FACTORPOOL_CACHE_CODEC', None)
            raw = dict(self.metadata['cache'], codec='raw')
            self.assertEqual(metadata_key('typed'), metadata_key('typed', dict(raw,
                tile_symbols=1024, chunk_max_bytes=4194304)))


class MarketTileLz4GrpcTests(unittest.TestCase):
    setUp = grpc_cases.GrpcBoundaryTests.setUp
    client = grpc_cases.GrpcBoundaryTests.client

    def test_typed_rpc_direct_cache_scoped_repair_and_outage_are_byte_exact(self):
        self.source = ColumnSource()
        self.source.metadata['cache']['codec'] = 'lz4'
        self.service.source = self.source
        self.cache.values['unrelated'] = b'preserve'
        fields = ('close', 'return', *ADJUSTMENT_FIELDS, PRESENT_FIELD, MEMBERSHIP_FIELD)
        with patch.dict(os.environ, FACTORPOOL_CACHE_CODEC='lz4', FACTORPOOL_CACHE_TILE_SYMBOLS='2',
                        FACTORPOOL_CACHE_CHUNK_MAX_BYTES='8192'):
            client = self.client()
            metadata = client.snapshot('typed')
            first = list(client.selected_columns(metadata, 0, 9, [3, 0], fields=fields))[0][2]
            self.assertEqual(client.stats['grpc_blocks'], len(fields)*2)
            hot = self.client()
            with patch.object(hot.stub, 'Snapshot', side_effect=AssertionError('hot metadata proxied')), \
                 patch.object(hot.stub, 'MarketBlocks', side_effect=AssertionError('hot matrix proxied')):
                second = list(hot.selected_columns(hot.snapshot('typed'), 0, 9, [3, 0], fields=fields))[0][2]
            for field in fields:
                expected = self.source.columns[field][:, [3, 0]]
                for actual in (first[field], second[field]):
                    self.assertEqual(actual.dtype, expected.dtype)
                    self.assertEqual(actual.tobytes(), expected.tobytes())
                    self.assertTrue(actual.flags.c_contiguous)
                    self.assertTrue(actual.flags.aligned)
                    self.assertFalse(actual.flags.writeable)
            key = tile_key(metadata, metadata['blocks'][0], '__segment__', 0)
            corrupted = bytearray(self.cache.values[key])
            corrupted[-1] ^= 1
            self.cache.values[key] = bytes(corrupted)
            repaired = list(client.selected_columns(metadata, 0, 9, [0], fields=('__segment__',)))[0][2]
            self.assertEqual(repaired['__segment__'].tobytes(), self.source.columns['__segment__'][:, :1].tobytes())
            self.assertEqual(client.stats['corrupt_blocks'], 1)
            self.assertEqual(self.cache.values['unrelated'], b'preserve')
            self.cache.online = False
            fallback = list(self.client().selected_columns(metadata, 0, 9, [3, 0], fields=fields))[0][2]
            for field in fields:
                self.assertEqual(fallback[field].tobytes(), first[field].tobytes())


if __name__ == '__main__':
    unittest.main()
