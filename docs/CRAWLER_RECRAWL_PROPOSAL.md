# 提案：正式 crawler 的 politeness 修正與 recrawl 車道

- 對象：WebCrawler（crawler 組）
- 提案人：index selection 組
- 日期：2026-10-13；2026-10-16 更新（新增 C10，C8 改成保留時間）；2026-10-17 更新（C10 對齊 PR #84 `35c5677`：URL 原樣使用、400 格式、多 worker；C6 檔案權限）
- 狀態：草稿，待 crawler 組討論。2026-10-18：C5、C6、C8、C10 由 WebCrawler PR #84（`4dce38f`）取代，C9 改寫成 API job 的 lane 分配，C1–C4、C7 不再提出，見下方「2026-10-18 狀態」

本文所有路徑都相對於 `WebCrawler/`，版本是 `2e4f87c`，和 Host2 `/opt/WebCrawler` 一致。Scrapy 原始碼取自 Host2 的 `webcrawler-crawler` image，版本 2.16.0。

## 2026-10-18 狀態

PR #84 改成 job API：`POST /v1/manual-bulk-crawls` 回傳 `job_id`，正式 crawler 用 priority -1 抓取，結果寫到 `/data/manual_bulk_crawls/results/{job_id}/`。`4dce38f` 再加上 `ttl_days`（1–60 天，從送出算起）、`DELETE /v1/manual-bulk-crawls/{job_id}`、每小時的 GC，以及 FIFO 的 job 順序。isel 已改接這個介面（`docs/RECRAWL_DESIGN.md` §4）。

| 編號 | 狀態 |
|---|---|
| C1–C4、C7 | 不再提出。politeness 沿用 crawler 現行的 AutoThrottle |
| C5、C6、C10 | 由 PR #84 的 job API 和結果目錄取代 |
| C8 | 由 PR #84 的 `ttl_days`、DELETE 和 GC 取代 |
| C9 | 改寫成 API job 的 lane 分配問題：API 請求在忙碌的 host 上永遠排最後，等待時又佔著 prefetch 名額。雙方已在 PR 留言說好 #84 之後再做 |

WebCrawler #85 在 2026-10-18 改寫成只談 C9，英文全文以 issue 為準。以下第 0 節之後的內容是 2026-10-17 的版本，保留作紀錄。

## 0. 摘要

我們請 crawler 組做兩類改動：

1. **politeness 修正（C1–C4、C7）。**
   - 不管有沒有 recrawl，正式 crawler 現在都不保證每個 hostname 0.2 qps。
   - 這是課程的硬性規定（`../crawler/starter-project.md:8-10`）。
   - 修正的核心是一個跨 process 共用的 host 閘門。每次送出請求前，閘門確認這個 hostname 距離上一次請求至少 5 秒。
2. **recrawl 車道（C5、C6）。**
   - index selection 選出的 128M 個 URL，需要重抓原始 HTML。isel 從 WARC 抽出正文永久保存，WARC 只暫存 24 小時。
   - recrawl 只是正式 crawler 的第二個輸入目錄。它走同一個閘門，所以兩條車道加總後仍遵守 0.2 qps。
3. **共用入口（C10）。**
   - WebCrawler PR #84 新增了 API gateway 和 `scheduler_ingest` 的 API 服務，提供 `crawl-requests`。
   - 我們請 crawler 組在同一個 API 服務上加第二種資源 `recrawl-batches`。isel 只透過它送出 recrawl 工作，不直接寫 `/data/ipc`。
   - 兩種資源共用 gateway、URL 檢查（從 `libs/urls.py` 拆出的 `check_url`）和 `/scheduler_ingest/v1/` 前綴，但語意不同，所以分開。
4. **車道仲裁（C9）。**
   - 兩條車道大量搶同一批 host：一般爬取 93.8% 的請求打在 recrawl 清單內的 host（§5.1）。
   - 同一個 host 的額度用加權輪替分配。份額由 isel 依每個 host 的期限算出，寫在 queue 檔裡。一般車道至少保留 25%。
   - 模擬結果：recrawl 14 天約 112.8M 頁；一般爬取的尖峰損失 8.5%，比固定 3:1 的 14.0% 低。

isel 端的程式已經完成並測試（`index-selection/src/isel/recrawl.py`），包括對 C10 的本地 e2e 測試。它負責三件事：決定抓哪些 URL、呼叫 C10 送出 batch、讀回結果並抽出正文。crawler 端需要做的是：照 C10 寫 queue 檔、照第 4 節讀檔和寫結果。

我們不再需要 5–6 TB 的空間。isel 只永久保存抽出的正文（約 0.4 TB，放在 isel 自己的 `/data/isel`）。WARC 只要在 `recrawl_result` 保留 24 小時（C8）。

## 1. 規則

| 規則 | 內容 |
|---|---|
| 限速單位 | 完整 hostname。`en.wikipedia.org` 和 `zh.wikipedia.org` 分開計算 |
| 速率 | 每個 hostname 每秒最多 0.2 次，相鄰兩次請求至少隔 5 秒 |
| 範圍 | 所有會抓網頁的機制加總：正式 crawler、recrawl、sitemap_patroller、golden parent patrol、手動腳本 |
| robots.txt | 必須遵守。robots.txt 本身的請求也算進額度 |

## 2. 現況問題

以下每一項都能單獨造成違規。

