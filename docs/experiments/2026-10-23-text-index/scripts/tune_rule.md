# Tuning rule for the golden batch 20 check (written 2026-10-23 10:26 UTC, before any split numbers)

Index: the 10,345 fetched golden pages plus the 44,028 crawl_result pages (`bg44k`).
Metric: recall given present = found pairs / pairs whose page is in the index, top 10.
Baseline: the committed config, `icu.json` with `combined_fields` over `title^2`,
`description`, `body`.

- **Split.** A keyword is in `test` if `int(sha256(keyword.encode()).hexdigest(), 16) % 2`
  is 1, else in `dev`. Both query ids of a keyword (head and random) fall in the same half.
- **Selection on dev only.** Query candidates: `combined_fields` with `title^3`;
  `most_fields` with title weight 1, 2 or 3; `most_fields` with `description^0.5`;
  `best_fields`. Index candidates, each queried with the best query on dev:
  `kstem` or `porter_stem` after `icu_folding`; English-stemmed `title.en` and
  `description.en` subfields (ICU + folding + `porter_stem`); URL tokens as `url.text`.
  An index candidate must add at most 10 % bytes per doc. The winners of the query,
  stemming and URL dimensions are combined, and the combination is checked on dev.
- **Confirmation on test, one comparison.** Final config minus baseline. Adopt if the
  95 % interval of the paired bootstrap (resampling keywords' query ids, 10,000 draws,
  seed 20) has a lower bound above 0, and no keyword script group has its interval
  entirely below 0. If it fails, the query-only change is the second and last test
  comparison.
- **NanoMIRACL guard** for a change of the main analyzer: weighted nDCG@10 interval lower
  bound ≥ −0.01, and no language with its interval entirely below 0.
