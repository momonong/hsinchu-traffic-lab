"""Explicit exposed-date inputs; no archive extraction, globs or old writers."""
from __future__ import annotations

from collections import Counter
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .parser import MODEL_PAIRS, TZ
from .collector import ROOT

NOMINALS = tuple(range(390, 556, 15))
SHIFTS = (-30, -15, 0, 15, 30)
LAGS = tuple(range(0, 61, 5))


class DataBlocked(ValueError):
    pass


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): result.update(block)
    return result.hexdigest()


def resolve(path, root=ROOT):
    path = Path(path)
    path = (path if path.is_absolute() else root/path).resolve()
    if not path.is_relative_to(root.resolve()): raise DataBlocked('input outside verified repository')
    return path


def verify_record(record, root=ROOT, verified=None):
    path = resolve(record['path'], root)
    if not path.is_file(): raise DataBlocked(f'missing explicit input: {path}')
    key = (str(path), record['sha256'], record['bytes'])
    if verified is not None and key in verified: return path
    if path.stat().st_size != record['bytes'] or file_hash(path) != record['sha256']:
        raise DataBlocked(f'input hash/size mismatch: {path}')
    if verified is not None: verified[key] = {'path':str(path),'sha256':record['sha256'],'bytes':record['bytes']}
    return path


def validate_dates(protocol):
    dates = protocol['dates']; roles = ('train','calibration','devcheck','unused_buffer','original_D_test_forbidden')
    sets = [set(dates[r]) for r in roles]
    if any(a&b for i,a in enumerate(sets) for b in sets[i+1:]): raise DataBlocked('date role overlap')
    allowed = sets[0]|sets[1]|sets[2]
    if len(allowed) != sum(len(dates[r]) for r in roles[:3]): raise DataBlocked('repeated role dates')
    return allowed


def context_records(plan, day):
    source = plan['observations']['context_inputs']
    if day in source: return source[day]
    try: return {kind:source[f'{day}/{kind}'] for kind in ('M04A','M05A')}
    except KeyError as error: raise DataBlocked('missing explicit context receipt: '+day) from error


def preflight_inputs(plan, protocol, root=ROOT):
    allowed = validate_dates(protocol)
    if set(plan['selected_days']) != allowed or set(plan['source_rows']) != allowed:
        raise DataBlocked('data plan must exactly match allowed dates')
    verified = {}; records = {}
    for day in plan['selected_days']:
        row_source = plan['source_rows'][day]
        receipt_path = verify_record(row_source['receipt'], root, verified)
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('status') != 'complete': raise DataBlocked('journey receipt incomplete: '+day)
        output = receipt.get('outputs',{}).get('S',{})
        if output.get('sha256') != row_source['rows']['sha256'] or resolve(output.get('path',''),root) != resolve(row_source['rows']['path'],root):
            raise DataBlocked('journey receipt output binding mismatch: '+day)
        verify_record(row_source['rows'],root,verified)
        records[day] = {}
        for kind, record in context_records(plan,day).items():
            if kind not in ('M04A','M05A'): raise DataBlocked('unadmitted observation dataset')
            path = verify_record(record,root,verified); receipt = json.loads(path.read_text())
            if receipt.get('status') != 'complete' or receipt.get('day') != day or receipt.get('dataset') != kind:
                raise DataBlocked('context receipt incomplete or wrong identity')
            verify_record(receipt['output'],root,verified)
            records[day][kind] = receipt
    for record in plan['observations']['base_processed'].values(): verify_record(record,root,verified)
    return {'status':'all_explicit_receipts_and_outputs_verified_no_extraction',
            'verified_files':list(verified.values()),'context_receipts':records,
            'D_test_read':False,'tar_scans':0,'old_writer_functions_called':False}


def load_observations(plan, protocol, root=ROOT):
    """Pure equivalent of D.observations; never imports/calls extract helpers."""
    evidence = preflight_inputs(plan,protocol,root); allowed = set(plan['selected_days'])
    pairs = {pair:i for i,pair in enumerate(MODEL_PAIRS)}; raw = {}; result = {}
    for key, record in plan['observations']['base_processed'].items():
        kind = key.rsplit('/',1)[-1]
        with resolve(record['path'],root).open() as stream:
            for row in csv.DictReader(stream):
                day = row['timestamp'][:10].replace('/','-'); pair = (row['from_gantry'],row['to_gantry'])
                if day not in allowed or pair not in pairs or pairs[pair]>=4 or row['vehicle_type']!='31': continue
                minute = dt.datetime.fromisoformat(row['timestamp'].replace('/','-')).hour*60 + dt.datetime.fromisoformat(row['timestamp'].replace('/','-')).minute
                item = raw.setdefault((day,minute,pairs[pair]),{})
                if kind in item: raise DataBlocked('duplicate base observation')
                measurement = float(row['travel_time_s'])/60 if kind=='m04a' else float(row['speed_raw'])
                item[kind] = (measurement,int(row['volume']))
    for key,value in raw.items():
        a,b=value.get('m04a'),value.get('m05a')
        if a and b and all(math.isfinite(x) and x>0 for x in (a[0],b[0])) and a[1]>0 and a[1]==b[1]:
            result[key] = (a[0],b[0],a[1])
    for day, receipts in evidence['context_receipts'].items():
        parsed = {kind:json.loads(resolve(r['output']['path'],root).read_text()) for kind,r in receipts.items()}
        for key in set(parsed['M04A'])|set(parsed['M05A']):
            a,b = parsed['M04A'].get(key), parsed['M05A'].get(key)
            if a is None or b is None: continue
            c_style = 'measurement' in a
            tt = a['measurement'] if c_style else a['raw_measurement']/60
            speed = b['measurement'] if c_style else b['raw_measurement']
            if not all(math.isfinite(x) and x>0 for x in (tt,speed)) or a['volume']<=0 or a['volume']!=b['volume']: continue
            minute,link=map(int,key.split(',')); link += 4 if c_style else 0
            if link not in range(10): raise DataBlocked('context link index outside ten-link contract')
            target = (day,minute,link); value = (tt,speed,a['volume'])
            if target in result and any(abs(x-y)>1e-9 for x,y in zip(result[target],value)):
                raise DataBlocked('base/context observations disagree')
            result[target] = value
    return result,evidence


