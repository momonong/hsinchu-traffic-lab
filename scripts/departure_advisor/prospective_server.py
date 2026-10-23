"""New-stage loopback UI: live observations, sealed issues, historical replay.

GET requests read artifacts only. This server cannot collect sources, fit a
model, produce a forecast, or reveal prospective M06 truth through forecast API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import signal
import time
from urllib.parse import parse_qs, urlsplit

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'

from .parser import DATASETS, GATES, TZ
from .prospective import ROOT, REPORT, OUTPUT, iso, read, process_identity, validate_admission
from .prospective_features import ALLOWED_DAYS, issue_identity, local_epoch
from .prospective_evaluation import load_published_forecast, verified_json
from .prospective_store import atomic_json, digest
from .server import ReplayRepository, BadQuery, ApiUnavailable, minutes, query_values, finite_prediction

PROTOTYPE = ROOT / 'prototype/prospective-departure-2026-10-20'
STATIC = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js',
          '/adapter.js': 'adapter.js', '/style.css': 'style.css'}


def alive(process):
    try:
        value = process['process']
        stat = Path(f"/proc/{value['pid']}/stat").read_text().rsplit(')', 1)[1].split()
        return stat[19] == str(value['start_ticks']) and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == value['boot_id']
    except (KeyError, OSError, IndexError): return False


class Application:
    def __init__(self, report=REPORT, repository=None, clock=time.time):
        self.report, self.clock = Path(report), clock
        self.repository = repository or ReplayRepository()

    def snapshot(self):
        path = self.report / 'state.json'
        return read(path) if path.exists() else None

    def current(self, state, now):
        result = {'status': 'not_ready', 'source_time': None, 'received_at': None,
                  'summary': '尚無本階段完整接收的即時來源。',
                  'summary_en': 'No complete live source has been received in this stage.', 'sources': []}
        if state is None: return result
        runtime = state.get('process', {})
        running = runtime.get('status') == 'running' and runtime.get('mode') == 'morning' and alive(runtime)
        if (self.report / 'STOP').exists() or not running:
            result.update(status='stopped', summary='晨間採集程序未在運作；封存資料不是現在路況。',
                          summary_en='Morning collection is stopped. Archived data is not current road information.')
            return result
        latest = {}
        for attempt in state['attempts']:
            if attempt['dataset'] in DATASETS: latest[attempt['dataset']] = attempt
        valid, ends, receipts = [], [], []
        for dataset in DATASETS:
            attempt = latest.get(dataset); item = {'dataset': dataset, 'status': 'not_received'}
            if attempt is not None:
                item.update(status=attempt['status'], sequence=attempt['sequence'], source_time=iso(attempt['source_epoch']))
                if attempt.get('status') == 'received_parsed' and attempt.get('parsed'):
                    path = (ROOT / attempt['parsed']['path']).resolve()
                    age = now - attempt['source_epoch']
                    available = attempt.get('available_epoch')
                    if (path.is_relative_to(self.report.resolve()) and digest(path) == attempt['parsed']['sha256']
                            and isinstance(available, (int, float)) and math.isfinite(available)
                            and attempt['source_epoch'] <= available <= now and 0 <= age <= 900):
                        parsed = read(path)
                        if (parsed['dataset'] != dataset or parsed['interval_end_epoch'] != attempt['source_epoch']
                                or parsed['interval_start_epoch'] != attempt['source_epoch'] - 300):
                            raise ApiUnavailable('current_source_interval_identity_mismatch')
                        item.update(status='available', received_at=iso(available), age_seconds=age,
                                    available_rows=parsed['context_available_rows'], expected_rows=parsed['context_expected_rows'],
                                    counts=parsed['counts'], clock_semantics=parsed['timestamp_semantics'])
                        valid.append(dataset); ends.append(attempt['source_epoch']); receipts.append(available)
                    else: item['status'] = 'stale_or_unverified'
            result['sources'].append(item)
        if valid:
            result.update(status='available' if len(valid) == 3 else 'data_insufficient',
                          source_time=iso(min(ends)), received_at=iso(max(receipts)),
                          summary=f'已收到{len(valid)}/3來源；M03為觀測，M04／M05供固定模型特徵。',
                          summary_en=f'{len(valid)}/3 sources received. M03 is observational; M04/M05 supply frozen-model features.')
        return result

    def status(self):
        now = self.clock(); state = self.snapshot()
        dates = self.repository.available_dates()
        return {'schema_version': 1, 'as_of': iso(now),
                'scope': {'gates': list(GATES), 'vehicle_class': '31', 'timezone': 'Asia/Taipei'},
                'qualification': self.repository.qualification(),
                'replay': {'available_dates': dates, 'default_day': dates[-1] if dates else None},
                'current_observation': self.current(state, now),
                'live_prediction': {'status': 'unavailable', 'reason_codes': ['prospective_issues_are_separate_sealed_shadow_artifacts', 'no_qualified_policy']},
                'prospective_dates': list(ALLOWED_DAYS), 'source_requests': state['HTTP_requests'] if state else 0,
                'schedule': self.schedule()}

    def schedule(self):
        path = self.report / 'host-execution/installed.json'
        if not path.is_file(): return {'status': 'not_installed_or_not_verified'}
        # Host-owned installation evidence is not proof of source receipts or forecasts.
        return {'status': 'installation_receipt_present', 'path': str(path.relative_to(ROOT)),
                'sha256': digest(path), 'not_proof_of_live_forecast': True}

    def route(self, path, query):
        if path == '/api/status':
            if query: raise BadQuery('status_has_no_query_fields')
            return self.status()
        if path == '/api/prospective': return self.prospective(query)
        if path == '/api/options': return self.repository.options(query_values(query))
        if path == '/api/outcomes': return self.repository.outcomes(query_values(query))
        raise BadQuery('unknown_API_path')

    def prospective(self, query):
        if set(query) != {'day', 'nominal', 'deadline', 'cost'} or any(len(value) != 1 for value in query.values()):
            raise BadQuery('four_exact_prospective_query_fields')
        day, nominal_text, deadline_text, cost_text = [query[key][0] for key in ('day', 'nominal', 'deadline', 'cost')]
        nominal, deadline = minutes(nominal_text), minutes(deadline_text)
        try: cost = float(cost_text)
        except ValueError as error: raise BadQuery('fixed_cost_profiles_only') from error
        if cost not in (0, .25, .5, 1): raise BadQuery('fixed_cost_profiles_only')
        try: identity = issue_identity(day, nominal)
        except ValueError as error: raise BadQuery('outside_fixed_prospective_schedule') from error
        now = self.clock(); I = local_epoch(day, nominal-45); state = self.snapshot()
        response = {'schema_version': 1, 'mode': 'prospective_issue', 'as_of': iso(now),
                    'day': day, 'nominal': nominal_text, 'deadline': deadline_text, 'cost': cost,
                    'issue_id': identity, 'issue_time': iso(I), 'status': 'not_due' if now < I else 'not_published',
                    'forecast_display_method': 'empirical', 'policy_qualified': False,
                    'policy_selected_method': None, 'not_nominal_is_proven_optimal': True,
                    'candidates': [], 'truth_loaded': False, 'per_request_model_forward': False,
                    'summary': '策略尚未合格，不代表照常已被證明最佳。',
                    'summary_en': 'The policy is unqualified; keeping the nominal window is not proven optimal.'}
        if state is None: return response
        issue = state['issues'].get(identity)
        if not issue: return response
        response['status'] = issue['status']
        if issue['status'] == 'insufficient_input':
            receipt = verified_json(issue['receipt'], ROOT)
            if (receipt['issue_id'] != identity or receipt['input_cutoff_epoch'] != I
                    or receipt['input_manifest_sha256'] != digest(self.report/'input-manifest.json')
                    or not I <= receipt['finished_epoch'] <= now):
                raise ApiUnavailable('insufficient_input_receipt_identity_mismatch')
            response.update(input_coverage=receipt['input_coverage'], source_inputs=receipt['source_inputs'])
        if issue['status'] != 'sealed_shadow_only': return response
        forecast = load_published_forecast(issue, ROOT, expected_program=digest(self.report/'program.json'),
                                           expected_protocol=digest(self.report/'protocol.json'),
                                           expected_manifest=digest(self.report/'input-manifest.json'))
        if forecast is None or forecast['issue_epoch'] != I or forecast['nominal'] != nominal or forecast['day'] != day:
            raise ApiUnavailable('sealed_issue_identity_mismatch')
        publication = verified_json(issue['publication'], ROOT)
        if (publication['forecast'] != issue.get('forecast') or publication['seal'] != issue.get('seal')
                or publication['seal_completed_epoch'] != issue.get('seal_completed_epoch')):
            raise ApiUnavailable('published_index_binding_mismatch')
        if (not isinstance(issue['published_epoch'], (int, float)) or not math.isfinite(issue['published_epoch'])
                or not issue['seal_completed_epoch'] <= issue['published_epoch'] <= now):
            raise ApiUnavailable('publication_after_render_clock')
        if forecast.get('forecast_display_method') != 'empirical' or forecast.get('recommendation_qualified') is not False:
            raise ApiUnavailable('frozen_unqualified_display_contract_mismatch')
        values = forecast['methods']['empirical']['final']
        if len(values) != 5: raise ApiUnavailable('five_sealed_candidates_required')
        base = finite_prediction(values[2])
        for shift, value in zip((-30,-15,0,15,30), values):
            prediction = finite_prediction(value)
            gain = base['Tq50'] - prediction['Tq50'] if base and prediction else None
            start, end = local_epoch(day, nominal+shift), local_epoch(day, nominal+shift+15)
            response['candidates'].append({'shift_minutes': shift, 'window_start': iso(start), 'window_end': iso(end),
                'window_elapsed': end <= now, 'prediction': prediction, 'predicted_roadgain': gain,
                'shift_cost': cost*abs(shift), 'predicted_netgain': gain-cost*abs(shift) if gain is not None else None,
                'estimated_q90_within_deadline': nominal+shift+prediction['Aq90'] <= deadline if prediction else None})
        response.update(finished_at=iso(forecast['finished_epoch']), sealed_at=iso(issue['seal_completed_epoch']),
                        input_coverage=forecast['input_coverage'], source_inputs=forecast['source_inputs'],
                        model_diagnostics=forecast['methods'], CPU_trace={key: forecast.get(key) for key in
                            ['device','CPU_threads','forward_elapsed_seconds','phase_timing_seconds','raw_features',
                             'transformed_features','frozen_model_artifacts','selection_sha256']})
        return response


class Handler(BaseHTTPRequestHandler):
    application = None
    def do_GET(self):
        parsed = urlsplit(self.path)
        try:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path in ('/api/status', '/api/prospective', '/api/options', '/api/outcomes'):
                value = self.application.route(parsed.path, query)
            elif parsed.path in STATIC and not parsed.query:
                path = PROTOTYPE / STATIC[parsed.path]
                data = path.read_bytes(); content = 'text/html' if path.suffix == '.html' else ('text/css' if path.suffix == '.css' else 'text/javascript')
                self.send_response(200); self.send_header('Content-Type', content+'; charset=utf-8')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
            else: self.send_error(404); return
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        except BadQuery as error:
            self.send_error(400, str(error))
        except (ApiUnavailable, OSError, KeyError, ValueError, TypeError) as error:
            self.send_error(503, 'Artifact unavailable or unverified')
    def log_message(self, *ignored): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admission', type=Path, required=True)
    parser.add_argument('--duration-seconds', type=int)
    args = parser.parse_args(); program, protocol, hashes = validate_admission(args.admission, 'server')
    admission = read(args.admission)
    if admission.get('server_sha256') != digest(Path(__file__)):
        raise ApiUnavailable('new-stage server hash missing from root admission')
    if admission.get('historical_replay_server_sha256') != digest(Path(__file__).with_name('server.py')):
        raise ApiUnavailable('historical replay adapter hash missing from root admission')
    for name in STATIC.values():
        if admission.get('prototype_assets', {}).get(name) != digest(PROTOTYPE / name):
            raise ApiUnavailable('new prototype asset admission mismatch')
    now = time.time()
    if args.duration_seconds is not None and not 1 <= args.duration_seconds <= 43200:
        raise BadQuery('explicit server duration must be1..43200seconds')
    deadline = min(program['prospective_final_deadline_epoch'],
                   now + args.duration_seconds if args.duration_seconds is not None else program['development_deadline_epoch'])
    if now >= deadline: raise ApiUnavailable('server deadline expired; require a new explicit bounded review duration')
    with (REPORT / 'server.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Handler.application = Application(); server = HTTPServer(('127.0.0.1', 8766), Handler); server.timeout = 1
        process = process_identity(); process['server_sha256'] = digest(Path(__file__))
        stopped = False
        def stop(*ignored):
            nonlocal stopped
            stopped = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        receipt = dict(status='running', process=process, started_epoch=now, deadline_epoch=deadline,
                       URL='http://127.0.0.1:8766', contract=hashes, source_requests_by_API=0, model_forwards_by_API=0)
        atomic_json(REPORT / 'server-launches' / f"{process['instance_id']}-start.json", receipt, immutable=True)
        atomic_json(REPORT / 'server-process.json', receipt)
        try:
            while not stopped and time.time() < deadline and not (REPORT/'STOP-server').exists(): server.handle_request()
        finally:
            server.server_close(); receipt.update(status='stopped', stopped_epoch=time.time())
            atomic_json(REPORT / 'server-launches' / f"{process['instance_id']}-terminal.json", receipt, immutable=True)
            atomic_json(REPORT / 'server-process.json', receipt)


if __name__ == '__main__': main()
