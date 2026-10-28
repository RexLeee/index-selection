# Recrawl 設計：取得 128M 選取 URL 的 HTML

調研日期：2026-10-12。所有數字都來自唯讀查詢，查詢方法列在附錄 A。

程式在 `RexLeee/WebSearchEngine` 的 `IndexSelectionV2/`。實作細節以該目錄的 `docs/RECRAWL.md` 為準。

## 0. 結論

2026-10-18 更新：入口改成 WebCrawler PR #84 的 manual-bulk-crawl job API。提案的 C5、C6、C8、C10 由它取代，C9 改寫成 API job 的 lane 分配。politeness（C1–C4、C7）不再提出，沿用 crawler 現行的 AutoThrottle。理由見 §4.1 和 §8。同日 PR 更新到 `4dce38f`，加上 `ttl_days`、DELETE 和 FIFO 的 job 順序，isel 跟著改用它們（§4.4）。

2026-10-21 更新：PR head 是 `bd40191`。job 依 crawler 拆成分區，不再用檔案鎖；結果目錄在 POST 時就建立，只有 API 的狀態查詢能判斷 job 完成。isel 改成由 planner 查狀態，archive 從 `crawler_NN/` 分區讀結果（§4.2、§4.4）。

- recrawl 不另建爬蟲。isel 把 URL 清單交給正式 crawler 的 job API，crawler 用一般的 `html_spider` 抓取。
- isel 只做三件事：
  - `POST /v1/manual-bulk-crawls` 送出 job，帶 `ttl_days`；
  - 必要時 `GET` 查 job 狀態；
  - 唯讀地讀取 `results/{job_id}/` 裡的結果檔；
  - 正文存好後，`DELETE` 這個 job 的結果。
- isel 不寫 crawlerdb，不直接寫 `/data/manual_bulk_crawls`，也不需要知道 crawler 的路由。刪除結果一律透過 API。
- 只永久保存抽出的正文（約 0.4 TB）。原始 HTML 由 crawler 寫成結果檔，isel 抽完正文就不再需要它。
- 結果依位置對應到 doc。archive 會先驗證順序；順序不對時，job 標成 `error`，planner 全面停止（§4.4）。
- 0.2 qps 的規定：
  - isel 讓 recrawl 自己對每個 host 平均不超過 0.2 qps（`min_gap_s`）；
  - 跨 lane 的速率沿用 crawler 現行的 AutoThrottle。2026-10-18 決定不再提出 C1–C4、C7。
- 14 天的上限約 108–123M 頁（§3）。目標是兩週內盡量多抓，rank 高的先抓。
- 啟動前提：PR #84（`bd40191` 或之後）已 merge 並部署。isel 依賴它的 `ttl_days` 和 DELETE，否則 `/data` 會塞滿。
- lane 分配不在 PR #84 的範圍內，已和 crawler 組說好另外追蹤，WebCrawler #85 已改寫成只談這個問題。

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
- 沒有任何程式會把 `should_crawl` 設回 TRUE，所以抓過的頁不會再抓。WebCrawler PR #84（2026-10-15 開啟，未合併）的 `crawl-requests` 會這樣做，但它的結果仍然只進 `crawl_result`，8 小時後就被刪掉。
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
| `/data` | 7.0 TB，crawler 停止時剩 6.6 TB。crawler 運作時 IPC 暫存實測 4.4–5.0 TB（prune log，2026-09-20 到 10-05），剩約 2 TB |
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

### 4.1 原則：交給正式 crawler，isel 只讀結果

- 正式 crawler 已經有每 host 的 slot、robots.txt 和 AutoThrottle。PR #84 讓 API 的 URL 走同一個 downloader，所以 recrawl 不需要自己的抓取平面。
- 「每 host 相鄰請求至少隔 5 秒」仍需要 crawler 端的跨 process 閘門（C2）。原因見 2026-10-13 版的分析：redirect 會沿用原 host 的 slot，同一個 hostname 也可能落在兩個 process。
- isel 能控制的只有「什麼時候送哪些 URL」。所以它做兩件事：
  - 同一個 host 同時只在一個 job 裡；
  - 下一個 job 至少等 `送出的 doc 數 × min_gap_s`。

### 4.2 元件

