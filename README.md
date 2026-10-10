# index-selection

從約 16.6 億個已爬 URL 中，每兩週選出 1.28 億個放進搜尋索引。目標是最大化下一批 golden URL 的 indexed rate。

- 調研（現況、論文、方法選型、未來架構）：[`docs/RESEARCH.md`](docs/RESEARCH.md)
- 設計（時間點、特徵、模型、評估、manifest、recrawl 介面）：[`docs/DESIGN.md`](docs/DESIGN.md)
- 實驗紀錄（真實資料的讀取、回測結果和判定）：[`docs/experiments/`](docs/experiments/)

## 方法一句話

兩層的 golden 傾向模型：網域層用 empirical Bayes 平滑 golden 比例，頁面層用 LightGBM 學同網域內的對比。每個 batch 的候選池和特徵都依 batch 出現前的時間點重建，crawler 從 golden 衍生的欄位一律禁用。

## 里程碑 1：離線研究管線

- 不寫正式 selectdb，不排程。
- 資料來源：metricdb 的 golden、crawlerdb 的 1% block sample 和 golden 主鍵查詢。
- 主要數字：在 2026-10-16 前凍結模型，對之後的新 batch 做前瞻評估。

## 指令

```bash
uv sync
uv run ruff check src tests
uv run pytest                                   # synthetic data, no network
uv run pytest -m integration                    # local postgres:16 container via Docker

set -a; source .env; set +a                     # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config config/isel.toml --config config/host.toml"
uv run isel $C pull-golden
uv run isel $C pull-dims                        # crawlerdb: needs TA approval
uv run isel $C pull-positives
uv run isel $C pull-sample --dry-run            # per-shard pages and timeouts, next no-read window
uv run isel $C pull-sample                      # guarded: read windows + IO load; resumable
uv run isel $C validate-sample                  # share of sample rows fetched more than once
uv run isel $C validate-routing
uv run isel $C build-dataset
uv run isel $C backtest --feature-sets full,no_inlinks,no_domain,no_history
uv run isel $C trends-snapshot --source trendspy  # pseudo golden: free trending pool
uv run isel $C trends-compare --a <id> --b <id>   # trendspy vs SerpApi snapshot
uv run isel $C pseudo-calibrate --batch 19 --tag random --n 100
uv run isel $C pseudo-batch --source official:19 --batch-id 10019 --random 800 --provider serper
uv run isel $C advance-snapshot                 # move S0 if crawlerdb is unchanged
uv run isel $C train                            # M0: official golden only
uv run isel $C --config config/pseudo.toml train  # M1: official + pseudo golden
uv run isel $C forward-eval --model-dir work/models/<M0> --model-dir work/models/<M1> --batch 20
uv run isel $C select-sample --model-dir work/models/<id>
uv run isel $C validate-manifest --run-dir work/selection/run=<id>
```

SERP 指令需要自己的 `ISEL_SERPAPI_KEY`、`ISEL_SERPER_KEY`，不能用課程的 `SERPAPI_KEY`。完整步驟、讀取時段限制和偽 golden 的校準門檻見 `docs/DESIGN.md` §5.1、§9–§10。

## 結構

| 模組 | 職責 |
|---|---|
| `config.py` | 嚴格的 TOML 設定 |
| `pg.py` | 唯讀 session、會照 PostgreSQL 詞法掃描的 SQL gate、限速、讀取時段與 IO 負載防護 |
| `io.py` | 原子寫入、DuckDB 連線、憑證檢查 |
| `urlnorm.py` | canonical URL、shard key、shard 路由 |
| `golden.py` | metricdb → golden 和 trending 池；讀取時可加入偽 golden |
| `pseudo.py` | 偽 golden：trending 快照與比對、官方選 query 規則、SERP client、額度帳本、校準 |
| `pull.py` | crawlerdb → split hosts、sample、golden 查詢 |
| `features.py` | 時間點候選池、特徵登記表、洩漏防護 |
| `dataset.py` | 訓練矩陣、tag 權重 |
| `model.py` | LightGBM、logistic regression、規則 baseline |
| `evaluate.py` | recall@K 估計、bootstrap、校準 |
| `backtest.py` | 回測、凍結訓練、前瞻評估、sample 選取 |
| `manifest.py` | 給未來 indexer 的輸出契約 |
| `cli.py` | 子指令 |
