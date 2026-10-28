# index-selection

從約 16.6 億個已爬 URL 中，每兩週選出 1.28 億個放進搜尋索引。目標是最大化下一批 golden URL 的 indexed rate。

這個 repo 只放中文的調研、設計筆記、實驗紀錄和週報。程式碼不在這裡。

## 程式碼在哪裡

- Repo：[`RexLeee/WebSearchEngine`](https://github.com/RexLeee/WebSearchEngine)，fork 自課程的 `NTU-CSIE5376/WebSearchEngine`。本機 clone 在 `../wse-idxsel`。
- 目錄：`IndexSelectionV2/`。
- 分支：`idxsel/p1-docs` 到 `idxsel/p11-yugabyte`，一個分支一個 PR，後一個疊在前一個上。最新的實作是最上層的分支。PR 合併後以 upstream `main` 為準。
- 權威規格是那邊的英文文件：`docs/DESIGN.md`（含 runbook）、`docs/RECRAWL.md`、`docs/TEXT_INDEX.md`、`docs/BENCHMARK.md`。
- 2026-10-28 以前的程式碼留在這個 repo 的 git 歷史裡。到這一天為止，Host2 的 `~/isel/src-<sha>` 和 selectdb `model_runs.code_version` 記錄的 SHA，都是這個 repo 的 commit。

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | 現況、論文、方法選型、未來架構 |
| [`docs/DESIGN.md`](docs/DESIGN.md) | 設計的中文說明和理由。實作細節以 R2 的 `docs/DESIGN.md` 為準 |
| [`docs/RECRAWL_DESIGN.md`](docs/RECRAWL_DESIGN.md) | recrawl 的容量分析、架構和否決的做法 |
| [`docs/CRAWLER_RECRAWL_PROPOSAL.md`](docs/CRAWLER_RECRAWL_PROPOSAL.md) | 給 crawler 組的提案 |
| [`docs/experiments/`](docs/experiments/) | 真實資料的實驗紀錄。腳本引用當時 commit 的程式碼 |
| [`docs/reports/`](docs/reports/) | 每週的課堂週報 |

## 方法一句話

兩層的 golden 傾向模型：網域層用 empirical Bayes 平滑 golden 比例，頁面層用 LightGBM 學同網域內的對比。每個 batch 的候選池和特徵都依 batch 出現前的時間點重建，crawler 從 golden 衍生的欄位一律禁用。
