"""Small in-memory report inspector. No credentials, headers or camera bytes.

The operator can inspect already completed participant exchanges. This module
does not send reports and never exposes administrator ground truth.
The request/response comparison idea comes from Suhyeon's simulator; this
implementation is written for this server without copying its source.
"""
from collections import deque
import copy
from datetime import datetime, timezone
import math
import re
import threading
import time
from urllib.parse import urlsplit

REPORT_PATHS = frozenset('/api/report/' + name for name in
                         ('me', 'ready', 'order', 'target', 'interpretation', 'detection', 'situation', 'drone'))
SENSITIVE = re.compile(r'missioncode|adminkey|viewerkey|apikey|token|password|secret|authorization|cookie|credential|headers|base64|image|camera|frame|jpeg|png', re.I)


class APIAudit:
    def __init__(self, limit=200, secrets=()):
        self.limit = max(1, min(500, int(limit)))
        self._secrets = tuple(sorted({str(s) for s in secrets if s}, key=len, reverse=True))
        self._rows = deque(maxlen=self.limit)
        self._lock = threading.Lock()
        self._next_id = 0

    def _clean(self, value):
        remaining = [8000, 300]

        def visit(item, depth=0):
            remaining[1] -= 1
            if depth > 6 or remaining[0] <= 0 or remaining[1] <= 0:
                return '… 일부 내용 생략'
            if isinstance(item, dict):
                result = {}
                for key, content in list(item.items())[:40]:
                    name = str(key)
                    safe_name = scrub(name)[:100]
                    result[safe_name] = '[숨김]' if SENSITIVE.search(re.sub(r'[^a-z0-9]', '', name.lower())) else visit(content, depth + 1)
                if len(item) > 40:
                    result['…'] = '추가 필드 생략'
                return result
            if isinstance(item, (list, tuple)):
                result = [visit(content, depth + 1) for content in item[:30]]
                if len(item) > 30:
                    result.append('… 추가 항목 생략')
                return result
            if isinstance(item, bytes):
                return {'omitted': '바이너리', 'bytes': len(item)}
            if item is None or isinstance(item, bool):
                return item
            if isinstance(item, (int, float)):
                return item if not isinstance(item, float) or math.isfinite(item) else None
            text = scrub(str(item))
            size = min(600, max(0, remaining[0]))
            remaining[0] -= min(len(text), size)
            return text[:size] + ('…' if len(text) > size else '')

        def scrub(text):
            for secret in self._secrets:
                text = text.replace(secret, '[숨김]')
            # URLs sometimes appear in validation messages. Never retain query credentials.
            text = re.sub(r'(?i)(mission_code|admin_key|viewer_key|api_key|token)=([^&\s]+)', r'\1=[숨김]', text)
            text = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [숨김]', text)
            text = re.sub(r'data:image/[^,\s]+,[^\s\"\']+', '[이미지 숨김]', text, flags=re.I)
            return re.sub(r'(?:iVBORw0KGgo|/9j/|R0lGOD|UklGR)[A-Za-z0-9+/=]{16,}', '[이미지 숨김]', text)

        return visit(value)

    def begin(self, method, path, query=None, body=None, phase=None):
        path = urlsplit(path).path.rstrip('/')
        if path not in REPORT_PATHS:
            return None
        return {'method': str(method), 'path': path, 'phase': str(phase or 'unknown'),
                'request': self._clean({'query': query or {}, 'body': body or {}}),
                'started': time.monotonic(), 'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}

    def record(self, context, status, response, content_type='application/json'):
        if context is None:
            return
        if isinstance(response, bytes) or not content_type.startswith('application/json'):
            response = {'omitted': '영상·바이너리 내용은 저장하지 않습니다.',
                        'bytes': len(response) if isinstance(response, bytes) else None}
        row = {key: value for key, value in context.items() if key != 'started'}
        row.update(status=int(status), response=self._clean(response),
                   duration_ms=round(max(0, time.monotonic() - context['started']) * 1000, 1))
        with self._lock:
            self._next_id += 1
            row['id'] = self._next_id
            self._rows.append(row)

    def snapshot(self):
        with self._lock:
            return {'rows': copy.deepcopy(list(reversed(self._rows))), 'limit': self.limit,
                    'dropped': max(0, self._next_id - len(self._rows)),
                    'scope': '현재 서버를 켠 뒤 완료된 참가팀의 임무 조회·보고 통신만 표시합니다. 인증값·영상은 제외됩니다.'}