```
selection run (manifest) ──recrawl-sync──► selectdb recrawl.{host,doc,job}
                                                  │ recrawl-plan（常駐）
                                                  ▼
             POST 127.0.0.1:4000/v1/manual-bulk-crawls {"urls": [...]} → job_id
                                                  │ API 正規化、去重、分給擁有該 domain 的 crawler
                                                  ▼
        /data/manual_bulk_crawls/pending/{job_id}.json + pending/crawler_NN/{job_id}.json
                                                  │ 正式 crawler 的 html_spider（priority 0，每個 process 最多 128 個在途）
                                                  ▼
       /data/manual_bulk_crawls/results/{job_id}/request.json
       /data/manual_bulk_crawls/results/{job_id}/crawler_NN/{NNNNN.json, NNNNN.html, progress.json}
                                                  │ recrawl-plan 用 GET 確認完成；recrawl-archive（常駐，只讀）
                                                  ▼
     /data/isel/text/fetch_date=YYYYMMDD/job-<batch_id>.parquet（正文，永久）
     /data/isel/log/YYYYMMDD/job-<batch_id>.jsonl.gz（每個 URL 的結果，永久）；更新 recrawl.doc
```

- API 在 POST 時就建好 `results/{job_id}/`、`request.json` 和每個 crawler 的空分區。所以目錄存在不代表完成。
- 每個 crawler 只寫自己的分區，寫完時在 `progress.json` 填 `completed_at`。所有分區都完成，API 的 `GET` 才回 `completed`。磁碟上沒有 `status.json`（PR 的 `docs/03` §3.7）。
- `request.json` 有 API 正規化並去重後的 `urls`，順序不變，還有每個 URL 的 `crawler_ids`。第 n 個結果是 `crawler_{crawler_ids[n]:02d}/{n:05d}.json`，HTML 在同一個分區。API 的下載路由也這樣解析。
- job 從 POST 起算 `ttl_days` 天後過期。過期後 crawler 不再處理它，`GET` 回 404，API 容器的 GC 每小時刪掉它的檔案。
- crawler 依 `created_at` 認領 job，所以 job 是 FIFO。

### 4.3 crawler 的改動

全部寫在 `docs/CRAWLER_RECRAWL_PROPOSAL.md`，2026-10-18 的狀態：

| 編號 | 內容 | 狀態 |
|---|---|---|
| C1 | politeness 設定：每 host 並發 1、delay 5 s、不加隨機延遲、AutoThrottle 關閉 | 不再提出，沿用現行 AutoThrottle |
| C2 | host 閘門，和以 hostname 為 key 的 slot | 不再提出 |
| C3 | robots.txt：Crawl-delay、5xx 視為禁止、24 小時快取 | 仍需要 |
| C4 | 429/503 放慢同一 host | 不再提出 |
| C5 | recrawl 車道 | 由 PR #84 取代 |
| C6 | recrawl 輸出：WARC + fetch log | 由 PR #84 的結果目錄取代 |
| C7 | 其他 fetcher 走同一個閘門 | 不再提出 |
| C8 | 結果的保留時間 | 由 PR #84 的 `ttl_days`、DELETE 和 GC 取代 |
| C9 | 車道仲裁 | 改寫成 API job 的 lane 分配問題，WebCrawler #85 只談這一項 |
| C10 | `recrawl-batches` API | 由 PR #84 的 job API 取代 |

### 4.4 isel 的元件（`src/isel/recrawl.py`，已實作）

**schema（revision 0003）**

| 表 | 一列代表 | 主要欄位 |
|---|---|---|
| `recrawl.host` | 一個 hostname 的 back queue | `host_id`（hostname 的 MD5 低 63 位元）、`best_rank`、`next_at`、`batch_id`（它所在的未結束 job） |
| `recrawl.doc` | 一個 `doc_id` | `url`、`host_id`、`rank`、`state`、`due_at`、`attempts`、`batch_id`、抓取結果欄位、`text_file`/`text_len`/`lang` |
| `recrawl.job` | 一個送給 API 的 job | `batch_id`（`batch_seq`）、`job_id`、`doc_ids`（送出順序）、`state`、時間欄位、`deleted_at`（API 上的結果已刪）、`records`、`error` |

- doc 的 `state`：`pending`、`dispatched`、`fetched`、`failed`、`gone`。
- job 的 `state`：

| 狀態 | 意思 | doc |
|---|---|---|
| `claimed` | 已寫進 DB，POST 還沒有確定的回應 | `dispatched` |
| `submitted` | API 回了 `job_id` | `dispatched` |
| `completed` | API 的 `GET` 回 `completed`。記下 API 的 `completed_at`，等 archive 套用 | `dispatched` |
| `error` | 結果違反合約（例如順序不對）。planner 全面停止，archive 每輪重試，直到修好或 TTL 到期 | `dispatched` |
| `applied` | 結果已套用。之後 plan 會 DELETE 它的結果，並填 `deleted_at` | — |
| `lost` | 不會有結果：API 拒絕、POST 沒回應、API 不認得（包括 TTL 已到期） | 已退回 `pending` |

- job 的期限只有一個：`result_ttl_days`（3 天），isel 在 POST 時送出。到期後由 server 停止 job，isel 不另設逾時。

