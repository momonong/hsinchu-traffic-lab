"""One durable budget ledger and immutable receipts for this prospective run."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import re
import shutil
import time
import uuid


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): result.update(block)
    return result.hexdigest()


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def atomic_json(path, value, *, immutable=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.part')
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    with temporary.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    try:
        if immutable:
            os.link(temporary, path)  # Atomic publication with no overwrite.
            temporary.unlink()
        else: os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        # Preserve the temporary evidence after an unsuccessful publication.
        raise
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


class BudgetStop(RuntimeError): pass


class Store:
    def __init__(self, report, output, program, program_sha, protocol_sha, *, clock=time.time, disk_scopes=None):
        self.report, self.output = Path(report), Path(output)
        self.program, self.clock = program, clock
        self.program_sha, self.protocol_sha = program_sha, protocol_sha
        self.state_path = self.report / 'state.json'; self.lock = None; self.state = None
        self.disk_scopes = [Path(path) for path in (disk_scopes or [self.report, self.output])]

    def __enter__(self):
        self.report.mkdir(parents=True, exist_ok=True)
        self.lock = (self.report / 'supervisor.lock').open('a+')
        try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close(); self.lock = None; raise
        try:
            # Read only after ownership is acquired; never reuse a pre-lock state.
            if self.state_path.exists(): self.state = json.loads(self.state_path.read_text())
            else:
                evidence = [self.report / name for name in ('source-receipts', 'issues', 'launches', 'truth')]
                if any(path.exists() and any(path.iterdir()) for path in evidence) or (self.output.exists() and any(self.output.iterdir())):
                    raise BudgetStop('missing ledger with previous runtime evidence; refuse budget reset')
                self.state = {'schema_version': 1, 'program_sha256': self.program_sha,
                              'protocol_sha256': self.protocol_sha, 'HTTP_requests': 0,
                              'HTTP_body_received_bytes': 0, 'HTTP_body_charged_bytes': 0,
                              'M06_day_charged_bytes': {}, 'attempts': [], 'issues': {},
                              'truth_days': {}, 'stopped_sources': {}, 'last_request_epoch': None}
            if (self.state['program_sha256'], self.state['protocol_sha256']) != (self.program_sha, self.protocol_sha):
                raise BudgetStop('runtime program/protocol drift')
            self.validate_ledger()
            for attempt in self.state['attempts']:
                if attempt['status'] == 'inflight_reserved':
                    attempt.update(status='abandoned_reservation_retained', abandoned_observed_epoch=self.clock())
            for issue in self.state['issues'].values():
                if issue['status'] == 'inflight': issue['status'] = 'unresolved_after_restart_no_reissue'
            self.persist(); return self
        except BaseException:
            self.__exit__(None, None, None); raise

    def __exit__(self, *ignored):
        if self.lock is not None:
            fcntl.flock(self.lock, fcntl.LOCK_UN); self.lock.close(); self.lock = None

    def persist(self):
        if self.lock is None: raise BudgetStop('state mutation without ownership')
        self.state['updated_epoch'] = self.clock()
        atomic_json(self.state_path, self.state)

    def validate_ledger(self):
        attempts = self.state['attempts']
        if self.state['HTTP_requests'] != len(attempts): raise BudgetStop('request counter/index disagreement')
        if len({row['source_key'] for row in attempts}) != len(attempts): raise BudgetStop('duplicate source key in ledger')
        charged = received = 0; daily = {}
        for index, row in enumerate(attempts, 1):
            cap, count = row['reserved_bytes'], row['HTTP_body_received_bytes']
            if row['sequence'] != index or not isinstance(cap, int) or not isinstance(count, int) or not 0 <= count <= cap:
                raise BudgetStop('invalid durable request sequence/byte accounting')
            retained = row['status'] in ('inflight_reserved', 'abandoned_reservation_retained') or row.get('reservation_retained', False)
            charge = cap if retained else count
            charged += charge
            if row['status'] != 'inflight_reserved': received += count
            if row['dataset'] == 'M06A': daily[row['day']] = daily.get(row['day'], 0) + charge
        if (charged != self.state['HTTP_body_charged_bytes'] or received != self.state['HTTP_body_received_bytes']
                or daily != self.state['M06_day_charged_bytes']):
            raise BudgetStop('body counters disagree with durable reservations')
        # An older state file cannot silently discard later published evidence.
        for folder in (self.report / 'source-receipts', self.output / 'raw'):
            if folder.exists():
                for path in folder.iterdir():
                    match = re.match(r'attempt-(\d+)', path.name)
                    if match and int(match[1]) > len(attempts):
                        raise BudgetStop('runtime evidence newer than ledger; refuse rollback/reset')

    def check(self, *, phase_deadline=None, additional_disk=0):
        now = self.clock(); limits = self.program['limits']
        if now >= self.program['prospective_final_deadline_epoch']:
            raise BudgetStop('absolute prospective terminal reached')
        if phase_deadline is not None and now >= phase_deadline: raise BudgetStop('session deadline reached')
        if (self.report / 'STOP').exists(): raise BudgetStop('STOP requested')
        if shutil.disk_usage(self.report).free < limits['free_disk_reserve_bytes'] + additional_disk:
            raise BudgetStop('free disk reserve')
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > limits['RSS_bytes_total']:
            raise BudgetStop('RSS budget')
        files = set()
        for scope in self.disk_scopes:
            if scope.is_file(): files.add(scope.resolve())
            elif scope.is_dir(): files.update(path.resolve() for path in scope.rglob('*') if path.is_file())
        if sum(path.stat().st_size for path in files) + additional_disk > limits['new_disk_bytes']:
            raise BudgetStop('new stage disk budget')

    def reserve(self, source_key, dataset, *, cap, day=None):
        self.check()
        if source_key in {a['source_key'] for a in self.state['attempts']}:
            raise BudgetStop('source already attempted; no retry')
        if dataset in self.state['stopped_sources']: raise BudgetStop('source disabled by HTTP response')
        limits = self.program['limits']
        if self.state['HTTP_requests'] >= limits['HTTP_requests']: raise BudgetStop('HTTP request budget')
        if cap <= 0 or self.state['HTTP_body_charged_bytes'] + cap > limits['HTTP_body_bytes']:
            raise BudgetStop('HTTP body reservation budget')
        if dataset == 'M06A':
            if day is None or self.state['M06_day_charged_bytes'].get(day, 0) + cap > limits['M06_HTTP_body_bytes_per_research_day']:
                raise BudgetStop('M06 per-day body reservation budget')
            self.state['M06_day_charged_bytes'][day] = self.state['M06_day_charged_bytes'].get(day, 0) + cap
        attempt = dict(sequence=len(self.state['attempts']) + 1, source_key=source_key, dataset=dataset,
                       day=day, status='inflight_reserved', reserved_bytes=cap,
                       started_epoch=self.clock(), HTTP_body_received_bytes=0)
        self.state['attempts'].append(attempt); self.state['HTTP_requests'] += 1
        self.state['HTTP_body_charged_bytes'] += cap; self.state['last_request_epoch'] = self.clock()
        self.persist(); return attempt

    def finish(self, attempt, status, received_bytes, *, retain_reservation=False, **fields):
        if attempt['status'] != 'inflight_reserved': raise BudgetStop('attempt already terminal')
        if not 0 <= received_bytes <= attempt['reserved_bytes']: raise BudgetStop('invalid actual body accounting')
        refund = 0 if retain_reservation else attempt['reserved_bytes'] - received_bytes
        self.state['HTTP_body_charged_bytes'] -= refund
        self.state['HTTP_body_received_bytes'] += received_bytes
        if attempt['dataset'] == 'M06A': self.state['M06_day_charged_bytes'][attempt['day']] -= refund
        attempt.update(status=status, HTTP_body_received_bytes=received_bytes, finished_epoch=self.clock(),
                       reservation_retained=retain_reservation, **fields)
        self.persist()

    def claim_issue(self, slot):
        self.check()
        if slot['issue_id'] in self.state['issues']: raise BudgetStop('issue already resolved or claimed')
        issue = dict(slot, status='inflight', dispatch_epoch=self.clock())
        self.state['issues'][slot['issue_id']] = issue; self.persist(); return issue
