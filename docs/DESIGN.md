# Index Selection 設計文件

- 程式位置：`src/isel/`，每節標出對應的模組
- 調研依據：[`RESEARCH.md`](RESEARCH.md)
- 所有參數：`config/isel.toml`

---

## 1. 目標與非目標

**目標：**

- 每兩週從約 16.6 億個已爬 URL 中選出 K = 1.28 億個，最大化下一批 golden 的 indexed rate。
- 方法可解釋，評估沒有洩漏，數字可以重現。
- 輸出一份穩定的 manifest，讓未來的 recrawl 和 inverted index 直接讀。

**里程碑 1 的非目標：**

- 不寫正式 selectdb，不排程，不做 daemon。
- 不抓頁面內容，不建 index。
- 不對 16.6 億列全量打分；用 1% block sample 估計 top-K。

---

## 2. 系統脈絡

```
crawlerdb (read-only, paced)        metricdb (read-only)
  ├─ pull-sample: TABLESAMPLE 1%      └─ pull-golden: batches, queries, URLs
  ├─ pull-positives: PK lookups
  └─ pull-dims: split hosts
            ▼ Parquet in work_dir (Host2)
FeatureStore (DuckDB): pool_b, as-of features, EB domain rates, URL history
            ▼
LightGBM golden-propensity model  ← trained on batches < t
            ▼
evaluate: recall@K from the sample boundary, bootstrap by query
            ▼
selection/run=<id>/manifest  →  (future) fetcher → WARC → text → BM25 index
```

### 2.1 工作目錄

| 路徑 | 內容 | 寫入者 |
|---|---|---|
| `golden/golden.parquet`、`summary.json` | 全部官方 golden 列和每批統計 | `pull-golden` |
| `golden/pool.parquet` | 每批完整的 trending 池（`metric_queries`，含沒被選的 query） | `pull-golden` |
| `golden/pseudo/batch=<id>.parquet`、`.json` | 偽 golden 列；JSON 只放數字 | `pseudo-batch` |
| `trends/snapshot=<id>/` | 10 個 geo 的 trending 原始列和 `_meta.json` | `trends-snapshot` |
| `trends/compare/<a>_vs_<b>.json` | 兩份快照的每個 geo 的 keyword Jaccard 和 volume 等級相關；只放數字 | `trends-compare` |
| `pseudo/serp/<run>/<provider>.jsonl` | 每次執行一份 SERP 快取（只存 query、link 和抓取時間）。續跑同一個 run 不會重付 | `pseudo-*` |
| `pseudo/ledger.json` | 每個供應商在每個額度期間的呼叫次數 | `pseudo-*`、`trends-snapshot --source serpapi` |
| `pseudo/calibration/batch=<b>_<tag>.{json,parquet}` | 校準結果；query 文字只在 Parquet | `pseudo-calibrate` |
| `dims/split_hosts.parquet` | `shard_split_subdomain` 白名單 | `pull-dims` |
| `sample/shard=NNN.parquet`、`_meta.json` | 1% block sample；`snapshot_at` 是 S0；`finished_at` 只在全部 shard 完成後出現，`shards_failed` 列出待重試的 shard | `pull-sample` |
| `positives/batch=<id>.parquet` | 每批 golden 的原始與 canonical 拼法查到的列 | `pull-positives` |
| `datasets/summary.json` | 每批的候選池大小、正例數、有無歷史、標籤稽核（§5） | `build-dataset` |
| `backtests/<id>/` | `results.json`、`report.md` | `backtest` |
| `models/<id>/` | 凍結的 LightGBM 模型和 meta | `train` |
| `forward_evals/batch=<b>_<id>/` | 前瞻評估結果 | `forward-eval` |
| `selection/run=<id>/` | manifest 和 delta | `select-sample` |

所有檔案都先寫成 `.tmp`，fsync 後才 rename（`io.py`）。

---

## 3. 時間點與候選池（`features.py`）