- doc 是否在 live 清單上由 `rank IS NOT NULL` 表示。sync 只寫 `rank` 和 `model_run_id`，archive 只寫抓取欄位。
- plan、archive、sync 的每個交易都取同一個 advisory lock，所以不會交錯，也不會死結。

**`recrawl-sync`**：每次 swap 後跑一次。

1. 讀 live run 的 manifest。每個 `doc_id` 取 rank 最小的一列。
2. 每 1M 列一個交易，upsert `host` 和 `doc`。
3. 不在新清單的 doc 設成 `rank = NULL`。
4. 重算 `best_rank`。新增或改了 rank 的 doc 會喚醒它的 host。
5. swap 凍結時段內拒絕執行。

**`recrawl-plan`**：常駐，每 30 秒一輪。HTTP 呼叫不在交易內執行。

1. 回收：
   - `claimed` 超過 `claim_timeout_s`（600 秒）的 job 標成 `lost`，doc 退回 `pending`；
   - 安全網：`dispatched` 的 doc 或有 `batch_id` 的 host，如果它的 job 已不是 `claimed`/`submitted`/`completed`/`error`，就釋放；
   - `fetched` 而且到期的 doc 退回 `pending`。
2. 查狀態：`submitted`、`completed`、`error` 的 job，距離上次查詢超過 `status_poll_s`（60 秒）就 `GET` 一次。48 個 job 約每秒 1 次請求。
   - `completed`，而且 job 還是 `submitted`：標成 `completed`，記下 API 的 `completed_at`。
   - `completed` 但看不到結果目錄：job 保持 `submitted`，回報 `config_error`，本輪不認領。這代表結果目錄沒有掛載對，所有 job 都讀不到。
   - 404：`lost`，doc 退回 `pending`。TTL 到期的 job 走這條。沒修好就過期的 `error` job、archive 來不及套用的 `completed` job 也走這條。
   - `pending`/`running`：記下 `checked_at`。
   - API 有回應但不能用，例如 5xx 或格式不對：回報 `api_error`，跳過這個 job，繼續查其他 job。所以一個壞掉的 job 不會擋住其他 job 的完成。
   - 連不上 API：回報 `api_error`，本輪停止查詢，避免每個 job 各等一次逾時。
3. 刪除：`applied` 而且 `deleted_at` 是 NULL 的 job，逐一 `DELETE`。
   - 204 或 404：填 `deleted_at`。
   - 其他回應或沒有回應：回報 `api_error`，本輪停止，下一輪再試。
   - planner 停住時也照樣刪除。
4. 停止條件，任一成立就不認領：
   - 有 job 在 `error`；
   - API 上次拒絕了整個 job，還在 `api_backoff_s` 內；
   - 結果目錄沒有掛載對（上一步的 `config_error`）；
   - 在 crawler 的 job（`claimed` + `submitted`）已有 `max_jobs_inflight`（48）個；
   - 等 archive 套用的 `completed` job 也已有 `max_jobs_inflight` 個（`archive_lag`）；
   - `text_root` 所在磁碟剩餘低於 `min_free_gb`。
5. 認領：依 `best_rank` 分頁讀出可派送的 host（沒有未結束的 job，`next_at` 已到）。
   - 每個 host 取最多 `host_docs_per_job`（120）個到期 doc；
   - 依序裝進 job，每個 job 最多 `job_size`（3000）個 URL，一輪最多 `plan_max_jobs`（8）個 job；
   - job 內輪流排列各 host 的 URL，讓每個 crawler process 的 128 個 API 名額分散到多個 host；
   - 寫入 `recrawl.job`（`claimed`），doc 標成 `dispatched`，host 設 `batch_id` 和 `next_at = now + doc 數 × min_gap_s`，commit。
6. 逐一 POST（`{"urls": [...], "ttl_days": 3}`），依回應處理：

| 回應 | 處理 |
|---|---|
| 200 + `job_id` | `submitted` |
| 400「Invalid HTTP(S) URL at index N」 | 第 N 個 doc 標成 `gone`（`invalid_url`），其餘立刻重送，最多 `max_resend` 次 |
| 其他 400、415、500、503 | API 沒有接受任何 URL。job 標成 `lost` 並釋放；暫停 `api_backoff_s`；未送的 job 也釋放 |
| 沒有回應、200 但沒有可用的 `job_id` | 結果不明。job 保持 `claimed`，由 claim 逾時回收；未送的 job 釋放 |

   - 結果不明的 job 可能已被接受。isel 不知道它的 `job_id`，所以刪不到；TTL 到期後由 server 清掉。
7. `--dry-run` 輸出每 host 的容量報告，不呼叫 API。

**`recrawl-archive`**：常駐，每 60 秒一輪。它對 `result_root` 只讀不寫。

