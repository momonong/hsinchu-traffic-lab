"""Offline contracts: no real source, credentials, host service or package install."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import io

REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'reports/xinglong-host-peak-2026-10-27'
FIXTURE=REPO/'tests/fixtures/host_peak_program.json'
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
H=load('old_fake',REPO/'reports/xinglong-public-feed-2026-10-23/test_once_probe.py')
M=load('host_runner',HERE/'host_runner.py') if (HERE/'host_runner.py').exists() else None
class Clock:
 def __init__(self,stamp='2026-10-27T17:00:00+08:00'):
  self.wall=dt.datetime.fromisoformat(stamp);self.seconds=0.
 def now(self):return self.wall
 def mono(self):return self.seconds
 def sleep(self,n):self.wall+=dt.timedelta(seconds=n);self.seconds+=n
 def set(self,stamp):self.wall=dt.datetime.fromisoformat(stamp)

def responses(clock,tt=120):
 static,live=H.F.fixture();end=clock.now()-dt.timedelta(seconds=10)
 live.update(UpdateTime=end.isoformat(),SrcUpdateTime=end.isoformat())
 for row in live['ETagPairLives']:
  row.update(StartTime=(end-dt.timedelta(minutes=5)).isoformat(),EndTime=end.isoformat(),DataCollectTime=end.isoformat())
  row['Flows'][0]['TravelTime']=tt
 return [H.Response({'access_token':'FAKE_SECRET_TOKEN'}),H.Response(static),H.Response(live)]

class HostContracts(unittest.TestCase):
 def setup_stage(self,temp):
  self.assertIsNotNone(M,'New fixed-date host runner is absent')
  s=Path(temp)
  program=json.loads(FIXTURE.read_text());program['repository_root']=str(REPO)
  (s/'program.json').write_text(json.dumps(program))
  for name in ('host_runner.py','candidate.html.tmpl'):
   shutil.copyfile(HERE/name,s/name)
  return s
 def runner(self,s,clock,opener=None,credentials=None,env=None):
  return M.Runner(s,now=clock.now,monotonic=clock.mono,sleep=clock.sleep,
   opener=opener,credentials=credentials or H.fake_credentials,
   environment={'INVOCATION_ID':'a'*32,'XINGLONG_UNIT':'test.service'} if env is None else env)
 def admission(self,s,c):
  r=self.runner(s,c)
  schedule={'schema_version':1,'id':'test','target':c.now().isoformat(),
   'program_sha256':M.digest(s/'program.json'),'runtime_hashes':r.hashes(),
   'unit':'test.service','settings_evidence':'synthetic-only-not-installed'}
  (s/'rehearsal-schedule.json').write_text(json.dumps(schedule))
  proof=r.rehearsal(s/'rehearsal-schedule.json')
  self.assertEqual(proof['status'],'passed_on_time')
  admission={'approved':True,'modes':['source'],'runtime_hashes':r.hashes(),
   'program_sha256':M.digest(s/'program.json'),'rehearsal_root_verified':True,
   'rehearsal_receipt':{'path':'rehearsal-test/receipt.json','sha256':M.digest(s/'rehearsal-test/receipt.json')}}
  (s/'root-source-admission.json').write_text(json.dumps(admission));return s/'root-source-admission.json'
 def test_rehearsal_network_free_real_identity_and_exclusive_receipt(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();op=H.Opener([AssertionError('network forbidden')])
   r=self.runner(s,c,op,credentials=lambda:(_ for _ in ()).throw(AssertionError('credential forbidden')))
   config={'schema_version':1,'id':'timed','target':c.now().isoformat(),'program_sha256':M.digest(s/'program.json'),
    'runtime_hashes':r.hashes(),'unit':'test.service'}
   q=s/'schedule.json';q.write_text(json.dumps(config));c.sleep(5)
   result=r.rehearsal(q)
   self.assertEqual(result['status'],'passed_on_time');self.assertEqual(result['delay_seconds'],5)
   self.assertFalse(op.calls);self.assertEqual(result['HTTP'],0)
   self.assertEqual(result['process']['pid'],os.getpid());self.assertTrue(result['process']['start_ticks'])
   self.assertEqual(result['invocation_id'],'a'*32)
   before=(s/'rehearsal-timed/receipt.json').read_bytes()
   self.assertEqual(r.rehearsal(q)['status'],'already_recorded')
   self.assertEqual((s/'rehearsal-timed/receipt.json').read_bytes(),before)
 def test_rehearsal_late_and_manual_context_cannot_pass(self):
  for delay in (-1,5.001):
   with tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();r=self.runner(s,c)
    q=s/'q.json';q.write_text(json.dumps({'id':'one','target':c.now().isoformat(),
     'program_sha256':M.digest(s/'program.json'),'runtime_hashes':r.hashes(),'unit':'test.service'}))
    c.sleep(delay);result=r.rehearsal(q);self.assertNotEqual(result['status'],'passed_on_time')
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();r=self.runner(s,c,env={})
   with self.assertRaisesRegex(M.Guard,'systemd'):r.rehearsal(s/'q.json')
 def test_source_early_grace_boundaries_no_credentials_when_missed(self):
  for stamp,want in [('17:29:59','not_due'),('17:30:05','single_shot_complete'),
                     ('17:30:06','single_shot_complete'),('17:31:00','single_shot_complete'),
                     ('17:31:00.001','missed_launch_grace')]:
   with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T'+stamp+'+08:00');op=H.Opener(responses(c))
    calls=[];r=self.runner(s,c,op,credentials=lambda:(calls.append(1) or H.fake_credentials()))
    result=r.source('1730',a);self.assertEqual(result['status'],want)
    if want!='single_shot_complete':self.assertFalse(calls);self.assertFalse(op.calls)
    else:self.assertEqual(result['timing_status'],'on_time' if stamp=='17:30:05' else 'late_started')
 def test_shared_reservations_two_slots_and_same_slot_idempotent(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   op=H.Opener(responses(c));r=self.runner(s,c,op);one=r.source('1730',a)
   self.assertEqual(one['status'],'single_shot_complete');self.assertEqual(len(op.calls),3)
   self.assertEqual(r.source('1730',a)['status'],'already_recorded');self.assertEqual(len(op.calls),3)
   c.set('2026-10-27T18:30:00+08:00');op2=H.Opener(responses(c));r=self.runner(s,c,op2)
   two=r.source('1830',a);self.assertEqual(two['status'],'single_shot_complete')
   ledger=json.loads((s/'ledger.json').read_bytes());self.assertEqual(ledger['HTTP_reserved'],6)
   self.assertEqual(len(op2.calls),3)
 def test_unknown_or_access_refusal_blocks_second_no_credentials(self):
  for kind in ('unknown','timeout',401,402,403,429):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
    if kind=='unknown':
     (s/'slot-1730').mkdir();(s/'slot-1730/reservation.json').write_text('{}')
    else:
     error=TimeoutError() if kind=='timeout' else urllib.error.HTTPError('https://tdx.transportdata.tw',kind,'',{},io.BytesIO())
     self.runner(s,c,H.Opener([error])).source('1730',a)
    c.set('2026-10-27T18:30:00+08:00');op=H.Opener(responses(c));calls=[]
    result=self.runner(s,c,op,credentials=lambda:(calls.append(1) or H.fake_credentials())).source('1830',a)
    self.assertTrue(result['status'].startswith('blocked_prior'));self.assertFalse(calls);self.assertFalse(op.calls)
 def test_clock_rollback_and_forward_jump_stop_without_second_request(self):
  for seconds in (-2,6,1000):
   with self.subTest(seconds=seconds),tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
    class Jump(H.Opener):
     def open(self,request,timeout):
      result=super().open(request,timeout);c.wall+=dt.timedelta(seconds=seconds);return result
    op=Jump(responses(c));r=self.runner(s,c,op);result=r.source('1730',a)
    self.assertEqual(len(op.calls),1);self.assertFalse(result['request_outcomes_known'])
    self.assertFalse(result['measurement_usable'])
 def test_admission_code_or_program_or_UI_mismatch_never_calls_credential(self):
  for name in ('host_runner.py','candidate.html.tmpl','program.json'):
   with self.subTest(name=name),tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
    (s/name).write_text((s/name).read_text()+'\n');op=H.Opener(responses(c))
    with self.assertRaises(M.Guard):self.runner(s,c,op).source('1730',a)
    self.assertFalse(op.calls)
 def test_finalize_no_network_missing_latest_no_old_good_substitution(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   self.runner(s,c,H.Opener(responses(c))).source('1730',a)
   c.set('2026-10-27T19:00:00+08:00');op=H.Opener([AssertionError('no HTTP')])
   result=self.runner(s,c,op).finalize()
   self.assertEqual(result['slots']['1830']['status'],'missed_not_started')
   self.assertIsNone(result['candidate']['frame']);self.assertEqual(result['candidate']['slot'],'1830')
   self.assertFalse(op.calls);self.assertFalse((s/'slot-1830/intent.json').exists())
   self.assertIsNone(result['ETA_0_15_30']);self.assertIsNone(result['benefit'])
 def test_older_unknown_not_relabelled_missing_by_finalize(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();(s/'slot-1730').mkdir();(s/'slot-1730/reservation.json').write_text('{}')
   c.set('2026-10-27T19:00:00+08:00');result=self.runner(s,c).finalize()
   self.assertEqual(result['slots']['1730']['status'],'unknown_attempt')
 def test_stage_budget_blocks_network_before_credential(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   (s/'fill').write_bytes(b'x'*850000);op=H.Opener(responses(c));calls=[]
   with self.assertRaisesRegex(M.Guard,'space|cap'):
    self.runner(s,c,op,credentials=lambda:(calls.append(1) or H.fake_credentials())).source('1730',a)
   self.assertFalse(op.calls);self.assertFalse(calls)
 def test_ui_expiry_nulls_and_latest_invalid_via_real_generated_script(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   self.runner(s,c,H.Opener(responses(c))).source('1730',a)
   page=(s/'candidate.html').read_text();self.assertNotIn('__PAYLOAD__',page)
   script=page.rsplit('<script>',1)[1].split('</script>',1)[0]
   check="""const assert=require('node:assert/strict');const t=Date.parse(PAYLOAD.as_of);assert.equal(countyView(PAYLOAD,t).segments[0].status,'available');assert.equal(countyView(PAYLOAD,t+901000).segments[0].status,'stale');assert.equal(countyView(PAYLOAD,t-1).segments[0].status,'clock_invalid');const bad=structuredClone(PAYLOAD);bad.frame.segments[0].usable_asof=false;assert.notEqual(countyView(bad,t).segments[0].status,'available');assert.equal(PAYLOAD.ETA_0_15_30,null);assert.equal(PAYLOAD.recommendation,null);"""
   run=subprocess.run(['node','-e',script+'\n'+check],capture_output=True,text=True,timeout=5)
   self.assertEqual(run.returncode,0,run.stderr)

class ReviewContracts(unittest.TestCase):
 setup_stage=HostContracts.setup_stage
 runner=HostContracts.runner
 admission=HostContracts.admission
 def test_transport_tampering_blocks_next_despite_claimed_known(self):
  for kind in ('outcome','probe','request'):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as t:
    s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
    self.runner(s,c,H.Opener(responses(c))).source('1730',a)
    q=s/'slot-1730'/({'outcome':'outcome.json','probe':'receipt.json','request':'request-1-receipt.json'}[kind])
    obj=json.loads(q.read_text())
    if kind=='outcome':obj['slot']='1830';obj['request_outcomes_known']=True
    elif kind=='probe':obj['code_sha256']='0'*64
    else:obj['request_started_at']='2026-10-27T17:00:00+08:00'
    q.write_text(json.dumps(obj))
    if kind=='probe':
     outcome=s/'slot-1730/outcome.json';v=json.loads(outcome.read_text());v['probe_receipt_sha256']=M.digest(q);outcome.write_text(json.dumps(v))
    c.set('2026-10-27T18:30:00+08:00');op=H.Opener(responses(c))
    result=self.runner(s,c,op).source('1830',a)
    self.assertTrue(result['status'].startswith('blocked_prior'));self.assertFalse(op.calls)
 def test_old_measurement_damage_does_not_break_new_publish_or_finalize(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   self.runner(s,c,H.Opener(responses(c))).source('1730',a)
   (s/'slot-1730/live-diagnostic.json').write_text('{}')
   c.set('2026-10-27T18:30:00+08:00');op=H.Opener(responses(c));r=self.runner(s,c,op)
   self.assertEqual(r.source('1830',a)['status'],'single_shot_complete');self.assertEqual(len(op.calls),3)
   data=json.loads((s/'current.json').read_text());self.assertIsNotNone(data['frame']);self.assertIsNone(data['new_observation_window_support'])
   c.set('2026-10-27T19:00:00+08:00');self.assertEqual(r.finalize()['status'],'finalized_network_free')
 def test_diagnostic_path_must_match_entire_filename(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00')
   self.runner(s,c,H.Opener(responses(c))).source('1730',a)
   q=s/'slot-1730/receipt.json';obj=json.loads(q.read_text());obj['diagnostic_file']='../live-diagnostic.json';q.write_text(json.dumps(obj))
   outcome=s/'slot-1730/outcome.json';v=json.loads(outcome.read_text());v['probe_receipt_sha256']=M.digest(q);outcome.write_text(json.dumps(v))
   with self.assertRaises(M.Guard):self.runner(s,c).frame('1730')
 def test_preflight_clock_jump_keeps_original_anchor(self):
  with tempfile.TemporaryDirectory() as t:
   s=self.setup_stage(t);c=Clock();a=self.admission(s,c);c.set('2026-10-27T17:30:00+08:00');op=H.Opener(responses(c))
   r=self.runner(s,c,op);original=r.admission
   def admission(path):
    result=original(path);c.wall+=dt.timedelta(seconds=6);return result
   r.admission=admission;result=r.source('1730',a)
   self.assertFalse(op.calls);self.assertFalse(result['request_outcomes_known']);self.assertFalse(result['measurement_usable'])

if __name__=='__main__':unittest.main()
