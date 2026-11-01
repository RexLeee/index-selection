# 2026-10-29 PR #84 starvation 實驗：腳本與結果

這個目錄保存 [`../2026-10-29-pr84-starvation.md`](../2026-10-29-pr84-starvation.md) 引用的腳本、patch 和彙總結果。

- 這裡不放原始資料。兩輪實驗的 access log、crawler log 和 manual 結果檔留在 2026-10-29 session 的 scratchpad。這裡只放彙總。
- Loki 的兩份 export 含 production 的 URL，也留在 scratchpad。這裡只放 `prod_stats.py` 算出的彙總。
- `run.sh` 預設 WebCrawler 在 `scripts/../wc-cc96500`。重跑前用 `WC=` 指定受測的 WebCrawler 目錄：原版用 PR #84 `cc965006`，修正版用 branch `pr84-weighted-dispatch` 的 `6d23789`。
- `run.sh` 讀這些環境變數：
  - `SHARE`：`weighted` 的 manual 比例，預設 50。
  - `KEEP=1`：一個 job 完成就補一個，維持固定的 job 數。
  - `MOCK_LINKS`、`MOCK_TEXT_REPEAT`：頁面的連結數和文字量，決定 parse 的 CPU 成本。
  - `MOCK_DEAD_EVERY=N`：每 N 個 host 有一個在 20 秒後才回應，超過 crawler 的 15 秒 timeout。
  - 其他 `CRAWLER_*` 變數直接傳給 crawler。

| 路徑 | sha256 前 12 碼 | 內容 |
|---|---|---|
| `scripts/run.sh` | `37157b065cac` | 一組實驗：啟動模擬網站、normal feeder、crawler，送出 job，取樣 CPU，結束時清掉 normal 結果 |
| `scripts/mockweb.py` | `26b3414f59bb` | aiohttp 模擬網站。一個 port 扮演所有 host，每個 host 有固定的延遲中位數 |
| `scripts/simdns.py` | `aec307145d49` | Scrapy DNS resolver，把所有 hostname 解析到 127.0.0.1 |
| `scripts/feeder.py` | `429d81d6f556` | 扮演 offerer，維持 180 個 `domain_*.json`，每檔 100 個 URL |
| `scripts/submit.py` | `ed2c5f59cfc2` | 用 PR 的 `ManualBulkCrawlService.submit` 送 job。可選的補件模式扮演 isel 的 planner |
| `scripts/analyze.py` | `f2be01a0b747` | 從 access log 和 heartbeat 算統計，輸出 `results/summary*.md` 的三張表 |
| `scripts/prod_stats.py` | `a139323d0203` | 從 Loki export 算 production 的延遲和 heartbeat 中位數 |
| `scripts/qshape.py` | `0115200824c5` | 量 production normal 佇列的形狀。實際在 Host2 跑的是同邏輯的前一版 |
| `scripts/fsync_bench.py` | `a9d75e6ec81a` | 照 PR 的 `save()` 寫法重複存檔，量每次的耗時。可選 macOS 的 `F_FULLFSYNC` |
| `capA.patch` | `55a0a6b9dc61` | 第一輪的修改版：manual 每個 host 的上限跟著 delay 走，共 4 行 |
| `results/summary.md` | | 第一輪 6 組實驗的統計。這一輪的 `analyze.py` 還沒有第三張表 |
| `results/timelines.txt` | | 第一輪每 30 秒的 normal 和 manual 速率。第一格只涵蓋部分時間 |
| `results/summary_round2.md` | | 第二輪各組的統計，`analyze.py` 的輸出 |
| `results/fsync_local.txt` | | 本機每次存檔的耗時：`fsync` 和 `F_FULLFSYNC` |
| `results/production_loki.txt` | | Host2 Loki：回應延遲分佈、16 個 worker 的 heartbeat 中位數 |
| `results/production_queue.txt` | | Host2：normal 佇列形狀、版本、CPU、每日請求量 |

## 第三輪（2026-10-30，Host3）

- 原始資料（access log、crawler log、manual 結果的 manifest）約 3 GB，留在 2026-10-30 session 的 scratchpad。這裡只放彙總。
- 受測的 WebCrawler：修正版加階段 A 測試的 `f5a7b93`，以及 production 的 `39e1363`。`main3.lanes` 裡的路徑是 Host3 上的位置。
- 在 Host3 上重跑：`main3.sh` 設定 `CPU0`、`HELPER_PIN`、`PYVER`，然後呼叫 `lanes3.sh`。
- 本機分析 Host3 的資料時要設 `TZ=UTC`。heartbeat 的時間沒有時區，`analyze3.py` 用本機時區解析。
- 主批次跑的 `run3.sh` 是 pgrep 修正前的版本。那一版的 pattern 會用前綴比對到兄弟 lane，例如 F5 比對到 F5_0。現在的版本在 pattern 加了 `/queue/`，其他部分相同。