def stamp(value):
    parsed = dt.datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def minute_on_day(value, day):
    return (stamp(value)-dt.datetime.fromisoformat(day).replace(tzinfo=TZ)).total_seconds()/60


def primary_admission(row, day):
    if row['vehicle_type']!=31 or row['trip_end']!='Y': return False
    entry,origin=stamp(row['entry_time']),stamp(row['origin_time'])
    return entry.date().isoformat()==day and 360<=minute_on_day(row['entry_time'],day)<600 and 0<=(entry-origin).total_seconds()/60<=360


def trip_targets(row, day, candidate):
    entry,exit_time=minute_on_day(row['entry_time'],day),minute_on_day(row['exit_time'],day)
    T=float(row['duration_minutes']); A=exit_time-candidate; offset=entry-candidate
    if not all(math.isfinite(x) for x in (T,A,offset)) or T<=0 or not 0<=offset<15:
        raise DataBlocked('trip outside candidate window or invalid target')
    if abs(A-(T+offset))>1e-6: raise DataBlocked('actual exit/entry/duration identity fails')
    return T,A


def verify_selection_freeze(plan, protocol, selection_path, root=ROOT):
    if not isinstance(selection_path,(str,Path)):raise DataBlocked('persisted selection freeze required')
    path=resolve(selection_path,root);seal_path=path.with_name('selection-seal.json')
    if path.name!='selection-freeze.json' or not path.is_file() or not seal_path.is_file():
        raise DataBlocked('missing persisted selection freeze/seal')
    selection=json.loads(path.read_text());seal=json.loads(seal_path.read_text())
    if seal.get('selection_sha256')!=file_hash(path) or selection.get('status')!='frozen_before_devcheck':
        raise DataBlocked('selection seal/status mismatch')
    stage=root/'reports/departure-advisor-2026-10-20'
    plan_file=stage/'data-plan.json';protocol_file=stage/'protocol.json'
    if not plan_file.is_file()or not protocol_file.is_file():raise DataBlocked('missing canonical protocol/data-plan')
    if json.loads(plan_file.read_text())!=plan or json.loads(protocol_file.read_text())!=protocol:
        raise DataBlocked('in-memory protocol/data-plan differs from canonical frozen files')
    if file_hash(protocol_file)!=plan['protocol_sha256']or selection.get('protocol_sha256')!=plan['protocol_sha256']or seal.get('protocol_sha256')!=plan['protocol_sha256']or selection.get('data_plan_sha256')!=file_hash(plan_file)or selection.get('data_plan_sha256')!=seal.get('data_plan_sha256'):
        raise DataBlocked('selection protocol/data-plan mismatch')
    if selection.get('calibration_dates')!=protocol['dates']['calibration'] or selection.get('train_dates')!=protocol['dates']['train'] or selection.get('devcheck_labels_read')is not False:
        raise DataBlocked('selection must be train/cal-only')
    artifacts=selection.get('artifacts')or{}
    methods={'HGB','TCN_ensemble','empirical','current_proxy'}
    if selection.get('forecast_display_method')not in methods|{None}or selection.get('policy_selected_method')not in methods|{None}:
        raise DataBlocked('unrecognized selection method')
    required={'HGB-Tq50','HGB-Tq90','HGB-Aq50','HGB-Aq90','TCN-42','TCN-314'}
    if set(artifacts.get('models',{}))!=required:raise DataBlocked('missing model/checkpoint freeze')
    for name,record in artifacts['models'].items():
        verify_record(record,root)
        fit=record.get('fit_receipt')
        if not fit:raise DataBlocked('missing successful fit receipt')
        receipt=json.loads(verify_record(fit,root).read_text())
        if receipt.get('status')!='completed' or receipt.get('exit_code')!=0 or receipt.get('protocol_sha256')!=plan['protocol_sha256'] or receipt.get('train_dates')!=protocol['dates']['train']:
            raise DataBlocked('model fit receipt incomplete or wrong training provenance')
        if receipt.get('model_sha256')!=record['sha256'] or not receipt.get('checkpoint_reload_passed'):
            raise DataBlocked('model/reload binding mismatch')
        if name.startswith('TCN') and receipt.get('weights_update_count',0)<=0:
            raise DataBlocked('TCN checkpoint has no actual weight update evidence')
    for name in ['preprocessor','corrections','cal_common_mask','cal_metrics']:
        if name not in artifacts:raise DataBlocked('missing selection calibration artifact')
        record=artifacts[name];content=json.loads(verify_record(record,root).read_text())
        if content.get('protocol_sha256')!=plan['protocol_sha256']:raise DataBlocked('selection artifact protocol mismatch')
        expected=protocol['dates']['train'] if name=='preprocessor'else protocol['dates']['calibration']
        if content.get('source_dates')!=expected:raise DataBlocked('selection artifact not train/cal-only')
    return selection


