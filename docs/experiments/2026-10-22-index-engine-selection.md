# 全文索引引擎選型：Typesense、Quickwit 與其他候選

- 日期：2026-10-22
- 範圍：
  - 主機資源，用唯讀 SSH 量測 ntu-sa1、ntu-sa2。
  - 本機實測：同一份語料分別建 Quickwit 和 OpenSearch 索引。
  - 9 個候選引擎的公開資料。
- 相關文件：`RECRAWL_DESIGN.md` §10（inverted index 規劃）、`../../INDEX_SELECTION_SPEC.md` §3.1（舊的選型表）。
- 信心等級：
  - HIGH：多個來源，或直接引用官方文件。
  - MED：單一權威來源，或小樣本實測。
  - LOW：廠商宣稱、社群討論或推論。
- 狀態：**建議，尚未定案。** 定案前要先做 §8 的 5 萬頁實測。
- 更新：2026-10-23 已定案為 OpenSearch，見 §10。

---

## 1. 結論

1. **Typesense 不可行。** 它把 inverted index 全部放在 RAM，官方的估算是「可搜尋欄位大小的 2–3 倍」。128M 份網頁的可搜尋文字約 550 GB，所以需要 1.1–1.65 TB 的 RAM。兩台主機各只有 755 GB，Host2 目前可用的只有 268 GB。
2. **建議推翻 Quickwit，改用 OpenSearch，但有一個前提。** 前提是 5 萬頁實測後，推估的索引大小仍在 Host2 的磁碟預算內，約 0.5 TB。
   - **推翻的理由：** 評分指標是 Recall@10，而 golden 關鍵字來自 10 個地區。OpenSearch 的 ICU 斷詞對中、日、印地文都正確，Quickwit 的官方 image 做不到（§4.2）。
   - **Quickwit 的優勢：** 索引約小 45%，記憶體用量約是 OpenSearch 的 1/25。
3. **決定性的限制是 Host2 的磁碟，不是 CPU 或 RAM。** recrawl 的正文 Parquet（約 0.4 TB）和 API 的 HTML 暫存，會和索引搶同一顆 `/data`。
4. **舊的選型理由要更正兩處。**
   - `INDEX_SELECTION_SPEC.md` 估 Typesense 要 100–200 GB RAM，這是從書籍的 metadata 外推的。改用網頁正文重新估算，約多 5–10 倍。
   - 那份表沒有比較斷詞品質。

---

## 2. 需求

| # | 需求 | 來源 |
|---|---|---|
| R1 | 128M 份網頁的全文 BM25：title、description、body | `RECRAWL_DESIGN.md` §10 |
| R2 | 多語言。golden 關鍵字來自 10 個地區，包含中、日、韓、印地文 | `RECRAWL_DESIGN.md` §10 |
| R3 | 不能影響 crawler 和 crawlerdb。主機是共用的 | 課程環境 |
| R4 | 每兩週換一次清單。要能刪除被移出的文件，或整批切換 | Metric 的量測週期（1 號、16 號） |
| R5 | 能用 URL 或 `doc_id` 精確查詢，以便和 golden 比對 | `RECRAWL_DESIGN.md` §10 |
| R6 | 查詢量低。離線量 Recall@10 時，每期約 1,000 個關鍵字 | WSE `measure.py` 的 `--keywordNums` |
| R7 | 學生單機維運得起來，可以用 Docker 部署 | 課程環境 |

---

## 3. 主機資源

2026-10-22 07:01 UTC 唯讀量測。指令只有 `free`、`df`、`lsblk`、`/proc/pressure/*` 和 `docker stats --no-stream`。

| 項目 | Host1（ntu-sa1） | Host2（ntu-sa2） |
|---|---|---|
| CPU | 144 核（Xeon Platinum 8352V ×2，2 個 NUMA 節點） | 144 核，load 約 31 |
| RAM 總量 | 755 GB | 755 GB |
| RAM 可用 | 484 GB，但多半是 crawlerdb 的 page cache（buff/cache 569 GB）。`postgres` 本身 183 GB，`yugabyte` 93 GB | **268 GB**。`scheduler_ingest` 256 GB，`yugabyte` 155 GB，`crawler` 20 GB |
| swap | 8 GB 中用了 8 GB | 8 GB 中用了 7 GB |
| `/data` | 7.0 TB，剩 590 GB（92%） | 7.0 TB，剩 1.9 TB。每小時清除前只剩約 1.1 TB |
| 根目錄 | 877 GB，剩 601 GB。selectdb `:5445` 在這裡 | 877 GB，剩 379 GB |
| 磁碟 | 2 × 3.5 TB NVMe（`vg_data`），加上 894 GB 開機碟 | 同左 |
| IO 壓力（avg10） | 45%。量測時正好在每小時 :00–:07 的 metric 掃描期間，平時約 15% | 約 0% |

