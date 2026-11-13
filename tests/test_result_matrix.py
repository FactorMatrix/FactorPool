"""Columnar values retain every public cell while avoiding per-row transport."""
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import struct
import threading
import unittest
from unittest.mock import patch

import numpy as np

from factorpool.result_matrix import (REASONS, decode_axes, decode_chunk, encode_axes,
    encode_chunk, iter_result_chunks, rows_from_chunk, write_jsonl, write_result_matrix)
from factorpool.data_client import DataClient
from factorpool.data_client.generated import data_pb2 as pb


class CaptureMatrices:
    def __init__(self):
        self.chunks = []
        self.finalized = False

    def StageValues(self, chunks, **kwargs):
        digest, count = hashlib.sha256(), 0
        for chunk in chunks:
            self.chunks.append(chunk)
            digest.update(bytes.fromhex(chunk.sha256))
            count += chunk.row_count
        return pb.ReceiptReply(valid=True,count=count,sha256=digest.hexdigest())

    def FinalizeValues(self, request, **kwargs):
        self.finalized = True
        return pb.ReceiptReply(valid=True,count=request.expected_count,sha256=request.sha256,receipt='matrix-captured')


class ResultMatrixTests(unittest.TestCase):
    def codecs(self):
        return ('raw','zlib','lz4') if importlib.util.find_spec('lz4') else ('raw','zlib')

    def sample(self):
        dates, symbols = ['2024-01-02','2024-01-03','2024-01-04'], ['A','B','C']
        values = np.array([[-0.0,2,np.nan],[4,5,6],[7,np.nan,9.]])
        valid = np.isfinite(values)
        ready = np.array([[True,True,False],[True]*3,[True]*3])
        reasons = np.array([[0,0,3],[0,0,0],[0,6,0]],dtype=np.uint8)
        forward = np.array([[.1,np.nan,-.2],[.3,.1,.2],[np.nan,.2,-0.0]])
        groups = np.array([[1,2,np.nan],[1,2,3],[1,np.nan,3.]])
        return dates,symbols,values,valid,ready,reasons,forward,groups

    def test_all_cells_axes_flags_nulls_and_signed_zero_round_trip(self):
        data = self.sample()
        for codec in self.codecs():
            with self.subTest(codec=codec),tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)/'values.matrix'
                info = write_result_matrix(path,*data,codec=codec)
                chunks = list(iter_result_chunks(path))
                axes = decode_axes(chunks[0][2])
                blocks = [decode_chunk(item[2],axes) for item in chunks[1:]]
                self.assertEqual(info['count'],9)
                self.assertEqual(axes['dates'],data[0])
                self.assertEqual(axes['symbols'],data[1])
                for name,expected in zip(('value','valid','ready','reason','forward_return','group'),data[2:]):
                    actual = np.concatenate([block[name] for block in blocks]).reshape(3,3)
                    np.testing.assert_array_equal(actual,expected)
                self.assertTrue(np.signbit(blocks[0]['value'][0]))
                self.assertTrue(np.signbit(blocks[0]['forward_return'][-1]))
                write_jsonl(Path(temporary)/'values.jsonl',path)
                rows = [json.loads(line) for line in (Path(temporary)/'values.jsonl').read_text().splitlines()]
                self.assertEqual(list(rows[0]),['date','symbol','value','valid','ready','reason','forward_return','group'])
                self.assertIsNone(rows[2]['value'])
                self.assertEqual(rows[2]['reason'],'not_ready')
                self.assertEqual(rows[7]['reason'],'outside_universe')

    def test_signal_date_selection_does_not_export_non_signal_cells(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'values.matrix'
            info = write_result_matrix(path,*self.sample(),signal_indices={2,0})
            items=list(iter_result_chunks(path));axes=decode_axes(items[0][2])
            rows=list(rows_from_chunk(axes,decode_chunk(items[1][2],axes)))
            self.assertEqual(info['count'],6)
            self.assertEqual(axes['dates'],['2024-01-02','2024-01-04'])
            self.assertEqual([row['date'] for row in rows],['2024-01-02']*3+['2024-01-04']*3)

    def test_wrong_axes_corruption_truncation_and_invalid_numeric_status_rejected(self):
        data=self.sample();axes=decode_axes(encode_axes(data[0],data[1]))
        encoded=encode_chunk(axes,0,*[a.reshape(-1) for a in data[2:]])
        altered=decode_axes(encode_axes(data[0],['X','Y','Z']))
        with self.assertRaises(ValueError):decode_chunk(encoded,altered)
        with self.assertRaises(ValueError):decode_chunk(encoded[:-1],axes)
        with self.assertRaises(ValueError):decode_chunk(encoded,axes,max_raw_bytes=1)
        for index,value in [(0,float('inf')),(5,1.25)]:
            arrays=[a.copy().reshape(-1) for a in data[2:]];arrays[index][0]=value
            with self.assertRaises(ValueError):encode_chunk(axes,0,*arrays)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix';write_result_matrix(path,*data)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError):list(iter_result_chunks(path))

    def test_actual_generated_stream_upload_uses_metadata_and_column_chunks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix';info=write_result_matrix(path,*self.sample())
            stub=CaptureMatrices()
            with DataClient(stub=stub,enabled=False) as client:
                receipt=client.upload_values('12345678-1234-1234-1234-123456789abc',1,
                    '87654321-4321-4321-4321-cba987654321',path,info['count'])
            self.assertTrue(stub.finalized)
            self.assertEqual(receipt['count'],9)
            self.assertEqual(stub.chunks[0].row_count,0)
            axes=decode_axes(stub.chunks[0].payload)
            rows=list(rows_from_chunk(axes,decode_chunk(stub.chunks[1].payload,axes),api=True))
            self.assertEqual(rows[0]['factor_group'],1)
            self.assertNotIn('date',rows[0])
            self.assertEqual(rows[0]['trading_date'],'2024-01-02')

    def test_empty_result_still_has_verifiable_axes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'empty.matrix'
            info=write_result_matrix(path,[],['A'],*[np.empty((0,1)) for _ in range(6)])
            chunks=list(iter_result_chunks(path))
            self.assertEqual(info['count'],0)
            self.assertEqual(len(chunks),1)
            self.assertEqual(decode_axes(chunks[0][2])['row_count'],0)

    def test_raw_and_zlib_chunks_respect_small_byte_budget_at_boundaries(self):
        generator=np.random.default_rng(427)
        shape=(31,17)
        values=generator.normal(size=shape);forward=generator.normal(size=shape)
        groups=generator.integers(0,100,size=shape).astype(float)
        ready=np.ones(shape,dtype=bool);reasons=np.zeros(shape,dtype=np.uint8)
        dates=[(date(2024,1,1)+timedelta(days=n)).isoformat() for n in range(shape[0])]
        symbols=[f'S{n:02}' for n in range(shape[1])]
        for codec in self.codecs():
            with self.subTest(codec=codec),tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/'values.matrix'
                info=write_result_matrix(path,dates,symbols,values,ready,ready,reasons,forward,groups,
                                         chunk_bytes=8192,codec=codec)
                parts=list(iter_result_chunks(path,max_bytes=8192));axes=decode_axes(parts[0][2])
                self.assertGreater(info['chunks'],1)
                self.assertEqual(info['stored_bytes'],path.stat().st_size)
                self.assertTrue(all(len(part[2])<=8192 for part in parts[1:]))
                decoded=np.concatenate([decode_chunk(part[2],axes,max_raw_bytes=8192)['value'] for part in parts[1:]])
                np.testing.assert_array_equal(decoded,values.reshape(-1))

    def test_sparse_dates_and_non_contiguous_views_cross_chunk_boundaries(self):
        values=np.arange(101*17,dtype=float).reshape(17,101).T
        shape=values.shape
        good=np.ones(shape,dtype=bool);reasons=np.zeros(shape,dtype=np.uint8)
        groups=np.ones(shape);forward=values*.001
        dates=[(date(2020,1,1)+timedelta(days=n)).isoformat() for n in range(shape[0])]
        symbols=[f'S{n:02}' for n in range(shape[1])]
        selected=list(range(0,101,2))
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix'
            write_result_matrix(path,dates,symbols,values,good,good,reasons,forward,groups,
                                signal_indices=selected,chunk_bytes=8192)
            parts=list(iter_result_chunks(path,max_bytes=8192));axes=decode_axes(parts[0][2])
            self.assertGreater(len(parts),3)
            decoded=np.concatenate([decode_chunk(part[2],axes,max_raw_bytes=8192)['value'] for part in parts[1:]])
            np.testing.assert_array_equal(decoded,values[selected].reshape(-1))

    def test_parallel_encoding_keeps_exact_bytes_when_chunks_finish_out_of_order(self):
        import factorpool.result_matrix as matrix
        shape=(101,17)
        values=np.arange(np.prod(shape),dtype=float).reshape(shape)
        good=np.ones(shape,dtype=bool);reasons=np.zeros(shape,dtype=np.uint8)
        dates=[(date(2020,1,1)+timedelta(days=n)).isoformat() for n in range(shape[0])]
        symbols=[f'S{n:02}' for n in range(shape[1])]
        args=(dates,symbols,values,good,good,reasons,values*.001,np.ones(shape))
        original=matrix.encode_chunk;later_finished=threading.Event();completed=[]
        def delayed(axes,ordinal,*arrays,**kwargs):
            if ordinal==0:
                self.assertTrue(later_finished.wait(2))
            result=original(axes,ordinal,*arrays,**kwargs)
            completed.append(ordinal)
            if ordinal:
                later_finished.set()
            return result
        with tempfile.TemporaryDirectory() as temporary:
            one=Path(temporary)/'one.matrix';many=Path(temporary)/'many.matrix'
            serial=write_result_matrix(one,*args,signal_indices=range(0,101,2),chunk_bytes=8192,threads=1)
            with patch.dict('os.environ',FACTORPOOL_CACHE_BATCH_MAX_BYTES='65536'),patch.object(matrix,'encode_chunk',side_effect=delayed):
                parallel=write_result_matrix(many,*args,signal_indices=range(0,101,2),chunk_bytes=8192,threads=4)
            self.assertGreater(completed[0],0)
            self.assertGreater(parallel['encoding_threads'],1)
            self.assertEqual(one.read_bytes(),many.read_bytes())
            self.assertEqual(serial['sha256'],parallel['sha256'])

    @unittest.skipUnless(importlib.util.find_spec('lz4'),'LZ4 runtime dependency required')
    def test_incompressible_doubles_still_fit_each_wire_chunk(self):
        rng=np.random.default_rng(64021);shape=(3,1000)
        bits=rng.integers(0,np.iinfo(np.uint64).max,size=shape,dtype=np.uint64)
        values=bits.view(np.float64).copy();values[~np.isfinite(values)]=0
        forward=np.flip(values,axis=1).copy()
        groups=rng.integers(-(2**31),2**31-1,size=shape).astype(float)
        good=np.ones(shape,dtype=bool);reasons=rng.integers(0,7,size=shape,dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix'
            write_result_matrix(path,['2024-01-02','2024-01-03','2024-01-04'],
                [f'S{n:04}' for n in range(shape[1])],values,good,good,reasons,forward,groups,chunk_bytes=8192,codec='lz4')
            parts=list(iter_result_chunks(path,max_bytes=8192));axes=decode_axes(parts[0][2])
            self.assertTrue(all(len(part[2])<=8192 for part in parts[1:]))
            actual=np.concatenate([decode_chunk(part[2],axes,max_raw_bytes=8192)['value'] for part in parts[1:]])
            np.testing.assert_array_equal(actual,values.reshape(-1))

    @unittest.skipUnless(importlib.util.find_spec('lz4'),'LZ4 runtime dependency required')
    def test_lz4_decoder_enforces_frame_length_checksum_and_single_frame(self):
        import lz4.frame
        data=self.sample();axes=decode_axes(encode_axes(data[0],data[1]))
        payload=encode_chunk(axes,0,*[item.reshape(-1) for item in data[2:]],codec='lz4')
        size=struct.unpack('!I',payload[4:8])[0];prefix=payload[:8+size]
        frame=payload[8+size:];raw=lz4.frame.decompress(frame)
        for malformed in [frame[:-1],frame+frame,lz4.frame.compress(raw+raw,store_size=True)]:
            with self.assertRaises(ValueError):decode_chunk(prefix+malformed,axes)


if __name__ == '__main__':
    unittest.main()