- 每個 batch b 有一個 as-of 時間：`as_of_b = min(created_b − lead_h, S0)`。`lead_h` 預設 30 小時，S0 是 sample 的快照時間。
- **候選池：** pool_b = {`last_fetch_ok` < as_of_b}。每個 URL 只爬一次，所以這等於重建 batch 出現前的爬取狀態。
- **golden 衍生特徵：** batch b 只用 `created_b′ < created_b` 且 `available_b′ ≤ as_of_b` 的 batch b′。官方 batch 的 `available_at` 等於建立時間；偽 batch 的 `available_at` 是 SERP 抓取時間（§5.1）。
- **訓練 batch：** 也用 `available_at ≤ 測試 batch 的 as_of` 判斷。所以 10 月才抓的偽 golden 不會進入 B18、B19 的回測，只會影響 batch 20 的前瞻評估。
- **衰減權重：** w(b′, b) = 0.5 ^ ((as_of_b − created_b′) / 天 / `half_life_days`)。
- **前瞻 batch：** 建立時間晚於 S0 的 batch，as_of 就是 S0，所以它只能看到 S0 之前的資訊。

---

## 4. 特徵（`features.py`）

模型只能讀 `FEATURES` 登記表裡的欄位。`FORBIDDEN` 裡的欄位一旦出現在 feature set，`resolve_set` 會直接報錯。

| 群組 | 特徵 | 洩漏等級 |
|---|---|---|
| static（URL 形狀） | `url_len`、`path_depth`、`is_root`、`has_query`、`n_params`、`path_long_number`、`path_date`、`path_marker`、`ext_class`、`is_https`、`sub_www`、`sub_mobile`、`sub_amp`、`host_labels` | 無 |
| fetch（抓取當下固定） | `title_null`、`title_len`、`has_anchor`、`discovery_source_type`、`robots_bits`、`is_redirect`、`redirect_hop_count`、`has_json_ld`、`hreflang_count`、`has_last_modified`、`has_cache_control`、`fetch_age_days` | 無 |
| cumulative（會累加） | `log_inlinks`、`log_ext`、`has_ext`、`rel_inlinks`、`rel_ext` | patrol 會讓 inlinks 膨脹；用 `no_inlinks` ablation 檢查 |
| history（URL 自己的 golden 歷史） | `hist_decayed`、`hist_head`、`hist_random`、`hist_days_since` | 只用 as-of 之前的 batch |
| domain（as-of 網域統計） | `lift_tld`、`lift_dkey`、`lift_host`、`lift_hp`、`dem_dkey`、`dem_host`、`log_size_dkey`、`log_size_host`、`dkey_ext_share`、`history_batches` | 只用 as-of 之前的 batch 和 pool |

**禁用欄位：** `source`、`url_score`、`url_score_updated_at`、`domain_score`、`discovered_from`、`should_crawl`、`last_scheduled`、`first_seen`、`parent_page_score`、所有 `*_90d`、`num_consecutive_fail`、`last_content_update`。

`url_score` 和 `source` 仍會被拉下來，但只給前瞻評估的 baseline 用。

### 4.1 網域層 empirical Bayes

階層由細到粗：`hp`（host + 第一段路徑）→ `host` → `dkey`（shard key，即白名單 host 或 eTLD+1）→ `tld` → 全體。

對每一層的 key k 和目標 batch b：

```
e_k(b) = Σ_{b′} w(b′, b) · n_sample(k, pool_b′) / p        # estimated crawled pages
g_k(b) = Σ_{b′} w(b′, b) · hits(k, b′)                      # crawled golden pages
r_global = G / E
r_tld  = (g_tld  + a) / (e_tld  + a / r_global)
r_dkey = (g_dkey + a) / (e_dkey + a / r_tld)
r_host = (g_host + a) / (e_host + a / r_dkey)
r_hp   = (g_hp   + a) / (e_hp   + a / r_host)
lift_x = ln(r_x / r_global)
```

- `hits(k, b′)` 是 batch b′ 中、在 pool_b′ 裡找得到的不重複 canonical golden 數。
- `a` 是 `features.eb_pseudo_hits`，單位是「虛擬命中數」。
- 沒有資料的 key（e = g = 0）會等於上一層，所以小網域不會得到極端值。
- `dem_*` 是不論有沒有爬到的 golden 需求量，對應 V2 的 prior。

### 4.2 網域內相對值

`rel_inlinks = ln(1 + inlinks) − mean_{dkey, pool_b}(ln(1 + inlinks))`，`rel_ext` 同理。這讓頁面層可以比較「同網域內誰的連入比較多」，而不是只看絕對值。