Host2 `/data` 會週期性變化，原因是 `prune_crawl_result` 每小時刪除超過 8 小時的 crawl_result。10-21 的 prune log 記錄：清除前剩 1.1 TB，清除後剩 1.7–1.8 TB。

從資源推出四個限制：

- **索引不能放 Host1。**
  - Host1 是 crawlerdb 的主機，2026-10-05 就是因為 `/data` 寫滿而停機了 16.7 小時。
  - 它的 page cache 是給 crawlerdb 每小時全表掃描用的。
- **Host2 的 RAM 有餘裕，但不寬裕。** 可用 268 GB，但 swap 已經用了 7 GB，代表曾經出現過記憶體壓力。索引服務最好控制在 32 GB 以內。
- **Host2 的磁碟預算約 0.5 TB。** 以清除前的 1.1 TB 計算，先扣掉正文 Parquet 0.4 TB，再扣掉 PR #84 的 HTML 暫存（量取決於 TTL 和送出的速度），剩下約 0.5 TB 以內。
- **CPU 不是限制。** 建索引可以分配 16–32 核。

---

## 4. 本機實測

### 4.1 方法

- **語料：**
  - 從 Host2 的 `/data/ipc/crawl_result/crawler_{07,12}/20261021/0710/0710.jsonl` 唯讀複製，共 2,647 筆。兩檔的 sha256 都和伺服器一致。
  - 用 `isel.extract.extract` 轉成 `TEXT_SCHEMA` Parquet，得到 2,399 列。排除了 228 筆 fail、20 筆重複；抽取 0 筆失敗。
  - 語言分布：en 1,030、ja 241、zh 226、de 174、fr 144、es 96、hi 35，其他較少。
- **兩邊的索引設定相同：**
  - title、description、body 都建全文索引，並記錄詞的位置。
  - body 不保存原文。
  - url 精確比對，`doc_id` 是 u64。
- **title 自我檢索：**
  - 從有 title、且長度 ≥ 8 的頁面中抽 300 頁，隨機種子 20261021。
  - 用每頁自己的 title 當查詢，在 title、description、body 三個欄位做 OR 查詢，看該頁是否進前 10 名。
  - 這只是代理指標。title 查詢很容易命中，所以它主要用來測斷詞和設定有沒有問題，不能代表真實的 Recall@10。
- **環境：** Mac 上的 Docker。
  - Quickwit 0.8.2：單機 `quickwit run`。
  - OpenSearch 3.9.0：單節點，heap 2 GB，加裝 `analysis-icu`。

### 4.2 結果

| 指標 | Quickwit 0.8.2 | OpenSearch 3.9.0 + ICU |
|---|---|---|
| 斷詞設定 | `chinese_compatible` | `icu_tokenizer` + `icu_folding` |
| 索引大小 | **5.04 MB**（2.1 KB/篇），1 個 split | 8.96 MB（3.7 KB/篇），1 個 segment。用 `best_compression`，並清掉 recovery source |
| 推估 128M 份的索引大小 | 約 0.27 TB | 約 0.48 TB |
| 記憶體（`docker stats`） | **約 100 MB** | 2.6 GB |
| 匯入時間 | 約 1.4 秒 | 約 1.9 秒，含 force merge |
| title 自我檢索，前 10 名 | 296/300 | **299/300** |
| title 自我檢索，排第 1 | 260/300 | **265/300** |
| 沒命中的頁面 | 4 頁，全是天城文（hi 1、mr 3） | 1 頁英文 |
| 用 URL 精確查詢 | 2,261/2,302 | **2,302/2,302** |
| 用 `doc_id` 精確查詢 | 2,302/2,302 | 沒測。`_id` 本身就是 `doc_id` |
| 詞組查詢 `"大谷翔平"` | 3 筆 | 3 筆 |
| 查詢延遲 | 1–2 ms | 0–10 ms |

### 4.3 實測中發現的行為