| 路徑 | sha256 前 12 碼 | 內容 |
|---|---|---|
| `scripts/run3.sh` | `37ab82fffc91` | 一條 lane：模擬網站、feeder、crawler（AutoThrottle、Scrapy 2.19.0）、isel 模擬器。可綁核，`crawl_result` 可放 `/dev/shm`，每 30 秒刪舊檔 |
| `scripts/lanes3.sh` | `63edac4ddb7a` | 讀 lane 表，同時跑多條 `run3.sh`。第 i 條用 `PORT0+i` 和 CPU `CPU0+i` |
| `scripts/main3.lanes` | `0f67a65c1fe4` | 主批次的 10 條 lane |
| `scripts/main3.sh` | `6492a643e456` | 主批次：跑全部 lane，暖機檢查 F0a，最後跑 `analyze3.py` |
| `scripts/mockweb3.py` | `eb479e4de53e` | 模擬網站。可設死 host、robots.txt 拒絕、403、redirect、404、429 |
| `scripts/feeder3.py` | `b47c6468e8e0` | 扮演 offerer。normal host 加上 recrawl 也會用到的熱門 host |
| `scripts/iselsim.py` | `1399a297c637` | 扮演 isel 的 recrawl planner，用 PR #84 的 `ManualBulkCrawlService` 送 job。第四輪起一個 host 同時只在一個 job 裡，和 isel 相同。第五輪起一個 host 每次拿滿 `HOST_DOCS_PER_JOB` 筆，不被 job 的剩餘量截短。第三輪用 `b65ed1832161`，第四輪用 `3ae4d0e543f7` |
| `scripts/analyze3.py` | `19ff5c485ca3` | C1–C4 的統計和判定。`--from`、`--minutes` 選時間窗 |
| `scripts/p0_gap.py` | `efc741c47d35` | P0：忙碌 slot 的平均送出間隔 ÷ delay |
| `scripts/diag3_failures.py` | `1d522fe834c7` | F5：依 host 類別（死、robots 拒絕、健康）拆開等待時間和 C2 超標 |
| `scripts/diag3_burst.py` | `4dd44c8505ee` | 每個 recrawl host 最忙的一分鐘 ÷ 它的 slot 速率 |
| `scripts/diag3_hot.py` | `8e56a34a13ea` | F4：熱門 host 的 normal 保留和 slot 餘裕 r − u |
| `scripts/diag3_save.py` | `2209c28cae0a` | 每次 manual 存檔的毫秒數隨時間的變化 |
| `scripts/main4.lanes` | `af286d9d362e` | 第四輪的 5 條 lane |
| `scripts/main4.sh` | `9b7086504ef4` | 第四輪的批次：跑全部 lane，暖機檢查 R0，最後跑 `analyze3.py`、`diag4_bighost.py`、`diag3_burst.py` |
| `scripts/diag4_bighost.py` | `d3f5ae7be88f` | 大 host 的 recrawl 速率、兩個 job 的間隔、抓完所需天數 |
| `scripts/golden_cap.py` | `7aebbedc6562` | 64M 加每 hostname 上限時的 golden coverage |
| `scripts/host_counts.py` | `f8d545c23eb8` | 64M（上限 241,920）每個 host 的 URL 數 |
| `scripts/route_demand.py` | `e3009d568b31` | 每 process 的 recrawl 需求、被限制 hostname 的 slot 餘裕、`w03_sizes` |
| `scripts/prod3_*.py` | | Host2 Loki 的查詢：速率、latency、heartbeat、每 host 的 u 和 60 秒峰值 |
| `results/production_2026-10-30.txt` | | Host2 的現況：版本、節流設定、部署前後的速率和峰值、recrawl 需求 |
| `results/golden_coverage_64m.txt` | | 128M、64M 和各種上限的 golden coverage |
| `results/calibration_host3.txt` | | Host3 的校準、P0、主批次的暖機檢查 |
| `results/analysis_round3.md` | | 主批次的 `analyze3.py` 原始輸出 |
| `results/summary_round3.md` | | 第三輪的判定表和診斷 |
| `results/analysis_round4.md` | | 第四輪的原始輸出：`analyze3.py`、`diag4_bighost.py`、`diag3_burst.py` |
| `results/summary_round4.md` | | 第四輪：isel 的「一個 host 同時只在一個 job 裡」規則下的 C2 和 C3 |

