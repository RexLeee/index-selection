# index-selection

從約 16.6 億個已爬 URL 中，每兩週選出 1.28 億個放進搜尋索引。目標是最大化下一批 golden URL 的 indexed rate。

- 調研（現況、論文、方法選型、未來架構）：[`docs/RESEARCH.md`](docs/RESEARCH.md)
- 設計（時間點、特徵、模型、評估、manifest、recrawl 介面）：[`docs/DESIGN.md`](docs/DESIGN.md)

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

set -a; source .env; set +a                     # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config config/isel.toml --config config/host.toml"
uv run isel $C pull-golden
uv run isel $C pull-dims                        # crawlerdb: needs TA approval
uv run isel $C pull-positives
uv run isel $C pull-sample --dry-run
uv run isel $C pull-sample
uv run isel $C validate-routing
uv run isel $C build-dataset
uv run isel $C backtest --feature-sets full,no_inlinks,no_domain,no_history
uv run isel $C train
uv run isel $C forward-eval --model-dir work/models/<id> --batch 20
uv run isel $C select-sample --model-dir work/models/<id>
uv run isel $C validate-manifest --run-dir work/selection/run=<id>
```

完整步驟和讀取時段限制見 `docs/DESIGN.md` §9–§10。

## 結構

| 模組 | 職責 |
|---|---|
| `config.py` | 嚴格的 TOML 設定 |
| `pg.py` | 唯讀 session、SQL 白名單、限速 |
| `io.py` | 原子寫入、DuckDB 連線、憑證檢查 |
| `urlnorm.py` | canonical URL、shard key、shard 路由 |
| `golden.py` | metricdb → golden |
| `pull.py` | crawlerdb → split hosts、sample、golden 查詢 |
| `features.py` | 時間點候選池、特徵登記表、洩漏防護 |
| `dataset.py` | 訓練矩陣、tag 權重 |
| `model.py` | LightGBM、logistic regression、規則 baseline |
| `evaluate.py` | recall@K 估計、bootstrap、校準 |
| `backtest.py` | 回測、凍結訓練、前瞻評估、sample 選取 |
| `manifest.py` | 給未來 indexer 的輸出契約 |
| `cli.py` | 子指令 |