1. **Quickwit 預設不依相關度排序。** 原生 search API 預設依文件 ID 排序，官方說明是為了降低延遲而關掉 BM25。沒帶 `sort_by=_score` 時，title 自我檢索只有 64/300。
2. **Quickwit 的 `raw` tokenizer 會丟掉超過 255 bytes 的 token。** 樣本中有 41 個 URL（1.7%）超過這個長度，128M 清單裡最長的 URL 有 2,405 bytes。所以要改用 `doc_id = md5_number_lower(canonicalize(url))` 查詢。
3. **官方 Quickwit image 沒有多語言斷詞。**
   - 建索引時指定 `multilang` tokenizer，會回 `unknown tokenizer`。這個功能要自己編譯才有。
   - 用 `analyze` API 試 `multilang` 時，日文 `東京オリンピックの開会式` 整串被當成一個詞。
   - 所以在官方 image 上，中日文只能用 `chinese_compatible` 逐字切開。
4. **`chinese_compatible` 會切開天城文的母音符號。** 這是造成那 4 頁沒命中的原因。
5. **OpenSearch 的初版索引有 22.6 MB。**
   - `_source` 排除 body 時，OpenSearch 會另外保留一份含完整原文的 recovery source，等 retention lease 到期、segment 合併後才會刪掉。
   - 預設的 codec 是 LZ4。
   - 改成 `best_compression`，並把 `soft_deletes.retention_lease.period` 縮短之後，降到 8.96 MB。正式建索引時兩個設定都要加。

### 4.4 外推的誤差

- 樣本只有 2,399 頁。詞典的成本不會隨文件數線性增加，所以大規模時每篇的大小可能會更小。§4.2 的推估可信度：MED 偏 LOW。
- 這批頁面來自正常 crawl，不是我們的清單。正文長度的分布可能和清單不同：這批的 body 長度中位數是 1,536 字元，上限設定是 200,000。
- 這兩點要用 §8 的 5 萬頁實測校正。

---

## 5. 為什麼不用 Typesense

1. **記憶體需求超過主機容量。** 這是決定性的理由，信心等級 HIGH。
   - 官方文件說，可搜尋欄位共 X MB 時，需要 2X–3X MB 的 RAM。只顯示、不搜尋的欄位放在磁碟，不算進 RAM。
   - §4 量到的可搜尋文字約 4.3 KB/篇（2,399 篇未壓縮共 10.3 MB），乘上 128M 約 550 GB。
   - 所以需要約 **1.1–1.65 TB** 的 RAM。這比 Host2 可用的 268 GB 多 4–6 倍，也超過單台主機 755 GB 的總量。
   - RAM 用量超過 `memory-used-max-percentage`（預設 85%）時，Typesense 會拒絕寫入。所以 RAM 不夠時，索引不只是變慢，而是建不起來。
2. **全量重建會讓磁碟膨脹。** issue #2005 記錄：每天 bulk import 40 GB JSONL、2,500 萬份文件時，磁碟會快速膨脹。我們每兩週重建 128M 份，正好碰到這個問題。信心等級 MED，因為只有單一 issue。
3. **用途不同。** Typesense 是為「邊打字邊搜尋」和容許錯字設計的，適合商品目錄這類短欄位。長篇網頁加上 BM25 排序，是 Lucene 和 Tantivy 系列引擎的設計目標。
4. **課程程式碼不構成限制。** WSE 的 `Metric/Measure/TypesenseRankMeasure.py` 看起來像是指定用 Typesense，但它無法運作：
   - `test()` 的內容全部被註解掉，呼叫了也什麼都不做。
   - 就算把註解拿掉，它也只寫進舊的 `Dataset` JSON，不會寫入 metricdb 的 `ranked_num` 和 `ranked_rate`。
   - 不論選哪個引擎，要讓官方 Metric 算 Recall@10，都得另外提 `RankMeasure` 的 PR。
5. **唯一可行的變形是不索引正文。** 只索引 title、description 和 URL 時，每篇約 300 bytes，128M 份約 38 GB，需要 80–115 GB RAM。但 golden 關鍵字只出現在正文時就查不到，Recall@10 一定會下降，所以不採用。

---

## 6. 候選比較

### 6.1 專案狀態

2026-10-22 用 GitHub API 查詢。

