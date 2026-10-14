# Recrawl 設計：取得 128M 選取 URL 的 HTML

調研日期：2026-10-12。所有數字都來自唯讀查詢，查詢方法列在附錄 A。

## 0. 結論

2026-10-13 更新：本節和 §4–§9 依 2026-10-13 的調研改寫。crawler 端的改動另寫在 `docs/CRAWLER_RECRAWL_PROPOSAL.md`，給 crawler 組討論。

- recrawl 不另建爬蟲。它是正式 crawler 的第二條輸入車道。
- QPS 合規靠一個跨 process 的 host 閘門（提案 C2）。每個 process 的 Scrapy slot 不夠，原因有兩個：
  - Scrapy 2.16 的 redirect 會沿用原 host 的 slot；
  - 同一個 hostname 可能落在兩個 process。
- isel 以 hostname 為單位規劃：每個 queue 檔只含一個 host 的 URL，每個 host 同時最多一個檔在磁碟上。
- 原始 HTML 由 crawler 寫成 WARC.gz，isel 永久保存全量，估計 5–6 TB。
- 實際每 host 約 0.15 qps，14 天約可抓 108M 頁（§3）。
- 正式 crawler 現在不保證每 host 0.2 qps。要符合規定，必須改 crawler（提案 C1–C4）。
- 兩條車道大量搶同一批 host（§3.1）。同一個 host 的額度用加權輪替分配（提案 C9）：
  - isel 依每個 host 的期限算出 recrawl 份額，範圍 0.25–0.75，寫進 queue 檔；
  - 一般車道在被搶的 host 上至少保留 25%；
  - 模擬：recrawl 14 天約 112.8M 頁，一般爬取的尖峰損失 8.5%。

## 1. 規則

使用者在 2026-10-12 確認了以下規則：

| 規則 | 內容 |
|---|---|
| 限速單位 | 完整 hostname（FQDN）。`en.wikipedia.org` 和 `zh.wikipedia.org` 分開計算。 |
| 速率 | 每個 host 每秒最多 0.2 次，也就是相鄰兩次請求至少隔 5 秒。 |
| 範圍 | 所有會抓網頁的機制加總後都要遵守。包括正式 crawler、recrawl、sitemap_patroller、golden parent patrol。 |
| robots.txt | 必須遵守。 |

課程原文在 `../crawler/starter-project.md:8-10`：

> - **NO violation** of politeness!!
>   - Honor robots.txt
>   - Honor 0.2 qps per domain

本專案原有的限制也要遵守：

- 不寫 crawlerdb（`DESIGN.md` §1）。
- 不改正式服務的程式和設定（`../INDEX_INFRA_WORK_SUMMARY.md:348`）。所以 crawler 的改動要以 PR 提給 crawler 組。
- 新設計不能再對 crawlerdb 加讀寫負載（`../ARCHITECTURE_SPEC.md:284`）。

## 2. 現況

以下路徑都相對於 `../WebCrawler/`。部署版本是 Host2 `/opt/WebCrawler`，HEAD 為 `2e4f87c`。crawler 相關檔案和本地 repo 完全一致。

### 2.1 資料流

```
crawlerdb.url_state_current_NNN (should_crawl = TRUE)
   │ offerer (scheduler_control, 16 個 process；每個負責 16 個 shard)
   ▼
/data/ipc/url_queue/crawler_NN/domain_<domain_id>.json   (每檔最多 100 個 URL)
   │ crawler (16 個 Scrapy process)
   ▼
/data/ipc/crawl_result/crawler_NN/<date>/<HHMM>/<HHMM>.jsonl   (含完整 HTML)
   │ router → ingestor / feature_extractor (scheduler_ingest)
   ▼
crawlerdb（只存 content_hash、content_length、title）
```