程式用精確的 DECIMAL 算 `(n·x − Σx) / n`，不直接減浮點平均。頁面值等於網域平均時，結果正好是 0。

---

## 5. 模型（`model.py`、`dataset.py`）

**訓練資料：** 對每個訓練 batch b：

- 正例：pool_b 中，url 等於 golden(b) 的原始或 canonical 拼法的列。
- 負例：sample ∩ pool_b 中其他列，用 `md5(url + salt + b)` 雜湊再抽 `neg_rate`。
- 正例權重依 tag 平衡，讓 head 和 random 的總權重相同。

**標籤的意義：** 標籤是每個 batch 各自定義的，模型估的是 P(url ∈ golden(b) | as_of_b 的特徵)。

- 一頁在 batch 3 是負例、batch 7 是正例，是這個機率的正常結果，不是標錯。官方 metric 量的也是「下一批 golden 有沒有被收錄」，所以目標就是這個觀測到的標籤過程。
- PU learning 的修正估的是「潛在相關」，會偏離官方 metric，所以不做。就算改估「未來會不會進」，在 SCAR（已標記的正例是全部正例的隨機子集）下，Elkan & Noto (2008) 證明分數只差常數倍，top-K 排序不變。
- 官方只認原始拼法（§6）。同一頁的 http/https、`www`、尾斜線變體在 crawlerdb 是不同列，選它們拿不到分，所以它們是真負例。
- `build-dataset` 的稽核欄位，每個官方 batch 一組：

| 欄位 | 意義 |
|---|---|
| `neg_rows` | pool_b 中的 sample 負例數 |
| `neg_later_golden`、`neg_next_golden` | 其中 URL 在之後任一個／下一個官方 batch 是 golden 的列數。用到未來資訊，只做診斷 |
| `later_golden_share` | `neg_later_golden / neg_rows`，即母體比例的估計 |
| `neg_variant_golden` | 和 golden(b) 某個拼法只差 scheme、開頭 `www.` 或結尾 `/` 的 sample 負例 |
| `canonical_only_positives` | 只靠 canonical 拼法配上的正例。原始拼法和 canonical 不同時，原始那列不會被爬到，官方 metric 永遠不會算它命中 |

**case-control 修正：** 負例的總抽樣率是 p·q。母體 logit = 模型 logit + ln(p·q)。修正只平移分數，不改 top-K 排序；它用來做校準檢查。

**訓練：**

- 訓練 batch：`first_label_batch` 之後、在測試 batch 的 as-of 之前已存在、而且有 golden 歷史。
- train frame 依 (url, shard) 排序。LightGBM 的 bagging 依列的位置抽樣，DuckDB 平行查詢的列順序又不固定，不排序的話，同樣的輸入每次會訓練出不同的模型。
- 跨列的浮點加總都先轉 `DECIMAL(38, 18)` 再加。範圍包括 `rel_*` 的網域加總、`lvl_*`、`dem_*`、`glob_stats` 和 `hist_decayed`。
  - DuckDB 平行加總 DOUBLE 時，相加順序不固定，每次結果差約 10⁻¹⁵。
  - LightGBM 把 0 單獨放一個 bin。應該是 0 的值變成 ±4×10⁻¹⁶ 時，模型就會不同。
  - DECIMAL 加總是整數運算，結果和相加順序無關。
- early stopping 只看最後一個官方訓練 batch。`refit_on_all = true` 時，用最佳輪數在全部訓練 batch 上重訓。
- 測試 batch 永遠不參與調參。

**baseline：**

| 名稱 | 定義 | 回測可用 |
|---|---|---|
| `random` | 全部同分，依 URL 雜湊排序 | 是 |
| `inlinks`、`ext` | `log_inlinks`、`log_ext` | 是 |
| `domain_prior` | `lift_dkey` | 是 |
| `logreg` | 同 `full` 特徵的 logistic regression | 是 |
| `v1_features` | V1 複製版：URL 形狀加 `dem_dkey` 的 LightGBM | 是 |
| `url_score` | crawler 的 ranker 分數 | **只在前瞻評估** |
| `v2_baseline_v1` | V2 公式 `tier + (inlinks + ext + url_score + 2·prior)/5` | **只在前瞻評估** |
| `later_unlabeled` | 同 `full` 的 LightGBM，但訓練 batch b 的負例中，URL 在同一個訓練清單裡、建立時間晚於 b 的 batch 是 golden 的列不參與訓練 | 是；`train` 不會訓練它 |

