"""Authenticated, local video recordings with code/model provenance."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from engine import APIError

MAX_BYTES = 256 * 1024 * 1024
_LOCK = threading.RLock()
_FIELDS = {'duration_s', 'runtime', 'code_version_id', 'code_sha256', 'model_id',
           'policy', 'started_at', 'ended_at', 'stop_reason', 'width', 'height', 'fps',
           'source', 'controller_status', 'loaded'}


class RecordingStore:
    def __init__(self, root):
        self.root = Path(root).resolve() / 'data' / 'recordings'
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self):
        records = []
        for path in self.root.glob('record-*.json'):
            try:
                value = json.loads(path.read_text())
                if value.get('id') == path.stem and (self.root / value['filename']).is_file():
                    records.append(value)
            except (ValueError, KeyError, OSError):
                continue
        return sorted(records, key=lambda x: x['created_at'], reverse=True)

    def save(self, content, mime, metadata):
        mime = mime.split(';')[0].strip().lower()
        if mime not in ('video/webm', 'video/mp4'):
            raise APIError(415, 'WebM 또는 MP4 영상만 저장할 수 있습니다')
        if not 16 <= len(content) <= MAX_BYTES:
            raise APIError(413, '영상 크기는 16바이트 이상 256 MiB 이하여야 합니다')
        if (mime == 'video/webm' and not content.startswith(b'\x1a\x45\xdf\xa3')) or (
                mime == 'video/mp4' and content[4:8] != b'ftyp'):
            raise APIError(422, '영상 컨테이너 형식을 확인할 수 없습니다')
        if not isinstance(metadata, dict):
            raise APIError(422, '녹화 정보는 JSON 객체여야 합니다')
        clean = {k: v for k, v in metadata.items() if k in _FIELDS}
        for k, value in clean.items():
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise APIError(422, '녹화 정보의 형식이 올바르지 않습니다')
            if isinstance(value, float) and not math.isfinite(value):
                raise APIError(422, '녹화 정보는 유한한 수여야 합니다')
            if isinstance(value, str) and len(value) > 256:
                raise APIError(422, '녹화 정보가 너무 깁니다')
        sha = hashlib.sha256(content).hexdigest()
        # A lost HTTP response can be retried without saving a duplicate video.
        rid = 'record-' + sha[:24]
        extension = 'webm' if mime == 'video/webm' else 'mp4'
        filename = rid + '.' + extension
        target = self.root / filename
        manifest = self.root / (rid + '.json')
        with _LOCK:
            if manifest.exists():
                return self.get(rid)[2]
            temporary = self.root / (filename + '.part')
            try:
                with temporary.open('wb') as stream:
                    stream.write(content)
                    stream.flush()
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != sha:
                    raise APIError(500, '저장된 영상의 해시 확인에 실패했습니다')
                # Browser WebM chunks lack final duration/cues. Copy-remux adds a
                # seekable container without re-encoding or changing video frames.
                normalized = self.root / (rid + '.normalized.' + extension)
                ffmpeg = shutil.which('ffmpeg')
                remuxed = False
                try:
                    if ffmpeg:
                        command = [ffmpeg,'-nostdin','-y','-v','error','-i',str(temporary),
                                   '-map','0:v:0','-c','copy','-an']
                        if extension == 'mp4': command += ['-movflags','+faststart']
                        command += ['-f',extension,str(normalized)]
                        outcome = subprocess.run(command, capture_output=True, timeout=45)
                        remuxed = outcome.returncode == 0 and normalized.is_file() and normalized.stat().st_size > 16
                    if remuxed: normalized.replace(temporary)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Keep the complete original upload if optional remux fails.
                finally:
                    normalized.unlink(missing_ok=True)
                temporary.replace(target)
                final_bytes = target.read_bytes()
                record = dict(id=rid, filename=filename, path=str(target), created_at=time.time(),
                              bytes=len(final_bytes), sha256=hashlib.sha256(final_bytes).hexdigest(),
                              upload_sha256=sha, container_normalized=remuxed, mime=mime, metadata=clean,
                              url='/api/recordings/' + rid)
                temp_manifest = manifest.with_suffix('.json.part')
                temp_manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2))
                temp_manifest.replace(manifest)
                return record
            finally:
                temporary.unlink(missing_ok=True)

    def get(self, rid):
        if not re.fullmatch(r'record-[0-9a-f]{24}', rid):
            raise APIError(404, '녹화 파일을 찾을 수 없습니다')
        try:
            record = json.loads((self.root / (rid + '.json')).read_text())
            filename = record['filename']
            if filename not in (rid + '.webm', rid + '.mp4'):
                raise APIError(409, '녹화 파일 정보가 변경되었습니다')
            data = (self.root / filename).read_bytes()
        except (ValueError, KeyError, OSError):
            raise APIError(404, '녹화 파일을 찾을 수 없습니다')
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise APIError(409, '저장 후 영상 파일이 변경되었습니다')
        return data, record['mime'], record


def handle_recording_request(handler, post, path):
    if post: handler.close_connection = True
    handler._auth()
    if not handler._local_request():
        raise APIError(403, '이 Mac의 로컬 화면에서만 녹화를 저장할 수 있습니다')
    store = getattr(handler.server, 'recordings', None)
    if not store:
        raise APIError(503, '녹화 저장 기능을 사용할 수 없습니다')
    if path == '/api/recordings':
        if not post:
            handler._send(200, {'recordings': store.list(), 'directory': str(store.root)})
            return True
        # Auth and size checks happen before reading a potentially large upload.
        if handler.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Chunked upload is not supported')
        try:
            length = int(handler.headers.get('Content-Length', '0'))
        except ValueError:
            raise APIError(400, 'Invalid Content-Length')
        if not 16 <= length <= MAX_BYTES:
            raise APIError(413, '영상 크기 제한은 256 MiB입니다')
        encoded = handler.headers.get('X-Recording-Metadata', '{}')
        if len(encoded) > 8192:
            raise APIError(413, '녹화 정보가 너무 깁니다')
        try:
            metadata = json.loads(unquote(encoded))
        except (ValueError, UnicodeError):
            raise APIError(422, '녹화 정보를 읽지 못했습니다')
        content = handler.rfile.read(length)
        if len(content) != length:
            raise APIError(400, '영상 전송이 끝나지 않았습니다')
        record = store.save(content, handler.headers.get('Content-Type', ''), metadata)
        handler._send(201, record)
        return True
    if not post and path.startswith('/api/recordings/'):
        data, mime, record = store.get(path[len('/api/recordings/'):])
        handler._send(200, data, mime, {'Content-Disposition': 'inline; filename="' + record['filename'] + '"',
                                      'X-Content-Sha256': record['sha256']})
        return True
    raise APIError(404, '녹화 경로를 찾을 수 없습니다')
