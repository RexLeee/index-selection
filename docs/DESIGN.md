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

**里程碑 1（離線研究）：** 用 1% block sample 估計 top-K，做回測和前瞻評估。

**里程碑 2（串上 Metric，目標 batch 20）：**

- 全量匯出已爬列，用凍結模型打分，取精確的 top-K（§12）。
- 清單寫進我們的 selectdb（Host1 `:5445`），Metric 用它自己的 SQL 讀（§7.1）。
- 在 Host2 用容器執行，每個指令是一次 `docker compose run --rm`。

**仍不做：**

- 不排程，不做 daemon。
- 不寫 crawlerdb、metricdb 和 V1 的 selectdb（`:5444`）。
- 不抓頁面內容，不建 index。

---

## 2. 系統脈絡

```
crawlerdb (read-only, paced)        metricdb (read-only)
  ├─ pull-sample: TABLESAMPLE 1%      └─ pull-golden: batches, queries, URLs
  ├─ pull-positives: PK lookups
  ├─ pull-dims: split hosts
  └─ pull-full: every crawled row, ctid chunks (milestone 2)
            ▼ Parquet in work_dir
FeatureStore (DuckDB): pool_b, as-of features, EB domain rates, URL history
            ▼
LightGBM golden-propensity model  ← trained on batches < t
            ├─► evaluate: recall@K from the sample boundary, bootstrap by query
            └─► score-full (sample aggregates) → select-full: exact top-K
                        ▼
OUR selectdb :5445  ◄── publish (COPY FREEZE) → swap (rename) / rollback
            ▲
            ├── official metric: SELECT url FROM selected_urls_current WHERE url = ANY(:urls)
            └── verify-live: the same query, as the metric's reader role
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
| `full/export/shard=NNN/part-<lo>-<hi>.parquet`、`_meta.json` | 全部已爬列，依 heap block 範圍分檔；`_meta.json` 記錄每個 shard 的 block 範圍、`frozen_probe` 和 `end_probe` | `pull-full` |
| `full/scored/shard=NNN.parquet`、`_meta.json` | 分數 ≥ τ 的列（url、shard、h、score）；`_meta.json` 記錄模型、K、τ | `score-full` |
| `full/selection/run=<id>/selected.parquet`、`_meta.json` | 精確的 top-K（url、score）；`_meta.json` 最後寫 | `select-full` |

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

### 7.1 selectdb 契約（`publish.py`）

Metric 讀這張表，所以 schema 和 V1 相同（WebSearchEngine `IndexSelection/selectdb_common.py`）：

| 表 | 欄位 |
|---|---|
| `selected_urls_current` | `url` PK、`score`、`first_seen`（NULL）、`run_id` FK、`selected_at` |
| `model_runs` | V1 的欄位，加上 `status`（loading、validated、live、retired、abandoned、rolled_back）、`activated_at`、`config_sha256`、`code_version` |

- **Metric 怎麼讀：** `SELECT url FROM public.selected_urls_current WHERE url = ANY(:urls)`，每次 1 萬個 URL，用寫死的 `select` 帳號。golden 原始字串出現在表中就算 indexed（`WebSearchEngine` main 的 `b0bea75`）。
- **載入：** `publish` 在一個交易內建 `selected_urls_r<id>`、`COPY FREEZE`、加 PK 和 FK、`ANALYZE`，再檢查列數等於 K。`wal_level=minimal` 下，同一交易內建立並填滿的表不寫 WAL。這時讀者還看不到新表。
- **切換：** `swap` 在 `lock_timeout` 5 秒內把 current 改名成 prev，再把新表改名成 current。讀者最多等幾毫秒，不會看到一半的清單。
- **凍結時段：** 1、16 日 11:30–20:00 UTC 拒絕 swap。Metric 在 12:00 建 random set、18:00 建 head set，建完立刻量。
- **回滾：** `rollback` 對調 current 和 prev。新的 load 或 rollback 會丟掉所有已載入但沒 swap 的表。
- **讀者帳號：** `init-selectdb` 建 `select` 角色，密碼和 Metric 寫死的相同，只有 SELECT 權限。
- **自我檢查：** `verify-live --batch <b>` 用 `select` 帳號送同一條 SQL。exact 比率等於 Metric 的 `indexed_rate`（找到的不重複原始 URL ÷ 不重複原始 URL），另外報 canonical 比率和以 query 為單位的 bootstrap CI。
- ⚠️ 未合併的 WebSearchEngine PR #5 會改成「原始或 canonical 拼法都算，而且要已爬」。到時 `indexed_canonical_rate` 比較接近官方數字。

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
| 讀取時機 | **讀 crawlerdb 前要取得助教同意**（2026-10-08 已同意）。`ReadGuard` 在 crawlerdb 和 metricdb 的每條語句前檢查：語句跑滿逾時也不會進入 golden 建立與量測日（1、16 日 00:00–20:00 UTC）或每小時 :58–:10；`pg_stat_activity` 中等待 IO 的連線數（3 次取中位數）超過 `max_io_waiters` 就暫停。IO 探測本身要 2 秒，所以探測完再檢查一次時段才放行。每次等待都寫進 log。時段和門檻都在 `[guard]`。Metric 整點掃描實測在 :00–:07，峰值 7–8 GB/s（2026-10-11）。語句跑滿逾時也會在 :58 前結束，所以每小時時段從 :58 開始。V2 的每日 02:50–04:40 時段在 2026-10-11 拿掉：它保護的是 V2 每天讀的 `url_state_history`（accounting 03:00 會 DROP 它的分區），isel 不讀這些表；gc 只鎖要刪的列，而且從 04:00 跑約 8 小時；`sar` 在 10-03 到 10-11 的這段時間沒有額外負載，爬蟲還在跑的 10-03 到 10-05 也一樣 |
| S0 的意義 | `pull-sample` 開始時記下 `frozen_probe`（server 啟動時間加 `url_state_current_*` 累計寫入列數）。`advance-snapshot` 只接受已完成的 sample，再量一次，完全相同才把 S0 移到現在，否則拒絕 |
| 資料不完整 | `FeatureStore` 拒絕三種狀況：sample 沒有 `finished_at`、任何 batch 缺 `positives/batch=<id>.parquet`、同一個 batch id 同時有官方和偽 golden 列 |
| 憑證外洩 | DSN 只放環境變數，或放在 `<NAME>_FILE` 指向的 secret 檔（容器用這種）；所有 JSON 輸出經過 `assert_no_secrets` |
| 寫入 selectdb | 只有 `publish.py` 會寫 DB。連線前，DSN 的 port 若在 `selectdb.forbidden_ports`（5432、5433、5444）就拒絕；連上後，資料庫若有 `url_state_current_000` 或 `metric_url` 也拒絕。`verify-live` 用 `default_transaction_read_only=on` 的讀者連線 |
| 全量讀取 | `pull-full` 每塊一條 `COPY`，只讀 `chunk_blocks` 個 heap block（TID range scan）。每塊 512 MiB，逾時取下限 120 秒，所以每個禁讀時段前只保留 2 分鐘。逾時依區塊大小計算，照樣經過 SQL gate、`ReadGuard` 和限速。heap 大小用 `pg_relation_size` 量，不用過期的 `relpages`；匯出途中新增的頁面會補成新的範圍 |

---

### 9.1 crawlerdb 若遷移到 YugabyteDB（只規劃，未實作）

1. **觸發條件：** 助教確認切換日期之後才動工。
2. **先看 schema：** 從 Voyager 產生的 DDL 確認 `url` 是 HASH 主鍵的第一欄，以及 256 張表是否保留。
3. **抽樣：** 改用 `WHERE yb_hash_code(url) < floor(65536 × p)`。條件會下推到 DocDB，只讀對應的 hash 範圍，抽到的是列層均勻樣本，沒有區塊群聚。先用 `EXPLAIN` 確認下推。`_meta.json` 記錄抽樣方式；hash 樣本和 TABLESAMPLE 樣本不能混用。
4. **設定：** session GUC 依 engine 分開。`pg_class.relpages` 沒有意義，改用 `reltuples × p` 估讀取量。`yb_read_time` 預設只保留 900 秒歷史，不能拿來固定 S0；仍用 `frozen_probe`。兩個節點只能 RF 1，沒有 follower reads 可以分流。
5. **驗證：** 本機用同版本 YugabyteDB 容器跑合約測試。PG 和 YB 並存時，golden URL 的主鍵查詢要逐筆一致，樣本只比較分布。
6. **要提醒助教的風險：** Voyager export 目錄和 YB 資料目錄都在 Host1 `/data`（2026-10-08 剩 385 GB，crawlerdb 6.6 TB）。live migration 要把 `wal_level` 改成 logical（需重啟），replication slot 卡住會累積 WAL，可能再次寫滿 `/data`。

## 10. 執行手冊（研究流程，Mac）

里程碑 2 的全量流程在 Host2 的容器裡跑，見 §12。

```bash
set -a; source .env; set +a                 # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config config/isel.toml"

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