def load_trip_bags(plan, protocol, dates, root=ROOT, selection_path=None, return_metadata=False):
    allowed=validate_dates(protocol)
    if not set(dates)<=allowed: raise DataBlocked('requested dates outside exposed allowlist')
    if set(dates)&set(protocol['dates']['devcheck']):
        verify_selection_freeze(plan,protocol,selection_path,root)
    preflight_inputs(plan,protocol,root)
    bags={};metadata={'day_counts':{},'exact_row_duplicate_flags':{'true':0,'false':0,'unknown':0},
        'count_semantics':'Unexpanded primary trip rows;duplicates retained,not independent people/trips'}
    for day in dates:
        values={b:[] for b in range(360,600,15)};primary_rows=0
        with resolve(plan['source_rows'][day]['rows']['path'],root).open() as stream:
            for line in stream:
                row=json.loads(line)
                if not primary_admission(row,day):continue
                primary_rows+=1;duplicate=row.get('exact_row_duplicate')
                if duplicate is not None and not isinstance(duplicate,bool):raise DataBlocked('invalid duplicate flag')
                metadata['exact_row_duplicate_flags']['unknown'if duplicate is None else str(duplicate).lower()]+=1
                candidate=int(minute_on_day(row['entry_time'],day)//15)*15
                T,A=trip_targets(row,day,candidate)
                values[candidate].append((T,A,minute_on_day(row['exit_time'],day)))
        for candidate, rows in values.items(): bags[(day,candidate)]=np.asarray(rows,dtype=np.float64).reshape(-1,3)
        metadata['day_counts'][day]={'primary_rows_unexpanded':primary_rows,
            'bins':{str(k):len(v)for k,v in values.items()}}
    metadata['primary_rows_unexpanded']=sum(d['primary_rows_unexpanded']for d in metadata['day_counts'].values())
    multiplicities=Counter(n+s for n in NOMINALS for s in SHIFTS)
    metadata['cross_case_reuse']={'case_occurrences_per_candidate_minute':dict(sorted(multiplicities.items())),
        'expanded_trip_case_rows':sum(len(bag)*multiplicities[minute]for (day,minute),bag in bags.items()),
        'independent_day_count':len(dates)}
    return (bags,metadata)if return_metadata else bags


def calendar_features(day, nominal, shift):
    if nominal not in NOMINALS or shift not in SHIFTS: raise ValueError('case outside fixed schedule')
    candidate,issue=nominal+shift,nominal-45;weekday=dt.date.fromisoformat(day).weekday()
    row=[nominal,candidate,shift,issue,candidate-issue]+[int(weekday==i)for i in range(5)]+[int(nominal==x)for x in NOMINALS]+[int(shift==s)for s in (-30,-15,15,30)]+[int(day<='2026-06-30' or day>='2026-08-31')]
    assert len(row)==27
    return np.asarray(row,dtype=np.float32)


def case_features(observations, day, nominal, shift, extra_delay_minutes=0):
    if extra_delay_minutes not in (0,5,10):raise ValueError('only fixed extra delay scenarios0/5/10')
    issue=nominal-45;bucket=((issue-10-extra_delay_minutes)//5)*5;sequence=[]
    for lag in LAGS:
        minute=bucket-lag
        if minute+10+extra_delay_minutes>issue: raise ValueError('future revised bucket')
        for link in range(10):
            value=observations.get((day,minute,link))
            sequence.extend([*value,0]if value is not None else [np.nan,np.nan,np.nan,1])
    result=np.concatenate([np.asarray(sequence,dtype=np.float32),calendar_features(day,nominal,shift)])
    assert result.shape==(547,)
    return result


def trip_weights(day, n_trips, n_issues=12, n_candidates=5):
    if n_trips<=0:raise ValueError('empty bag has no zero-valued trip')
    return np.full(n_trips,1/(n_issues*n_candidates*n_trips),dtype=np.float64)


def live_available(receipt, issue_epoch):
    times=[stamp(receipt[k]).timestamp()for k in ('full_body_received_at','parse_finished_at')if receipt.get(k)]
    return len(times)==2 and all(math.isfinite(x) and x<=issue_epoch for x in times)
