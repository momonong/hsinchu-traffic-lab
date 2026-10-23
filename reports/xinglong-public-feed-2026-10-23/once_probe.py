"""One authorized TDX auth + two filtered reads. No retry, proxy or redirects."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parents[1]
HOST = 'https://tdx.transportdata.tw'
AUTH_URL = HOST+'/auth/realms/TDXConnect/protocol/openid-connect/token'
FILTER = "ETagPairID eq 'ES-0075' or ETagPairID eq 'ES-0011'"
QUERY = urllib.parse.urlencode({'$filter': FILTER, '$top': 2, '$format': 'JSON'})
URLS = {
    'static': HOST+'/api/basic/v2/Road/Traffic/ETagPair/City/HsinchuCounty?'+QUERY,
    'live': HOST+'/api/basic/v2/Road/Traffic/Live/ETag/City/HsinchuCounty?'+QUERY,
}
CAP = 65536
STAGE_CAP = 300000
TIMEOUT = 20
WORK_SECONDS = 88  # Leave a bounded margin for the final local receipt.
HEADER_NAMES = ('Date', 'Retry-After', 'RateLimit-Limit', 'RateLimit-Remaining',
                'RateLimit-Reset', 'RateLimit-Policy', 'RateLimit',
                'X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset')


class Halt(Exception):
    """Only fixed local reason codes may be persisted, never provider messages."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode()+b'\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def size(directory):
    return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())


