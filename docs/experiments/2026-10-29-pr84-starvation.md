# 實驗紀錄：PR #84 手動批次爬取 API 的 starvation

- 日期：2026-10-29（UTC）
- 對象：WebCrawler PR #84（手動批次爬取 API）。實驗用 PR 的 head `cc965006`。PR 在同一天 08:02:56 UTC merge，merge commit 是 `5485bcd`，當時的 head 仍是 `cc965006`。
- 修正版：本地 WebCrawler 的 branch `pr84-weighted-dispatch`，commit `6d23789`，從 `cc965006` 分出。還沒 push，也還沒開 PR。
- 環境：本機 Apple M4（10 核）、macOS 26.6.2、Python 3.12.11、Scrapy 2.16.0、Twisted 26.4.0、aiohttp 3.14.5。第一、二輪時 production 是 Python 3.12.13，Scrapy 和 Twisted 的版本相同。2026-10-29 17:29Z 重建 image 後，production 改成 Python 3.12.16、Scrapy 2.19.0（§9）。
- 校準資料：Host2 的 Loki 和 crawler container，全部是唯讀操作（附錄 C）。
- 腳本、patch 和彙總結果：[`2026-10-29-pr84-starvation/`](2026-10-29-pr84-starvation/README.md)。

---

## 1. 結論

- **原版在兩個方向都會 starvation：**
  - `manual_first` 是 merge 後的預設。它讓 normal crawl 只剩 1%，manual 也只有 6.1 req/s。
  - `balanced` 讓 normal 剩 13%，manual 只有 6.6 req/s。兩邊加起來只有原本總量的 26%。
- **根本原因是每個 host 的上限太高。** 一個 host 可以掛 16 個 manual 請求，但 AutoThrottle 每個 delay 只放一個出去。所以 88% 的位子在排隊，什麼也沒下載。派發順序再公平，也解決不了這個問題（§2）。
- **修正版讓兩個方向都有可量測的保證（§6）：**
  - share 50%：manual 拿到 50.0% 的請求。normal 和 manual 分別是 28.5 和 28.4 req/s，總量是同時段 normal-only 對照組的 123%。
  - share 30%：manual 拿到 30.0%。normal 是對照組的 86%。
  - 每 10 個 host 有 1 個死掉時，normal 仍有無死 host 時的 95%。
  - CPU 吃滿時，manual 仍拿到 49.9%。
  - 只送 4 個 job 時，manual 用不完份額，normal 拿走其餘容量。總量是對照組的 102%。
  - 在兩邊都有待抓量的組裡，每邊每 30 秒的最低速率都在該邊平均速率的 94% 以上。
- **兩個設計試過後刪除了（§5.5）：** 佔用保護讓比例偏離設定。待派發上限讓總量掉到 88%，job 也完成不了。
- **部署狀態（§7）：** 10-29 沒有任何主機跑 PR #84。照 merge 後的預設部署，normal crawl 會在送出 job 後停擺。
- **第三、四輪（10-30，Host3，§9）：** p = 20、64M recrawl 時，C1 和 C4 成立。crawler 的 recrawl 是每 process 8.9 req/s，需求是 4.0。
  - C2 在每 job 30 筆時成立。120 筆時，`www.espn.com` 每分鐘估計 68–81 次，門檻是 67。
  - C3 對大 host 不成立。isel 讓一個 host 等它的 job 完成才給下一批，所以大 host 只有 0.02–0.07 req/s。process 03 有 15–45% 的 URL 在 14 天內抓不完。
  - 要同時滿足 C2 和 C3，要調 isel 的 job 數，或在 crawler 加每 host 的速率上限（§9.7）。
  - 現行 production（`39e1363`、`manual_first`）收到 job 時，normal 只剩 9%。
  - 每次存檔在 Linux NVMe 上是 0.86–0.98 ms，最高一段是 3.6 ms。
- **第五輪（10-31，Host3，§9.8）：** crawler 對每個 host 的 recrawl 用固定間隔，isel 改用大批次。C2 和 C3 同時成立。
  - 最好的設定是 A3：間隔 4.8 秒、`min_gap_s` 4.8、每 job 每 host 240 筆、輪詢 15 秒。大 host 是 0.198 req/s，241,920 筆要 14.0–14.5 天。
  - 一個 host 一分鐘的 recrawl 最多 15–16 次。加上 normal 最多 24 次，R0 是 21 次。
  - normal 保有 97–99%，recrawl 佔 15.9%。
  - slot 餘裕 r − u 低於 0.25 的熱門 host，normal 只剩 19–33%。Host2 被限制的 hostname 餘裕都 ≥ 0.34。
  - isel 維持 48 個 job、每 job 120 筆時，20% 的比例被用滿，大 host 只有 0.047 req/s。
- **10-31 之後的狀態：**
  - crawler 的固定間隔合併成一個本地 commit `e1ae98d`，內容和第五輪用的 `381c08d` 相同，136 passed。push、開 PR、在 PR #84 留言都要先經使用者同意。
  - isel 改用 A3 的設定，是本地 commit `a842519`。`max_host_days` 同時改成 13.44，所以上限仍是 241,920，64M 清單不變。301 passed。
  - redirect 和 retry 的請求不經過間隔控制。Host2 的 normal crawl 裡，64M 清單的 232 個大 host 每完成一個 URL 多送 0.071 個請求，p90 0.237。被限制的 `www.cnnbrasil.com.br` 是 0.378，照算會到約 0.27 req/s，超過 C2。
  - 本地 commit `f24189c` 把每次 redirect 和 retry 往後推一個間隔。本機 mock 網站設 30% redirect、5% 429 時，每 host 速率從 p50 0.285 降到 0.207，最忙一分鐘從 27 降到 16。139 passed。數據見 `results/smoke6_local.txt`。
  - 代價：會 redirect 的 host 抓得比較慢。cnnbrasil 如果 recrawl 也是 0.378，241,920 筆要約 18.6 天。清單存的是 redirect 後的網址，所以實際比例應該更低，canary 時要量。
  - canary 還不能做。Host2 的 normal crawl 從 10-30 19:00Z 起是 0 req/s（YugabyteDB 遷移後），所以沒有基準可比。`/opt/WebCrawler` 也只有 `webcrawler` 群組能寫，修正版要經 PR 由維護者部署。

## 2. PR #84 怎麼分配下載容量

### 2.1 機制

PR #84 讓每個 crawler process 有兩個 scheduler 佇列：normal 和 manual。manual 佇列由一個 consumer 補件。

- **consumer 的補件規則：** 每個 host 最多有 slot concurrency 個 manual 請求在外面，production 是 16。全部 host 加起來最多 `CONCURRENT_REQUESTS` 個，production 是 256。consumer 先補「剩餘服務時間最長」的 host，這個順序叫 LRPT（longest remaining processing time，剩下最多工作的先做）。
- **Scrapy 的全域上限：** `Downloader.active` 包含已經進 slot、但還在 slot 佇列裡等 delay 的請求。`active` 達到 256 時，engine 停止從 scheduler 取請求。
- **slot 的放行速度：** slot 每個 `delay` 只啟動一個請求。AutoThrottle 把 delay 調向 `latency / AUTOTHROTTLE_TARGET_CONCURRENCY`。production 設 2.0，所以 delay 約是 latency 的一半。

