# 提案：正式 crawler 的 politeness 修正與 recrawl 車道

- 對象：WebCrawler（crawler 組）
- 提案人：index selection 組
- 日期：2026-10-13
- 狀態：草稿，待 crawler 組討論

本文所有路徑都相對於 `WebCrawler/`，版本是 `2e4f87c`，和 Host2 `/opt/WebCrawler` 一致。Scrapy 原始碼取自 Host2 的 `webcrawler-crawler` image，版本 2.16.0。

## 0. 摘要

我們請 crawler 組做兩類改動：

1. **politeness 修正（C1–C4、C7）。**
   - 不管有沒有 recrawl，正式 crawler 現在都不保證每個 hostname 0.2 qps。
   - 這是課程的硬性規定（`../crawler/starter-project.md:8-10`）。
   - 修正的核心是一個跨 process 共用的 host 閘門。每次送出請求前，閘門確認這個 hostname 距離上一次請求至少 5 秒。
2. **recrawl 車道（C5、C6）。**
   - index selection 選出的 128M 個 URL，需要重抓原始 HTML，並以 WARC 永久保存。
   - recrawl 只是正式 crawler 的第二個輸入目錄。它走同一個閘門，所以兩條車道加總後仍遵守 0.2 qps。
3. **車道仲裁（C9）。**
   - 兩條車道大量搶同一批 host：一般爬取 93.8% 的請求打在 recrawl 清單內的 host（§5.1）。
   - 同一個 host 的額度用加權輪替分配。份額由 isel 依每個 host 的期限算出，寫在 queue 檔裡。一般車道至少保留 25%。
   - 模擬結果：recrawl 14 天約 112.8M 頁；一般爬取的尖峰損失 8.5%，比固定 3:1 的 14.0% 低。

isel 端的程式已經完成並測試（`index-selection/src/isel/recrawl.py`）。它負責三件事：決定抓哪些 URL、寫 queue 檔、讀回結果。crawler 端只需要照第 4 節的介面讀檔和寫檔。

我們也需要約 5–6 TB 的 `/data` 空間（C8）。

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
| 目錄 | `/data/ipc/url_queue/crawler_NN/` | `/data/ipc/recrawl_queue/crawler_NN/` |
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
- **檔案仍是讀完就刪（at-most-once）。** isel 會處理遺失的結果：它在檔案被取走 2 小時後，把沒有結果的 URL 重新派送。

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
5. 沒有任何成功回應的時段，也要產生 WARC，裡面只有 warcinfo record。isel 依 log 搬檔，WARC 必須存在。

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

### C8：儲存空間

- 128M 頁的 WARC.gz 估計 5–6 TB。實際每頁大小在 1–5M 頁的試抓後才能確定。
- Host2 `/data` 現在剩 6.6 TB。crawler 復工後，`crawl_result` 保留 8 小時，尖峰約 4 TB。兩者加起來放不下。
- isel 的 planner 在 `/data` 剩餘空間低於 4,500 GB 時停止派送（`[recrawl] min_free_gb`），以保護 crawler 的 IPC。
- 請 crawler 組選一個做法：
  1. 把 `crawl_result` 的保留時間從 8 小時降到約 2 小時，尖峰約 1 TB；
  2. 另外加一顆磁碟給 WARC。

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

## 4. 介面契約

### 4.1 目錄和權限

| 目錄 | 寫入者 | 讀取者 | 權限 |
|---|---|---|---|
| `/data/ipc/recrawl_queue/crawler_NN/` | isel planner（uid 1014, gid 1015） | crawler | `root:1015`，mode 2775 |
| `/data/ipc/recrawl_result/crawler_NN/` | crawler（root） | isel archive | `root:1015`，mode 2775；crawler 的 umask 要讓檔案可讀 |
| `/data/isel_warc/YYYYMMDD/crawler_NN/` | isel archive | 下游 | isel 自己建立 |

一次性的設定，需要 root：

```bash
for d in recrawl_queue recrawl_result; do
  for n in $(seq -w 0 15); do install -d -o root -g 1015 -m 2775 /data/ipc/$d/crawler_$n; done
done
```

- recrawl 目錄放在 `crawl_result/` 之外。router 遇到 `crawl_result/crawler_NN/` 下的非日期子目錄會 crash-loop（`libs/ipc/folder_reader.py:79-81`）。
- isel 一次掛載整個 `/data`，所以搬 WARC 用 rename，不用複製。

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
| 寫入方式 | isel 先寫 `.tmp`，資料庫 commit 後才 rename |
| 同一 host 的檔數 | 磁碟上最多一個。crawler 取走之後，isel 才會寫下一個 |
| 路由 | NN = `crawl_shard // 16`。`crawl_shard` 取自 crawlerdb export。路由只影響工作落在哪個 process，合規由 C2 保證 |

### 4.3 fetch log 和 WARC

見 C6。

## 5. 容量和設定值

| 項目 | 數值 | 依據 |
|---|---|---|
| 每 host 週期 | max(5 s, latency)，平均約 6.7–8 s | 2026-10-04 的 latency 中位數 6.6 s |
| recrawl 水位 | 每 process 128，共 2,048 | §5.2：前幾天的瓶頸是在途 host 數 |
| isel 每目錄的檔案上限 | 384 | 水位的 3 倍 |
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
5. **份額下限選 0.25，不選 0.10。** 一個 100 URL 的檔在最差情況下要 100 × 6.7 / 0.25 ≈ 45 分鐘，仍在 isel 的 2 小時逾時內。下限 0.10 時約 1.9 小時，會重複派送。

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
| 3 | 建立 4.1 節的目錄；C5、C6、C9 上線。recrawl 水位先設 8，`RECRAWL_SHARE_MIN = RECRAWL_SHARE_MAX = 0.5`，也就是公平分享 | 步驟 2 通過 |
| 4 | isel 啟動 planner 和 archive，試抓 1–5M 頁，量測 WARC 大小、兩條車道的吞吐量和一般車道的損失 | 步驟 3 |
| 5 | 放寬到 0.25 / 0.75，再量 24 小時。一般車道的損失超出預期時，退回 0.5 / 0.5 | 步驟 4 |
| 6 | 依 C8 的結論調整儲存，水位調到 128，全量 | 步驟 5 |

每一步都可以回退：

- C5 的水位設成 0，recrawl 就停止。
- `RECRAWL_SHARE_MIN = RECRAWL_SHARE_MAX = 0.5`，就退回公平分享。
- isel 停掉 `recrawl-plan`，就不會有新檔案。

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

## 8. 待確認的問題

1. C1 會讓一般車道的吞吐量下降多少？是否要同時調高 `IPC_DOMAIN_LOW_WATERMARK`？
2. 閘門用 SQLite 在 tmpfs 上，crawler 組能接受嗎？另一個選項是改用 Redis，但那會多一個常駐服務。
3. UA 要不要改成可識別的字串（C3）？
4. C8 選哪個做法？
5. crawler 預計何時復工？它從 2026-10-06 起就停止運作。
6. C9 讓一般車道在被搶的 host 上至少保留 25%。這個下限可以接受嗎？還是要更高？
7. `IPC_DOMAIN_LOW_WATERMARK` 能不能改成 env 可覆寫，讓被 C9 拖慢的名額能補回來？
8. recrawl 水位 128 時，`CONCURRENT_REQUESTS = 512` 是否足夠？需要在試抓時看 `downloader.active`。

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
