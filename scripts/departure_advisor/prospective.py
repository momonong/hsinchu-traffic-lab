"""Admitted, bounded morning sessions; immutable real CPU forecasts, no refits.

Root owns host execution/timers. Merely importing or validating this module
never contacts a source or starts a service. Separate truth sessions never load
the forecast models, and cannot issue forecasts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

from .collector import ROOT
from .parser import DATASETS, TZ, parse_csv
from .prospective_features import ALLOWED_DAYS, build_live_features, issue_slots, local_epoch, source_slots
from .prospective_store import BudgetStop, Store, atomic_json, digest, fsync_directory

REPORT = ROOT / 'reports/prospective-departure-2026-10-20'
OUTPUT = ROOT / 'outputs/prospective-departure-2026-10-20'
CODE_FILES = ('prospective.py', 'prospective_features.py', 'prospective_store.py',
              'prospective_inference.py', 'prospective_truth.py', 'prospective_evaluation.py')


def read(path): return json.loads(Path(path).read_text())
def iso(epoch): return dt.datetime.fromtimestamp(epoch, TZ).isoformat()
def code_hashes(): return {str((ROOT / 'scripts/departure_advisor' / name).relative_to(ROOT)):
                           digest(ROOT / 'scripts/departure_advisor' / name) for name in CODE_FILES}
def record(path): return {'path': str(Path(path).relative_to(ROOT)), 'sha256': digest(path), 'bytes': Path(path).stat().st_size}


def process_identity():
    stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': os.getpid(), 'start_ticks': stat[19],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'instance_id': str(uuid.uuid4()), 'code': code_hashes()}


def validate_admission(path, mode):
    program, protocol = read(REPORT / 'program.json'), read(REPORT / 'protocol.json')
    admission = read(path)
    actual = {'program_sha256': digest(REPORT / 'program.json'),
              'protocol_sha256': digest(REPORT / 'protocol.json'),
              'input_manifest_sha256': digest(REPORT / 'input-manifest.json')}
    if any(admission.get(key) != value for key, value in actual.items()):
        raise BudgetStop('root admission contract hash mismatch')
    if admission.get('code') != code_hashes() or mode not in admission.get('allowed_modes', []):
        raise BudgetStop('root admission code/mode mismatch')
    if admission.get('status') != 'root_approved_bounded_execution':
        raise BudgetStop('root execution approval missing')
    if time.time() >= program['prospective_final_deadline_epoch']:
        raise BudgetStop('expired prospective program')
    from .data import verify_record
    for item in read(REPORT / 'input-manifest.json')['files']: verify_record(item, ROOT)
    return program, protocol, actual


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class Supervisor:
    def __init__(self, store, protocol, *, clock=time.time, sleeper=time.sleep, transport=None):
        self.store, self.protocol, self.clock, self.sleeper = store, protocol, clock, sleeper
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open
        self.identity = process_identity()
        self.phase_deadline = None; self.stopping = False; self.predictor = None

    def check(self, additional_disk=0):
        if self.stopping: raise BudgetStop('owned process stop requested')
        self.store.check(phase_deadline=self.phase_deadline, additional_disk=additional_disk)

    def wait_until(self, target):
        while self.clock() < target:
            self.check()
            remaining = target - self.clock()
            if remaining <= 0: return
            self.sleeper(min(5, remaining))

    def request(self, key, dataset, url, cap, *, day, source_epoch=None, source_hour=None):
        self.check()
        parsed_url = urllib.parse.urlsplit(url)
        if (parsed_url.scheme != 'https' or parsed_url.netloc != 'tisvcloud.freeway.gov.tw'
                or parsed_url.query or parsed_url.fragment or not parsed_url.path.startswith('/history/TDCS/' + dataset + '/')):
            raise BudgetStop('unadmitted source URL')
        last = self.store.state['last_request_epoch']
        if last is not None: self.wait_until(last + 1)
        self.check(additional_disk=cap + 100000)
        attempt = self.store.reserve(key, dataset, cap=cap, day=day)
        attempt.update(url=url, source_epoch=source_epoch, source_hour=source_hour, process_pid=self.identity['pid'])
        attempt['version_role'] = ('single_conservative_Dplus6_not_official_completion_SLA' if dataset == 'M06A'
                                   else 'first_observed_live_not_guaranteed_first_publication')
        if source_epoch is not None:
            attempt.update(source_interval_start_epoch=source_epoch-300, source_interval_end_epoch=source_epoch)
        self.store.persist()
        number = attempt['sequence']; folder = OUTPUT / 'raw'; folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f'attempt-{number:06d}.csv.gz.part'
        final = folder / f'attempt-{number:06d}.csv.gz'
        got, body_hash, complete = 0, hashlib.sha256(), False
        fields, response_status = {}, None
        timer_old = None
        def request_expired(*ignored): raise TimeoutError('absolute20second HTTP request/body deadline')
        try:
            # This Linux session runs on its main thread; body reads have a total
            # deadline, rather than restarting a socket timeout for every chunk.
            timer_old = signal.signal(signal.SIGALRM, request_expired)
            signal.setitimer(signal.ITIMER_REAL, min(20, max(.001, self.phase_deadline - self.clock())))
            request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'hsinchu-prospective/1'}, method='GET')
            try: response = self.transport(request, timeout=20)
            except urllib.error.HTTPError as error: response = error
            with response:
                response_status = response.status
                fields.update(HTTP_status=response_status, HTTP_Date=response.headers.get('Date'),
                              Last_Modified=response.headers.get('Last-Modified'))
                if response_status in (401, 403, 429):
                    self.store.state['stopped_sources'][dataset] = dict(status=response_status, epoch=self.clock())
                    self.store.persist()
                declared = response.headers.get('Content-Length')
                declared = int(declared) if declared is not None else None
                if declared is not None and (declared < 0 or declared > cap):
                    raise BudgetStop('declared body exceeds current single/day/global reservation')
                if response.headers.get('Content-Encoding') not in (None, '', 'identity'):
                    raise BudgetStop('unexpected HTTP content encoding')
                with partial.open('xb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
                        while got < cap:
                            self.check()
                            block = response.read(min(65536, cap - got))
                            if not block:
                                complete = True; fields['full_body_received_epoch'] = self.clock(); break
                            if not got: fields['first_body_received_epoch'] = self.clock()
                            body_hash.update(block); compressed.write(block); got += len(block)
                            if declared is not None and got == declared:
                                complete = True; fields['full_body_received_epoch'] = self.clock(); break
                        if not complete: raise BudgetStop('body cap reached without proven complete response')
                    raw.flush(); os.fsync(raw.fileno())
                if declared is not None and got != declared: raise ValueError('truncated body')
                os.link(partial, final); partial.unlink(); fsync_directory(final.parent)
                fields.update(payload_sha256=body_hash.hexdigest(), raw=record(final))
            status = 'received_body' if response_status == 200 else ('missing_404_no_retry' if response_status == 404 else 'HTTP_rejected_no_retry')
            if dataset != 'M06A' and response_status == 200:
                with gzip.open(final, 'rb') as stream: body = stream.read()
                parsed = parse_csv(body, dataset, source_epoch, fields['full_body_received_epoch'])
                # Save only bounded research context; raw national payload remains
                # immutable and hashed. Never parse all previous files again.
                parsed.pop('rows')
                parsed_path = REPORT / 'parsed' / f'attempt-{number:06d}.json'
                self.check(additional_disk=100000)
                fields['parsed'] = atomic_json(parsed_path, parsed, immutable=True)
                fields['parser_sha256'] = digest(Path(parse_csv.__code__.co_filename))
                fields['parsed_sha256'] = fields['parsed']['sha256']
                fields['parse_finished_epoch'] = self.clock()
                fields['available_epoch'] = max(fields['full_body_received_epoch'], fields['parse_finished_epoch'])
                status = 'received_parsed'
            self.store.finish(attempt, status, got, **fields)
        except BaseException as error:
            if attempt['status'] == 'inflight_reserved':
                self.store.finish(attempt, 'blocked_no_retry', got, retain_reservation=not complete,
                                  error_type=type(error).__name__, error=str(error),
                                  partial_payload_sha256=body_hash.hexdigest(), **fields)
            if isinstance(error, (KeyboardInterrupt, SystemExit)): raise
        finally:
            if timer_old is not None:
                signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, timer_old)
            receipt_path = REPORT / 'source-receipts' / f'attempt-{number:06d}.json'
            atomic_json(receipt_path, attempt, immutable=True)
        return attempt

    def input_records(self, day):
        records = []
        for attempt in self.store.state['attempts']:
            if attempt['day'] != day or attempt['dataset'] not in DATASETS: continue
            value = dict(attempt)
            if attempt.get('parsed'):
                item = attempt['parsed']; path = ROOT / item['path']
                # Atomic-json returns absolute paths; convert once at boundary.
                if path.is_absolute() and not path.is_relative_to(REPORT): raise BudgetStop('parsed path outside this stage')
                if digest(path) != item['sha256']: raise BudgetStop('published parsed hash changed')
                value['parsed'] = read(path)
            records.append(value)
        return records

    def issue(self, slot):
        self.check()
        if self.clock() > slot['issue_epoch'] + 30:
            self.resolve_missed(slot, 'missed_dispatch_window'); return
        issue = self.store.claim_issue(slot)
        inputs = build_live_features(self.input_records(slot['day']), slot['day'], slot['nominal'])
        base = dict(slot, input_cutoff_epoch=slot['issue_epoch'], dispatch_epoch=issue['dispatch_epoch'],
                    source_inputs=inputs['source_inputs'], input_coverage=inputs['coverage'],
                    input_manifest_sha256=digest(REPORT / 'input-manifest.json'),
                    recommendation_qualified=False, forecast_display_method='empirical', issuer_process=self.identity)
        if not inputs['input_gate_passed']:
            base.update(status='insufficient_input', finished_epoch=self.clock())
            path = REPORT / 'issues' / f"{slot['issue_id']}.json"
            issue.update(status='insufficient_input', receipt=atomic_json(path, base, immutable=True)); self.store.persist(); return
        result = self.predictor.forward(inputs['raw_features'], inputs['observations'], slot['day'], slot['nominal'])
        finished = self.clock(); base.update(result, finished_epoch=finished, status='computed_shadow_pending_seal')
        forecast = OUTPUT / 'forecasts' / f"{slot['issue_id']}.json"
        self.check(additional_disk=100000)
        forecast_record = atomic_json(forecast, base, immutable=True)
        sealed = self.clock(); earliest = local_epoch(slot['day'], slot['nominal'] - 30)
        seal = dict(issue_id=slot['issue_id'], issue_epoch=slot['issue_epoch'], forecast=forecast_record,
                    sealed_epoch=sealed, earliest_candidate_epoch=earliest, issuer_process=self.identity,
                    protocol_sha256=self.store.protocol_sha, program_sha256=self.store.program_sha)
        seal_record = atomic_json(OUTPUT / 'seals' / f"{slot['issue_id']}.json", seal, immutable=True)
        seal_completed = self.clock()  # Both immutable files have been fsynced and published.
        status = 'sealed_shadow_only' if seal_completed <= slot['issue_epoch'] + 60 and seal_completed < earliest else 'late_output'
        publication = dict(issue_id=slot['issue_id'], status=status, forecast=forecast_record, seal=seal_record,
                           seal_completed_epoch=seal_completed, primary_evaluation_valid=status == 'sealed_shadow_only',
                           issue_epoch=slot['issue_epoch'], earliest_candidate_epoch=earliest,
                           no_validated_recommendation=True, program_sha256=self.store.program_sha,
                           protocol_sha256=self.store.protocol_sha)
        publication_record = atomic_json(OUTPUT / 'publications' / f"{slot['issue_id']}.json", publication, immutable=True)
        issue.update(status=status, forecast=forecast_record, seal=seal_record, publication=publication_record,
                     seal_completed_epoch=seal_completed, published_epoch=self.clock())
        self.store.persist()

    def resolve_missed(self, slot, reason):
        if slot['issue_id'] in self.store.state['issues']: return
        value = dict(slot, status=reason, observed_epoch=self.clock(), no_forecast_created=True)
        receipt = atomic_json(REPORT / 'issues' / f"{slot['issue_id']}.json", value, immutable=True)
        self.store.state['issues'][slot['issue_id']] = dict(value, receipt=receipt); self.store.persist()

    def morning(self, day):
        if day not in ALLOWED_DAYS: raise BudgetStop('morning date not admitted')
        self.phase_deadline = local_epoch(day, 530)  # 08:50:00, after last08:49 source dispatch.
        self.check()
        startup = self.clock(); slots = [slot for slot in issue_slots() if slot['day'] == day]
        for slot in slots:
            if slot['issue_epoch'] <= startup: self.resolve_missed(slot, 'missed_restart_no_backfill')
        from .prospective_inference import FrozenPredictor
        self.predictor = FrozenPredictor(read(REPORT / 'input-manifest.json'))
        ready = self.clock()
        events = [(slot['issue_epoch'], 'issue', slot) for slot in slots if slot['issue_epoch'] > ready]
        for slot in slots:
            if startup < slot['issue_epoch'] <= ready: self.resolve_missed(slot, 'missed_model_warmload')
        prior_keys = {a['source_key'] for a in self.store.state['attempts']}
        skipped = self.store.state.setdefault('skipped_source_keys', [])
        for slot in source_slots(day):
            for index, dataset in enumerate(DATASETS):
                key = f"{dataset}/{int(slot['source_epoch'])}"
                due = slot['due_epoch'] + index
                if key in prior_keys or key in skipped: continue
                if due <= ready: skipped.append(key)
                else: events.append((due, dataset, dict(slot, source_key=key)))
        self.store.persist()
        for due, kind, value in sorted(events, key=lambda item: (item[0], item[1])):
            self.wait_until(due); self.check()
            if kind == 'issue':
                if value['issue_id'] not in self.store.state['issues']: self.issue(value)
            elif kind not in self.store.state['stopped_sources']:
                if self.clock() >= due + 300:
                    skipped.append(value['source_key']); self.store.persist(); continue
                stamp = dt.datetime.fromtimestamp(value['source_epoch'], TZ)
                compact = stamp.strftime('%Y%m%d'); name = f'TDCS_{kind}_{compact}_{stamp:%H%M}00.csv'
                url = f'https://tisvcloud.freeway.gov.tw/history/TDCS/{kind}/{compact}/{stamp:%H}/{name}'
                self.request(value['source_key'], kind, url, 1000000, day=day, source_epoch=value['source_epoch'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--morning-day'); group.add_argument('--truth-day')
    parser.add_argument('--admission', type=Path, required=True)
    args = parser.parse_args(); mode = 'morning' if args.morning_day else 'truth'
    program, protocol, hashes = validate_admission(args.admission, mode)
    scopes = [REPORT, OUTPUT, ROOT / 'prototype/prospective-departure-2026-10-20']
    scopes += list((ROOT / 'scripts/departure_advisor').glob('prospective*.py'))
    scopes += list((ROOT / 'tests').glob('test_departure_advisor_prospective*.py'))
    with Store(REPORT, OUTPUT, program, hashes['program_sha256'], hashes['protocol_sha256'], disk_scopes=scopes) as store:
        supervisor = Supervisor(store, protocol)
        launch_id = supervisor.identity['instance_id']; started = time.time()
        def stop(*ignored): supervisor.stopping = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        launch = dict(status='running', mode=mode, day=args.morning_day or args.truth_day,
                      started_epoch=started, process=supervisor.identity, contract=hashes,
                      admission_sha256=digest(args.admission), no_new_fit=True)
        atomic_json(REPORT / 'launches' / f'{launch_id}.json', launch, immutable=True)
        store.state['process'] = launch; store.persist()
        try:
            if mode == 'morning': supervisor.morning(args.morning_day)
            else:
                from .prospective_truth import run_truth_session
                run_truth_session(supervisor, args.truth_day)
            status, reason = 'completed', 'bounded_session_finished'
        except BudgetStop as error: status, reason = 'stopped', str(error)
        except BaseException as error:
            status, reason = 'failed', f'{type(error).__name__}: {error}'
            raise
        finally:
            terminal = dict(instance_id=launch_id, status=locals().get('status', 'failed'),
                            reason=locals().get('reason', 'unknown'), finished_epoch=time.time(), process=supervisor.identity)
            atomic_json(REPORT / 'launches' / f'{launch_id}-terminal.json', terminal, immutable=True)
            store.state['process'] = terminal; store.persist()


if __name__ == '__main__': main()