| # | 問題 | 證據 | 後果 |
|---|---|---|---|
| P1 | 實際生效的限速是每 host 並發 16、delay 0.25 s，而且開著 AutoThrottle。設定來自 supervisord 的 env，改 `settings.py` 預設值不會生效 | `containers/crawler/supervisord.conf:21-24,48-51,75-78`；`crawler/settings.py:44-63` | 2026-10-04 的 10 分鐘樣本中，204 個 host 平均超過 0.2 qps，最高 0.27 qps |
| P2 | `RANDOMIZE_DOWNLOAD_DELAY` 沒設，Scrapy 預設是 True | Scrapy `default_settings.py` | 間隔會乘上 0.5–1.5，即使 delay 設成 5 秒也會低於 5 秒 |
| P3 | **redirect 沿用原 host 的 slot。** downloader 把 slot 名稱寫進 `meta["download_slot"]`；redirect 用 `request.replace()` 複製 meta | Scrapy `core/downloader/__init__.py:166-169,180`、`downloadermiddlewares/redirect.py:123-131` | `a.com` 轉到 `b.com` 時，請求在 `a.com` 的 slot 排隊。`b.com` 自己的 slot 不知道這次請求，所以它可以在 5 秒內再送一次。meta refresh 也一樣 |
| P4 | 一個 hostname 可能同時由兩個 process 抓 | `libs/db/sharding/key.py:22-41`；`scripts/refresh_split_whitelist_cron.sh:4-8` | 原因有三種：跨 shard 的 redirect；白名單切分後 router 重啟前的過渡期；process 重啟後 slot 的計時歸零。每個 process 的 slot 只管自己 |
| P5 | Scrapy 在 pop 時記錄 `slot.lastseen`，真正送出是之後的 coroutine | Scrapy `core/downloader/__init__.py:204-211` | reactor 忙碌時，兩次 pop 隔 5 秒，實際送出的間隔卻可能更短。starter 專案量到 timer 延遲 1.3–4.6 秒（`../crawler/crawler/downloader.py` 的說明） |
| P6 | 沒有處理 Crawl-delay | `crawler/settings.py:10`；Scrapy `robotstxt.py` 不讀 Crawl-delay | 站點要求更長間隔時沒有遵守。AutoThrottle 每次收到 200 也會把 delay 拉回 ≤ 10 s |
| P7 | robots.txt 回 5xx 時，Scrapy 仍會解析錯誤頁的內容，等於全部允許 | Scrapy `robotstxt.py:118-123` | RFC 9309 §2.3.1.4 要求 5xx 視為全部禁止 |
| P8 | 429/503 只等 Scrapy 的重試；同一檔的其他 URL 繼續送 | `crawler/settings.py:92-93` | 站點要求放慢時，crawler 沒有放慢 |
| P9 | sitemap_patroller 有自己的 fetcher，間隔各自計算 | `containers/sitemap_patroller/patrol/service.py:63-80`；`discover/service.py:35-55` | 它和 Scrapy 可能在 5 秒內各打一次同一個 host。目前容器已停止 4 個月 |

## 3. 提案

### C1：politeness 設定

改 `containers/crawler/supervisord.conf` 三個 program 的 `environment=`：

```ini
CRAWLER_USE_AUTOTHROTTLE="false",
CRAWLER_CONCURRENT_REQUESTS="512",
CRAWLER_CONCURRENT_REQUESTS_PER_DOMAIN="1",
CRAWLER_DOWNLOAD_DELAY="5.0"
```

`crawler/settings.py` 加一行：

```python
RANDOMIZE_DOWNLOAD_DELAY = False
```

理由：

- AutoThrottle 關閉。每 host 只有 1 個並發時，它沒有作用，而且會覆蓋 Crawl-delay（P6）。
- fixed 分支會讀 `domain_qps.json`。它的 `_default` 是 delay 5.0、並發 4，其中 39 個 host 的 delay 是 0.333。這些值都要改：`_default` 的並發改成 1，並刪掉 delay 小於 5 秒的項目。
- `CONCURRENT_REQUESTS` 調到 512。slot 佇列中的請求也算進這個上限。一般車道 144 個 domain 加上 recrawl 64 個 host，會撞到 256。

驗收：啟動 log 的 `crawler.config` 事件顯示新值。

### C2：跨 process 的 host 閘門（核心）

**設計**

- 閘門是一個 SQLite 檔：`/run/crawlgate/gate.sqlite`，放在 Host2 的 tmpfs 上。
- crawler 和所有其他 fetcher 都 bind-mount 同一個目錄。
- 每次送出請求前，fetcher 對 hostname 做一次原子的「檢查並設定」：
  - 距離上一次請求不到 `gap` 秒時，這次不送，稍後再試。
  - 已經超過 `gap` 秒時，記下現在的時間，然後送出。
- `gap` 是 `max(5.0, Crawl-delay)`。
- 時鐘用 `time.monotonic()`。Linux 的 `CLOCK_MONOTONIC` 在同一次開機內對所有 process 和容器一致。tmpfs 在重開機時清空，正好和時鐘一起歸零。

```sql
CREATE TABLE IF NOT EXISTS host_gate (
    host TEXT PRIMARY KEY,
    last REAL NOT NULL,          -- monotonic time of the last dispatch
    gap  REAL NOT NULL DEFAULT 5.0
);
-- Acquire: returns a row only when the host may be fetched now.
INSERT INTO host_gate (host, last) VALUES (:host, :now)
ON CONFLICT (host) DO UPDATE SET last = excluded.last
WHERE excluded.last - host_gate.last >= host_gate.gap
RETURNING last;
```

**Scrapy 端的實作**

做法是自訂 `DOWNLOADER`，移植 `../crawler/crawler/downloader.py`，並做兩個修改：

1. `get_slot_key` 一律回傳 `urlparse(request.url).hostname`，忽略 meta。這修好了 P3。starter 用 eTLD+1，這裡改用 hostname，因為規則的單位是 hostname。
2. 在 `_download` 的開頭，送出前先 `await gate.acquire(host)`：
   - 取得時寫 dispatch log，然後送出。
   - 取不到時等 `last + gap - now` 秒再試。等待期間 slot 不放行下一個請求，因為每 host 只有 1 個並發。

```python
class HostGateDownloader(Downloader):
    def get_slot_key(self, request):
        return urlparse_cached(request).hostname or ""

    async def _download(self, slot, request):
        host = self.get_slot_key(request)
        while (wait := self.gate.try_acquire(host, monotonic())) > 0:
            await sleep(wait)                 # deferLater on the reactor
        self.dispatch_log.write(host, request.url, monotonic())
        ...                                   # the rest of Scrapy 2.16 _download, unchanged
```

**其他行為**

- SQLite 開 WAL，`busy_timeout = 1000`。
- 預估負載約 450 次 acquire/s（16 個 process 合計），對 tmpfs 上的 SQLite 很輕。
- 閘門出錯時不送出（fail closed），請求記為 `gate_error`。

**這個閘門一次解決的問題**

- P3 和 P4：任何 process、任何 redirect，都要先過同一個 hostname 的紀錄。
- P5：時間在送出前才記錄。
- process 重啟：紀錄在 tmpfs 上，不會隨 process 消失。所以不需要重啟後暫停。
- C7：其他 fetcher 只要呼叫同一個閘門。

**驗收**

- 單元測試：兩個 process 對同一 host 搶 acquire，間隔都 ≥ 5 秒。
- 單元測試：redirect 到另一個 host 時，用的是目標 host 的紀錄。
- 上線後：dispatch log 經 `../crawler/ops/verify_politeness.py` 檢查，每個 hostname 的最小間隔 ≥ 5.0 秒。

### C3：robots.txt

移植 `../crawler/crawler/middlewares/robots.py`，並做以下調整：