### 10.1 在 Mac 執行

2026-10-09 已實測：Mac 經 CSIE VPN 可以直連兩個 DB。伺服器上不需要任何檔案。

- `.env` 放在 repo 根目錄，權限 600。DSN 的 host 和 `.env.example` 相同。兩個 DB 的 `pg_hba.conf` 接受任何來源的 scram-sha-256 登入。
- 只用 `config/isel.toml`。base 設定就是 Mac 的規模：8 threads、DuckDB 16 GB。
- 長指令用 `caffeinate -i` 執行，Mac 要接電源並保持 VPN 連線。
- VPN 曾在幾分鐘內重設連線多次，指令會因 `OperationalError` 結束。重跑同一個指令即可續傳：`pull-sample` 從沒完成的 shard 繼續，`pull-positives` 跳過已有檔案的 batch。
- 實測量（2026-10-09）：`pull-positives` 每個 batch 約 2 分鐘；`pull-sample` 讀 33.9 GB 的頁、1,648 萬列，約 47 分鐘（含一次每小時禁讀時段）；讀取期間 Host1 的 IO PSI avg10 最高 17.9%。
- 直連失敗時，改用 `ssh -N -L 127.0.0.1:15432:127.0.0.1:5432 -L 127.0.0.1:15433:127.0.0.1:5433 ntu-sa1`，DSN 改成這兩個本機 port。

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

