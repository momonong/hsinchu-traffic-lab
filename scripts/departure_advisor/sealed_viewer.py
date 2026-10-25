"""Bounded loopback viewer of verified sealed issues. Never runs research jobs."""
from __future__ import annotations

import argparse
import datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import re
import signal
import time
from urllib.parse import parse_qs, urlsplit

from . import connected_server as connected

canonical = connected.canonical
REPORT = canonical.REPORT
PROTOTYPE = Path(__file__).resolve().parents[2] / 'prototype/sealed-viewer'
STATIC = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
DAYS = tuple(canonical.ALLOWED_DAYS)
NOMINALS = tuple(range(390, 556, 15))
STATE_CAP = 4 * 1024 * 1024
OUTPUT_CAP = 256 * 1024
STATUSES = {'sealed_shadow_only', 'insufficient_input', 'not_due', 'not_published',
            'inflight', 'inflight_reserved', 'missed', 'missed_startup', 'missed_dispatch',
            'late_output', 'unknown', 'failed', 'unresolved_after_restart',
            'missed_dispatch_window', 'missed_restart_no_backfill', 'missed_model_warmload'}


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)


def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000000


def clock_text(value):
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError('invalid public clock')
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or not finite(parsed.timestamp()):
        raise ValueError('timezone-aware clock required')
    return value


def codes(values):
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError('invalid reason codes')
    return [v for v in values if isinstance(v, str) and re.fullmatch(r'[a-zA-Z0-9_:-]{1,96}', v)]


def read_state(path):
    """Exactly one bounded open; the response never rereads mutable state."""
    with Path(path).open('rb') as stream:
        data = stream.read(STATE_CAP + 1)
    if len(data) > STATE_CAP: raise ValueError('state size limit')
    def invalid(_): raise ValueError('nonfinite JSON')
    value = json.loads(data, parse_constant=invalid)
    if not isinstance(value, dict) or not isinstance(value.get('issues'), dict):
        raise ValueError('invalid state structure')
    return value


def public_coverage(raw):
    """Count-only projection; no arbitrary paths, identifiers or nested state."""
    if not isinstance(raw, dict): raise ValueError('invalid input coverage')
    result = {key: None for key in ('valid_link_slots', 'fixed_link_slots', 'eligible_links', 'context_links')}
    for key in ('valid_link_slots', 'fixed_link_slots'):
        if key in raw:
            if not count(raw[key]): raise ValueError('invalid coverage count')
            result[key] = raw[key]
    if (result['valid_link_slots'] is not None and result['fixed_link_slots'] is not None
            and result['valid_link_slots'] > result['fixed_link_slots']):
        raise ValueError('invalid coverage ratio')
    result['links'] = []
    if 'link_gates' in raw:
        gates = raw['link_gates']
        if not isinstance(gates, list) or len(gates) > 10: raise ValueError('invalid link coverage')
        for gate in gates:
            if not isinstance(gate, dict) or not isinstance(gate.get('eligible'), bool):
                raise ValueError('invalid link gate')
            paired, positive, age = (gate.get(k) for k in ('valid_paired_slots', 'positive_slots', 'latest_interval_end_age_seconds'))
            if not count(paired) or paired > 13 or not isinstance(positive, dict):
                raise ValueError('invalid paired coverage')
            if any(not count(positive.get(k)) or positive[k] > 13 for k in ('M04A', 'M05A')):
                raise ValueError('invalid positive coverage')
            if age is not None and (not finite(age) or age < 0): raise ValueError('invalid source age')
            result['links'].append({'valid_paired_slots': paired,
                'positive_slots': {k: positive[k] for k in ('M04A', 'M05A')},
                'latest_interval_end_age_seconds': age, 'eligible': gate['eligible'],
                'reason_codes': codes(gate.get('reasons', []))})
        result.update(context_links=len(gates), eligible_links=sum(g['eligible'] for g in gates))
    return result


def public_sources(inputs):
    """Dataset-level ranges of clocks stored in the verified issue, not live reads."""
    if not isinstance(inputs, list) or len(inputs) > 600: raise ValueError('invalid source inputs')
    grouped = {}
    for source in inputs:
        if not isinstance(source, dict): raise ValueError('invalid source input')
        dataset = source.get('dataset', 'unknown')
        if dataset not in ('M03A', 'M04A', 'M05A', 'unknown'): raise ValueError('unknown dataset')
        group = grouped.setdefault(dataset, {'receipts': 0, 'source_epoch': [],
            'full_body_received_epoch': [], 'parse_finished_epoch': []})
        group['receipts'] += 1
        for key in ('source_epoch', 'full_body_received_epoch', 'parse_finished_epoch'):
            if key in source:
                if not finite(source[key]): raise ValueError('invalid source clock')
                group[key].append(source[key])
    result = []
    for dataset, group in sorted(grouped.items()):
        item = {'dataset': dataset, 'receipts': group['receipts']}
        for key, name in (('source_epoch', 'source_time'), ('full_body_received_epoch', 'received_at'),
                          ('parse_finished_epoch', 'parsed_at')):
            values = group[key]
            item['earliest_'+name] = canonical.iso(min(values)) if values else None
            item['latest_'+name] = canonical.iso(max(values)) if values else None
        result.append(item)
    return result