| 項目 | 做法 |
|---|---|
| Crawl-delay | 寫進閘門的 `gap`，取 `max(5, Crawl-delay)` |
| Crawl-delay > 60 s | 這個 host 不抓，結果記為 `robots_crawl_delay` |
| robots.txt 回 5xx 或讀不到 | 視為全部禁止（RFC 9309），結果記為 `robots_unavailable`；之後依退避時間再試 |
| robots.txt 回 4xx | 視為全部允許（RFC 9309） |
| 快取 | 24 小時後重抓（RFC 9309 §2.4） |
| robots.txt 請求本身 | 走同一個閘門 |

**待討論**：`USER_AGENT` 現在是 Chrome 字串（`crawler/settings.py:17-21`），所以只有 `User-agent: *` 規則會被套用。要不要改用可識別的 UA？starter 用 `ntu-sa-crawler/1.0 (...)`。

### C4：429 和 503

- 回應帶 `Retry-After` 時，把這個 host 的 `gap` 暫時調高到 `Retry-After`，上限 1 小時。
- 同一 host 佇列中的 **recrawl 車道**請求立刻失敗，原因記為 `host_backoff`，不要再送。isel 會依 `retry_after_s` 暫停這個 host，之後重新派送。
- **一般車道的請求不可以丟棄**，只能留在閘門等 `gap` 過去。理由：
  - offerer 寫 queue 檔時，就已經把這些 URL 設成 `should_crawl=FALSE`（`containers/scheduler_control/offerer/selection/golden_discovery_ranker_v1_strategy.py:189-223`）。
  - queue 檔讀完就刪（`containers/crawler/crawler/queue_consumer.py:99-106`）。
  - 所以丟掉的一般請求不會再被派送，URL 就永久遺失。
- recrawl 車道的請求設 `dont_retry=True`。重試由 isel 依退避時間重新派送。

### C5：recrawl 車道

在 `spiders/spider.py` 加第二個 `QueueConsumer`：

| 項目 | 一般車道（現況） | recrawl 車道（新增） |
|---|---|---|
| 目錄 | `/data/ipc/url_queue/crawler_NN/` | `/data/ipc/recrawl_queue/crawler_NN/`，由 C10 的 API 寫入 |
| 檔名的 id | `domain_id`（eTLD+1） | `host_id`（一個 hostname） |
| 在途水位 | `IPC_DOMAIN_LOW_WATERMARK = 144`（寫死在 `crawler/settings.py:87`） | `RECRAWL_HOST_LOW_WATERMARK`，env 控制，預設 128 |
| 在途排除集合 | `_domain_pending` | 另一份 `_recrawl_pending` |
| `meta` | `source_url`、`_track_domain_id` | 另加 `lane="recrawl"`、`batch_id`、`doc_id`、`recrawl_share`（取自 queue 檔，C9 用） |
| 結果 | `crawl_result/crawler_NN/` | 只寫 C6 的 WARC 和 fetch log，**不進 `JsonPipeline`** |

設計說明：

- **兩條車道的排除集合分開。** 如果共用，一般車道持有 espn.com 時，recrawl 就不能抓 `www.espn.com`。而這些大 host 正是 recrawl 的瓶頸。兩條車道打同一個 host 時，由 C9 決定誰先送。
- **水位 128。** 模擬顯示，前幾天 recrawl 的瓶頸是在途 host 數，不是單一 host 的額度。水位從 64 提高到 128 後，第 3 天的進度從 30.8M 提高到 61.6M（§5.2）。
- **recrawl 的結果和失敗不可以進 `JsonPipeline`。** ingestor 會依失敗原因暫停整個 domain_id，例如 429 暫停 1 h × 2^n（`containers/scheduler_ingest/ingestor/db_ops.py:42-54`）。recrawl 的失敗如果進去，會暫停一般車道的 domain。
- **建議 `IPC_DOMAIN_LOW_WATERMARK` 改成 env 可覆寫。** 一般車道的 domain 檔要等 100 個 URL 都做完才釋放名額。C9 讓被搶的 host 變慢時，crawler 組可以調高這個值補回名額。
- **`parse` 要用 try/finally 包住 `_finish_owned_request`。** 現在 `parse` 拋例外時（`spiders/spider.py:360-418`），這個 domain 會一直留在排除集合裡，直到 process 重啟。recrawl 車道的 callback 只做寫 WARC 和寫 log 兩件事。
- **檔案仍是讀完就刪（at-most-once）。** isel 會處理遺失的結果：它在檔案被取走 8 小時後（`dispatch_timeout_s`），把沒有結果的 URL 重新派送。8 小時涵蓋最慢的情況：Crawl-delay 60 秒、份額 0.25 時，100 個 URL 要 24,000 秒。

### C6：recrawl 的輸出

每個 process 同時寫一組檔案，兩個檔共用同一個 basename：

```
/data/ipc/recrawl_result/crawler_NN/recrawl-NN-YYYYMMDDTHHMMSSZ-SEQ.warc.gz
/data/ipc/recrawl_result/crawler_NN/recrawl-NN-YYYYMMDDTHHMMSSZ-SEQ.jsonl
```

**寫入規則**

1. 寫入中的檔案名稱加上 `.open`。
2. 每 1 GB 或 10 分鐘換一組。換檔時先 rename WARC，再 rename jsonl。isel 只讀已經 rename 好的 `.jsonl`。
3. 每個 WARC record flush 之後，才寫它對應的 log 行。
4. 每個 queue 檔的每個 URL，最後都要寫一行 log，包括失敗、robots 禁止和閘門拒絕。
5. 沒有任何成功回應的時段，也要產生 WARC，裡面只有 warcinfo record。isel 依 log 找 WARC，WARC 必須存在。
6. 檔案權限要明確設定，不依賴 umask：檔案 0644、目錄 0755。isel 是 uid 1014，只有讀權限；用 `tempfile.mkstemp` 建的檔是 0600，isel 讀不到。isel 讀不到時不會丟資料，但這個 log 會一直卡在重試。
7. 保留和刪除：
   - 一組檔案保留 24 小時後刪除。先刪 `.jsonl`，再刪 `.warc.gz`，所以看得到 log 就代表 WARC 還在。
   - isel 只讀這個目錄，不搬、不改、不刪。它用 selectdb 的 `recrawl.log` 記錄哪些 log 處理過。
   - `warc_offset` 必須指向最後一跳的 response record，而且該 record 的 `WARC-Target-URI` 等於 `final_url`。isel 會檢查這一點，不符合就當成解析失敗。

**WARC 格式**（WARC/1.1，用 warcio 寫，每個 record 一個 gzip member）

