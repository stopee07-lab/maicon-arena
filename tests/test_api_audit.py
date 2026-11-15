import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from api_audit import APIAudit


class AuditTests(unittest.TestCase):
    def test_nested_credentials_and_known_secret_in_error_are_redacted(self):
        audit = APIAudit(secrets=['TEAMSECRET', 'ADMINSECRET'])
        ctx = audit.begin('POST', '/api/report/detection?mission_code=TEAMSECRET',
                          {'mission_code': 'TEAMSECRET'},
                          {'sectors': [], 'nested': {'apiKey': 'unknown-key', 'Authorization': 'Bearer raw'}},
                          phase='running')
        audit.record(ctx, 422, {'detail': 'bad TEAMSECRET and ADMINSECRET', 'received': {'token': 'other'}})
        row = audit.snapshot()['rows'][0]
        encoded = json.dumps(row)
        for value in ['TEAMSECRET', 'ADMINSECRET', 'unknown-key', 'Bearer raw', 'other']:
            self.assertNotIn(value, encoded)
        self.assertEqual(row['path'], '/api/report/detection')
        self.assertEqual(row['status'], 422)
        self.assertEqual(row['phase'], 'running')

    def test_only_report_routes_are_recorded_and_images_are_omitted(self):
        audit = APIAudit()
        for path in ['/api/config', '/api/admin/state', '/sim/robot/state', '/api/report/not-a-route']:
            audit.record(audit.begin('GET', path), 200, {'secret': 'not retained'})
        self.assertEqual(audit.snapshot()['rows'], [])
        audit.record(audit.begin('GET', '/api/report/drone'), 200, b'private image bytes', 'image/jpeg')
        row = audit.snapshot()['rows'][0]
        self.assertEqual(row['response'], {'omitted': '영상·바이너리 내용은 저장하지 않습니다.', 'bytes': 19})

    def test_ring_buffer_is_bounded_and_snapshot_is_detached(self):
        audit = APIAudit(limit=3)
        for i in range(5):
            audit.record(audit.begin('GET', '/api/report/me'), 200, {'index': i})
        state = audit.snapshot()
        self.assertEqual([r['response']['index'] for r in state['rows']], [4, 3, 2])
        self.assertEqual(state['dropped'], 2)
        state['rows'][0]['response']['index'] = -1
        self.assertEqual(audit.snapshot()['rows'][0]['response']['index'], 4)

    def test_image_fields_and_inline_image_data_are_hidden(self):
        audit = APIAudit()
        ctx = audit.begin('POST', '/api/report/detection', body={
            'image': 'private-image-content',
            'nested': {'camera_frame': 'private-camera-content'},
            'notes': 'received data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAABQ',
            'encoded': '/9j/abcdefghijklmnopqrstuv'})
        audit.record(ctx, 422, {'detail': 'bad image'})
        encoded = json.dumps(audit.snapshot())
        for value in ['private-image-content', 'private-camera-content', 'iVBORw0', '/9j/']:
            self.assertNotIn(value, encoded)

    def test_large_or_deep_payload_is_bounded(self):
        audit = APIAudit()
        item = {'items': ['x' * 10000] * 1000}
        for _ in range(20):
            item = {'nested': item}
        audit.record(audit.begin('POST', '/api/report/ready', body=item), 200, {'rows': ['y' * 10000] * 1000})
        self.assertLess(len(json.dumps(audit.snapshot())), 30000)

    def test_concurrent_records_have_unique_ids_and_duration(self):
        audit = APIAudit(limit=100)
        def insert(_):
            audit.record(audit.begin('GET', '/api/report/order', phase='ready'), 409, {'detail': '아직 경기 전입니다'})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(insert, range(80)))
        rows = audit.snapshot()['rows']
        self.assertEqual(len({row['id'] for row in rows}), 80)
        self.assertTrue(all(row['duration_ms'] >= 0 and row['status'] == 409 for row in rows))


if __name__ == '__main__':
    unittest.main()