| 專案 | 授權 | 最新 release | 最後推送 | stars |
|---|---|---|---|---|
| OpenSearch | Apache-2.0 | 3.9.0（2026-09-29） | 2026-10-21 | 13.9k |
| Quickwit | Apache-2.0 | v0.9.2（2026-10-16） | 2026-10-21 | 11.7k |
| Tantivy | MIT | 0.26.1（2026-05-10） | 2026-10-21 | 16.2k |
| Typesense | GPL-3.0 | v30.2（2026-04-19） | 2026-10-21 | 27.0k |
| Meilisearch | MIT 核心＋商業版（GitHub 標 NOASSERTION） | v1.55.0（2026-10-12） | 2026-10-21 | 59.6k |
| Manticore Search | GPL-3.0 | 29.9.0（2026-09-11） | 2026-10-22 | 12.1k |
| ParadeDB | AGPL-3.0 | v0.26.2（2026-10-16） | 2026-10-22 | 9.4k |
| Vespa | Apache-2.0 | v8.763.13（2026-10-05） | 2026-10-22 | 7.1k |
| Anserini | Apache-2.0 | 沒有 GitHub release | 2026-10-20 | 1.2k |

### 6.2 評估矩陣

✅ 符合、⚠️ 有條件、❌ 不符合。

| 候選 | R1 全文 BM25 | R2 多語言 | R3 資源 | R4 換清單 | R7 維運 | 判定 | 信心 |
|---|---|---|---|---|---|---|---|
| **OpenSearch** | ✅ Lucene BM25，預設就啟用 | ✅ ICU、kuromoji、nori、smartcn，以及內建的 hindi、cjk analyzer | ⚠️ heap ≤ 31 GB，加上 page cache；索引約 0.48 TB | ✅ 可依 `doc_id` 刪除，alias 可原子切換 | ✅ 單節點 Docker | **首選，條件是磁碟夠用** | MED |
| Quickwit | ⚠️ BM25 要 opt-in | ⚠️ CJK 逐字切開、日文不斷詞、天城文會漏 | ✅ 約 100 MB RAM，索引約 0.27 TB | ⚠️ split 不能修改，刪除要跑 delete task；必須有時間戳記欄位 | ✅ 單機 Docker | **備案**：磁碟不夠時改用它 | MED |
| Typesense | ✅ | ✅ 可依 locale 設定 | ❌ 約 1.1–1.65 TB RAM | ⚠️ 磁碟膨脹（#2005） | ✅ | 不可行 | HIGH |
| Meilisearch | ⚠️ 排序規則不是 BM25，是 words、typo、proximity 等規則依序比較 | ✅ charabia | ⚠️ 用記憶體映射的 LMDB；記憶體不夠時大量匯入會 crash | ✅ | ✅ | 不建議：為短欄位的即時搜尋設計 | LOW |
| Manticore | ✅ | ✅ ICU、jieba | ⚠️ 官方宣稱比 ES 省記憶體，沒有獨立數據 | ✅ | ✅ | 第三選擇；證據只有廠商的 benchmark | LOW |
| ParadeDB `pg_search` | ✅ 底層是 Tantivy | ⚠️ 限制和 Quickwit 相同 | ❌ 正文約 0.55 TB 加上索引，放不進 `:5445` 剩下的 601 GB；而且在 crawlerdb 的主機上 | ✅ 用 SQL | ⚠️ | 不可行；公開 benchmark 只做到 1,000 萬列 | LOW |
| Vespa | ✅ | ✅ | ⚠️ attribute 放在記憶體 | ✅ 即時 | ❌ 要維運 config server 和 content node | 太重 | MED |
| Anserini／Pyserini | ✅ Lucene BM25 | ✅ Lucene 語言 analyzer | ✅ | ❌ 要重建 | ✅ | 不能當線上服務；適合當離線量 Recall@10 的對照 | MED |
| Tantivy 函式庫 | ✅ | ⚠️ 限制和 Quickwit 相同 | ✅ | ⚠️ | ❌ 服務層要自己寫 | 不建議 | MED |

Elasticsearch 和 OpenSearch 的核心相同。OpenSearch 是 Apache-2.0，所以只列 OpenSearch。

---

## 7. 選型理由

### 7.1 為什麼 OpenSearch 比 Quickwit 適合

1. **Recall@10 靠斷詞品質。**
   - golden 關鍵字包含中、日、韓、印地文。
   - Quickwit 官方 image 對日文整句不斷詞，CJK 只能逐字切開，查詢會變成一長串單字的詞組比對。
   - ICU 用字典斷詞。§4.2 的天城文差異就是實際的例子。
2. **每兩週換清單。**
   - OpenSearch 可以依 `doc_id` 刪掉被移出清單的文件，或建新索引後用 alias 原子切換。
   - Quickwit 的 split 不能修改，刪除很貴。它是為 log 搜尋設計的，必須有時間戳記欄位，這次只好拿 `fetched_at` 充當。