1. 候選 job：`completed` 和 `error`，而且 `result_root/{job_id}` 存在。archive 不呼叫 API。目錄已消失的 job 留給 planner，`GET` 回 404 後就變成 `lost`。`lost` 的 job 不再套用，因為它的 doc 已經重排。
2. `archive_workers` 個 process 各處理一個 job（`prepare_job`）：
   - 讀 `request.json` 的 `urls` 和 `crawler_ids`。兩者要等長，`crawler_ids` 要是非負整數；
   - 讀清單中每個位置在它的分區裡的 `NNNNN.json`；
   - 檢查每筆結果：必要欄位、型別、`url` 等於 `request.json` 的第 N 個、`html_file` 只能是 `NNNNN.html`、時間有 UTC offset；
   - 對應（`map_results`）：
     - 長度相同：第 i 個結果對應第 i 個 doc。若某位置的 URL 不同，而且等於另一個送出的 URL，代表順序變了，判定違反合約；
     - 長度較短（API 去掉了重複）：只用完全相同的字串對應。對不上的 doc 記成 `retry`（`unmatched`），下次通常單獨在一個 job，就能用位置對應；
   - 2xx HTML：計算 `payload_sha1`，用 Resiliparse 抽出 title、description、正文和語言。編碼由位元組或 `<meta charset>` 判斷；
   - 寫一個正文 Parquet 和一個 gzip 的逐 URL 結果。日期取自 job 的 `completed_at`（planner 記下的 API 值），所以重跑會覆蓋同一個檔。
3. 違反合約：job 標成 `error`，不寫 DB。planner 停止，直到修好後的下一輪成功套用。
4. 讀檔的 OSError（權限、EIO）：這個 job 下一輪重試，不改狀態。整個目錄消失也算這一種。
5. 主程序在一個交易內套用，最後才把 job 標成 `applied`，所以 crash 後重跑沒有副作用：

| 結果 | 新狀態 | 下次 |
|---|---|---|
| 2xx HTML | `fetched`，記下 `text_file`、`text_len`、`lang`、`payload_sha1` | `stale_days` 後 |
| 2xx 非 HTML、404、410 | `gone` | 不再抓 |
| `IgnoreRequest`（robots.txt 禁止；其他 middleware 也可能丟出） | `failed` | `robots_retry_days` 後 |
| 429、503 | `failed`，host 暫停 `retry_base_s` | 指數退避 |
| 其他失敗、`unmatched`、正文解析失敗 | `failed`，`attempts + 1` | 指數退避；超過 `max_attempts` 後改等 `stale_days` |

   - 2xx：比已存的新就接受，不管 doc 在等哪個 job；
   - 失敗：只在 doc 正在等這個 job 時才算；
   - 這個 job 的 doc 如果完全沒有結果，記成 `no_result` 重試。

設定都在 `config/isel.toml` 的 `[recrawl]`。部署用 `deploy/compose.compute.yaml` 的 `recrawl-plan`、`recrawl-archive` 兩個常駐 service（profile `recrawl`）：

- `recrawl-plan` 用 `network_mode: host`，因為 PR #84 的 API 只綁 `127.0.0.1:4000`。API 沒有驗證，所以不需要 token。DELETE 也由 plan 送出，因為 archive 連不到 API。
- 兩者都唯讀掛載 `/data/manual_bulk_crawls/results`，可寫掛載 `/data/isel`。結果檔是 root 的 0644，目錄是 0755，isel 的 1014:1015 可以讀。
- `up` 之前要先用 isel 使用者執行 `mkdir -p /data/isel`，否則 Docker 會用 root 建立它。
- `recrawl-archive` 給 8 CPU，`archive_workers = 8`。

**錯誤後怎麼恢復**

| 狀況 | 恢復方式 |
|---|---|
| job 在 `error` | 修好原因（程式或 server）後，archive 下一輪自動套用，planner 自動恢復。沒修好的話，TTL 到期後它會變成 `lost`，planner 也會恢復 |
| DELETE 一直失敗 | plan 每輪回報 `api_error` 並重試。結果目錄最晚在 TTL 到期後由 server 清掉 |
| archive 停機超過 TTL | 已抓到的結果會被 server 刪掉，job 變成 `lost`，doc 重抓。只損失重抓的時間 |
| 某個 crawler 停機 | 每個 job 的 URL 分在多個 crawler，所以幾乎每個 job 都等它。在途的 job 滿 `max_jobs_inflight` 後，planner 停止認領。crawler 在 TTL 內回來，job 就照常完成。超過 TTL，job 變成 `lost`，其他分區已抓到的結果也跟著丟掉 |
| planner 或 archive crash | 直接重啟。狀態都在 DB，檔案用 tmp + rename 寫入 |
| 要撤掉整個 recrawl | `git revert` 後執行 `alembic downgrade 0002`，它會 `DROP SCHEMA recrawl CASCADE`。正文 Parquet 不受影響 |