結果是：一個 host 同時在下載的請求只有 `latency / delay` ≈ 2 個。其餘最多 14 個在 slot 裡排隊，卻都算進全域的 256。16 個這樣的 host 就佔滿 256 個位子。

### 2.2 為什麼兩種 policy 都會 starvation

- **`manual_first`（merge 後的預設）：** scheduler 只在 manual 佇列是空的時候才派 normal。consumer 一直補 manual，所以 normal 幾乎拿不到派發。
- **`balanced`：** 派發次數一邊一次。但一個 manual 請求在 slot 裡要排在同 host 的十幾個請求後面，佔位時間很長。由 Little's law（平均佔位數 = 速率 × 平均佔位時間）：
  - 只跑 normal 時：256 ÷ 50 req/s ≈ 5.1 秒。
  - `balanced` 加 manual 後：256 ÷ 13.2 req/s ≈ 19 秒。
  - `manual_first` 加 manual 後：256 ÷ 6.6 req/s ≈ 39 秒。

  位子空出來的速度變慢，派發次數再公平也沒用。兩邊的速率一起掉。

### 2.3 PR 的歷史

- `d88cce7`（10-20）加入「manual 最多佔 50%」的上限。`00b2353`（10-21）改成 round robin 時把它移除了。
- TA 在 10-20 的留言要求兩件事之一：一個可以設定的分配比例，例如 50/50 或 70/30；或者提出實證，證明兩個方向都不會 starvation。
- PR 自己的 scenario 測試也跑真的 `HtmlSpider`，也把 DNS 指到本機。但它沒有 normal 流量、server 零延遲、關掉 AutoThrottle，所以量不到這個問題。
- API 依 registered domain 把 URL 分給 crawler（`containers/api/routing.py`），和 normal 排程相同。所以 normal 和 manual 會共用同一個 host 的 slot。

## 3. 方法

### 3.1 Harness

每組實驗跑一個 crawler process，用的是受測版本的 `HtmlSpider` 原始碼。腳本在 `2026-10-29-pr84-starvation/scripts/`。

| 元件 | 做什麼 |
|---|---|
| `run.sh` | 啟動以下元件，取樣 CPU，結束時清掉 normal 的結果檔 |
| `mockweb.py` | aiohttp 模擬網站。一個 port 扮演所有 host，每個 host 有固定的延遲中位數 |
| `simdns.py` | Scrapy 的 DNS resolver，把所有 hostname 解析到 127.0.0.1 |
| `feeder.py` | 扮演 offerer。維持 180 個 `domain_*.json`，每檔 100 個 URL |
| `submit.py` | 用 PR 的 `ManualBulkCrawlService.submit` 送 job。`KEEP=1` 時扮演 isel 的 planner：一個 job 完成就補一個 |
| `analyze.py` | 從模擬網站的 access log 和 crawler 的 heartbeat 算統計 |

crawler 的設定照 production：

- `CONCURRENT_REQUESTS` 256，`CONCURRENT_REQUESTS_PER_DOMAIN` 16，`DOWNLOAD_DELAY` 0.25 秒。
- AutoThrottle 開啟：start delay 0.5 秒，max delay 10 秒，target concurrency 2.0。
- `DOWNLOAD_TIMEOUT` 15 秒，`RETRY_TIMES` 1。這兩個是程式預設值，沒有覆寫。

### 3.2 校準

| 項目 | production（Host2） | 模擬 |
|---|---|---|
| 回應延遲 | Loki `download_latency` p50 3,543 ms、p90 7,489 ms；每 host 中位數的 p50 3,856 ms | 每 host 中位數取 lognormal，中位數 3.5 秒、σ 0.55，上限 12 秒 |
| normal 佇列 | 每個 crawler 約 180 個檔，每檔中位數 100 個 URL | 180 個檔，每檔 100 個 URL |
| heartbeat 中位數 | inflight 208、transferring 120、slot_queue 57 | 只跑 normal 時 inflight 256、transferring 約 177 |
| 每 process 的速率 | 約 18–21 req/s，CPU 約 93% | 約 50 req/s，CPU 約 30% |
| job 形狀 | isel 的 `host_docs_per_job` 120、`max_jobs_inflight` 48 | 48 個 job，每個 job 在這個 process 有 2 個 host、每 host 120 個 URL |

一個 isel job 約有 29 個 host，分散到 16 個 process，所以每個 process 約分到 2 個。每 process 的速率有兩個來源：Loki 樣本是 289 req/s，metricdb 是 10-21 到 10-25 每日 2,810–3,000 萬次，也就是 325–347 req/s。兩者都除以 16 個 process。

#### 模擬的實測和 production 的比對

上表是模擬的設定值。下表比對模擬實際跑出來的數字，資料來自 normal-only 對照組 `N0`。

- 延遲取 `N0` 全程的 25,266 筆 `request.end`，欄位是 `download_latency_ms`。
- 每 host 中位數只算至少 3 筆的 host，共 314 個。
- heartbeat 取後三分之二的中位數，避開開頭的爬升期。

| 指標 | production（Host2） | 模擬（`N0`） | 判斷 |
|---|---|---|---|
| 下載延遲 p50 / p75 / p90 / p99（ms） | 3,543 / 4,877 / 7,489 / 12,883 | 3,287 / 4,860 / 6,894 / 11,944 | 相近。模擬低 0–8% |
| 每 host 中位數的 p50 / p90（ms） | 3,856 / 8,353 | 3,496 / 6,834 | 模擬的慢 host 偏少 |
| heartbeat `slot_queue` | 57 | 60 | 相近 |
| heartbeat `inflight` | 208 | 252 | 不同：限制條件不同 |
| heartbeat `transferring` | 120 | 189 | 不同：同上 |
| heartbeat `engine_slot_inprogress` | 約 400 | 261 | 不同：同上 |

- **相近的部分是輸入。** 延遲分佈和 slot 裡排隊的數量都接近 production。starvation 的成因（§2.1）取決於這兩項和 Scrapy 的計數規則，而 Scrapy 和 Twisted 的版本也相同。
- **不同的部分是限制條件。** production 受 CPU 限制：回應等著 parse，所以 `engine_slot_inprogress` 約 400，engine 在 inflight 只有 208 時就停止取請求。模擬受 256 個位子限制，CPU 約 30%。模擬網站的頁面比較小，parse 的成本比較低。
- **CPU 吃滿組涵蓋一部分差異。** 第二輪的 `cpu` 組加重頁面，讓只跑 normal 時的 CPU 達到 97–98%，速率 24.5 req/s，接近 production 的每 process 速率（§6）。
- **機制的檢查。** 原版的一個 host 掛 16 個 manual 請求，約 2 個在下載。理論上排隊比例是 14 ÷ 16 = 87.5%，第一輪 `manual_first` 實測是 88%（§4）。

所以模擬可以還原 starvation 是否發生，以及兩邊分配的比例。它不能還原 production 的絕對速率，例如「總量是對照組的 123%」。production 的數字要等 R3 在 process 07 上試跑才能量到。

### 3.3 時間窗和指標