第四輪的原始資料約 1.3 GB，同樣留在 2026-10-30 session 的 scratchpad。

## 第五輪（2026-10-31，Host3）

- 受測的 WebCrawler 是 branch `pr84-weighted-dispatch` 上的本地 commit：
  - `381c08d`：每個 host 的 recrawl 請求按固定網格交給 scheduler，同時可以有 ceil(latency / delay) + 1 個。5c 用這版。
  - `e1ae98d`：把 `2436f4b` 到 `381c08d` 合併成一個 commit，內容和 `381c08d` 相同。合併後，這四個 commit 只留在本機 WebCrawler 的 reflog。
  - `d61e4bf`：間隔從實際交出的時間算起，同時只有 1 個。5a 的 A1–A5 用這版。
  - `2436f4b`：間隔從實際交出的時間算起，同時可以有 ceil(latency / delay) + 1 個。5a 的 N3 用這版。
  - `804632f`：間隔從請求送出的時間算起。本機 smoke 的中位數間隔變成 5.77 秒，所以沒有上 Host3。
- 5a 跑了 8 分鐘的 job 後停掉，原因見 `results/round5a_stopped.txt`。
- 5b 在暖機檢查自動中止：R0 是 38.2 req/s，超過 31–38 的範圍。5c 把頁面權重從 w = 0.38 調到 0.42。
- `main5.lanes`、`main5.sh` 是 5c 的版本。路徑是 Host3 上的位置。

| 路徑 | sha256 前 12 碼 | 內容 |
|---|---|---|
| `scripts/main5.lanes` | `9bc908ec34d1` | 5c 的 6 條 lane |
| `scripts/main5.sh` | `d3e13654da8f` | 5c 的批次：跑全部 lane，暖機檢查 R0，最後跑 `analyze3.py`、`diag4_bighost.py`、`diag5_pace.py` |
| `scripts/diag5_pace.py` | `9f039af7d2f0` | 每個 recrawl host 相鄰兩次請求在模擬網站端的間隔，以及最忙的 60 秒 |
| `scripts/diag5_gapsplit.py` | `166ee0d52924` | 同上，熱門 host 和其他 host 分開算 |
| `scripts/left14.py` | `6bc7329b23f6` | 每 host 速率固定時，process 03 在 14 天後抓不完的 host 和 URL |
| `results/smoke5_local.txt` | | 本機 smoke：三種間隔做法的間隔和速率 |
| `results/round5a_stopped.txt` | | 5a 停掉前 8 分鐘的速率、間隔、latency 和 outstanding |
| `results/analysis_round5.md` | | 5c 的原始輸出：`analyze3.py`、`diag4_bighost.py`、`diag5_pace.py`、`diag3_hot.py`、`diag5_gapsplit.py`、`left14.py` |
| `results/summary_round5.md` | | 第五輪的判定表和診斷 |

5c 的原始資料約 1.6 GB、5a 約 0.86 GB，留在 2026-10-31 session 的 scratchpad。

## 第六輪（2026-10-31，Host2 Loki 和本機）：redirect 和 retry

- 問題：redirect 和 retry 的請求不經過 `refill()`，所以固定間隔只控制每個 URL 的第一個請求。
- Loki 只讀取 Host2 10-29 20Z 到 10-30 02Z 的 normal crawl，沒有送出任何請求。
- 本機 smoke 用 `e1ae98d` 對照 `f24189c`。`f24189c` 把每次 redirect 和 retry 記進該 host 的網格。

| 路徑 | sha256 前 12 碼 | 內容 |
|---|---|---|
| `scripts/prod5_extra.py` | `329a5da38726` | Host2 Loki：每個大 host 每完成一個 URL，redirect 和 retry 多送幾個請求。`@RX@` 換成 `hosts_rx` 的 regex |
| `scripts/diag6_extra.py` | `61839d9ba6bc` | 每個 recrawl host 含 redirect 和 retry 的速率，以及最忙的 60 秒 |
| `results/redirect_extra_2026-10-31.txt` | | 128 個有流量的大 host，各自多送的請求比例 |
| `results/smoke6_local.txt` | | 本機 smoke：30% redirect、5% 429，記帳前後的速率 |
