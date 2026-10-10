# 實驗紀錄：第一次真實資料回測（M0）

- 日期：2026-10-09 讀取資料；2026-10-10 用修正後的程式重跑回測（時間一律 UTC）
- 程式版本：資料讀取用 commit `32402d1`；§7–§8 的回測用 commit `9ab440a`，它修正了浮點加總（§9.1）
- 執行環境：MacBook，24 GB RAM、10 核，經 CSIE VPN 直連 crawlerdb 和 metricdb
- 套件：Python 3.12.11、LightGBM 4.7.0、DuckDB 1.5.6、pyarrow 25.0.1、psycopg 3.3.6、scikit-learn 1.9.1、numpy 2.5.3
- 設定：只用 `config/isel.toml`。`config_sha256` 開頭是 `ba208bd0a876c75c`，修正前後相同
- 結果檔：`work/backtests/20261010T043436446958Z/`（`results.json`、`report.md`）。修正前的結果在 `work/backtests/20261009T072233578244Z/`。`work/` 不進 git

---

## 1. 結論

1. **LightGBM 有效。** 5 個測試 batch 合併計算，K = 1.28 億時，`lgbm:full` 的 indexed exact rate 是 head 22.49%、random 18.01%。它比所有 baseline 都高，配對 95% CI 全部不含 0。
2. **排名已接近上限。** 上限是「golden URL 在 as-of 前有沒有被爬到」。head 的上限是 18–28%，`lgbm:full` 拿到上限的 94–99%。要再往上提高，得先擴大爬取覆蓋。
3. **網域特徵最重要。** 拿掉網域類特徵，head 少 1.3–1.7 個百分點。拿掉 inlink 類少 0.6–0.9 個百分點。拿掉 URL 歷史，head 只少約 0.1 個百分點，random 沒有顯著差異。
4. **`later_unlabeled` 不採用。** 它在每個測試 batch 只拿掉 0–22 列負例，所有配對 CI 都包含 0。依 DESIGN §5 事先寫定的規則，維持現在的負例定義。
5. **重現性 bug 已在 2026-10-10 修好。** DuckDB 平行加總浮點數有 10⁻¹⁵ 的誤差，會讓 LightGBM 訓練出不同的樹。修正後，同一份資料重跑的結果完全相同。§7–§8 是修正後重跑的數字，和修正前最多差 0.06 個百分點。唯一改變的判斷是：URL 歷史在 head 的差距變得顯著，但只有約 0.1 個百分點（見 §8.1、§9.1）。
6. **比 V1 好，進步大多來自訓練方式。** 只用 V1 的特徵、改用新的訓練和評估方式（`v1_features`），head 就到 20.59%。再加上網域層和 inlink 特徵，又多約 1.9 個百分點（見 §4.3、§8.6）。

---

## 2. 目的

- 把過去官方 golden batch 和對應的 crawlerdb 特徵讀到 Mac。
- 在 Mac 上訓練 M0（只用官方 golden 的 LightGBM），用回測看效果。
- 依事先寫定的規則判定 `later_unlabeled`（DESIGN §5）。

---

## 3. 工作總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| W1 讀取防護 | SQL gate 只放行唯讀語句；讀取時段和 IO 負載檢查；依表大小調整逾時；`frozen_probe` | commit `6ed2ecd` |
| W2 偽 golden | 用 Google Trends 和自己的 SERP key 產生偽標籤的工具 | commit `d05d031`，這次沒有使用 |
| 重現性修正 | train frame 依 (url, shard) 排序，讓 bagging 的結果固定 | commit `f6928f1` |
| 負例稽核 | `build-dataset` 量出「負例之後變成 golden」的比例；新增 `later_unlabeled` 實驗；拼錯的 baseline 名稱直接報錯 | commit `32402d1` |
| 讀取真實資料 | Mac 經 VPN 直連 DB，讀了 golden、正例特徵和 1% sample | 本紀錄 §5，伺服器上沒有寫任何檔案 |
| 回測 | 5 個測試 batch、4 組特徵、7 個 baseline | 本紀錄 §7–§8 |
| 浮點加總修正 | 跨列的浮點加總改用 DECIMAL，讓結果和相加順序無關 | commit `9ab440a`，見 §9.1 |
| 重跑回測 | 用修正後的程式、相同的參數重跑完整回測 | 2026-10-10，§7–§8 的數字來自這次 |

上面的 commit 已在 2026-10-10 push 到 origin。

---

## 4. 方法

### 4.1 資料來源