- 每組先只跑 normal 240 秒，然後送 job，再跑 360 秒。
- `before` 是送 job 前的 90 秒。`after` 是送 job 後第 60 秒到結束，共 300 秒。
- 速率從 access log 算，用 10 秒一格，取平均和 95% 信賴區間。這個區間假設各格獨立，所以偏樂觀。
- `normal kept` = after 的 normal 速率 ÷ before 的 normal 速率。
- `waiting share` = heartbeat 中位數的（inflight − transferring）÷ inflight，也就是在 slot 裡排隊的比例。
- `manual share` = after 窗內 manual 請求數 ÷（normal + manual 請求數）。
- `lowest 30 s` = after 窗內，每 30 秒一格的最低速率。
- **before 窗會高估基準。** 第二輪加跑了不送 job 的對照組 `N0`。它的 normal 在 before 窗是 50.7 req/s，在 after 窗只有 46.4 req/s，也就是 91%。所以 `normal kept` 和「總量」兩欄都低估了約 9%。§6 的判定改用 `N0` 在 after 窗的速率當基準。第一輪沒有對照組，表格照原樣列出。

## 4. 第一輪：原版和每 host 上限（capA）

capA 是 PR 原版加 4 行：manual 每個 host 的上限改成 `ceil(latency / delay) + 1`（§5.1）。`keep` 表示用 `KEEP=1` 一直維持 48 個 job。

| run | normal kept | manual req/s | 總量（after ÷ before） | waiting share | 完成的 job |
|---|---|---|---|---|---|
| `manual_first` | 1% | 6.1 | 13% | 88% | 0/48 |
| `balanced` | 13% | 6.6 | 26% | 77% | 0/48 |
| capA `manual_first` | 15% | 28.3 | 71% | 48% | 19/48 |
| capA `balanced` | 56% | 27.4 | 110% | 13% | 16/48 |
| capA keep `manual_first` | 5% | 31.4 | 67% | 48% | 18/66 |
| capA keep `balanced` | 55% | 28.2 | 111% | 13% | 16/64 |

完整的表在 `results/summary.md`，每 30 秒的速率在 `results/timelines.txt`。

- 原版的兩種 policy 都讓總量掉到 26% 以下。88% 和 77% 的位子在排隊，什麼也沒下載。
- capA 讓排隊比例降到 13%，manual 速度變成 4–5 倍。
- capA 加 `manual_first` 仍然讓 normal 只剩 5–15%。上限只解決「位子被排隊佔走」，不解決「派發順序」。
- capA 加 `balanced` 的總量超過 100%。原因是 manual 的 host 和 normal 的 host 不同，多出來的 host 有自己的 delay 額度。production 受 CPU 限制，所以不會出現這個加成。

## 5. 修正方案

修正版在 `6d23789`。它改了 4 個程式檔，共 101 行增加、15 行刪除。測試、`docker-compose.yml` 和 `docs/03` 另外有 229 行增加。

### 5.1 每個 host 的上限跟著 delay 走

- **位置：** `manual_bulk_crawls.py` 的 `_domain_limits`。
- **規則：** `delay > 0` 時，上限 = `min(slot concurrency, ceil(latency / delay) + MANUAL_BULK_CRAWL_HOST_HEADROOM)`。headroom 預設 1。
- **理由：** slot 每個 delay 放行一個，所以同時在下載的只有 `latency / delay` 個。再多的請求只會排隊並佔住全域位子。headroom 讓下一個請求在 delay 到期時已經在 slot 裡。
- **延遲估計：** 沿用 PR 的平滑 latency。沒有回應的失敗（例如 timeout）不更新估計，所以死 host 的上限停在初始值附近。
- `delay == 0` 時不設上限，維持原行為。

### 5.2 `weighted` policy

- **位置：** `fair_scheduler.py`。這是新的預設 policy。
- **規則：** stride scheduling（每次派發讓該邊的計數加上一個步長，計數小的一邊先派）。步長是整數：normal 為 p，manual 為 100 − p。p 由 `MANUAL_BULK_CRAWL_SHARE_PERCENT` 設定，範圍 1–99，預設 50。
- **兩邊都有請求時：** manual 拿到正好 p% 的派發。10,000 次派發的誤差在 ±1 以內。
- **一邊沒有請求時：** 另一邊拿走這一輪，所以有空閒容量時不會擋著不用。被跳過的一邊不累積額度，回來後不會連續搶好幾次。
- **計數相同時：** 給上一次沒被派發的一邊。所以 p = 50 時兩邊嚴格交替。
- `manual_first`、`balanced`、`normal_first` 保留。文件寫明前兩種嚴格優先的模式會 starvation。

### 5.3 等待時間加進 host 的優先序

- **位置：** `manual_bulk_crawls.py` 的 `_refresh_priorities`。
- **規則：** 分數 = 剩餘 URL × 每 URL 秒數 + 該 host 最舊待抓 job 已等待的秒數 + 固定 jitter。兩項的單位都是秒，所以不需要權重設定。
- **理由：** LRPT 只看剩餘時間。新的大 job 持續進來時，舊 job 剩下的小 host 可能一直排不到，直到 3 天的 TTL 到期。加上等待時間後，一個 host 等得比新 host 的剩餘時間久，就一定會排到。

### 5.4 觀測欄位

R3 試跑時要在 Loki 量兩邊的速率，所以加了這些欄位：

- `request.start`、`request.end`、`request.fail` 加上 `source`，值是 `normal` 或 `manual`。
- consumer 的 `fetch_failed` 補記一筆帶 `source` 的 `request.fail`。原版的 manual 失敗不寫 log。
- heartbeat 加入 `manual_active`、`normal_active`、`manual_outstanding`、`dequeued_normal`、`dequeued_manual`、`manual_saves`、`manual_save_ms`。

### 5.5 試過但拿掉的設計

| 設計 | 做法 | 結果 | 處理 |
|---|---|---|---|
| 佔用保護 | 派發時數 `Downloader.active` 裡各邊的請求。一邊佔滿 `ceil(份額 × 256)` 而另一邊有請求時，先派另一邊 | manual share 只有 35.8%（目標 50%），W30 只有 22.6%。死 host 時 normal 保留 57%，沒有保護的 capA 是 58% | 刪除 |
| 待派發上限 | consumer 最多讓 32 個 manual 請求停在 scheduler，進 slot 後再補 | 比例正確（50.0%），但總量 88%，48 個 job 一個都沒完成。上限放大到 256 後，總量 112%、完成 17 個 | 刪除 |

- **佔用保護失敗的原因：** manual 請求的佔位時間比 normal 長。保護規則用「佔用」限制 manual，等於用 slot 時間而不是派發次數計算份額。結果 manual 的派發比例被壓低。死 host 的影響已經被 §5.1 的上限擋住，所以保護沒有額外效果。
- **待派發上限失敗的原因：** heartbeat 顯示排隊比例從 13% 升到 27%。沒有這個上限時，scheduler 的 `DownloaderAwarePriorityQueue` 會優先派給 active 最少的 slot，把請求分散到不同 host。加上上限後，consumer 的 LRPT 順序決定派發，同一批 host 被反覆補滿。LRPT 也讓各 host 的剩餘量趨於一致，所以 job 都拖到最後才完成。

## 6. 第二輪：修正版的驗證

所有組都用 `KEEP=1`，每個 job 在這個 process 有 2 個 host、每 host 120 個 URL。完整的三張表在 `results/summary_round2.md`。

### 6.1 最終版的結果

