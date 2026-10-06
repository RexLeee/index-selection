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
| `golden/golden.parquet`、`summary.json` | 全部 golden 列和每批統計 | `pull-golden` |
| `dims/split_hosts.parquet` | `shard_split_subdomain` 白名單 | `pull-dims` |
| `sample/shard=NNN.parquet`、`_meta.json` | 1% block sample；`snapshot_at` 是 S0 | `pull-sample` |
| `positives/batch=<id>.parquet` | 每批 golden 的原始與 canonical 拼法查到的列 | `pull-positives` |
| `datasets/summary.json` | 每批的候選池大小、正例數、有無歷史 | `build-dataset` |
| `backtests/<id>/` | `results.json`、`report.md` | `backtest` |
| `models/<id>/` | 凍結的 LightGBM 模型和 meta | `train` |
| `forward_evals/batch=<b>_<id>/` | 前瞻評估結果 | `forward-eval` |
| `selection/run=<id>/` | manifest 和 delta | `select-sample` |

所有檔案都先寫成 `.tmp`，fsync 後才 rename（`io.py`）。

---

## 3. 時間點與候選池（`features.py`）

- 每個 batch b 有一個 as-of 時間：`as_of_b = min(created_b − lead_h, S0)`。`lead_h` 預設 30 小時，S0 是 sample 的快照時間。
- **候選池：** pool_b = {`last_fetch_ok` < as_of_b}。每個 URL 只爬一次，所以這等於重建 batch 出現前的爬取狀態。
- **golden 衍生特徵：** batch b 只用 `created_b′ < created_b` 且 `created_b′ ≤ as_of_b` 的 batch b′。
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

---

## 5. 模型（`model.py`、`dataset.py`）

**訓練資料：** 對每個訓練 batch b：

- 正例：pool_b 中，url 等於 golden(b) 的原始或 canonical 拼法的列。
- 負例：sample ∩ pool_b 中其他列，用 `md5(url + salt + b)` 雜湊再抽 `neg_rate`。
- 正例權重依 tag 平衡，讓 head 和 random 的總權重相同。

**case-control 修正：** 負例的總抽樣率是 p·q。母體 logit = 模型 logit + ln(p·q)。修正只平移分數，不改 top-K 排序；它用來做校準檢查。

**訓練：**

- 訓練 batch：`first_label_batch` 之後、在測試 batch 的 as-of 之前已存在、而且有 golden 歷史。
- early stopping 只看最後一個訓練 batch。`refit_on_all = true` 時，用最佳輪數在全部訓練 batch 上重訓。
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

---

## 6. 評估（`evaluate.py`、`backtest.py`）

**recall@K 估計：**

- sample 的抽樣率是 p，所以母體排名 K 大約落在 sample 排名 round(K·p)。
- 把 sample 中排名 round(K·p) 的列當邊界，排序是 (score desc, URL 雜湊 asc)。
- golden 列排在邊界或之前就算入選。golden 列不加進 sample。
- p = 1 時，結果等於精確的 top-K（`tests/test_eval_golden_manifest.py`）。

**指標：**

- `indexed_exact_rate`：原始字串完全比對，等於官方 metric。
- `indexed_canonical_rate`：原始或 canonical 拼法有一個入選就算。
- `crawled_exact_rate`、`crawled_canonical_rate`：as-of 時已爬的上限。
- 三種 K：`k_values`、`fractions`（pool 的比例）、`gain_curve_k`。

**信賴區間：** 以 query 為單位的群集 bootstrap。出現在 c 個 query 的 URL，在每個 query 權重 1/c。每個 scorer 都和參考模型做配對差異。

**校準：** Σ(母體機率) / p 對上 pool 中實際的 golden 頁數。

**採用規則（事先寫定）：** 在 t−1 調參、在 t 報告；和最強 baseline 的配對 95% CI 下界 > 0 才採用。

**前瞻評估：** `train` 在 S0 凍結模型；新 batch 出現後，`pull-positives --batches <b>` 再 `forward-eval`。模型的 `snapshot_at` 必須等於 sample 的 S0，batch 必須晚於 S0，否則拒絕執行。

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
| 寫入 crawlerdb（帳號是 superuser） | 每條連線設 `default_transaction_read_only=on`；用戶端 `assert_read_only_sql` 只放行單一讀取語句 |
| 長查詢 | `statement_timeout` 120 秒；sample 每個 shard 一條語句，只讀約 1% 的頁 |
| 伺服器端暫存檔 | 不做 server 端排序或彙總；`temp_file_limit=0`、`work_mem=16MB`、不開平行 worker |
| 搶資源 | 語句之間至少間隔 `pull.min_interval_s` |
| 讀取時機 | **讀 crawlerdb 前要取得助教同意。** 避開量測日（1、16 日 00:00–20:00 UTC）和 accounting/gc 時段（02:50–04:40 UTC） |
| 憑證外洩 | DSN 只放環境變數；所有 JSON 輸出經過 `assert_no_secrets` |

---

## 10. 執行手冊（Host2）

```bash
cd ~/isel/repo && uv sync
set -a; source ~/isel/.env; set +a          # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config config/isel.toml --config config/host.toml"

uv run isel $C pull-golden                  # metricdb only
# --- needs TA approval from here ---
uv run isel $C pull-dims
uv run isel $C pull-positives
uv run isel $C pull-sample --dry-run
uv run isel $C pull-sample                  # ~16.6M rows, resumable per shard
uv run isel $C validate-routing             # expect candidate_rate >= 0.99
uv run isel $C build-dataset
uv run isel $C backtest --feature-sets full,no_inlinks,no_domain,no_history
uv run isel $C train                        # freeze before 2026-10-16 12:00 UTC
# after batch 20 exists:
uv run isel $C pull-golden
uv run isel $C pull-positives --batches 20
uv run isel $C forward-eval --model-dir work/models/<id> --batch 20
```

---

## 11. 已知限制

- **inlinks 膨脹：** 回測的 inlink 特徵在 batch 之後量測，可能被 patrol 膨脹。前瞻評估能排除這點；為了縮短落差，新 batch 出現後要盡快跑 `pull-positives`。
- **block sample：** TABLESAMPLE SYSTEM 以頁為單位抽，同一頁的列常來自同一網域。小網域的 `*_size` 和 `rel_*` 估計較吵。
- **跨 shard 重複：** 同一 URL 出現在兩個 shard 時，sample 排名會算兩次。影響可忽略。
- **拼法：** 清單目前輸出 crawler 的拼法。過去 golden 的原始拼法若和 canonical 不同，exact 比對會漏掉；`indexed_canonical_rate` 顯示這部分的損失。
- **batch 10–17 是空的：** 測試 batch 18 時，最近的訓練 batch 是 3 個月前的 batch 8 或 9。