| 資料 | 來源 | 用途 |
|---|---|---|
| golden | metricdb | 標籤：每個 batch 的 query 和 SerpApi 回傳的 URL |
| 1% block sample | crawlerdb，`TABLESAMPLE SYSTEM` | 負例，以及估計每個 batch 的候選池 |
| 正例特徵 | crawlerdb 主鍵查詢 | golden URL 的原始和 canonical 拼法在 crawlerdb 裡的列 |
| split hosts | crawlerdb | 把 URL 對到 shard |

### 4.2 模型怎麼訓練

**時間點。** 每個 batch b 有一個 as-of 時間：`as_of_b = min(建立時間 − 30 小時, S0)`。
- 候選池是 as-of 前已爬的頁：`last_fetch_ok < as_of_b`。每個 URL 只爬一次，所以現在的爬取狀態可以重建當時的候選池。
- golden 衍生的特徵只用更早、而且在 as-of 前已經可用的 batch。較舊的 batch 依 90 天半衰期降權。

**標籤。** 每個 batch 各自定義標籤：
- **正例：** as-of 前已爬的頁，原始或 canonical 拼法在 golden(b) 裡。
- **負例：** 同一個候選池裡沒進 golden(b) 的頁。從 1% sample 再用 md5 雜湊抽 10%。訓練時加上 case-control offset，校正這兩層抽樣。
- batch 1 沒有更早的 golden 可以算特徵，所以只提供歷史，不當訓練資料。
- head 和 random 兩類 query 的正例，總權重相等。

**特徵（`full`，45 個）。** 模型分兩層：網域層判斷「哪個網域容易有 golden」，頁面層判斷「網域內哪一頁比較可能」。

| 類別 | 數量 | 例子 | 層 |
|---|---|---|---|
| URL 字串 | 14 | `url_len`、`path_depth`、`is_root`、`path_marker` | 頁面 |
| 爬取中繼資料 | 12 | `title_len`、`is_redirect`、`has_json_ld`、`fetch_age_days` | 頁面 |
| inlink | 5 | `log_inlinks`、`log_ext`、`rel_inlinks`、`rel_ext` | 頁面（`rel_*` 是相對同網域平均的值） |
| URL 歷史 | 4 | `hist_decayed`、`hist_head`、`hist_days_since` | 頁面 |
| 網域 | 10 | `lift_tld`、`lift_dkey`、`lift_host`、`lift_hp`、`dem_host`、`log_size_host` | 網域（empirical Bayes 平滑的 golden 比例） |

- empirical Bayes：網域的 golden 比例會向全體平均收縮。網域越小，收縮越多，避免小網域因為一兩次命中就被高估。
- **禁用欄位：** crawler 從 golden 算出來的欄位會把答案洩漏給模型，例如 `url_score`、`domain_score`、`source`、`parent_page_score`。它們列在 `FORBIDDEN`，出現在任何 feature set 都會直接報錯。

**LightGBM 設定。**
- 二元分類，learning rate 0.05，63 個葉節點，`min_data_in_leaf` 200。
- `feature_fraction` 和 `bagging_fraction` 都是 0.8，`lambda_l2` 1.0，`deterministic = true`。
- 用最後一個官方訓練 batch 做 early stopping：最多 3,000 輪，100 輪沒改善就停。
- 停止後用最佳輪數，在全部訓練 batch 上重訓（`refit_on_all`）。這次實際訓練了 149–453 輪。

**評估。** 回測逐批往前推：
1. 測試 batch t 只用 t 之前、而且在 as-of_t 前可用的 batch 訓練。
2. 模型對 t 的候選池打分，模擬取分數最高的 K = 1.28 億頁。sample 率是 1%，所以全體第 1.28 億名約對應 sample 的第 128 萬名。這一列就是門檻：golden 頁的排名在門檻以前，就算被選中（`evaluate.py`）。
3. 計算 indexed exact rate：golden 的原始 URL 字串有多少比例被選中。這和官方 metric 的比對方式相同。
4. 以 query 為單位做配對 bootstrap，算出 95% CI。

### 4.3 和 V1（PR #4）的架構差別