- offerer 依 `url_score DESC`、`domain_score DESC` 挑 URL。在同一個 statement 裡，它把 `should_crawl` 設成 FALSE（`containers/scheduler_control/offerer/selection/golden_discovery_ranker_v1_strategy.py:189-223`）。
- 每個 domain 經 `compute_shard` 對應到一個 shard。process 編號 = shard // 16。所以一個 domain 只由一個 crawler process 抓。
- crawler 依檔案 mtime 由舊到新讀取。讀完立刻刪檔。已經在途的 domain 會跳過（`containers/crawler/crawler/queue_consumer.py:56-114`）。檔名必須符合 `^domain_(\d+)\.json$`（同檔 :9）。
- ingestor 寫回結果時再把 `should_crawl` 設成 FALSE（`containers/scheduler_ingest/ingestor/db_ops.py:368-399`）。

### 2.2 為什麼現在拿不到 HTML

- HTML 只出現在兩份 IPC 檔：`crawl_result/crawler_NN` 和 router 複製的 `crawl_result/ingestor_NN`。
- root cron 每小時刪掉超過 8 小時的檔案（`../ARCHITECTURE_SPEC.md:185`，Host2 `/data/ipc/logs/prune_crawl_result.log`）。
- crawlerdb 只存 `content_hash`（sha1）、`content_length`（字元數，不是 bytes）和 `title`。
- 沒有任何程式會把 `should_crawl` 設回 TRUE，所以抓過的頁不會再抓。
- `scripts/golden_inject.py` 和 `scripts/wiki_pageview_inject.py` 只提高 `url_score`，不動 `should_crawl`。
- golden parent patrol 的設計會重抓頁面（`docs/08-golden-parent-patrol.md`），但 `run_golden_parent_patrol.py` 不在 repo 裡。

### 2.3 正式 crawler 的限速設定

設定來源：`containers/crawler/supervisord.conf` 和 `containers/crawler/crawler/settings.py`。

| 設定 | 值 | 說明 |
|---|---|---|
| process 數 | 16 | 15 個 `CONCURRENT_REQUESTS=256`，canary `crawler_07` 是 384 |
| 全域在途上限 | 4,224 | 15×256 + 384；沒有全域 QPS 上限 |
| `CRAWLER_USE_AUTOTHROTTLE` | true | `domain_qps.json` 因此不生效 |
| `DOWNLOAD_DELAY` | 0.25 s | AutoThrottle 的下限 |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | 16 | slot 以 hostname 為單位 |
| `AUTOTHROTTLE_TARGET_CONCURRENCY` | 2.0 | |
| `AUTOTHROTTLE_MAX_DELAY` | 10.0 s | |
| `RANDOMIZE_DOWNLOAD_DELAY` | 未設定，Scrapy 預設 True | delay 會乘上 0.5–1.5 |
| `ROBOTSTXT_OBEY` | True | Scrapy 不套用 `Crawl-delay` |
| `DOWNLOAD_TIMEOUT` / `DNS_TIMEOUT` | 15 s | |
| `DOWNLOAD_MAXSIZE` | 10 MiB | |
| `RETRY_TIMES` | 1 | |

其他會影響抓取的機制：

- 失敗後的 domain 暫停：429 和 timeout 暫停 1 小時，5xx 暫停 30 分鐘。暫停時間是基數 × 2^min(fail_count, 6)（`containers/scheduler_ingest/ingestor/db_ops.py:42-62, 439-472`）。
- offerer 每個 domain 一批最多 100 個 URL（`config/control.yaml` 的 `per_domain_url_cap`）。
- sitemap_patroller 自己有 fetcher。它的全域間隔是 2.0 秒，每 domain 冷卻 60 秒。它從 2026-06 起就停止運作。

### 2.4 實測負載

- 正式 crawler 在 2026-10-01 到 10-04 每天約 30M 個回應，約 345 req/s（Loki `request.end`）。約 90% 回 200。
- 2026-10-04 11:00–11:10Z 的 10 分鐘內：
  - 245,854 個請求，打到 8,553 個 host。
  - 204 個 host 的平均速率超過 0.2 qps（10 分鐘超過 120 次）。最高是 160 次，等於 0.27 qps。
  - 超出 120 次的部分共 2,050 個請求，佔 0.8%。