### 4.5 每一期的流程

1. 第一期抓完整 128M，14 天約 108M（§3）。
2. 之後每期只抓：新加入的頁、到期的頁、上一期沒抓完的頁。
3. 重抓間隔一律是 `stale_days`。Cho & Garcia-Molina 證明在 Poisson 變動模型下，固定間隔的平均新鮮度優於依變動率加權。
4. conditional GET（`If-None-Match` / `If-Modified-Since`）延後再做。它省下傳輸，但請求仍算進 QPS 額度。
5. 從清單移除的頁，`rank` 設成 NULL，不再派送。已抽出的正文保留。

### 4.6 大 host

- 14 天抓不完的 host，照 rank 抓到期末。剩下的低 rank 部分留到下一期。
- Wikimedia 的 host（en.wiktionary.org、zh.wikipedia.org、en.wikipedia.org 等）可以改用官方 snapshot，不必抓網頁。
  - 公開 HTML dump 在 2025-03-24 停止更新（信心 MED）。
  - 現在要用 Wikimedia Enterprise 的 Snapshot API，需要免費帳號和 token（信心 LOW，未實測）。
- 另一個選項：讓 selection 知道每個 host 每期的抓取上限，把超出的名額分給其他 host。這會改變 selection 的目標，列為待評估。

## 5. QPS 合規

| 項目 | 做法 | 狀態 |
|---|---|---|
| recrawl 自己的速率 | isel 讓同一 host 的下一個 job 至少等 `doc 數 × min_gap_s`，平均不超過 0.2 qps | 已實作 |
| 一個 job 內的突發 | crawler 用 AutoThrottle，每 host 並發 16、delay 0.25 s，所以 job 內可能短時間超過 0.2 qps | 沿用現行 AutoThrottle |
| 跨 lane 加總 | 一般 crawl 和 API 請求共用 slot，但 crawler 不保證 5 秒間隔 | 沿用現行 AutoThrottle |
| 車道分配 | PR #84 `bd40191`：API 請求 priority 0，每個 process 最多 128 個在途（`max_concurrency_ratio` 0.5）。這是上限，不是保底 | WebCrawler #85 追蹤 |
| robots.txt | crawler 照常遵守；被拒的 URL 回 `IgnoreRequest` | PR #84 已處理 |
| 429/503 | isel 暫停這個 host 的派送 | 已實作 |
| 事後稽核 | Loki 每 host 每 10 分鐘 ≤ 120 次 | 試抓時檢查 |

## 6. 儲存

2026-10-16 改成只永久保存正文。依據是 Host2 的實測：

- `/data/ipc/logs/prune_crawl_result.log` 每小時記一次 `/data` 用量。crawler 運作時（2026-09-20 到 10-05），prune 前約 5.0 TB，prune 後 4.4–4.5 TB。
- 所以 crawler 復工後，`/data` 只剩約 2 TB，不是原本估的 2.6 TB。5–6 TB 的永久 WARC 放不下。
- 正文的大小：Common Crawl 2025-10 的 WET（抽出的純文字）約每頁 2.8 KiB；`RESEARCH.md` §8 估 3.1 KB。128M 頁約 0.35–0.4 TB。

| 資料 | 大小估計 | 位置 | 保留方式 |
|---|---|---|---|
| pending job 檔 | 每個 job 一個 manifest，加上每個 crawler 一個佇列檔，可忽略 | Host2 `/data/manual_bulk_crawls/pending` | 佇列檔由 crawler 做完後刪除；manifest 由 DELETE 或 GC 刪除 |
| 原始 HTML + 每 URL 的 JSON（暫存） | 壓縮後的 128M 頁就有 5–6 TB；每個 URL 兩個檔 | Host2 `/data/manual_bulk_crawls/results/{job_id}` | isel 套用後立刻 DELETE；isel 沒刪到的由 TTL（3 天）清掉 |
| 正文 Parquet | 約 0.35–0.4 TB | Host2 `/data/isel/text/fetch_date=YYYYMMDD/job-<batch_id>.parquet` | 永久 |
| gzip 的逐 URL 結果 | 約 8 GB | Host2 `/data/isel/log/YYYYMMDD/job-<batch_id>.jsonl.gz` | 永久；selectdb 沒有備份，這些檔是重建 DB 的依據 |
| `recrawl` schema | 約 37 GB（未實測） | Host1 selectdb（`:5445`） | 常駐 |