| 面向 | V1 | 這次 |
|---|---|---|
| 特徵 | 56 個 URL 字串特徵，加上 1 個網域頻率 | 45 個特徵，分 5 類，有網域層和頁面層 |
| 實際模型 | learning rate 0.5，`best_iteration = 1`，基本上是一棵樹 | learning rate 0.05，149–453 輪 |
| 負例 | 100 萬個隨機列，包含沒爬過的頁 | 候選池內已爬、但沒進 golden 的頁 |
| 時間切分 | train/test 隨機切 | 只用過去的 batch 預測未來的 batch |
| 洩漏 | 網域頻率直接用 golden 算，沒有排除同一批 | 特徵都在 as-of 計算，洩漏欄位禁用 |
| 拼法 | — | 正例同時比對原始和 canonical 拼法；評估只認原始拼法 |
| K | 3,200 萬 | 1.28 億 |
| 評估 | test AUC 0.784 | 5 個測試 batch 的命中率，附 CI 和 baseline 比較 |
| 對 DB 的動作 | 每次整個覆寫 selectdb | 只讀；結果寫在本機 |

V1 的資料來自 RESEARCH §3.4。`v1_features` 這個 baseline 只用 V1 的特徵（14 個 URL 字串特徵加 `dem_dkey`），但訓練和評估都用這次的方式。它和 `lgbm:full` 的差距就是「只換特徵」帶來的提升（見 §8.6）。

---

## 5. 資料讀取

### 5.1 讀取前的伺服器狀態

| 項目 | 狀態 | 意義 |
|---|---|---|
| crawler 容器 | 從 10-06 停到現在 | `last_fetch_ok` 不會變，候選池可以重建 |
| crawlerdb | 仍是 PostgreSQL，256 張 shard 表都在 | `TABLESAMPLE` 可用 |
| crawlerdb 寫入 | 評分程式正在更新 `url_score`，約每 2–3 分鐘做一次 checkpoint | 列層沒有凍結。`url_score` 被 `FORBIDDEN` 擋在模型外，不影響特徵 |
| Host1 IO PSI avg10 | gc 時段內 45.8%，之後 6.7–14% | 等 04:40 gc 時段結束才開始讀 |

### 5.2 讀取步驟

所有語句都經過 SQL gate、`default_transaction_read_only=on` 和 `ReadGuard`。一次只跑一個指令。

| 指令 | 時間 | 結果 |
|---|---|---|
| `pull-golden` | 04:41，1 分鐘內 | 11 個 batch：1–9、18、19（10–17 是空的） |
| `pull-dims` | 04:41，幾秒 | 48 個 split host，其中 42 個還沒遷移完成 |
| `pull-positives` | 04:42–05:36 | 11 個 batch 全部完成。每個 batch 約 2–2.5 分鐘，其餘時間花在斷線重試 |
| `pull-sample --dry-run` | 05:36 | 見下表 |
| `pull-sample` | 05:39:22–06:25:31 | 256 個 shard 全部完成，16,480,360 列，1.2 GB Parquet，沒有失敗的 shard |

`pull-sample --dry-run` 的估計：

| 項目 | 值 |
|---|---|
| 256 張表的頁數 | 413,558,601 頁（`reltuples` 約 67 億列，含未爬的 URL） |
| 1% 要讀的資料頁 | 33.9 GB |
| 最長逾時 | 170 秒（shard 224，最大的 shard） |
| 逾時超過 120 秒下限的 shard | 5 個 |
| 開始時等待 IO 的連線數 | 2（門檻 15） |

- **S0** 是 `2026-10-09T05:39:22Z`，也就是 `pull-sample` 開始的時間。`frozen_probe` 的 `tuple_changes` 是 738,306,138。
- **讀取速度：** 有效讀取速度約 18.6 MB/s，是保守估計 5 MB/s 的 3.7 倍。pull 在 05:53 停下來等 05:55–06:10 的禁讀時段，扣掉這 17 分鐘，實際讀取約 29 分鐘。
- **對伺服器的影響：** 讀取期間每 10 分鐘量一次 Host1 IO PSI，avg10 介於 6.4% 和 17.9% 之間，和讀取前差不多。停止門檻是 35。

`pull-positives` 的查詢結果（batch 1–3 的統計在第一次斷線時遺失，資料檔完整）：

| batch | 拼法數 | 找到的列 | 已爬的列 | 已爬 / 拼法 |
|---|---|---|---|---|
| 4 | 14,997 | 8,924 | 5,452 | 36.4% |
| 5 | 19,822 | 11,799 | 7,157 | 36.1% |
| 6 | 15,079 | 8,943 | 5,517 | 36.6% |
| 7 | 14,751 | 8,648 | 5,267 | 35.7% |
| 8 | 14,626 | 8,635 | 5,174 | 35.4% |
| 9 | 7,352 | 4,037 | 2,462 | 33.5% |
| 18 | 7,744 | 4,372 | 2,776 | 35.8% |
| 19 | 14,650 | 6,204 | 3,881 | 26.5% |

