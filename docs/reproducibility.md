# 0.2.0 重現範圍

## 支援與不支援

支援：`python -B -m scripts.departure_advisor.release_demo` 和 README 明列的 synthetic 測試。預設 127.0.0.1:8767，1–3600 秒，單 owner bind，沒有持久服務或背景工作。主介面沿用原四個前端檔；wrapper 僅在回傳 HTML 注入雙語歷史示例標籤，以及選取既有 replay 控件的開頁動作，不改原 UI bytes。

不支援：完整研究重訓、原模型 forward、舊 source collector／supervisor／truth CLI、真實封存研究回放或原本機 server main。此版刻意排除其 runtime 合約／輸入／權重，不能把缺少延後模組當作應自行補抓資料的訊號。正常 import 的必要 local closure 已納入；`prospective.py` 的延後 `prospective_inference`／`prospective_truth` 分支不屬公開支援入口。

## 範例資料

adapter 內含 600 列、10 日已曝光 calibration 群體彙總（bundled exposed calibration aggregates），不是 pure-code-only、OOS 或今天路況。匿名示例沒有車輛／逐旅次 ID、軌跡或憑證。四個分位數估計（T/A 各 q50/q90）保持原值；僅攜 D45 的部分事後群體統計，改截止時不能虛構準時比例。預測、實際後續結果與政策資格分開，不做推薦。

國道 75→98 公里完整通過母體和興隆橋兩段局部觀測是不同資料問題。兩段觀測沒有完整 OD 旅次／0、15、30 分鐘事前預測或個人效益證據。原五日前瞻的 policy=None／display empirical 不變，並未因發佈而晉升模型。

## 依賴

- 已驗證既有環境：Ubuntu Linux、Python 3.12、專案既有 NumPy；host 測試使用 Linux `/proc`、`fcntl`、`resource`。
- Node 用於測試實際前端純函數與 fake DOM。瀏覽器示例本身不需要 Node。
- 基礎 `uv sync --locked` 不要求 `--extra gpu`；本次沒有安裝套件／下載依賴，未驗證乾淨環境安裝。Torch／CUDA 不在這個示例的必要 import 路徑，也沒有發布模型權重。
- pyproject／lock 保留原套件解析與 optional GPU index，僅專案自身版本 0.1.0→0.2.0。不能將保留的 index 當成已驗 CPU-only ML 環境。

## 選定測試證據

在 `/tmp` 的 explicit source snapshot 執行 README 的完整兩組命令：59 個 API／feature／store／host／UI 契約＋14 個 feed/probe 契約，共 73，exit0。CPU affinity 限一個核心，無 HTTP、credential、fit、forward 或真實 outcome 資料讀取。新 wrapper 先以缺實作得到 5 個預期紅燈，再由實作轉綠。

此處宣稱的是此版選定測試，不是歷史專案全部測試。原 `test_departure_advisor_collector.py` 會讀真實 source-admission bodies；原 portable parity 與 next-window parity 測試會讀本機研究 artifacts，不隨這份來源發布，也未在此次執行。

新 `test_xinglong_host_release.py` 沿用既有 15 個 host 契約，將 root 位置改為動態 checkout、將操作 program 改為 `tests/fixtures/host_peak_program.json` 合成設定。它只呼叫 fake transport；原 stage 的 host runner、template、program、admission、units 及原測試完全不改。

root 已在同一 allowlist snapshot 的實際 loopback 瀏覽器完成 8 項互動核對：預設歷史模式／雙語 600 列 10 日標籤、五選項／暫選／獨立揭露、修改 D=07:31 後 Enter 且準時比例未知、英文切換、前瞻未發布、來源停止，以及 console errors=[]。本輪測試 server 已正常停止。這不是跨裝置動態部署或人工接受；Git merge／push／tag 由 root 另行核對，亦未宣稱 GitHub CI 或全新作業系統已驗證。

## 來源安全邊界

正常 demo 只有固定靜態路由與 `/api/status`、空索引 `/api/prospective`；其他路由 404。無任意檔案 document root，不提供真實 archive／truth／模型路由，不讀現有 `.state` 或研究 state。缺漏保持 unknown/unpublished，不造 0 或以舊值取代。

host runner 僅以原路徑保存供查核；其來源執行另需 program／root admission 與 systemd 執行身分，這些操作設定均未發布。once_probe 則是保留的歷史研究來源，其 CLI 的 --execute 會讀本機 .state 憑證並嘗試真實來源，並不檢查 host program／root admission。它不是本版支援入口，不要把 --execute 當 demo 或離線測試執行，也不要手動重送現有未知 intent。來源程式存在本身不代表已取得來源執行權限。

## 授權與保留

未替使用者選定開源 license；來源資料歸屬與再利用條件仍需依主管機關。公開範圍僅本版明列來源、測試及已授權歷史聚合示例。原 raw/processed/work/outputs、模型、媒體、帳號、操作收據、私人預覽 URL、研究報告／state／program 均未納入。版本更新本身沒有部署、啟停或改動原排程。
