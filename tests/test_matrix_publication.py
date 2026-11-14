"""Real PostgreSQL/gRPC columnar publication, paging, retries and fencing."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np

from factorpool.data_client import DataClient
from factorpool.data_client.generated import data_pb2 as pb, data_pb2_grpc
from factorpool.result_matrix import decode_axes, iter_result_chunks, write_result_matrix
from services.data_service.postgres import connect
from services.data_service.postgres.values import ValueStore


@unittest.skipUnless(os.environ.get('FACTORPOOL_TEST_DB')=='1','Requires disposable PostgreSQL')
class MatrixPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('FACTORPOOL_DB_HOST')!='test-db' or os.environ.get('FACTORPOOL_DB_NAME')!='factorpool':
            raise RuntimeError('Matrix publication tests require isolated test-db/factorpool')
        cls.connection=connect();cls.connection.autocommit=True
        cls.connection.execute(Path('/opt/factorpool/schema.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        self.connection.execute('TRUNCATE research_tasks CASCADE')
        self.task,self.token=str(uuid.uuid4()),str(uuid.uuid4())
        self.connection.execute("INSERT INTO research_tasks(id,name,snapshot,status,execution_attempt,lease_token,lease_expires_at) "
            "VALUES(%s,'isolated columnar values','{}','RUNNING',1,%s,clock_timestamp()+interval '120 seconds')",(self.task,self.token))
        self.store=ValueStore()

    def chunks(self,path,*,attempt=1,token=None):
        lease=pb.Lease(task_id=self.task,attempt=attempt,lease_token=token or self.token)
        return [pb.ValueChunk(lease=lease,ordinal=ordinal,row_count=count,payload=payload,sha256=sha)
                for ordinal,count,payload,sha in iter_result_chunks(path)]

    def request(self,staged,*,attempt=1,token=None):
        return pb.ReceiptRequest(task_id=self.task,attempt=attempt,lease_token=token or self.token,
                                 expected_count=staged.count,sha256=staged.sha256)

    def complete(self,attempt=1):
        self.connection.execute("UPDATE research_tasks SET status='SUCCEEDED',result_attempt=%s WHERE id=%s",(attempt,self.task))

    def artifact(self,path,days=7,width=101,chunk_bytes=8192):
        shape=(days,width)
        values=np.arange(days*width,dtype=float).reshape(shape)
        values[0,0]=-0.;values[0,1]=np.nan
        good=np.isfinite(values);ready=np.ones(shape,dtype=bool);ready[0,1]=False
        reasons=np.zeros(shape,dtype=np.uint8);reasons[0,1]=3
        forward=values*.001;groups=np.ones(shape)
        dates=[(date(2020,1,1)+timedelta(days=n)).isoformat() for n in range(days)]
        symbols=[f'S{n:04}' for n in range(width)]
        return write_result_matrix(path,dates,symbols,values,good,ready,reasons,forward,groups,chunk_bytes=chunk_bytes)

    def test_columnar_finalization_is_atomic_hidden_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix';info=self.artifact(path)
            chunks=self.chunks(path)
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                self.store.stage(chunks[:2],8192)
            with self.assertRaisesRegex(ValueError,'no complete'):
                self.store.visible_version(self.task)
            staged=self.store.stage(chunks,8192)
            self.assertEqual(staged,self.store.stage(chunks,8192))
            first=self.store.finalize(self.request(staged))
            self.assertEqual(first,self.store.finalize(self.request(staged)))
            self.assertEqual(self.connection.execute('SELECT storage_format,row_count FROM factor_value_publications').fetchone(),('matrix-v1',info['count']))
            with self.assertRaisesRegex(ValueError,'no complete'):
                self.store.visible_version(self.task)
            self.complete()
            rows=self.store.page(self.task,0,3)
            self.assertTrue(np.signbit(rows[0]['value']))
            self.assertIsNone(rows[1]['value'])
            self.assertFalse(rows[1]['ready'])
            self.assertEqual(rows[1]['reason'],'not_ready')
            self.assertEqual(rows[2]['factor_group'],1)
            boundary=chunks[1].row_count-1
            rows=self.store.page(self.task,boundary,3)
            self.assertEqual(len(rows),3)
            for n,row in enumerate(rows):self.assertEqual(row['value'],float(boundary+n))
            self.assertEqual(self.store.page(self.task,info['count'],100),[])
            with patch.dict(os.environ,FACTORPOOL_RESULT_MAX_BYTES='1'):
                self.assertEqual(len(self.store.page(self.task,0,3)),3)

    def test_new_attempt_replaces_old_chunks_and_rejects_late_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix';self.artifact(path)
            staged=self.store.stage(self.chunks(path),8192);receipt=self.store.finalize(self.request(staged))
            newer=str(uuid.uuid4())
            self.connection.execute("UPDATE research_tasks SET execution_attempt=2,lease_token=%s WHERE id=%s",(newer,self.task))
            with self.assertRaisesRegex(ValueError,'lease is stale'):self.store.finalize(self.request(staged))
            second=self.store.stage(self.chunks(path,attempt=2,token=newer),8192)
            self.store.finalize(self.request(second,attempt=2,token=newer));self.complete(2)
            self.assertEqual(self.connection.execute('SELECT DISTINCT attempt FROM factor_value_matrix_chunks').fetchall(),[(2,)])
            self.assertEqual(self.store.visible_version(self.task),2)
            self.assertEqual(len(self.store.page(self.task,0,3)),3)

    def test_cancel_and_chunk_corruption_never_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix';self.artifact(path);chunks=self.chunks(path)
            broken=pb.ValueChunk();broken.CopyFrom(chunks[1]);broken.payload=broken.payload[:-1]
            with self.assertRaises(ValueError):self.store.stage([chunks[0],broken],8192)
            self.connection.execute("UPDATE research_tasks SET status='CANCELLING' WHERE id=%s",(self.task,))
            with self.assertRaisesRegex(ValueError,'lease is stale'):self.store.stage(chunks,8192)
            self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_publications').fetchone()[0],0)

    def test_stream_reuses_connection_commits_each_chunk_and_closes_on_failure(self):
        connections=[]
        def tracked_connect():
            connection=connect();connections.append(connection)
            return connection
        with tempfile.TemporaryDirectory() as temporary, \
                patch('services.data_service.postgres.values.connect',side_effect=tracked_connect):
            path=Path(temporary)/'values.matrix';self.artifact(path);chunks=self.chunks(path)
            def disconnected():
                for index,chunk in enumerate(chunks[:2]):
                    yield chunk
                    self.assertEqual(connections[-1].info.transaction_status.name,'IDLE')
                    self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_matrix_chunks').fetchone()[0],index)
                raise OSError('stream disconnected')
            with self.assertRaisesRegex(OSError,'stream disconnected'):
                self.store.stage(disconnected(),8192)
            self.assertEqual(len(connections),1)
            self.assertTrue(connections[-1].closed)
            self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_matrix_chunks').fetchone()[0],1)
            with self.assertRaisesRegex(ValueError,'no complete'):self.store.visible_version(self.task)
            def completed():
                for chunk in chunks:
                    yield chunk
                    self.assertEqual(connections[-1].info.transaction_status.name,'IDLE')
            self.assertEqual(self.store.stage(completed(),8192).count,7*101)
            self.assertEqual(len(connections),2)
            self.assertTrue(connections[-1].closed)
            with self.assertRaisesRegex(ValueError,'Empty'):self.store.stage([],8192)
            self.assertEqual(len(connections),3)
            self.assertTrue(connections[-1].closed)
            def cancelled():
                yield chunks[0]
                # The preceding chunk released its task lock and committed;
                # the next chunk must inspect the new live lease state.
                self.connection.execute("UPDATE research_tasks SET status='CANCELLING' WHERE id=%s",(self.task,))
                yield chunks[1]
            with self.assertRaisesRegex(ValueError,'lease is stale'):self.store.stage(cancelled(),8192)
            self.assertEqual(len(connections),4)
            self.assertTrue(connections[-1].closed)
            self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_publications').fetchone()[0],0)

    def test_real_grpc_more_than_two_million_values_use_few_pg_chunks(self):
        import grpc
        from services.data_service.api import DataService
        from services.data_service.redis_cache import Cache
        shape=(2001,1000)
        dates=[(date(2000,1,1)+timedelta(days=n)).isoformat() for n in range(shape[0])]
        symbols=[f'S{n:04}' for n in range(shape[1])]
        zeros=np.zeros(shape);good=np.ones(shape,dtype=bool);reasons=np.zeros(shape,dtype=np.uint8)
        token='isolated-columnar-result-grpc-token'
        with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,FACTORPOOL_INTERNAL_TOKEN=token,FACTORPOOL_CACHE_ENABLED='false'):
            path=Path(temporary)/'values.matrix'
            info=write_result_matrix(path,dates,symbols,zeros,good,good,reasons,zeros,zeros)
            server=grpc.server(ThreadPoolExecutor(max_workers=2))
            data_pb2_grpc.add_DataServiceServicer_to_server(DataService(source=object(),cache=Cache(),values=self.store),server)
            port=server.add_insecure_port('127.0.0.1:0');server.start()
            try:
                with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
                    stub=data_pb2_grpc.DataServiceStub(channel)
                    with DataClient(stub=stub,enabled=False) as client:
                        receipt=client.upload_values(self.task,1,self.token,path,info['count'])
                    self.assertEqual(receipt['count'],2_001_000)
                    request=pb.ValuesRequest(task_id=self.task,offset=2_000_001,limit=3)
                    credentials=(('x-factorpool-internal-token',token),)
                    with self.assertRaises(grpc.RpcError) as invisible:
                        stub.Values(request,timeout=5,metadata=credentials)
                    self.assertEqual(invisible.exception.code(),grpc.StatusCode.FAILED_PRECONDITION)
                    self.complete()
                    rows=json.loads(stub.Values(request,timeout=5,metadata=credentials).json)
                    self.assertEqual([row['symbol'] for row in rows],['S0001','S0002','S0003'])
                    self.assertEqual(rows[0]['trading_date'],dates[-1])
                    stored=self.connection.execute('SELECT count(*),sum(row_count) FROM factor_value_matrix_chunks').fetchone()
                    self.assertLess(stored[0],30)
                    self.assertEqual(stored[1],2_001_000)
            finally:
                server.stop(0).wait()


if __name__=='__main__':
    unittest.main()