### 5.3 事件：VPN 重設連線

- **現象：** 04:50–05:15 之間，`pull-positives` 的連線被重設 5 次。伺服器 log 寫「用戶端重設連線」，用戶端則看到「伺服器關閉連線」。postgres 沒有重啟。
- **排除的原因：**
  - 查詢內容：逐條重放 batch 4 的全部查詢，沒有一次斷線。
  - TCP keepalive：分別開、關 keepalive，閒置 45 秒和 90 秒的連線都沒有斷。
  - OpenVPN 重連：VPN log 在這段時間沒有任何事件。
- **判斷：** 兩端都說對方重設，所以重設來自中間的網路設備。05:17 之後沒有再發生。
- **處理：** 用重試迴圈執行長指令。斷線時先等 ping 通，再等 60 秒重跑。pull 會從斷點續傳：`pull-sample` 以 shard 為單位，`pull-positives` 以 batch 為單位。

---

## 6. 資料驗證

| 指令 | 結果 | 判定 |
|---|---|---|
| `validate-sample` | `refetched_share` 1.63%（268,170 / 16,480,360）；`title_null_share` 17.7% | 通過。98% 以上的頁只成功爬過一次 |
| `validate-routing` | `candidate_rate` 0.9996；`primary_rate` 0.9991 | 通過（門檻 0.99） |
| `build-dataset` | 每個官方 batch 都有正例。耗時 87 秒，記憶體峰值 13.0 GB | 通過 |

`build-dataset` 的每批摘要和標籤稽核（DESIGN §5）：

| batch | as_of | golden URL | 候選池估計 | 正例 | canonical-only | 負例（sample） | 之後進 golden | 下一批進 golden | `later_golden_share` | 拼法變體 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 02-26 12:25 | 13,633 | 6,075,300 | 119 | 0 | 60,752 | 9 | 3 | 1.5×10⁻⁴ | 0 |
| 2 | 03-15 06:00 | 14,204 | 27,984,700 | 405 | 2 | 279,843 | 28 | 6 | 1.0×10⁻⁴ | 0 |
| 3 | 03-31 06:00 | 14,273 | 48,258,500 | 664 | 7 | 482,576 | 37 | 4 | 7.7×10⁻⁵ | 0 |
| 4 | 04-15 06:00 | 14,821 | 134,459,700 | 802 | 6 | 1,344,588 | 51 | 12 | 3.8×10⁻⁵ | 0 |
| 5 | 04-30 06:00 | 19,631 | 314,160,500 | 1,227 | 9 | 3,141,591 | 43 | 9 | 1.4×10⁻⁵ | 1 |
| 6 | 05-21 18:00 | 14,918 | 804,662,000 | 2,642 | 15 | 8,046,590 | 77 | 24 | 9.6×10⁻⁶ | 2 |
| 7 | 05-28 18:00 | 14,552 | 956,694,700 | 2,865 | 17 | 9,566,920 | 67 | 20 | 7.0×10⁻⁶ | 2 |
| 8 | 06-04 08:00 | 14,431 | 1,129,897,400 | 3,611 | 35 | 11,298,942 | 57 | 16 | 5.0×10⁻⁶ | 1 |
| 9 | 06-11 08:00 | 7,139 | 1,148,639,800 | 1,589 | 15 | 11,486,380 | 43 | 20 | 3.7×10⁻⁶ | 2 |
| 18 | 09-16 00:09 | 7,671 | 1,223,463,100 | 1,946 | 22 | 12,234,605 | 27 | 27 | 2.2×10⁻⁶ | 1 |
| 19 | 09-30 06:00 | 14,432 | 1,506,221,300 | 2,944 | 27 | 15,062,178 | 0 | — | 0 | 3 |

- **候選池** 從 600 萬頁長到 15 億頁。batch 2–4 的正例只有 405–802 個，因為當時爬到的頁還少。
- **canonical-only 正例** 每批 0–35 個，約佔正例 1%。拼法造成的損失很小。
- **`later_golden_share`** 介於 2×10⁻⁶ 和 1.5×10⁻⁴ 之間，和先前推算的上限 7×10⁻⁵ 同一量級。負例之後變成 golden 的比例極小。
- **拼法變體負例** 每批 0–3 個，可以忽略。

---

## 7. 回測設定

