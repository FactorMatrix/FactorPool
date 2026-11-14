"""Actual generated protobuf uploads accept the research core's integral groups."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from factorpool.data_client import DataClient
from factorpool.data_client.generated import data_pb2 as pb
from factorpool.runner import run


class CaptureUpload:
    def __init__(self):
        self.rows = []
        self.finalized = False

    def StageValues(self, chunks, **kwargs):
        digest, count = hashlib.sha256(), 0
        for chunk in chunks:
            self.rows.extend(pb.ValuesPayload.FromString(chunk.payload).rows)
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

    def test_real_runner_jsonl_round_trips_through_actual_generated_protobuf(self):
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
            values=path/'values.jsonl'
            original=[json.loads(line) for line in values.read_text().splitlines()]
            self.assertTrue(any(type(row.get('group')) is float for row in original))
            stub=CaptureUpload()
            receipt=self.upload(stub,values,len(original))
        self.assertEqual(receipt['count'],len(original))
        self.assertTrue(stub.finalized)
        self.assertEqual(len(stub.rows),len(original))
        for expected, actual in zip(original,stub.rows):
            self.assertEqual(actual.date,expected['date'])
            self.assertEqual(actual.symbol,expected['symbol'])
            self.assertEqual(actual.has_group,expected.get('group') is not None)
            self.assertEqual(actual.group if actual.has_group else None,
                             int(expected['group']) if expected.get('group') is not None else None)
            for field, present in [('value','has_value'),('forward_return','has_forward_return')]:
                self.assertEqual(getattr(actual,present),expected.get(field) is not None)
                self.assertEqual(getattr(actual,field) if getattr(actual,present) else None,expected.get(field))
            self.assertEqual((actual.valid,actual.ready,actual.reason),
                             (expected['valid'],expected['ready'],expected['reason']))

    def test_integral_float_group_boundaries_and_null(self):
        groups=[None,0.0,1.0,-1.0,float(-(2**31)),float(2**31-1)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'values.jsonl'
            path.write_text(''.join(json.dumps(dict(date='2024-01-02',symbol=f'S{n}',value=1.,
                valid=True,ready=True,reason='',group=group))+'\n' for n,group in enumerate(groups)))
            stub=CaptureUpload()
            self.upload(stub,path,len(groups))
        self.assertEqual([row.group if row.has_group else None for row in stub.rows],
                         [None,0,1,-1,-(2**31),2**31-1])

    def test_fractional_nonfinite_and_out_of_int32_groups_never_finalize(self):
        for group in [1.5,-1.5,float('nan'),float('inf'),-float('inf'),2**31,-(2**31)-1,True,'1']:
            with self.subTest(group=repr(group)),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'values.jsonl'
                path.write_text(json.dumps(dict(date='2024-01-02',symbol='A',value=1.,
                    valid=True,ready=True,reason='',group=group))+'\n')
                stub=CaptureUpload()
                with self.assertRaisesRegex(ValueError,'finite integral int32'):
                    self.upload(stub,path,1)
                self.assertFalse(stub.finalized)
