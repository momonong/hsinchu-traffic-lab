// Pure controller / rendering contracts. No browser, source or model calls.
const assert = require('node:assert/strict');
const V = require('../prototype/sealed-viewer/app.js');
const day = '2026-10-21';
const asof = Date.parse(day+'T05:45:10+08:00');
function payload(d = day) {
  return {schema_version:1, day:d, as_of:new Date(asof).toISOString(), status:'available',
    verified_count:0, fixed_scheduled_issues:12, policy_qualified:false, recommendation:null,
    truth_loaded:false, slots:V.NOMINALS.map(n => ({issue_id:V.identity(d,n),nominal:V.hhmm(n),
      status:'not_published', candidates:[]}))};
}
const reply = data => ({ok:true,json:async()=>data});
async function run() {
  let jobs=[]; let calls=0; let now=asof;
  const controller=new V.Controller(()=>{calls++;return new Promise(resolve=>jobs.push(resolve));},()=>now);
  controller.setDay(day); const first=controller.refresh();
  controller.setDay('2026-10-22'); const second=controller.refresh();
  jobs[1](reply(payload('2026-10-22')));await second;
  jobs[0](reply(payload(day)));await first;
  assert.equal(controller.data.day,'2026-10-22','older request must not replace date');
  controller.setDay(day);const third=controller.refresh();controller.select(420);
  jobs[2](reply(payload()));await third;
  assert.equal(controller.nominal,420,'pending refresh must retain latest slot choice');
  assert.equal(controller.lastSuccessfulCheck,new Date(asof).toISOString());
  const failure=controller.refresh();jobs[3]({ok:false,status:503});await failure;
  assert.equal(controller.data,null,'failed refresh clears prior numbers');assert.ok(controller.error);
  assert.equal(controller.lastSuccessfulCheck,new Date(asof).toISOString());
  controller.setDay('2026-10-24');await controller.refresh();
  assert.equal(calls,4,'unscheduled day must not silently load historical day');
  assert.equal(controller.status,'not_scheduled');
  const row={window_end:new Date(asof+1000).toISOString(),window_elapsed:false};
  assert.equal(V.elapsed(row,asof),false);assert.equal(V.elapsed(row,asof+1000),true);
  assert.equal(V.elapsed({...row,window_elapsed:true},asof-100),true,'language/clock cannot resurrect elapsed archive');
  assert.equal(V.elapsed({window_end:'bad'},asof),true);
  assert.equal(V.number(null),'—');assert.equal(V.number(0),'0.00');
  assert.throws(()=>V.validate({...payload(),slots:payload().slots.slice(1)},day));
  assert.throws(()=>V.validate({...payload(),slots:Array(12).fill(payload().slots[0])},day));
  assert.throws(()=>V.validate({...payload(),truth_loaded:true},day));
  assert.equal(V.today(Date.parse('2026-10-20T17:00:00Z')),day);
  const zh=V.COPY.zh,en=V.COPY.en;
  for(const key of Object.keys(zh))assert.equal(typeof en[key],'string',`missing English ${key}`);
  assert.match(en.scope,/gantry/);assert.match(en.limits,/confidence interval/);
  assert.match(en.policy,/unqualified/);assert.match(zh.policy,/未合格/);
  assert.match(V.detailHTML({status:'integrity_error',candidates:[]},'en',asof),/verification/i);
  let focused=null;
  const oldButton={dataset:{nominal:'420'}},otherControl={dataset:{}};
  const newButton={focus(options){focused={button:this,options};}};
  const list={contains:el=>el===oldButton,querySelector:query=>query==='[data-nominal="420"]'?newButton:null};
  V.replaceSlotList(list,'new rows',oldButton);
  assert.equal(focused.button,newButton,'keyboard-selected slot retains focus after render');
  assert.equal(focused.options.preventScroll,true);
  focused=null;V.replaceSlotList(list,'new rows',otherControl);
  assert.equal(focused,null,'async render must not steal focus from a different control');
  console.log('sealed viewer UI: race, failure, identity, expiry, unknown, bilingual contracts passed');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