def blank_slot(day, nominal, now, status):
    I = canonical.local_epoch(day, nominal-45)
    text = lambda minute: f'{minute//60:02d}:{minute%60:02d}'
    return {'issue_id': canonical.issue_identity(day, nominal), 'day': day,
            'nominal': text(nominal), 'deadline': text(nominal+45), 'cost': .5,
            'issue_time': canonical.iso(I), 'as_of': canonical.iso(now), 'status': status,
            'sealed_at': None, 'finished_at': None, 'candidates': [],
            'input_coverage': None, 'source_inputs': [], 'reason_codes': []}


def public_slot(raw, base):
    if not isinstance(raw, dict): raise ValueError('invalid public issue')
    for key in ('issue_id', 'day', 'nominal', 'deadline', 'cost', 'issue_time', 'as_of'):
        if raw.get(key) != base[key]: raise ValueError('public issue identity mismatch')
    if raw.get('policy_qualified') is not False or raw.get('truth_loaded') is not False:
        raise ValueError('unqualified no-truth contract')
    if raw.get('per_request_model_forward') is not False: raise ValueError('no-forward contract')
    status = raw.get('status')
    # Runtime statuses are not evidence of a successful publication.
    base['status'] = status if status in STATUSES or (isinstance(status, str) and
        re.fullmatch(r'unresolved_[a-z_]{1,64}', status)) else 'unknown'
    if 'input_coverage' in raw: base['input_coverage'] = public_coverage(raw['input_coverage'])
    if 'source_inputs' in raw: base['source_inputs'] = public_sources(raw['source_inputs'])
    if status == 'sealed_shadow_only':
        base['sealed_at'] = clock_text(raw['sealed_at'])
        base['finished_at'] = clock_text(raw['finished_at'])
        rows = raw.get('candidates')
        if not isinstance(rows, list) or len(rows) != 5: raise ValueError('five candidate rows required')
        for shift, row in zip((-30, -15, 0, 15, 30), rows):
            if not isinstance(row, dict) or row.get('shift_minutes') != shift: raise ValueError('candidate identity')
            prediction = row.get('prediction')
            if prediction is not None:
                if not isinstance(prediction, dict) or set(prediction) != {'Tq50', 'Tq90', 'Aq50', 'Aq90'}:
                    raise ValueError('four heads required')
                if any(not finite(x) for x in prediction.values()): raise ValueError('nonfinite head')
            projected = {'shift_minutes': shift, 'window_start': clock_text(row['window_start']),
                'window_end': clock_text(row['window_end']), 'prediction': prediction,
                'window_elapsed': row['window_elapsed']}
            if not isinstance(projected['window_elapsed'], bool): raise ValueError('invalid elapsed flag')
            for key in ('predicted_roadgain', 'shift_cost', 'predicted_netgain'):
                value = row.get(key)
                if value is not None and not finite(value): raise ValueError('nonfinite comparison')
                projected[key] = value
            flag = row.get('estimated_q90_within_deadline')
            if flag is not None and not isinstance(flag, bool): raise ValueError('invalid deadline flag')
            projected['estimated_q90_within_deadline'] = flag
            base['candidates'].append(projected)
    return base