- 第一個 record 是 `warcinfo`，內容包括 crawler 版本、`crawler_id`、UA、robots 政策，以及「HTTP header 是重建的、body 已解壓」的說明。
- 每次 HTTP 交換寫一組 `request` 和 `response`，包括 3xx 的每一跳，用 `WARC-Concurrent-To` 連結。
- `WARC-Target-URI` 是這一跳的 URL。`WARC-Date` 是送出時間。`WARC-IP-Address` 取 `response.ip_address`。payload 和 block 的 sha1 由 warcio 計算。
- HTTP block 的處理：
  - body 用 `response.body`，也就是已經解壓的內容。`HttpCompressionMiddleware` 已經刪掉 `Content-Encoding`。
  - 刪掉 `Transfer-Encoding`，`Content-Length` 改成 `len(body)`。
  - 原本的這兩個值保留成 `X-Orig-Transfer-Encoding`、`X-Orig-Content-Length`。
- 只有 2xx 而且 Content-Type 是 `text/html` 或 `application/xhtml+xml` 的回應存完整 body。其他回應只存 header，並加上 `WARC-Truncated: length`。

```python
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

headers = [(k.decode(), b", ".join(v).decode("latin-1"))
           for k, v in response.headers.items()
           if k.lower() not in (b"transfer-encoding", b"content-length")]
headers.append(("Content-Length", str(len(response.body))))
http = StatusAndHeaders(f"{response.status} {reason(response.status)}", headers,
                        protocol="HTTP/1.1")
record = writer.create_warc_record(response.url, "response",
                                   payload=io.BytesIO(response.body), http_headers=http)
offset = out.tell()
writer.write_record(record)
length = out.tell() - offset
```

**fetch log 的欄位**

每行一個 JSON 物件，必須剛好有這 16 個 key。isel 遇到多或少 key 的行會報錯。

| key | 型別 | 說明 |
|---|---|---|
| `batch_id` | int | queue 檔的 `batch_id` |
| `doc_id` | int | queue 檔的 `doc_ids[i]`，原樣回傳 |
| `source_url` | str | queue 檔的 `urls[i]`，原樣回傳，不要 canonicalize |
| `final_url` | str \| null | 最後一跳的 `response.url` |
| `http_status` | int \| null | 最後一跳的 status；沒有回應時是 null |
| `content_type` | str \| null | 最後一跳的 Content-Type |
| `etag` | str \| null | |
| `last_modified` | str \| null | ISO 8601 UTC |
| `dispatched_at` | str | 第一跳送出的時間，ISO 8601 UTC |
| `fetched_at` | str | 最後一跳完成的時間，ISO 8601 UTC |
| `fail_reason` | str \| null | 成功時是 null；失敗時的值見下表 |
| `retry_after_s` | float \| null | 429/503 的 `Retry-After` 秒數 |
| `warc_file` | str \| null | 這組檔案的 WARC 檔名（不含目錄）；沒有寫 response record 時是 null |
| `warc_offset` | int \| null | 最後一跳 response record 在 WARC 中的起點 |
| `warc_length` | int \| null | 這個 record 的長度 |
| `payload_sha1` | str \| null | body 的 sha1 hex |

`fail_reason` 的值：

| 值 | 意義 |
|---|---|
| `robots_disallow` | robots.txt 禁止 |
| `robots_unavailable` | robots.txt 讀不到或回 5xx |
| `robots_crawl_delay` | Crawl-delay 超過 60 秒 |
| `host_backoff` | C4 讓同一 host 的其他請求快速失敗 |
| `gate_error` | 閘門出錯，沒有送出 |
| `timeout`、`dns`、`connection`、`maxsize` | 網路錯誤 |
| `http_error` | 其他沒有被列出的 HTTP 失敗 |

### C7：其他 fetcher

- sitemap_patroller 重新啟用前，`discover` 和 `patrol` 都要改成先呼叫 C2 的閘門。
- `scripts/refresh_split_whitelist.py`、`scripts/check_pause_unblock.py`、`scripts/show_subdomain_counts.py` 會打真實網站，也要走閘門。
- golden parent patrol 會經 Scrapy 重抓，所以自動受 C2 保護。

### C8：結果的保留時間

2026-10-16 改寫。原本要求 5–6 TB 永久空間，現在不需要。

- isel 只永久保存抽出的正文，約 0.4 TB，放在 isel 自己建立的 `/data/isel`（`/data` 是 1777）。
- 實測 crawler 運作時，Host2 `/data` 的 IPC 暫存是 4.4–5.0 TB，只剩約 2 TB（`/data/ipc/logs/prune_crawl_result.log`，2026-09-20 到 10-05）。
- 我們請 crawler 組做兩件事：
  1. `recrawl_result` 的檔案保留 24 小時後刪除，先刪 `.jsonl` 再刪 `.warc.gz`。可以加進現有的 `prune_crawl_result` cron。
  2. 設一個總容量上限。超過時 C10 回 503，C5 也暫停取新檔。
- WARC 尖峰約每天 0.9 TB（前 3 天每天約 21M 頁），24 小時保留約佔 0.9 TB。
- isel 的 planner 另有兩道背壓：`/data/isel` 剩餘低於 800 GB，或 `recrawl_result` 超過 1000 GB 時停止派送。

### C9：車道仲裁（每 hostname 的加權輪替）

**問題**

- 兩條車道大量搶同一批 host（§5.1）。最大的 10 個 recrawl host，在 C1 之後一般爬取的需求會用滿 64–100% 的額度。
- 只靠閘門先到先送時，大約各拿一半。recrawl 拿不到優先。
- 只設 `request.priority` 等於嚴格優先。Scrapy 2.16 每個 host 佇列內是嚴格 priority（`pqueues.py:169-221`）。一般爬取在最大的 host 上會停 14 天以上。

**做法**

- 自訂 `SCHEDULER_PRIORITY_QUEUE`，叫 `LaneAwareRingPriorityQueue`。
- 它以 starter 的 `RingDownloaderAwarePriorityQueue`（`../crawler/crawler/pqueue.py`）為基底。
  - 預設的 pop 每次掃全部 host。starter 實測這佔 reactor 52% 的 CPU。C1 之後每個 process 的 host 數會變多，所以本來就該換。
- 每個 host 的佇列換成 `LanePair`：裡面有 `normal`、`recrawl` 兩個 `ScrapyPriorityQueue`。
- 兩條都有請求時，用 smooth weighted round robin（SWRR，平滑加權輪替）挑一條。recrawl 的權重是份額 `s`，一般車道是 `1 - s`。
- 只有一條有請求時，直接取它。所以一條車道閒著時，另一條可以用滿這個 host。
- `s` 來自 queue 檔的 `recrawl_share`（§4.2），由 isel 依期限算出。crawler 用 env 夾住它，控制權在 crawler 組。

**為什麼放在 scheduler**