**`later_unlabeled` 實驗（事先寫定，2026-10-14 20:10 凍結前判定）：**

- 它直接檢驗「之後會進 golden 的頁不該當負例」。訓練清單只含測試 as-of 前可用的 batch，所以沒有洩漏。
- 變體和 `lgbm:full` 用同一個訓練矩陣，只遮掉被標記的列，負例抽樣、特徵和列順序都相同。
- 執行：`backtest --baselines random,later_unlabeled`。判定用預設 `eval.test_batches` 的 pooled 結果，B18、B19 另外列出。
- 採用條件：每個 tag 的 `later_unlabeled − lgbm:full` 配對 95% CI 下界都 > 0。被拿掉的列數（兩者 meta 的 `rows` 差）要和 CI 並列；拿掉的列很少卻判定通過時，視為訓練隨機性，不採用。原因是配對 CI 只重抽 query，不包含 LightGBM bagging 的變異。
- 沒通過就維持現狀，結果照樣寫進報告。通過時才加 `dataset` 設定開關，讓 M0 和 M1 一起改用。
- 限制：最新的官方訓練 batch 沒有「之後」，所以它的負例永遠不會被拿掉。

---

### 5.1 偽 golden（`pseudo.py`）

官方 golden 太少（batch 10–17 是空的）。偽 golden 用免費額度照官方規則再產生一批標籤，只拿來訓練和計算特徵，**永遠不拿來評估**。

| 步驟 | 做法 |
|---|---|
| trending 池 | `trends-snapshot`：10 個 geo、168 小時；用完整字串去重，frequency 是各 geo volume 的總和（照抄 `WebSearchEngine/Metric/RawDataReader/DatabaseRawDataReader.py`） |
| 選 query | 回填：從官方 batch 的池中、官方沒選到的 query 均勻抽樣，標 `random`。前瞻：對新快照跑官方規則，head 取前 N、random 從全池均勻抽 |
| SERP | `engine=google, q, num=10`，不帶 `gl`/`hl`，只留 `link`，rank 是過濾後的位置（照抄 `Metric/Query/QueryStrategy.py`） |
| 列 | id 是 `pseudo.id_offset + n`；`batch_created_at` 決定候選池（回填用原官方 batch 的時間）；`available_at` 是最後抓取時間；`label_source = "pseudo"` |
| 權重 | 和官方列相同（λ = 1，事先寫定，不調參）。early stopping 只看最後一個官方 batch |

**額度：**

- 每個供應商的設定都在 `[serp.providers.<name>]`：key 的環境變數、URL、最小間隔、`budget` 和 `budget_period`。
- key 只從 `ISEL_SERPAPI_KEY`、`ISEL_SERPER_KEY` 讀。config 拒絕 `SERPAPI_KEY`；執行時若 key 值等於課程的 `SERPAPI_KEY` 也拒絕。
- 每一次 HTTP 嘗試送出前都先記入 `pseudo/ledger.json`，包含重試。原因是我方逾時的請求，供應商可能已經計費。
- SerpApi 的 `budget_period = "month"`，每個日曆月重算。Serper 的註冊額度只送一次，所以是 `"total"`，永不重算。
- `pseudo-batch` 和 `pseudo-calibrate` 開始前先預檢：剩餘額度不夠付還沒快取的 query，就一次都不花，直接停止。預檢只保證每個 query 至少一次呼叫，重試仍會用掉餘裕。
- Serper 分配：校準 150、回填 B19 800、B18 500、前瞻批次 950（head 500 + random 450）、10-16 同步檢查 50，留約 50 次給重試。

**事先寫定的校準門檻（不過就停掉該部分）：**

