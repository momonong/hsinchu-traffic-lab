import { DepartureApi, normalizeQuery, timeMinutes, currentDisplay, sealedPreferences } from './adapter.js';

const api = new DepartureApi();
const state = {
  language: 'zh', mode: 'prospective', status: null, connection: 'loading', connectionError: null,
  options: null, optionsState: 'empty', optionsError: null, outcomes: null, revealState: 'hidden', revealError: null,
  minimumGain: 5, selectedId: null, generation: 0, connectionGeneration: 0, deadlineEdited: false,
  prospective: { form: { day: '2026-10-21', nominal: '06:30', deadline: '07:15', cost: .5 }, response: null, loading: false, error: null, generation: 0, advance:15, delay:30, minimumGain:5, selectedShift:null },
  form: { day: '2026-08-24', nominal: '06:45', deadline: '07:30', advance: 15, delay: 30, cost: .5 }
};
const copy = {
  skip: ['跳至方案比較', 'Skip to the comparison'], brand: ['通勤時窗研究', 'Commute-window research'], research: ['研究原型', 'Research prototype'],
  eyebrow: ['時間可以調，取捨要看清楚', 'A schedule shift has a cost'], title: ['換個時間，值得嗎？', 'Is changing the time worth it?'],
  intro: ['把在車上的時間、調整行程的代價與末門架截止，放在一起比較。', 'Compare road time, the cost of shifting your schedule, and the final-gantry deadline.'],
  route: ['國道1號南下75–98公里', 'National Freeway 1 · southbound km 75–98'],
  routeDetail: ['23公里門架走廊 · 小客車 · 非家到公司', '23 km gantry corridor · passenger cars · not home to work'],
  prospectiveDay: ['前瞻觀測日', 'Prospective observation date'], prospectiveTab: ['前瞻封存預測', 'Prospectively sealed forecasts'], replayTab: ['歷史選項回放', 'Historical option replay'], currentTab: ['晨間來源觀測', 'Morning source observations'],
  footer: ['目標是在準時條件下減少塞車時間；可操作的原型、預測表現與實際通勤效益分開驗收。', 'Goal: less traffic time while meeting a deadline. Working software, prediction performance and commuter benefit require separate evidence.'],
  local: ['本機研究', 'Local research'], unknown: ['未知', 'Unknown'], minute: ['分', 'min'],
  connectLoading: ['正在載入資料…', 'Loading data…'],
  connectReady: ['資料已載入', 'Data loaded'],
  connectError: ['資料暫時無法載入，請稍後重新連接。', 'Data is temporarily unavailable. Reconnect shortly.'],
  connectionHelp: ['請由專案的本機後端開啟此頁。此畫面沒有內建假資料，也不會自行啟動研究。', 'Open this page through the project’s local backend. This interface has no built-in example data and starts no research jobs.'],
  refresh: ['重新連接', 'Reconnect'], sourceClock: ['服務狀態截至', 'Backend status as of'],
  formStep: ['01 / 設定你的限制', '01 / Set your constraints'], formTitle: ['怎樣的調整可以接受？', 'What can you change?'],
  formDescription: ['以下時間都指門架通過時窗，不是離家或上交流道的時間。', 'These windows refer to gantry crossings, not leaving home or entering an interchange.'],
  day: ['回放日期', 'Replay date'], noDates: ['沒有可用回放日期', 'No replay dates available'],
  nominal: ['原訂75公里首門架時窗', 'Nominal first-gantry window (km 75)'],
  deadline: ['末98公里門架截止', 'Final-gantry deadline (km 98)'],
  deadlineHint: ['同一日期，可設任意合法分鐘；不是到公司時間。', 'Any valid minute on this date; not arrival at work.'],
  advance: ['最多提前', 'Maximum advance'], delay: ['最多延後', 'Maximum delay'], cost: ['調整行程的代價偏好', 'Schedule-shift cost preference'],
  costHint: ['偏好假設，不是已量到的等待或早到成本。', 'A preference assumption, not measured waiting or early-arrival cost.'],
  submit: ['比較五個時窗', 'Compare five windows'], submitting: ['正在讀取預測…', 'Loading predictions…'],
  emptyTitle: ['先設定限制，再看五個方案', 'Set your constraints, then compare'],
  emptyDescription: ['按「比較五個時窗」讀取同一歷史決策時刻的預測。後續實際結果會先隱藏。', 'Load the predictions for one historical issue time. Subsequent observations stay hidden until requested.'],
  backendTitle: ['資料暫時無法載入', 'Data is temporarily unavailable'],
  backendDescription: ['請稍後重新連接；目前不顯示任何預測數字。', 'Reconnect shortly. No prediction values are shown.'],
  noDataTitle: ['目前沒有可回放的日期', 'No historical replay is available yet'],
  noDataDescription: ['後端尚未提供可用日期。等待資料準備完成後，再重新連接。', 'The backend has not provided replay dates. Reconnect once the data is ready.'],
  errorTitle: ['這次比較尚未完成', 'The comparison could not be completed'],
  errorDescription: ['沒有用舊方案或零值代替缺漏；請檢查服務或稍後重試。', 'No older options or zeros replace missing data. Check the backend or try again later.'],
  resultsStep: ['02 / 比較預測與代價', '02 / Compare predictions and cost'], candidatesTitle: ['同一決策時刻，五個候選時窗', 'Five windows at the same issue time'],
  candidatesHint: ['數字是預測，不是已發生的旅次結果', 'Predictions, not subsequent trip outcomes'],
  nominalLabel: ['原時窗', 'Nominal'], earlier: ['提前', 'Earlier'], later: ['延後', 'Later'],
  roadMedian: ['在車上的時間 · 中位估計', 'Road time · median estimate'], roadQ90: ['較保守90分位', 'More cautious 90th percentile'],
  arrivalTitle: ['預測末門架通過時刻', 'Predicted final-gantry passage'], arrivalMedian: ['中位估計', 'Median estimate'], arrivalQ90: ['90分位估計', '90th-percentile estimate'],
  roadGain: ['預測少花車程', 'Predicted road-time reduction'], shiftCost: ['調時折算代價', 'Shift-cost equivalent'], netGain: ['扣代價後的差額', 'Reduction after shift cost'],
  riskPass: ['較保守的通過時間估計符合期限', 'The more cautious passage estimate meets the deadline'],
  riskLate: ['較保守的通過時間估計晚於期限', 'The more cautious passage estimate is later than the deadline'],
  riskUnknown: ['資料不足，無法判讀截止條件', 'Insufficient data for the deadline condition'],
  outside: ['超出你的可調範圍', 'Outside your allowed shift'], unavailable: ['這個時窗資料不足', 'Insufficient data for this window'],
  pick: ['我先選這個', 'My tentative choice'], picked: ['已暫選', 'Tentatively selected'],
  policyMark: ['回放中模型選擇的時窗', 'Window selected by the model in this replay'],
  selectionEmpty: ['你可以自行暫選一個時窗；個人暫選不會變成模型推薦。', 'You may make a tentative choice. Your choice does not become a model recommendation.'],
  selectionWarning: ['這個暫選的q90預測晚於截止，請留意風險。', 'This choice’s q90 prediction is later than the deadline.'],
  support: ['校準支持', 'Calibration support'], daysUnit: ['日', 'days'], tripsUnit: ['旅次', 'trips'],
  replayDay: ['回放日', 'Replay date'], issue: ['決策時刻', 'Issue time'], dataAsOf: ['歷史特徵截至', 'Historical features through'], method: ['顯示預測方法', 'Forecast display method'],
  primarySetting: ['主要研究條件：D45／代價0.5／±30分', 'Primary research setting: D45 / cost 0.5 / ±30 min'],
  customSetting: ['自訂條件試算，未繼承主要策略資格', 'Custom what-if conditions; primary policy qualification does not carry over'],
  qualifiedBadge: ['策略資格：主要研究條件通過', 'Policy qualified for the primary research setting'],
  unqualifiedBadge: ['策略資格：尚未通過', 'Policy not qualified'],
  timelineTitle: ['把時間攤開來看', 'See the windows on a timeline'],
  timelineLegend: ['灰條：首門架15分鐘時窗；實心點：末門架中位通過時刻；空心點：90分位。這段線不是信賴區間。', 'Gray bar: 15-minute first-gantry window. Filled dot: final-gantry median passage; open dot: 90th percentile. The line is not a confidence interval.'],
  timelineAlt: ['五個首門架時窗與預測末門架通過時刻比較', 'Five first-gantry windows and predicted final-gantry passage times'],
  finalDeadline: ['末門架截止', 'Final-gantry deadline'],
  meaning: ['這些數字應該怎麼讀？', 'How should I read these numbers?'],
  meaningItems: [
    ['q50是旅次分布的中位估計，q90是較保守的90分位估計；都不是信賴區間或個人準時保證。', 'q50 is the estimated trip-distribution median; q90 is its more cautious 90th percentile. Neither is a confidence interval or an individual on-time guarantee.'],
    ['末門架時刻由首門架時窗起點加A估計；A包含旅次在15分鐘時窗內通過首門架的偏移，不能直接拿車程T相加。', 'Final-gantry time is the first-window start plus estimated A. A includes the within-window first-gantry offset; it is not simply travel time T.'],
    ['調時代價是你的偏好折算。家中接駁、等待、早到與工作時間安排仍未知。', 'Shift cost represents your assumed preference. Home access, waiting, early arrival and workplace scheduling remain unknown.'],
    ['本頁只適用完整通過國1南下75→88→92.8→95→98公里五門架的母體；不能代表中途上／下國道或一般竹北到竹科通勤。', 'This population completes all five N1 southbound gantries at km 75→88→92.8→95→98. It does not cover intermediate entrants/exits or a general Zhubei-to-Science-Park commute.'],
    ['歷史修訂資料與假設發布延遲的回放，不是首次可得版本的即時驗證；校準後分位數也不提供個人準時保證。', 'Replay uses revised historical data and assumed publication delays, not a first-publication live test. Calibrated quantiles still provide no personal on-time guarantee.']
  ],
  rawEvidence: ['預測與資料契約（原始欄位）', 'Prediction evidence (original field values)'],
  revealTitle: ['03 / 要看看後來發生什麼嗎？', '03 / See what happened later?'],
  revealDescription: ['獨立揭露同日期、同時窗旅次群的後續結果。這不會改變上方預測或政策狀態。', 'Reveal later outcomes for the same dated trip cohorts. This will not change the predictions or policy status above.'],
  reveal: ['揭露歷史實際結果', 'Reveal historical outcomes'], revealing: ['正在讀取後續結果…', 'Loading subsequent outcomes…'],
  revealed: ['已獨立揭露後續結果', 'Subsequent outcomes revealed separately'],
  revealUnavailable: ['後續旅次結果尚未提供', 'Subsequent trip outcomes are not available'],
  outcomeInsufficient: ['已查詢，後續旅次資料仍不足', 'Checked; subsequent trip data is still insufficient'],
  outcomeError: ['後續結果讀取失敗，沒有用缺漏代替零。可再次嘗試。', 'Could not load outcomes. Missing values remain unknown; you can try again.'],
  actualMedian: ['實際群體車程q50', 'Observed cohort travel q50'], actualQ90: ['實際群體車程q90', 'Observed cohort travel q90'],
  actualArrivalMedian: ['實際群體末門架q50', 'Observed cohort final q50'], actualArrivalQ90: ['實際群體末門架q90', 'Observed cohort final q90'],
  actualShare: ['截止前通過比例', 'Share passing before deadline'], actualN: ['旅次數', 'Trips'],
  outcomeWarning: ['這是不同歷史旅次群的事後結果，不是同一個人換時間的因果節時，也不是今日通勤建議。', 'These are retrospective results from different historical trip cohorts, not the causal time saving of one person changing time or advice for today.'],
  currentTitle: ['今日觀測，和歷史回放分開', 'Today’s observations stay separate'],
  currentDescription: ['這是首次接收的來源觀測；只有排定晨間時刻才可能生成獨立封存預測。觀測不會混入歷史回放。', 'These are first-received source observations. Separate forecasts may be sealed only at scheduled morning issues. Observations are not mixed into historical replay.'],
  sourceTime: ['來源資料時刻', 'Source time'], receiptTime: ['完整接收／可用時刻', 'Complete receipt / available time'], currentState: ['資料狀態', 'Observation status'],
  currentScope: ['觀測只代表其來源與時格；不同來源時格不可視為同一快照，也不代表現在順暢或適合出發。', 'Observations retain their source and time bucket. Different source buckets are not one snapshot, and do not establish clear traffic or a good time to leave.'],
  sourceSummary: ['後端來源摘要（原文）', 'Backend source summary (original language)'],
  liveReason: ['策略尚未合格；不提供今日調時推薦，也不把照常當成已驗證最佳。', 'The policy is unqualified: no schedule-shift advice, and nominal is not proven best.'],
  statuses: {
    available: ['觀測可讀', 'Observations available'], stale: ['資料已過期', 'Stale observations'], not_ready: ['尚未備妥', 'Not ready'], data_insufficient: ['資料不足', 'Insufficient data'], stopped: ['來源已停止', 'Source stopped']
  },
  decisions: {
    strategy_unqualified: ['目前沒有模型推薦', 'No model recommendation is qualified', '可以比較預測，但策略尚未通過推薦資格；你自行暫選的方案不代表已驗證最佳。', 'Predictions can be compared, but the policy has not qualified. A tentative user choice is not a validated best option.'],
    data_insufficient: ['資料不足，暫時無法完整比較', 'Insufficient data for a complete comparison', '缺漏保持未知，不用零值、舊時窗或今日觀測代替。', 'Missing values remain unknown. Zeros, older windows and today’s observations are not substitutes.'],
    no_feasible_option: ['沒有通過截止條件的選項', 'No option meets the deadline condition', '在你的可調範圍內，沒有方案的預測q90通過時刻不晚於截止；這不是實際遲到的保證。', 'Within your allowed shifts, no predicted q90 passage meets the deadline. This does not guarantee an actual late arrival.'],
    no_qualified_change: ['沒有通過收益條件的調時方案', 'No shift meets the change criteria', '截止條件與調時收益條件分開；後端沒有選出合格變更。', 'Deadline feasibility and schedule-shift benefit are separate. The backend has selected no qualified change.'],
    keep_nominal: ['回放中的模型選擇：維持原時窗', 'The model’s replay choice: keep the nominal window', '這是凍結政策在歷史輸入上的選擇，不是今日出發建議或個人準時保證。', 'This frozen-policy choice uses historical inputs. It is not advice for today or a personal on-time guarantee.'],
    change_historical_shadow_only: ['回放中的模型選出一個調時時窗', 'The model selected a shifted window in this replay', '選擇來自後端凍結政策；請連同調時代價與截止條件查看，不當成今日推薦。', 'The backend frozen policy owns this selection. Read it alongside shift cost and the deadline condition; it is not a recommendation for today.']
  }
};