---

## 12. 容器和部署（里程碑 2）

### 12.1 放在哪裡

| 元件 | 主機 | 理由 |
|---|---|---|
| isel 容器（`deploy/compose.compute.yaml`） | Host2 | 爬蟲停止中，可用 RAM 684 GB、根目錄剩 564 GB；經 LAN 讀 Host1 的 DB |
| 我們的 selectdb `:5445`（`deploy/compose.selectdb.yaml`） | Host1 | 2026-10-04 起已在跑；資料在根目錄 NVMe，不在 crawlerdb 的 `/data` |

Mac 經 VPN 只有約 4 MB/s，全量匯出只能在伺服器上跑。

### 12.2 image（`Dockerfile`）

- **多階段：** `builder` 用 `uv sync --locked --no-dev --no-editable` 建 `/opt/venv`；`runtime` 只複製 venv 和 `config/`。依賴層只在 `uv.lock` 改變時重建。
- **固定版本：** base image 和 uv image 都以 digest 固定。uv 用 0.7.3，和本機相同。
- **`libgomp1`：** slim image 沒有 OpenMP runtime，LightGBM 會 import 失敗。
- **`test` 階段：** 用 `RUN --network=none` 跑單元測試，證明 DuckDB、tldextract、LightGBM 執行時不需要下載任何東西。
- **身分：** `USER 1014:1015`，和主機帳號相同，掛載目錄裡的檔案都歸使用者。
- **版本：** `--build-arg GIT_SHA=<short sha>` 寫進 `ISEL_CODE_VERSION` 和 OCI `revision` label。模型 meta、`results.json`、選取 run 和 selectdb 的 `model_runs.code_version` 都記錄它。
- **設定：** `config/` 打包進 image，所以 `config_sha256` 和程式版本一起固定。容器的規模在 `config/container.toml`。

### 12.3 compose 設定

| 設定 | 理由 |
|---|---|
| `profiles: [job]`，用 `run --rm` | 一個指令一個容器，跑完就刪；`up` 不會啟動它；不設 restart、不開 port |
| `init: true` | Ctrl-C 和 `docker stop` 能傳到 isel；所有指令都可續跑 |
| `read_only: true`、`tmpfs: /tmp` | 只有 `/work` 可寫 |
| secret 檔 + `<NAME>_FILE` | DSN 不出現在 `docker inspect`、env 或 log（V1 的 cron 檔就外洩過 DSN） |
| `mem_limit`/`memswap_limit` 64g、`cpus` 16 | DuckDB 40 GB、LightGBM 16 threads，都設在上限以下。DuckDB 1.5 會讀 cgroup，但預設用到 80%，所以明確設定，留空間給 Arrow 和 Python 物件。LightGBM 預測時不讀模型存的參數，所以每次 `predict` 都傳 `num_threads`；不傳時 OpenMP 依主機的 144 核開執行緒，被 16 CPU 的配額節流。設定檔因此要求 `model.params.num_threads` 是正整數，也拒絕 `n_jobs` 等別名 |
| `cpu_shares: 256`、`oom_score_adj: 1000`、`pids_limit: 512` | 爬蟲恢復後，搶資源時爬蟲優先，記憶體不足時先殺我們 |
| `json-file` log，20 MB × 5 | 和現有容器相同，不會塞滿根目錄 |
| 固定 bridge 子網 `172.20.251.0/24` | Docker 自動挑的網段可能撞到 VPN 用戶端（172.24.x.x）；Host1 的 `:5445` 也這樣做 |