- 平均值只顯示一部分問題。delay 0.25 秒加上每 host 16 個並發，代表短時間的連發沒有被擋住。
- 中位數 latency 約 6.6 秒。
- 頁面大小抽樣（`content_feature_current` 的 3 個 shard）：平均 146K–202K 字元，中位數 80K–158K 字元。
- crawler 從 2026-10-06 08:02Z 起停止運作，到 2026-10-13 仍未恢復（容器 Exited 137）。

### 2.5 Host2 資源

| 資源 | 數值 |
|---|---|
| `/data` | 7.0 TB，剩 6.6 TB。crawler 復工後 IPC 暫存可能用掉約 4 TB（0.5 TB/h × 8 h） |
| root `/` | 877 GB，剩 482 GB |
| RAM / CPU | 755 GiB / 144 threads |
| 對外網路 | 只有 `ens81np0 172.16.191.2/16`，經 `172.16.0.1` NAT 出去。沒有 proxy |

## 3. 容量分析

輸入是 run `20261011T202851094417Z` 的 `selected.parquet`，共 128,000,000 列。host 從 URL 取出。eTLD+1 用 tldextract 的離線 PSL 計算。

| 單位 | 數量 | 中位數 | p99 | 最大 |
|---|---|---|---|---|
| host | 1,677,068 | 1 | 173 | www.espn.com 656,637 |
| eTLD+1 | 1,137,271 | 1 | 322 | wikipedia.org 979,851 |

URL 最多的 10 個 host：

| host | URL 數 | 0.2 qps 下需要的天數 |
|---|---|---|
| www.espn.com | 656,637 | 38.0 |
| www.youtube.com | 606,303 | 35.1 |
| en.wiktionary.org | 509,533 | 29.5 |
| ameblo.jp | 466,663 | 27.0 |
| www.foxsports.com | 432,927 | 25.1 |
| pacificleague.com | 429,680 | 24.9 |
| open.spotify.com | 427,945 | 24.8 |
| www.cnnbrasil.com.br | 411,168 | 23.8 |
| www.cbsnews.com | 406,143 | 23.5 |
| www.lance.com.br | 396,278 | 22.9 |

一個 host 每天最多 17,280 次請求（86,400 秒 × 0.2）。假設 recrawl 能用滿每個 host 的額度，結果如下：

| 期間 | 每 host 上限 | 抓不完的 host | 這些 host 的 URL 數 | 可抓到的 URL 數 |
|---|---|---|---|---|
| 1 天 | 17,280 | 1,408 | 92.4M | 59.9M（46.8%） |
| 7 天 | 120,960 | 174 | 38.7M | 110.3M（86.2%） |
| 14 天 | 241,920 | 57 | 18.8M | 123.0M（96.1%） |
| 28 天 | 483,840 | 3 | 1.8M | 127.7M（99.75%） |

URL 集中的程度：

- 有 233 個 host 各有 10 萬個以上的 URL，共 45.2M。
- 有 2,001 個 host 各有 1 萬個以上的 URL，共 100.2M。

這些數字的意義：

- 瓶頸是大 host，不是頻寬。14 天平均只需要 106 req/s，約 20 MB/s。正式 crawler 的輸出約 84 MB/s。
- 正式 crawler 也抓同一批 host 時，recrawl 能用的額度會更少。上表是上限。
- 每個 host 還要先抓一次 robots.txt。1.68M 個 host 等於 1.68M 次額外請求，這些請求也算進額度。
- 上表假設每 host 用滿 0.2 qps。實際的週期是 max(5 s, latency)，latency 中位數 6.6 s，所以約 0.15 qps。扣掉 robots.txt 請求和約 10% 失敗，14 天約 108M 頁。若要抓到 123M，需要 17–18 天，或約 120 req/s。