def write_new(path, data, *, stage=STAGE):
    if size(stage)+len(data) > STAGE_CAP:
        raise Halt('stage_byte_cap')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def credentials():
    """Fixed private path; caller never receives/logs the full private object."""
    fd = os.open(ROOT/'.state/tdx-credentials.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise Halt('credential_permissions')
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise Halt('credential_size')
    values = json.loads(raw)
    result = {}
    for name in ('client_id', 'client_secret'):
        value = values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise Halt('credential_schema')
        result[name] = value.strip()
    return result


def headers_only(headers):
    result = {}
    for name in HEADER_NAMES:
        value = headers.get(name)
        if isinstance(value, str):
            result[name] = value.replace('\r', '').replace('\n', '')[:256]
    return result


def adapter():
    path = STAGE/'feed_adapter.py'
    spec = importlib.util.spec_from_file_location('xinglong_feed', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_once(directory, *, opener=None, load_credentials=credentials, now=utcnow,
             monotonic=time.monotonic, sleep=time.sleep, stage=STAGE):
    """Inject fake transport/clocks in tests. Existing intent always forbids rerun."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(directory/'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'wb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Halt('owned_lock_busy') from None
        if (directory/'intent.json').exists():
            raise Halt('intent_exists_no_retry_or_unknown_resend')
        # Reserve enough space for the largest allowed data and small receipts.
        if size(stage)+2*CAP+45000 > STAGE_CAP:
            raise Halt('insufficient_stage_budget_before_network')
        started, end = monotonic(), monotonic()+WORK_SECONDS
        receipt = {'schema_version': 1, 'kind': 'single_shot_official_TDX_validation',
            'status': 'running', 'started_at': now(), 'requests': [],
            'max_requests': 3, 'max_data_requests': 2, 'max_body_per_response_bytes': CAP,
            'max_data_body_bytes': 2*CAP, 'max_wall_seconds': 90, 'timeout_seconds': TIMEOUT,
            'request_body_bytes_received': 0, 'data_body_bytes_received': 0,
            'remaining_quota': None, 'remaining_quota_semantics': 'unknown; no usage endpoint queried',
            'transport_connected': False, 'pair_mapping_verified': False,
            'measurement_usable': False, 'continuous_live_connection': False,
            'forecast': None, 'whole_route_ETA': None, 'recommendation': None,
            'retry': False, 'proxy': False, 'redirects': False,
            'code_sha256': sha(Path(__file__).read_bytes()),
            'adapter_sha256': sha((STAGE/'feed_adapter.py').read_bytes())}
        write_new(directory/'intent.json', encode({
            'created_at': receipt['started_at'], 'pid': os.getpid(),
            'code_sha256': receipt['code_sha256'], 'adapter_sha256': receipt['adapter_sha256'],
            'urls': {'auth': AUTH_URL, **URLS}, 'max_requests': 3,
            'no_resume_or_resend': True, 'unknown_result_requires_inspection_not_reexecution': True,
        }), stage=stage)
        last_start = None
        opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        def guard():
            if monotonic() >= end:
                raise Halt('absolute_work_deadline')

        def fetch(kind, request):
            nonlocal last_start
            guard()
            if last_start is not None:
                wait = max(0., 1-(monotonic()-last_start))
                if monotonic()+wait >= end:
                    raise Halt('absolute_work_deadline')
                if wait:
                    sleep(wait)
            guard()
            index = len(receipt['requests'])+1
            if index > 3:
                raise Halt('request_cap')
            row = {'index': index, 'kind': kind, 'url': request.full_url,
                   'method': request.get_method(), 'request_started_at': now(),
                   'http_status': None, 'body_bytes_received': 0, 'headers': {}}
            # Durable reservation BEFORE sending, including auth. A crash cannot reset it.
            write_new(directory/f'request-{index}-intent.json', encode(row), stage=stage)
            receipt['requests'].append(row)
            last_start = monotonic()
            response = None
            try:
                response = opener.open(request, timeout=min(TIMEOUT, end-monotonic()))
                row.update(http_status=response.status, headers=headers_only(response.headers))
                if response.status != 200:
                    raise Halt('http_non_200_stop')
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) >= CAP):
                    raise Halt('body_length_at_or_above_cap')
                parts, total = [], 0
                while total < CAP:
                    guard()
                    part = response.read(min(8192, CAP-total))
                    if not part:
                        break
                    total += len(part)
                    row['body_bytes_received'] = total
                    parts.append(part)
                if total >= CAP:
                    raise Halt('body_reached_cap_no_extra_byte_read')
                if length is not None and total != int(length):
                    raise Halt('content_length_mismatch_incomplete_body')
                data = b''.join(parts)
                row['full_body_received_at'] = now()
                parsed = json.loads(data)
                row['parse_completed_at'] = now()
                # Authentication responses never get a body/hash/path in receipts.
                if kind != 'auth':
                    key = 'ETagPairs' if kind == 'static' else 'ETagPairLives'
                    if (not isinstance(parsed, dict) or not isinstance(parsed.get(key), list)
                            or len(parsed[key]) > 2 or any(not isinstance(x, dict) for x in parsed[key])):
                        raise Halt('unexpected_county_data_schema')
                    if any(x.get('ETagPairID') not in ('ES-0075', 'ES-0011') for x in parsed[key]):
                        raise Halt('response_outside_fixed_pair_scope')
                    name = f'HsinchuCounty_{kind}.json'
                    write_new(directory/name, data, stage=stage)
                    row.update(body_sha256=sha(data), body_file=name)
                row['status'] = 'received_parsed'
                return parsed, row
            except urllib.error.HTTPError as error:
                row.update(http_status=error.code, headers=headers_only(error.headers),
                           status='stopped_http_error', error_type='HTTPError')
                error.close()  # Never read, print or save error bodies.
                raise Halt('http_error_stop_no_retry') from None
            except BaseException as error:
                row.update(status='stopped_error', error_type=type(error).__name__)
                raise
            finally:
                if response is not None:
                    response.close()
                row['finished_at'] = now()
                receipt['request_body_bytes_received'] += row['body_bytes_received']
                if kind != 'auth':
                    receipt['data_body_bytes_received'] += row['body_bytes_received']
                write_new(directory/f'request-{index}-receipt.json', encode(row), stage=stage)

        try:
            values = load_credentials()
            form = urllib.parse.urlencode({'grant_type': 'client_credentials', **values}).encode()
            auth, _ = fetch('auth', urllib.request.Request(AUTH_URL, data=form,
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}))
            token = auth.get('access_token') if isinstance(auth, dict) else None
            if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
                raise Halt('authentication_token_schema')
            if auth.get('token_type', 'bearer').lower() != 'bearer':
                raise Halt('authentication_token_type')
            del auth, form, values
            objects, rows = {}, {}
            for kind, url in URLS.items():
                objects[kind], rows[kind] = fetch(kind, urllib.request.Request(url,
                    headers={'Authorization': 'Bearer '+token, 'Accept': 'application/json'}))
            del token
            receipt['transport_connected'] = True
            guard()
            F = adapter()
            frame = F.normalize(objects['static'], objects['live'], as_of=now(), receipt={
                'full_body_received_at': rows['live']['full_body_received_at'],
                'parse_completed_at': rows['live']['parse_completed_at']})
            buffer = F.RollingBuffer()
            ingested = buffer.ingest(frame)
            mapping = all(s['identity_valid'] for s in frame['segments'])
            diagnostic = {'schema_version': 1, 'kind': 'single_shot_transport_and_measurement_validation',
                'as_of': frame['as_of'], 'transport_connected': True,
                'transport_semantics': 'one auth and two filtered GETs; not continuous live',
                'pair_mapping_verified': mapping,
                'measurement_usable': all(s['usable_asof'] for s in frame['segments']),
                'remaining_quota': None, 'forecast': None, 'whole_route_ETA': None,
                'recommendation': None, 'normalized_snapshot': frame,
                'bounded_buffer_ingest': ingested, 'bounded_buffer_view': buffer.view(as_of=frame['as_of']),
                'input_bodies': {kind: {k: row[k] for k in ('body_file', 'body_sha256',
                    'request_started_at', 'full_body_received_at', 'parse_completed_at')} for kind, row in rows.items()}}
            data = encode(diagnostic)
            if len(data) > 20000:
                raise Halt('diagnostic_byte_cap')
            write_new(directory/'live-diagnostic.json', data, stage=stage)
            receipt.update(status='single_shot_complete', transport_connected=True,
                pair_mapping_verified=mapping, measurement_usable=diagnostic['measurement_usable'],
                diagnostic_file='live-diagnostic.json', diagnostic_sha256=sha(data),
                pair_statuses=[{'pair_id': s['pair_id'], 'quality': s['quality']['status'],
                    'freshness': s['freshness']['status'], 'availability': s['availability']['status'],
                    'usable_asof': s['usable_asof']} for s in frame['segments']])
        except BaseException as error:
            # No exception text/traceback, HTTP body or credential ever enters outputs.
            reason = str(error) if isinstance(error, Halt) else 'local_or_transport_exception'
            receipt.update(status='stopped_no_retry', stop_reason=reason, error_type=type(error).__name__)
        finally:
            receipt.update(finished_at=now(), elapsed_seconds=monotonic()-started,
                           peak_rss_KiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            write_new(directory/'receipt.json', encode(receipt), stage=stage)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Consumes the one authorized intent; no rerun')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required; code/tests alone do not make source requests')
    resource.setrlimit(resource.RLIMIT_AS, (128*1024**2, 128*1024**2))
    allowed = os.sched_getaffinity(0)
    os.sched_setaffinity(0, {min(allowed)})
    def alarm(signum, frame):
        raise Halt('absolute_wall_deadline')
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(89)
    try:
        result = run_once(STAGE/'live-validation-once')
        print(json.dumps({k: result[k] for k in ('status', 'transport_connected',
            'pair_mapping_verified', 'measurement_usable', 'remaining_quota')}, ensure_ascii=False))
        return 0 if result['status'] == 'single_shot_complete' else 2
    except BaseException as error:
        print(json.dumps({'status': 'not_executed_or_intent_guard_stopped',
            'reason': str(error) if isinstance(error, Halt) else 'local_exception',
            'error_type': type(error).__name__}))
        return 2
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