服務層級的 `mem_limit`、`cpus` 在這兩台（Docker 29.1、Compose v5）已實測生效。`deploy.resources` 在非 Swarm 下的行為說法不一，所以不用它。

### 12.4 secret 檔

使用者在 Host2 的 `/home/r14725023/isel/secrets/` 建立以下檔案，權限 600。內容不進 repo、image、env 或 log。

| 檔案 | 內容 |
|---|---|
| `crawlerdb_dsn` | crawlerdb DSN（`172.16.191.1:5432`） |
| `metricdb_dsn` | metricdb DSN（`172.16.191.1:5433`） |
| `selectdb_admin_dsn` | `idxsel_admin` 連 `172.16.191.1:5445/selectdb` |
| `selectdb_reader_dsn` | `select` 連 `172.16.191.1:5445/selectdb` |
| `selectdb_reader_password` | Metric 寫死的 `select` 密碼（只有 `init-selectdb` 用） |

### 12.5 部署和執行（每一步都要使用者核准）

```bash
# Mac: ship one commit (no repo access needed on the server)
SHA=$(git rev-parse --short HEAD)
git archive --format=tar "$SHA" | ssh ntu-sa2 "mkdir -p ~/isel/src-$SHA && tar -x -C ~/isel/src-$SHA"
rsync -a work/sample work/golden work/positives work/dims ntu-sa2:isel/work/
rsync -a work/models/<M0> ntu-sa2:isel/work/models/

# Host2
cd ~/isel/src-$SHA
docker build --target test .                                   # unit tests, no network
GIT_SHA=$SHA docker compose -f deploy/compose.compute.yaml build --build-arg GIT_SHA=$SHA isel
export GIT_SHA=$SHA
J="docker compose -f deploy/compose.compute.yaml run --rm isel"
$J check-config
$J pull-full --dry-run                       # catalog only; check heap_bytes, chunks, free disk
$J pull-full                                 # needs TA approval; rerun until shards_missing = 0
$J score-full --model-dir /work/models/<M0>
$J select-full
$J init-selectdb                             # idempotent
$J publish --run-dir /work/full/selection/run=<id>
$J swap --db-run-id <n>
$J verify-live --batch 19                    # sanity: in-sample batch
# after batch 20 exists (2026-10-16 20:00 UTC): pull-golden, then
$J verify-live --batch 20
```

- 長指令用 `nohup`，或在 `tmux` 裡跑；中斷後重跑同一個指令就會續跑。
- 想先演練時，可以用 `select-full --k 1000000` 的結果跑一次 publish、swap、verify-live、rollback。
- `build` 一定要寫服務名 `isel`。服務在 `job` profile 裡，不指名就不會建。

### 12.6 回復

- 清單有問題：`$J rollback`，上一份清單立刻恢復。
- Metric 要改回 V1：由助教把 `--select_db_url` 改回 `:5444`。
- 移除：`docker compose -f deploy/compose.compute.yaml --profile job down` 刪掉 network，`docker image rm isel_r14725023:<sha>` 刪掉 image，再刪掉 `~/isel/src-<sha>`。共用主機上不跑 `docker system/image/volume prune`。

### 12.7 規模估計

| 項目 | 估計 | 依據 |
|---|---|---|
| crawlerdb heap | 約 3.4–4.9 TB | 3.4 TB 是 10-09 的 `relpages`（4.14 億頁），4.9 TB 是 10-05 的 `url_state_current` 大小；`pull-full --dry-run` 會給實際值 |
| 匯出時間 | 3.4 TB 時約 3–20 小時 | 單連線 67–440 MB/s；禁讀時段和探測讓約 28% 的時間不能讀（repo 的 `next_allowed_start` 模擬）。第一個 shard 量完再定 |
| 匯出檔 | 約 90 GB | 約 16 億列 × 每列約 55 B（V2 量測） |
| 打分結果 | 約 1.5 億列，約 12 GB | τ 只留約 K·(1 + `tau_margin`) 列 |
| `score-full` 記憶體 | 峰值約 10 GB | Mac 實測，用真實的 1% sample（1,648 萬列）。峰值出現在估 τ 時；之後每次只載入一個匯出檔（8 GiB heap，約 350 萬列）。若一次載入整個 shard，最大的 shard 約 3,500 萬列，估計峰值約 63 GB，接近 64 GiB 上限 |
| selectdb | 每份清單約 30 GB（heap 加 PK）；current、prev、新表同時存在時約 90 GB | Host1 根目錄剩 654 GB；`selectdb.max_db_bytes` 擋住超量 |