### 3.1 和一般爬取的重疊（2026-10-13 更新）

- 資料：Loki 2026-10-03、10-04 全天的 `request.start`，每天約 34.5M 次。`domain` 欄位是完整 hostname。
- 一般爬取 93.8% 的請求打在 recrawl 清單內的 host，23.4% 打在 10k URL 以上的 host。
- 10k URL 以上的 2,001 個 host 中，有 513–517 個 host 的額度被一般爬取用掉一半以上。
- 最大的 10 個 recrawl host，一般爬取每天打 8k–16.5k 次，等於額度的 48–95%。C1 之後是 64–100%。
- 所以上面的表是上限。實際能抓多少，取決於兩條車道怎麼分同一個 host 的額度。政策比較見提案 §5.2，採用的做法見 §4.4 和 §5。

## 4. 架構

### 4.1 原則：一個抓取平面，一個 host 閘門

- 「每 host 相鄰請求至少隔 5 秒」需要知道同一 host 的上一次請求。
- 每個 crawler process 的 Scrapy slot 只知道自己送過的請求。以下情況會讓兩個 process 打同一個 host：
  - 跨 host 的 redirect：Scrapy 2.16 把 slot 名稱寫進 `meta["download_slot"]`，redirect 複製 meta，所以目標 host 的請求在原 host 的 slot 排隊；
  - crawlerdb 的白名單切分改變 routing；
  - isel 用的 routing 快照過期；
  - process 重啟，slot 的計時歸零。
- 所以合規放在一個跨 process 的閘門：Host2 tmpfs 上的 SQLite，每次送出前對 hostname 做原子的「檢查並設定」（提案 C2）。
- isel 的 routing 只決定工作落在哪個 process，不負責合規。

### 4.2 元件

```
selection run (manifest) ──recrawl-sync──► selectdb recrawl.{host,doc}
                                                  │ recrawl-plan（常駐）
                                                  ▼
                        /data/ipc/recrawl_queue/crawler_NN/domain_<host_id>.json
                                                  │
                                                  ▼
               正式 crawler process NN（兩條車道，都先過 host 閘門）
                                                  │ lane=recrawl
                                                  ▼
     /data/ipc/recrawl_result/crawler_NN/recrawl-NN-<ts>Z-<seq>.{warc.gz,jsonl}
                                                  │ recrawl-archive（常駐）
                                                  ▼
       /data/isel_warc/YYYYMMDD/crawler_NN/（WARC + gzip 的 fetch log）；更新 recrawl.doc
```

### 4.3 crawler 的改動

全部寫在 `docs/CRAWLER_RECRAWL_PROPOSAL.md`：

| 編號 | 內容 |
|---|---|
| C1 | politeness 設定：每 host 並發 1、delay 5 s、不加隨機延遲、AutoThrottle 關閉 |
| C2 | host 閘門，和以 hostname 為 key 的 slot |
| C3 | robots.txt：Crawl-delay、5xx 視為禁止、24 小時快取 |
| C4 | 429/503 放慢同一 host |
| C5 | recrawl 車道：第二個 queue 目錄，自己的水位和排除集合 |
| C6 | recrawl 輸出：WARC + fetch log |
| C7 | 其他 fetcher 走同一個閘門 |
| C8 | 5–6 TB 的儲存空間 |
| C9 | 車道仲裁：每個 host 的兩條車道依 `recrawl_share` 加權輪替 |

### 4.4 isel 的元件（`src/isel/recrawl.py`，已實作）

**schema（revision 0003）**

| 表 | 一列代表 | 主要欄位 |
|---|---|---|
| `recrawl.host` | 一個 hostname 的 back queue | `host_id`（hostname 的 MD5 低 63 位元）、`crawl_shard`、`best_rank`、`next_at`、`batch_id` |
| `recrawl.doc` | 一個 `doc_id` | `url`、`host_id`、`rank`、`state`、`due_at`、`attempts`、`batch_id`、`dispatched_at`、抓取結果欄位、`warc_file`/`warc_offset`/`warc_length` |

