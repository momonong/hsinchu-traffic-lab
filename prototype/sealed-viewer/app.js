/* Read-only sealed timetable. No inference, truth endpoint or source polling. */
(function (root) {
  'use strict';
  const DAYS = ['2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27'];
  const NOMINALS = Array.from({length:12}, (_,i) => 390+i*15);
  const hhmm = n => `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
  const identity = (day,n) => `${day}-N${String(n).padStart(4,'0')}`;
  const COPY = {
    zh: {
      brand:'通勤時窗研究', language:'語言', title:'封存預測結果', controls:'日期與刷新',
      question:'這一天有哪些預測通過封存驗證？哪些時窗仍無法比較？',
      readonly:'唯讀 · 未合格策略', skip:'跳至時窗詳情', day:'研究日期（台灣）',
      refresh:'重新核對封存', checked:'上次成功核對伺服器', verified:'已驗封存／固定應發',
      slots:'12 個原首門架時窗', slotsNote:'點選查看發出當時的封存資料。', details:'時窗詳情',
      scopeTitle:'這些數字適用於哪裡？',
      scope:'國1南下 75→88→92.8→95→98 公里五門架、小客車代碼31；比較首門架通過時窗，並非住家到公司全程。',
      fixed:'固定研究條件：末門架截止＝原時窗＋45分鐘；調時成本 λ＝0.5 分鐘等值／位移分鐘。empirical 是凍結的歷史分布基準。',
      limits:'q50／q90 是分位數估計，不是信賴區間或個人準時機率。沒有載入真值；這裡不產生建議，也不證明節時或照常最佳。',
      policy:'策略未合格；數值僅供研究回看，不代表照常已被證明最佳。',
      loading:'正在核對選定日期的封存資料…', failure:'核對失敗，先前數值已清除。請重試；不會改用其他日期或舊值。',
      unavailable:'研究狀態檔缺失或無法驗證；目前無法判斷封存數量。',
      notScheduled:'這一天不在固定五日排程內。請明確選擇一個研究日期；不會自動改看歷史資料。',
      available:'只計通過原封存驗證的預測。未發、尚未到期與驗證失敗分別保留。',
      partial:'部分時點驗證失敗，已隔離該筆數值；其餘時點仍逐筆核對。',
      choose:'請選擇時窗查看詳情。', issue:'預定發出', sealed:'完成封存', finished:'推論完成（當時）',
      deadline:'末門架截止', sourceDetail:'發出當時的來源與完整度', coverage:'有效配對格／固定格',
      links:'通過輸入門檻的 context 路段', unknown:'未知', noSources:'沒有可展示的已驗來源摘要。',
      sourceRange:'來源觀測區間終點（最早 → 最新）', receiptRange:'完整接收時間（最早 → 最新）',
      parseRange:'解析完成時間（最早 → 最新）', receipts:'封存輸入收據',
      sourceNote:'時間取自封存輸入收據；來源區間為終點前5分鐘。這是發出當時的資料，不是現在路況。',
      noValues:'此時點沒有可展示的已驗五候選；未用其他時點頂替。',
      expired:'已過窗 · 僅供封存回看', future:'尚未過窗 · 仍非可採取建議',
      window:'首門架通過時窗', heads:'走廊車程 q50 / q90', arrival:'末門架抵達估計 q50 / q90',
      gain:'相對原窗道路時間差', penalty:'位移成本', net:'扣位移成本的預測淨差', margin:'q90 符合截止',
      tableNote:'單位：分鐘；抵達為台灣時間。數值保留原封存估計，沒有重新推論。',
      original:'原時窗', early:'提前', late:'延後', yes:'是（非保證）', no:'否',
      seconds:'秒', serverAge:'距上次核對', checking:'等待核對', reason:'原因',
      sealed_shadow_only:'已驗封存 · 研究影子預測', not_due:'尚未到發出時間', not_published:'沒有已發布封存',
      inflight:'正在處理，尚未封存', inflight_reserved:'已保留處理，尚未封存',
      insufficient_input:'輸入不足，未提供預測', unresolved_after_restart:'重啟後未決，未補發',
      unknownStatus:'狀態未知', integrity_error:'驗證失敗，不展示數值', unavailableStatus:'研究狀態不可用',
      missed:'錯過發出時點', missed_startup:'啟動時已錯過', missed_dispatch:'派發逾時', late_output:'逾時產出，未作有效預測', failed:'處理失敗',
      missed_dispatch_window:'錯過派發窗口，未補發', missed_restart_no_backfill:'重啟時已錯過，未補發', missed_model_warmload:'模型預載未及，未補發',
      codes:'輸入門檻原因代碼', interval:'最新有效來源距發出時間', currentNotLoaded:'本頁不讀取現在來源或成熟真值。'
    },
    en: {
      brand:'Hsinchu commute windows', language:'Language', title:'Sealed forecast results', controls:'Date and refresh',
      question:'Which forecasts are verified for this date, and why can a window be compared or not?',
      readonly:'Read only · Unqualified policy', skip:'Skip to window details', day:'Study date (Taipei)',
      refresh:'Verify archives again', checked:'Last successful server check', verified:'Verified / scheduled issues',
      slots:'12 nominal first-gantry windows', slotsNote:'Choose a window to inspect what was sealed at issue time.', details:'Window details',
      scopeTitle:'What do these estimates cover?',
      scope:'National Freeway 1 southbound, five gantries at 75→88→92.8→95→98 km, vehicle class 31. These are first-gantry crossing windows, not a door-to-office commute.',
      fixed:'Fixed research conditions: final-gantry deadline = nominal window +45 minutes; shift cost λ =0.5 minute-equivalents per shifted minute. Empirical is the frozen historical-distribution baseline.',
      limits:'q50 / q90 are quantile estimates, not a confidence interval or an individual on-time probability. Truth is not loaded. This viewer provides no recommendation, proven time saving, or proof that the nominal window is best.',
      policy:'The policy remains unqualified. These are research archives; keeping the nominal window is not proven optimal.',
      loading:'Verifying archives for the selected date…', failure:'Verification failed. Previous values were cleared. Retry; no other date or older value will be substituted.',
      unavailable:'Study state is missing or unverified. The verified count is currently unknown.',
      notScheduled:'This date is outside the fixed five-day schedule. Explicitly choose a study date; historical data will not be selected automatically.',
      available:'Only forecasts passing the original publication checks are counted. Unpublished, not-yet-due and integrity failures remain separate.',
      partial:'Some issue artifacts failed verification and their values are hidden. Other issues are checked separately.',
      choose:'Choose a window to inspect its details.', issue:'Scheduled issue', sealed:'Seal completed', finished:'Forward completed (then)',
      deadline:'Final-gantry deadline', sourceDetail:'Sources and coverage at issue time', coverage:'Valid paired / fixed link slots',
      links:'Context links passing the input gate', unknown:'Unknown', noSources:'No verified source summary is available for display.',
      sourceRange:'Source interval ends (earliest → latest)', receiptRange:'Complete receipts (earliest → latest)',
      parseRange:'Parse completions (earliest → latest)', receipts:'Sealed input receipts',
      sourceNote:'Clocks come from sealed input receipts. Each source interval is the five minutes before its end. This describes inputs at issue time, not current traffic.',
      noValues:'No verified five-candidate values are available for this issue. Other issues are not substituted.',
      expired:'Window elapsed · Archive only', future:'Window not elapsed · Still no actionable advice',
      window:'First-gantry crossing window', heads:'Corridor duration q50 / q90', arrival:'Final-gantry arrival q50 / q90',
      gain:'Road-time difference vs nominal', penalty:'Shift cost', net:'Predicted net difference after shift cost', margin:'q90 meets deadline',
      tableNote:'Units: minutes; arrivals in Taipei time. Original sealed estimates, with no new forward pass.',
      original:'Nominal', early:'Earlier', late:'Later', yes:'Yes (not guaranteed)', no:'No',
      seconds:'s', serverAge:'Since check', checking:'Awaiting verification', reason:'Reason',
      sealed_shadow_only:'Verified seal · Research shadow', not_due:'Issue time not reached', not_published:'No published seal',
      inflight:'Processing, not sealed', inflight_reserved:'Reserved, not sealed',
      insufficient_input:'Insufficient input; no forecast', unresolved_after_restart:'Unresolved after restart; no backfill',
      unknownStatus:'Unknown status', integrity_error:'Verification failed; values hidden', unavailableStatus:'Study state unavailable',
      missed:'Issue missed', missed_startup:'Already missed at startup', missed_dispatch:'Dispatch missed', late_output:'Late output; not a valid forecast', failed:'Processing failed',
      missed_dispatch_window:'Dispatch window missed; no backfill', missed_restart_no_backfill:'Missed at restart; no backfill', missed_model_warmload:'Model warmload missed; no backfill',
      codes:'Input-gate reason codes', interval:'Latest valid source age at issue', currentNotLoaded:'This page does not read current sources or mature truth.'
    }
  };
  function today(now=Date.now()) {
    return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
  }
  function elapsed(row,now) {
    return row.window_elapsed === true || !Number.isFinite(Date.parse(row.window_end)) || Date.parse(row.window_end)<=now;
  }
  const number = value => typeof value==='number' && Number.isFinite(value) ? value.toFixed(2) : '—';
  const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function stamp(value,lang='zh',timeOnly=false) {
    if(!value || !Number.isFinite(Date.parse(value)))return '—';
    const options={timeZone:'Asia/Taipei',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'};
    if(!timeOnly)Object.assign(options,{year:'numeric',month:'2-digit',day:'2-digit'});
    return new Intl.DateTimeFormat(lang==='en'?'en-GB':'zh-TW',options).format(Date.parse(value));
  }
  function statusText(status,lang) {
    const c=COPY[lang];
    if(status==='unavailable')return c.unavailableStatus;
    return c[status] || (status?.startsWith('unresolved_')?c.unresolved_after_restart:c.unknownStatus);
  }
  function validate(value,day) {
    if(!value || value.schema_version!==1 || value.day!==day || !Number.isFinite(Date.parse(value.as_of)) ||
       value.policy_qualified!==false || value.recommendation!==null || value.truth_loaded!==false)throw Error('invalid_response');
    if(!DAYS.includes(day)) {
      if(value.status!=='not_scheduled' || value.slots.length!==0)throw Error('invalid_schedule');
      return value;
    }
    if(!Array.isArray(value.slots) || value.slots.length!==12)throw Error('invalid_slots');
    value.slots.forEach((s,i)=>{
      if(s.issue_id!==identity(day,NOMINALS[i]) || s.nominal!==hhmm(NOMINALS[i]) || !Array.isArray(s.candidates))throw Error('invalid_identity');
      if(s.status==='sealed_shadow_only' && s.candidates.length!==5)throw Error('invalid_candidates');
    });
    return value;
  }
  class Controller {
    constructor(fetcher,clock=Date.now,onChange=()=>{}) {
      this.fetcher=fetcher;this.clock=clock;this.onChange=onChange;this.day=today(clock());this.nominal=390;
      this.serial=0;this.loading=false;this.data=null;this.error=null;this.status='checking';
      this.lastSuccessfulCheck=null;this.offset=0;this.highWater=-Infinity;
    }
    setDay(day) {
      this.serial++;this.day=day;this.data=null;this.error=null;this.loading=false;
      this.status=DAYS.includes(day)?'checking':'not_scheduled';this.onChange();
    }
    select(n) {if(NOMINALS.includes(n))this.nominal=n;this.onChange();}
    serverNow() {this.highWater=Math.max(this.highWater,this.clock()+this.offset);return this.highWater;}
    async refresh() {
      const id=++this.serial,day=this.day;
      this.data=null;this.error=null;
      if(!DAYS.includes(day)){this.loading=false;this.status='not_scheduled';this.onChange();return;}
      this.loading=true;this.status='loading';this.onChange();
      try {
        const response=await this.fetcher('/api/day?day='+encodeURIComponent(day),{cache:'no-store',redirect:'error',headers:{Accept:'application/json'}});
        const data=await response.json();
        if(!response.ok && data.status!=='unavailable')throw Error('verification_failed');
        validate(data,day);
        if(id!==this.serial || day!==this.day)return;
        if(!response.ok)throw Error('state_unavailable');
        this.data=data;this.status=data.status;this.offset=Date.parse(data.as_of)-this.clock();
        this.highWater=Date.parse(data.as_of);this.lastSuccessfulCheck=data.as_of;
      } catch(error) {
        if(id!==this.serial || day!==this.day)return;
        this.data=null;this.error=error.message==='state_unavailable'?'unavailable':'failure';this.status='error';
      } finally {if(id===this.serial){this.loading=false;this.onChange();}}
    }
  }
  function range(source,field,lang){return `${stamp(source['earliest_'+field],lang)} → ${stamp(source['latest_'+field],lang)}`;}
  function replaceSlotList(container,html,activeElement) {
    const nominal=container.contains(activeElement)?Number(activeElement?.dataset?.nominal):NaN;
    container.innerHTML=html;
    if(NOMINALS.includes(nominal)) {
      container.querySelector(`[data-nominal="${nominal}"]`)?.focus({preventScroll:true});
    }
  }
  function sourceHTML(slot,lang) {
    const c=COPY[lang],p=slot.input_coverage;
    let html='';
    if(p){html+=`<p>${c.coverage}: <strong>${p.valid_link_slots??'—'} / ${p.fixed_link_slots??'—'}</strong> · ${c.links}: ${p.eligible_links??'—'} / ${p.context_links??'—'}</p>`;
      const reasons=[...new Set((p.links||[]).flatMap(x=>x.reason_codes))];
      if(reasons.length)html+=`<p>${c.codes}: ${escape(reasons.join(', '))}</p>`;
    }
    const sources=slot.source_inputs||[];
    html+=sources.map(s=>`<div class="source"><h4>${escape(s.dataset)} · ${c.receipts} ${s.receipts}</h4><p>${c.sourceRange}<br>${range(s,'source_time',lang)}</p><p>${c.receiptRange}<br>${range(s,'received_at',lang)}</p><p>${c.parseRange}<br>${range(s,'parsed_at',lang)}</p></div>`).join('');
    if(!sources.length)html+=`<p>${c.noSources}</p>`;
    return `<details><summary>${c.sourceDetail}</summary>${html}<p class="muted">${c.sourceNote}</p></details>`;
  }
  function detailHTML(slot,lang,now) {
    const c=COPY[lang];
    if(!slot)return `<p class="empty">${c.choose}</p>`;
    const notice=`<p class="notice">${c.policy}</p>`;
    let html=`<div class="slot-heading"><div><h3>${escape(slot.nominal||'—')}</h3><p class="muted">${escape(slot.issue_id||'')}</p></div><span class="status-mark">${statusText(slot.status,lang)}</span></div>`;
    html+=`<dl class="clocks">${[[c.issue,slot.issue_time],[c.sealed,slot.sealed_at],[c.finished,slot.finished_at],[c.deadline,slot.deadline]].map(([label,value])=>`<div><dt>${label}</dt><dd>${label===c.deadline?escape(value||'—'):stamp(value,lang)}</dd></div>`).join('')}</dl>${notice}`;
    if(slot.status!=='sealed_shadow_only')return html+`<p class="empty">${c.noValues}</p>`+sourceHTML(slot,lang);
    const rows=slot.candidates.map(row=>{
      const p=row.prediction,expiry=elapsed(row,now),shift=row.shift_minutes;
      const name=shift===0?c.original:`${shift<0?c.early:c.late} ${Math.abs(shift)} min`;
      const arrival=q=>{
        const value=p?new Date(Date.parse(row.window_start)+p[q]*60000):null;
        return value && Number.isFinite(value.getTime())?stamp(value.toISOString(),lang,true):'—';
      };
      const flag=row.estimated_q90_within_deadline;
      return `<tr><td>${name}<small>${stamp(row.window_start,lang,true)}–${stamp(row.window_end,lang,true)}</small><small class="${expiry?'expired':''}">${expiry?c.expired:c.future}</small></td><td>${number(p?.Tq50)} / ${number(p?.Tq90)}</td><td>${arrival('Aq50')} / ${arrival('Aq90')}</td><td>${number(row.predicted_roadgain)}</td><td>${number(row.shift_cost)}</td><td>${number(row.predicted_netgain)}</td><td>${flag===null?c.unknown:flag?c.yes:c.no}</td></tr>`;
    }).join('');
    html+=`<div class="table-scroll" tabindex="0" role="region" aria-label="${escape(c.window)}"><table><caption>${c.tableNote}</caption><thead><tr>${[c.window,c.heads,c.arrival,c.gain,c.penalty,c.net,c.margin].map(x=>`<th scope="col">${x}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
    return html+sourceHTML(slot,lang);
  }
  function start() {
    const $=id=>document.getElementById(id);
    let lang=new URLSearchParams(location.search).get('lang')==='en'?'en':'zh';
    const controller=new Controller((...args)=>fetch(...args),Date.now,render);
    function render() {
      const c=COPY[lang];document.documentElement.lang=lang==='en'?'en':'zh-Hant';
      document.title=c.title+' · '+c.brand;
      document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=c[el.dataset.i18n]);
      $('toolbar').setAttribute('aria-label',c.controls);
      $('language').value=lang;
      const dates=DAYS.includes(controller.day)?DAYS:[controller.day,...DAYS];
      $('day').innerHTML=dates.map(day=>`<option value="${day}"${day===controller.day?' selected':''}>${day}${DAYS.includes(day)?'':lang==='en'?' · Not scheduled':' · 未排程'}</option>`).join('');
      $('refresh').disabled=controller.loading;
      $('alert').hidden=!controller.error;$('alert').textContent=controller.error?c[controller.error]:'';
      $('checked').textContent=stamp(controller.lastSuccessfulCheck,lang);
      const count=controller.data?.verified_count;
      $('verified').textContent=(count===null || count===undefined?'—':count)+' / '+(controller.status==='not_scheduled'?'—':'12');
      $('day-state').textContent=c[controller.error || (controller.status==='partial_integrity_error'?'partial':controller.status)]||c.checking;
      const slots=controller.data?.slots||[];
      const active=NOMINALS.indexOf(controller.nominal);
      const slotHTML=controller.status==='not_scheduled'?`<p class="empty">${c.notScheduled}</p>`:NOMINALS.map((n,i)=>`<button class="slot" type="button" data-nominal="${n}" aria-pressed="${n===controller.nominal}"><time>${hhmm(n)}</time><span>${slots[i]?statusText(slots[i].status,lang):controller.loading?c.loading:c.unknownStatus}</span></button>`).join('');
      replaceSlotList($('slots'),slotHTML,document.activeElement);
      $('detail-content').innerHTML=detailHTML(slots[active],lang,controller.serverNow());
      tick();
    }
    function tick() {
      const now=controller.serverNow(),c=COPY[lang];
      $('server-age').textContent=controller.lastSuccessfulCheck?`${c.serverAge} ${Math.max(0,Math.floor((now-Date.parse(controller.lastSuccessfulCheck))/1000))} ${c.seconds}`:'';
      const slot=controller.data?.slots.find(s=>s.nominal===hhmm(controller.nominal));
      // Only time labels change; no request, date control rebuild or focus loss.
      if(slot?.status==='sealed_shadow_only') {
        document.querySelectorAll('tbody tr').forEach((row,i)=>{
          const el=row.querySelector('td small:last-child');if(!el)return;
          const expired=elapsed(slot.candidates[i],now);el.textContent=expired?c.expired:c.future;el.className=expired?'expired':'';
        });
      }
    }
    $('day').addEventListener('change',event=>{controller.setDay(event.target.value);controller.refresh();});
    $('refresh').addEventListener('click',()=>controller.refresh());
    $('language').addEventListener('change',event=>{lang=event.target.value==='en'?'en':'zh';render();});
    $('slots').addEventListener('click',event=>{const el=event.target.closest('[data-nominal]');if(el)controller.select(Number(el.dataset.nominal));});
    render();controller.refresh();setInterval(tick,1000);
  }
  const API={DAYS,NOMINALS,hhmm,identity,COPY,today,elapsed,number,validate,Controller,detailHTML,replaceSlotList};
  if(typeof module!=='undefined' && module.exports)module.exports=API;
  else {root.SealedViewer=API;document.addEventListener('DOMContentLoaded',start);}
})(typeof globalThis!=='undefined'?globalThis:this);