- planner 的背壓：`/data/isel` 剩餘低於 `min_free_gb`（800 GB）時停止派送；在 crawler 的 job 和等 archive 的 `completed` job 各最多 `max_jobs_inflight` 個。
- 結果目錄同時只留未套用的 job，加上少數等 DELETE 重試的 job。上限約為 2 × `max_jobs_inflight` × `job_size` = 288k 頁。
- Host1 `/data` 在 2026-10-16 只剩 229 GB（97%）。`recrawl` schema 的 37 GB 要等 Host1 有空間再載入（R1）。
- 每頁正文的實際大小要在試抓後更新。2026-10-17 的 590 KB 只來自 9 個網頁的 CPU 測試，不能拿來估磁碟用量。

## 7. 分階段

| 階段 | 內容 | 前提 |
|---|---|---|
| R0 | schema（0003）、`recrawl-sync`/`plan`/`archive`、正文抽取、測試（2026-10-18 改接 PR #84 job API；2026-10-21 對齊 `bd40191`；本地 e2e 包含 PR 的真實 API 和分區寫入程式） | 只動本 repo |
| R1 | ⛔ 確認 `:5445` 在 0002 後，`migrate-selectdb` 到 0003；`recrawl-sync` 在 live 上載入；`recrawl-plan --dry-run` 容量報告 | batch 20 swap 完成 |
| R2 | PR #84（`bd40191` 或之後）merge 並部署 | crawler 組同意；crawler 復工 |
| R3 | ⛔ 啟動 `recrawl-plan`、`recrawl-archive`，試抓 1–5M 頁，量測 HTML 和正文大小 | R2 |
| R4 | 全量；之後每期只抓增量 | R3 的量測 |

## 8. 不採用的做法

2026-10-18 改接 PR #84 時新增的決定：

| 做法 | 不採用的理由 |
|---|---|
| 用 URL 字串對應結果 | API 會正規化 URL。例如 PR 的 w3lib 2.3.1 把 `%25C3%25A9` 改成 `%C3%A9`，用字串會找不到 doc。所以用位置，並驗證順序 |
| 用比例門檻判斷順序錯誤 | 一個 job 會放同一個 host 的 120 個 URL。整個 host 被改寫時就佔 4%，比例門檻會誤判並停住 planner。改用精確檢查：不同的 URL 若等於另一個送出的 URL，才算順序錯誤 |
| 讀 `.incomplete/` 拿部分結果 | 那是 PR 的內部目錄，沒有寫進合約 |
| isel 以 root 執行並刪除結果 | 破壞 isel 非 root 的部署；`.claims/{job}` 也是 server 的內部目錄。PR #84 已提供 DELETE |
| 每個 job 只送一個 crawler 的 URL | 原本用來避開 uuid 順序造成的插隊。PR #84 已改成 FIFO，不再需要 |
| 等整波 job 都完成再送下一波 | 最慢的 host 會拖住整波 |
| isel 自己的 job 逾時，再用 DELETE 取消 | 要同時維護 isel 的逾時和 server 的 TTL 兩個期限。改用 TTL 當唯一期限：server 會停止過期的 job，isel 從 404 得知 |
| 套用 `lost` job 晚到的結果 | TTL 到期後 server 會丟掉晚到的結果，所以不會再有晚到的目錄。刪掉這段處理，程式比較簡單 |
| TTL 用 PR 的預設 14 天 | 沒套用的結果會在 `/data` 留 14 天。3 天已足夠 FIFO 下的 job 跑完；archive 停機超過 3 天，只會重抓 |

2026-10-21 對齊 `bd40191` 時新增的決定：

| 做法 | 不採用的理由 |
|---|---|
| archive 自己讀各分區的 `progress.json` 判斷完成 | 這是複製 API 的 `job_status`。PR 文件寫明只有 API 判斷完成，而且這個格式一週內改了兩次。也會漏掉 API 的過期檢查 |
| archive 用 HTTP 下載結果 | 一個 3,000 URL 的 job 約 6,000 次 GET，全量約每秒 60 MB 經過 FastAPI。archive 也就要連得到 API。isel 和結果在同一台機器，直接讀檔就好 |
| 掃描 `crawler_*` 目錄找結果檔 | 會看到 `.json.tmp`、`progress.json` 等其他檔；crawler 編號超過 99 時目錄名稱是 3 位數。改用 `request.json` 的 `crawler_ids` 直接定位，和 API 的下載路由一致 |
| `completed` 的 job 也算進 `max_jobs_inflight` | 它已經不佔 crawler。算進去會讓 archive 的延遲拖慢派送；改成另外設一個同樣大小的上限 |

以下是 2026-10-18 之前的決定，以 C5、C6、C9、C10 為前提，保留作紀錄：