- **指令：**
  ```bash
  uv run isel --config config/isel.toml backtest \
    --feature-sets full,no_inlinks,no_domain,no_history \
    --baselines random,inlinks,ext,domain_prior,logreg,v1_features,later_unlabeled
  ```
- **合併執行：** 計畫原本把主回測、`later_unlabeled` 和消融分成三個指令。這裡合併成一次，因為 train frame 已經依 (url, shard) 排序，而且同一次執行的所有模型共用同一個訓練矩陣。
- **資源：** 耗時 58.5 分鐘（3,510 秒），記憶體峰值 13.0 GB。修正前那次是 50.5 分鐘、14.5 GB。store 建置只多約 30 秒，其餘的時間差來自同時執行的其他程式：當時系統 swap 用了約 10 GB。
- **測試 batch：** 預設的 6、7、8、18、19。每個測試 batch 只用 `available_at` 不晚於它 as-of 的 batch 訓練。
- **負例：** sample 列以 md5 雜湊抽 10%（`neg_rate`），訓練時用 case-control offset 校正。

| 測試 batch | 訓練 batch | 訓練列數 | 正例 | 最佳輪數 | 候選池估計 |
|---|---|---|---|---|---|
| 6 | 2–5 | 527,384 | 3,098 | 149 | 804,662,000 |
| 7 | 2–6 | 1,334,589 | 5,740 | 184 | 956,694,700 |
| 8 | 2–7 | 2,292,988 | 8,605 | 245 | 1,129,897,400 |
| 18 | 2–9 | 4,576,843 | 13,805 | 453 | 1,223,463,100 |
| 19 | 2–9、18 | 5,805,092 | 15,751 | 361 | 1,506,221,300 |

early stopping 都看最後一個官方訓練 batch。`refit_on_all = true`，所以最後會用最佳輪數在全部訓練 batch 上重訓。

---

## 8. 結果

### 8.1 合併 5 個測試 batch（K = 1.28 億，indexed exact rate）

| 模型 | head | 和 `lgbm:full` 的差（95% CI） | random | 和 `lgbm:full` 的差（95% CI） |
|---|---|---|---|---|
| `lgbm:full` | **22.49%**（21.93–23.07%） | — | **18.01%**（17.50–18.51%） | — |
| `later_unlabeled` | 22.48% | −0.06 … +0.04 | 18.01% | −0.05 … +0.05 |
| `lgbm:no_history` | 22.39% | −0.16 … −0.04 | 17.99% | −0.09 … +0.05 |
| `lgbm:no_inlinks` | 21.73% | −0.86 … −0.64 | 17.38% | −0.74 … −0.53 |
| `lgbm:no_domain` | 21.00% | −1.66 … −1.34 | 16.55% | −1.62 … −1.30 |
| `v1_features` | 20.59% | −2.07 … −1.75 | 16.23% | −1.96 … −1.61 |
| `logreg` | 19.86% | −2.84 … −2.41 | 15.78% | −2.43 … −2.03 |
| `domain_prior` | 17.72% | −5.05 … −4.49 | 12.88% | −5.41 … −4.84 |
| `inlinks` | 15.93% | −6.89 … −6.26 | 13.27% | −4.99 … −4.50 |
| `ext` | 14.91% | −7.93 … −7.24 | 11.83% | −6.48 … −5.92 |
| `random` | 2.81% | −20.20 … −19.17 | 2.39% | −16.09 … −15.16 |

CI 是以 query 為單位的配對 bootstrap，重抽 1,000 次。

- `lgbm:no_history` 在 head 的 CI 不含 0，但差距只有約 0.1 個百分點。配對 CI 只重抽 query，不包含 LightGBM 的訓練隨機性。修正前後 `lgbm:full` 自己就變動了 0.02–0.05 個百分點，所以這個差距只能說明 URL 歷史對 head 可能有很小的幫助。

### 8.2 每個測試 batch（K = 1.28 億）

「上限」是 golden URL 在 as-of 前已被爬到的比例，任何排名都不會超過它。

