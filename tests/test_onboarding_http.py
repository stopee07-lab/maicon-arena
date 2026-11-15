"""Real HTTP contract tests on an isolated temp root and OS-assigned port.

No physics worker, participant controller or live workspace server is started.
"""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from engine import APIError


class OnboardingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='maicon-http-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        web = self.root / 'web'
        web.mkdir()
        self.web_content = {}
        for name in ('workbench.html',):
            content = (Path(server.__file__).parent / 'web' / name).read_bytes()
            self.web_content[name] = content
            (web / name).write_bytes(content)
        root_patch = patch.object(server, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        log_patch = patch.object(server.Handler, 'log_message', return_value=None)
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.access = {
            'admin_key': 'fixture-admin-private',
            'viewer_key': 'fixture-viewer-private',
            'teams': [dict(slot=slot, mission_code=f'TEAM{i:04d}')
                      for i, slot in enumerate(('B-1', 'B-2', 'B-3', 'R-1', 'R-2', 'R-3'), 1)],
        }
        self.runtime = server.SimulationHTTPServer(('127.0.0.1', 0), self.access)
        self.worker = threading.Thread(target=self.runtime.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)
        self.connection = http.client.HTTPConnection('127.0.0.1', self.runtime.server_port, timeout=3)
        self.addCleanup(self.connection.close)
        self.admin = {'X-Admin-Key': self.access['admin_key']}
        self.code = self.access['teams'][0]['mission_code']

    def close_server(self):
        self.runtime.shutdown()
        self.runtime.server_close()
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def request(self, path, *, method='GET', body=None, headers=None):
        headers = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers.setdefault('Content-Type', 'application/json')
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        raw = response.read()
        data = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        return response.status, data

    def audit(self):
        status, data = self.request('/api/admin/api-audit', headers=self.admin)
        self.assertEqual(status, 200)
        return data

    def test_inspector_requires_admin_and_never_records_admin_reads(self):
        self.assertEqual(self.request('/api/admin/api-audit')[0], 401)
        self.assertEqual(self.request('/api/admin/api-audit', headers={'X-Admin-Key': self.access['viewer_key']})[0], 401)
        self.assertEqual(self.audit()['rows'], [])
        self.assertEqual(self.audit()['rows'], [])

    def test_valid_invalid_and_missing_report_code_are_logged_without_credentials(self):
        status, data = self.request('/api/report/me?mission_code=' + self.code + '&admin_key=' + self.access['admin_key'],
                                    headers={'Authorization': 'Bearer private-header-fixture'})
        self.assertEqual(status, 200)
        self.assertEqual(data['slot'], 'B-1')
        self.assertEqual(self.request('/api/report/me?mission_code=WRONG001')[0], 401)
        self.assertEqual(self.request('/api/report/me')[0], 422)
        rows = self.audit()['rows']
        self.assertEqual([row['status'] for row in rows], [422, 401, 200])
        self.assertTrue(all(row['method'] == 'GET' and row['path'] == '/api/report/me' for row in rows))
        encoded = json.dumps(rows)
        for secret in (self.code, 'WRONG001', self.access['admin_key'], 'private-header-fixture'):
            self.assertNotIn(secret, encoded)
        # Reusing HTTP/1.1 must not copy the previous request's audit context.
        self.request('/api/config')
        self.request('/')
        self.assertEqual(len(self.audit()['rows']), 3)

    def test_invalid_json_and_report_validation_preserve_safe_failure_records(self):
        invalid = b'{"mission_code":"TEAM0001","token":"malformed-body-secret"'
        self.assertEqual(self.request('/api/report/ready', method='POST', body=invalid,
                                      headers={'Content-Type': 'application/json'})[0], 422)
        self.assertEqual(self.request('/api/report/ready', method='POST', body={})[0], 422)
        self.assertEqual(self.request('/api/admin/control', method='POST', body={'action': 'prepare'}, headers=self.admin)[0], 200)
        self.assertEqual(self.request('/api/report/ready', method='POST', body={'mission_code': self.code})[0], 200)
        self.assertEqual(self.request('/api/admin/control', method='POST', body={'action': 'start'}, headers=self.admin)[0], 200)
        invalid_report = {'mission_code': self.code, 'sectors': [], 'nested': {'api_key': 'nested-private-key'}}
        self.assertEqual(self.request('/api/report/detection', method='POST', body=invalid_report)[0], 422)
        rows = self.audit()['rows']
        self.assertEqual([row['status'] for row in rows], [422, 200, 422, 422])
        self.assertEqual(rows[0]['phase'], 'running')
        self.assertEqual(rows[0]['response']['detail'][0]['loc'], ['body', 'sectors'])
        self.assertTrue(rows[1]['response']['accepted'])
        self.assertEqual(rows[-1]['request']['body'], {})
        encoded = json.dumps(rows)
        for secret in (self.code, 'malformed-body-secret', 'nested-private-key'):
            self.assertNotIn(secret, encoded)

    def test_response_errors_are_sanitized_at_http_boundary(self):
        detail = 'fixture failure: ' + self.code + ' / ' + self.access['admin_key']
        with patch.object(self.runtime.engine, 'report', side_effect=APIError(422, detail)):
            self.assertEqual(self.request('/api/report/ready', method='POST', body={'mission_code': self.code})[0], 422)
        row = self.audit()['rows'][0]
        self.assertNotIn(self.code, row['response']['detail'])
        self.assertNotIn(self.access['admin_key'], row['response']['detail'])
        self.assertIn('[숨김]', row['response']['detail'])

    def test_camera_bytes_are_not_retained(self):
        payload = b'private camera payload fixture'
        with patch.object(server.Handler, '_drone', return_value=(payload, {})):
            self.assertEqual(self.request('/api/report/drone?mission_code=' + self.code), (200, payload))
        row = self.audit()['rows'][0]
        self.assertEqual(row['response']['bytes'], len(payload))
        self.assertNotIn(payload.decode(), json.dumps(row))

    def test_home_and_legacy_run_page_route_to_their_real_html(self):
        for path, filename in (('/', 'workbench.html'), ('/?page=run', 'workbench.html'),
                               ('/?page=develop', 'workbench.html')):
            with self.subTest(path=path):
                self.assertEqual(self.request(path), (200, self.web_content[filename]))
        self.assertEqual(self.audit()['rows'], [])


if __name__ == '__main__':
    unittest.main()
