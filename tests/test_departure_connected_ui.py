"""Pure JS and fake-DOM contracts; the snapshot path cannot issue fetch calls."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

VM = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const dir='prototype/departure-connected-2026-10-22/';let calls=0,timers=[];
const stamp='2026-10-22T12:00:00+08:00',day='2026-10-21';
const iso=epoch=>new Date(epoch+8*3600000).toISOString().replace('Z','+08:00');
const clock=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
const issues=Array.from({length:12},(_,i)=>{
 const n=390+15*i,I=Date.parse(day+'T'+clock(n-45)+':00+08:00'),D=n+45;
 return {schema_version:1,mode:'prospective_issue',as_of:stamp,day,nominal:clock(n),deadline:clock(D),cost:.5,
 issue_id:day+'-N'+String(n).padStart(4,'0'),issue_time:iso(I),status:'sealed_shadow_only',finished_at:iso(I+1000),sealed_at:iso(I+2000),
 forecast_display_method:'empirical',policy_qualified:false,policy_selected_method:null,truth_loaded:false,per_request_model_forward:false,
 candidates:[-30,-15,0,15,30].map(shift=>({shift_minutes:shift,window_start:iso(I+(45+shift)*60000),window_end:iso(I+(60+shift)*60000),
 prediction:{Tq50:20,Tq90:25,Aq50:25,Aq90:30},predicted_roadgain:0,shift_cost:.5*Math.abs(shift),predicted_netgain:-.5*Math.abs(shift),estimated_q90_within_deadline:n+shift+30<=D}))};
});
const snapshot={schema_version:1,delivery_mode:'sealed_snapshot',exported_at:stamp,issues,status:{schema_version:1,as_of:stamp,
 delivery_mode:'sealed_snapshot',scope:{gates:['01F0750S','01F0880S','01F0928S','01F0950S','01F0980S'],vehicle_class:'31',timezone:'Asia/Taipei'},
 qualification:{forecast_display_method:'empirical',policy_qualified:false},replay:{available_dates:['2026-08-24']},prospective_dates:['2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27'],
 current_observation:{status:'stopped',source_time:null,received_at:null,summary:'快照未連線',summary_en:'Snapshot disconnected',sources:[]},live_prediction:{status:'unavailable'}}};
const context={console,Date,URLSearchParams,TextEncoder,structuredClone,CONNECTED_EXPORT:snapshot,
 fetch:()=>{calls++;throw Error('network forbidden');},location:{search:''},setInterval:(fn,ms)=>{timers.push({fn,ms});}};
vm.createContext(context);vm.runInContext(fs.readFileSync(dir+'adapter.js','utf8').replace(/export /g,''),context);
const evaluate=s=>vm.runInContext(s,context),flush=()=>new Promise(r=>setImmediate(r));
'''


def node(script):
    result = subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True,
        env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)


class ConnectedUIContracts(unittest.TestCase):
    def test_cached_current_expiry_unknown_clock_and_no_revival(self):
        node(VM+r'''
evaluate(`const t0=Date.parse('2026-10-21T06:00:00+08:00');
 const fresh={as_of:isoEpoch('2026-10-21T06:00:00+08:00')&&'2026-10-21T06:00:00+08:00',
 current_observation:{status:'available',source_time:'2026-10-21T06:00:00+08:00',received_at:'2026-10-21T06:00:00+08:00',summary:'ok'},
 sources:[{dataset:'M04A',status:'available',source_time:'2026-10-21T06:00:00+08:00'}]};`);
assert.equal(evaluate('currentDisplay(fresh,t0+900000).status'),'available');
assert.equal(evaluate('currentDisplay(fresh,t0+901000).status'),'stale');
assert.equal(evaluate('currentDisplay(fresh,t0+901000).sources[0].status'),'stale');
assert.equal(evaluate('fresh.current_observation.status'),'available'); // cached evidence untouched
assert.equal(evaluate('currentDisplay(fresh,t0-1).status'),'not_ready');
evaluate("fresh.current_observation.status='stopped'");assert.equal(evaluate('currentDisplay(fresh,t0+901000).status'),'stopped');
evaluate(`const invalid={as_of:fresh.as_of,current_observation:{status:'data_insufficient',source_time:null,received_at:null,summary:'沒有有效研究列'},sources:[{dataset:'M04A',status:'parsed_invalid'}]};`);
assert.equal(evaluate('currentDisplay(invalid,t0+901000).status'),'data_insufficient');
assert.equal(evaluate('currentDisplay(invalid,t0+901000).sources[0].status'),'parsed_invalid');
evaluate(`const partial=structuredClone(fresh);partial.sources[0].status='data_insufficient';`);
assert.equal(evaluate('currentDisplay(partial,t0+901000).sources[0].status'),'stale');
assert.equal(calls,0);
''')

    def test_export_exact_identity_clocks_no_substitution_and_nulls(self):
        node(VM+r'''
(async()=>{
 await evaluate(`(async()=>{
 const api=new DepartureApi(),q={day:'2026-10-21',nominal:'06:30',deadline:'07:15',cost:.5};
 const initial=await api.prospective(q),original=JSON.stringify(initial.candidates.map(c=>c.prediction));
 const changed=deriveSealed(initial,{...q,cost:0,deadline:'00:00'});
 if(JSON.stringify(changed.candidates.map(c=>c.prediction))!==original)throw Error('prediction mutation');
 if(changed.as_of!==initial.as_of||changed.sealed_at!==initial.sealed_at||changed.issue_time!==initial.issue_time)throw Error('clock rewrite');
 if(changed.candidates.some(c=>c.estimated_q90_within_deadline!==false))throw Error('deadline mismatch');
 const missing=structuredClone(initial);missing.candidates[1].prediction=null;missing.candidates[1].predicted_roadgain=null;missing.candidates[1].predicted_netgain=null;missing.candidates[1].estimated_q90_within_deadline=null;
 const value=deriveSealed(missing,{...q,cost:1});if(value.candidates[1].predicted_roadgain!==null||value.candidates[1].predicted_netgain!==null)throw Error('null became zero');
 const absent=await api.prospective({...q,day:'2026-10-22'}),future=await api.prospective({...q,day:'2026-10-23'});
 if(absent.status!=='not_published'||absent.candidates.length||future.status!=='not_due'||future.candidates.length)throw Error('historical substitution');
 for(const kind of ['duplicate','missing','wrong-clock','wrong-identity']){
  const bad=structuredClone(globalThis.CONNECTED_EXPORT);
  if(kind==='duplicate')bad.issues[1]=structuredClone(bad.issues[0]);
  if(kind==='missing')bad.issues.pop();
  if(kind==='wrong-clock')bad.issues[0].as_of='2026-10-22T12:00:01+08:00';
  if(kind==='wrong-identity')bad.issues[0].nominal='06:45';
  let refused=false;try{validateExport(bad);}catch(e){refused=true;}if(!refused)throw Error('bad export accepted '+kind);
 }
 const p=sealedPreferences(initial,{advance:15,delay:30,minimumGain:5},Date.parse(initial.issue_time)+3000);
 if(p.filter(c=>c.matches_numeric_conditions).length!==0||p.length!==5)throw Error('minimum gate');
 const late=sealedPreferences(initial,{advance:30,delay:30,minimumGain:0},Date.parse(initial.candidates[0].window_end));
 if(late[0].can_tentatively_select||!late[0].window_elapsed)throw Error('expired action');
 if(p.some(c=>c.policy_qualified||c.complete_lateness_risk_delta!==null))throw Error('risk promotion');
 })()`);assert.equal(calls,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')

    def test_renderer_auto_sealed_button_enter_tick_and_language(self):
        node(VM+r'''
class Element {
 constructor(tag,text=''){this.tag=tag;this._text=text;this.children=[];this.events={};this.attributes={};this.dataset={};this.classList={add(){},remove(){},toggle(){}};this.style={};this._value='';}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join(' ');}set textContent(v){this._text=String(v);this.children=[];}
 get value(){return this._value;}set value(v){this._value=String(v);}
 append(...items){for(const e of items){this.children.push(e);e.parent=this;}}
 replaceChildren(...items){this.children=[];this._text='';this.append(...items);}
 addEventListener(name,fn){this.events[name]=fn;}setAttribute(k,v){this.attributes[k]=String(v);}focus(){context.document.activeElement=this;}
 checkValidity(){return this.valid!==false;}reportValidity(){this.validityReported=true;return this.checkValidity();}
}
const root=new Element('body'),all=e=>[e,...e.children.flatMap(all)],find=id=>all(root).find(e=>e.id===id);
for(const id of ['language','connection','workspace','brand-link','mode-tabs']){const e=new Element('div');e.id=id;root.append(e);}
context.document={getElementById:find,documentElement:{lang:'zh-Hant'},querySelectorAll:()=>[],createElement:tag=>new Element(tag),createTextNode:text=>new Element('#text',text),createElementNS:(ns,tag)=>new Element(tag)};
const visible=e=>e.tag==='pre'?'':e._text+' '+e.children.map(visible).join(' ');
vm.runInContext(fs.readFileSync(dir+'app.js','utf8').replace(/^import .*;\n/,'').replace(/export /g,''),context);
(async()=>{
 await flush();await flush();
 assert.equal(evaluate('state.mode'),'prospective');assert.equal(evaluate('state.prospective.response.status'),'sealed_shadow_only');
 assert.equal(all(find('workspace')).filter(e=>(e.className||'').split(' ').includes('candidate-card')).length,5);
 assert.match(visible(find('connection')),/已驗封存快照/);assert.match(visible(find('workspace')),/不可當作現在可採取/);
 assert.equal(evaluate('state.prospective.selectedShift'),null);assert.equal(find('read-sealed').type,'button');
 const saved=evaluate('JSON.stringify(state.prospective.response)');evaluate("changeLanguage('en')");
 assert.equal(evaluate('JSON.stringify(state.prospective.response)'),saved);assert.match(visible(find('workspace')),/not actionable options/);
 assert.equal(timers[0].ms,15000);timers[0].fn();assert.equal(evaluate('JSON.stringify(state.prospective.response)'),saved);
 assert(all(find('workspace')).filter(e=>e.id?.startsWith('sealed-pick')).every(e=>e.disabled));
 await find('read-sealed').events.click();const form=find('read-sealed').parent;
 let prevented=false;await form.events.keydown({key:'Enter',isComposing:false,target:{tagName:'INPUT'},preventDefault(){prevented=true;}});assert(prevented);
 await flush();const draft=find('prospective-deadline');draft.value='00:00';const draftForm=find('read-sealed').parent;
 await draftForm.events.keydown({key:'Enter',isComposing:false,target:{tagName:'INPUT'},preventDefault(){}});await flush();
 assert.equal(evaluate('state.prospective.response.deadline'),'00:00');assert(evaluate('state.prospective.response.candidates.every(c=>c.estimated_q90_within_deadline===false)'));
 const edit=find('prospective-deadline');edit.value='07:16';context.document.activeElement=edit;timers[0].fn();
 assert.equal(find('prospective-deadline'),edit);assert.equal(edit.value,'07:16');assert.equal(context.document.activeElement,edit);assert.notEqual(evaluate('state.prospective.response'),null);
 edit.value='';timers[0].fn();assert.equal(find('prospective-deadline'),edit);assert.equal(edit.value,'');
 edit.value='07:16';
 const latestForm=find('read-sealed').parent;latestForm.valid=false;await find('read-sealed').events.click();assert(latestForm.validityReported);
 const nominalControl=find('prospective-nominal');nominalControl.value='405';nominalControl.events.change();
 assert.equal(find('prospective-deadline').value,'07:30');assert.equal(evaluate('state.prospective.form.nominal'),'06:45');
 evaluate("state.mode='current';render()");assert.match(visible(find('workspace')),/Snapshot disconnected/);
 evaluate("changeLanguage('zh')");assert.match(visible(find('workspace')),/快照未連線/);assert.equal(calls,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
''')


if __name__=='__main__':unittest.main()