| batch | tag | 上限 | `lgbm:full` | `no_inlinks` | `v1_features` | `domain_prior` | `inlinks` | `random` |
|---|---|---|---|---|---|---|---|---|
| 6 | head | 18.40% | 17.89% | 17.52% | 17.35% | 13.41% | 14.38% | 3.18% |
| 6 | random | 17.11% | 16.47% | 15.92% | 15.92% | 10.72% | 13.65% | 2.47% |
| 7 | head | 20.16% | 19.88% | 19.54% | 18.55% | 16.91% | 14.06% | 2.79% |
| 7 | random | 19.34% | 18.69% | 18.33% | 17.24% | 14.58% | 14.11% | 2.76% |
| 8 | head | 25.70% | 25.17% | 24.54% | 22.97% | 18.72% | 18.31% | 3.04% |
| 8 | random | 24.19% | 23.42% | 22.88% | 20.79% | 16.74% | 17.12% | 2.88% |
| 18 | head | 28.41% | 26.77% | 24.62% | 24.52% | 22.91% | 17.45% | 3.17% |
| 18 | random | 22.41% | 20.56% | 19.56% | 18.34% | 15.24% | 14.73% | 2.68% |
| 19 | head | 26.09% | 24.84% | 23.86% | 21.49% | 19.24% | 16.19% | 2.05% |
| 19 | random | 14.94% | 12.27% | 11.38% | 9.97% | 8.40% | 7.52% | 1.32% |

- 測試 batch 越晚，`lgbm:full` 和 `v1_features` 的差距越大：batch 6 的 head 差 0.5 個百分點，B19 差 3.3 個百分點。
- B18、B19 和上限的差距較大（head 差 1.3–1.6 個百分點）。它們和最近的訓練 batch 相隔約 3 個月，因為 batch 10–17 是空的。

### 8.3 K 的影響（`lgbm:full`）

| batch | tag | K = 3,200 萬 | K = 6,400 萬 | K = 1.28 億 |
|---|---|---|---|---|
| 6 | head | 17.15% | 17.58% | 17.89% |
| 7 | head | 18.81% | 19.66% | 19.88% |
| 8 | head | 23.83% | 24.51% | 25.17% |
| 18 | head | 24.19% | 25.64% | 26.77% |
| 19 | head | 23.17% | 24.02% | 24.84% |
| 19 | random | 9.93% | 10.95% | 12.27% |

K 從 1.28 億減半到 6,400 萬，head 只少 0.2–1.1 個百分點。大部分命中集中在排名最前面的 3,200 萬頁。

### 8.4 特徵重要度（`lgbm:full`，gain 佔前 15 名總和的比例）

| 測試 batch | 前 5 名 |
|---|---|
| 6 | `rel_ext` 53%、`log_inlinks` 12%、`lift_hp` 8%、`dem_host` 7%、`rel_inlinks` 5% |
| 7 | `rel_ext` 51%、`lift_hp` 12%、`dem_host` 10%、`rel_inlinks` 9%、`log_size_host` 4% |
| 8 | `rel_ext` 47%、`lift_hp` 14%、`rel_inlinks` 14%、`dem_host` 7%、`hist_decayed` 4% |
| 18 | `rel_ext` 43%、`lift_hp` 15%、`rel_inlinks` 12%、`hist_decayed` 12%、`dem_host` 5% |
| 19 | `rel_ext` 42%、`lift_hp` 16%、`hist_decayed` 12%、`rel_inlinks` 11%、`dem_host` 5% |

- `rel_ext`（同網域內的相對外部 inlink 數）的 gain 最高。可是拿掉全部 inlink 類特徵，head 只少 0.6–0.9 個百分點，因為網域特徵能替代大部分資訊。
- `hist_decayed` 在 B18、B19 佔 12%。可是拿掉 URL 歷史，合併的 head 只少約 0.1 個百分點，random 沒有顯著差異。其他特徵可以補上大部分的資訊。

### 8.5 `later_unlabeled` 判定

規則（DESIGN §5，事先寫定）：每個 tag 的 `later_unlabeled − lgbm:full` 配對 95% CI 下界都 > 0 才採用。拿掉的列很少時，即使通過也視為訓練隨機性，不採用。

| 範圍 | tag | `lgbm:full` | `later_unlabeled` | 差的 95% CI |
|---|---|---|---|---|
| 合併 | head | 22.49% | 22.48% | −0.06 … +0.04 |
| 合併 | random | 18.01% | 18.01% | −0.05 … +0.05 |
| B18 | head | 26.77% | 26.83% | −0.14 … +0.27 |
| B18 | random | 20.56% | 20.56% | −0.22 … +0.20 |
| B19 | head | 24.84% | 24.76% | −0.22 … +0.06 |
| B19 | random | 12.27% | 12.17% | −0.26 … +0.06 |

