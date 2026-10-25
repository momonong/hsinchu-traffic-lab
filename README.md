# hsinchu-traffic-lab · 0.2.0

新竹通勤時窗研究原型：把道路時間、調整時窗的代價與末門架截止放在一起比較。

**這個來源版提供可操作的歷史示例、唯讀封存 API 與資料品質契約測試；沒有合格的出發推薦策略。** 可操作的軟體不等於已證明個人節時、少塞車或降低遲到風險。

## 查看既有封存結果

新增獨立、唯讀的封存結果候選介面；原 0.2.0 歷史示例仍保留。

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python -B -m scripts.departure_advisor.sealed_viewer --duration-seconds 3600
```

開啟 `http://127.0.0.1:8768/`（英文 `/?lang=en`）。預設選台灣今天；只有固定五個研究日期可核對，不會自動以歷史日期頂替。頁面列出當日 12 個應發時點、已驗封存數量、五候選估計與發出／封存／來源時間；刷新由使用者觸發。

此入口只透過既有驗證鏈讀取本機已發布資料，不寫研究狀態，不讀現在來源或成熟真值，不執行模型、採集或排程。**Git clone 不攜帶真實封存資料**；缺少研究狀態檔時會明示不可用，不能把它當正常 0/12。服務固定 loopback、最長一小時、Ctrl+C 或 SIGTERM 可停止，不是跨裝置 HTTPS 部署。範圍、API 與選定測試見 [封存檢視器說明](docs/sealed-viewer.md)。

## 執行歷史示例

目前驗證環境為 Ubuntu Linux、Python 3.12。已有相符環境時：

```sh
.venv/bin/python -B -m scripts.departure_advisor.release_demo --duration-seconds 3600
```

開啟 `http://127.0.0.1:8767/`；英文可用 `http://127.0.0.1:8767/?lang=en` 或頁面語言選單。服務只綁 loopback，最長一小時，Ctrl+C 可提早停止。它不寫入研究狀態、不讀憑證、不抓交通來源、不訓練或執行模型，也不建立服務或排程。

新環境可依專案 lock 安裝基礎依賴後執行：

```sh
uv sync --locked
uv run --locked --no-sync python -B -m scripts.departure_advisor.release_demo
```

這是安裝指引，**本次未驗證乾淨環境安裝**。示例需要 NumPy；不需要 Torch、CUDA、模型權重或 `--extra gpu`。Node 僅供 JS 契約測試，不是瀏覽器示例的執行依賴。完整依賴／平台限制見 [重現說明](docs/reproducibility.md)。

## 可以操作什麼

- 歷史模式預設開啟：選日期、原首門架時窗、末門架截止、提前／延後範圍、最低預測道路時間縮短及調時成本，查看完整五個方案並人工暫選。
- 預測與後續群體結果分開；按揭露才顯示已攜帶的歷史結果。任意截止不會以錯誤的 D45 準時率代替，未知保持未知。
- 來源模式明示 stopped；前瞻模式沒有攜帶真實 issue，顯示未發布，不用歷史資料冒充今日預測。

預設例：2026-08-24、首門架 06:45、末門架截止 07:30、最多提前 15 分鐘、最低預測道路時間縮短 5 分鐘。提前 15 分鐘的預測差約 3.12 分鐘、已揭露的不同車群中位差約 3.85 分鐘，均未達 5 分鐘。數值條件符合時也只是探索性比較，不是正式推薦。

## 包含的示例資料及邊界

`prototype/departure-connected-2026-10-22/adapter.js` 內含 **600 列、10 天已曝光校準期的匿名群體彙總示例**（bundled exposed calibration aggregates）：日期／原時窗／位移、四個分位數、有限 D45 群體統計。這份 release 並非只有純程式碼，但不含車輛／逐旅次 ID、個別旅次、原始來源或完整歷史資料。

- 母體是完整通過國1南下 75→88→92.8→95→98 公里五門架的小客車，並非一般竹北到竹科、住家到公司或興隆橋全程。
- 時窗是通過 75 公里首門架的時窗，截止是通過 98 公里末門架，不是離家時間或上班抵達時間。
- q50／q90 是兩個分位點，不是信賴區間或個人準時保證。歷史不同車群比較不是同一人換時間的因果節時。
- 這些日期已曝光，不是獨立保留測試、事前實證或今天有效的建議。原策略仍未合格，`policy_selected_method=None`，顯示基準仍是 empirical；未合格也不代表照常已被證明最佳。

## 此版來源範圍

| 區域 | 本版內容 |
|---|---|
| `scripts/departure_advisor/` | connected API 與其必要 import closure；新增唯讀 `release_demo` 入口 |
| `prototype/departure-connected-2026-10-22/` | 原四個 UI 來源與已授權的歷史聚合示例，原檔 bytes 保留 |
| `prototype/sealed-viewer/` | 獨立雙語封存時窗表，透過單一唯讀 `/api/day` 核對當日 12 個時點 |
| `reports/xinglong-public-feed-2026-10-23/` | 僅 adapter、單次 probe 與兩個 synthetic test 來源 |
| `reports/xinglong-host-peak-2026-10-27/` | 僅 frozen host runner 與候選 template；沒有實機 program／admission／收據 |
| `tests/` | 選定合成 API／時鐘／缺值／封存／顯示／可攜 host 契約 |

興隆橋來源程式只提供兩段觀測品質與時鐘守門；兩段均值不相加成完整 OD ETA。發布不表示即時來源已連通，也不表示 0／15／30 分鐘出發比較已可用。

**支援入口只有上述 demo 與選定測試。** 此版刻意不包含完整科研訓練／模型 forward／採集／成熟 truth 工作流，不能直接重跑舊 collector、supervisor、research CLI 或原本機 server main。部分保留模組內的延後研究 import 及 runtime 合約不在發布範圍；缺少合約時應拒絕，而非自動下載、補資料或換 gate。

## 驗證

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python -B -m unittest \
  tests.test_departure_connected tests.test_departure_connected_ui \
  tests.test_departure_advisor_prospective_server \
  tests.test_departure_advisor_prospective_features \
  tests.test_departure_advisor_prospective_store \
  tests.test_xinglong_host_release tests.test_release_demo -v

python3 -B -S -m unittest discover \
  -s reports/xinglong-public-feed-2026-10-23 -p 'test_*.py' -v
```

本次在精確 allowlist 的 `/tmp` 副本、既有環境通過 **59＋14＝73 項選定測試**，沒有來源 HTTP／fit／forward／真實 truth 讀取。這不是所有歷史研究測試全通過，也不是新環境安裝或使用者接受證據。root 另以實際 loopback 瀏覽器核對預設歷史模式、雙語標籤、五選項／暫選／獨立揭露、修改截止後準時率未知、語言切換、前瞻未發布及來源停止，console errors=[]；測試 server 已正常停止。這不是跨裝置動態部署或使用者接受；Git 交付由 root 另核，不由單元測試替代。

## 資料歸屬與授權

交通欄位語意與來源屬 [交通部 TDX](https://tdx.transportdata.tw/) 及 [高速公路交通資料庫](https://tisvcloud.freeway.gov.tw/)。來源使用與再利用條件仍依各主管機關規定；本專案不另授來源資料權利。專案尚未指定開源授權，不應自行推定 MIT 或其他授權。

原研究資料、模型、媒體、帳號／主機狀態、操作收據、私人預覽 URL 與未授權報告不隨此版發布。本次版本更新不部署服務、不改既有研究或排程。變更見 [CHANGELOG](CHANGELOG.md)。