class Viewer:
    def __init__(self, report=REPORT, clock=time.time):
        self.report, self.clock = Path(report), clock

    def day(self, day):
        now = self.clock()
        if not finite(now): raise ValueError('invalid render clock')
        result = {'schema_version': 1, 'mode': 'verified_sealed_results', 'day': day,
            'as_of': canonical.iso(now), 'allowed_days': list(DAYS), 'status': 'available',
            'fixed_scheduled_issues': 12, 'verified_count': 0, 'slots': [],
            'scope': {'timezone': 'Asia/Taipei', 'vehicle_class': '31',
                'gates': ['75', '88', '92.8', '95', '98'], 'deadline_offset_minutes': 45, 'cost': .5},
            'forecast_display_method': 'empirical', 'policy_qualified': False,
            'policy_selected_method': None, 'recommendation': None, 'truth_loaded': False,
            'viewer_operations': {'source_requests': 0, 'fits': 0, 'model_forwards': 0}}
        if day not in DAYS:
            result.update(status='not_scheduled', fixed_scheduled_issues=0, verified_count=None)
            return result
        slots = [blank_slot(day, n, now, 'not_due' if now < canonical.local_epoch(day,n-45)
                            else 'not_published') for n in NOMINALS]
        result['slots'] = slots
        try:
            state = read_state(self.report/'state.json')
        except (OSError, ValueError, TypeError):
            result.update(status='unavailable', verified_count=None, reason_codes=['state_unavailable'])
            for slot in slots: slot.update(status='unavailable', reason_codes=['state_unavailable'])
            return result
        app = connected.Application(self.report, clock=lambda: now, frozen_state=state)
        for index, slot in enumerate(slots):
            query = {k: [str(slot[k])] for k in ('day', 'nominal', 'deadline', 'cost')}
            try:
                # Calls the existing inherited verifier. No current(), status(),
                # historical repository or prospective evaluation entry is called.
                slots[index] = public_slot(app.prospective(query), dict(slot, candidates=[]))
            except (OSError, ValueError, KeyError, TypeError, IndexError, canonical.ApiUnavailable):
                slots[index] = dict(slot, status='integrity_error', candidates=[],
                                    reason_codes=['unverified_issue_artifact'])
        result['verified_count'] = sum(s['status'] == 'sealed_shadow_only' for s in slots)
        if any(s['status'] == 'integrity_error' for s in slots): result['status'] = 'partial_integrity_error'
        return result


def same_origin(host, origin):
    if host not in ('127.0.0.1:8768', 'localhost:8768'): return False
    return origin is None or origin == 'http://'+host


def dispatch(target, viewer):
    """Exact route/query allowlist, shared by HTTP and synthetic route tests."""
    if len(target) > 1024: return 400, 'text/plain', b'Invalid request'
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment: return 400, 'text/plain', b'Invalid request'
    try: query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
    except ValueError: return 400, 'text/plain', b'Too many query fields'
    if parsed.path == '/api/day':
        if set(query) != {'day'} or len(query['day']) != 1: return 400, 'text/plain', b'One day required'
        day = query['day'][0]
        try:
            if dt.date.fromisoformat(day).isoformat() != day: raise ValueError('noncanonical date')
        except ValueError: return 400, 'text/plain', b'Invalid day'
        value = viewer.day(day)
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > OUTPUT_CAP: return 503, 'text/plain', b'Response unavailable'
        return (503 if value['status'] == 'unavailable' else 200), 'application/json', data
    if parsed.path in STATIC:
        if query and not (parsed.path in ('/', '/index.html') and query in ({'lang':['zh']}, {'lang':['en']})):
            return 400, 'text/plain', b'Invalid static query'
        path = PROTOTYPE/STATIC[parsed.path]
        return 200, {'.html':'text/html', '.js':'text/javascript', '.css':'text/css'}[path.suffix], path.read_bytes()
    return 404, 'text/plain', b'Not found'


class Handler(BaseHTTPRequestHandler):
    application = None
    def do_GET(self):
        if (len(self.headers.get_all('Host', [])) != 1 or len(self.headers.get_all('Origin', [])) > 1
                or not same_origin(self.headers.get('Host'), self.headers.get('Origin'))
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            self.respond(403, 'text/plain', b'Same-origin loopback only'); return
        try: status, mime, data = dispatch(self.path, self.application)
        except (OSError, ValueError, TypeError): status, mime, data = 503, 'text/plain', b'Unavailable'
        self.respond(status, mime, data)
    def do_POST(self): self.respond(405, 'text/plain', b'Read-only GET only')
    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST
    def respond(self, status, mime, data):
        self.send_response(status)
        for key, value in {'Content-Type': mime+'; charset=utf-8', 'Content-Length': str(len(data)),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            'Referrer-Policy': 'no-referrer', 'Connection': 'close'}.items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *ignored): pass


class BoundedServer(HTTPServer):
    allow_reuse_address = False
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=int, default=3600)
    args = parser.parse_args(argv)
    if not 1 <= args.duration_seconds <= 3600: parser.error('duration must be 1..3600 seconds')
    return args


def main(argv=None):
    args = arguments(argv)
    Handler.application = Viewer()
    stopped = False
    def stop(*ignored):
        nonlocal stopped
        stopped = True
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with BoundedServer(('127.0.0.1', 8768), Handler) as server:
            end = time.monotonic()+args.duration_seconds
            while not stopped and time.monotonic() < end:
                server.timeout = min(.5, max(0, end-time.monotonic()))
                server.handle_request()
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


if __name__ == '__main__': main()