0. `trends-compare`：同一時間的 trendspy 和 SerpApi 快照，每個 geo 的 keyword Jaccard 平均 ≥ `gate_trend_jaccard`（0.7）。沒過時，前瞻批次改用 `trends-snapshot --source serpapi`。
1. `pseudo-calibrate`：同一個 query 由 SerpApi 和 Serper 接連各抓一次，平均 overlap@10 ≥ `gate_provider_overlap`（0.7）。
2. 同一個指令：現在的 SerpApi 和官方存的 URL，平均 overlap@10 ≥ `gate_drift_overlap`（0.5）。B19、B18 分別判定，決定要不要回填。
3. `pseudo-gate`：回填批次的已爬正例率 ≥ 原官方 batch 的 `gate_positive_ratio`（0.6）倍。前瞻批次比凍結的爬取狀態新，不判定。

## 6. 評估（`evaluate.py`、`backtest.py`）

**recall@K 估計：**

- sample 的抽樣率是 p，所以母體排名 K 大約落在 sample 排名 round(K·p)。
- 把 sample 中排名 round(K·p) 的列當邊界，排序是 (score desc, URL 雜湊 asc)。
- golden 列排在邊界或之前就算入選。golden 列不加進 sample。
- p = 1 時，結果等於精確的 top-K（`tests/test_eval_golden_manifest.py`）。

**指標：**

- `indexed_exact_rate`：原始字串完全比對，等於官方 metric。官方用 SerpApi 原始 `link` 對 `selected_urls_current.url` 做完全比對，沒有任何正規化（`WebSearchEngine/Metric/Measure/CrawlerAllMetricMeasure.py:79-84, 207-209`）。
- `indexed_canonical_rate`：原始拼法或 canonical 拼法的字串有一個入選就算。它不涵蓋 http/https、`www` 這類等價拼法。
- `crawled_exact_rate`、`crawled_canonical_rate`：as-of 時已爬的上限。
- 三種 K：`k_values`、`fractions`（pool 的比例）、`gain_curve_k`。

**信賴區間：** 以 query 為單位的群集 bootstrap。出現在 c 個 query 的 URL，在每個 query 權重 1/c。每個 scorer 都和參考模型做配對差異。

**校準：** Σ(母體機率) / p 對上 pool 中實際的 golden 頁數。

**採用規則（事先寫定）：** 在 t−1 調參、在 t 報告；和最強 baseline 的配對 95% CI 下界 > 0 才採用。

**前瞻評估：** `train` 在 S0 凍結模型；新 batch 出現後，`pull-positives --batches <b>` 再 `forward-eval`。模型的 `snapshot_at` 必須等於 sample 的 S0，batch 必須晚於 S0，否則拒絕執行。

**M0 對 M1（batch 20，事先寫定）：**

- M0 用 `config/isel.toml` 訓練，M1 再加 `config/pseudo.toml`（`golden.use_pseudo = true`）。
- `forward-eval --model-dir <M0> --model-dir <M1>`：依模型 meta 的 `use_pseudo` 各建一個 FeatureStore，依 (url, shard) 對齊 frame；M1 的名稱加 `+pseudo`，配對差異以 M0 為參考。
- 採用 M1 的條件：每個 tag 的 M1 − M0 配對 95% CI 下界 > 0。
- `pseudo_overlap` 把 batch 20 的 golden 和命中拆成「偽 golden 已出現過」和「沒出現過」。前者屬於 nowcasting（用最新熱門預測下一批），要分開報告。

---

## 7. 輸出契約（`manifest.py`）

```
selection/run=<id>/
  manifest/index_shard=NN.parquet   # one row per URL, MANIFEST_SCHEMA
  deltas/added.parquet              # doc ids not in the parent run
  deltas/removed.parquet            # parent doc ids no longer selected
  _MANIFEST.json                    # written last: parent, rows, sha256 per file
```

| 欄位 | 意思 |
|---|---|
| `doc_id` | canonical URL 的 MD5 低 64 位元；跨 run 穩定，永不重用 |
| `url` | 要交給 metric 的拼法（目前是 crawler 的拼法） |
| `canonical_url`、`domain_key` | 正規化 URL 和 shard key |
| `crawl_shard` | crawler 的 0–255 shard，給抓取時做禮貌性限速 |
| `index_shard` | `doc_id mod index_shards`，給 indexer 分工 |
| `score`、`rank`、`reason` | 模型分數、名次、前幾名特徵貢獻（LightGBM SHAP） |
| `content_hash`、`simhash`、`lang`、`text_len` | 預留給 Phase B 的內容回饋，目前是 NULL |
| `run_id`、`schema_version` | 來源和版本 |