3. **專案的穩定性。**
   - Quickwit 從 2024 年到 2026-10 都沒有發布新版（issue #5746 有使用者追問）。2026 年 10 月才連續發了 0.9.0 到 0.9.2，其中兩版是安全修補。
   - 2025-01 被 Datadog 收購後，團隊的重心是整合 Datadog。
   - OpenSearch 由 Linux Foundation 管理，每月發布新版。
4. **和 IR 文獻可以對照。** OpenSearch 的 BM25 是 Lucene 的實作，和 Anserini 相同。報告裡的數字可以直接和文獻的 baseline 比較。

### 7.2 Quickwit 仍然勝出的情況

- **磁碟不夠時。** 5 萬頁實測後，如果推估的 OpenSearch 索引大於 Host2 的預算（約 0.5 TB），就選 Quickwit。
- **RAM 被擠壓時。** Host2 的 RAM 再被擠壓，撥不出 16–31 GB 給 heap 時，也選 Quickwit。
- **如果選 Quickwit，要做三件事：**
  1. 升級到 v0.9.2。官方公告 0.4.0 到 0.9.1 都在安全漏洞範圍內，這次本機測試用的 0.8.2 也在其中。
  2. 自己編譯含 `multilang` 的版本，或接受 CJK 逐字切開。
  3. URL 一律改用 `doc_id` 查詢。

---

## 8. 下一步

1. **擴大樣本重做 §4。**
   - 用唯讀 SSH 抓約 35 個 crawl_result jsonl，約 5 萬頁、10 GB。只用 `cat` 加 sha256 比對，不在伺服器上執行任何其他程式。
   - 校正三個數字：每篇的索引大小、建索引的速度（篇/秒/核），以及推估的 128M 總量。
   - OpenSearch 要再試 `zstd` codec，以及「body 不記錄詞位置」（`index_options: freqs`）的版本，量它對大小和詞組查詢的影響。
2. **依實測結果定案。** 決策規則：
   - OpenSearch 推估 ≤ 0.5 TB，而且 Host2 撥得出 16–31 GB heap：選 OpenSearch。
   - 否則：選 Quickwit v0.9.2。
3. **跟 crawler 組確認 Host2 `/data` 的預算。** 這和 PR #84 的 HTML 暫存是同一筆空間，要一起談。
4. **更新文件。** 定案後更新 `RECRAWL_DESIGN.md` §10 的「引擎」一段，並在 `INDEX_SELECTION_SPEC.md` §3.1 註明這份文件取代了它。
5. **量 Recall@10 要等 PR #84 部署。** 這批 crawl_result 頁面只有約 1.1% 在我們的清單上（10-21 抽樣 1,476 個 URL，16 個完全相同），所以現在量不出有意義的 Recall@10。

---

## 9. 衝突的資料

- ⚠️ **Quickwit 的授權。**
  - dbdb.io 標 AGPL v3。
  - Datadog 在 2025-01 的公告說會改成 Apache 2.0。
  - GitHub repo 目前的 LICENSE 是 **Apache-2.0**，以 repo 為準。
- ⚠️ **Typesense 需要多少 RAM。**
  - `INDEX_SELECTION_SPEC.md` 估 100–200 GB，是從「2,800 萬本書用 14 GB」外推的。
  - 本文用網頁正文 4.3 KB/篇，乘上官方的 2–3 倍，得到 1.1–1.65 TB。
  - 以本文為準，因為書的資料只有短欄位，網頁是整篇正文。
- ⚠️ **Tantivy 比 Lucene 快 2 倍。**
  - 這個數字來自 Tantivy 團隊的 search-benchmark-game。
  - ParadeDB 訪談提到 Lucene 之後已經補上大部分差距。
  - R6 的查詢量很低，速度不是決定因素。
- ⚠️ **Manticore 比 Elasticsearch 快多少。** 官方 blog 說 4 倍，官網首頁說 2.83 倍，兩者都是廠商自己的數字。

---

## 附錄 A：實測設定

**Quickwit `index.yaml`**

```yaml
version: 0.8
index_id: isel-text-trial
doc_mapping:
  mode: strict
  field_mappings:
    - {name: doc_id, type: u64, fast: true}
    - {name: url, type: text, tokenizer: raw}
    - {name: final_url, type: text, tokenizer: raw}
    - {name: fetched_at, type: datetime, input_formats: [rfc3339], fast: true, fast_precision: seconds}
    - {name: lang, type: text, tokenizer: raw, fast: true}
    - {name: title, type: text, tokenizer: chinese_compatible, record: position, fieldnorms: true}
    - {name: description, type: text, tokenizer: chinese_compatible, record: position, fieldnorms: true}
    - {name: body, type: text, tokenizer: chinese_compatible, record: position, fieldnorms: true, stored: false}
  timestamp_field: fetched_at
search_settings:
  default_search_fields: [title, description, body]
```