- `state` 的值：`pending`、`dispatched`、`fetched`、`failed`、`gone`。
- doc 是否在 live 清單上由 `rank IS NOT NULL` 表示。sync 只寫 `rank` 和 `model_run_id`，archive 只寫抓取欄位。
- `host_ready (best_rank) WHERE batch_id IS NULL AND best_rank IS NOT NULL`：planner 先挑可派送的 host。
- `doc_due (host_id, rank) WHERE rank IS NOT NULL AND state IN ('pending','failed')`：取一個 host 的前 100 個 doc。
- `doc_stale (due_at) WHERE state = 'fetched' AND rank IS NOT NULL`：抓過的 doc 到期後回到 `pending`。
- `doc_inflight (batch_id) WHERE state = 'dispatched'`。
- plan、archive、sync 的每個交易都取同一個 advisory lock，所以不會交錯，也不會死結。

**`recrawl-sync`**：每次 swap 後跑一次。

1. 讀 live run 的 manifest。每個 `doc_id` 取 rank 最小的一列。
2. 每 1M 列一個交易，upsert `host` 和 `doc`。
3. 不在新清單的 doc 設成 `rank = NULL`。
4. 重算 `best_rank`。
5. swap 凍結時段內拒絕執行。

**`recrawl-plan`**：常駐，每 30 秒一輪。

1. crawler 取走 queue 檔後，planner 記下時間到 `doc.dispatched_at`，並清掉 `host.batch_id`。
2. 取走超過 `dispatch_timeout_s` 仍沒有結果的 doc 退回 `pending`，`attempts` 不加。
3. `fetched` 而且到期的 doc 退回 `pending`。
4. `/data` 剩餘空間低於 `min_free_gb` 時不派送。
5. 每個目錄最多 `max_files_per_dir`（384）個檔。依 `best_rank` 挑 host，每個 host 取最多 100 個到期 doc。
6. 每個檔帶一個 `recrawl_share`，由 `pace_share` 算出：
   - `backlog` 是這個 host 現在到期的 doc 數；`since` 是它們最早的 `due_at`；
   - `share = clamp(backlog × pace_cycle_s / max(since + pace_days − now, plan_interval_s), share_min, share_max)`；
   - 意義是「在 `since` 之後 `pace_days` 天內抓完這個 host 需要的份額」。期限過了就給 `share_max`；
   - backlog 的 count 封頂在 `pace_count_cap`，超過時份額本來就是上限，所以大 host 不用每次數 656k 列；
   - 新 doc 的 `due_at` 由 `recrawl-sync` 寫成 sync 的時間，所以第一期的期限從 sync 開始算。sync 要在啟動 planner 前不久才跑。
7. 同一個交易內標記 `dispatched`，commit 後才 rename queue 檔。
8. `--dry-run` 輸出每 host 的容量報告，不寫任何東西。

**`recrawl-archive`**：常駐，每 60 秒一輪。

1. 只讀已關閉的 fetch log，依 `(batch_id, doc_id)` 冪等套用：
   - 2xx：比已存的新就接受；
   - 失敗：只在 doc 正在等這個 batch 時才算。
2. 狀態對應：

| 結果 | 新狀態 | 下次 |
|---|---|---|
| 2xx HTML | `fetched` | `stale_days` 後 |
| 2xx 非 HTML、404、410 | `gone` | 不再抓 |
| robots 禁止、Crawl-delay 過長 | `failed` | `robots_retry_days` 後 |
| 429、503 | `failed`，並暫停 host（Retry-After，上限 `host_pause_max_s`） | 指數退避 |
| 其他失敗 | `failed`，`attempts + 1` | 指數退避；超過 `max_attempts` 後改等 `stale_days` |