| 做法 | 不採用的理由 |
|---|---|
| 用 starter crawler 另建 recrawler | 它和正式 crawler 不共享狀態，無法保證同一 host 間隔 5 秒 |
| 把 crawlerdb 的 `should_crawl` 設回 TRUE | 要寫 crawlerdb；會被 offerer 排序和 GC 影響（`gc/service.py:158-176`）；結果以 redirect 後的 URL 為 key；ingest 會增加 crawlerdb 負載 |
| 用 PR #84 的 `crawl-requests` 做 recrawl | 就是上一列的做法，只是改由 API 執行。另外：128M 個 URL 要打約 128,000 次 API，每次都寫 crawlerdb；HTML 8 小時後就被刪；送出後無法追蹤 |
| 在 `crawl-requests` 加 mode 參數 | 兩者語意不同：一個改 crawlerdb 的待爬清單、送出後不追蹤；另一個不能碰 crawlerdb，而且結果要帶回 `batch_id`、`doc_id` |
| isel 直接寫 `/data/ipc/recrawl_queue` | `/data/ipc` 是 root 755，要 root 先開群組權限；入口也無法和 PR #84 共用 |
| API 自己算路由和 `host_id` | 路由要讀 crawlerdb 的 `shard_split_subdomain`；`host_id` 用 MD5 第 8–15 byte 的 little-endian，兩邊重寫很容易算出不同的值 |
| 永久保存全量 WARC | 5–6 TB；crawler 運作時 Host2 只剩約 2 TB（§6）。建 inverted index 只需要正文 |
| archive 搬走或刪除 crawler 的結果檔 | 要寫入權限；由 crawler 依保留時間刪除就夠了，isel 只讀 |
| 只靠每個 process 的 Scrapy slot | redirect 沿用原 host 的 slot；routing 改變和重啟時，兩個 process 會打同一個 host（§4.1） |
| 共用 Redis token bucket | 多一個常駐服務；SQLite on tmpfs 做得到同樣的事 |
| 以 `domain_id`（eTLD+1）為單位規劃 | 規則的單位是 hostname；一個 eTLD+1 的多個 host 會被一個檔綁在一起 |
| 兩條車道共用排除集合 | 一般車道持有大 host 時，recrawl 被擋住，而大 host 正是瓶頸 |
| recrawl 請求設較高的 Scrapy priority | 每個 host 佇列內是嚴格 priority，等於嚴格優先。最大的 10 個 host 上一般爬取的需求是滿的，它們會停 14 天以上 |
| 固定比例（例如 3:1） | 14 天的頁數和配速幾乎一樣（113.0M 對 112.8M），但損失集中在前幾天：尖峰 14.0% 對 8.5% |
| 在閘門內跨 process 仲裁 | 兩條車道搶同一個 host 時，幾乎都在同一個 process，在 scheduler 仲裁就夠。跨 process 仲裁會讓 16 個 process 的 SQLite 寫入互相等待 |
| offerer 跳過有 recrawl 的 domain | 單位是 eTLD+1，太粗；延遲約 60 秒；要改 offerer |
| 份額下限 0.10 | 尖峰只比 0.25 少 1.2 個百分點；一個 100 URL 的檔在一般 host 上要約 1.9 小時，Crawl-delay 長的 host 更久，逾時要跟著拉長 |
| C10 先正規化 URL 再驗證 | fetch log 要求 `doc_id == md5(source_url)`，改寫 URL 就對不上。PR #84 的 `validate_url` 會改寫 URL，`validators.url` 也會擋掉 w3lib 視為 canonical 的 `\|`、`[ ]`；兩邊的 w3lib 版本也不同（2.3.1 對 2.5.0） |
| HTML 經 jsonl 傳給 isel，再由 isel 寫 WARC | 要 base64（多 33%），或只能傳失去原始編碼的 `response.text` |
| 在 router 端對高分頁直接保存內容 | crawler 不會重抓已抓過的頁，所以抓不到已經在清單裡的 128M（`DESIGN.md` §8.4 的選項） |
| 以 `removed` 狀態表示移出清單 | sync 和 archive 會寫同一欄；改用 `rank IS NULL` |

## 9. 風險與待確認