查詢一律帶 `"sort_by": "_score"`。

**OpenSearch 索引設定**

```json
{
  "settings": {
    "number_of_shards": 1, "number_of_replicas": 0,
    "codec": "best_compression",
    "soft_deletes.retention_lease.period": "1s",
    "analysis": {"analyzer": {"icu": {"type": "custom", "tokenizer": "icu_tokenizer",
                                      "filter": ["icu_folding"]}}}
  },
  "mappings": {
    "_source": {"excludes": ["body"]},
    "properties": {
      "doc_id": {"type": "unsigned_long"},
      "url": {"type": "keyword", "ignore_above": 4096},
      "final_url": {"type": "keyword", "ignore_above": 4096},
      "fetched_at": {"type": "date"}, "lang": {"type": "keyword"},
      "title": {"type": "text", "analyzer": "icu"},
      "description": {"type": "text", "analyzer": "icu"},
      "body": {"type": "text", "analyzer": "icu"}
    }
  }
}
```

- 查詢用 `multi_match`，`type: most_fields`，欄位是 title、description、body。
- 匯入後先 force merge 到 1 個 segment，等 retention lease 同步（約 30 秒），再 force merge 一次，最後才量大小。

**語料轉換**

- 每筆 crawl_result 的處理方式：
  - `status == "ok"` 的 `content` 先轉成 UTF-8 bytes，再用 `isel.extract.extract(payload, "utf-8", 200000)` 抽取。
  - `doc_id = md5_number_lower(canonicalize(url))`。
  - 同一個 `doc_id` 只留 `fetched_at` 最新的一筆。
  - 用 `isel.io.write_parquet_atomic` 寫成 `TEXT_SCHEMA`。
- crawl_result 沒有 HTTP 狀態碼，`status == "ok"` 一律記成 200。
- `batch_id` 記成 0，因為這些頁面不是 recrawl job 抓的。

---

## 附錄 B：來源

