"""Real PostgreSQL staged visibility, upload idempotency and lease fencing."""
import hashlib
import os
from pathlib import Path
import unittest
import tempfile
import uuid

from factorpool.data_client.generated import data_pb2 as pb
from services.data_service.postgres import connect
from services.data_service.postgres.values import ValueStore


@unittest.skipUnless(os.environ.get('FACTORPOOL_TEST_DB') == '1', 'Requires disposable PostgreSQL')
class DataPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('FACTORPOOL_DB_HOST') != 'test-db' or os.environ.get('FACTORPOOL_DB_NAME') != 'factorpool':
            raise RuntimeError('Data tests require isolated host=test-db dbname=factorpool')
        cls.connection = connect()
        cls.connection.autocommit = True
        cls.connection.execute(Path('/opt/factorpool/schema.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        self.connection.execute('TRUNCATE research_tasks CASCADE')
        self.task, self.token = str(uuid.uuid4()), str(uuid.uuid4())
        self.connection.execute("INSERT INTO research_tasks(id,name,snapshot,status,execution_attempt,lease_token,lease_expires_at) "
            "VALUES(%s,'isolated values','{}','RUNNING',1,%s,clock_timestamp()+interval '60 seconds')",(self.task,self.token))
        self.store = ValueStore()

    def chunks(self, *, ordinal=0, value=-0.0):
        import numpy as np
        from factorpool.result_matrix import iter_result_chunks, write_result_matrix
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'values.matrix'
            write_result_matrix(path,['2024-01-02'],['A','B'],np.array([[value,np.nan]]),
                np.array([[True,False]]),np.array([[True,False]]),np.array([[0,1]],dtype=np.uint8),
                np.full((1,2),np.nan),np.full((1,2),np.nan),reason_labels=('ok','warmup'))
            return [pb.ValueChunk(lease=pb.Lease(task_id=self.task,attempt=1,lease_token=self.token),
                ordinal=position+ordinal,payload=payload,row_count=count,sha256=sha)
                for position,count,payload,sha in iter_result_chunks(path)]

    def request(self, staged, count=2):
        return pb.ReceiptRequest(task_id=self.task,attempt=1,lease_token=self.token,expected_count=count,sha256=staged.sha256)

    def complete(self):
        self.connection.execute("UPDATE research_tasks SET status='SUCCEEDED',result_attempt=1 WHERE id=%s",(self.task,))

    def test_incomplete_and_finalized_rows_hidden_until_summary_success(self):
        staged=self.store.stage(self.chunks(),8192)
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            self.store.finalize(self.request(staged,3))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_publications').fetchone()[0],0)
        receipt=self.store.finalize(self.request(staged))
        self.assertTrue(receipt.valid)
        with self.assertRaisesRegex(ValueError,'no complete'):
            self.store.visible_version(self.task)
        self.complete()
        self.assertEqual(self.store.visible_version(self.task),1)
        rows=self.store.page(self.task,0,100)
        self.assertEqual([row['symbol'] for row in rows],['A','B'])
        self.assertEqual(rows[1]['value'],None)
        self.assertFalse(rows[1]['ready'])
        self.assertEqual(rows[1]['reason'],'warmup')

    def test_chunk_and_finalize_retries_are_idempotent_and_divergence_rejected(self):
        chunks=self.chunks()
        staged=self.store.stage(chunks,8192)
        repeated=self.store.stage(chunks,8192)
        self.assertEqual(staged,repeated)
        with self.assertRaisesRegex(ValueError,'Conflicting retry'):
            self.store.stage(self.chunks(value=123),8192)
        first=self.store.finalize(self.request(staged))
        second=self.store.finalize(self.request(staged))
        self.assertEqual(first,second)
        self.assertEqual(self.connection.execute('SELECT coalesce(sum(row_count),0) FROM factor_value_matrix_chunks').fetchone()[0],2)
        self.complete()
        request=self.request(staged)
        request.receipt=first.receipt
        self.connection.execute("UPDATE research_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(self.task,))
        self.assertTrue(self.store.verify(request).valid)

    def test_stale_execution_cannot_publish_or_verify_over_new_attempt(self):
        staged=self.store.stage(self.chunks(),8192)
        receipt=self.store.finalize(self.request(staged))
        self.connection.execute("UPDATE research_tasks SET execution_attempt=2,lease_token=%s WHERE id=%s",(uuid.uuid4(),self.task))
        with self.assertRaisesRegex(ValueError,'lease is stale'):
            self.store.finalize(self.request(staged))
        request=self.request(staged); request.receipt=receipt.receipt
        with self.assertRaisesRegex(ValueError,'lease is stale'):
            self.store.verify(request)
        with self.assertRaisesRegex(ValueError,'lease is stale'):
            self.store.stage(self.chunks(),8192)
        self.assertEqual(self.connection.execute('SELECT attempt FROM factor_value_publications WHERE task_id=%s',(self.task,)).fetchone()[0],1)

    def test_cancel_and_expiry_stop_upload_without_publishing(self):
        for change in ["status='CANCELLING'", "status='RUNNING',lease_expires_at=clock_timestamp()-interval '1 second'"]:
            self.connection.execute('UPDATE research_tasks SET '+change+' WHERE id=%s',(self.task,))
            with self.assertRaisesRegex(ValueError,'lease is stale'):
                self.store.stage(self.chunks(),8192)
        self.assertEqual(self.connection.execute('SELECT coalesce(sum(row_count),0) FROM factor_value_matrix_chunks').fetchone()[0],0)

    def test_results_without_current_publication_are_rejected(self):
        self.connection.execute("UPDATE research_tasks SET status='SUCCEEDED',execution_attempt=0,result_attempt=NULL WHERE id=%s",(self.task,))
        with self.assertRaisesRegex(ValueError,'binary result matrix publication'):
            self.store.visible_version(self.task)
        with self.assertRaisesRegex(ValueError,'binary result matrix publication'):
            self.store.page(self.task,0,100)
        self.connection.execute("UPDATE research_tasks SET execution_attempt=1,result_attempt=1 WHERE id=%s",(self.task,))
        receipt=str(uuid.uuid4())
        self.connection.execute("INSERT INTO factor_value_publications(task_id,attempt,row_count,sha256,receipt,storage_format) VALUES(%s,1,0,%s,%s,'rows-v1')",(self.task,'0'*64,receipt))
        with self.assertRaisesRegex(ValueError,'binary result matrix publication'):
            self.store.visible_version(self.task)
        with self.assertRaisesRegex(ValueError,'binary result matrix publication'):
            self.store.page(self.task,0,100)
        self.assertFalse(self.store.verify(pb.ReceiptRequest(task_id=self.task,attempt=1,
            lease_token=self.token,receipt=receipt,expected_count=0,sha256='0'*64)).valid)

    def test_bad_chunk_sha_sequence_and_size_rejected_before_storage(self):
        for change in ['sha','ordinal','size']:
            chunks=self.chunks()
            if change=='sha': chunks[1].sha256='0'*64
            if change=='ordinal': chunks[1].ordinal=1
            with self.assertRaises(ValueError):
                self.store.stage(chunks,1 if change=='size' else 8192)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM factor_value_matrix_chunks').fetchone()[0],0)

    def test_values_rpc_bad_cached_header_type_repairs_from_real_postgres(self):
        from concurrent.futures import ThreadPoolExecutor
        import json
        import struct
        from unittest.mock import patch
        import grpc
        from factorpool.data_client.generated import data_pb2_grpc
        from services.data_service.api import DataService
        from services.data_service.redis_cache import Cache
        from test_data_service_boundary import MemoryRedis
        staged = self.store.stage(self.chunks(), 8192)
        self.store.finalize(self.request(staged))
        self.complete()
        cache = MemoryRedis()
        token = 'isolated-values-cache-header-test'
        with patch.dict(os.environ, FACTORPOOL_INTERNAL_TOKEN=token, FACTORPOOL_CACHE_ENABLED='true'):
            service = DataService(source=object(), cache=Cache(cache), values=self.store)
            server = grpc.server(ThreadPoolExecutor(max_workers=2))
            data_pb2_grpc.add_DataServiceServicer_to_server(service, server)
            port = server.add_insecure_port('127.0.0.1:0')
            server.start()
            try:
                with grpc.insecure_channel(f'127.0.0.1:{port}') as channel, \
                        patch.object(self.store, 'page', wraps=self.store.page) as pages:
                    stub = data_pb2_grpc.DataServiceStub(channel)
                    def read():
                        return json.loads(stub.Values(pb.ValuesRequest(task_id=self.task,offset=0,limit=100),
                            timeout=3,metadata=(('x-factorpool-internal-token',token),)).json)
                    expected = read()
                    self.assertEqual([row['symbol'] for row in expected], ['A','B'])
                    self.assertEqual(pages.call_count, 1)
                    key = f'fp:values:v1:{self.task}:1:0:100'
                    cache.values['unrelated-value-page'] = b'preserve'
                    for number, header in enumerate([b'[]', b'null', b'0', b'"wrong-type"'], 2):
                        corrupted = b'FPV1' + struct.pack('!I',len(header)) + header
                        cache.values[key] = corrupted
                        self.assertEqual(read(), expected)
                        self.assertEqual(pages.call_count, number)
                        self.assertNotEqual(cache.values[key], corrupted)
                        self.assertEqual(cache.values['unrelated-value-page'], b'preserve')
                        self.assertEqual(read(), expected)
                        self.assertEqual(pages.call_count, number)
            finally:
                server.stop(0).wait()