3. 把 WARC 和 gzip 的 fetch log 搬到 `warc_root/YYYYMMDD/crawler_NN/`。selectdb 沒有備份，這些 log 是重建 DB 的依據。

設定都在 `config/isel.toml` 的 `[recrawl]`。部署用 `deploy/compose.compute.yaml` 的 `recrawl-plan`、`recrawl-archive` 兩個常駐 service（profile `recrawl`）。

### 4.5 每一期的流程

1. 第一期抓完整 128M，14 天約 108M（§3）。
2. 之後每期只抓：新加入的頁、到期的頁、上一期沒抓完的頁。
3. 重抓間隔一律是 `stale_days`。Cho & Garcia-Molina 證明在 Poisson 變動模型下，固定間隔的平均新鮮度優於依變動率加權。
4. conditional GET（`If-None-Match` / `If-Modified-Since`）延後再做。它省下傳輸，但請求仍算進 QPS 額度。
5. 從清單移除的頁，`rank` 設成 NULL，不再派送。已抓的 WARC 保留。

### 4.6 大 host

- 14 天抓不完的 host，照 rank 抓到期末。剩下的低 rank 部分留到下一期。
- Wikimedia 的 host（en.wiktionary.org、zh.wikipedia.org、en.wikipedia.org 等）可以改用官方 snapshot，不必抓網頁。
  - 公開 HTML dump 在 2025-03-24 停止更新（信心 MED）。
  - 現在要用 Wikimedia Enterprise 的 Snapshot API，需要免費帳號和 token（信心 LOW，未實測）。
- 另一個選項：讓 selection 知道每個 host 每期的抓取上限，把超出的名額分給其他 host。這會改變 selection 的目標，列為待評估。

## 5. QPS 合規

| 項目 | 做法 |
|---|---|
| 間隔 | C2 的 host 閘門：每 hostname 相鄰兩次送出至少 `max(5 s, Crawl-delay)`，跨 process 和車道 |
| 車道仲裁 | C9：scheduler 的每個 host 佇列分成兩條車道，依 `recrawl_share` 加權輪替。它只決定誰先送，不影響間隔 |
| redirect | slot key 一律由 URL 的 hostname 算出，不讀 meta |
| robots.txt | 照常遵守；robots.txt 請求走同一個閘門；5xx 視為禁止 |
| Crawl-delay | 寫進閘門；超過 60 秒的 host 不抓 |
| 429/503 | crawler 放慢同一 host；isel 暫停這個 host 的派送 |
| 所有機制加總 | 其他 fetcher 啟用前必須接上閘門（C7） |
| 事後稽核 | dispatch log 經 `verify_politeness.py` 檢查；Loki 每 host 每 10 分鐘 ≤ 120 次 |

## 6. 儲存

| 資料 | 大小估計 | 位置 | 保留方式 |
|---|---|---|---|
| queue 檔 | 每目錄最多 384 個，可忽略 | Host2 `/data/ipc/recrawl_queue` | crawler 讀完即刪 |
| WARC + fetch log（處理前） | 每組最多 1 GB 或 10 分鐘 | Host2 `/data/ipc/recrawl_result` | archive 處理完就搬走 |
| WARC.gz | 約 40–45 KB/頁，128M 頁約 5–6 TB | Host2 `/data/isel_warc` | 永久；需要 C8 的空間 |
| gzip 的 fetch log | 約 8 GB | 同上，和 WARC 放在一起 | 永久 |
| `recrawl` schema | 約 37 GB | Host1 selectdb（`:5445`） | 常駐；selectdb 是 `synchronous_commit=off` 且沒有備份，可以從 fetch log 重建 |

- Host2 `/data` 剩 6.6 TB，crawler IPC 尖峰約 4 TB。planner 在剩餘空間低於 4,500 GB 時停止派送，等 C8 定案後再調整。
- 每頁的實際大小要在試抓後更新。

## 7. 分階段

