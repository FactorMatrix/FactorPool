"""Real HTTP/gRPC/PG lease tests; opt in only against the isolated service stack."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
import urllib.request


@unittest.skipUnless(os.getenv('FACTORPOOL_SERVICE_TESTS') == '1' and os.getenv('FACTORPOOL_TEST_DB') == '1',
                     'Requires isolated PostgreSQL, Redis, Java and data-service; worker must be stopped')
class ServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.base = os.environ['FACTORPOOL_BACKEND_URL'].rstrip('/')
        self.token = os.environ['FACTORPOOL_INTERNAL_TOKEN']
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.created, self.leases = [], []

    def http(self, path, body=None, *, auth=False, token=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['X-FactorPool-Internal-Token'] = token if token is not None else self.token
        request = urllib.request.Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                data = response.read()
                return response.status, json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def enqueue(self):
        definition = dict(name='isolated-service-contract', factor_class='Factor', factor_params={},
                          source_code='from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n'
                          '    def update(self, input):\n        self.value = input.volume\n        return True\n',
                          data=dict(provider='fixture', universe='fixture', frequency='1d', adjustment='raw',
                                    factor_price='quote_adjusted', factor_start='2024-01-02', factor_end='2024-01-02'),
                          analysis=dict(groups=3, horizons=[1], decay_steps=1, entry='signal_close', exit='next_close'))
        code, value = self.http('/api/tasks', definition)
        self.assertEqual(code, 200, value)
        task_id = value['id']
        self.created.append(task_id)
        return task_id

    def claim(self, worker='integration-worker'):
        code, value = self.http('/internal/worker/claim', dict(worker_id=worker), auth=True)
        self.assertIn(code, (200, 204), value)
        if value:
            self.leases.append(value)
        return value

    def tearDown(self):
        for task_id in self.created:
            self.http(f'/api/tasks/{task_id}/cancel', {})
        for lease in self.leases:
            self.http('/internal/worker/fail', dict(lease, status='CANCELLED'), auth=True)

    def test_internal_auth_and_atomic_global_capacity(self):
        self.assertEqual(self.http('/internal/worker/claim', dict(worker_id='unauthorized'))[0], 401)
        self.assertEqual(self.http('/internal/worker/claim', dict(worker_id='unauthorized'), auth=True, token='wrong')[0], 401)
        ids = {self.enqueue() for _ in range(4)}
        with ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(self.claim, [f'parallel-{n}' for n in range(8)]))
        active = [lease for lease in claims if lease]
        self.assertEqual(len(active), 2, claims)  # preview global capacity is deliberately two.
        self.assertEqual(len({lease['task_id'] for lease in active}), 2)
        self.assertTrue(all(lease['task_id'] in ids for lease in active))

    def test_expired_execution_cannot_renew_or_upload(self):
        task_id = self.enqueue()
        old = self.claim()
        self.assertEqual(old['task_id'], task_id)
        time.sleep(old['lease_seconds']+1)
        fresh = self.claim('replacement-worker')
        self.assertEqual(fresh['task_id'], task_id)
        self.assertEqual(fresh['attempt'], old['attempt']+1)
        self.assertEqual(self.http('/internal/worker/heartbeat', old, auth=True)[0], 409)
        from factorpool.data_client import DataClient
        with tempfile.TemporaryDirectory() as tmp, DataClient() as data:
            path = Path(tmp)/'values.jsonl'
            path.write_text('')
            with self.assertRaises(Exception):
                data.upload_values(task_id, old['attempt'], old['lease_token'], path, 0)
        self.assertEqual(self.http(f'/api/tasks/{task_id}/result')[0], 409)

    def test_waiting_database_lock_does_not_extend_expired_lease(self):
        import psycopg
        from factorpool.data_client import DataClient
        task_id = self.enqueue()
        lease = self.claim()
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(
                'host=db dbname=factorpool user=factorpool') as connection, ThreadPoolExecutor(max_workers=2) as executor:
            connection.execute('SELECT id FROM research_tasks WHERE id=%s FOR UPDATE', (task_id,))
            heartbeat = executor.submit(self.http, '/internal/worker/heartbeat', lease, auth=True)
            path = Path(tmp)/'values.jsonl'
            path.write_text('')
            def upload():
                try:
                    with DataClient() as data:
                        data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, 0)
                    return True
                except Exception:
                    return False
            staged = executor.submit(upload)
            time.sleep(lease['lease_seconds']+1)
            connection.commit()
            self.assertEqual(heartbeat.result(timeout=10)[0], 409)
            self.assertFalse(staged.result(timeout=10))

    def test_publication_visibility_replay_and_cancel_fencing(self):
        from factorpool.data_client import DataClient
        task_id = self.enqueue()
        lease = self.claim()
        with tempfile.TemporaryDirectory() as tmp, DataClient() as data:
            path = Path(tmp)/'values.jsonl'
            row = dict(date='2024-01-02', symbol='A', value=0.0, valid=True, ready=True,
                       reason='', forward_return=None, group=1.0)
            path.write_text(json.dumps(row)+'\n')
            receipt = data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, 1)
            replay = data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, 1)
            self.assertEqual(receipt, replay)
        self.assertEqual(self.http(f'/api/tasks/{task_id}/values')[0], 409)
        result = dict(metadata=dict(value_count=1), summary={}, daily=[])
        body = dict(lease, result=result, **receipt)
        code, value = self.http('/internal/worker/complete', body, auth=True)
        self.assertEqual(code, 200, value)
        self.assertEqual(self.http('/internal/worker/complete', body, auth=True)[0], 200)
        code, rows = self.http(f'/api/tasks/{task_id}/values')
        self.assertEqual(code, 200, rows)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['value'], 0.0)
        self.assertEqual(rows[0]['factor_group'], 1)
        cancelled_id = self.enqueue()
        cancelled = self.claim()
        self.http(f'/api/tasks/{cancelled_id}/cancel', {})
        with tempfile.TemporaryDirectory() as tmp, DataClient() as data:
            path = Path(tmp)/'values.jsonl'
            path.write_text('')
            with self.assertRaises(Exception):
                data.upload_values(cancelled_id, cancelled['attempt'], cancelled['lease_token'], path, 0)
        self.assertEqual(self.http(f'/api/tasks/{cancelled_id}/result')[0], 409)


if __name__ == '__main__':
    unittest.main()