| run | 設定 | normal req/s | manual req/s | manual 佔請求 | 每 30 秒最低（normal / manual） | 完成的 job |
|---|---|---|---|---|---|---|
| `N0` | 不送 job | 46.4 | 0 | 0% | 43.3 / – | – |
| `W50d` | share 50 | 28.5 | 28.4 | 50.0% | 26.9 / 27.0 | 14/62 |
| `W30d` | share 30 | 40.0 | 17.1 | 30.0% | 38.9 / 16.8 | 0/48 |
| `W50d_dead` | share 50，每 10 個 host 有 1 個死掉 | 27.2 | 27.1 | 50.0% | 25.7 / 25.6 | 14/62 |
| `capA_dead` | capA 加 `balanced`，同樣的死 host | 27.2 | 27.2 | 50.0% | 26.1 / 26.0 | 14/62 |
| `W50d_cpu` | share 50，加重頁面 | 21.2 | 21.2 | 49.9% | 20.4 / 20.4 | 0/48 |
| `W50d_light` | share 50，只送 4 個 job | 44.7 | 2.7 | 5.7% | 43.3 / 1.9 | 3/7 |

- `W50d_dead` 的死 host 在 20 秒後才回應，超過 15 秒的 timeout。死 host 同時出現在 normal 和 manual 的 host 裡。
- `W50d_cpu` 把頁面加到 900 個連結和約 320 KB 文字。只跑 normal 時 CPU 是 98%，速率是 24.5 ± 3.5 req/s。這組沒有對應的 normal-only 對照組。
- `W30d` 沒有 job 完成。manual 只有 17.1 req/s，48 個 job 共 11,520 個 URL 要約 670 秒，比 300 秒的窗長。

### 6.2 成功條件

| 條件 | 結果 | 判定 |
|---|---|---|
| share 50：manual 佔 50 ± 3% | 50.0% | 通過 |
| share 50：兩邊都 ≥ normal-only 的 45% | 兩邊都是 `N0` 的 61% | 通過 |
| share 30：manual 佔 30 ± 3% | 30.0% | 通過 |
| 死 host：normal ≥ 無死 host 時的 80% | 27.2 ÷ 28.5 = 95% | 通過 |
| CPU 吃滿：manual 佔 50 ± 5%，兩邊都不歸零 | 49.9%；兩邊最低 20.4 req/s | 通過 |
| 低負載：總量 ≥ normal-only 的 95% | 47.4 ÷ 46.4 = 102% | 通過 |
| 兩邊都有待抓量時，每 30 秒的速率 ≥ 該邊份額速率的 25% | 比例最低的是 `W50d` 的 normal：26.9 req/s，是它平均的 94%，份額速率的 95% | 通過 |
| 第一個 job 完成後，job 持續完成 | 360 秒的窗不夠判斷（§8） | 未判定 |

- **低負載組原本差 1 個百分點。** 用 before 窗當基準時，它的總量是 94%。`N0` 證明這 6% 是基準漂移，不是 manual 擋住 normal。heartbeat 也顯示 manual 只佔約 20 個位子，而且 `manual_active` 等於 `manual_outstanding`，沒有 manual 請求停在 scheduler。
- **死 host 的影響很小。** 死 host 的 latency 估計不會因 timeout 更新，停在初始值 1.0 秒。AutoThrottle 沒收到回應，delay 也停在起始值 0.5 秒。所以上限約是 `ceil(1.0 ÷ 0.5) + 1 = 3`，每個死 host 最多佔約 3 個位子。`capA_dead` 和 `W50d_dead` 幾乎相同，所以 §5.5 的佔用保護沒有必要。
- **CPU 吃滿時總量反而變高。** manual 的回應只存檔，不做連結抽取，所以每個請求的 CPU 成本比 normal 低。production 的 manual 頁面是真實網站，成本可能不同。

### 6.3 最終版和刪掉的設計

| 設定 | 最終版 | 佔用保護 | 待派發上限 32 | 待派發上限 256 |
|---|---|---|---|---|
| share 50，manual 佔請求 | 50.0% | 35.8% | 50.0% | 50.0% |
| share 50，normal / manual req/s | 28.5 / 28.4 | 31.1 / 17.3 | 22.6 / 22.5 | 28.3 / 28.3 |
| share 50，排隊比例 | 13% | 21% | 27% | 13% |
| share 50，完成的 job | 14/62 | 0/48 | 0/48 | 17/63 |
| share 30，manual 佔請求 | 30.0% | 22.6% | 29.9% | 30.0% |
| CPU 吃滿，manual 佔請求 | 49.9% | 36.2% | 37.6% | – |
| CPU 吃滿，manual 每 30 秒最低 | 20.4 | 11.9 | 6.2 | – |

待派發上限 256 永遠不會生效，因為 consumer 本身最多只有 256 個請求在外面。它和最終版的差別只在補件時機，兩組的結果一致。

## 7. 部署狀態

2026-10-29 下午用唯讀指令檢查了 ntu-sa1 到 ntu-sa4。PR #84 已經 merge，但沒有任何主機在跑它。

| 檢查 | 結果（4 台都一樣） |
|---|---|
| `api` container，包含已停止的 | 沒有 |
| 10-28 之後建立的 image | 沒有 |
| uvicorn 或 `containers.api` 的 process | 沒有 |
| `/data/manual_bulk_crawls` | 不存在 |
| `127.0.0.1:4000/healthz` | 連不上 |

- Host2 的 `/opt/WebCrawler` 掛在 crawler 的 `/app`。它的 HEAD 是 `2e4f87c`，`FETCH_HEAD` 和 `ORIG_HEAD` 都是 2026-06-01。
- Host2 的 crawler 從 10-20 一直在跑，環境變數沒有 `MANUAL_BULK_CRAWL_*`。
- 當天唯一的 container 異動在 Host3：YugabyteDB Voyager 的搬遷工作，和 API 無關。

部署需要 crawler 團隊在 Host2 pull main、建立 `/data/manual_bulk_crawls`、起 `api` 並重啟 crawler。照 merge 後的預設，重啟後 crawler 會用 `manual_first`，§4 的結果會直接發生。

**2026-10-30 更新：** 上面的檢查之後，PR #84 在當天晚上部署了。

- 2026-10-29 17:24Z，Host2 的 `/opt/WebCrawler` pull 到 `39e1363`。
- 17:29Z，crawler image 重建並重啟。`api` container 也起來了，聽 `127.0.0.1:4000`。
- `MANUAL_BULK_CRAWL_SCHEDULING_POLICY` 沒有設定，所以是 `manual_first`。
- 到 10-30 02:14Z，`/data/manual_bulk_crawls` 是空的，還沒有任何 job。

細節在 §9。

## 8. 限制

- **只有一個 process 和模擬網站。** 真實網站的延遲分佈有長尾，robots.txt 也會失敗。模擬只用 lognormal 和固定的死 host 比例。
- **production 受 CPU 限制。** 主要結果在 30% CPU 下量測。`cpu` 組只涵蓋一種頁面大小。
- **存檔的 fsync 沒有在 Linux 上量。** 每次 manual 存檔在 event loop 上做 3 次 fsync 和 2 次 rename。
  - 本機 `fsync` 每次存檔 p50 0.31 ms，crawler process 內 4 組實測的平均是 0.78–0.90 ms，來源是 heartbeat 的 `manual_save_ms ÷ manual_saves`。macOS 的 `fsync` 不清磁碟快取。
  - 本機 `F_FULLFSYNC` 每次存檔 p50 13.85 ms。它清磁碟快取，和多數 Linux 磁碟上的 `fsync` 行為相近。
  - production 估計每 process 約 9.5 次存檔/秒。如果 Linux 的成本接近 `F_FULLFSYNC`，event loop 每秒會卡住約 132 ms。實作前設的門檻是 50 ms，所以這列為下一個要查的問題。使用者要求驗證只在本機做，所以 Host2 的 `/data` 沒有量。
  - 數字在 `results/fsync_local.txt`，腳本是 `scripts/fsync_bench.py`。
  - **10-30 更新：** Host3 的磁碟和 Host2 相同。在 Host3 上，crawler process 內每次存檔是 0.86–0.92 ms，磁碟被其他工作以 600 MB/s 寫入時是 3.6 ms（§9.4）。
