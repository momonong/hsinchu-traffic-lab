"""One bounded TDCS collector; immutable raw/receipts, no forecasts or fits."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import resource
import shutil
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid

from .parser import DATASETS, TZ, parse_csv

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'reports/departure-advisor-2026-10-20'
RAW = ROOT / 'data/raw/departure-advisor-2026-10-20'


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()


def read(path):
    return json.loads(path.read_text())


def next_schedule(now, offset=240):
    """Strictly future 5min boundary+4min; midnight uses the same UTC+8 grid."""
    if not math.isfinite(now): raise ValueError('invalid schedule clock')
    if offset not in (240,540):raise ValueError('unadmitted schedule offset')
    return (math.floor((now - offset) / 300) + 1) * 300 + offset


def source_url(dataset, source_epoch):
    if dataset not in DATASETS or source_epoch % 300:
        raise ValueError('unadmitted dataset/slot')
    local = dt.datetime.fromtimestamp(source_epoch, TZ)
    day, hour, stamp = local.strftime('%Y%m%d'), local.strftime('%H'), local.strftime('%H%M%S')
    return f'https://tisvcloud.freeway.gov.tw/history/TDCS/{dataset}/{day}/{hour}/TDCS_{dataset}_{day}_{stamp}.csv'


def identity():
    fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': os.getpid(), 'start_ticks': fields[19],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'instance_id': str(uuid.uuid4()), 'hostname': socket.gethostname(),
            'argv': sys.argv, 'executable': sys.executable,
            'collector_sha256': digest(Path(__file__).read_bytes()),
            'parser_sha256': digest(Path(__file__).with_name('parser.py').read_bytes())}


class StopRun(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Collector:
    def __init__(self, report=REPORT, raw=RAW, clock=time.time, sleeper=time.sleep, opener=None):
        self.report, self.raw = Path(report), Path(raw)
        self.clock, self.sleeper = clock, sleeper
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.lock = None; self.stop_requested = False; self.state = None
        self.program_path = self.report/'program.json'
        self.contract_path = self.report/'source-contract.json'
        self.state_path = self.report/'collector-state.json'
        self.spec = read(self.program_path); self.contract = read(self.contract_path)
        self.program_hash = digest(self.program_path.read_bytes())
        self.contract_hash = digest(self.contract_path.read_bytes())
        self.amendment_path=self.report/'source-schedule-amendment.json'
        self.amendment_hash=digest(self.amendment_path.read_bytes()) if self.amendment_path.exists() else None
        self.schedule={'revision':'source-contract-v1','sha256':self.contract_hash,
            'fetch_offset_seconds':self.contract['fetch_offset_seconds'],'not_before_source_epoch':None}
        if self.amendment_hash:
            amendment=read(self.amendment_path)
            if amendment['program_sha256']!=self.program_hash or amendment['base_source_contract_sha256']!=self.contract_hash or amendment['fetch_offset_seconds']!=540:
                raise ValueError('schedule amendment binding mismatch')
            self.schedule={'revision':amendment['revision'],'sha256':self.amendment_hash,
                'fetch_offset_seconds':540,'not_before_source_epoch':amendment['not_before_source_epoch']}
        self.limits = self.spec['limits']; self.process = None
        if self.contract['program_sha256'] != self.program_hash:
            raise ValueError('source contract program mismatch')
        if tuple(self.contract['sources']) != DATASETS:
            raise ValueError('source allowlist changed')
        if self.contract['HTTP_response_cap_bytes'] > self.limits['HTTP_body_bytes_per_response']:
            raise ValueError('source body cap exceeds program')
        if self.contract['stop_new_source_epoch'] != self.spec['new_work_stop_epoch']:
            raise ValueError('source stop mismatch')

    def disk_bytes(self):
        paths = [self.report, self.raw]
        if self.report == REPORT:
            paths += [ROOT/'scripts/departure_advisor',
                      ROOT/'prototype/departure-advisor-2026-10-20',
                      ROOT/'data/processed/departure-advisor-2026-10-20']
            paths += list((ROOT/'tests').glob('test_departure_advisor*.py'))
        files = {p.resolve() for top in paths for p in
                 ([top] if top.is_file() else top.rglob('*')) if p.is_file()}
        return sum(p.stat().st_size for p in files)

    def write(self, path, body, exclusive=False):
        path = Path(path)
        if self.disk_bytes()+len(body) > self.limits['new_disk_bytes']:
            raise StopRun('new_disk_limit')
        if shutil.disk_usage(self.report).free-len(body) < self.limits['free_disk_reserve_bytes']:
            raise StopRun('free_disk_reserve')
        path.parent.mkdir(parents=True, exist_ok=True)
        target = path if exclusive else path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        with target.open('xb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        if not exclusive: os.replace(target, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def persist(self):
        self.state['updated_at'] = iso(self.clock())
        self.state['peak_RSS_KiB'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.write(self.state_path, encode(self.state))

    def next_eligible_due(self, now):
        offset=self.schedule['fetch_offset_seconds'];due=next_schedule(now,offset)
        while True:
            stamp=due-offset;minimum=self.schedule['not_before_source_epoch']
            attempted=self.state['attempted_keys'] if self.state else []
            if (minimum is None or stamp>=minimum) and not any(f'{dataset}:{int(stamp)}'in attempted for dataset in DATASETS):return due
            due+=300

    def acquire(self):
        self.lock = (self.report/'collector.lock').open('a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock.close(); self.lock = None
            raise AlreadyRunning('collector already owns lock') from error
        try:
            # State is loaded only after ownership; a prior owner may have advanced it.
            if digest(self.program_path.read_bytes()) != self.program_hash or digest(self.contract_path.read_bytes()) != self.contract_hash:
                raise ValueError('contract drift before lock')
            current_amendment=digest(self.amendment_path.read_bytes())if self.amendment_path.exists()else None
            if current_amendment!=self.amendment_hash:raise ValueError('schedule drift before lock')
            if self.state_path.exists():
                state = read(self.state_path)
                if state['program_sha256'] != self.program_hash or state['source_contract_sha256'] != self.contract_hash:
                    raise ValueError('resume contract mismatch')
            else:
                evidence = [self.report/'collector-receipts', self.raw]
                if (self.report/'collector-process.json').exists() or any(p.is_file() for top in evidence for p in top.rglob('*')):
                    raise ValueError('missing state with prior collector evidence; refuse budget reset')
                probe = self.contract['root_probe']; receipt_path = ROOT/probe['receipt']
                if self.report != REPORT:
                    receipt_path = self.report/'source-admission/receipt.json'
                if digest(receipt_path.read_bytes()) != probe['receipt_sha256']:
                    raise ValueError('probe receipt mismatch')
                receipt = read(receipt_path)
                if receipt['total_requests'] != probe['requests'] or receipt['total_body_bytes'] != probe['body_bytes']:
                    raise ValueError('probe counters mismatch')
                state = {'schema_version': 1, 'program_sha256': self.program_hash,
                    'source_contract_sha256': self.contract_hash, 'T0_epoch': self.spec['T0_epoch'],
                    'deadline_epoch': self.spec['deadline_epoch'], 'status': 'initialized',
                    'HTTP_requests': probe['requests'], 'HTTP_body_bytes_received': probe['body_bytes'],
                    'HTTP_body_bytes_charged': probe['body_bytes'], 'root_probe_imported_once': True,
                    'root_probe_excluded_from_live_history': True,
                    'sources': {s: {'status': 'admitted_collecting', 'successes': 0, 'last_manifest': None} for s in DATASETS},
                    'next_due_epoch': self.next_eligible_due(self.clock()), 'attempts': [], 'published': [],
                    'attempted_keys': [], 'skipped_schedules': [], 'process_history': [],
                    'last_request_finished_epoch': max(dt.datetime.fromisoformat(x['received_utc']).timestamp() for x in receipt['items']),
                    'new_fits': 0, 'GPU_used': False}
            self.state = state
            probe = self.contract['root_probe']
            if state['T0_epoch']!=self.spec['T0_epoch'] or state['deadline_epoch']!=self.spec['deadline_epoch']:
                raise ValueError('resume clock/deadline drift')
            if state['HTTP_requests']!=probe['requests']+len(state['attempts']):
                raise ValueError('request counter/attempt ledger mismatch')
            if state['HTTP_body_bytes_charged']<state['HTTP_body_bytes_received'] or state['HTTP_body_bytes_received']<probe['body_bytes']:
                raise ValueError('body counter rollback')
            ids=[a['id']for a in state['attempts']]
            if ids!=list(range(probe['requests']+1,state['HTTP_requests']+1)):
                raise ValueError('attempt identity sequence mismatch')
            if len(set(state['attempted_keys']))!=len(state['attempted_keys']):
                raise ValueError('duplicate attempted key')
        except BaseException:
            self.release(); raise

    def release(self):
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN); self.lock.close(); self.lock = None

    def check(self, request=False):
        now = self.clock()
        if self.stop_requested or (self.report/'STOP').exists() or (self.report/'STOP-collector').exists():
            raise StopRun('stop_requested')
        if now >= self.spec['deadline_epoch']: raise StopRun('hard_deadline')
        if now >= self.contract['stop_new_source_epoch']: raise StopRun('final_hour_consolidation')
        if now < self.spec['T0_epoch'] - 1: raise StopRun('clock_before_T0')
        if digest(self.program_path.read_bytes()) != self.program_hash or digest(self.contract_path.read_bytes()) != self.contract_hash:
            raise StopRun('contract_changed')
        current_amendment=digest(self.amendment_path.read_bytes())if self.amendment_path.exists()else None
        if current_amendment!=self.amendment_hash:raise StopRun('schedule_contract_changed')
        if self.disk_bytes()+1000000 >= self.limits['new_disk_bytes']: raise StopRun('new_disk_reserve')
        if shutil.disk_usage(self.report).free < self.limits['free_disk_reserve_bytes']+1000000:
            raise StopRun('free_disk_reserve')
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > self.limits['research_RSS_bytes']:
            raise StopRun('RSS_limit')
        if request:
            if self.state['HTTP_requests'] >= self.limits['HTTP_requests_total']: raise StopRun('HTTP_request_limit')
            if self.state['HTTP_body_bytes_charged']+self.contract['HTTP_response_cap_bytes'] > self.limits['HTTP_body_bytes_total']:
                raise StopRun('HTTP_body_limit')

    def wait_until(self, end):
        while self.clock() < end:
            self.check()
            remaining = end-self.clock()
            if remaining <= 0: break
            self.sleeper(min(5, remaining))
        self.check()

    def recover(self):
        published = {p['attempt_id'] for p in self.state['published']}
        for attempt in self.state['attempts']:
            if attempt['status'] == 'inflight_reserved':
                attempt.update(status='abandoned_after_restart_reservation_retained',
                               recovery_observed_at=iso(self.clock()),
                               continuity_break=True, body_availability_claimed=False)
            elif attempt['id'] not in published:
                attempt.update(status='unpublished_after_restart', recovery_observed_at=iso(self.clock()),
                               continuity_break=True, body_availability_claimed=False)
        previous=self.state.get('active_schedule')
        changed=previous!=self.schedule
        if changed:
            self.state.setdefault('schedule_history',[]).append({'previous':previous,'new':self.schedule,
                'applied_at':iso(self.clock()),'old_next_due_epoch':self.state['next_due_epoch']})
            self.state['active_schedule']=self.schedule
        offset=self.schedule['fetch_offset_seconds'];stamp=self.state['next_due_epoch']-offset
        invalid_stamp=(self.schedule['not_before_source_epoch'] is not None and stamp<self.schedule['not_before_source_epoch'])or any(f'{s}:{int(stamp)}'in self.state['attempted_keys']for s in DATASETS)
        if changed or invalid_stamp or self.state['next_due_epoch'] <= self.clock():
            prior = self.state['next_due_epoch']; future = self.next_eligible_due(self.clock())
            self.state['skipped_schedules'].append({'from_due_epoch': prior, 'before_due_epoch': future,
                'reason': 'schedule_revision_or_resume_no_backfill', 'recorded_at': iso(self.clock()),'schedule':self.schedule})
            self.state['next_due_epoch'] = future
        self.persist()

    def fetch(self, dataset, due):
        if not self.lock: raise RuntimeError('fetch requires collector lock')
        if self.state['sources'][dataset]['status'] != 'admitted_collecting': return
        source_epoch = due-self.schedule['fetch_offset_seconds']; key = f'{dataset}:{int(source_epoch)}'
        minimum=self.schedule['not_before_source_epoch']
        if minimum is not None and source_epoch<minimum:raise ValueError('source timestamp before schedule amendment admission')
        if key in self.state['attempted_keys']: raise ValueError('same dataset/timestamp already attempted; no retry')
        if self.clock() < due or self.clock() >= due+300: raise ValueError('outside current scheduled cycle')
        prior = self.state.get('last_request_finished_epoch')
        if prior is not None: self.wait_until(max(self.clock(), prior+self.contract['minimum_seconds_between_requests']))
        self.check(request=True)
        if self.clock() >= due+300: raise StopRun('cycle_expired_before_request')
        cap = self.contract['HTTP_response_cap_bytes']; index = self.state['HTTP_requests']+1
        attempt = {'id': index, 'dataset': dataset, 'key': key, 'status': 'inflight_reserved',
                   'source_epoch': source_epoch, 'source_timestamp': dt.datetime.fromtimestamp(source_epoch,TZ).isoformat(),
                   'interval_start': iso(source_epoch-300), 'interval_end': iso(source_epoch),
                   'scheduled_due_epoch': due, 'request_started_at': iso(self.clock()),
                   'active_schedule':self.schedule.copy(),
                   'request_started_epoch': self.clock(), 'URL': source_url(dataset, source_epoch),
                   'body_reserved_bytes': cap, 'body_bytes_received': 0, 'process_instance': self.process,
                   'version': 'first_observed_not_guaranteed_original_publication',
                   'version_reason':'This run first observes this scheduled current-day URL;no original-publication guarantee or later-version overwrite.'}
        self.state['attempted_keys'].append(key); self.state['attempts'].append(attempt)
        self.state['HTTP_requests'] += 1; self.state['HTTP_body_bytes_charged'] += cap
        self.persist()  # Durable reservation precedes all network activity.
        body = bytearray(); complete = False; response = None; parsed = None
        try:
            request = urllib.request.Request(attempt['URL'], headers={'User-Agent':'departure-advisor-bounded-research/1.0','Accept':'text/csv'}, method='GET')
            try:
                response = self.opener.open(request, timeout=self.contract['HTTP_timeout_seconds'])
            except urllib.error.HTTPError as error:
                response = error
            attempt['HTTP_status'] = int(response.code)
            attempt['headers'] = {k: response.headers.get(k) for k in ['Date','Last-Modified','Content-Type','Content-Length']}
            length_text = response.headers.get('Content-Length')
            declared = int(length_text) if length_text and length_text.isdigit() else None
            while len(body) < cap:
                self.check()
                try:
                    chunk = response.read(min(65536, cap-len(body)))
                except http.client.IncompleteRead as error:
                    partial=error.partial[:cap-len(body)]
                    if partial and not body:attempt['first_body_received_at']=iso(self.clock())
                    body.extend(partial);attempt['read_error']='IncompleteRead';break
                if not chunk:
                    complete = True; break
                if not body: attempt['first_body_received_at'] = iso(self.clock())
                body.extend(chunk)
            if len(body) == cap and declared == cap: complete = True
            if declared is not None and declared != len(body): complete = False
            attempt['body_complete'] = complete
            if not complete:
                attempt['status'] = 'body_incomplete_or_limit'; attempt['continuity_break'] = True
            else:
                attempt['full_body_received_at'] = iso(self.clock())
                attempt['full_body_received_epoch'] = self.clock()
                if attempt['HTTP_status'] == 200:
                    try:
                        parsed = parse_csv(bytes(body), dataset, source_epoch, attempt['full_body_received_epoch'])
                        attempt['status'] = 'received_parsed' if not parsed['file_errors'] else 'received_quality_flagged'
                    except (ValueError, UnicodeError, csv.Error) as error:
                        attempt['status'] = 'parse_rejected'; attempt['parse_error'] = str(error)
                    attempt['parse_finished_at'] = iso(self.clock())
                elif attempt['HTTP_status'] == 404: attempt['status'] = 'missing_404_no_retry'
                else: attempt['status'] = 'HTTP_error_no_retry'
        except StopRun as error:
            attempt.update(status='interrupted_request', error=str(error), continuity_break=True)
            raise
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            attempt.update(status='network_error_no_retry', error=f'{type(error).__name__}: {error}', continuity_break=True)
        finally:
            if response is not None: response.close()
            attempt['request_finished_at'] = iso(self.clock())
            attempt['request_finished_epoch'] = self.clock()
            attempt['available_at']=attempt['request_finished_at']if complete and parsed is not None else None
            attempt['body_bytes_received'] = len(body)
            self.state['HTTP_body_bytes_received'] += len(body)
            if complete: self.state['HTTP_body_bytes_charged'] -= cap-len(body)
            else: attempt['unresolved_reservation_retained'] = True
            self.state['last_request_finished_epoch'] = self.clock()
            if attempt.get('HTTP_status') in self.contract['stop_source_statuses']:
                self.state['sources'][dataset].update(status='stopped_source_denial', stop_status=attempt['HTTP_status'])
            self.persist()  # Accounting is durable even when artifact publication fails.
            self.publish(attempt, bytes(body), parsed)

    def publish(self, attempt, body, parsed):
        index = attempt['id']; stem = f'attempt-{index:06d}'; base = self.report/'collector-receipts'
        if body:
            raw_path = self.raw/attempt['dataset']/(stem+'.csv' if attempt.get('HTTP_status')==200 and attempt.get('body_complete') else stem+'.partial')
            self.write(raw_path, body, exclusive=True)
            attempt['raw_artifact'] = {'path':str(raw_path),'bytes':len(body),'sha256':digest(body)}
        attempt['payload_sha256'] = digest(body)
        if parsed is not None:
            parsed_path = base/(stem+'-parsed.json'); parsed_bytes = encode(parsed)
            self.write(parsed_path, parsed_bytes, exclusive=True)
            attempt['parsed_artifact'] = {'path':str(parsed_path),'sha256':digest(parsed_bytes)}
        receipt_path = base/(stem+'.json'); receipt_bytes = encode(attempt)
        self.write(receipt_path, receipt_bytes, exclusive=True)
        publication = {'attempt_id':index, 'dataset':attempt['dataset'], 'key':attempt['key'],
            'receipt_path':str(receipt_path),'receipt_sha256':digest(receipt_bytes),
            'payload_sha256':attempt['payload_sha256'],'published_at':iso(self.clock()),
            'available_epoch':self.clock()if attempt.get('available_at')else None,
            'available_at':attempt.get('available_at'),'active_schedule':self.schedule.copy(),
            'full_body_received_at':attempt.get('full_body_received_at'),
            'status':attempt['status'], 'continuity_break':attempt['status']!='received_parsed'}
        self.state['published'].append(publication)
        if attempt['status']=='received_parsed': self.state['sources'][attempt['dataset']]['successes'] += 1
        self.state['sources'][attempt['dataset']]['last_manifest'] = publication
        self.persist()

    def run(self, cycles=None):
        self.acquire()
        try:
            self.recover(); self.check()
            self.process = {**identity(), 'started_at':iso(self.clock())}
            self.state['process_history'].append(self.process); self.state['process'] = self.process
            self.state['status'] = 'collector_running'; self.persist()
            self.write(self.report/'collector-process.json', encode({'status':'collector_running','process':self.process}))
            completed = 0
            while True:
                self.check()
                if all(s['status']!='admitted_collecting' for s in self.state['sources'].values()):
                    raise StopRun('all_sources_stopped')
                due = self.state['next_due_epoch']; self.wait_until(due)
                for dataset in DATASETS:
                    self.check()
                    if self.clock() >= due+300: break
                    self.fetch(dataset,due)
                self.state['next_due_epoch'] = max(due+300,self.next_eligible_due(self.clock()))
                self.persist(); completed += 1
                if cycles is not None and completed >= cycles: raise StopRun('bounded_test_cycles_complete')
        except StopRun as error:
            self.state.update(status='collector_stopped',stop_reason=str(error),stopped_at=iso(self.clock()))
            self.persist()
            self.write(self.report/'collector-process.json',encode({'status':'collector_stopped','process':self.process,
                'stop_reason':str(error),'stopped_at':self.state['stopped_at']}))
        except BaseException as error:
            if self.state is not None:
                self.state.update(status='collector_failed',error=f'{type(error).__name__}: {error}',failed_at=iso(self.clock()))
                self.persist()
                self.write(self.report/'collector-process.json',encode({'status':'collector_failed','process':self.process,
                    'error':self.state['error'],'failed_at':self.state['failed_at']}))
            raise
        finally:
            self.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles',type=int,help='Optional bounded smoke cycles;real sampling cadence is unchanged')
    args = parser.parse_args()
    if args.cycles is not None and args.cycles < 1: parser.error('cycles must be positive')
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name] = '1'  # Reserve the other three stage threads for research.
    collector = Collector()
    def stop(signum,frame): collector.stop_requested = True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    collector.run(cycles=args.cycles)


if __name__ == '__main__': main()
