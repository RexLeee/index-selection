# 2026-10-23 全文索引實驗：腳本與結果

這個目錄保存 WSE `IndexSelectionV2/docs/TEXT_INDEX.md` 引用的實驗腳本和結果。

- 課程 repo 只記錄每個腳本的 sha256 前綴。`scripts/` 裡的檔案和那張表一致。
- 這裡不放資料。NanoMIRACL 的 card 沒有寫授權（HF `hakari-bench/NanoMIRACL`，revision `ea97b8c`），5 萬頁的正文 Parquet 也沒有放進來。
- golden batch 20 抓下來的 10,345 頁正文也沒有放進來。每個 URL 的抓取紀錄（5.5 MB）留在 scratchpad，這裡只放彙總。
- 腳本裡的路徑指向 2026-10-23 session 的 scratchpad 和 `wse-idxsel` worktree。重跑前要先改路徑。

| 路徑 | 內容 |
|---|---|
| `scripts/` | 18 個腳本和 1 份調參規則（`tune_rule.md`）。用途見 TEXT_INDEX.md 的 Reproduction 表 |
| `inputs/selected.txt` | 從 Host2 唯讀串流的 36 個 crawl_result 檔 |
| `inputs/pull_50k.log` | 每個檔的 sha256 比對結果和筆數 |
| `results/nanomiracl/analysis.json` | 預先登記的主要比較和所有變體 |
| `results/nanomiracl/analysis_final.json` | 用最終版程式重跑 `icu` 後的主要比較 |
| `results/nanomiracl/sensitivity.json` | 修正文字分類後的權重重算 |
| `results/c50k/` | 大小變體、語料統計、128M 推估、排序一致性、golden eval |
| `results/c50k/*_before_*` | 修正前的量測，當作問題的證據：merge 後沒有 refresh，以及沒有 tie-break |
| `results/golden20/fetch_summary.json` | 抓取 golden URL 的結果分布、被 robots.txt 擋的 host、正文長度 |
| `results/golden20/golden20_trial_*.json` | 500 個 URL 的試跑 |
| `results/golden20/golden20_eval.json`、`golden20_analysis.json` | 第一輪 7 個變體，以及 `icu` 的細分和漏掉的例子 |
| `results/golden20/tune*.json` | 調參：所有候選（`tune.json`）、dev 的挑選、test 的唯一比較（`tune_test_confirm.json`）、全集的描述數字、配對的進出 |
| `results/golden20/size_c50k_url.json` | `url.text` 在 44k 爬蟲頁面上的大小，以及 128M 的推估 |
| `results/golden20/cli_build.json`、`cli_eval.json` | 用新設定跑 `text-index-build` 和 `text-index-eval` 的輸出 |