- scheduler 用 downloader 的 `get_slot_key` 分 host 佇列（`pqueues.py:267-274,404-409`）。C2 的 `get_slot_key` 回傳 hostname，所以一個 `LanePair` 剛好對應一個 hostname。
- downloader 的 slot 佇列是 FIFO（`popleft`），而且等待中的請求也算進 `CONCURRENT_REQUESTS`。所以要在 scheduler 決定，downloader 每個 host 只放約 1 個請求。
- 同一個 hostname 只屬於一個 domain_id、一個 shard、一個 process（`libs/db/sharding/key.py:22-28`）。isel 的 `crawl_shard` 也來自同一個 shard。所以兩條車道搶同一個 host 時，幾乎都在同一個 process 裡，不需要跨 process 仲裁。
  - 例外是白名單切分變動和跨 host redirect。這些請求由 C2 閘門先到先送，合規不受影響。
- robots.txt 請求走 `engine.download_async`，不經 scheduler（Scrapy 2.16 `downloadermiddlewares/robotstxt.py:100`），所以不受仲裁影響。

**參考實作**

```python
from scrapy.pqueues import ScrapyPriorityQueue, _path_safe

from crawler.pqueue import RingDownloaderAwarePriorityQueue

LANES = ("normal", "recrawl")


class LanePair:
    """One host's two lanes; pops by smooth weighted round robin on the recrawl share."""

    def __init__(self, make, low, high, default):
        self.queues = {lane: make(lane) for lane in LANES}
        self.credit = dict.fromkeys(LANES, 0.0)
        self.low, self.high = low, high
        self.share = min(max(default, low), high)

    def push(self, request):
        lane = "recrawl" if request.meta.get("lane") == "recrawl" else "normal"
        if lane == "recrawl" and "recrawl_share" in request.meta:
            self.share = min(max(float(request.meta["recrawl_share"]), self.low), self.high)
        self.queues[lane].push(request)

    def _pick(self, commit):
        ready = [lane for lane in LANES if len(self.queues[lane])]
        if not ready:
            return None
        if len(ready) == 1:
            if commit:
                self.credit = dict.fromkeys(LANES, 0.0)   # an idle lane banks no credit
            return ready[0]
        credit = {"normal": self.credit["normal"] + 1 - self.share,
                  "recrawl": self.credit["recrawl"] + self.share}
        lane = max(ready, key=credit.__getitem__)         # a tie goes to normal
        if commit:
            credit[lane] -= 1
            self.credit = credit
        return lane

    def pop(self):
        lane = self._pick(commit=True)
        return None if lane is None else self.queues[lane].pop()

    def peek(self):
        lane = self._pick(commit=False)
        return None if lane is None else self.queues[lane].peek()

    def close(self):
        for q in self.queues.values():
            q.close()
        return []                                         # memory only: nothing to resume

    def __len__(self):
        return sum(len(q) for q in self.queues.values())


class LaneAwareRingPriorityQueue(RingDownloaderAwarePriorityQueue):
    def __init__(self, crawler, downstream_queue_cls, key, slot_startprios=None, **kw):
        s = crawler.settings
        if s.get("JOBDIR"):
            raise ValueError("lane queues keep no disk state; unset JOBDIR")
        self._shares = (s.getfloat("RECRAWL_SHARE_MIN"), s.getfloat("RECRAWL_SHARE_MAX"),
                        s.getfloat("RECRAWL_SHARE_DEFAULT"))
        super().__init__(crawler, downstream_queue_cls, key, slot_startprios, **kw)

    def pqfactory(self, slot, startprios=()):
        def make(lane):
            return ScrapyPriorityQueue(self.crawler, self.downstream_queue_cls,
                                       f"{self.key}/{_path_safe(slot)}/{lane}", (),
                                       start_queue_cls=self._start_queue_cls)
        return LanePair(make, *self._shares)
```

**設定**

| 設定 | 預設 | 說明 |
|---|---|---|
| `SCHEDULER_PRIORITY_QUEUE` | `crawler.pqueue.LaneAwareRingPriorityQueue` | |
| `RECRAWL_SHARE_MIN` | 0.25 | env 控制 |
| `RECRAWL_SHARE_MAX` | 0.75 | env 控制。一般車道在被搶的 host 上至少保留 1 − 0.75 = 25% |
| `RECRAWL_SHARE_DEFAULT` | 0.5 | queue 檔沒有 `recrawl_share` 時使用 |

**保證**

- 兩條車道都有請求時，一般車道在每 4 次中至少拿 1 次。share 0.75 時的順序是 r n r r r n r r。
- 一個一般請求最多多等 3 個 recrawl 請求，約 20 秒。
- `RECRAWL_SHARE_MIN = RECRAWL_SHARE_MAX = 0.5` 時，兩條車道輪流，等於公平分享。

**觀測**

- C2 的 dispatch log 加一欄 `lane`。
- stats 加 `lane/<lane>/dispatched`。在 C2 的 `_download` 取得閘門時累加。
- heartbeat 加每條車道在 scheduler 中的請求數。

**驗收（單元測試草稿）**

- share 0.75、兩條車道各 100 個請求：前 8 次 pop 是 6 個 recrawl、2 個 normal；normal 之間最多隔 3 次。
- 一條車道空著時，另一條連續取完，credit 不累積。
- share 0.5 時兩條車道輪流。
- `recrawl_share` 超出 env 範圍時被夾住。
- 設了 `JOBDIR` 時啟動失敗。

### C10：`recrawl-batches` API（和 PR #84 共用入口）

**目的**

- 讓 recrawl 和 PR #84 的 `crawl-requests` 走同一個入口：同一個 gateway、同一個 API 服務、同一套 URL 驗證。
- crawler 組擁有 `/data/ipc` 底下的所有寫入。isel 不需要任何 `/data/ipc` 的寫入權限，也不需要 root 一次性建立群組目錄。

**為什麼另開一種資源，不在 `crawl-requests` 加參數**

| | `crawl-requests`（PR #84） | `recrawl-batches`（C10） |
|---|---|---|
| 語意 | 排進 crawlerdb 的待爬清單 | 可追蹤的抓取工作：一個 hostname、最多 100 個 URL |
| 寫入 | crawlerdb（`should_crawl`、`domain_state`）和 ingestor IPC | 只寫 `recrawl_queue/crawler_NN/domain_{host_id}.json` |
| 結果 | 進 `crawl_result`，8 小時後刪除 | C6 的 WARC 和 fetch log，帶回 `batch_id`、`doc_id` |
| 追蹤 | 沒有 | `GET` 查排隊狀態，結果逐筆對帳 |

**路由**：`/scheduler_ingest/v1/recrawl-batches`。gateway 已經轉送 `/scheduler_ingest/*`，不需要改 `default.conf`。

**認證**：這兩條路由要 `Authorization: Bearer <token>`。Host2 是多人共用的主機，所有使用者都連得到 `127.0.0.1:4000`。

**`POST`**

