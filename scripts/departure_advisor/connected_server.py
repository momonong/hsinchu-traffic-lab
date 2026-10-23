"""Read-only candidate UI and public sealed-issue export. No model/source jobs.

The inherited prospective() is the frozen canonical verification chain.
Only current-source presentation, public projection, assets and lifecycle differ.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import signal
import time
from urllib.parse import parse_qs, urlsplit

from . import prospective_server as canonical

ROOT = canonical.ROOT
REPORT = ROOT / 'reports/departure-connected-2026-10-22'
PROTOTYPE = ROOT / 'prototype/departure-connected-2026-10-22'
STATIC = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js',
          '/adapter.js': 'adapter.js', '/style.css': 'style.css'}
CAL_DAYS = ['2026-08-13', '2026-08-14', '2026-08-17', '2026-08-18', '2026-08-19',
            '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25', '2026-08-26']
ApiUnavailable, BadQuery = canonical.ApiUnavailable, canonical.BadQuery


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parsed_usable(parsed, dataset):
    """Country-wide rejection counters are not rejected study rows."""
    expected = 5 if dataset == 'M03A' else 10
    if not isinstance(parsed.get('file_errors'), list) or parsed['file_errors']:
        return False
    counters = parsed.get('counts')
    if not isinstance(counters, dict) or any(not count(v) for v in counters.values()):
        return False
    available, actual_expected, rows = (parsed.get(k) for k in
        ('context_available_rows', 'context_expected_rows', 'row_count'))
    return (all(count(v) for v in (available, actual_expected, rows))
            and actual_expected == expected and 0 < available <= expected and rows >= available)


class Application(canonical.Application):
    # Avoid creating/loading the historical repository. Historical calibration
    # replay remains the existing local aggregate adapter in the candidate UI.
    def __init__(self, report=canonical.REPORT, clock=time.time, frozen_state=None):
        super().__init__(report, repository=object(), clock=clock)
        self.frozen_state = frozen_state

    def snapshot(self):
        return self.frozen_state if self.frozen_state is not None else super().snapshot()

    def current(self, state, now):
        result = dict(status='not_ready', source_time=None, received_at=None, sources=[],
            summary='尚無可用的完整來源。', summary_en='No usable complete source is available.')
        if state is None:
            return result
        runtime = state.get('process', {})
        if ((self.report / 'STOP').exists() or runtime.get('status') != 'running'
                or runtime.get('mode') != 'morning' or not canonical.alive(runtime)):
            result.update(status='stopped', summary='晨間程序已停止；封存資料不是現在路況。',
                summary_en='Morning collection is stopped. Archived data is not current road information.')
            return result
        latest = {}
        for attempt in state.get('attempts', []):
            if attempt.get('dataset') in canonical.DATASETS:
                latest[attempt['dataset']] = attempt
        ends, received = [], []
        for dataset in canonical.DATASETS:
            a = latest.get(dataset)
            item = dict(dataset=dataset, status='not_received')
            if a is not None:
                item['status'] = a.get('status', 'not_received')
            if a and a.get('status') == 'received_parsed' and a.get('parsed'):
                source, available = a.get('source_epoch'), a.get('available_epoch')
                if not all(finite(v) for v in (source, available, now)) or not source <= available <= now:
                    raise ApiUnavailable('current_source_clock_invalid')
                path = (canonical.ROOT / a['parsed']['path']).resolve()
                if not path.is_relative_to(self.report.resolve()) or canonical.digest(path) != a['parsed']['sha256']:
                    raise ApiUnavailable('current_source_hash_or_path_mismatch')
                p = canonical.read(path)
                if (p.get('dataset') != dataset or p.get('interval_end_epoch') != source
                        or p.get('interval_start_epoch') != source - 300):
                    raise ApiUnavailable('current_source_interval_identity_mismatch')
                age = now - source
                item.update(source_time=canonical.iso(source), received_at=canonical.iso(available),
                    age_seconds=age, available_rows=p.get('context_available_rows'),
                    expected_rows=p.get('context_expected_rows'))
                if not parsed_usable(p, dataset):
                    item['status'] = 'parsed_invalid'
                elif age > 900:
                    item['status'] = 'stale'
                else:
                    item['status'] = 'available' if p['context_available_rows'] == p['context_expected_rows'] else 'data_insufficient'
                    ends.append(source); received.append(available)
            result['sources'].append(item)
        statuses = [s['status'] for s in result['sources']]
        if statuses.count('available') == 3:
            status = 'available'
        elif 'parsed_invalid' in statuses or 'data_insufficient' in statuses or ends:
            status = 'data_insufficient'
        elif 'stale' in statuses:
            status = 'stale'
        else:
            status = 'not_ready'
        result.update(status=status, source_time=canonical.iso(min(ends)) if ends else None,
            received_at=canonical.iso(max(received)) if received else None,
            summary='來源內容、研究列與時鐘分別檢查；此模式不提供出發建議。',
            summary_en='Source content, study rows and clocks are checked separately. This view gives no departure advice.')
        return result

    def status(self):
        now, state = self.clock(), self.snapshot()
        return dict(schema_version=1, as_of=canonical.iso(now), delivery_mode='connected_server',
            scope=dict(gates=list(canonical.GATES), vehicle_class='31', timezone='Asia/Taipei'),
            qualification=dict(forecast_display_method='empirical', policy_qualified=False,
                reason_codes=['no_qualified_policy']),
            replay=dict(available_dates=CAL_DAYS), current_observation=self.current(state, now),
            live_prediction=dict(status='unavailable', reason_codes=['sealed_shadow_only_no_advice']),
            prospective_dates=list(canonical.ALLOWED_DAYS), schedule=dict(status='not_displayed_by_candidate'))

    def route(self, path, query):
        if path == '/api/status':
            if query:
                raise BadQuery('status_has_no_query_fields')
            return self.status()
        if path == '/api/prospective':
            return public_issue(self.prospective(query))
        raise BadQuery('unknown_API_path')


def public_issue(value):
    """Projection follows canonical verification, not an alternative validator."""
    keys = ('schema_version', 'mode', 'as_of', 'day', 'nominal', 'deadline', 'cost',
        'issue_id', 'issue_time', 'status', 'forecast_display_method', 'policy_qualified',
        'policy_selected_method', 'not_nominal_is_proven_optimal', 'candidates',
        'truth_loaded', 'per_request_model_forward', 'summary', 'summary_en',
        'finished_at', 'sealed_at')
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def expected_ids(day='2026-10-21'):
    return [canonical.issue_identity(day, n) for n in range(390, 556, 15)]


def export_snapshot(application, day='2026-10-21'):
    """One state snapshot; exact scheduled identities, never directory discovery."""
    now, state = application.clock(), application.snapshot()
    frozen = Application(application.report, clock=lambda: now, frozen_state=state)
    values, refs = [], {}
    wanted = expected_ids(day)
    present = [key for key, issue in state.get('issues', {}).items() if issue.get('day') == day]
    if len(present) != 12 or set(present) != set(wanted):
        raise ApiUnavailable('complete_unique_twelve_issue_set_required')
    for nominal, identity in zip(range(390, 556, 15), wanted):
        clock = lambda n: f'{n//60:02}:{n%60:02}'
        value = frozen.prospective({k: [v] for k, v in dict(day=day, nominal=clock(nominal),
            deadline=clock(nominal+45), cost='0.5').items()})
        if value['issue_id'] != identity or value['status'] != 'sealed_shadow_only':
            raise ApiUnavailable('complete_verified_sealed_issue_set_required')
        values.append(public_issue(value))
        refs[identity] = {kind: state['issues'][identity][kind]['sha256'] for kind in ('forecast', 'seal', 'publication')}
    status = frozen.status()
    status['delivery_mode'] = 'sealed_snapshot'
    status['current_observation'] = dict(status='stopped', source_time=None, received_at=None, sources=[],
        summary='這是已驗封存資料快照，未連接即時來源；不是今日路況。',
        summary_en='This is a verified sealed-data snapshot with no live connection; it is not current road information.')
    payload = dict(schema_version=1, delivery_mode='sealed_snapshot', exported_at=canonical.iso(now),
        status=status, issues=values)
    return payload, refs


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()


def portable_html(data):
    html = (PROTOTYPE/'index.html').read_text()
    css = (PROTOTYPE/'style.css').read_text()
    adapter = (PROTOTYPE/'adapter.js').read_text().replace('export ', '')
    app = (PROTOTYPE/'app.js').read_text()
    app = app.replace(app.splitlines()[0], '').replace('export ', '')
    encoded = data.decode().replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    script = 'globalThis.CONNECTED_EXPORT='+encoded+';\n'+adapter+'\n'+app
    script = script.replace('</script', '<\\/script')
    html = html.replace('<link rel="stylesheet" href="./style.css">', '<style>'+css+'</style>')
    html = html.replace('<script type="module" src="./app.js"></script>', '')
    html = html.replace('</body>', '<script>'+script+'</script></body>')
    return html


def build_export():
    output = REPORT / 'export-v1'
    if output.exists():
        raise FileExistsError('Export is immutable; use an explicitly new version')
    source_bytes = (canonical.REPORT/'state.json').read_bytes()
    payload, refs = export_snapshot(Application(frozen_state=json.loads(source_bytes)))
    data = json_bytes(payload)
    output.mkdir()
    (output/'snapshot.json').write_bytes(data)
    (output/'portable-connected-v1.html').write_text(portable_html(data))
    receipt = dict(schema_version=1, exported_at=payload['exported_at'], issues=len(refs),
        snapshot_sha256=hashlib.sha256(data).hexdigest(), input_publication_bindings=refs,
        source_state_sha256=hashlib.sha256(source_bytes).hexdigest(),
        files={p.name: canonical.digest(p) for p in output.iterdir()},
        canonical_verifier_sha256=canonical.digest(Path(canonical.__file__)),
        exporter_sha256=canonical.digest(Path(__file__)), model_forward=0, source_HTTP=0, truth_read=False)
    (output/'receipt.json').write_bytes(json_bytes(receipt))
    print(json.dumps(receipt, ensure_ascii=False))


def repackage_export():
    """UI-only revision. No canonical read or changed snapshot clocks."""
    source, output = REPORT/'export-v1', REPORT/'export-v2'
    if output.exists():
        raise FileExistsError('Repackaged version already exists')
    original = canonical.read(source/'receipt.json')
    data = (source/'snapshot.json').read_bytes()
    if hashlib.sha256(data).hexdigest() != original['snapshot_sha256']:
        raise ApiUnavailable('export_snapshot_hash_mismatch')
    output.mkdir()
    target = output/'portable-connected-v2.html'
    target.write_text(portable_html(data))
    receipt = dict(schema_version=1, repackaged_at=canonical.iso(time.time()),
        exported_at=json.loads(data)['exported_at'], canonical_export_repeated=False,
        source_snapshot=dict(path=str((source/'snapshot.json').relative_to(ROOT)), sha256=original['snapshot_sha256']),
        source_export_receipt=dict(path=str((source/'receipt.json').relative_to(ROOT)), sha256=canonical.digest(source/'receipt.json')),
        files={target.name: canonical.digest(target)}, launch_hashes=launch_hashes(),
        reason='UI form Enter, draft-preserving clock updates and expiry actions',
        source_HTTP=0, model_forwards=0, truth_read=False)
    (output/'receipt.json').write_bytes(json_bytes(receipt))
    print(json.dumps(receipt, ensure_ascii=False))


class Handler(BaseHTTPRequestHandler):
    application = None

    def do_GET(self):
        url = urlsplit(self.path)
        try:
            if url.path in ('/api/status', '/api/prospective'):
                data = json_bytes(self.application.route(url.path, parse_qs(url.query, keep_blank_values=True)))
                content = 'application/json'
            elif url.path in STATIC and not url.query:
                path = PROTOTYPE/STATIC[url.path]
                data = path.read_bytes()
                content = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[path.suffix]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content+'; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        except BadQuery as error:
            self.send_error(400, str(error))
        except (ApiUnavailable, OSError, KeyError, ValueError, TypeError):
            self.send_error(503, 'Artifact unavailable or unverified')

    def log_message(self, *ignored):
        pass


def launch_hashes():
    paths = [Path(__file__), Path(canonical.__file__), Path(canonical.__file__).with_name('server.py')]
    paths += [PROTOTYPE/name for name in sorted(set(STATIC.values()))]
    return {str(p.relative_to(ROOT)): canonical.digest(p) for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true')
    parser.add_argument('--repackage-export', action='store_true')
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--duration-seconds', type=int, default=3600)
    args = parser.parse_args()
    if args.export:
        build_export(); return
    if args.repackage_export:
        repackage_export(); return
    if not args.admission or not 1 <= args.duration_seconds <= 3600:
        parser.error('Root admission and bounded duration 1..3600 are required')
    admission = canonical.read(args.admission)
    now = time.time()
    if admission.get('allowed_mode') != 'connected_server' or admission.get('hashes') != launch_hashes():
        raise ApiUnavailable('root_server_admission_hash_mismatch')
    absolute = admission.get('deadline_epoch')
    if not finite(absolute) or absolute <= now:
        raise ApiUnavailable('explicit_absolute_server_deadline_required')
    deadline = min(absolute, now+args.duration_seconds)
    REPORT.mkdir(exist_ok=True)
    with (REPORT/'server.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Handler.application = Application()
        server = HTTPServer(('127.0.0.1', 8766), Handler); server.timeout = 1
        stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
        receipt = dict(status='running', process=dict(pid=os.getpid(), start_ticks=stat[19],
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()),
            started_epoch=now, deadline_epoch=deadline, hashes=launch_hashes(),
            source_HTTP=0, model_forwards=0, policy_qualified=False)
        stopped = False
        def stop(*ignored):
            nonlocal stopped
            stopped = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        canonical.atomic_json(REPORT/'server-process.json', receipt)
        try:
            while not stopped and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
            receipt.update(status='stopped', stopped_epoch=time.time(), reason='signal' if stopped else 'bounded_deadline')
            canonical.atomic_json(REPORT/'server-process.json', receipt)


if __name__ == '__main__':
    main()