- **job 會一起完成。** 48 個 job 同時送出、大小相同，所以 LRPT 讓它們在 330 秒左右一起完成。360 秒的窗不夠看出後續的完成節奏。§5.3 的等待項只在 job 送出時間不同時才有作用，這部分由單元測試驗證。
- **兩個既有測試失敗。** `test_domain_service_time_single_crawler_scenarios` 的 `wiki_only` 和 `wiki_and_diverse` 在原版和修正版都失敗。它們跑約 61 秒，接近 60 秒的 `CLOSESPIDER_TIMEOUT`。fsync 已排除。
  - 10-30 查到原因和 Scrapy 版本有關：在 Scrapy 2.19.0 下，原版和修正版都通過，每個約 23 秒。
  - 修正版的整套測試在 2.19.0 下是 126 個全過（§9）。

## 9. 10-30：production 查證和第三輪（Host3）

### 9.1 production 的現況

10-30 02:14–03:30Z 用唯讀指令查了 Host2。數字在 `results/production_2026-10-30.txt`。

- **部署：** `/opt/WebCrawler` 在 10-29 17:24Z 拉到 `39e1363`，17:29Z 重建 image 並重啟。`api` container 聽 `127.0.0.1:4000`，還沒有任何 job。
- **policy：** `MANUAL_BULK_CRAWL_SCHEDULING_POLICY` 沒有設定，所以是 `manual_first`。
- **版本：** 重建後是 Python 3.12.16、Scrapy 2.19.0。`requirements.txt` 只寫 `Scrapy>=2.13`，所以重建時裝到最新版。
- **節流：** 16 個 process 都跑 AutoThrottle。supervisord 對每個 program 設 env，所以 `docker inspect` 看不到這些設定。
- **速率：** 部署前每 process 約 20–22 req/s，latency p50 3.4 秒。部署後約 33–35 req/s，p50 2.1 秒，CPU 都約 100%。
- **每 host 的峰值：** 每個（hostname, process）每 60 秒的最大值，從部署前的 23 升到部署後的 40–56。

### 9.2 第三輪的方法

第三輪要回答：p = 20、64M recrawl 時，C1–C4 是否同時成立。條件的定義在 `results/summary_round3.md`。

- **主機：** Host3。它的 CPU（Xeon 8352V）和 `/data` 磁碟（PM9A3 NVMe、ext4）都和 Host2 相同。
- **平行：** 10 條 lane 同時跑。每條 lane 的 crawler 獨佔一個實體核（CPU 37–46），其他元件共用 58–71。
- **版本：** 修正版加階段 A 的測試，`f5a7b93`。F1 用 production 的 `39e1363`。
- **job：** `iselsim.py` 扮演 isel 的 planner。它用 process 03 分到的 64M 清單，每個 job 188 筆，最多 48 個 job 同時存在。一個 host 被取走 n 筆後，要等 5n 秒才能再被取，也就是平均 0.2 req/s。
- **頁面權重：** 主要 lane 的 normal-only 速率是 34.8–35.2 req/s，CPU 100%，對應部署後的 production。F6 是 20.7–20.9 req/s，對應部署前。
- **P0：** 一個忙碌 slot 的平均送出間隔是 delay 的 1.211 倍，符合 slot 模型的 1.218 ± 0.05。校準數字在 `results/calibration_host3.txt`。
- **時間：** 04:39:40Z 開始，04:54:41Z 送 job，05:54:40Z 或 06:39:43Z 結束。

### 9.3 結果

59 分鐘窗。F5 對 F5_0 比，F6 對 F6_0 比，其他對 F0a 比。

| lane | 設定 | normal，對照組的百分比 | recrawl req/s | recrawl 佔比最大 | job 生命週期 p90 | 沒通過的條件 |
|---|---|---|---|---|---|---|
| F1 | `39e1363`、`manual_first`、120 筆 | 9% | 18.65 | 90.1% | 454 秒 | C1、C2、C4 |
| F2 | 120 筆 | 96% | 8.44 | 20.0% | 1393 秒 | 無。104 分鐘時 C2 的 10 分鐘窗有 3 個 host 超過 |
| F3 | 30 筆 | 96% | 8.46 | 20.0% | 1841 秒 | 每 host 等待 870 秒；C2 的 10 分鐘窗有 2 個 host 超過 |
| F4 | 20 個熱門 host | 98% | 3.96 | 10.4% | 145 秒 | 一個熱門 host 的 normal 只剩 17%；recrawl 3.96 |
| F5 | 加失敗 | 94% | 9.79 | 20.1% | 1952 秒 | 每 host 等待；C2 的 10 分鐘窗有 11 個 host 超過 |
| F6 | 重頁面 | 96% | 5.09 | 20.1% | 1888 秒 | 每 host 等待 1482 秒 |

- **現行 production 收到 job 就會 starvation。** F1 的 normal 只剩 3.0 req/s，crawler 的 CPU 掉到 25%。
- **修正版保住 normal。** 每條 lane 的 normal 都在對照組的 94% 以上。每條 lane 最低的 60 秒，都在對照組中位數的 87% 以上。
- **比例正確。** recrawl 佔請求的 19.8–20.1%，p = 20。
- **recrawl 不會停。** 沒有任何 5 分鐘是 0。job 生命週期的 p90 都在 1 小時內。
- **C3 有餘裕。** F2、F3 的 recrawl 是 8.5 req/s，F6 是 5.09 req/s，都超過需求 4.0。F4 的 3.96 是設計上的上限：20 個 host 乘 0.2 req/s。
- **長時間不漂移。** F2、F3 跑 104 分鐘，前後兩半的 normal、recrawl 和佔比都一樣，RSS 維持 288–298 MB。
- **F0b 和 F0a 的設定相同。** 兩者的 normal 是 35.1 和 35.2 req/s，所以 lane 之間的雜訊遠小於門檻。

### 9.4 沒通過的條件和原因

**每 host 的等待超過 600 秒。** planner 一直維持 40–48 個 job，recrawl 永遠有待抓量。所以 host 要排隊。

- F3 同時有 256 個 host 有待抓量，F2 只有 96 個。所以 F3 的等待比較長，870 秒對 533 秒。
- F5 的 3599 秒是量測假象（`scripts/diag3_failures.py`）。
  - robots.txt 拒絕的 12 個 host 永遠不會收到請求。
  - 死 host 的請求在 timeout 時被斷線，模擬網站沒寫 log。
  - 健康的 227 個 host 最長等 695 秒，14 個超過 600 秒。
- 等待時間有上限，因為 §5.3 把等待時間加進了優先序。job 也都會完成。這個條件保護的是 C2，見下一項。