1. PR #84 何時 merge 並部署。`bd40191` 有 TTL、DELETE、FIFO 和分區結果，isel 已對齊它（2026-10-21 本地 e2e）。PR 再改結果格式時，isel 要跟著改。
2. lane 分配（#85 的 C9）。一般 crawl 最忙的正是最大的 host；等待中的 API 請求還佔著 prefetch 名額，可能讓一個 crawler 停止收 API URL。雙方已在 PR 留言說好不在 #84 處理，#84 之後再做。`bd40191` 的 128 名額只是 API 的上限，沒有保底。
3. API 內部的 head-of-line。128 個名額包含在 scheduler 排隊的請求，依 job、index 順序填，沒有 per-host 上限。依 rank 排，前 50 個 job 每個只有約 29 個 host，分到 16 個 crawler 後每個約 2 個。若大 host 被 AutoThrottle 壓到約 0.3 qps，總速率約 10 req/s，遠低於 14 天需要的 106 req/s。只調小 `host_docs_per_job` 會讓大 host 在「一個 host 只在一個 job」的規則下抓不完。候選解法是讓同一 host 同時在多個 job，由 `next_at` 控速；等 Loki 或試抓的實測再決定。
4. `result_ttl_days` 是否夠長。lane 分配改好之前，忙碌 host 上的 job 可能 3 天跑不完。試抓時要量 job 從送出到完成的時間。
5. politeness 沿用 crawler 現行的 AutoThrottle，跨 lane 的 0.2 qps 沒有額外保證。試抓時用 Loki 稽核每 host 每 10 分鐘的請求數。
6. crawler 已復工。它從 2026-10-06 起停止運作，2026-10-20 約 03:30 UTC 重新啟動（2026-10-21 唯讀確認）。PR #84 的 API 還沒部署。
7. HTML 和正文的實際大小。
8. Wikimedia Enterprise snapshot 能不能用。
9. 「low level domain」已確認為完整 hostname。若助教另有解釋，3 節的表要改用 eTLD+1 那一列：最大的是 wikipedia.org，要 56.7 天；14 天可抓 122.0M。

## 10. Inverted index（規劃）

本節只記錄預計做法。這一輪要確保的是：recrawl 存下來的資料足以建索引。

**資料來源**

- 正文 Parquet（`text_root`）是唯一的資料來源。每個 job 一個檔。欄位：`doc_id, batch_id, url, final_url, fetched_at, http_status, lang, title, description, body, text_len, payload_sha1`。`batch_id` 是 `recrawl.job` 的主鍵，`payload_sha1` 是原始 HTML 的 SHA-1。
- `doc_id` 是 uint64，和 manifest 相同，可以直接 join。`recrawl.doc` 裡的 `doc_id` 是同一個值的 signed BIGINT。
- 只讀 `**/*.parquet`。crash 可能留下 `.parquet.tmp`，要略過。
- 同一個 doc 可能出現在多個檔案裡，例如重抓或重跑。建索引時每個 `doc_id` 只取 `fetched_at` 最新的一列。
- 只索引 live 清單上的 doc（`recrawl.doc.rank IS NOT NULL`），所以索引是 `selected_urls_current` 的子集，ranked ≤ indexed。

**索引欄位**

| 欄位 | 用途 |
|---|---|
| `url`、`canonical_url`、`final_url` | 三者互為別名。golden URL 經 w3lib 正規化後比對；約 23% 的 golden 已爬頁是 redirect 來源 |
| `title`、`description`、`body` | 全文檢索，BM25 |
| `lang` | 選 tokenizer。golden 關鍵字來自 10 個地區，包含中日韓文字 |

**引擎**

- 依 `ARCHITECTURE_SPEC.md` 的選擇用 Quickwit：索引放磁碟，searcher 不需要把整個 inverted index 放進 RAM。Typesense 對 128M 文件估計要 100–200 GB RAM，不採用。
- 大小：精簡 BM25 約 0.2 TB，完整索引約 1.2 TB（`RESEARCH.md` §8）。實作前要依 §6 的剩餘空間決定放在哪台主機。

**沒抓到正文的 doc**

- `gone`、`failed` 或還沒抓到的 doc，用備援文件：URL 的字詞，加上 crawlerdb 的 title 和 anchor。這需要在 full export 多拉 `title`、`anchor_text` 兩欄。
- 這樣每期換清單時，索引從第一天就涵蓋全部 128M 個 URL，只是部分文件只有 metadata。

**Recall@10 量測**

- 官方 Metric 目前把 `ranked_num` 寫死成 0，`TypesenseRankMeasure` 只是 stub。
- 離線量法：用每個 batch 的 golden 關鍵字查詢，取前 10 名，以 canonical URL 比對 golden URL。
- 之後若要進官方 Metric，要另提 `RankMeasure` 的 PR，寫入 `metric_url.is_ranked` 和 `ranked_rate`。

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
| Host2 `/data/ipc/logs/prune_crawl_result.log`（每小時的 `/data` 用量）、`df`、`id` | 2026-10-16 | HIGH |
| live run `20261014T030855450365Z` 的 manifest 和 full export 的 join：128,012,317 列全部有 `last_fetch_ok` | 2026-10-16 | HIGH |
| [Common Crawl 2025-10 crawl archive](https://commoncrawl.org/blog/october-2025-crawl-archive-now-available)（2.61B 頁、WET 7.38 TiB） | 2025-10 | HIGH |
| Resiliparse / FastWARC 1.0.9 文件，經 ctx7 `/chatnoir-eu/chatnoir-resiliparse` | 2026-10 查詢 | HIGH |
| WebCrawler PR #84（`upstream/pr-84`，head `d158461`） | 2026-10-15 | HIGH |
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