```json
{"batch_id": 1234, "crawler_id": 7, "host_id": 7881729332391558645, "recrawl_share": 0.25,
 "docs": [{"doc_id": -6440635563249392445, "url": "https://a.example/1"},
          {"doc_id": 7107690653915941791, "url": "https://a.example/2"}]}
```

| 欄位 | 規則 |
|---|---|
| `crawler_id` | 0–15。isel 用 crawlerdb export 的 `shard // 16` 算出，和 offerer 的路由同一個來源。API 不自己算路由，所以不必讀 crawlerdb |
| `host_id` | isel 算好，API 只拿來當檔名。這樣兩邊不必各自實作 MD5 的位元組順序 |
| `docs` | 1–100 筆，所有 `urlsplit(url).hostname` 必須相同 |
| URL | 原樣使用，不正規化，也不比對正規化後的結果。只檢查：http/https、有 hostname、沒有 userinfo、≤ 2,500 字元、是公開位址（IP literal 要 `is_global`；擋 `localhost` 和單一標籤的 hostname）。不合格就列進 `rejected` |

**為什麼不用 `validate_url` 正規化**

- fetch log 要求 `doc_id == md5(source_url)`，API 改寫 URL 就對不上。isel 送來的已經是 canonical URL。
- PR #84 的 `validate_url` 會回傳正規化後的 URL，`crawl-requests` 也直接用它。
- `validators.url` 會擋掉 w3lib 視為 canonical 的 `|`、`[ ]`。golden 的 138,446 個 canonical URL 中有 9 個會被擋。
- `scheduler_ingest` 釘住 `w3lib==2.3.1`，isel 用 2.5.0。2.5.0 多做了 dot-segment 解析等規則，比對正規化結果會受版本影響。
- 建議：把上面的檢查從 `validate_url` 拆成 `check_url()`，`crawl-requests` 和 C10 都呼叫它；`crawl-requests` 之後再正規化。

伺服器的處理順序：

1. 驗證 token 和 body。任何一筆 URL 不合格就整批拒絕，和 `crawl-requests` 一樣全有或全無。body 本身不合格（缺欄位、`docs` 不是 1–100 筆、hostname 不只一個、`crawler_id` 不在 0–15）時，回 400 並讓 `rejected` 為空。
2. 檢查 16 個目錄中是否已經有 `domain_{host_id}.json`。有就回 409。
3. 檢查目標目錄的檔數是否已達 `max_files_per_dir`（建議 384）。達到就回 429。
4. 檢查磁碟和 `recrawl_result` 的容量上限。超過就回 503。
5. 寫入 §4.2 格式的 queue 檔：先寫一個名稱不符合 `^domain_\d+\.json$` 的 tmp 檔，緊接著 `os.link` 到正式檔名，再刪掉 tmp。
   - `os.link` 遇到同名檔會失敗，所以 409 的判斷是原子的。PR #84 已經有 4 個 Uvicorn worker；同一個 host 一定落在同一個目錄，兩個 worker 搶同一個 host 時會撞到同一個 link 目標，所以多 worker 下仍然安全。
   - tmp 檔名要含 uuid 或 pid。thread id 在不同 process 之間會重複。
   - tmp 在 link 前一刻才寫，queue consumer 依 mtime 排序時順序正確。

| 回應 | 意義 | isel 的處理 |
|---|---|---|
| 201 `{"file": "crawler_07/domain_….json"}` | 已排隊 | 記為已送出（isel 接受任何 2xx） |
| 400 `{"error": "…", "rejected": [{"doc_id": …, "reason": …}]}` | 沒有寫檔 | `rejected` 中的 doc 設成 `gone`，其餘下一輪重送 |
| 400 `{"error": "…", "rejected": []}` | body 不合格，沒有寫檔 | 停止送出並暫停 5 分鐘；這是合約不一致，要人處理 |
| 401 / 403 | token 錯誤 | 停止送出並暫停 5 分鐘 |
| 409 | 這個 host 已經有一批在排隊 | 下一輪再試 |
| 429 | 目錄已滿，附 `Retry-After` | 同一個 process 的 batch 這一輪不再送 |
| 503 | 磁碟或容量上限 | 停止送出並暫停 5 分鐘 |
| 其他 5xx、timeout | 不確定有沒有寫檔 | 保持認領，靠 GET 和逾時對帳；這一輪停止送出 |

- 400 一律帶 `error` 和 `rejected` 兩個 key。`error` 和 PR #84 現有的錯誤格式一樣；`rejected` 讓 isel 分得出「部分 URL 不合格」和「整個請求不合格」。
- isel 依 GET 的結果算出每個目錄的空位，只送放得下的 batch，所以正常情況不會收到 429。

**`GET`**：回傳 `{"queued": [host_id, ...]}`，是 16 個目錄中所有還在排隊的 `host_id`。isel 每輪呼叫一次，用來判斷 crawler 已經取走哪些檔。也可以另附每個目錄的檔數。

**不需要的東西**：API 不碰 crawlerdb、不算路由、不算雜湊、不處理結果。結果仍然照 C6 走檔案。

**驗收**：`index-selection/tests/recrawl_fakes.py` 的 `ApiStub` 是這份合約的參考實作，isel 的整合測試和 e2e 測試都對它跑。

## 4. 介面契約

### 4.1 目錄和權限

| 目錄 | 寫入者 | 讀取者 | 權限 |
|---|---|---|---|
| `/data/ipc/recrawl_queue/crawler_NN/` | C10 的 API（`scheduler_ingest` 容器，root） | crawler | root 預設即可；isel 不讀不寫 |
| `/data/ipc/recrawl_result/crawler_NN/` | crawler（root） | isel archive（uid 1014），只讀 | 目錄 0755、檔案 0644，要明確 chmod（C6 規則 6） |
| `/data/isel/{text,log}/` | isel archive | 下游（索引） | isel 自己建立 |

- 不再需要 root 一次性建立群組目錄。
- recrawl 目錄放在 `crawl_result/` 之外。router 遇到 `crawl_result/crawler_NN/` 下的非日期子目錄會 crash-loop（`libs/ipc/folder_reader.py:79-81`）。

### 4.2 queue 檔

路徑：`/data/ipc/recrawl_queue/crawler_{NN}/domain_{host_id:06d}.json`。

```json
{"generated_at": "2026-10-20T12:00:00+00:00", "domain_id": 7881729332391558645,
 "batch_id": 1234, "urls": ["https://a.example/1", "https://a.example/2"],
 "doc_ids": [-6440635563249392445, 7107690653915941791], "recrawl_share": 0.25}
```

