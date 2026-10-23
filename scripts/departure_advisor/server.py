"""Loopback prototype: frozen historical forecasts and separately revealed trips.

No model fitting, source HTTP, archive extraction or current-day advice occurs
in this server. Only the new prototype and fixed new-stage report routes serve
files. The repository is never a document root.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import signal
import threading
import time
from urllib.parse import parse_qs,urlsplit

for _thread_option in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ[_thread_option]='1'
import numpy as np

from .collector import ROOT,REPORT,identity,encode
from .data import NOMINALS,SHIFTS,DataBlocked,file_hash,verify_record,verify_selection_freeze
from .decision import choose,weighted_quantile
from .parser import TZ,GATES,DATASETS

PROTOTYPE=ROOT/'prototype/departure-advisor-2026-10-20'
RUN=REPORT/'research-run-v2'
STATIC={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/adapter.js':'adapter.js','/style.css':'style.css'}
# This whitelist never includes earlier dayrun reports or arbitrary query paths.
RESEARCH={'/research':'index.html','/research/english':'english-draft.html','/research/plan':'plan.html'}


class ApiUnavailable(DataBlocked):pass
class BadQuery(ValueError):pass


def iso(epoch):return dt.datetime.fromtimestamp(epoch,TZ).isoformat()


def minutes(text):
    if not isinstance(text,str)or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',text):raise BadQuery('invalid_HH_MM')
    return int(text[:2])*60+int(text[3:])


def date_time(day,minute):
    return dt.datetime.fromisoformat(day).replace(tzinfo=TZ)+dt.timedelta(minutes=minute)


def query_values(query):
    fields={'day','nominal','deadline','advance','delay','cost'}
    if set(query)!=fields or any(not isinstance(v,list)or len(v)!=1 for v in query.values()):raise BadQuery('six_exact_query_fields_required')
    result={k:v[0]for k,v in query.items()}
    try:
        parsed=dt.date.fromisoformat(result['day'])
        if parsed.isoformat()!=result['day']:raise ValueError()
        if minutes(result['nominal'])not in NOMINALS:raise ValueError()
        minutes(result['deadline'])
        if result['advance']not in ('0','15','30')or result['delay']not in ('0','15','30'):raise ValueError()
        result['advance']=int(result['advance']);result['delay']=int(result['delay'])
        result['cost']=float(result['cost'])
        if result['cost']not in (0,.25,.5,1):raise ValueError()
    except (ValueError,TypeError)as error:raise BadQuery('query_outside_fixed_contract')from error
    return {k:result[k]for k in ['day','nominal','deadline','advance','delay','cost']}


def candidate_id(day,nominal,shift):return f'{day}-{nominal:04d}-{shift:+03d}'


def finite_prediction(value):
    if value is None:return None
    p=np.asarray(value,dtype=float)
    if p.shape!=(4,)or not np.isfinite(p).all():return None
    T50,T90,A50,A90=p
    if min(p)<0 or T90<T50 or A90<A50 or not T50<=A50<=T50+15 or not T90<=A90<=T90+15:
        raise ApiUnavailable('frozen_prediction_physical_contract_failed')
    return dict(zip(['Tq50','Tq90','Aq50','Aq90'],map(float,p)))


class ReplayRepository:
    def __init__(self,root=ROOT,run=RUN):
        self.root=Path(root);self.stage=self.root/'reports/departure-advisor-2026-10-20';self.run=Path(run)
        self.receipt_hash=None;self.data=None;self.predictions={};self.key_indices={};self.cal_days={};self.outcome_cache={}

    def bound_artifact(self,record):
        path=verify_record(record,self.root)
        if not path.is_relative_to(self.run):raise ApiUnavailable('artifact_outside_fixed_research_run')
        return path

    def load(self):
        path=self.run/'evaluation/receipt.json'
        if not path.is_file():return False
        digest=file_hash(path)
        if digest==self.receipt_hash:
            if file_hash(self.stage/'protocol.json')!=self.data['receipt']['protocol_sha256']or file_hash(self.stage/'data-plan.json')!=self.data['freeze']['data_plan_sha256']:
                raise ApiUnavailable('cached_replay_protocol_drift')
            return True
        receipt=json.loads(path.read_text())
        if receipt.get('status')!='completed'or receipt.get('D_test_read')is not False:raise ApiUnavailable('research_receipt_not_completed')
        protocol=json.loads((self.stage/'protocol.json').read_text());plan=json.loads((self.stage/'data-plan.json').read_text())
        if receipt['protocol_sha256']!=file_hash(self.stage/'protocol.json')or plan['protocol_sha256']!=receipt['protocol_sha256']:
            raise ApiUnavailable('research_protocol_binding_failed')
        freeze_record=receipt['selection_freeze'];self.bound_artifact(freeze_record)
        freeze=verify_selection_freeze(plan,protocol,self.run/'selection-freeze.json',self.root)
        seal_path=self.bound_artifact(receipt['selection_seal'])
        if json.loads(seal_path.read_text())['selection_sha256']!=freeze_record['sha256']:raise ApiUnavailable('selection_receipt_seal_mismatch')
        artifacts=receipt['artifacts'];results_path=self.bound_artifact(artifacts['results.json']);results=json.loads(results_path.read_text())
        if results['protocol_sha256']!=receipt['protocol_sha256']or results['selection_sha256']!=freeze_record['sha256']:
            raise ApiUnavailable('result_selection_binding_failed')
        if results['forecast_display_method']!=freeze['forecast_display_method']or results['policy_selected_method']!=freeze['policy_selected_method']:
            raise ApiUnavailable('result_changed_frozen_model_selection')
        prepared_hashes={json.loads(verify_record(model['fit_receipt'],self.root).read_text()).get('input_hashes',{}).get('prepared')for model in freeze['artifacts']['models'].values()}
        prepared_path=self.run/'prepared/receipt.json'
        if len(prepared_hashes)!=1 or None in prepared_hashes or file_hash(prepared_path)not in prepared_hashes:raise ApiUnavailable('prepared_receipt_model_binding_failed')
        self.data={'receipt':receipt,'protocol':protocol,'freeze':freeze,'results':results,'artifacts':artifacts,
            'prepared':json.loads(prepared_path.read_text())}
        self.predictions={};self.key_indices={};self.cal_days={};self.outcome_cache={}
        for role,filename in [('calibration','calibration-primary-predictions.npz'),('devcheck','devcheck_primary-predictions.npz')]:
            keys=json.loads(self.bound_artifact(artifacts[role+'-keys.json']).read_text())
            expected=[(day,n,s)for day in protocol['dates'][role]for n in NOMINALS for s in SHIFTS]
            if [tuple(k)for k in keys]!=expected:raise ApiUnavailable('replay_case_key_order_mismatch')
            self.key_indices[role]={tuple(key):i for i,key in enumerate(keys)}
            with np.load(self.bound_artifact(artifacts[filename]),allow_pickle=False)as archive:
                for method in {freeze['forecast_display_method'],freeze['policy_selected_method']}-{None}:
                    values=archive[method+'_final']
                    if values.shape!=(len(keys),4):raise ApiUnavailable('replay_prediction_shape_mismatch')
                    self.predictions[(role,method)]=values
                    if role=='calibration':self.cal_days[method]=len({k[0]for k,p in zip(keys,values)if np.isfinite(p).all()})
        self.receipt_hash=digest;return True

    def available_dates(self):
        if not self.load():return []
        return sorted(self.data['protocol']['dates']['calibration']+self.data['protocol']['dates']['devcheck'])

    def qualification(self,query=None):
        if not self.load():return {'status':'not_ready','policy_qualified':False,'forecast_display_method':None,
            'policy_selected_method':None,'reason_codes':['research_not_completed']}
        results=self.data['results'];primary=query is None or(
            minutes(query['deadline'])==minutes(query['nominal'])+45 and query['cost']==.5 and query['advance']==30 and query['delay']==30)
        qualified=bool(primary and results['policy_qualified'])
        return {'status':'qualified_primary_historical_shadow'if qualified else 'strategy_unqualified',
            'policy_qualified':qualified,'forecast_display_method':results['forecast_display_method'],
            'policy_selected_method':results['policy_selected_method'],
            'reason_codes':list(results['reason_codes'])+([]if primary else['exploratory_constraint_scenario']),
            'scope':'D=nominal+45 at98km gantry,cost.5,advance30,delay30;historical shadow only'}

    def method_for(self,query):
        qualification=self.qualification(query)
        return (qualification['policy_selected_method']if qualification['policy_qualified']else qualification['forecast_display_method']),qualification

    def options(self,query):
        if not self.load():raise ApiUnavailable('research_not_completed')
        if query['day']not in self.available_dates():raise BadQuery('day_not_in_available_replay_dates')
        nominal=minutes(query['nominal']);deadline=minutes(query['deadline']);method,qualification=self.method_for(query)
        role='calibration'if query['day']in self.data['protocol']['dates']['calibration']else'devcheck'
        rows=[]
        for shift in SHIFTS:
            key=(query['day'],nominal,shift);index=self.key_indices[role][key]
            p=finite_prediction(self.predictions[(role,method)][index])if method is not None else None
            rows.append({'id':candidate_id(*key),'shift_minutes':shift,
                'window_start':date_time(query['day'],nominal+shift).isoformat(),
                'window_end':date_time(query['day'],nominal+shift+15).isoformat(),
                'data_status':'available'if p is not None else'data_insufficient','prediction':p,
                'permitted_by_user_bounds':-query['advance']<=shift<=query['delay'],
                'estimated_q90_within_deadline':nominal+shift+p['Aq90']<=deadline if p is not None else None,
                'calibration_support':{'days':self.cal_days.get(method),'trips':None},
                'reason_codes':[]if p is not None else['frozen_forecast_unavailable']})
        permitted=[r for r in rows if r['permitted_by_user_bounds']]
        selected=None;reasons=[]
        if any(r['prediction']is None for r in permitted):status='data_insufficient';reasons=['permitted_option_prediction_missing']
        elif not any(r['estimated_q90_within_deadline']for r in permitted):status='no_feasible_option';reasons=['no_permitted_window_meets_estimated_q90_deadline']
        elif not qualification['policy_qualified']:status='strategy_unqualified';reasons=qualification['reason_codes']
        else:
            decision=choose([{'shift':r['shift_minutes'],'prediction':list(r['prediction'].values())if r['prediction']is not None else[np.nan]*4}for r in rows],
                nominal,deadline,query['cost'],qualified=True)
            status=decision['status'];selected=decision.get('shift')if decision['selected']is not None else None
        issue=date_time(query['day'],nominal-45)
        return {'schema_version':1,'mode':'historical_replay','request':query,'issue_time':issue.isoformat(),
            'data_as_of':issue.isoformat()if any(r['prediction']is not None for r in rows)else None,
            'model_id':method,'qualification':qualification,'decision':{'status':status,'selected_shift_minutes':selected,'reason_codes':reasons},
            'candidates':rows,'outcome_reveal_available':True,
            'feature_clock_semantics':'Revised historical bucket start+5min end+5min assumed publication;not first-vintage live inference.',
            'forecast_delivery':'Read-only replay of actual fixed-model CPU predictions;API latency is not model inference latency.',
            'research_receipt_sha256':self.receipt_hash}

    def outcome_bags(self,role):
        if role not in self.outcome_cache:
            if role=='devcheck':artifact=self.data['artifacts']['devcheck-bags.npz']
            else:artifact=self.data['prepared']['artifacts']['calibration-bags.npz']
            with np.load(self.bound_artifact(artifact),allow_pickle=False)as archive:
                self.outcome_cache[role]={(name.rsplit('_',1)[0],int(name.rsplit('_',1)[1])):archive[name]for name in archive.files}
        return self.outcome_cache[role]

    def outcomes(self,query):
        options=self.options(query);role='calibration'if query['day']in self.data['protocol']['dates']['calibration']else'devcheck'
        bags=self.outcome_bags(role);nominal=minutes(query['nominal']);deadline=minutes(query['deadline']);rows=[]
        for shift in SHIFTS:
            bag=bags.get((query['day'],nominal+shift));observed=None
            if bag is not None and len(bag)>=30:
                observed={name:weighted_quantile(bag[:,column],np.ones(len(bag)),q)
                    for name,column,q in [('Tq50',0,.5),('Tq90',0,.9),('Aq50',1,.5),('Aq90',1,.9)]}
                observed.update({'ontime_share':float(np.mean(bag[:,2]<=deadline)),'trips':len(bag)})
            rows.append({'id':candidate_id(query['day'],nominal,shift),'observed':observed,
                'reason_codes':[]if observed is not None else['historical_cohort_below30_or_missing']})
        return {'schema_version':1,'mode':'historical_outcomes','request':query,'model_id':options['model_id'],
            'status':'available'if any(r['observed']is not None for r in rows)else'data_insufficient','candidates':rows,
            'claim':'Different retrospectively observed trip cohorts,not causal same-person time savings.'}


def process_alive(process):
    try:
        if process['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():return False
        fields=(Path('/proc')/str(process['pid'])/'stat').read_text().rsplit(')',1)[1].split()
        return fields[0]not in ('Z','X')and fields[19]==str(process['start_ticks'])
    except (KeyError,FileNotFoundError,ProcessLookupError):return False


def current_observation(stage=REPORT,clock=time.time):
    asof=clock();state_path=stage/'collector-state.json'
    if not state_path.is_file():return {'status':'not_ready','source_time':None,'received_at':None,'summary':'尚未有本輪觀測。','summary_en':'No observations for this run yet.','missing_features':list(DATASETS)}
    state=json.loads(state_path.read_text());running=state.get('status')=='collector_running'and process_alive(state.get('process')or{})and not(stage/'STOP').exists()and not(stage/'STOP-collector').exists()
    parts=[];parts_en=[];times=[];available=[];missing=[];stale=False
    for dataset in DATASETS:
        try:
            source=state['sources'][dataset];manifest=source.get('last_manifest')or{}
            if source['status']!='admitted_collecting'or manifest.get('status')!='received_parsed':raise ApiUnavailable('latest_source_not_healthy')
            receipt_path=Path(manifest['receipt_path'])
            if not receipt_path.resolve().is_relative_to(stage.resolve())or file_hash(receipt_path)!=manifest['receipt_sha256']:raise ApiUnavailable('receipt_hash_or_scope')
            receipt=json.loads(receipt_path.read_text())
            if receipt['id']!=manifest['attempt_id']or receipt['dataset']!=dataset or receipt['payload_sha256']!=manifest['payload_sha256']or receipt['status']!='received_parsed':raise ApiUnavailable('source_identity_mismatch')
            parsed_record=receipt['parsed_artifact'];parsed_path=Path(parsed_record['path'])
            if not parsed_path.resolve().is_relative_to(stage.resolve())or file_hash(parsed_path)!=parsed_record['sha256']:raise ApiUnavailable('parsed_hash_or_scope')
            parsed=json.loads(parsed_path.read_text());source_epoch=receipt['source_epoch'];available_epoch=manifest['available_epoch']
            if available_epoch is None or not all(math.isfinite(x)for x in [source_epoch,available_epoch,asof])or source_epoch>asof or available_epoch>asof:raise ApiUnavailable('unknown_or_future_source_clock')
            if parsed['dataset']!=dataset or parsed['interval_end_epoch']!=source_epoch or parsed['file_errors']:raise ApiUnavailable('parsed_source_clock_or_quality')
            have,need=parsed['context_available_rows'],parsed['context_expected_rows']
            age=asof-source_epoch;stale|=age>900;times.append(source_epoch);available.append(available_epoch)
            if have!=need:missing.append(dataset+'_incomplete_context')
            stamp=dt.datetime.fromtimestamp(source_epoch,TZ).strftime('%H:%M')
            parts.append(f'{dataset} {stamp}：{have}/{need}');parts_en.append(f'{dataset} {stamp}: {have}/{need}')
        except (ApiUnavailable,KeyError,ValueError,OSError,TypeError):
            missing.append(dataset+'_latest_unavailable');parts.append(dataset+' 最新格不可用');parts_en.append(dataset+' latest bucket unavailable')
    status='stopped'if not running else'stale'if stale else'data_insufficient'if missing else'available'
    prefix='採集已停止；'if not running else'';prefix_en='Collection stopped; 'if not running else''
    return {'status':status,'source_time':iso(times[0])if len(times)==3 and len(set(times))==1 else None,
        'received_at':iso(max(available))if available else None,'summary':prefix+'；'.join(parts)+'。僅觀測，非晨間預測或出發建議。',
        'summary_en':prefix_en+'; '.join(parts_en)+'. Observations only; no live morning forecast or departure advice.',
        'missing_features':missing,'display_freshness_seconds':900,'clock_semantics':'Each live source timestamp is the end of the preceding5min interval;available time includes immutable publication.'}


class Application:
    def __init__(self,repository=None,stage=REPORT,clock=time.time,review_only=False):
        self.repository=repository or ReplayRepository();self.stage=stage;self.clock=clock;self.review_only=review_only

    def status(self):
        dates=self.repository.available_dates()
        current=({'status':'stopped','source_time':None,'received_at':None,'summary':'僅檢視已封存的歷史研究；未讀取或更新今日觀測。',
            'summary_en':'Archived historical research review only;current observations are not read or updated.','missing_features':['archive_review_only']}
            if self.review_only else current_observation(self.stage,self.clock))
        return {'schema_version':1,'as_of':iso(self.clock()),'scope':{'gates':list(GATES),'vehicle_class':'31','timezone':'Asia/Taipei'},
            'qualification':self.repository.qualification(),'replay':{'available_dates':dates,'default_day':dates[-1]if dates else None},
            'current_observation':current,
            'live_prediction':{'status':'unavailable'if self.review_only else'out_of_domain',
                'reason_codes':['archive_review_only']if self.review_only else['run_ends_before_first_morning_issue','no_nighttime_use_of_morning_model']}}


class Handler(BaseHTTPRequestHandler):
    application=None
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass
    def send(self,status,body,kind='application/json'):
        self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        parsed=urlsplit(self.path)
        try:
            if parsed.path=='/api/status':value=self.application.status()
            elif parsed.path in ('/api/options','/api/outcomes'):
                query=query_values(parse_qs(parsed.query,keep_blank_values=True));value=(self.application.repository.options(query)
                    if parsed.path=='/api/options'else self.application.repository.outcomes(query))
            else:
                if parsed.path in STATIC:path=PROTOTYPE/STATIC[parsed.path];allowed_root=PROTOTYPE
                elif parsed.path in RESEARCH:path=REPORT/RESEARCH[parsed.path];allowed_root=REPORT
                else:self.send(404,encode({'error':'fixed_route_not_found'}));return
                if not path.resolve().is_relative_to(allowed_root.resolve()):raise ApiUnavailable('asset_symlink_outside_whitelist_root')
                if not path.is_file():self.send(404,encode({'error':'artifact_not_available'}));return
                self.send(200,path.read_bytes(),'text/javascript'if path.suffix=='.js'else mimetypes.guess_type(path.name)[0]or'application/octet-stream');return
            body=encode(value)
            if len(body)>1048576:raise ApiUnavailable('API_response_limit')
            self.send(200,body)
        except BadQuery as error:self.send(400,encode({'error':str(error)}))
        except (DataBlocked,KeyError,ValueError,OSError,TypeError)as error:self.send(503,encode({'error':'evidence_unavailable','detail':str(error)}))
    def do_POST(self):self.send(405,encode({'error':'GET_only_no_fit_or_forecast_generation'}))


def runtime_bounds(program,review_only,duration_seconds,epoch):
    if review_only:
        duration=3600 if duration_seconds is None else duration_seconds
        if not isinstance(duration,int)or not 1<=duration<=3600:raise BadQuery('review duration must be1..3600seconds')
        return epoch+duration,duration
    if duration_seconds is not None:raise BadQuery('duration applies only to explicit manual review mode')
    if epoch>=program['deadline_epoch']:raise ApiUnavailable('expired program requires explicit bounded archive review;research cannot restart')
    return program['deadline_epoch'],None


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--review-only',action='store_true',help='Manual archive-only session;never restarts collection or research')
    parser.add_argument('--duration-seconds',type=int,help='Review-only cap1..3600seconds(default3600)');args=parser.parse_args()
    if args.port!=8766:parser.error('fixed loopback port8766 only')
    program=json.loads((REPORT/'program.json').read_text())
    runtime_deadline,duration=runtime_bounds(program,args.review_only,args.duration_seconds,time.time())
    monotonic_end=time.monotonic()+duration if duration is not None else None
    repository=ReplayRepository()
    if args.review_only and not repository.load():raise ApiUnavailable('review requires completed sealed research archive')
    with(REPORT/'prototype.lock').open('a+')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        Handler.application=Application(repository,review_only=args.review_only);server=HTTPServer(('127.0.0.1',args.port),Handler)
        process={**identity(),'server_sha256':file_hash(Path(__file__))};receipt_path=REPORT/('prototype-review-process.json'if args.review_only else'prototype-process.json')
        def receipt(status,reason=None):
            value={'status':status,'process':process,'URL':'http://127.0.0.1:8766','at':iso(time.time()),
                'program_sha256':file_hash(REPORT/'program.json'),'deadline_epoch':runtime_deadline,
                'original_research_deadline_epoch':program['deadline_epoch'],'review_only':args.review_only,'duration_seconds':duration,'reason':reason}
            temporary=receipt_path.with_name('prototype-process.'+str(os.getpid())+'.tmp');temporary.write_bytes(encode(value));os.replace(temporary,receipt_path)
        stopped=threading.Event()
        def stop(signum=None,frame=None):stopped.set()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        receipt('running');server.timeout=.5
        try:
            while not stopped.is_set()and time.time()<runtime_deadline and(monotonic_end is None or time.monotonic()<monotonic_end)and not(REPORT/'STOP-prototype').exists():server.handle_request()
        finally:
            server.server_close();receipt('stopped','signal_or_deadline_or_stopfile');fcntl.flock(lock,fcntl.LOCK_UN)


if __name__=='__main__':main()
