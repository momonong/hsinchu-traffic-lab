# hsinchu-traffic-lab · 0.2.1

新竹通勤時窗研究原型：把道路時間、調整時窗的代價與末門架截止放在一起比較。

**這個來源版提供可操作的歷史示例、唯讀封存 API 與資料品質契約測試；沒有合格的出發推薦策略。** 可操作的軟體不等於已證明個人節時、少塞車或降低遲到風險。

套件版本為 **0.2.1**。已發布的 0.2.1 增量為公開彙總文件與版本 metadata；本次賽事收尾另加入離線歷史比較指令與三份固定紀錄，驗證範圍見下方。模型、依賴與排程未改。

## 晨間研究快照

[公開晨間摘要](docs/research/morning-overview-2026-10-27/README.md) 分開保存 10/26 與 10/27 的核對狀態。截至 **2026-10-27 09:31:52（台灣）**，全 60 應發時點為 36 封存、24 缺測、0 未到期；真值、效益與 CI 仍未知。只發布已授權彙總與來源 digest，原研究資料及操作收據不附，也不以後續結果更新此快照。

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

這是安裝指引，**本次未驗證乾淨環境的網路安裝**。示例需要 NumPy；不需要 Torch、CUDA、模型權重或 `--extra gpu`。Node 供下方離線比較指令及 JS 契約測試使用，不是瀏覽器示例的執行依賴。完整依賴／平台限制見 [重現說明](docs/reproducibility.md)。

## 可以操作什麼

- 歷史模式預設開啟：選日期、原首門架時窗、末門架截止、提前／延後範圍、最低預測道路時間縮短及調時成本，查看完整五個方案並人工暫選。
- 預測與後續群體結果分開；按揭露才顯示已攜帶的歷史結果。任意截止不會以錯誤的 D45 準時率代替，未知保持未知。
- 來源模式明示 stopped；前瞻模式沒有攜帶真實 issue，顯示未發布，不用歷史資料冒充今日預測。

預設例：2026-08-24、首門架 06:45、末門架截止 07:30、最多提前 15 分鐘、最低預測道路時間縮短 5 分鐘。提前 15 分鐘的預測差約 3.12 分鐘、已揭露的不同車群中位差約 3.85 分鐘，均未達 5 分鐘。數值條件符合時也只是探索性比較，不是正式推薦。

## 重現賽事歷史比較

在 checkout 根目錄執行；只需 Node，無需 Python、服務、來源連線或模型：

```sh
node scripts/contest_replay.cjs --scenario undisclosed
node scripts/contest_replay.cjs --scenario disclosed
node scripts/contest_replay.cjs --scenario changed-deadline
```

JSON 寫至 stdout，固定版本為 `contest-replay-v1`。三份[公開紀錄](docs/contest/replay/)保存同一個 2026-08-24／06:45 案例：提前上限 15、延後上限 30、最低道路縮短 5 分鐘、成本 0.5。完整五列保留，符合數值條件 0、正式推薦 0，完整遲到風險差保持 `null`。

`undisclosed` 不包含後續群體數值；`disclosed` 才附上既有部分群體結果。`changed-deadline` 將截止改為 07:31 並保持揭露狀態，所有觀測準時比例為 `null`，原預測不變。紀錄包含實際 adapter SHA256、輸入、條件檢查與限制，沒有執行時間或本機路徑；同來源重跑產生相同 bytes。逐檔比對方式與數值解讀見[重現說明](docs/reproducibility.md#賽事比較紀錄contest-replay-v1)。

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
| `scripts/contest_replay.cjs`、`docs/contest/replay/` | 既有公開 adapter 的離線比較指令與三份版本化 JSON |
| `prototype/departure-connected-2026-10-22/` | 原四個 UI 來源與已授權的歷史聚合示例，原檔 bytes 保留 |
| `prototype/sealed-viewer/` | 獨立雙語封存時窗表，透過單一唯讀 `/api/day` 核對當日 12 個時點 |
| `reports/xinglong-public-feed-2026-10-23/` | 僅 adapter、單次 probe 與兩個 synthetic test 來源 |
| `reports/xinglong-host-peak-2026-10-27/` | 僅 frozen host runner 與候選 template；沒有實機 program／admission／收據 |
| `tests/` | 選定合成 API／時鐘／缺值／封存／顯示／可攜 host 契約 |

興隆橋來源程式只提供兩段觀測品質與時鐘守門；兩段均值不相加成完整 OD ETA。發布不表示即時來源已連通，也不表示 0／15／30 分鐘出發比較已可用。

**支援入口為上述 demo、離線比較指令與公開測試。** 此版刻意不包含完整科研訓練／模型 forward／採集／成熟 truth 工作流，不能直接重跑舊 collector、supervisor、research CLI 或原本機 server main。部分保留模組內的延後研究 import 及 runtime 合約不在發布範圍；缺少合約時應拒絕，而非自動下載、補資料或換 gate。

## 驗證

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/python -B -m unittest \
  tests.test_departure_connected tests.test_departure_connected_ui \
  tests.test_departure_advisor_prospective_server \
  tests.test_departure_advisor_prospective_features \
  tests.test_departure_advisor_prospective_store \
  tests.test_xinglong_host_release tests.test_release_demo \
  tests.test_sealed_viewer tests.test_contest_replay -v

python3 -B -S -m unittest discover \
  -s reports/xinglong-public-feed-2026-10-23 -p 'test_*.py' -v

node tests/test_sealed_viewer_ui.js
```

2026-10-27 賽事收尾在隔離 checkout、其自身離線 lock 環境（Ubuntu Linux、Python 3.12.3、NumPy 2.3.5、Node 22.22.2）通過 **82＋14＝96 項公開 Python 測試**與既有 Node 封存 viewer 契約。新增 10 項先觀察缺少指令的紅燈，再由實作轉綠；包含三份紀錄 bytes 比對與複製公開 adapter 後注入缺預測的 null 邊界。未讀原研究資料或執行來源請求、fit、forward、truth 工作流；本次命令驗證不等於媒體／瀏覽器驗收。

既有 **0.2.0 歷史 demo** 在精確 allowlist 的 `/tmp` 副本、既有環境通過 **59＋14＝73 項選定測試**，沒有來源 HTTP／fit／forward／真實 truth 讀取。這不是所有歷史研究測試全通過，也不是新環境安裝或使用者接受證據。root 當時另以實際 loopback 瀏覽器核對預設歷史模式、雙語標籤、五選項／暫選／獨立揭露、修改截止後準時率未知、語言切換、前瞻未發布及來源停止，console errors=[]；測試 server 已正常停止。這不是跨裝置動態部署或使用者接受；當時的 0.2.1 文件／版本更新沿用這份既有證據，沒有重新執行此測試組或宣稱新部署。本次賽事收尾的新測試範圍另列於上一段。

## 資料歸屬與授權

交通欄位語意與來源屬 [交通部 TDX](https://tdx.transportdata.tw/) 及 [高速公路交通資料庫](https://tisvcloud.freeway.gov.tw/)。來源使用與再利用條件仍依各主管機關規定；本專案不另授來源資料權利。專案尚未指定開源授權，不應自行推定 MIT 或其他授權。

原研究資料、模型、媒體、帳號／主機狀態、操作收據、私人預覽 URL 與未授權報告不隨此版發布。本次版本更新不部署服務、不改既有研究或排程。變更見 [CHANGELOG](CHANGELOG.md)。