| 欄位 | 說明 |
|---|---|
| 檔名格式 | 和 offerer 相同，`QUEUE_FILE_RE = ^domain_(\d+)\.json$` 可以直接用 |
| `domain_id` | 其實是 `host_id`：hostname 的 MD5 取低 63 位元。一個檔只含一個 hostname 的 URL |
| `urls` | canonical URL，依 rank 排序，最多 100 個 |
| `doc_ids` | 和 `urls` 一一對應，原樣寫回 fetch log |
| `batch_id` | 原樣寫回 fetch log |
| `recrawl_share` | 0.25–0.75。這個 host 被兩條車道搶時，recrawl 拿的份額，C9 使用。算法見 §5.3 |
| 寫入方式 | C10 的 API 依 POST body 寫入，用 tmp 加 `os.link` |
| 同一 host 的檔數 | 16 個目錄合計最多一個。API 用 409 保證；crawler 取走之後，isel 才會送下一批 |
| 路由 | NN = POST body 的 `crawler_id` = `crawl_shard // 16`。`crawl_shard` 取自 crawlerdb export。路由只影響工作落在哪個 process，合規由 C2 保證 |

### 4.3 fetch log 和 WARC

見 C6。

## 5. 容量和設定值

| 項目 | 數值 | 依據 |
|---|---|---|
| 每 host 週期 | max(5 s, latency)，平均約 6.7–8 s | 2026-10-04 的 latency 中位數 6.6 s |
| recrawl 水位 | 每 process 128，共 2,048 | §5.2：前幾天的瓶頸是在途 host 數 |
| C10 每目錄的檔案上限（`max_files_per_dir`） | 384 | 水位的 3 倍；超過時 API 回 429 |
| 14 天可抓 | 約 108–113M 頁 | §5.2 的模擬上限是 112.8M；扣掉 robots.txt 請求和約 10% 失敗後更少 |
| 最大的 host | www.espn.com 有 656,637 個 URL，0.2 qps 下要 38 天 | `index-selection/docs/RECRAWL_DESIGN.md` §3 |

C1 對一般車道的影響：每 host 從 16 個並發降到 1 個。維持現在的 345 req/s 約需要 2,300 個 host 同時在途。目前 16 個 process 合計 2,304 個 domain，所以可能要調高 `IPC_DOMAIN_LOW_WATERMARK`。實際影響要上線後量測。

### 5.1 兩條車道搶同一批 host

資料來源是 Loki 2026-10-03、10-04 全天的 `request.start`（每天約 34.5M 次）。`domain` 欄位是完整 hostname。

| 項目 | 10-03 | 10-04 |
|---|---|---|
| 一般爬取的請求打在 recrawl 清單內 host 的比例 | 93.8% | 93.8% |
| 打在 10k URL 以上 recrawl host 的比例 | 23.4% | 23.4% |
| 這 2,001 個大 host 中，一般爬取用掉 ≥ 50% 額度的 host 數 | 517 | 513 |
| 一般爬取的前 100 名 host 中，屬於 recrawl 大 host 的數量 | 42 | 35 |

最大的 10 個 recrawl host（espn、youtube、en.wiktionary、ameblo、foxsports 等）每天被一般爬取打 8k–16.5k 次。這等於額度的 48–95%。

### 5.2 政策比較（模擬）

- 輸入：
  - recrawl 每 host 的 URL 數，取自 run `20261011T202851094417Z`。
  - 一般爬取每 host 每天的請求數，取自 Loki 2026-10-04。
- 假設：
  - 每 host 每天上限 C = 86,400 / 6.7 s。C1 之後一般爬取的需求取 min(實測, C)，基準是每天 32.96M 次。
  - recrawl 車道的容量是 K 個「host·天」，每個 host 同時只有一個檔。
  - planner 的順序設成隨機。
  - 仲裁是 work-conserving。
- 這個模型不計失敗和 robots.txt，所以只用來比較政策之間的相對差異。

| 政策 | K | recrawl d3 | d7 | d14 | 一般爬取尖峰損失 | 平均 | 14 天總損失 |
|---|---|---|---|---|---|---|---|
| 先到先送（約 1:1） | 1024 | 33.2M | 74.7M | 103.4M | 6.7% | 5.1% | 23.4M |
| 固定 3:1 | 1024 | 36.4M | 83.7M | 109.6M | 9.6% | 6.7% | 31.1M |
| 嚴格優先 | 1024 | 39.6M | 90.1M | 114.3M | 12.4% | 8.2% | 38.0M |
| 先到先送（約 1:1） | 2048 | 64.9M | 90.7M | 107.1M | 10.0% | 5.5% | 25.5M |
| 固定 3:1 | 2048 | 69.0M | 96.4M | 113.0M | 14.0% | 7.2% | 33.2M |
| **配速，份額 0.25–0.75（採用）** | **2048** | **61.6M** | **90.7M** | **112.8M** | **8.5%** | **6.5%** | **29.9M** |
| 配速，份額 0.10–0.75 | 2048 | 57.3M | 88.9M | 112.6M | 7.3% | 6.3% | 29.3M |

結論：

1. **在被搶的 host 上，recrawl 每多抓一頁，一般爬取就少抓一頁。** 總損失主要由 recrawl 在這些 host 上抓多少頁決定。政策能改的是「損失集中在哪幾天」，以及「抓不完的大 host 能抓到多少」。
2. **固定比例會把損失集中在前幾天。** 配速依每個 host 的期限只拿需要的份額。14 天的頁數和固定 3:1 幾乎一樣（112.8M 對 113.0M），尖峰損失從 14.0% 降到 8.5%。
3. **水位是最大的槓桿。** K 從 1024 提高到 2048 後，第 3 天的進度幾乎加倍。
4. **嚴格優先不採用。** 最大的 10 個 host 上一般爬取的需求是滿的，嚴格優先會讓一般爬取在這些 host 上停 14 天以上。
5. **份額下限選 0.25，不選 0.10。** 一個 100 URL 的檔在一般 host 上要 100 × 6.7 / 0.25 ≈ 45 分鐘；下限 0.10 時約 1.9 小時。isel 的逾時是 8 小時，涵蓋 Crawl-delay 60 秒的最慢情況。

### 5.3 份額的算法（isel 端，已實作）

isel 每次寫 queue 檔時，對這個 host 計算：

```
backlog  = 這個 host 現在到期的 doc 數
since    = 這些 doc 最早的 due_at（第一期是 recrawl-sync 的時間）
left     = max(since + pace_days - now, plan_interval_s)
share    = clamp(backlog × pace_cycle_s / left, share_min, share_max)
```

- 設定在 `index-selection/config/isel.toml` 的 `[recrawl]`：`pace_days = 14`、`pace_cycle_s = 6.7`、`share_min = 0.25`、`share_max = 0.75`。
- 意義：每個 host 只拿「在期限內抓完它」需要的份額。小 host 拿 0.25；抓不完的大 host 和落後的 host 拿 0.75。
- 一條車道閒著時，另一條仍可用滿這個 host，所以低份額不會浪費額度。

