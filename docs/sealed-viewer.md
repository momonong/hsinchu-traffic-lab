# 唯讀封存結果檢視器

這個候選介面回答：**選定日期有哪些預測已通過原封存驗證，哪些時窗無法比較，以及原因是什麼？** 它讀取已存在的研究產物，不建立預測或補發漏掉的時點。

## 啟動與停止

在已有相符依賴的專案根目錄：

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python -B -m scripts.departure_advisor.sealed_viewer --duration-seconds 600
```

- 固定 `http://127.0.0.1:8768/`，英文 `http://127.0.0.1:8768/?lang=en`。
- `--duration-seconds` 只接受 1–3600，預設 3600。Ctrl+C／SIGTERM 提早停止；連線讀取有 2 秒 timeout，單執行緒循序處理。
- 不寫 process／lock／receipt／研究狀態或日誌檔，不裝依賴、不新建服務／排程。固定 port 已占用時啟動失敗，不選另一個 port。
- 本機入口不能讓另一部手機直接連線，沒有公開部署、私有 HTTPS API 或跨裝置動態服務。

本來源版沒有攜帶真實 state、forecast、seal 或 publication。只有程式的 clone 會明示研究狀態不可用；需要既有合法本機封存產物才能呈現資料。沒有下載、合成真實資料或讀取其他目錄的 fallback。全新的環境安裝尚未驗證；依賴說明沿用 [重現說明](reproducibility.md)。

## 日期、狀態與時間

預設日期由瀏覽器以 Asia/Taipei 的今天計算。固定研究日為 2026-10-21、22、23、26、27；其他日期顯示未排程，必須由使用者明確選擇研究日期。每個研究日固定列出原首門架時窗 06:30–09:15、每 15 分鐘一個，共 12 個 issue；發出時間為原時窗減 45 分鐘。

| 狀態 | 顯示行為 |
|---|---|
| `sealed_shadow_only` | 通過原 canonical publication／seal／forecast 驗證，才展示五候選 |
| `not_due`／`not_published` | 尚未到發出時間／沒有已發布封存；不改用其他時點 |
| `inflight`／`unresolved_*` | 保留進行中或未決狀態，不稱已完成 |
| `insufficient_input` | 核對原不足收據，展示有限輸入摘要，不展示預測 |
| `integrity_error` | 單一 issue 的 hash、時鐘、身份或公開資料結構不符，隔離該筆數值 |
| 整體 `unavailable` | state 缺失、無法讀取或結構不符；數量未知，不能當正常 0/12 |

發出、推論完成、封存、來源觀測區間終點、完整接收與解析完成時刻各自保留。來源摘要只來自該筆已驗封存／不足收據，不呼叫 `current()` 或 `status()`，也不讀 parsed 原始來源檔。來源時間是發出當時的輸入；不是現在路況。完整度取原 `valid_link_slots`／`fixed_link_slots` 及逐 link 門檻計數，不能解讀為道路全覆蓋或真值有效數。

頁面分開顯示上次成功核對伺服器與研究產物時間。按刷新才重新讀取；刷新失敗清掉舊數值並提供重試，不退回最後成功的一批。選日期或時點後，較慢的舊回應不會換回先前日期或時點。瀏覽器每秒依 server offset 更新過窗標示與距核對時間，不做自動 HTTP。`sealed_shadow_only` 是封存狀態，不因開頁而改寫；候選視窗已過期時，數值僅供封存回看，沒有可採取的方案按鈕。

## 唯讀 API 與公開範圍

唯一 API 是 `GET /api/day?day=YYYY-MM-DD`，只接受一個 `day`；不接受自訂成本、deadline、其他欄位或重複 query。有效但不在固定日期內回 `not_scheduled`。固定靜態路由只有 `/`、`/index.html`、`/app.js`、`/style.css`，首頁僅可另帶 `lang=zh` 或 `lang=en`。

每次回應只讀一份有大小上限的 `state.json`，取得一次伺服器時間，傳入 `connected_server.Application(..., frozen_state=..., clock=...)`，對 12 個 nominal 直接呼叫繼承的 `prospective()`。原 program／protocol／input-manifest、publication、seal、forecast、輸入 cutoff 與完成時鐘檢查均沿用，沒有另寫或放寬驗證鏈。沒有建立歷史 Repository 或呼叫 evaluation／truth 工作流。

公開欄位只含日期、issue 身份、發出／封存時刻、原五候選估計與比較值、計數完整度、來源 dataset 與時間範圍、有限原因代碼。不回傳檔案路徑、CPU／模型 artifact 資訊、任意 state、完整來源收據或憑證。state 讀取上限 4 MiB，JSON API 輸出上限 256 KiB。依舊有 verifier 讀取的小型 immutable 檔案不會變成可任意指定的下載路由。

回應有 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`，不提供 CORS；Host／Origin 限本 loopback 同源。CSP 只允許本站 script、style、connect，沒有外部字型／圖示／資產。這是本機來源隔離，並非使用者認證系統。

## 數值與產品邊界

- 國1南下 75→88→92.8→95→98 公里五門架、小客車代碼31；首門架通過時窗與末門架 deadline，不能當住家出發或公司到達。
- 原五候選位移為 −30／−15／0／+15／+30 分鐘；deadline 固定 nominal＋45，成本 λ 固定 0.5。只展示原 empirical 分布基準的四個分位數估計（T/A 各 q50/q90）。
- q90 達截止不是個人準時機率、安全保證或信賴區間。沒有成熟 truth、效益或 CI；未知數值保留未知，不填 0。
- `policy_qualified=false`、`policy_selected_method=null`、`recommendation=null`；沒有推薦或最優方案。策略未合格也不證明照常最佳。
- 介面不產生 source HTTP、fit 或 model forward，不改既有五日排程、研究資料、模型、原候選或 0.2.0 離線 demo。原研究是否收資料與真值成熟，依原流程獨立結算。

## 選定驗證

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python -B -m unittest \
  tests.test_sealed_viewer tests.test_departure_advisor_prospective_server -v

node tests/test_sealed_viewer_ui.js
```

Python 使用既有 synthetic prospective fixture 在暫存目錄建立 publication／seal／forecast，覆蓋同回應快照、刷新新發布、固定身份、無跨日 fallback、缺 state／壞檔、晚 seal／未來輸入、公開投影、同源路由與有界生命週期。JS 測試是 Node 純 controller／呈現契約，覆蓋舊回應競態、時點保留、失敗清值、未排程、過窗、未知與雙語；不取代真實瀏覽器和 viewport 驗收。實際瀏覽器／本機已有封存資料核對由 root 另留證據，不以單元測試或服務啟動當人工接受。