copy.minimumGain=['最低預測道路時間縮短','Minimum predicted road-time reduction'];
copy.minimumHint=['5分是期望的篩選條件，不是已證實少塞5分；與調時代價係數分開。','5 min is a preference filter, not demonstrated congestion-time savings; separate from shift cost.'];
copy.title=['首門架時窗偏好試算','Gantry-window preferences'];
copy.intro=['最多提前15分、期望道路時間少5分；先看條件與未知，再自行比較。','Advance at most 15 min and seek 5 min less road time: compare conditions and unknowns.'];
copy.research=['封存預測候選介面','Sealed-forecast candidate'];
copy.connectReady=['已讀取資料狀態','Data status loaded'];
copy.sourceClock=['狀態／快照截至','Status / snapshot as of'];
copy.replayTab=['校準期歷史回放','Calibration-period replay'];
copy.prospectiveTab=['事前封存預測','Prospectively sealed forecasts'];
copy.currentTab=['來源狀態','Source status'];
copy.outcomeWarning=['同日不同首門架時窗的歷史車群；不是個人因果節時。這份聚合只保留D45的準時比例，其他截止保持未知；T90/A等未攜欄位也保持未知。','Historical cohorts in different first-gantry windows on the same date, not personal causal savings. This aggregate only retains D45 on-time shares; other deadlines and uncarried T90/A statistics stay unknown.'];
const t = key => copy[key]?.[state.language === 'en' ? 1 : 0] ?? key;
const bilingual = (zh, en) => state.language === 'en' ? en : zh;
const $ = id => document.getElementById(id);
function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); if (className) n.className = className; return n; }
function fmt(value, digits = 1) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : t('unknown'); }
function minutes(value, signed = false) { if (typeof value !== 'number' || !Number.isFinite(value)) return t('unknown'); return (signed && value > 0 ? '+' : '') + fmt(value) + ' ' + t('minute'); }
function timeText(epoch, withDate = false) {
  if (typeof epoch === 'string') epoch = Date.parse(epoch);
  if (!Number.isFinite(epoch) || Math.abs(epoch) > 8.64e15) return t('unknown');
  return new Intl.DateTimeFormat(state.language === 'en' ? 'en-GB' : 'zh-TW', { timeZone: 'Asia/Taipei', hourCycle: 'h23', ...(withDate ? { year: 'numeric', month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit' }).format(new Date(epoch));
}
function shiftText(value) { return value === 0 ? t('nominalLabel') : (value < 0 ? t('earlier') : t('later')) + ' ' + Math.abs(value) + ' ' + t('minute'); }
function field(id, key, control, hint) { const label = node('label', undefined, 'field'); control.id = id; label.htmlFor = id; label.append(node('span', t(key)), control); if (hint) label.append(node('small', hint)); return label; }
function select(values, selected, formatter = v => v) { const s = node('select'); for (const value of values) { const o = node('option', formatter(value)); o.value = String(value); s.append(o); } s.value = String(selected); return s; }
function detail(label, data) { const d = node('details', undefined, 'evidence-details'); d.append(node('summary', label), node('pre', JSON.stringify(data, null, 2))); return d; }
function setStaticLanguage() {
  document.documentElement.lang = state.language === 'en' ? 'en' : 'zh-Hant';
  document.title = bilingual('換個時間，值得嗎？｜通勤時窗研究', 'Is changing the time worth it? · Commute-window research');
  $('brand-link').setAttribute('aria-label', t('brand'));
  for (const element of document.querySelectorAll('[data-text]')) element.textContent = t(element.dataset.text);
  $('language').value = state.language;
  $('mode-tabs').setAttribute('aria-label', bilingual('資料模式', 'Data mode'));
  for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed', String(button.dataset.mode === state.mode));
}
function renderConnection() {
  const container = $('connection'); container.replaceChildren();
  container.append(node('span', t(state.connection === 'loading' ? 'connectLoading' : state.connection === 'ready' ? 'connectReady' : 'connectError')));
  if (state.connection === 'ready') container.append(node('span', (state.status.delivery_mode==='sealed_snapshot'?bilingual('已驗封存快照，未連即時資料；匯出於','Verified sealed snapshot, no live connection; exported at'):bilingual('連線模式；狀態截至','Connected mode; status as of')) + ' ' + timeText(state.status.as_of, true) + ' · UTC+8'));
  const button = node('button', t('refresh')); button.type = 'button'; button.disabled = state.connection === 'loading'; button.addEventListener('click', connect); container.append(button);
  if (state.connectionError) {
    const details = detail(bilingual('連線詳細資訊', 'Connection details'), { code: state.connectionError.code, message: state.connectionError.message });
    const help = node('p', t('connectionHelp'), 'small');
    details.append(help); container.append(details);
  }
}
function syncProspectiveDraft() {
  if(state.mode!=='prospective')return;
  const session=state.prospective,day=$('prospective-day'),nominal=$('prospective-nominal'),deadline=$('prospective-deadline'),cost=$('prospective-cost');
  if(!day||!nominal||!deadline||!cost)return;
  const form={day:day.value,nominal:clock(Number(nominal.value)),deadline:deadline.value,cost:Number(cost.value)};
  if(JSON.stringify(form)!==JSON.stringify(session.form)){
    session.form=form;session.generation++;session.loading=false;session.response=null;session.error=null;session.selectedShift=null;
  }
}
function render() { renderContent(); }
function renderContent() {
  setStaticLanguage(); renderConnection();
  const workspace = $('workspace'); workspace.replaceChildren();
  if (state.mode === 'current') return renderCurrent(workspace);
  if (state.mode === 'prospective') return renderProspective(workspace);
  const grid = node('div', undefined, 'workspace-grid'), formPanel = renderForm(), comparison = node('section', undefined, 'content-column'); comparison.id = 'comparison'; comparison.setAttribute('aria-label', bilingual('歷史方案比較', 'Historical option comparison'));
  grid.append(formPanel, comparison); workspace.append(grid); renderComparison();
}
function invalidate() {
  state.generation++; state.options = null; state.optionsState = 'empty'; state.optionsError = null; state.outcomes = null; state.revealState = 'hidden'; state.revealError = null; state.selectedId = null;
  const submit = $('compare-button'); if (submit) { submit.disabled = state.connection !== 'ready' || !state.form.day; submit.textContent = t('submit'); }
  renderComparison();
}
function renderForm() {
  const panel = node('aside', undefined, 'panel form-panel'); panel.append(node('p', t('formStep'), 'step-label'), node('h2', t('formTitle')), node('p', t('formDescription'), 'description'));
  const form = node('form', undefined, 'preference-form'); form.id = 'preferences';
  const dates = state.status?.replay.available_dates ?? [];
  const day = select(dates.length ? dates : [''], state.form.day, value => value || t('noDates')); day.disabled = state.connection !== 'ready' || !dates.length;
  const nominal = select(Array.from({ length: 12 }, (_, i) => 390 + 15 * i), timeMinutes(state.form.nominal), value => clock(value) + '–' + clock(value + 15));
  const deadline = node('input'); deadline.type = 'time'; deadline.step = '60'; deadline.required = true; deadline.value = state.form.deadline;
  const advance = select([0, 15, 30], state.form.advance, value => value + ' ' + t('minute'));
  const delay = select([0, 15, 30], state.form.delay, value => value + ' ' + t('minute'));
  const cost = select([0, .25, .5, 1], state.form.cost, value => bilingual('每調15分 ≈ ' + fmt(value * 15) + '分代價', '15 min shift ≈ ' + fmt(value * 15) + ' min cost'));
  for (const control of [nominal, deadline, advance, delay, cost]) control.disabled = state.connection !== 'ready' || !dates.length;
  form.append(field('replay-day', 'day', day), field('nominal', 'nominal', nominal), field('deadline', 'deadline', deadline, t('deadlineHint')));
  const pair = node('div', undefined, 'field-pair'); pair.append(field('advance', 'advance', advance), field('delay', 'delay', delay)); form.append(pair);

  const minimum=select([0,1,2,3,5,7.5,10],state.minimumGain,v=>v+' '+t('minute'));
  minimum.addEventListener('change',()=>{state.minimumGain=Number(minimum.value);invalidate();});
  form.append(field('minimum-gain','minimumGain',minimum,t('minimumHint')));
  const costField = field('cost', 'cost', cost); costField.classList.add('cost-field'); form.append(costField);
  const explanation = node('p', costText(), 'cost-explanation'); explanation.id = 'cost-explanation'; form.append(explanation);
  const submitWrap = node('div', undefined, 'submit-wrap'), button = node('button', t(state.optionsState === 'loading' ? 'submitting' : 'submit'), 'primary full-width'); button.id = 'compare-button'; button.type = 'button'; button.disabled = state.connection !== 'ready' || !dates.length || state.optionsState === 'loading'; submitWrap.append(button); form.append(submitWrap);
  day.addEventListener('change', () => { state.form.day = day.value; invalidate(); });
  nominal.addEventListener('change', () => { state.form.nominal = clock(Number(nominal.value)); if (!state.deadlineEdited) { state.form.deadline = clock(Number(nominal.value) + 45); deadline.value = state.form.deadline; } invalidate(); });
  deadline.addEventListener('input', () => { state.form.deadline = deadline.value; state.deadlineEdited = true; invalidate(); });
  for (const [control, key] of [[advance, 'advance'], [delay, 'delay'], [cost, 'cost']]) control.addEventListener('change', () => { state.form[key] = Number(control.value); $('cost-explanation').textContent = costText(); invalidate(); });
  // The private preview permits scripts but intentionally forbids native forms.
  // Both click and Enter take this local path; neither depends on form submission.
  const compareLocally = () => {
    if (button.disabled) return;
    if (!form.checkValidity()) { form.reportValidity(); return; }
    return submitOptions();
  };
  button.addEventListener('click', compareLocally);
  form.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing && !['SELECT', 'TEXTAREA'].includes(event.target?.tagName)) {
      event.preventDefault(); return compareLocally();
    }
  });
  form.addEventListener('submit', event => { event.preventDefault(); });
  panel.append(form); return panel;
}
function clock(value) { return String(Math.floor(value / 60)).padStart(2, '0') + ':' + String(value % 60).padStart(2, '0'); }
function costText() { return bilingual('每調整15分鐘，折算 ' + fmt(state.form.cost * 15) + ' 分鐘代價。', 'Each 15-minute shift costs an assumed ' + fmt(state.form.cost * 15) + ' equivalent minutes. ') + ' ' + t('costHint'); }
function emptyState(title, description, symbol = '↗') { const box = node('div', undefined, 'empty-state'); box.append(node('div', symbol, 'symbol'), node('h2', t(title)), node('p', t(description))); return box; }
function decisionNotice(options) {
  const record = copy.decisions[options.decision.status], index = state.language === 'en' ? 1 : 0;
  const notice = node('section', undefined, 'notice ' + (['strategy_unqualified', 'no_feasible_option', 'data_insufficient'].includes(options.decision.status) ? 'amber' : ''));
  notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite'); notice.append(node('h2', record[index]), node('p', record[index + 2])); return notice;
}
export function candidateView(candidate, options) {
  const p = candidate.prediction, baseline = options.candidates.find(c => c.shift_minutes === 0)?.prediction;
  const gain = p && baseline ? baseline.Tq50 - p.Tq50 : null;
  const cost = options.request.cost * Math.abs(candidate.shift_minutes);
  return { gain, cost, net: gain === null ? null : gain - cost, arrival50: p ? Date.parse(candidate.window_start) + p.Aq50 * 60000 : null, arrival90: p ? Date.parse(candidate.window_start) + p.Aq90 * 60000 : null };
}

export function preferenceChecks(candidate,options,minimumGain) {
  if(!Number.isFinite(minimumGain)||minimumGain<0)throw new Error('invalid minimum road gain');
  const view=candidateView(candidate,options),p=candidate.prediction;
  const gain=Number.isFinite(view.gain)?view.gain:null;
  const bounds=candidate.shift_minutes>=-options.request.advance&&candidate.shift_minutes<=options.request.delay;
  const deadline=Date.parse(options.request.day+'T'+options.request.deadline+':00+08:00');
  const q90=p&&Number.isFinite(view.arrival90)?view.arrival90<=deadline:null;
  const minimum=gain===null?null:gain>=minimumGain;
  return{within_bounds:bounds,gain_meets_minimum:minimum,q90_meets_gantry_deadline:q90,
    matches_numeric_conditions:bounds&&minimum===true&&q90===true,
    policy_qualified:false,complete_lateness_risk_delta:null,not_a_recommendation:true};
}
export function preferenceSummary(options,minimumGain) {
  const checks=options.candidates.map(c=>preferenceChecks(c,options,minimumGain));
  return{candidate_count:checks.length,matching:checks.filter(c=>c.matches_numeric_conditions).length,
    unknown:checks.filter(c=>c.gain_meets_minimum===null||c.q90_meets_gantry_deadline===null).length,
    formal_recommendations:0,checks};
}
function conditionWords(value){return value===null?t('unknown'):value?bilingual('符合','Meets'):bilingual('未符','Fails');}
function renderUserComparison(options) {
  const nominal=options.candidates.find(c=>c.shift_minutes===0),chosen=options.candidates.find(c=>c.id===state.selectedId);
  const section=node('section',undefined,'panel user-comparison');
  section.append(node('h3',bilingual('原時窗與你的暫選','Nominal and your tentative choice')));
  const columns=node('div',undefined,'comparison-pair');
  for(const [label,c]of [[bilingual('原時窗','Nominal'),nominal],[bilingual('你的暫選','Your tentative choice'),chosen]]) {
    const column=node('div');column.append(node('h4',label));
    if(!c){column.append(node('p',bilingual('尚未暫選；不會自動代選。','No tentative choice; none is selected automatically.')));columns.append(column);continue;}
    const v=candidateView(c,options);
    column.append(node('p',timeText(c.window_start)+'–'+timeText(c.window_end)),metricPair(t('roadMedian'),minutes(c.prediction?.Tq50)),
      metricPair(t('roadGain'),minutes(v.gain,true)),metricPair(t('shiftCost'),minutes(v.cost)),metricPair(t('netGain'),minutes(v.net,true)),
      metricPair(t('arrivalQ90'),timeText(v.arrival90)),node('p',bilingual('完整準時風險差：未知；策略未合格。','Complete lateness-risk difference: unknown; policy unqualified.')));
    columns.append(column);
  }
  section.append(columns);return section;
}
function renderComparison() {
  const container = $('comparison'); if (!container) return; container.replaceChildren();
  if (state.connection !== 'ready') { container.append(emptyState('backendTitle', 'backendDescription', '○')); return; }
  if (!state.status.replay.available_dates.length) { container.append(emptyState('noDataTitle', 'noDataDescription', '○')); return; }
  if (state.optionsState === 'loading') {
    const grid = node('div', undefined, 'loading-grid'); grid.setAttribute('aria-label', t('submitting')); grid.setAttribute('aria-busy', 'true'); for (let i = 0; i < 5; i++) grid.append(node('div', undefined, 'skeleton')); container.append(grid); return;
  }
  if (state.optionsState === 'error') { container.append(emptyState('errorTitle', 'errorDescription', '!'), detail(bilingual('讀取診斷', 'Request details'), { code: state.optionsError.code, message: state.optionsError.message })); return; }
  const options = state.options;
  if (!options) { container.append(emptyState('emptyTitle', 'emptyDescription')); return; }

  container.append(decisionNotice(options));
  const summary=preferenceSummary(options,state.minimumGain),finding=node('section',undefined,'notice preference-finding');
  finding.setAttribute('role','status');
  finding.append(node('h2',summary.matching?bilingual(summary.matching+'個選項符合自訂數值條件，尚非正式推薦',summary.matching+' options meet custom numerical conditions; not formal recommendations'):bilingual('沒有符合自訂數值條件的選項','No option meets the custom numerical conditions')),
    node('p',bilingual('全部五個選項仍列出。q90符合末門架截止，不代表準時風險不增加；策略未合格也不代表照常最好。','All five options remain visible. Meeting the q90 gantry deadline does not establish unchanged lateness risk; an unqualified policy does not prove nominal best.')));
  container.append(finding,renderUserComparison(options));
  const meta = node('div', undefined, 'meta-strip');
  for (const [key, value] of [['replayDay', options.request.day], ['issue', timeText(options.issue_time)], ['dataAsOf', timeText(options.data_as_of)], ['method', options.qualification.forecast_display_method ?? t('unknown')]]) { const span = node('span'); span.append(node('strong', t(key) + ' '), document.createTextNode(value)); meta.append(span); }
  meta.append(node('span', 'Asia/Taipei · UTC+8'));
  meta.append(node('span', t(options.request.cost === .5 && options.request.advance === 30 && options.request.delay === 30 && timeMinutes(options.request.deadline) === timeMinutes(options.request.nominal) + 45 ? 'primarySetting' : 'customSetting')));
  const badge = node('strong', t(options.qualification.policy_qualified ? 'qualifiedBadge' : 'unqualifiedBadge')); badge.className = 'qualification-badge'; meta.append(badge); container.append(meta);
  const heading = node('div', undefined, 'section-heading'), title = node('div'); title.append(node('p', t('resultsStep'), 'step-label'), node('h3', t('candidatesTitle'))); heading.append(title, node('p', t('candidatesHint'))); container.append(heading);
  const cards = node('div', undefined, 'candidate-grid'); for (const candidate of options.candidates) cards.append(renderCandidate(candidate, options)); container.append(cards);
  const chosen = options.candidates.find(c => c.id === state.selectedId), note = node('p', undefined, 'selection-note' + (chosen ? '' : ' empty')); note.setAttribute('aria-live', 'polite');
  if (chosen) note.textContent = bilingual('你的暫選：', 'Your tentative choice: ') + shiftText(chosen.shift_minutes) + ' · ' + timeText(chosen.window_start) + '–' + timeText(chosen.window_end) + '。 ' + bilingual('這是使用者選擇，不是模型推薦。', 'This is your choice, not a model recommendation. ') + (chosen.estimated_q90_within_deadline === false ? t('selectionWarning') : '');
  else note.textContent = t('selectionEmpty'); container.append(note);
  if (options.candidates.some(c => c.prediction !== null)) container.append(renderTimeline(options));
  const explanation = node('details', undefined, 'explain'); explanation.append(node('summary', t('meaning'))); const list = node('ul'); for (const item of copy.meaningItems) list.append(node('li', item[state.language === 'en' ? 1 : 0])); explanation.append(list); container.append(explanation, renderReveal(options), detail(t('rawEvidence'), options));
}
function metricPair(label, value) { const row = node('div', undefined, 'metric-pair'); row.append(node('span', label), node('strong', value)); return row; }
function renderCandidate(candidate, options) {
  const p = candidate.prediction, view = candidateView(candidate, options);
  const card = node('article', undefined, 'candidate-card' + (candidate.shift_minutes === 0 ? ' nominal' : '') + (state.selectedId === candidate.id ? ' is-chosen' : '') + (!candidate.permitted_by_user_bounds ? ' excluded' : ''));
  card.append(node('h3', shiftText(candidate.shift_minutes), 'shift'), node('p', timeText(candidate.window_start) + '–' + timeText(candidate.window_end), 'window'));
  card.append(node('p', t('roadMedian'), 'metric-label')); const major = node('p', p ? fmt(p.Tq50) : t('unknown'), 'major'); if (p) major.append(node('small', ' ' + t('minute'))); card.append(major, node('p', t('roadQ90') + ' ' + minutes(p?.Tq90), 'secondary-metric'), node('hr', undefined, 'card-divider'));
  card.append(node('p', t('arrivalTitle'), 'metric-label'), metricPair(t('arrivalMedian'), timeText(view.arrival50)), metricPair(t('arrivalQ90'), timeText(view.arrival90)));
  const condition = candidate.estimated_q90_within_deadline;
  card.append(node('p', t(!candidate.permitted_by_user_bounds ? 'outside' : condition === null ? 'riskUnknown' : condition ? 'riskPass' : 'riskLate'), 'risk' + (condition === false ? ' late' : condition === null || !candidate.permitted_by_user_bounds ? ' unknown' : '')));
  const costs = node('div', undefined, 'card-cost'); costs.append(metricPair(t('roadGain'), minutes(view.gain, true)), metricPair(t('shiftCost'), minutes(view.cost)), metricPair(t('netGain'), minutes(view.net, true))); card.append(costs);

  const checks=preferenceChecks(candidate,options,state.minimumGain),conditions=node('div',undefined,'preference-conditions');
  for(const [label,value]of [[bilingual('可調範圍','Allowed shift'),checks.within_bounds],
    [bilingual('道路差達最低','Road reduction meets minimum'),checks.gain_meets_minimum],
    [bilingual('q90末門架截止','q90 final-gantry deadline'),checks.q90_meets_gantry_deadline]])conditions.append(metricPair(label,conditionWords(value)));
  conditions.append(node('p',bilingual('策略未合格；完整準時風險差未知。','Policy unqualified; complete lateness-risk difference unknown.')));
  card.append(conditions);
  if (options.decision.selected_shift_minutes === candidate.shift_minutes) card.append(node('p', t('policyMark'), 'small'));
  const button = node('button', t(state.selectedId === candidate.id ? 'picked' : 'pick'), 'pick'); button.type = 'button'; button.id = 'pick-' + candidate.shift_minutes; button.disabled = !candidate.permitted_by_user_bounds || p === null; button.setAttribute('aria-pressed', String(state.selectedId === candidate.id)); button.setAttribute('aria-label', t('pick') + ' · ' + shiftText(candidate.shift_minutes)); button.addEventListener('click', () => { state.selectedId = candidate.id; renderComparison(); $('pick-' + candidate.shift_minutes)?.focus(); }); card.append(button);
  card.append(node('p', t('support') + ': ' + fmt(candidate.calibration_support.days, 0) + ' ' + t('daysUnit') + ' / ' + fmt(candidate.calibration_support.trips, 0) + ' ' + t('tripsUnit'), 'support')); return card;
}
function svgNode(tag, attrs) { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, String(value)); return n; }
function renderTimeline(options) {
  const panel = node('section', undefined, 'panel timeline-panel'); panel.append(node('h3', t('timelineTitle')), node('p', t('timelineLegend'), 'legend'));
  const scroll = node('div', undefined, 'timeline-scroll'), svg = svgNode('svg', { viewBox: '0 0 960 305', role: 'img', 'aria-label': t('timelineAlt'), class: 'timeline' });
  const day = options.request.day, deadline = Date.parse(day + 'T' + options.request.deadline + ':00+08:00');
  const times = [deadline]; for (const c of options.candidates) { times.push(Date.parse(c.window_start), Date.parse(c.window_end)); const v = candidateView(c, options); if (Number.isFinite(v.arrival90) && Math.abs(v.arrival90) <= 8.64e15) times.push(v.arrival90); }
  const min = Math.min(...times) - 300000, max = Math.max(...times) + 300000, left = 110, width = 815, x = time => left + (time - min) / Math.max(max - min, 60000) * width;
  const step = Math.max(15, Math.ceil((max - min) / 60000 / 10 / 15) * 15) * 60000;
  for (let epoch = Math.ceil(min / step) * step; epoch <= max; epoch += step) { svg.append(svgNode('line', { x1: x(epoch), x2: x(epoch), y1: 28, y2: 266, class: 'tick' })); const label = svgNode('text', { x: x(epoch), y: 292, 'text-anchor': 'middle' }); label.textContent = timeText(epoch); svg.append(label); }
  svg.append(svgNode('line', { x1: x(deadline), x2: x(deadline), y1: 25, y2: 266, class: 'deadline' })); const dl = svgNode('text', { x: x(deadline), y: 14, 'text-anchor': 'middle' }); dl.textContent = t('finalDeadline') + ' ' + timeText(deadline); svg.append(dl);
  options.candidates.forEach((c, i) => {
    const y = 48 + 47 * i, label = svgNode('text', { x: 3, y: y + 4, class: 'row-label' }); label.textContent = shiftText(c.shift_minutes); svg.append(label);
    svg.append(svgNode('rect', { x: x(Date.parse(c.window_start)), y: y - 7, width: x(Date.parse(c.window_end)) - x(Date.parse(c.window_start)), height: 14, rx: 4, class: 'window-bar' }));
    const view = candidateView(c, options); if (view.arrival50 === null || !Number.isFinite(view.arrival90) || Math.abs(view.arrival90) > 8.64e15) return;
    const late = c.estimated_q90_within_deadline === false;
    svg.append(svgNode('line', { x1: x(view.arrival50), x2: x(view.arrival90), y1: y, y2: y, class: 'arrival-line' + (late ? ' late-line' : '') }));
    svg.append(svgNode('circle', { cx: x(view.arrival50), cy: y, r: 4.5, class: 'arrival-dot' + (late ? ' late-dot' : '') }));
    const end = svgNode('circle', { cx: x(view.arrival90), cy: y, r: 5, class: 'arrival-end' }); const tip = svgNode('title', {}); tip.textContent = shiftText(c.shift_minutes) + ' · q50 ' + timeText(view.arrival50) + ' · q90 ' + timeText(view.arrival90); end.append(tip); svg.append(end);
  }); scroll.append(svg); panel.append(scroll); return panel;
}
function renderReveal(options) {
  const panel = node('section', undefined, 'reveal-panel'); panel.append(node('h3', t('revealTitle')), node('p', t('revealDescription'), 'description'));
  const button = node('button', t(state.revealState === 'loading' ? 'revealing' : state.revealState === 'ready' ? state.outcomes.status === 'data_insufficient' ? 'outcomeInsufficient' : 'revealed' : options.outcome_reveal_available ? 'reveal' : 'revealUnavailable'));
  button.type = 'button'; button.id = 'reveal-outcomes'; button.disabled = !options.outcome_reveal_available || ['loading', 'ready'].includes(state.revealState);
  button.addEventListener('click', revealOutcomes); panel.append(button);
  if (state.revealState === 'error') panel.append(node('p', t('outcomeError'), 'outcome-notice'));
  if (state.revealState === 'ready') {
    if (state.outcomes.status === 'data_insufficient') panel.append(node('p', t('outcomeInsufficient'), 'outcome-notice'));
    const wrap = node('div', undefined, 'outcome-table-wrap'), table = node('table'), head = node('thead'), hr = node('tr');
    for (const key of ['nominal', 'actualMedian', 'actualQ90', 'actualArrivalMedian', 'actualArrivalQ90', 'actualShare', 'actualN']) hr.append(node('th', key === 'nominal' ? bilingual('首門架時窗', 'First-gantry window') : t(key))); head.append(hr); table.append(head);
    const body = node('tbody'); for (const candidate of options.candidates) {
      const o = state.outcomes.candidates.find(r => r.id === candidate.id)?.observed, row = node('tr');
      const arrival = key => o?.[key] === null || o?.[key] === undefined ? t('unknown') : timeText(Date.parse(candidate.window_start) + o[key] * 60000);
      const share = o?.ontime_share === null || o?.ontime_share === undefined ? t('unknown') : fmt(o.ontime_share * 100) + '%';
      for (const value of [timeText(candidate.window_start) + '–' + timeText(candidate.window_end), minutes(o?.Tq50), minutes(o?.Tq90), arrival('Aq50'), arrival('Aq90'), share, fmt(o?.trips, 0)]) row.append(node('td', value)); body.append(row);
    } table.append(body); wrap.append(table); panel.append(wrap, node('p', t('outcomeWarning'), 'outcome-notice'));
  }
  return panel;
}
function renderCurrent(container) {
  if (state.connection !== 'ready') { container.append(emptyState('backendTitle', 'backendDescription', '○')); return; }
  const current = currentDisplay(state.status), panel = node('section', undefined, 'panel current-panel'); panel.append(node('p', bilingual('獨立資料模式 / 不產生出發推薦', 'SEPARATE DATA MODE / NO DEPARTURE ADVICE'), 'step-label'), node('h2', t('currentTitle')), node('p', t('currentDescription'), 'description'));
  const notice = node('div', undefined, 'notice amber'); notice.append(node('h3', t('liveReason'))); panel.append(notice);
  if(state.status.schedule?.status==='installation_receipt_present') panel.append(node('p', bilingual('已保存主機排程安裝收據；這不代表已收到晨間資料或已產生預測。', 'A host schedule installation receipt is saved. This does not mean morning sources or forecasts have arrived.')));
  const facts = node('div', undefined, 'current-facts');
  const labels = [[t('sourceTime'), timeText(current.source_time, true)], [t('receiptTime'), timeText(current.received_at, true)], [t('currentState'), copy.statuses[current.status][state.language === 'en' ? 1 : 0]]];
  for (const [label, value] of labels) { const fact = node('div', undefined, 'fact'); fact.append(node('span', label), node('strong', value)); facts.append(fact); } panel.append(facts);
  if (state.language === 'zh' || current.summary_en) panel.append(node('p', state.language === 'en' ? current.summary_en : current.summary));
  else panel.append(detail(t('sourceSummary'), { summary: current.summary }));
  if (current.sources?.length) {
    const list = node('ul');
    for (const source of current.sources) {const labels={available:['可用','Available'],data_insufficient:['研究列不足','Insufficient study rows'],parsed_invalid:['內容或有效列不符','Content / valid rows rejected'],stale:['已過期','Stale'],not_received:['尚未接收','Not received'],missing:['來源缺檔','Source file missing'],received_parsed:['已解析','Parsed']};const label=labels[source.status]?.[state.language==='en'?1:0]??bilingual('尚未可用','Unavailable');list.append(node('li',source.dataset+': '+label+(source.status==='available'?' · '+source.available_rows+'/'+source.expected_rows+' · '+timeText(source.source_time)+' → '+timeText(source.received_at):'')));}
    panel.append(list);
  }
  panel.append(node('p', 'Asia/Taipei · UTC+8. ' + t('currentScope'), 'scope-note'), detail(bilingual('當前資料狀態（原始欄位）', 'Observation status (original field values)'), { current_observation: current, live_prediction: state.status.live_prediction })); container.append(panel);
}