| 階段 | 內容 | 前提 |
|---|---|---|
| R0 | schema（0003）、`recrawl-sync`/`plan`/`archive`、測試、提案文件（已完成） | 只動本 repo |
| R1 | ⛔ `recrawl-sync` 在 live 上載入；`recrawl-plan --dry-run` 容量報告 | batch 20 swap 完成 |
| R2 | crawler 組實作 C1–C7；上線後跑稽核 | crawler 組同意；crawler 復工 |
| R3 | ⛔ 啟動 `recrawl-plan`、`recrawl-archive`，試抓 1–5M 頁，量測 WARC 大小 | R2 |
| R4 | 全量；之後每期只抓增量 | C8 定案 |

## 8. 不採用的做法

| 做法 | 不採用的理由 |
|---|---|
| 用 starter crawler 另建 recrawler | 它和正式 crawler 不共享狀態，無法保證同一 host 間隔 5 秒 |
| 把 crawlerdb 的 `should_crawl` 設回 TRUE | 要寫 crawlerdb；會被 offerer 排序和 GC 影響（`gc/service.py:158-176`）；結果以 redirect 後的 URL 為 key；ingest 會增加 crawlerdb 負載 |
| 只靠每個 process 的 Scrapy slot | redirect 沿用原 host 的 slot；routing 改變和重啟時，兩個 process 會打同一個 host（§4.1） |
| 共用 Redis token bucket | 多一個常駐服務；SQLite on tmpfs 做得到同樣的事 |
| 以 `domain_id`（eTLD+1）為單位規劃 | 規則的單位是 hostname；一個 eTLD+1 的多個 host 會被一個檔綁在一起 |
| 兩條車道共用排除集合 | 一般車道持有大 host 時，recrawl 被擋住，而大 host 正是瓶頸 |
| recrawl 請求設較高的 Scrapy priority | 每個 host 佇列內是嚴格 priority，等於嚴格優先。最大的 10 個 host 上一般爬取的需求是滿的，它們會停 14 天以上 |
| 固定比例（例如 3:1） | 14 天的頁數和配速幾乎一樣（113.0M 對 112.8M），但損失集中在前幾天：尖峰 14.0% 對 8.5% |
| 在閘門內跨 process 仲裁 | 兩條車道搶同一個 host 時，幾乎都在同一個 process，在 scheduler 仲裁就夠。跨 process 仲裁會讓 16 個 process 的 SQLite 寫入互相等待 |
| offerer 跳過有 recrawl 的 domain | 單位是 eTLD+1，太粗；延遲約 60 秒；要改 offerer |
| 份額下限 0.10 | 一個 100 URL 的檔最差約 1.9 小時，會碰到 2 小時逾時並重複派送；尖峰只比 0.25 少 1.2 個百分點 |
| HTML 經 jsonl 傳給 isel，再由 isel 寫 WARC | 要 base64（多 33%），或只能傳失去原始編碼的 `response.text` |
| 在 router 端對高分頁直接保存內容 | crawler 不會重抓已抓過的頁，所以抓不到已經在清單裡的 128M（`DESIGN.md` §8.4 的選項） |
| 以 `removed` 狀態表示移出清單 | sync 和 archive 會寫同一欄；改用 `rank IS NULL` |

## 9. 風險與待確認

1. crawler 組是否同意提案的 C1–C8。C1 會改變正式 crawler 的行為。
2. crawler 何時復工。它從 2026-10-06 起停止運作。
3. 正式 crawler 在 C1 之後的吞吐量。
4. WARC 的實際大小，以及 C8 的空間怎麼安排。
5. Wikimedia Enterprise snapshot 能不能用。
6. 「low level domain」已確認為完整 hostname。若助教另有解釋，3 節的表要改用 eTLD+1 那一列：最大的是 wikipedia.org，要 56.7 天；14 天可抓 122.0M。

## 附錄 A：查詢方法

**每 host 的 URL 分佈**（Host2，isel image，資料唯讀掛載）：