> **已更正（§9.7）：** 第三輪的 `iselsim.py` 少了 isel 的一條規則。isel 只挑不在任何 open job 裡的 host（`recrawl.py` 的 `batch_id IS NULL`），所以一個 host 同時只在一個 job 裡。模擬器沒有這條規則，host 會累積好幾批。下面這一項只描述第三輪的模擬器，不適用於真正的 isel。

**C2：recrawl 的突發受 slot 速率限制，不受每 job 的筆數限制**（`scripts/diag3_burst.py`）。

- 等過的 host 會一次累積好幾批。輪到它時，consumer 用 slot 的速度送完。
- 所以 host 最忙的那一分鐘，會到它 slot 速率的 0.87–1.05 倍。30 筆和 120 筆都一樣。
- 30 筆的差別只在比例。最忙一分鐘的中位數，30 筆是 slot 速率的 0.35 倍，120 筆是 0.71 倍。
- 模擬的峰值低（23–26 次/分），原因是 CPU 排隊讓 latency 變成 3.2 秒，slot 變慢。F5 的 latency 比較低，一個 1.26 秒的 host 在 30 筆設定下一分鐘到 65 次。
- 換算到 Host2：`www.espn.com` 的 slot 是每分鐘 83 次，所以追趕時會到 72–80 次，超過 67。其他 22 個被限制的 hostname 都在 65 以下。
- latency 低於 1.47 秒、又累積超過 67 筆的未限制 hostname 也會超過 67。這類 hostname 的數量沒有量。
- 10 分鐘窗的超標（F2 3 個、F3 2 個、F5 11 個）是同一個原因。長期平均仍受 isel 的 0.2 req/s 限制。

**共用 slot 時，熱門 host 的 normal 變少**（`scripts/diag3_hot.py`）。

- manual 在 host 的 slot 裡掛 3 個請求。scheduler 會先派給 active 比較少的 slot，所以這個 host 的 normal 被延後。
- 這個現象在 slot 還有空間時也會發生。F4 裡 total ÷ slot 速率只有 52–54% 的 host，normal 仍少了約 30%。
- 影響和 slot 餘裕 r − u 有關：

  | r − u（req/s） | host 數 | normal 保留 |
  |---|---|---|
  | 0.42–0.45 | 13 | 66–72% |
  | 0.34–0.39 | 4 | 47–56% |
  | 0.15–0.20 | 3 | 17–36% |

- Host2 上 23 個被限制的 hostname 都有 r − u ≥ 0.34，所以預期 normal 保留 47% 以上，高於 40% 的門檻。

**每次 manual 存檔的成本**（`scripts/diag3_save.py`）。

- 10 條 lane 同時跑時，每次存檔 0.86–0.92 ms。
- 05:54Z 起，root 的 `yb-voyager`（YugabyteDB 遷移）開始以約 600 MB/s 寫 `/data`。之後每次存檔升到 3.2–3.6 ms。
- 第四輪跑的時候 Voyager 仍在跑，每次存檔卻是 0.90–0.98 ms。所以第三輪升高的原因還沒確定。
- 最高的 3.6 ms 也遠低於階段 D 的中止門檻 20 ms。§8 擔心的 13.85 ms（macOS `F_FULLFSYNC`）在 Linux 上沒有出現。

### 9.5 結論（第三輪和第四輪合併）

- **C1、C4 成立。** normal 保有 94% 以上，recrawl 佔 20.0%。30 筆和 120 筆都一樣。
- **C2 在 30 筆時成立，120 筆時對最快的 host 不成立（§9.7）。** 用 isel 真正的規則時，一個 host 每分鐘的 recrawl 最多是每 job 的筆數。
  - 30 筆：`www.espn.com` 每分鐘約 30 + 25 = 55 次，低於 67。
  - 120 筆：espn 的一批會用 slot 速度跑超過一分鐘，估計到 68–81 次。
- **C3 對大 host 不成立（§9.7）。** crawler 的 recrawl 容量足夠，每 process 8.9 req/s，需求是 4.0。限制來自 isel：一個 host 要等它的 job 完成，才拿得到下一批。
  - 120 筆：大 host 約 0.05–0.07 req/s。241,920 筆要 38–93 天，process 03 有 15–26% 的 URL 在 14 天內抓不完。
  - 30 筆：大 host 約 0.02 req/s，要約 150 天，有 45% 抓不完。
- **所以現行的 isel 設定（120 筆、48 個 job）無法同時滿足 C2 和 C3。** 要調 isel 的 job 數或筆數，再加一輪模擬確認。第五輪的結果在 §9.8。
- **部署順序不變。** 修正版上線並設 `MANUAL_BULK_CRAWL_SHARE_PERCENT=20` 之前，isel 不能送 job。F1 證明現行 production 收到 job 就會 starvation。

### 9.6 測試

10-30 在本機重跑（Python 3.12，附錄 B 的指令）：

| 範圍 | Scrapy | `cc965006` | `f5a7b93` |
|---|---|---|---|
| `containers/crawler` | 2.19.0 | 84 passed | 126 passed |
| `containers/crawler` | 2.16.0 | 82 passed、2 failed | 124 passed、2 failed |

### 9.7 第四輪：模擬器加上「一個 host 同時只在一個 job 裡」

數字在 `results/summary_round4.md`，原始輸出在 `results/analysis_round4.md`。

**改了什麼**

- `iselsim.py` 加上 isel 的規則：host 不在任何 open job 裡，而且已過 5 秒 × 上一批的筆數，才能再被挑。
- job 完成後，模擬器每 60 秒輪詢一次。看到完成後，下一次 archive（每 60 秒一次）才釋放它的 host。這和 isel 的流程相同。
- 本機 smoke 確認沒有 host 同時在兩個 job 裡。host 在上一個 job 完成後 54–88 秒拿到下一批。

**設定**

- 5 條 lane 同時跑：07:11:11Z 開始，07:26:14Z 送 job，08:26:14Z 結束。其他設定和第三輪相同。
- R0 只跑 normal。R120 和 R30 用 process 03 的清單，每 job 每 host 120 或 30 筆。H30 和 H120 是 20 個熱門 host，每個 1 萬筆。
- Host3 上的 Voyager 遷移整段都在跑。暖機時 5 條 lane 都是 35.0–35.4 req/s，CPU 100%。

**C2：突發被每 job 的筆數壓住**

- R120 和 R30 沒有任何 host 在一個釋放週期內拿到第二批。第三輪分別有 46 個和 53 個。
- 每 host 10 分鐘窗的超標從 2–4 個變成 0 個。recrawl host 每 60 秒的最大值是 19–22，和 R0 的 22 相同。
- 一批 120 筆會用 slot 的速度送。最忙一分鐘到 slot 速率的 0.82–0.98 倍。

**C3：大 host 太慢**

| lane | 10 萬筆以上的 host | 每 host recrawl 中位數 | 241,920 筆所需天數 | 同一 host 兩個 job 的間隔 |
|---|---|---|---|---|
| R120 | 11 個 | 0.046 req/s | 38–93 天 | 1,620 秒 |
| R30 | 11 個 | 0.019 req/s | 148–155 天 | 1,230–1,620 秒 |
| H30 | 20 個 1 萬筆 | 0.182 req/s | – | 150–180 秒 |
| H120 | 20 個 1 萬筆 | 0.197 req/s | – | 440–600 秒 |

