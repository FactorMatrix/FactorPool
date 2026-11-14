"""Only bounded binary matrices cross the result upload contract."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.generated import data_pb2 as pb
from factorpool.runner import run
from factorpool.result_matrix import decode_axes, decode_chunk, rows_from_chunk, write_result_matrix


class CaptureUpload:
    def __init__(self):
        self.rows = []
        self.finalized = False
        self.axes = None

    def StageValues(self, chunks, **kwargs):
        digest, count = hashlib.sha256(), 0
        for chunk in chunks:
            if self.axes is None:
                self.axes = decode_axes(chunk.payload)
            else:
                self.rows.extend(rows_from_chunk(self.axes, decode_chunk(chunk.payload, self.axes)))
            count += chunk.row_count
            digest.update(bytes.fromhex(chunk.sha256))
        return pb.ReceiptReply(valid=True, count=count, sha256=digest.hexdigest())

    def FinalizeValues(self, request, **kwargs):
        self.finalized = True
        return pb.ReceiptReply(valid=True, count=request.expected_count, receipt='captured',sha256=request.sha256)


class DataUploadTests(unittest.TestCase):
    def upload(self, stub, path, count):
        with DataClient(stub=stub, enabled=False) as client:
            return client.upload_values('12345678-1234-1234-1234-123456789abc',1,
                '87654321-4321-4321-4321-cba987654321',path,count)

    def test_real_runner_matrix_round_trips_through_actual_generated_protobuf(self):
        request=dict(name='upload-group-protocol',
            source_code='from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n'
                        '    def update(self, input):\n        self.value = input.volume\n        return True\n'
                        'if __name__ == "__main__":\n    Factor.run()\n',
            workspace_config=dict(base=dict(start_date='2024-01-02',end_date='2024-01-04'),
                data=dict(provider='fixture',universe='fixture'),
                analysis=dict(groups=3,horizons=[1],decay_steps=1,entry='signal_close',exit='next_close')))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'request.json').write_text(json.dumps(request))
            run(directory)
            values=path/'values.matrix'
            result=json.loads((path/'result.json').read_text())
            count=result['metadata']['value_count']
            stub=CaptureUpload()
            receipt=self.upload(stub,values,count)
        self.assertEqual(receipt['count'],9)
        self.assertTrue(stub.finalized)
        self.assertEqual(len(stub.rows),9)
        self.assertEqual([row['symbol'] for row in stub.rows],['A','B','C']*3)
        self.assertTrue(all(type(row['group']) is float for row in stub.rows))
        self.assertEqual([row['date'] for row in stub.rows],['2024-01-02']*3+['2024-01-03']*3+['2024-01-04']*3)

    def test_integral_float_group_boundaries_and_null(self):
        groups=np.array([[np.nan,0.,1.,-1.,float(-(2**31)),float(2**31-1)]])
        shape=groups.shape
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'values.matrix'
            write_result_matrix(path,['2024-01-02'],[f'S{n}' for n in range(shape[1])],
                np.ones(shape),np.ones(shape,dtype=bool),np.ones(shape,dtype=bool),
                np.zeros(shape,dtype=np.uint8),np.zeros(shape),groups)
            stub=CaptureUpload()
            self.upload(stub,path,shape[1])
        self.assertEqual([row['group'] for row in stub.rows],
                         [None,0,1,-1,-(2**31),2**31-1])

    def test_jsonl_upload_is_rejected_without_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'values.jsonl'
            path.write_text('{"date":"2024-01-02","symbol":"A","value":1}\n')
            stub=CaptureUpload()
            with self.assertRaises(ValueError):
                self.upload(stub,path,1)
            self.assertFalse(stub.finalized)

    def test_fractional_and_out_of_int32_groups_never_finalize(self):
        for group in [1.5,-1.5,float('inf'),-float('inf'),2**31,-(2**31)-1]:
            with self.subTest(group=repr(group)),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'values.matrix'
                stub=CaptureUpload()
                with self.assertRaises(ValueError):
                    write_result_matrix(path,['2024-01-02'],['A'],np.ones((1,1)),
                        np.ones((1,1),dtype=bool),np.ones((1,1),dtype=bool),
                        np.zeros((1,1),dtype=np.uint8),np.zeros((1,1)),np.array([[group]]))
                    self.upload(stub,path,1)
                self.assertFalse(stub.finalized)
