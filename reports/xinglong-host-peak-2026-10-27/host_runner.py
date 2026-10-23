"""Fixed-date low-disk host checks. Source mode requires root's frozen admission."""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import sys
import time
import urllib.request

UTC=dt.timezone.utc
RUNTIME=('host_runner.py','candidate.html.tmpl')
DENIED=(401,402,403,429)
class Guard(Exception): pass

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def encode(value): return (json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode()
def aware(value):
    value=dt.datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(value,dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Guard('timezone_required')
    return value

def read(path,cap=65536):
    with open(path,'rb') as stream: data=stream.read(cap+1)
    if len(data)>cap: raise Guard('local_file_cap')
    return json.loads(data)

def process():
    stat=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
    high=[x.split(':',1)[1].strip() for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmHWM:')]
    return {'pid':os.getpid(),'start_ticks':int(stat[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'VmHWM':high[0] if high else None,'interpreter':str(Path(sys.executable).resolve()),
        'python_version':sys.version.split()[0],'cwd':os.getcwd(),'uid':os.getuid(),
        'CPU_affinity':sorted(os.sched_getaffinity(0)),
        'RLIMIT_AS_bytes':list(resource.getrlimit(resource.RLIMIT_AS))}

class ClockGate:
    def __init__(self,runner,target,end,anchor):
        self.r,self.target,self.end=runner,target,end
        self.wall,self.mono=anchor;self.last=self.wall
    def check(self):
        wall=self.r.now().timestamp();elapsed=self.r.monotonic()-self.mono
        if wall<self.last or abs((wall-self.wall)-elapsed)>5: raise Guard('clock_discontinuity')
        self.last=wall
        if elapsed>=88 or not self.target<=wall<self.end: raise Guard('request_time_window_closed')

class GuardedTransport:
    def __init__(self,opener,gate): self.opener,self.gate=opener,gate
    def open(self,request,timeout):
        self.gate.check()
        result=self.opener.open(request,timeout=timeout)
        # Check after open too: a response received across a clock jump is not usable.
        try: self.gate.check()
        except BaseException:
            result.close();raise
        return GuardedResponse(result,self.gate)
class GuardedResponse:
    def __init__(self,response,gate):
        self.response,self.gate=response,gate;self.status=response.status;self.headers=response.headers
    def read(self,n):
        self.gate.check();result=self.response.read(n);self.gate.check();return result
    def close(self): self.response.close()

class Runner:
    def __init__(self,stage,*,now=None,monotonic=time.monotonic,sleep=time.sleep,
                 opener=None,credentials=None,environment=None):
        self.stage=Path(stage).resolve();self.program=read(self.stage/'program.json')
        self.now=now or (lambda:dt.datetime.now(UTC));self.monotonic,self.sleep=monotonic,sleep
        self.opener,self.credentials=opener,credentials
        self.environment=dict(os.environ if environment is None else environment)
    def hashes(self): return {name:digest(self.stage/name) for name in RUNTIME}
    def size(self):
        total=0
        for path in self.stage.rglob('*'):
            if path.is_symlink(): raise Guard('stage_symlink_forbidden')
            if path.is_file(): total+=path.stat().st_size
        return total
    def write(self,path,value,*,replace=False):
        path=Path(path);data=value if isinstance(value,bytes) else encode(value)
        if self.size()+len(data)>self.program['limits']['stage_bytes']: raise Guard('stage_byte_cap')
        path.parent.mkdir(exist_ok=True,mode=0o700)
        target=path.with_name(path.name+'.pending') if replace else path
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
        if replace: os.replace(target,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    @contextlib.contextmanager
    def locked(self):
        fd=os.open(self.stage/'stage.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Guard('owned_stage_lock_busy') from None
            yield
    def systemd_identity(self):
        invocation=self.environment.get('INVOCATION_ID','')
        unit=self.environment.get('XINGLONG_UNIT','')
        if not re.fullmatch('[0-9a-fA-F]{32}',invocation) or not unit.endswith('.service'):
            raise Guard('systemd_invocation_required')
        return {'invocation_id':invocation,'unit':unit,'process':process()}
    def bindings(self,value):
        if value.get('program_sha256')!=digest(self.stage/'program.json') or value.get('runtime_hashes')!=self.hashes():
            raise Guard('admission_program_or_runtime_hash_mismatch')
    def rehearsal(self,schedule_path):
        identity=self.systemd_identity();config=read(schedule_path);self.bindings(config)
        label=config.get('id','')
        if not re.fullmatch('[A-Za-z0-9_-]{1,48}',label): raise Guard('rehearsal_id')
        if config.get('unit')!=identity['unit']: raise Guard('rehearsal_unit_mismatch')
        target=aware(config['target']);started=aware(self.now());delay=(started-target).total_seconds()
        directory=self.stage/('rehearsal-'+label)
        with self.locked():
            if (directory/'receipt.json').exists(): return {'status':'already_recorded','receipt_sha256':digest(directory/'receipt.json')}
            if (directory/'intent.json').exists(): raise Guard('unknown_rehearsal_no_resend')
            directory.mkdir(exist_ok=True,mode=0o700)
            self.write(directory/'intent.json',{'schedule_sha256':digest(schedule_path),'started_at':started.isoformat(),**identity})
            result={'schema_version':1,'kind':'network_free_real_timer_rehearsal',
                'status':'passed_on_time' if 0<=delay<=5 else 'not_on_time',
                'target':target.isoformat(),'started_at':started.isoformat(),'delay_seconds':delay,
                'finished_at':aware(self.now()).isoformat(),'HTTP':0,'credential_reads':0,
                'program_sha256':digest(self.stage/'program.json'),'runtime_hashes':self.hashes(),
                'schedule_sha256':digest(schedule_path),**identity,
                'timer_origin_requires_root_journal_verification':True}
            self.write(directory/'receipt.json',result);return result
    def admission(self,path):
        value=read(path);self.bindings(value)
        if value.get('approved') is not True or 'source' not in value.get('modes',[]) or value.get('rehearsal_root_verified') is not True:
            raise Guard('source_not_admitted')
        ref=value.get('rehearsal_receipt',{});relative=Path(ref.get('path',''))
        if relative.is_absolute() or '..' in relative.parts: raise Guard('rehearsal_path_scope')
        p=self.stage/relative
        if digest(p)!=ref.get('sha256'): raise Guard('rehearsal_hash_mismatch')
        proof=read(p);self.bindings(proof)
        if proof.get('status')!='passed_on_time' or not 0<=proof.get('delay_seconds',-1)<=5 or proof.get('HTTP')!=0:
            raise Guard('rehearsal_not_passed')
        current=process()
        for key in ('interpreter','cwd','uid'):
            if proof.get('process',{}).get(key)!=current[key]: raise Guard('rehearsal_execution_identity_mismatch')
        return value
    def old_probe(self):
        root=Path(self.program['repository_root'])
        for relative,expected in self.program['dependencies'].items():
            if digest(root/relative)!=expected: raise Guard('frozen_dependency_hash_mismatch')
        path=root/'reports/xinglong-public-feed-2026-10-23/once_probe.py'
        spec=importlib.util.spec_from_file_location('fixed_once_probe',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def inspect_slot(self,slot):
        directory=self.stage/('slot-'+slot);terminal=directory/'outcome.json'
        unknown={'slot':slot,'status':'unknown_attempt','request_outcomes_known':False,'HTTP_actual':None}
        if terminal.exists():
            try:
                value=read(terminal)
                if value.get('slot')!=slot: raise Guard('slot_identity_mismatch')
                self.bindings(value)
                if value['status'] in ('missed_launch_grace','blocked_prior_unknown_or_access_refusal','missed_not_started'):
                    ledger=self.stage/'ledger.json'
                    if value.get('HTTP_actual')!=0 or any((directory/n).exists() for n in ('reservation.json','intent.json','receipt.json')) or (ledger.exists() and slot in read(ledger).get('slots',{})):
                        raise Guard('no_request_outcome_has_attempt_evidence')
                    return {**value,'request_outcomes_known':True,'access_refused':False}
                receipt_path=directory/'receipt.json'
                if digest(receipt_path)!=value.get('probe_receipt_sha256'): raise Guard('probe_receipt_hash_mismatch')
                receipt=read(receipt_path);P=self.old_probe()
                if receipt.get('code_sha256')!=self.program['dependencies']['reports/xinglong-public-feed-2026-10-23/once_probe.py'] or receipt.get('adapter_sha256')!=self.program['dependencies']['reports/xinglong-public-feed-2026-10-23/feed_adapter.py']:
                    raise Guard('probe_code_binding_mismatch')
                reservation=read(directory/'reservation.json');self.bindings(reservation)
                if reservation.get('slot')!=slot or reservation.get('HTTP_reserved')!=3: raise Guard('reservation_binding')
                rows=receipt['requests']
                if not isinstance(rows,list) or not 1<=len(rows)<=3: raise Guard('unknown_request_count')
                began=aware(receipt['started_at']);finished=aware(receipt['finished_at'])
                for i,row in enumerate(rows,1):
                    kind=('auth','static','live')[i-1]
                    expected_url=P.AUTH_URL if kind=='auth' else P.URLS[kind]
                    if row.get('index')!=i or row.get('kind')!=kind or row.get('url')!=expected_url or row.get('method')!=('POST' if i==1 else 'GET'):
                        raise Guard('request_identity_binding')
                    if read(directory/f'request-{i}-receipt.json')!=row: raise Guard('request_receipt_binding')
                    intent=read(directory/f'request-{i}-intent.json')
                    if any(intent.get(k)!=row.get(k) for k in ('index','kind','url','method','request_started_at')):
                        raise Guard('request_intent_binding')
                    start=aware(row['request_started_at']);end=aware(row['finished_at'])
                    if not began<=start<=end<=finished: raise Guard('request_clock_order')
                    if row.get('status')=='stopped_http_error' and row.get('http_status') is not None: continue
                    if row.get('http_status')!=200 or not began<=start<=aware(row['full_body_received_at'])<=aware(row['parse_completed_at'])<=end:
                        raise Guard('unknown_incomplete_request')
                # Derive these fields from verified requests; never trust outcome booleans.
                return {**value,'request_outcomes_known':True,'HTTP_actual':len(rows),
                    'access_refused':any(row.get('http_status') in DENIED for row in rows)}
            except (OSError,ValueError,TypeError,KeyError,Guard):
                return {**unknown,'transport_evidence':'unverified_or_incomplete'}
        if directory.exists() and any((directory/name).exists() for name in ('reservation.json','intent.json','receipt.json')):
            return unknown
        ledger=self.stage/'ledger.json'
        if ledger.exists() and slot in read(ledger).get('slots',{}): return unknown
        return {'slot':slot,'status':'not_started','request_outcomes_known':True,'HTTP_actual':0}
    def outcome(self,slot,value):
        directory=self.stage/('slot-'+slot);directory.mkdir(exist_ok=True,mode=0o700)
        self.write(directory/'outcome.json',value)
    def source(self,slot,admission_path):
        # Capture launch time before filesystem validation; do not silently move target.
        actual=aware(self.now());anchor=(actual.timestamp(),self.monotonic());identity=self.systemd_identity()
        if slot not in self.program['slots']: raise Guard('unknown_slot')
        times=self.program['slots'][slot];target=aware(times['target']);end=aware(times['end_exclusive'])
        with self.locked():
            self.admission(admission_path)
            existing=self.inspect_slot(slot)
            if existing['status']=='unknown_attempt': return existing
            if existing['status']!='not_started': return {'status':'already_recorded','original_status':existing['status']}
            delay=(actual-target).total_seconds()
            if delay<0: return {'status':'not_due','HTTP_actual':0}
            base={'schema_version':1,'slot':slot,'target':target.isoformat(),'end_exclusive':end.isoformat(),
                'started_at':actual.isoformat(),'delay_seconds':delay,
                'timing_status':'on_time' if delay<=5 else 'late_started',**identity,
                'program_sha256':digest(self.stage/'program.json'),'runtime_hashes':self.hashes()}
            if delay>60 or actual>=end:
                result={**base,'status':'missed_launch_grace','request_outcomes_known':True,'HTTP_actual':0,'measurement_usable':False}
                self.outcome(slot,result);self.publish(slot);return result
            if slot=='1830':
                prior=self.inspect_slot('1730')
                if prior.get('request_outcomes_known') is not True or prior.get('access_refused'):
                    result={**base,'status':'blocked_prior_unknown_or_access_refusal','request_outcomes_known':True,
                        'HTTP_actual':0,'measurement_usable':False,'prior_status':prior['status']}
                    self.outcome(slot,result);self.publish(slot);return result
            # Old probe bounds the entire slot at300KB. Also reserve both new display
            # writes and16KB for ledger/outcome; old display bytes already count in size().
            reserve=max(self.program['limits']['pre_source_artifact_reserve_bytes'],
                300000+2*20000+(self.stage/'candidate.html.tmpl').stat().st_size+16384)
            if self.size()+reserve>self.program['limits']['stage_bytes']: raise Guard('insufficient_stage_space_cap')
            fs=os.statvfs(self.stage)
            if fs.f_bavail*fs.f_frsize<reserve: raise Guard('insufficient_free_space_for_bounded_artifacts')
            P=self.old_probe();directory=self.stage/('slot-'+slot);directory.mkdir(exist_ok=True,mode=0o700)
            ledgerpath=self.stage/'ledger.json'
            ledger=read(ledgerpath) if ledgerpath.exists() else {'HTTP_reserved':0,'slots':{}}
            if slot in ledger['slots']: raise Guard('unknown_ledger_reservation_no_resend')
            # Reconcile unknown older reservations conservatively without granting budget back.
            for key in self.program['slots']:
                if key not in ledger['slots'] and (self.stage/('slot-'+key)/'reservation.json').exists():
                    ledger['slots'][key]={'HTTP_reserved':3,'reconciled_unknown':True};ledger['HTTP_reserved']+=3
            if ledger['HTTP_reserved']+3>6: raise Guard('shared_HTTP_reservation_cap')
            self.write(directory/'reservation.json',{**base,'HTTP_reserved':3,'unknown_consumes_budget':True})
            ledger['slots'][slot]={'HTTP_reserved':3};ledger['HTTP_reserved']+=3
            self.write(ledgerpath,ledger,replace=ledgerpath.exists())
            gate=ClockGate(self,target.timestamp(),end.timestamp(),anchor)
            opener=self.opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),P.NoRedirect())
            def credential(): gate.check();return (self.credentials or P.credentials)()
            try:
                receipt=P.run_once(directory,stage=directory,opener=GuardedTransport(opener,gate),load_credentials=credential,
                    now=lambda:aware(self.now()).isoformat(),monotonic=self.monotonic,sleep=self.sleep)
                rows=receipt['requests']
                known=bool(rows) and all(x.get('http_status') is not None and (x.get('status')=='stopped_http_error' or x.get('full_body_received_at')) for x in rows)
                result={**base,'status':receipt['status'],'finished_at':aware(self.now()).isoformat(),
                    'request_outcomes_known':known,'HTTP_actual':len(rows),
                    'access_refused':any(x.get('http_status') in DENIED for x in rows),
                    'transport_connected':receipt['transport_connected'],'measurement_usable':receipt['measurement_usable'],
                    'probe_receipt_sha256':digest(directory/'receipt.json'),
                    'legacy_ru_maxrss_KiB_unverified':receipt.get('peak_rss_KiB'),
                    'process_at_finish':process()}
            except BaseException as error:
                result={**base,'status':'local_failure_no_resend','finished_at':aware(self.now()).isoformat(),
                    'error_type':type(error).__name__,'request_outcomes_known':False,'HTTP_actual':None,'measurement_usable':False}
            self.outcome(slot,result);self.publish(slot);return result
    def frame(self,slot):
        result=self.inspect_slot(slot);directory=self.stage/('slot-'+slot)
        if result.get('status')!='single_shot_complete': return None
        receipt=read(directory/'receipt.json')
        if digest(directory/'receipt.json')!=result.get('probe_receipt_sha256'): raise Guard('probe_receipt_hash_mismatch')
        diagnostic=directory/receipt['diagnostic_file']
        if receipt['diagnostic_file']!='live-diagnostic.json' or digest(diagnostic)!=receipt['diagnostic_sha256']: raise Guard('diagnostic_hash_mismatch')
        value=read(diagnostic)
        for kind,row in value['input_bodies'].items():
            if row['body_file']!='HsinchuCounty_'+kind+'.json' or digest(directory/row['body_file'])!=row['body_sha256']:
                raise Guard('source_body_hash_mismatch')
        return value['normalized_snapshot']
    def safe_frame(self,slot):
        try: return self.frame(slot),None
        except (OSError,ValueError,TypeError,KeyError,Guard): return None,'measurement_evidence_unverified'
    def publish(self,slot):
        frame,measurement_error=self.safe_frame(slot);asof=aware(self.now()).isoformat()
        payload={'schema_version':1,'mode':'isolated_county_observation_snapshot','slot':slot,'as_of':asof,
            'slot_status':self.inspect_slot(slot)['status'],'frame':frame,'continuous_live':False,
            'measurement_evidence_error':measurement_error,
            'ETA_0_15_30':None,'recommendation':None,'benefit':None,'same_EndTime_is_not_new_support':True}
        if slot=='1830':
            old,old_error=self.safe_frame('1730')
            payload['previous_measurement_evidence_error']=old_error
            payload['new_observation_window_support']=None if not old or not frame else [
                {'pair_id':s['pair_id'],'changed':s['window']['end_epoch']!=old['segments'][i]['window']['end_epoch']}
                for i,s in enumerate(frame['segments'])]
        raw=encode(payload);template=(self.stage/'candidate.html.tmpl').read_text()
        page=template.replace('__PAYLOAD__',json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')).encode()
        self.write(self.stage/'current.json',raw,replace=(self.stage/'current.json').exists())
        self.write(self.stage/'candidate.html',page,replace=(self.stage/'candidate.html').exists())
        return payload
    def finalize(self):
        if aware(self.now())<aware(self.program['finalize_not_before']): raise Guard('finalize_not_due')
        with self.locked():
            path=self.stage/'finalize-receipt.json'
            if path.exists(): return read(path)
            slots={}
            for slot in self.program['slots']:
                value=self.inspect_slot(slot)
                if value['status']=='not_started':
                    value={'slot':slot,'status':'missed_not_started','HTTP_actual':0,'request_outcomes_known':True,'measurement_usable':False,
                        'program_sha256':digest(self.stage/'program.json'),'runtime_hashes':self.hashes()}
                    self.outcome(slot,value)
                slots[slot]=value
            payload=self.publish('1830')
            result={'status':'finalized_network_free','as_of':aware(self.now()).isoformat(),'HTTP_this_operation':0,
                'slots':slots,'candidate':payload,'ETA_0_15_30':None,'recommendation':None,'benefit':None,
                'program_sha256':digest(self.stage/'program.json'),'runtime_hashes':self.hashes(),'process':process()}
            self.write(path,result);return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode',choices=['rehearsal','source','finalize']);ap.add_argument('--stage',type=Path,default=Path(__file__).resolve().parent)
    ap.add_argument('--schedule',type=Path);ap.add_argument('--admission',type=Path);ap.add_argument('--slot',choices=['1730','1830'])
    args=ap.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(128*1024**2,128*1024**2));os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    def timeout(signum,frame): os._exit(124)  # Hard deadline cannot be swallowed by old probe's cleanup handler.
    signal.signal(signal.SIGALRM,timeout);signal.alarm(90)
    try:
        r=Runner(args.stage)
        if args.mode=='rehearsal':
            if not args.schedule: raise Guard('schedule_required')
            result=r.rehearsal(args.schedule)
        elif args.mode=='source':
            if not args.slot or not args.admission: raise Guard('source_arguments_required')
            result=r.source(args.slot,args.admission)
        else: result=r.finalize()
        print(json.dumps({'status':result['status'],'HTTP_actual':result.get('HTTP_actual',0)},ensure_ascii=False))
        return 0
    except BaseException as error:
        print(json.dumps({'status':'guard_stopped_no_resend','reason':str(error) if isinstance(error,Guard) else type(error).__name__}))
        return 2
    finally: signal.alarm(0)
if __name__=='__main__': raise SystemExit(main())