- [Typesense System Requirements](https://typesense.org/docs/guide/system-requirements.html)：官方，查閱 2026-10-22。HIGH
- [Typesense Cloud: Choosing how much memory you need](https://cloud-help.typesense.org/article/choosing-how-much-memory-you-need)：官方。HIGH
- [Typesense community: RAM estimation](https://threads.typesense.org/t/29453366/hello-i-ve-few-questions-that-how-to-calculate-how-much-memo)：官方人員的回覆。MED
- [Quickwit Querying concepts](https://quickwit.io/docs/overview/concepts/querying)：BM25 要 opt-in。官方，HIGH
- [Quickwit REST API](https://quickwit.io/docs/reference/rest-api)：官方，HIGH
- [Quickwit Elasticsearch compatible API](https://quickwit.io/docs/reference/es_compatible_api)：官方，HIGH
- [Quickwit release v0.9.2](https://github.com/quickwit-oss/quickwit/releases/tag/v0.9.2)：2026-10-16，安全修補。HIGH
- [Quickwit issue #5746「Next release?」](https://github.com/quickwit-oss/quickwit/issues/5746)：2026-10-15 關閉。MED
- [Quickwit joins Datadog](https://quickwit.io/blog/quickwit-joins-datadog)：2025-01。HIGH
- [Datadog acquires Quickwit](https://www.datadoghq.com/blog/datadog-acquires-quickwit/)：2025-01。HIGH
- [dbdb.io: Quickwit](https://dbdb.io/db/quickwit)：第三方，授權資訊和 repo 衝突。LOW
- [AWS OpenSearch: Choosing the number of shards](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp-sharding.html)：官方。HIGH
- [Building Retrieval Systems for the ClueWeb22-B Corpus (arXiv 2402.04357)](https://arxiv.org/html/2402.04357v1)：2024-02，8,700 萬篇的 Lucene 索引約 825 GB。HIGH
- [Hierarchical BM25 (arXiv 2608.00229)](https://arxiv.org/pdf/2608.00229)：2026-07，10 億篇的 BM25 索引超過 400 GB。MED
- [search-benchmark-game](https://github.com/quickwit-oss/search-benchmark-game)：Tantivy 團隊。MED
- [ParadeDB: Tantivy creator interview](https://paradedb.com/blog/tantivy-interview)：MED
- [ParadeDB pg_search introduction](https://paradedb.com/blog/introducing_search)：廠商。LOW
- [Tembo: Benchmarking ParadeDB](https://tembo.io/blog/paradedb-search)：單一作者，1,000 萬列。LOW
- [Manticore: alternative to Elasticsearch](https://manticoresearch.com/blog/manticore-alternative-to-elasticsearch/)：廠商。LOW
- [Meilisearch known limitations](https://meilisearch.com/docs/resources/help/known_limitations)：官方。HIGH
- [Meilisearch indexing best practices](https://meilisearch.com/docs/capabilities/indexing/advanced/indexing_best_practices)：官方。MED
- [Vespa BM25 reference](https://docs.vespa.ai/en/reference/bm25.html)：官方。MED
- [Pyserini (arXiv 2102.10073)](https://arxiv.org/pdf/2102.10073)：MED
- GitHub REST API：各 repo 的 LICENSE、最新 release 和最後推送日期，查詢日 2026-10-22。HIGH
- 主機量測：唯讀 SSH，2026-10-22 07:01 UTC。HIGH
- 本機實測：§4，2026-10-22。MED

---

## 10. 2026-10-23 更新

- 依據：課程 repo 分支 `idxsel/p10-text-index` 的 `IndexSelectionV2/docs/TEXT_INDEX.md`。完整的數字、方法和重現步驟都在那份文件。
- 狀態：**定案為 OpenSearch 3.9.0。** 決策規則在跑實驗之前就寫好，Q、S、M 三個條件都成立。

### 10.1 決策結果

| 條件 | 結果 | 判定 |
|---|---|---|
| Q 品質 | NanoMIRACL 8 種語言，依 batch 20 關鍵字的文字比例加權。OS-ICU 的 nDCG@10 比 Quickwit `chinese_compatible` 高 0.010，95% 區間 [+0.001, +0.017] | 成立，下界 ≥ −0.02 |
| S 大小 | 44,028 頁實測每篇 1,791 bytes，推估 128M 篇 0.23 TB。預算 B 是 0.82 TB | 成立 |
| M 記憶體 | Host2 可用記憶體 231 GiB，16–31 GB 的 heap 放得下 | 成立 |

- 差距主要來自日文（+0.26）。Quickwit 的 `chinese_compatible` 把連續的假名當成一個詞，所以 `オリンピックの` 用 `オリンピック` 查不到。
- 中文是 Quickwit 較好（−0.032）。
- 每種語言各用自己的 analyzer，還能再高 0.031，主要來自英文的詞幹還原。這是下一個可以試的方向，這次沒有做。

### 10.2 和 10-22 版本不同的地方

1. **索引大小的推估減半。** 10-22 用 2,399 頁估出每篇 3.7 KB，128M 篇約 0.48 TB。這次用 44,028 頁量到每篇 1.79 KB，推估 0.23 TB。每個正文 byte 對應的索引 bytes 會隨樣本變大而下降（1/4、1/2、全量依序是 0.479、0.453、0.435），所以小樣本會高估。
2. **預算依公式重算。** 10-22 07:00 到 10-23 06:00 UTC 的 prune log 中，清除前最低剩 1.2 TiB（1.32 TB）。B = 0.9 ×（1.32 − 0.40 正文 − 0.014 HTML 暫存）= 0.82 TB。10-22 寫的「約 0.5 TB」是粗估。
3. **新的限制是重建時的磁碟峰值。** 決策規則只比較單一索引的大小，但重建時新舊索引會同時存在。
   - 不保留前一版時，`icu` 的峰值是 0.46–0.88 TB；保留前一版時是 0.69–1.11 TB。
   - `/data` 和 crawler 共用。在 95% 的 flood stage 之前，清除前的低點只剩 0.52 TB。本機實測確認 flood stage 在單節點也會生效。
   - 所以 Host2 必須關掉 `keep_prev`，而且要先用約 1,000 萬篇量一次峰值。
   - 備案是 body 不記詞位置：索引小 47%，1,948 個 golden 關鍵字的前 10 名和分數完全相同。
4. **Quickwit 備案改為 v0.9.3。** 它在 10-22 發布，又是一次安全修補。官方 image 仍然沒有 `multilang` tokenizer。

### 10.3 主機狀態（2026-10-23，唯讀 SSH）

| 項目 | Host1 | Host2 |
|---|---|---|
| 可用記憶體 | 490 GB（04:56 UTC），多半是 page cache | 231 GiB（06:06 UTC，`free -g`）。04:56 時是 254 GB，10-22 是 268 GB |
| swap | 用滿 | 7/7 GiB 用滿 |
| `/data` 剩餘 | 425 GB（94%），10-22 是 590 GB | 1.99 TB（06:06，剛清除完）。清除前的低點是 1.2 TiB |
| IO 壓力（avg300） | 約 32%，持續 | 約 0% |
| 其他 | | 多了 cassandra、cockroach、citus、pg1_bench 等 DB benchmark 容器 |

### 10.4 實作時發現並修正的問題

1. **force merge 之後要明確 refresh。** shard 超過 30 秒沒有搜尋時，OpenSearch 不做背景 refresh，舊的 segment 會一直佔著磁碟。第一次量到 223 MB，實際的索引只有 78 MB。
2. **同分文件的順序取決於匯入順序。** 同一份資料建兩次，約 10% 關鍵字的前 10 名集合不同。改成依 `doc_id` 打破同分後，1,948 個關鍵字全部一致。Anserini 也是用同樣方法修正（Lin and Yang, SIGIR 2019）。
3. **recovery source 的保留界線也受 safe commit 限制。** 只等 retention lease 不夠。現在 lease 追上之後會再 commit 一次才 merge，manifest 也記錄 `recovery_source_pruned`。
4. **merge 剛完成時，store 可能還包含舊 segment 的檔案。** 有一次量到 170 MB，磁碟上的索引其實是 79 MB。大小改用 live segment 的檔案計算。
5. **batch 20 的文字分類有錯。** 34 個泰盧固文、馬拉雅拉姆文、卡納達文、古吉拉特文和希臘文的關鍵字被算成拉丁文。修正權重後重算，Q 仍然成立。

### 10.5 golden batch 20 驗證和查詢調整

44k 爬蟲頁面幾乎不含 golden 頁面，所以量不到排序品質。這次直接抓 golden 頁面，放進索引一起比。

- **抓取。** 從 Mac 把 14,822 個 golden URL 各抓一次，時間是 10-23 08:01–10:01 UTC。
  - 每個 host 每 5 秒最多 1 個請求，遵守 robots.txt，不重試。
  - 成功抓到 10,345 頁，涵蓋 69.8% 的配對。
  - robots.txt 擋掉 3,628 個 URL，主要是 facebook、instagram、x、reddit 和 imdb。
- **索引。** golden 頁面加上 44,028 頁爬蟲頁面，共 54,370 篇。
- **指標。** 條件式 recall：頁面已在索引裡的配對中，有幾成排進前 10。
- **調參方法。** 規則在看數字之前寫好（10:26 UTC）。
  - 關鍵字依 sha256 分成 dev 和 test 兩半。
  - 只用 dev 挑設定，最後在 test 上只做一次比較。

| 項目 | 調整前 | 調整後 |
|---|---|---|
| 查詢 | `combined_fields`，`title^2`、`description`、`body` | `best_fields`，再加上 `url.text` |
| test 的條件式 recall | 0.761 | 0.811，差 +0.050 [+0.040, +0.061] |
| 全部關鍵字 | 0.769（head 0.734，random 0.801） | 0.813（head 0.787，random 0.836） |
| nDCG@10 | 0.609 | 0.647 |
| 每篇索引大小（44k 爬蟲頁面） | 1,785 bytes | 1,840 bytes（+3.1%），128M 篇約 0.24 TB |

- `best_fields` 用分數最高的欄位計分。標題直接寫出關鍵字的頁面，會贏過只重複其中一個詞的長頁面。
- `url.text` 把 URL 切成詞。新聞和體育網址常帶有 slug，例如 `dodgers-brewers`。
- 英文詞幹在 `best_fields` 下沒有幫助，所以主 analyzer 不變，NanoMIRACL 的結論仍然成立。
- 限制：
  - 只用一期 golden 挑選和確認，只是 dev 和 test 的關鍵字不重疊。
  - 索引只有 54k 篇。文件數每加倍，recall 約掉 2 個百分點，所以 128M 時的絕對值會低很多。
  - 社群網站被 robots.txt 擋掉，沒有量到。

### 10.6 下一步

1. commit 並更新 WSE 的 PR。這一步要先經過你同意。
2. 部署到 Host2 前，先完成 `TEXT_INDEX.md` 的 "Before Host2" 清單。
3. 真正的 Recall@10 要等 PR #84 部署，而且 recrawl 抓到清單的正文之後才能量。
4. 下一期 golden 出來後，用同樣方法再驗證一次查詢設定。