| 測試 batch | 拿掉的負例 | `lgbm:full` 最佳輪數 | `later_unlabeled` 最佳輪數 |
|---|---|---|---|
| 6 | 0 | 149 | 149 |
| 7 | 1 | 184 | 199 |
| 8 | 8 | 245 | 255 |
| 18 | 16 | 453 | 551 |
| 19 | 22 | 361 | 238 |

- **判定：不採用。** 所有 CI 下界都 < 0，而且每個測試 batch 只拿掉 0–22 列（佔訓練列數 0.0004% 以下）。修正前後的判定相同。
- 只拿掉 16 列，B18 的最佳輪數就從 453 變成 551。B19 拿掉 22 列，最佳輪數從 361 變成 238。這印證了規則的前提：列數改變會讓 bagging 抽到不同的列，所以小差異只是訓練隨機性。

### 8.6 和 V1 的比較

| 比較 | head | random | 說明 |
|---|---|---|---|
| `lgbm:full` − `v1_features`（合併） | +1.90 個百分點 | +1.78 個百分點 | 只差在特徵，CI 都不含 0（§8.1） |
| `v1_features` − `random`（合併） | +17.78 個百分點 | +13.84 個百分點 | 只用 V1 的特徵，新的訓練方式就大幅領先 |
| B18：`lgbm:full` | 26.77% | 20.56% | V1 自己報告 B18 約 14.7%（只算已爬部分） |

- V1 的 14.7% 和這次的 K、計算方式都不同，只能當參考，不能直接相減。
- 進步的主要來源是訓練方式：時間切分、as-of 特徵、正確的負例和多輪 boosting。特徵帶來的提升比較小，約 1.8–1.9 個百分點。
- 測試 batch 越晚，特徵的貢獻越大：batch 6 的 head 差 0.5 個百分點，B19 差 3.3 個百分點（§8.2）。

---

## 9. 已知問題與限制

### 9.1 浮點彙總不確定（2026-10-10 已修）

- **現象：** 只跑 B19 的那次回測（`20261009T063122120231Z`）停在第 441 輪，head 24.74%、random 12.23%。完整回測的 B19 停在第 362 輪，head 24.77%、random 12.19%。
- **原因：** 用兩個獨立的 store 建 batch 18 的 train frame，列順序完全相同，但有 9 個特徵欄的值不同。這 9 欄都是 DuckDB 平行彙總算出的浮點數，平行加總的順序不固定：

  | 欄位 | 不同的列 | 最大絕對差 | 轉成 float32 後仍不同的列 |
  |---|---|---|---|
  | `rel_inlinks` | 549,196 | 4.9×10⁻¹⁵ | 400 |
  | `rel_ext` | 132,135 | 1.8×10⁻¹⁵ | 39 |
  | `lift_*`、`dem_*`、`hist_decayed` | 10–856,366 | ≤ 1.1×10⁻¹⁵ | 0 |

  `rel_*` 仍不同的列，正確值應該是 0，但兩次分別算出 0 和 ±4×10⁻¹⁶。LightGBM 會把 0 單獨放一個 bin，所以這些列落進不同的 bin，長出不同的樹。
- **影響：** 兩次的差距約 0.04 個百分點，遠小於 CI 寬度。同一次執行的模型共用同一個矩陣，所以模型之間的比較仍然有效。
- **修法：** `features.py` 有 6 處跨列的浮點加總，現在都先轉 `DECIMAL(38, 18)` 再加。DuckDB 的 DECIMAL 加總是整數運算，結果和相加順序無關。
  - `rel_*` 改用 DECIMAL 算 `(n·x − Σx) / n`，不再減浮點平均。頁面值等於網域平均時，結果正好是 0。
  - `lvl_*`、`dem_*`、`glob_stats` 和 `hist_decayed` 的加權加總也改用 DECIMAL。
  - `dkey_ext_share` 不用改。它加總的是 0 和 1，DOUBLE 加總整數本來就精確。
- **單元測試：** 新增 2 個測試。舊程式連跑 3 次都失敗，新程式通過。
  - `test_training_frames_do_not_depend_on_the_order_of_crawl_rows`：把 sample 檔的列順序反轉，用 8 個 thread 建 store。train frame 的每一欄都要相同。這個測試取代了只比對 URL 順序的舊測試。
  - `test_relative_inlinks_are_zero_for_pages_at_the_domain_mean`：網域內每頁的 inlink 都相同時，`rel_*` 要正好是 0。