```bash
ssh ntu-sa2 'docker run --rm -i --network none --cpus 16 --memory 64g \
  -v $ISEL_WORK_DIR/full/selection/run=20261011T202851094417Z:/sel:ro \
  --entrypoint python $ISEL_IMAGE:6022068 -' < domain_dist.py
```

`domain_dist.py` 的核心查詢：

```sql
SELECT lower(regexp_extract(url, '^[a-zA-Z]+://([^/:?#]+)', 1)) AS host, count(*) AS n
FROM read_parquet('/sel/selected.parquet') GROUP BY 1;
-- coverable in D days: sum(least(n, 17280 * D))
```

eTLD+1 用 `tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)` 計算。

**正式 crawler 每 host 的速率**（Host2 的 Loki，`localhost:3100`，時間點 `2026-10-04T11:10:00Z`）：

```logql
count(sum by (domain) (count_over_time({container_name="crawler", event="request.start"} | json domain [10m])) > 120)
```

**每 host 每日請求數**（2026-10-13，每個 worker 分開查，避開 `max_query_series = 10000`）：

```logql
sum by (domain) (count_over_time({container_name="crawler", event="request.start", worker_id="<w>"} | json domain [12h]))
```

- 每個 worker 查 `time=2026-10-04T12:00:00Z` 和 `2026-10-05T00:00:00Z` 兩段，相加。
- 和 recrawl 每 host 的 URL 數 join 後，逐日模擬各政策（提案 §5.2）：
  - 每 host 每天上限 C = 86,400 / 6.7；一般爬取的需求取 min(實測, C)；
  - recrawl 車道的容量是 K 個「host·天」，每 host 一次一個檔；
  - 兩條車道都有需求時，recrawl 拿 `max(s × C, C − 一般需求)`。

**每日請求數**：

```logql
count_over_time({container_name="crawler", event=~"request.end|request.fail"}[24h])
```

## 附錄 B：來源

| 來源 | 日期 | 信心 |
|---|---|---|
| WebCrawler 原始碼 `2e4f87c`，部署版和本地一致 | 2026-06-01 | HIGH |
| Host2 `docker inspect`、`/data/ipc`、Loki、`df` | 2026-10-12 | HIGH |
| `../crawler/starter-project.md`（課程規則） | 2026-09 | HIGH |
| Scrapy 文件，經 ctx7 `/scrapy/scrapy`：slot 以 hostname 為單位，AutoThrottle 下限是 `DOWNLOAD_DELAY` | 2026-10 查詢 | HIGH |
| [Stanford IR book：The URL frontier](https://nlp.stanford.edu/IR-book/html/htmledition/the-url-frontier-1.html)（每 host 一個 back queue 的設計） | 2008 | HIGH |
| Host2 `webcrawler-crawler` image 的 Scrapy 2.16.0 原始碼：`core/downloader/__init__.py:166-180`、`downloadermiddlewares/redirect.py:123-131`、`robotstxt.py` | 2026-10-13 | HIGH |
| RFC 9309（robots.txt：5xx 視為全部禁止、快取上限 24 h） | 2022-09 | HIGH |
| Cho & Garcia-Molina 的重抓策略，經 [MPI 講義](https://resources.mpi-inf.mpg.de/d5/teaching/ss09/dyn/slides/dyn-kap3.pdf) 轉述 | 2009 | MED |
| [warcio 文件](https://warcio.readthedocs.io/en/latest/) | 1.8.1 | MED |
| [Heritrix 1.12.0 feature notes](https://github.com/internetarchive/heritrix3/wiki/Feature-Notes---1.12.0)（conditional GET 重抓） | 2007 | MED |
| [http.dev：If-Modified-Since](https://http.dev/if-modified-since) | 未標日期 | MED |
| [Wikimedia Enterprise HTML Dump Archive](https://cdimage.debian.org/mirror/wikimedia.org/other/enterprise_html/index.html)（2025-03-24 停止更新） | 2025 | MED |
