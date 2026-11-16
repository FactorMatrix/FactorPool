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


REPORT_SECTIONS = ('', '/ic', '/groups', '/trace', '/source', '/export')


def report_fixture(count):
    """Current immutable publication shape; no retired top-level daily payload."""
    day = '2024-01-02'
    brain = dict(sharpe=None,turnover=0.,fitness=None,returns=0.,drawdown=0.,margin=None)
    alpha = dict(brain_style=brain,pnl=dict(book_size=1,unit='BOOK',cumulative=[0.]),
                 nav=[1.],drawdown_series=[0.],returns=[0.],turnover_series=[0.],
                 monthly=[dict(period='2024-01',value=0.)],yearly=[dict(period='2024',value=0.)],
                 yearly_stats=[dict(year='2024',start=day,end=day,days=1,**brain,long_count=0,short_count=0)],
                 n=1,annual_return=0.,cagr=0.,annual_volatility=None,sharpe=None,max_drawdown=0.,win_rate=0.,payoff_ratio=None)
    ic = dict(mean=0.,std=None,ir=None,positive_ratio=0.,skewness=None,excess_kurtosis=None,
              t_iid=None,p_iid=None,t_hac=None,p_hac=None,n=1)
    horizon = dict(horizon=1,dates=[day],portfolio_dates=[day],portfolios=dict(alpha=alpha,high_low=alpha),
                   partitions={},ic=ic,rank_ic=ic,series=dict(ic=[0.],rank_ic=[0.],ic_rolling30=[0.],
                   rank_ic_rolling30=[0.],ic_cumulative=[0.],rank_ic_cumulative=[0.]),
                   monthly=dict(ic=[dict(period='2024-01',mean=0.)],rank_ic=[dict(period='2024-01',mean=0.)]),
                   yearly=dict(ic=[dict(period='2024',mean=0.)],rank_ic=[dict(period='2024',mean=0.)]),decay=[],
                   alpha_trace=[],trace_status=dict(not_rebalance_dates=[],no_future_dates=[]),pnl_book=dict(size=1,unit='BOOK'))
    return dict(metadata=dict(report_version=3,value_count=count,source_sha256='0'*64,
                              brain_settings=dict(visualization=True),synthetic=True,reproduction_marker='full-source-metadata'),
                summary=dict(ic_mean=0.,rank_ic_mean=0.,icir=None,coverage=1.),
                diagnostics=dict(version=3,test_period=dict(start=None),horizons={'1':horizon},
                    conventions=dict(annual_days=252,brain_settings=dict(visualization=True),missing_datasets={}),
                    alpha_constraints=dict(truncation=0.,daily=[dict(date=day,full_gross_feasible=False,
                        target_gross=0.,unallocated_gross=1.,valid_count=0,capacity=0.)])))