- **真實資料驗證：** 用 batch 18 的 train frame 檢查，共 1,228,249 列。

  | 檢查 | 結果 |
  |---|---|
  | 兩個新版 store 的 train frame | 每一欄都完全相同 |
  | 和舊版相比的列順序 | 相同 |
  | 和舊版相比的數值 | 最大差 9.3×10⁻¹⁵ |
  | 正好是 0 的列（舊版 → 新版） | `rel_inlinks` 109,203 → 109,855；`rel_ext` 569,362 → 569,372 |
  | store 建置時間 | 93 秒 → 119–125 秒 |

- **B19 回測：** 用新版程式連跑兩次（`20261010T025206936098Z`、`20261010T025722024039Z`），`results.json` 和 `report.md` 完全相同。

  | 執行 | best_iteration | head | random |
  |---|---|---|---|
  | 舊版，只跑 B19 | 441 | 24.74% | 12.23% |
  | 舊版，完整回測 | 362 | 24.77% | 12.19% |
  | 新版，兩次都是 | 361 | 24.84% | 12.27% |

- **完整回測：** 用相同參數重跑（修正前 `20261009T072233578244Z`，修正後 `20261010T043436446958Z`）。
  - 8 個模型和 baseline 在每個 batch 的數字都完全相同：`lgbm:no_inlinks`、`lgbm:no_domain`、`v1_features`、`logreg`、`domain_prior`、`inlinks`、`ext`、`random`。
  - 只有 3 個模型的數字有變，變動 ≤ 0.06 個百分點：

    | 模型 | head（合併） | random（合併） |
    |---|---|---|
    | `lgbm:full` | 22.47% → 22.49% | 17.96% → 18.01% |
    | `later_unlabeled` | 22.45% → 22.48% | 17.95% → 18.01% |
    | `lgbm:no_history` | 22.42% → 22.39% | 17.98% → 17.99% |

  - 完整回測裡的 B19，和上面兩次只跑 B19 的結果完全相同。
  - 判斷只改了一項：`lgbm:no_history` 在 head 的配對 CI 從 −0.11 … +0.01 變成 −0.16 … −0.04，不再包含 0（見 §8.1）。

### 9.2 其他限制

- **inlink 在 batch 之後才量：** 回測用的 inlink 數是 2026-10-09 量的，可能被事後補爬灌高。`no_inlinks` 只少 0.6–0.9 個百分點，所以這個偏差最多就是這麼大。batch 20 的前瞻評估能完全排除它。
- **crawlerdb 列層沒有凍結：** 評分程式持續更新 `url_score`，所以 `frozen_probe` 會改變，`advance-snapshot` 會拒絕移動 S0。M0 要直接用 S0 = 10-09 凍結，這符合 10-15 06:00 的期限。
- **batch 10–17 是空的：** B18、B19 的最近訓練資料是 3 個月前的 batch 8、9，所以它們離上限較遠。

---

## 10. 重現步驟

```bash
R="<repo path>"
set -a; source "$R/.env"; set +a       # ISEL_CRAWLERDB_DSN, ISEL_METRICDB_DSN
C="--config $R/config/isel.toml"
uv --directory "$R" run isel $C pull-golden
uv --directory "$R" run isel $C pull-dims
uv --directory "$R" run isel $C pull-positives          # rerun after a dropped connection
uv --directory "$R" run isel $C pull-sample --dry-run
uv --directory "$R" run isel $C pull-sample             # rerun until shards_missing = 0
uv --directory "$R" run isel $C validate-sample
uv --directory "$R" run isel $C validate-routing
uv --directory "$R" run isel $C build-dataset
uv --directory "$R" run isel $C backtest \
  --feature-sets full,no_inlinks,no_domain,no_history \
  --baselines random,inlinks,ext,domain_prior,logreg,v1_features,later_unlabeled
```

- 讀取時段和 Mac 的執行細節見 DESIGN §9、§10.1。
- 重抽 sample 的結果會和這次不同。爬蟲恢復後，S0 的爬取狀態就再也抽不到了。
- §7–§8 的數字來自 commit `9ab440a`。用同一份 `work/` 資料和同一個 commit 重跑，結果完全相同（§9.1）。

---

## 11. 下一步

1. 用 S0 = 10-09 的 sample 跑 `train`，凍結 M0。期限是 2026-10-15 06:00 UTC。§9.1 的修正已完成，凍結的模型可以重現。
2. batch 20 在 10-16 建立。之後跑 `pull-golden`、`pull-positives --batches 20`，再跑 `forward-eval`。
3. 偽 golden（M1）要等 M0 凍結之後，另外決定要不要做。