`validate-manifest` 會檢查每個檔案的 sha256 和列數。

---

## 8. 未來：recrawl → inverted index

### 8.1 分階段

| 階段 | 內容 | 技術 | 前提 |
|---|---|---|---|
| A（現在） | selection 輸出 manifest 和 delta | 本 repo | 助教同意讀 crawlerdb |
| B | 抓 1–5M 個 URL（含全部 golden），存 WARC，抽文字，建小型 BM25 index，用 golden query 評估。把 `content_hash`、`simhash`、`lang`、`text_len` 回饋給 selection | warcio、Resiliparse、Parquet + zstd、Pyserini | Host2 root 的 569 GB 足夠 |
| C | 全量 1.28 億篇、16 個 index shard；每期重建 base index 後原子切換；期間用 fresh tier 和刪除過濾 | OpenSearch（備案：Pyserini 分區加 fan-out） | 需要 1.5–2 TB；要協調 Host2 `/data`（6.6 TB 空） |

### 8.2 元件

```
manifest + deltas ──► fetch scheduler (politeness by crawl_shard / domain_key)
                            │  priority = rank; only added + stale URLs
                            ▼
                      fetcher (reuse crawler, keep body) ──► WARC.gz (staging, TTL)
                            ▼
                      extractor (Resiliparse) ──► text Parquet by index_shard
                            │                        (content_hash, simhash, lang, len)
                            │                        └──► feedback to selection features
                            ▼
                      indexer per index_shard: base (rebuild per cycle, blue/green)
                                             + fresh tier + removed-doc filter
                            ▼
                      broker: fan-out BM25, merge top-k, add score as static prior
```

### 8.3 現在就要定下的決定

1. `doc_id` 用 canonical URL 的雜湊，不用原始拼法，也不用列號。
2. `index_shard` 和 crawler 的 256 個 shard 分開。前者給 indexer 分工，後者給抓取限速。
3. 同時輸出 `url` 和 `canonical_url`。metric 比原始字串，fetcher 和 index 用 canonical。
4. delta 對「上一份已發布的 run」計算，並記錄 `parent_run_id`。
5. 輸出穩定的 `score`。index 可以把它當 query-independent 先驗（Maguro 式 tier）。
6. 預留內容回饋欄位，讓 Phase B 的特徵不需要改 schema。

### 8.4 recrawl 預算

- 每兩週全量重抓 1.28 億頁，等於每天 9.1M 頁，佔 crawler 產能（每天 26–29M）約 34%。
- 所以只抓 delta（`added`）和過期的頁。
- 正式產出時加遲滯：連續 2 期低於門檻才移除，控制每期的變動量。
- 另一條路：在 crawler 的 router 端，對預測分數高的頁直接保存內容，避免重抓。這要改 crawler，列為 Phase B 的選項。

---

## 9. 生產安全（`pg.py`、`pull.py`）