const prospectiveStatuses = {
  not_due: ['尚未到排定時刻', 'Scheduled issue has not arrived'],
  not_published: ['尚無已發布的封存預測', 'No published sealed forecast'],
  sealed_shadow_only: ['已封存，僅供研究觀察', 'Sealed for research shadow evaluation'],
  insufficient_input: ['輸入完整度不足，沒有產生預測', 'Input coverage is insufficient; no forecast'],
  late_output: ['輸出封存逾時，不列為有效準時預測', 'Late seal; excluded from timely forecast evaluation'],
  inflight: ['計算或封存尚未完成', 'Computation or sealing is incomplete'],
  unresolved_after_restart: ['重啟後未決；不補造預測', 'Unresolved after restart; no backfilled forecast'],
  missed_restart_no_backfill: ['啟動已過時刻，明記漏失', 'Started after issue; explicitly missed'],
  missed_model_warmload: ['模型尚未備妥，明記漏失', 'Model warmload missed this issue'],
  missed_dispatch_window: ['超過派發時窗，明記漏失', 'Dispatch window missed']
};
export function prospectiveWindowState(row, now = Date.now()) {
  return Date.parse(row.window_end) <= now ? bilingual('此首門架時窗已過', 'This first-gantry window has passed') : bilingual('尚未結束的首門架時窗', 'First-gantry window has not ended');
}
function sealedWindowLabel(row) {
  const label=node('p',prospectiveWindowState(row));label.id='sealed-time-state-'+row.shift_minutes;return label;
}
function sealedComparison(rows) {
  const session=state.prospective,nominalRow=rows.find(c=>c.shift_minutes===0),selected=rows.find(c=>c.shift_minutes===session.selectedShift&&!c.window_elapsed);
  const pair=node('section',undefined,'panel user-comparison');pair.id='sealed-comparison';pair.append(node('h3',bilingual('原時窗與人工暫選','Nominal and tentative choice')));
  const columns=node('div',undefined,'comparison-pair');
  for(const [label,c]of [[t('nominalLabel'),nominalRow],[bilingual('人工暫選','Tentative choice'),selected]]){
    const column=node('div');column.append(node('h4',label),node('p',c?timeText(c.window_start)+'–'+timeText(c.window_end):bilingual('尚未暫選／時窗已過','No choice / window passed')),
      metricPair(t('roadMedian'),minutes(c?.prediction?.Tq50)),metricPair(t('roadGain'),minutes(c?.predicted_roadgain,true)));columns.append(column);
  }pair.append(columns);return pair;
}
function refreshTimePresentation() {
  if(state.mode==='current'){render();return;}
  if(state.mode!=='prospective'||state.prospective.response?.status!=='sealed_shadow_only')return;
  const session=state.prospective,value=session.response,rows=sealedPreferences(value,session);
  // The clock tick never reconstructs a form or reads a partially entered time.
  const draftPending=$('prospective-deadline')?.value!==session.form.deadline;
  for(const row of rows){
    const label=$('sealed-time-state-'+row.shift_minutes),button=$('sealed-pick-'+row.shift_minutes);
    if(label)label.textContent=prospectiveWindowState(row);
    if(button){button.disabled=draftPending||!row.can_tentatively_select;button.textContent=row.window_elapsed?bilingual('時窗已過，不可採取','Window passed; not actionable'):draftPending?bilingual('請先比較新條件','Compare the edited conditions first'):bilingual('人工暫選（非建議）','Tentative choice (not advice)');}
  }
  if(session.selectedShift!==null&&!rows.some(c=>c.shift_minutes===session.selectedShift&&c.can_tentatively_select))session.selectedShift=null;
  const pair=$('sealed-comparison');if(pair)pair.replaceChildren(...sealedComparison(rows).children);
}
function renderProspective(container) {
  const panel = node('section', undefined, 'panel');
  panel.append(node('p', bilingual('前瞻研究 / 原始版本與當時預測', 'PROSPECTIVE RESEARCH / FIRST-RECEIVED INPUTS'), 'step-label'),
    node('h2', bilingual('在當時封存的五個時窗預測', 'Five-window forecasts sealed at issue time')),
    node('p', bilingual('這裡讀取事前保存的預測，不是每次查詢重新推論。沒有合格策略，不提供今天該改幾點的建議；也不代表照常最好。', 'This view reads predictions saved prospectively; it does not run a model on each request. No policy is qualified to advise a shift today. Nominal is not proven best.'), 'description'));
  const session = state.prospective, form = node('form', undefined, 'prospective-form');
  const days = state.status?.prospective_dates?.length ? state.status.prospective_dates : ['2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27'];
  const day = select(days, session.form.day), nominal = select(Array.from({length:12}, (_,i)=>390+15*i), timeMinutes(session.form.nominal), clock);
  const deadline = node('input'); deadline.type = 'time'; deadline.value = session.form.deadline; deadline.required = true;
  const cost = select([0,.25,.5,1], session.form.cost);
  form.append(field('prospective-day', 'prospectiveDay', day), field('prospective-nominal', 'nominal', nominal), field('prospective-deadline', 'deadline', deadline), field('prospective-cost', 'cost', cost));
  function invalid() { session.generation++; session.loading=false; session.response = null; session.error = null;session.selectedShift=null; }
  day.addEventListener('change', ()=>{session.form.day=day.value; invalid(); render();});
  nominal.addEventListener('change', ()=>{session.form.nominal=clock(Number(nominal.value));session.form.deadline=clock(Number(nominal.value)+45);invalid(); render();});
  deadline.addEventListener('change', ()=>{session.form.deadline=deadline.value; invalid(); render();});
  cost.addEventListener('change', ()=>{session.form.cost=Number(cost.value); invalid(); render();});
  for(const [key,values,label] of [['advance',[0,15,30],'advance'],['delay',[0,15,30],'delay'],['minimumGain',[0,1,2,3,5,7.5,10],'minimumGain']]){
    const control=select(values,session[key],v=>v+' '+t('minute'));
    control.addEventListener('change',()=>{session[key]=Number(control.value);session.selectedShift=null;render();});
    form.append(field('sealed-'+key,label,control));
  }
  const button = node('button', bilingual(session.loading?'讀取中…':'讀取已封存預測', session.loading?'Loading…':'Read sealed forecast'), 'primary'); button.type='button';button.id='read-sealed';button.disabled=session.loading||state.connection!=='ready';
  const compare=()=>{syncProspectiveDraft();if(form.checkValidity&&!form.checkValidity()){form.reportValidity?.();return;}return loadProspective();};
  button.addEventListener('click',compare);
  form.addEventListener('submit',event=>event.preventDefault());
  form.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing&&event.target?.tagName!=='SELECT'){event.preventDefault();compare();}});
  form.append(button);panel.append(form);
  panel.append(node('p', bilingual('固定首門架15分鐘時窗；截止指通過98公里末門架，不是到公司。代價0／0.25／0.5／1是偏好假設，不是實測等待成本。', 'Each first-gantry window is 15 minutes. The deadline is passage at the final km-98 gantry, not arrival at work. Cost 0 / 0.25 / 0.5 / 1 is an assumption, not measured waiting.'), 'scope-note'));
  if (session.error) panel.append(node('p', bilingual('封存資料未能通過讀取／核對，沒有以舊值或零補上。', 'The sealed artifact could not be read or verified. No older value or zero substitutes for it.'), 'notice amber'));
  if (session.response) {
    const value=session.response, words=prospectiveStatuses[value.status] ?? [value.status,value.status];
    panel.append(node('h3', words[state.language==='en'?1:0]));
    panel.append(node('p', bilingual('決策時刻：','Issue: ')+timeText(value.issue_time,true)+' · '+bilingual('查看截至：','Viewed as of: ')+timeText(value.as_of,true)));
    if(value.status==='sealed_shadow_only') {
      panel.append(node('p', bilingual('封存於：','Sealed at: ')+timeText(value.sealed_at,true)+' · empirical · '+bilingual('由訓練期統計的歷史分布基準；HGB／TCN僅作其他固定模型診斷。','Train-only historical-distribution baseline; HGB/TCN remain separate frozen-model diagnostics.')));
      const rows=sealedPreferences(value,session),matches=rows.filter(c=>c.matches_numeric_conditions).length;
      panel.append(node('h3',matches?bilingual(matches+'個方案符合數值條件，仍非正式推薦',matches+' options meet numerical conditions; still not recommendations'):bilingual('沒有符合自訂數值條件的方案','No option meets the custom numerical conditions')),
        node('p',bilingual('q90截止條件與完整準時風險不同；完整風險差仍未知。過去時窗僅供歷史比較，不可當作現在可採取的選項。','A q90 deadline condition does not establish unchanged lateness risk. Complete risk difference is unknown. Past windows are archived comparisons, not actionable options.')));
      const grid=node('div',undefined,'candidate-grid');
      for(const c of rows){
        const p=c.prediction,card=node('article',undefined,'candidate-card'+(c.shift_minutes===0?' nominal':'')+(session.selectedShift===c.shift_minutes?' is-chosen':''));
        card.append(node('h3',shiftText(c.shift_minutes)),node('p',timeText(c.window_start)+'–'+timeText(c.window_end),'window'),sealedWindowLabel(c),
          metricPair(t('roadMedian'),minutes(p?.Tq50)),metricPair(t('roadQ90'),minutes(p?.Tq90)),
          metricPair(t('arrivalMedian'),p?timeText(Date.parse(c.window_start)+p.Aq50*60000):t('unknown')),
          metricPair(t('arrivalQ90'),p?timeText(Date.parse(c.window_start)+p.Aq90*60000):t('unknown')),
          metricPair(t('roadGain'),minutes(c.predicted_roadgain,true)),metricPair(t('shiftCost'),minutes(c.shift_cost)),metricPair(t('netGain'),minutes(c.predicted_netgain,true)),
          metricPair(bilingual('可調範圍','Allowed shift'),conditionWords(c.within_bounds)),
          metricPair(bilingual('道路差達最低','Road reduction meets minimum'),conditionWords(c.gain_meets_minimum)),
          metricPair(bilingual('q90末門架截止','q90 final-gantry deadline'),conditionWords(c.estimated_q90_within_deadline)),
          node('p',bilingual('策略未合格；完整準時風險差未知。','Policy unqualified; complete lateness-risk difference unknown.')));
        const pick=node('button',c.window_elapsed?bilingual('時窗已過，不可採取','Window passed; not actionable'):bilingual('人工暫選（非建議）','Tentative choice (not advice)'));
        pick.type='button';pick.id='sealed-pick-'+c.shift_minutes;pick.disabled=!c.can_tentatively_select;
        pick.addEventListener('click',()=>{syncProspectiveDraft();if(session.response!==value){render();return;}const fresh=sealedPreferences(value,session).find(row=>row.shift_minutes===c.shift_minutes);if(!fresh?.can_tentatively_select){refreshTimePresentation();return;}session.selectedShift=c.shift_minutes;render();});card.append(pick);grid.append(card);
      }
      panel.append(grid);
      panel.append(sealedComparison(rows));
    }
    panel.append(detail(bilingual('來源／模型與當時輸入證據','Sources, models and issue-time evidence'),value));
  }
  panel.append(node('p', bilingual('q50／q90是兩個分位點，不是信賴區間或個人準時保證。最新規定時格缺值仍保留缺值；首次接收與修訂歷史資料存在差異，歷史校準覆蓋不保證適用。完整旅次結果須等待成熟M06，尚未取得不當成零誤差或效益成功。', 'q50/q90 are quantile points, not a confidence interval or personal on-time guarantee. Missing canonical slots stay missing. First-received inputs differ from revised history, so historical calibration coverage does not carry over. Mature M06 trips are required for evaluation; pending outcomes are neither zero error nor proven benefit.'), 'scope-note'));
  container.append(panel);
}
async function loadProspective() {
  const session=state.prospective;let query;try{const q=normalizeQuery({...session.form,advance:session.advance,delay:session.delay});query={day:q.day,nominal:q.nominal,deadline:q.deadline,cost:q.cost};}catch(error){session.error=error;session.response=null;session.selectedShift=null;render();return;}const generation=++session.generation; session.loading=true;session.error=null;session.response=null;render();
  try { const value=await api.prospective(query);if(generation!==session.generation)return;session.response=value; }
  catch(error){if(generation!==session.generation)return;session.error=error;}
  finally {if(generation===session.generation){session.loading=false;render();}}
}