## 6. 上線順序

| 步驟 | 內容 | 前提 |
|---|---|---|
| 1 | C1–C4 上線，只有一般車道，跑 24 小時 | crawler 組同意；crawler 復工 |
| 2 | 稽核：dispatch log 跑 `verify_politeness.py`；Loki 確認每 host 每 10 分鐘 ≤ 120 次 | 步驟 1 |
| 3 | C10 在 PR #84 的 API 服務上線；C5、C6、C9 上線。recrawl 水位先設 8，`RECRAWL_SHARE_MIN = RECRAWL_SHARE_MAX = 0.5`，也就是公平分享 | 步驟 2 通過；PR #84 合併 |
| 4 | isel 啟動 planner 和 archive，試抓 1–5M 頁，量測 WARC 大小、兩條車道的吞吐量和一般車道的損失 | 步驟 3 |
| 5 | 放寬到 0.25 / 0.75，再量 24 小時。一般車道的損失超出預期時，退回 0.5 / 0.5 | 步驟 4 |
| 6 | 依 C8 的實測調整保留時間和容量上限，水位調到 128，全量 | 步驟 5 |

每一步都可以回退：

- C5 的水位設成 0，recrawl 就停止。
- `RECRAWL_SHARE_MIN = RECRAWL_SHARE_MAX = 0.5`，就退回公平分享。
- isel 停掉 `recrawl-plan`，就不會有新檔案。
- C10 回 503，或撤銷 token，也能立刻停止新的 batch。

## 7. 驗收

| 項目 | 標準 |
|---|---|
| 每 hostname 最小間隔 | dispatch log 中，所有 process、所有車道合計 ≥ 5.0 秒 |
| 閘門錯誤 | `gate_error` 為 0 |
| Loki | `count_over_time({container_name="crawler", event="request.start"} \| json domain [10m]) > 120` 的 host 數為 0 |
| robots.txt | robots 回 5xx 的 host，沒有後續請求 |
| recrawl 結果 | 每個 queue 檔的每個 URL 在 fetch log 都有一行；WARC 能用 `warcio index` 讀完 |
| 車道仲裁 | 兩條車道都有請求的 host 上，dispatch log 的 recrawl 比例接近該 host 的 `recrawl_share`，誤差 ≤ 5 個百分點 |
| 一般車道 | 和步驟 1 的基準比，一般車道每天的請求數下降不超過 §5.2 的預估（尖峰 8.5%） |
| 一般車道不遺失 | `host_backoff` 只出現在 recrawl 車道的 fetch log；一般車道沒有因 C4 被丟棄的請求 |
| C10 | 同一 host 在 16 個目錄中最多一個檔；不合格 URL 回 400 並列出 `rejected`；沒有 token 回 401；API 不寫 crawlerdb |
| 結果保留 | `recrawl_result` 中沒有超過 24 小時的檔；不存在沒有 WARC 的 `.jsonl` |

## 8. 待確認的問題

1. C1 會讓一般車道的吞吐量下降多少？是否要同時調高 `IPC_DOMAIN_LOW_WATERMARK`？
2. 閘門用 SQLite 在 tmpfs 上，crawler 組能接受嗎？另一個選項是改用 Redis，但那會多一個常駐服務。
3. UA 要不要改成可識別的字串（C3）？
4. C8 的 24 小時保留和容量上限，要加進現有的 `prune_crawl_result` cron，還是由 crawler 自己刪？
5. crawler 預計何時復工？它從 2026-10-06 起就停止運作，2026-10-16 仍是 `Exited (137)`。
6. C9 讓一般車道在被搶的 host 上至少保留 25%。這個下限可以接受嗎？還是要更高？
7. `IPC_DOMAIN_LOW_WATERMARK` 能不能改成 env 可覆寫，讓被 C9 拖慢的名額能補回來？
8. recrawl 水位 128 時，`CONCURRENT_REQUESTS = 512` 是否足夠？需要在試抓時看 `downloader.active`。
9. C10 要跟 PR #84 一起做，還是 PR #84 合併後另開 PR？token 由誰產生和保管？

## 附錄：來源

| 來源 | 日期 | 信心 |
|---|---|---|
| WebCrawler `2e4f87c` 原始碼 | 2026-06-01 | HIGH |
| Host2 `webcrawler-crawler` image 中的 Scrapy 2.16.0 原始碼 | 2026-10-13 查詢 | HIGH |
| Host2 Loki `request.start`，2026-10-04 11:00–11:10Z | 2026-10-12 查詢 | HIGH |
| Host2 Loki `request.start`，2026-10-03、10-04 全天，每個 `worker_id` 分開查詢 | 2026-10-13 查詢 | HIGH |
| `../crawler/crawler/pqueue.py`（starter 的 `RingDownloaderAwarePriorityQueue` 和 CPU profile） | 2026-09 | HIGH |
| Shreedhar & Varghese, "Efficient Fair Queuing Using Deficit Round Robin", SIGCOMM 1995，經 [Cambridge 講義](https://www.cl.cam.ac.uk/teaching/2002/DigiComm2/sched.pdf) 轉述 | 1995 / 2002 | MED |
| [Heritrix: Frontier queue budgets](https://github.com/internetarchive/heritrix3/wiki/Frontier-queue-budgets)、[Politeness parameters](https://github.com/internetarchive/heritrix3/wiki/Politeness-parameters) | 未標日期 | MED |
| §5.2 的模擬：日粒度、隨機 planner 順序、固定 6.7 s 週期、不計失敗和 robots.txt | 2026-10-13 | LOW–MED，只比較相對差異 |
| `../crawler/`（starter）：`downloader.py`、`dispatch.py`、`middlewares/robots.py`、`ops/verify_politeness.py` | 2026-09 | HIGH |
| Scrapy 文件，經 ctx7 `/scrapy/scrapy`：「download_slot 在 redirect 時保留」、`DOWNLOAD_SLOTS` | 2026-10 查詢 | HIGH |
| RFC 9309（Robots Exclusion Protocol） | 2022-09 | HIGH |
| [warcio 文件](https://warcio.readthedocs.io/en/latest/) | 1.8.1 | MED |
| ISO 28500:2017（WARC 1.1） | 2017 | HIGH |
| WebCrawler PR #84（`upstream/pr-84`，head `d158461`）：API gateway 和 `crawl-requests` | 2026-10-15 | HIGH |
| Host2 `/data/ipc/logs/prune_crawl_result.log`：crawler 運作時 `/data` 每小時的用量 | 2026-10-16 查詢 | HIGH |