| 風險 | 做法 |
|---|---|
| 寫入 crawlerdb（帳號是 superuser） | 每條連線設 `default_transaction_read_only=on` 和 `standard_conforming_strings=on`。用戶端 `assert_read_only_sql` 先用 `normalize_sql` 照 PostgreSQL 的詞法掃一遍：字串（含 `E''`、`$$`）收成 `''`，巢狀註解拿掉，引號識別字還原成名稱，`U&"..."` 直接拒絕，非 ASCII 字母也算識別字字元。`%s` 不能緊接在識別字或 `$` 後面，因為 psycopg 送出時會把它改寫成 `$1`。之後只放行單一讀取語句：COPY 必須正好是 `COPY (SELECT/WITH 查詢) TO STDOUT [[WITH] (選項)]`，伺服器端檔案或 PROGRAM 目標一律拒絕；`SELECT ... INTO` 也拒絕。關鍵字和函式的檢查一律涵蓋整段語句，並拒絕會改伺服器狀態或代執行 SQL 字串的函式族（`set_config`、`pg_terminate_backend`、replication slot、`pg_stat_*reset*`、advisory lock、`lo_*`、`brin_summarize_*`、`gin_clean_pending_list`、`pg_prewarm`、`query_to_xml` 等）。`statement_timeout` 只能經 `ReadOnlySession.set_statement_timeout` 以整數參數設定 |
| 長查詢 | 預設 `statement_timeout` 120 秒，不能超過 `guard.timeout_cap_s`。sample 每個 shard 一條語句，只讀約 1% 的頁；逾時依 `pg_class.relpages` 和 `guard.min_mb_s` 計算，介於 `timeout_floor_s` 和 `timeout_cap_s` 之間（`pull.timeout_for`）。撞到逾時或 `lock_timeout` 的 shard 記進 `shards_failed`，其他 shard 繼續，下次執行再重試 |
| 伺服器端暫存檔 | 不做 server 端排序或彙總；`temp_file_limit=0`、`work_mem=16MB`、不開平行 worker |
| 搶資源 | 語句之間至少間隔 `pull.min_interval_s` |
| 讀取時機 | **讀 crawlerdb 前要取得助教同意**（2026-10-08 已同意）。`ReadGuard` 在 crawlerdb 和 metricdb 的每條語句前檢查：語句跑滿逾時也不會進入 golden 建立與量測日（1、16 日 00:00–20:00 UTC）、accounting/gc 時段（02:50–04:40 UTC）或每小時 :55–:10；`pg_stat_activity` 中等待 IO 的連線數（3 次取中位數）超過 `max_io_waiters` 就暫停。IO 探測本身要 2 秒，所以探測完再檢查一次時段才放行。每次等待都寫進 log。時段和門檻都在 `[guard]` |
| S0 的意義 | `pull-sample` 開始時記下 `frozen_probe`（server 啟動時間加 `url_state_current_*` 累計寫入列數）。`advance-snapshot` 只接受已完成的 sample，再量一次，完全相同才把 S0 移到現在，否則拒絕 |
| 資料不完整 | `FeatureStore` 拒絕三種狀況：sample 沒有 `finished_at`、任何 batch 缺 `positives/batch=<id>.parquet`、同一個 batch id 同時有官方和偽 golden 列 |
| 憑證外洩 | DSN 只放環境變數；所有 JSON 輸出經過 `assert_no_secrets` |

---

### 9.1 crawlerdb 若遷移到 YugabyteDB（只規劃，未實作）

1. **觸發條件：** 助教確認切換日期之後才動工。
2. **先看 schema：** 從 Voyager 產生的 DDL 確認 `url` 是 HASH 主鍵的第一欄，以及 256 張表是否保留。
3. **抽樣：** 改用 `WHERE yb_hash_code(url) < floor(65536 × p)`。條件會下推到 DocDB，只讀對應的 hash 範圍，抽到的是列層均勻樣本，沒有區塊群聚。先用 `EXPLAIN` 確認下推。`_meta.json` 記錄抽樣方式；hash 樣本和 TABLESAMPLE 樣本不能混用。
4. **設定：** session GUC 依 engine 分開。`pg_class.relpages` 沒有意義，改用 `reltuples × p` 估讀取量。`yb_read_time` 預設只保留 900 秒歷史，不能拿來固定 S0；仍用 `frozen_probe`。兩個節點只能 RF 1，沒有 follower reads 可以分流。
5. **驗證：** 本機用同版本 YugabyteDB 容器跑合約測試。PG 和 YB 並存時，golden URL 的主鍵查詢要逐筆一致，樣本只比較分布。
6. **要提醒助教的風險：** Voyager export 目錄和 YB 資料目錄都在 Host1 `/data`（2026-10-08 剩 385 GB，crawlerdb 6.6 TB）。live migration 要把 `wal_level` 改成 logical（需重啟），replication slot 卡住會累積 WAL，可能再次寫滿 `/data`。

## 10. 執行手冊（Host2）