async function connect() {
  const generation = ++state.connectionGeneration; state.connection = 'loading'; state.connectionError = null; render();
  try {
    const result = await api.status(); if (generation !== state.connectionGeneration) return;
    state.status = result; state.connection = 'ready';
    if (!result.replay.available_dates.includes(state.form.day)) { state.form.day = result.replay.available_dates.at(-1) ?? ''; state.options = null; state.optionsState = 'empty'; state.outcomes = null; state.revealState = 'hidden'; state.selectedId = null; state.generation++; }
  } catch (error) { if (generation !== state.connectionGeneration) return; state.connection = 'error'; state.connectionError = error; }
  render();
}
async function submitOptions() {
  if (state.connection !== 'ready' || state.optionsState === 'loading') return;
  let query; try { query = normalizeQuery(state.form); if (!state.status.replay.available_dates.includes(query.day)) throw new Error('Replay date is not available'); }
  catch (error) { state.optionsState = 'error'; state.optionsError = { code: 'input', message: error.message }; renderComparison(); return; }
  const generation = ++state.generation; state.optionsState = 'loading'; state.options = null; state.outcomes = null; state.revealState = 'hidden'; state.selectedId = null; render();
  try { const result = await api.options(query); if (generation !== state.generation) return; state.options = result; state.optionsState = 'ready'; }
  catch (error) { if (generation !== state.generation) return; state.optionsState = 'error'; state.optionsError = error; }
  render();
}
async function revealOutcomes() {
  if (!state.options || !state.options.outcome_reveal_available || ['loading', 'ready'].includes(state.revealState)) return;
  const options = state.options, generation = state.generation; state.revealState = 'loading'; state.revealError = null; renderComparison();
  try { const result = await api.outcomes(options); if (generation !== state.generation || options !== state.options) return; state.outcomes = result; state.revealState = 'ready'; }
  catch (error) { if (generation !== state.generation || options !== state.options) return; state.revealState = 'error'; state.revealError = error; }
  renderComparison();
}
export function changeLanguage(value) { if (!['zh', 'en'].includes(value)) return;syncProspectiveDraft();state.language = value;render(); }
function initialize() {
  $('language').addEventListener('change', () => changeLanguage($('language').value));
  for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click', () => { syncProspectiveDraft();state.mode = button.dataset.mode;render(); });
  const language = new URLSearchParams(location.search).get('lang'); if (['zh','en'].includes(language)) state.language=language;
  render(); connect().then(()=>{if(state.connection==='ready'&&state.mode==='prospective')loadProspective();});
  setInterval(refreshTimePresentation,15000);
}
if (typeof document !== 'undefined') initialize();