- **原因是 job 的週期。** 一個 host 每個週期只拿到一批。週期 = job 生命週期 + 輪詢和 archive 的約 75 秒。
- **生命週期由 Little's law 決定**（平均在途數 = 速率 × 平均停留時間）：48 個 job × 188 筆 ÷ 8.85 req/s ≈ 1,020 秒。實測中位數是 863–947 秒。
- **H lane 證明了這個機制。** 只有 20 個 host 時，job 很短，中位數 103 和 341 秒，所以 host 能到 0.16–0.20 req/s。
- **對 process 03 的影響：** 它有 69,599 個 host、457.5 萬個 URL。每個 host 都照實測速率抓時：
  - 0.074 req/s，R120 最快的大 host：12 個 host 抓不完，剩 14.8%。
  - 0.046 req/s，R120 的中位數：19 個 host 抓不完，剩 25.9%。
  - 0.019 req/s，R30：42 個 host 抓不完，剩 45.3%。
- **production 可能更慢。** 在 Host2，一個 job 的 3,000 筆分散到 16 個 process。job 要等最慢的 process 做完才算完成。

**其他**

- normal 保有 R0 的 94%。R lane 的熱門 host 保有 86–87% 的 normal，第三輪只有 74–76%，原因是大 host 不再累積好幾批。
- H lane 把全部 recrawl 集中在 20 個熱門 host 上，模擬的 slot 又慢，所以這些 host 的 normal 只保有 22–35%。
- 每次存檔 0.90–0.98 ms。

**下一步要決定的事**

要同時滿足 C2 和 C3，有兩個方向。兩者都要再跑一輪模擬：

- **降低 isel 的 `max_jobs_inflight`。** 生命週期和 job 數成正比。120 筆要到 0.2 req/s，週期要 ≤ 600 秒，所以生命週期要 ≤ 約 525 秒，每 process 約 24 個 job 以下。但 120 筆時 espn 每分鐘仍會超過 67 次。
- **在 crawler 加每 host 的 manual 速率上限，同時用 120 筆。** 突發由上限壓住，不由筆數壓住。這要改 WebCrawler 修正版。

10-31 選了第二個方向，並一起調 isel 的 job 數和筆數，結果在 §9.8。

### 9.8 第五輪：crawler 固定間隔加 isel 大批次（10-31，方向 A）

數字在 `results/summary_round5.md`，原始輸出在 `results/analysis_round5.md`。

**結論**

- **方向 A 讓 C1–C4 同時成立。** 有一個例外：slot 很慢的熱門 host 不成立。最好的設定是 A3：
  - crawler：`CRAWLER_MANUAL_BULK_CRAWL_HOST_INTERVAL=4.8`。
  - isel：`min_gap_s` 4.8、`host_docs_per_job` 240、`status_poll_s` 和 `archive_interval_s` 15。
  - 結果：大 host 每 host 0.198 req/s，241,920 筆要 14.0–14.5 天。process 03 在 14 天後剩 0.16% 的 URL。
- **間隔 5 秒時 C3 差一點。** A2 是 0.192 req/s，要 14.6 天。A1 用 120 筆和 60 秒輪詢，是 0.183 req/s，要 15.3 天。
- **isel 的 job 數一定要降，或改成大批次。** A5 維持 48 個 job、每 job 120 筆，有 96 個 host 同時在等。20% 的比例被用滿，大 host 只有 0.047 req/s，要 52–103 天。
- **熱門 host 的 normal 保留要看 slot 餘裕。** recrawl 到 0.2 req/s 時，餘裕 r − u ≥ 0.36 的 host 保有 53–74%。餘裕 0.16–0.24 的 3 個 host 只剩 19–33%，所以 C1 的這一項不通過。Host2 被限制的 hostname 餘裕都 ≥ 0.34。

**crawler 的改動和選擇過程**

`CRAWLER_MANUAL_BULK_CRAWL_HOST_INTERVAL`（秒，預設 0 = 關閉）設定一個 host 兩次 manual 請求交給 scheduler 的間隔。試了四版，都在 branch `pr84-weighted-dispatch` 上：

| 版本 | 間隔從哪裡算 | 每 host 同時在外面的上限 | 每 host 速率，目標 0.208 |
|---|---|---|---|
| `2436f4b` | 實際交出的時間 | ceil(latency / delay) + 1 | 本機 0.204，Host3 0.178 |
| `804632f` | 估計的送出時間 | 1 | 本機 0.166 |
| `d61e4bf` | 實際交出的時間 | 1 | 本機 0.192，Host3 0.12 |
| `381c08d` | 固定網格 | ceil(latency / delay) + 1 | Host3 0.198 |

- **上限 1 在 Host3 上不行。** crawler 的 CPU 是 100%，回應要排隊等 scraper 處理，所以存檔比回應晚約 5 秒。下一個請求要等存檔，所以每 host 只有 0.12 req/s。Host2 的 CPU 也是約 100%。
- **從實際交出的時間算也不夠。** CPU 忙時，計時器平均晚約 1 秒，所以速率掉到 0.178。
- **最後用固定網格。** 計時器晚了，下一個間隔就縮短，最短到半個間隔。落後超過半個間隔（例如 host 閒置後）時，從現在重新起算，不補送。
- `381c08d` 在本機和 Host3 都是 136 passed（Scrapy 2.19.0）。

**模擬器的改動**

`iselsim.py` 改成一個 host 每次拿滿每 job 的筆數。原本 job 的剩餘量會截短 host：188 筆的 job 中，每兩個 host 就有一個被截短。isel 只截短 3,000 筆 job 的最後一個 host。

**三次嘗試**

- **5a（03:22Z）：** 跑了 8 分鐘的 job 就停掉。A1–A5 用 `d61e4bf`，每 host 只有 0.12 req/s（`results/round5a_stopped.txt`）。
- **5b（03:53Z）：** 暖機檢查自動中止。R0 是 38.2 req/s，超過 31–38。Host3 的 Voyager 結束後機器變快，6 條 lane 都在 37.7–38.2。
- **5c（04:09Z–05:24Z）：** 頁面權重從 w = 0.38 改成 0.42。暖機時 6 條 lane 都是 34.4–35.0 req/s，CPU 100%。下面是 5c 的結果。

**C2：間隔壓住突發**

- 一個 host 的 recrawl 最忙一分鐘是 15–16 次。間隔 4.8–5 秒的平均是 12–12.5 次。
- 加上 normal，最忙的 host 一分鐘是 23–24 次，R0 是 21 次。每 host 10 分鐘窗的超標是 0 個。
- 送出端的間隔不固定。29–44% 的間隔短於 0.9 倍間隔，最短 1% 是 0.5–1.8 秒，但平均值正確。原因是網格會縮短計時器晚到後的間隔，而且每個請求在 scheduler 和 slot 裡等的時間不同。
- Host2 的估計：一個 host 一分鐘最多是它的 normal 峰值加 16 次。
  - `www.espn.com`：25 + 16 = 41。
  - `ameblo.jp`：33 + 16 = 49。
  - 6 小時內 normal 峰值最高的大 host 是 61。如果它也被 recrawl，而且兩邊的峰值落在同一分鐘，最多是 77。

**C3：大 host 的週期**