```bash
cd ~/isel/repo && uv sync
set -a; source ~/isel/.env; set +a          # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config config/isel.toml --config config/host.toml"

uv run isel $C pull-golden                  # metricdb only
# --- needs TA approval from here ---
uv run isel $C pull-dims
uv run isel $C pull-positives
uv run isel $C pull-sample --dry-run        # pg_class only: pages, timeouts, next window
uv run isel $C pull-sample                  # ~16.6M rows, resumable per shard, guarded; rerun until shards_missing = 0
uv run isel $C validate-sample              # refetched_share: how often "fetched once" fails
uv run isel $C validate-routing             # expect candidate_rate >= 0.99
uv run isel $C build-dataset                # also the label audit (DESIGN §5)
uv run isel $C backtest --feature-sets full,no_inlinks,no_domain,no_history
uv run isel $C backtest --feature-sets full --baselines random,later_unlabeled  # pre-registered §5
# --- pseudo golden (SERP calls run on a laptop with ISEL_SERPAPI_KEY / ISEL_SERPER_KEY) ---
uv run isel $C trends-snapshot --source trendspy
uv run isel $C trends-snapshot --source serpapi       # same hour, 10 SerpApi calls
uv run isel $C trends-compare --a <trendspy id> --b <serpapi id>
uv run isel $C pseudo-calibrate --batch 19 --tag random --n 100
uv run isel $C pseudo-calibrate --batch 18 --tag random --n 50
uv run isel $C pseudo-batch --source official:19 --batch-id 10019 --random 800 --provider serper
uv run isel $C pseudo-batch --source snapshot:<id> --batch-id 10100 --head 500 --random 450 --provider serper  # keeps ~50 Serper calls for retries
P="$C --config config/pseudo.toml"
uv run isel $P pull-positives --batches 10019,10100
uv run isel $P build-dataset && uv run isel $P pseudo-gate --batch-id 10019
uv run isel $C advance-snapshot             # only if crawlerdb is unchanged since the sample
uv run isel $C train                        # M0; freeze with S0 <= 2026-10-15 06:00 UTC
uv run isel $P train                        # M1
# after batch 20 exists:
uv run isel $C pull-golden
uv run isel $C pull-positives --batches 20
uv run isel $C forward-eval --model-dir work/models/<M0> --model-dir work/models/<M1> --batch 20
```

---

## 11. 已知限制

- **inlinks 膨脹：** 回測的 inlink 特徵在 batch 之後量測，可能被 patrol 膨脹。前瞻評估能排除這點；為了縮短落差，新 batch 出現後要盡快跑 `pull-positives`。
- **block sample：** TABLESAMPLE SYSTEM 以頁為單位抽，同一頁的列常來自同一網域。小網域的 `*_size` 和 `rel_*` 估計較吵。
- **跨 shard 重複：** 同一 URL 出現在兩個 shard 時，sample 排名會算兩次。影響可忽略。
- **拼法：** crawler 存 `canonicalize_url(response.url)`，清單也輸出這個拼法；官方只認 SerpApi 的原始拼法。原始拼法和 canonical 不同時，原始那列沒有 `last_fetch_ok`，不會進 pool，exact 比對永遠配不到；`canonical_only_positives` 和 `indexed_canonical_rate` 顯示這部分的大小。同一頁的 http/https、`www`、尾斜線變體是不同列，它們是真負例（§5）。
- **redirect：** crawler 存 redirect 之後的網址（`WebCrawler/containers/crawler/crawler/settings.py` 的 `REDIRECT_ENABLED`）。golden 原始網址若會 redirect，原始那列永遠沒被爬到，抓到的內容記在目標網址名下。
- **每個 URL 只爬一次：** §3 依賴這個假設。sample 帶回 `num_fetch_ok_90d` 只為了檢查它（`FORBIDDEN` 擋住它進模型，`FeatureStore` 也不載入它和 `title`）。`validate-sample` 的 `refetched_share` 偏高時，pool 會把重抓的頁誤當成 batch 之後才爬到。
- **偽 golden 的雜訊：** 回填批次的 SERP 比官方晚 1–3 週抓，供應商預設值也可能不同（§5.1 校準）。爬取狀態凍結在 2026-10-05，所以偏新的頁面不會成為正例。`history_batches` 在 M1 的評分時會多出幾批，訓練列中只有前瞻批次看得到同樣的範圍。
- **免費額度只夠一次：** Serper 的 2,500 次只送一次，之後只剩 SerpApi 每月 250 次。M1 的做法不能每兩週持續；報告要寫明。
- **trendspy 是非官方 API：** 失效時改用 `trends-snapshot --source serpapi`（每次 10 次呼叫）。
- **batch 10–17 是空的：** 測試 batch 18 時，最近的訓練 batch 是 3 個月前的 batch 8 或 9。