def write_values(path, count=0):
    import numpy as np
    from factorpool.result_matrix import write_result_matrix
    shape=(count,1)
    write_result_matrix(path,['2024-01-02'] if count else [],['A'],np.zeros(shape),
        np.ones(shape,dtype=bool),np.ones(shape,dtype=bool),np.zeros(shape,dtype=np.uint8),
        np.full(shape,np.nan),np.ones(shape))


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
        definition = dict(name='isolated-service-contract',
                          source_code='from factorpool import PythonIndicator\nclass Factor(PythonIndicator):\n'
                          '    def update(self, input):\n        self.value = input.volume\n        return True\n'
                          'if __name__ == "__main__":\n    Factor.run()\n',
                          workspace_config=dict(base=dict(start_date='2024-01-02', end_date='2024-01-02'),
                              data=dict(provider='fixture', universe='fixture', factor_price='quote_adjusted'),
                              analysis=dict(groups=3, horizons=[1], decay_steps=1,
                                            entry='signal_close', exit='next_close')))
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

    def assert_reports_status(self, task_id, expected):
        for section in REPORT_SECTIONS:
            with self.subTest(report_section=section or 'core'):
                self.assertEqual(self.http(f'/api/tasks/{task_id}/result{section}')[0], expected)

    def assert_published_report_contract(self, task_id, expected=None):
        code, core = self.http(f'/api/tasks/{task_id}/result')
        self.assertEqual(code, 200, core)
        code, complete = self.http(f'/api/tasks/{task_id}/result/export')
        self.assertEqual(code, 200, complete)
        if expected is not None:
            self.assertEqual(complete, expected, 'Export must be the exact immutable publication')
        self.assertEqual(set(core), {'metadata','diagnostics'})
        self.assertNotIn('daily', complete)
        self.assertEqual(set(core['diagnostics']['horizons']), {'1'})
        self.assertLessEqual(len(json.dumps(core).encode()), 300000)
        self.assertLess(len(json.dumps(core)), len(json.dumps(complete)))
        self.assertEqual(core['metadata'], {key:complete['metadata'][key] for key in
            ('report_version','brain_settings','source_sha256','snapshot_id','synthetic','value_count')
            if key in complete['metadata']})
        h1 = core['diagnostics']['horizons']['1']
        original = complete['diagnostics']['horizons']['1']
        self.assertEqual(set(h1['portfolios']), {'alpha'})
        self.assertEqual(h1['portfolios']['alpha']['brain_style'], original['portfolios']['alpha']['brain_style'])
        self.assertEqual(h1['portfolios']['alpha']['pnl'], original['portfolios']['alpha']['pnl'])
        self.assertNotIn('ic',h1)
        self.assertNotIn('alpha_trace',h1)
        for section in REPORT_SECTIONS[1:-1]:
            code, research = self.http(f'/api/tasks/{task_id}/result{section}')
            self.assertEqual(code, 200, research)
            self.assertEqual(research['metadata']['report_version'], complete['metadata']['report_version'])
            if section == '/source':
                self.assertEqual(research, dict(metadata=complete['metadata']))
                continue
            current = research['diagnostics']['horizons']['1']
            if section == '/ic':
                self.assertEqual(research['summary'], {key:complete['summary'][key] for key in
                    ('ic_mean','rank_ic_mean','icir','coverage')})
                self.assertEqual(current['ic']['mean'], original['ic']['mean'])
                self.assertNotIn('portfolios', current)
            elif section == '/groups':
                self.assertEqual(set(current['portfolios']), set(original['portfolios']))
                for name,portfolio in current['portfolios'].items():
                    self.assertEqual(portfolio['returns'],original['portfolios'][name]['returns'])
                    self.assertNotIn('brain_style',portfolio)
                self.assertNotIn('ic',current)
            else:
                self.assertEqual(current,{key:original[key] for key in
                    ('horizon','pnl_book','alpha_trace','trace_status','alpha_constraints') if key in original})
                self.assertNotIn('portfolios',current)
        self.assertEqual(self.http(f'/api/tasks/{task_id}/result/unknown')[0],400)

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
            path = Path(tmp)/'values.matrix'
            write_values(path)
            with self.assertRaises(Exception):
                data.upload_values(task_id, old['attempt'], old['lease_token'], path, 0)
        self.assert_reports_status(task_id, 409)

    def test_waiting_database_lock_does_not_extend_expired_lease(self):
        import psycopg
        from factorpool.data_client import DataClient
        task_id = self.enqueue()
        self.assert_reports_status(task_id, 409)  # QUEUED cannot leak any report section.
        lease = self.claim()
        with tempfile.TemporaryDirectory() as tmp, psycopg.connect(
                'host=db dbname=factorpool user=factorpool') as connection, ThreadPoolExecutor(max_workers=2) as executor:
            connection.execute('SELECT id FROM research_tasks WHERE id=%s FOR UPDATE', (task_id,))
            heartbeat = executor.submit(self.http, '/internal/worker/heartbeat', lease, auth=True)
            path = Path(tmp)/'values.matrix'
            write_values(path)
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
            path = Path(tmp)/'values.matrix'
            write_values(path,1)
            receipt = data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, 1)
            replay = data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, 1)
            self.assertEqual(receipt, replay)
        self.assertEqual(self.http(f'/api/tasks/{task_id}/values')[0], 409)
        self.assert_reports_status(task_id, 409)
        result = report_fixture(1)
        body = dict(lease, result=result, **receipt)
        code, value = self.http('/internal/worker/complete', body, auth=True)
        self.assertEqual(code, 200, value)
        self.assertEqual(self.http('/internal/worker/complete', body, auth=True)[0], 200)
        self.assert_published_report_contract(task_id,result)
        code, rows = self.http(f'/api/tasks/{task_id}/values')
        self.assertEqual(code, 200, rows)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['value'], 0.0)
        self.assertEqual(rows[0]['factor_group'], 1)
        cancelled_id = self.enqueue()
        cancelled = self.claim()
        self.http(f'/api/tasks/{cancelled_id}/cancel', {})
        with tempfile.TemporaryDirectory() as tmp, DataClient() as data:
            path = Path(tmp)/'values.matrix'
            write_values(path)
            with self.assertRaises(Exception):
                data.upload_values(cancelled_id, cancelled['attempt'], cancelled['lease_token'], path, 0)
        self.assert_reports_status(cancelled_id, 409)

    def test_matrix_receipt_publishes_through_http_and_pages_across_dates(self):
        import numpy as np
        from factorpool.data_client import DataClient
        from factorpool.result_matrix import write_result_matrix
        task_id = self.enqueue()
        lease = self.claim()
        values = np.array([[-0., np.nan, 2.], [3., 4., 5.]])
        valid = np.isfinite(values)
        reasons = np.where(valid, 0, 3).astype(np.uint8)
        with tempfile.TemporaryDirectory() as tmp, DataClient() as data:
            path = Path(tmp) / 'values.matrix'
            info = write_result_matrix(path, ['2024-01-02', '2024-01-03'], ['A', 'B', 'C'],
                                       values, valid, valid, reasons, values * .01,
                                       np.array([[1., np.nan, 2.], [1., 2., 3.]]))
            receipt = data.upload_values(task_id, lease['attempt'], lease['lease_token'], path, info['count'])
        self.assertEqual(self.http(f'/api/tasks/{task_id}/values')[0], 409)
        self.assert_reports_status(task_id, 409)
        result = report_fixture(6)
        self.assertEqual(self.http('/internal/worker/complete', dict(lease, result=result, **receipt), auth=True)[0], 200)
        self.assert_published_report_contract(task_id,result)
        code, first = self.http(f'/api/tasks/{task_id}/values?offset=0&limit=2')
        self.assertEqual(code, 200, first)
        self.assertTrue(np.signbit(first[0]['value']))
        self.assertIsNone(first[1]['value'])
        self.assertEqual(first[1]['reason'], 'not_ready')
        code, page = self.http(f'/api/tasks/{task_id}/values?offset=2&limit=3')
        self.assertEqual(code, 200, page)
        self.assertEqual([(row['trading_date'], row['symbol'], row['value']) for row in page],
                         [('2024-01-02', 'C', 2.), ('2024-01-03', 'A', 3.), ('2024-01-03', 'B', 4.)])
        self.assertEqual(self.http(f'/api/tasks/{task_id}/values?offset=6&limit=3'), (200, []))

    def test_worker_upload_overlaps_diagnostics_and_keeps_failed_reports_invisible(self):
        import psycopg
        import sys
        from factorpool.data_client import DataClient
        from services.worker_service.process_manager import ProcessManager
        from services.worker_service.worker import Worker

        # The real runner pauses diagnostics until the real gRPC/PG finalize
        # has completed. A serial worker cannot satisfy this handshake.
        command = '''import sys,time
from pathlib import Path
import factorpool.runner as runner
directory=Path(sys.argv[1]); fail=sys.argv[2]=='1'
original=runner.build_diagnostics
def diagnostics(*args,**kwargs):
    deadline=time.monotonic()+15
    while not (directory/'upload-finalized').exists():
        if time.monotonic()>deadline: raise RuntimeError('upload did not overlap diagnostics')
        time.sleep(.01)
    if fail: raise RuntimeError('diagnostics failed after finalized upload')
    return original(*args,**kwargs)
runner.build_diagnostics=diagnostics
runner.run(directory)
'''
        for fail_after_upload in (False, True):
            with self.subTest(fail_after_upload=fail_after_upload):
                task_id = self.enqueue()
                lease = self.claim('early-publication-worker')
                observed = []
                test = self

                class DiagnosticProcess(ProcessManager):
                    def run(self, directory, timeout, **controls):
                        return super().run(directory, timeout, **controls,
                            command=[sys.executable, '-c', command, str(directory), str(int(fail_after_upload))])

                class ObservedData(DataClient):
                    def upload_values(self, *args, **kwargs):
                        receipt = super().upload_values(*args, **kwargs)
                        test.assert_reports_status(task_id, 409)
                        test.assertEqual(test.http(f'/api/tasks/{task_id}/values')[0], 409)
                        with psycopg.connect('host=db dbname=factorpool user=factorpool') as connection:
                            row = connection.execute('SELECT row_count FROM factor_value_publications WHERE task_id=%s',
                                                     (task_id,)).fetchone()
                        test.assertEqual(row, (3,))
                        observed.append(receipt)
                        (Path(args[3]).parent/'upload-finalized').write_text('ready')
                        return receipt

                with tempfile.TemporaryDirectory() as temporary:
                    worker = Worker(root=temporary, concurrency=1, heartbeat_seconds=.1,
                                    process_manager=DiagnosticProcess(), data_factory=ObservedData)
                    try:
                        worker.execute(lease)
                        self.assertEqual(list(Path(temporary).iterdir()), [])
                    finally:
                        worker.executor.shutdown(wait=True)
                self.assertEqual(len(observed), 1)
                code, task = self.http(f'/api/tasks/{task_id}')
                self.assertEqual(code, 200, task)
                self.assertEqual(task['status'], 'FAILED' if fail_after_upload else 'SUCCEEDED')
                expected = 409 if fail_after_upload else 200
                self.assert_reports_status(task_id, expected)
                self.assertEqual(self.http(f'/api/tasks/{task_id}/values')[0], expected)
                if not fail_after_upload:
                    self.assert_published_report_contract(task_id)


if __name__ == '__main__':
    unittest.main()