- 一個 host 的週期 = 一批的筆數 × 間隔 + 等 poll、archive 和下一次 plan 的時間。
- 實測同一 host 兩個 job 的間隔：A1 是 660 秒（600 + 60），A2 是 1,260 秒（1,200 + 60），A3 是 1,200–1,230 秒（1,152 + 48–78）。
- 大批次和 15 秒的輪詢讓等待的比例變小。間隔 4.8 秒讓速率能到 0.2，又在 C2 的 10 分鐘上限內：0.208 < 0.22。
- 同時在 job 裡的 host 數要低於「比例 × 總速率 ÷ 每 host 速率」。這次是 8.4 ÷ 0.208，約 40 個。

**C1：熱門 host 和 slot 餘裕（A3）**

| r − u（req/s） | host 數 | normal 保留 |
|---|---|---|
| 0.40–0.44 | 12 | 67–74% |
| 0.36–0.38 | 3 | 53–59% |
| 0.32 | 1 | 49% |
| 0.16–0.24 | 3 | 19–33% |

- 不通過的 3 個 host，模擬的 slot 只有 0.33–0.41 req/s，latency p50 是 4–5 秒。
- 第四輪的熱門 host 保有 86–87%，原因是大 host 那時只有 0.05 req/s。recrawl 速率變高，代價就是共用 slot 的 normal 變少。
- Host2 上沒被限制的大 hostname，餘裕還沒量過。

**還不確定的事**

- 模擬器依大小挑 host，所以一小時內只有最大的 32–40 個 host 拿到 job。大 host 的速率是上限。isel 實際依 best rank 挑。
- **production 的 job 數：** 在 Host2，每 job 3,000 筆分散到 16 個 process。每 host 240 筆時，一個 job 約 12.5 個 host，平均每 process 0.78 個。48 個 job 約等於每 process 37 個 host，接近 A4。job 裡的 host 比較小時，同時在 job 裡的 host 會更多。
- 模擬的 latency p50 是 3.1 秒，來自 CPU 排隊。Host2 是 2.1 秒。

## 附錄 A：實驗時段（UTC，2026-10-29）

| run | 程式 | 開始 | 送出 job | 結束 |
|---|---|---|---|---|
| `manual_first` | `cc965006` | 03:03:58 | 03:07:59 | 03:13:58 |
| `balanced` | `cc965006` | 03:14:21 | 03:18:23 | 03:24:22 |
| capA `manual_first` | capA | 03:32:44 | 03:36:46 | 03:42:44 |
| capA `balanced` | capA | 03:43:06 | 03:47:08 | 03:53:10 |
| capA keep `manual_first` | capA | 03:54:18 | 03:58:20 | 04:04:18 |
| capA keep `balanced` | capA | 04:04:42 | 04:08:44 | 04:14:46 |

第二輪的最終版和對照組（程式都是 `6d23789`，`capA_dead` 除外）：

| run | 開始 | 送出 job | 結束 |
|---|---|---|---|
| `W50d_dead` | 14:25:20 | 14:29:22 | 14:35:20 |
| `W50d_cpu` | 14:35:45 | 14:39:47 | 14:45:49 |
| `W50d_light` | 14:46:09 | 14:50:11 | 14:56:13 |
| `W50d` | 14:56:42 | 15:00:44 | 15:06:42 |
| `W30d` | 15:07:03 | 15:11:05 | 15:17:03 |
| `N0` | 15:17:26 | 15:21:28 | 15:27:30 |
| `capA_dead`（capA） | 07:58:55 | 08:02:57 | 08:08:55 |

刪掉的設計（程式是當時的工作目錄，沒有 commit）：

| run | 版本 | 開始 | 送出 job | 結束 |
|---|---|---|---|---|
| `W50`、`W30`、`W50_dead`、`W50_cpu`、`W50_light` | 佔用保護 | 07:27:41 | – | 08:29:49 |
| `W50b`、`W30b`、`W50b_dead`、`W50b_cpu` | 待派發上限 32 | 08:30:17 | – | 09:11:34 |
| `W50c`、`W30c` | 待派發上限 256 | 14:00:41 | – | 14:21:04 |

每組的時長都相同：240 秒只跑 normal，再跑 360 秒。

## 附錄 B：測試

測試在本機跑。crawler 的指令如下，`<WC>` 是 WebCrawler 目錄：

```
PYTHONPATH=<WC> uv run --no-project --python 3.12 --with scrapy==2.16.0 --with twisted==26.4.0 \
  --with tldextract --with pqdict==1.5.1 --with PyYAML==6.0.2 --with python-json-logger==2.0.7 \
  --with pytest==8.4.2 --with w3lib==2.3.1 --with validators==0.35.0 \
  python -m pytest <WC>/containers/crawler -q -p no:cacheprovider --rootdir <WC>
```

API 的測試改用 `--with-requirements <WC>/containers/api/requirements.txt --with scrapy==2.16.0 --with pqdict==1.5.1`。

| 範圍 | 原版 `cc965006` | 修正版 `6d23789` |
|---|---|---|
| `containers/crawler` | 82 passed、2 failed | 122 passed、2 failed |
| `containers/api` | 186 passed | 186 passed |

- 兩個失敗都是 `test_domain_service_time_single_crawler_scenarios`，在兩個版本都失敗（§8）。
- 新增的測試涵蓋：
  - stride 比例在 p = 25、50、75 時，10,000 次派發的誤差 ±1。
  - 閒置的一邊不累積額度；一邊加入後兩邊交替。
  - 無效的比例、headroom 和 `MAX_ACTIVE_JOBS` 在啟動時被拒絕。
  - 每 host 上限的公式、邊界、delay 變大時不取消已送出的請求、delay 0 時不設上限。
  - 失敗的請求不更新 latency。
  - 100 個 host 時，256 個請求分散到至少 86 個 host。
  - 等了 500 秒的小 host 排在新來的 200 個 URL 的大 host 前面。
- ruff：每個改過的 Python 檔，`uvx ruff check --isolated` 的發現數量都和原版相同。

## 附錄 C：對 production 的唯讀操作

- SSH 進 ntu-sa1 到 ntu-sa4，執行 `docker ps -a`、`docker inspect`、`docker events`、`docker images`、`docker compose ls`、`ps`、`ss`、`df`、`top`、`ls`。
- 對每台主機的 `127.0.0.1:4000/healthz` 發 GET。
- 用唯讀 transaction 查 catalog：YugabyteDB（Host3 `:5433`）、selectdb `:5445`、metricdb。
- 查 Host2 的 Loki `:3100`：`request.end` 取 02:56:58–02:57:15，heartbeat 取 03:01:16–03:03:13。
- `docker exec crawler` 讀設定、套件版本和 `.git/HEAD`。
- `qshape.py`：用 `docker cp` 複製進 crawler container，執行後刪除。

沒有送出任何 job，沒有改任何設定，也沒有重啟任何服務。

10-30 的操作：

- Host2：讀 `supervisord.conf`、`/proc/<pid>/environ`、`.git/logs/HEAD` 和套件版本，並查 Loki。腳本是 `scripts/prod3_*.py`。
- Host3：經使用者同意，只在 `~/r3/`、`/data/r14725023/r3/` 和 `/dev/shm/r14725023-r3/` 跑模擬。uv 裝在 `~/.local/bin/`。
- Host3 的模擬只用 `127.0.0.1:18080–18089`。它沒有改動 docker、YugabyteDB 和其他使用者的行程。資源用量只用 `docker stats` 和 `ps` 唯讀查過。
