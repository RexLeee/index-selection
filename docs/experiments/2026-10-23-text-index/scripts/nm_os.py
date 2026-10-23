"""NanoMIRACL on OpenSearch through the product code (isel.textindex.build and search).

Usage: nm_os.py NM_DIR OUT.parquet VARIANT[,VARIANT...] LANG[,LANG...]
One index per (variant, language), built from NM_DIR/text/{lang} with the analyzer
`text` replaced per variant, searched with the product query over `body`, then dropped.
Writes one row per query: variant, lang, qid, ndcg, found, judged, hits, took_ms.
"""
import copy
import dataclasses
import json
import sys
from pathlib import Path

import pyarrow as pa
from isel.rankeval import ndcg_at_k
from isel.textindex import OpenSearch, build, search

from isel.config import load_config
from isel.io import write_parquet_atomic

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
nm, out = Path(sys.argv[1]), Path(sys.argv[2])
variants, langs = sys.argv[3].split(","), sys.argv[4].split(",")

ICU = json.loads((V / "config/text_index/icu.json").read_text())
LANG_ANALYZER = {"en": "english", "de": "german", "fr": "french", "es": "spanish",
                 "hi": "hindi", "bn": "bengali", "ja": "cjk", "zh": "cjk"}
# icu_folding outside the Indic blocks (U+0900-U+0DFF), which keep their virama and nukta.
INDIC_SAFE = {"type": "icu_folding", "unicode_set_filter": "[^\\u0900-\\u0DFF]"}


def analysis(variant: str, lang: str) -> dict:
    if variant == "standard":
        return {"analyzer": {"text": {"type": "custom", "tokenizer": "standard",
                                      "filter": ["lowercase"]}}}
    if variant == "icu":
        return ICU["settings"]["analysis"]
    if variant == "icu_indic":
        return {"filter": {"fold": INDIC_SAFE}, "analyzer": {"text": {
            "type": "custom", "tokenizer": "icu_tokenizer", "filter": ["fold"]}}}
    if variant == "icu_bigram":
        return {"analyzer": {"text": {"type": "custom", "tokenizer": "icu_tokenizer",
                                      "filter": ["cjk_bigram", "icu_folding"]}}}
    if variant == "lang":
        return {"analyzer": {"text": {"type": LANG_ANALYZER[lang]}}}
    raise SystemExit(f"unknown variant {variant}")


base = load_config([V / "config/isel.toml"])
work = nm / "work"
client = OpenSearch(base.text_index.url, 300.0, 3, 1.0)
rows = []
for variant in variants:
    for lang in langs:
        spec = copy.deepcopy(ICU)
        spec["settings"]["analysis"] = analysis(variant, lang)
        settings_file = nm / "settings" / f"{variant}-{lang}.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(spec, ensure_ascii=False))
        t = dataclasses.replace(base.text_index, alias=f"nm-{variant.replace('_', '-')}-{lang}",
                                settings_file=str(settings_file), fields=["body"],
                                lease_wait_s=1.0, keep_prev=False, warmup_queries=0)
        cfg = dataclasses.replace(base, text_index=t, run=dataclasses.replace(
            base.run, work_dir=str(work)))
        built = build(cfg, client, nm / "text" / lang)
        judged = json.loads((nm / "judged" / f"{lang}.json").read_text())
        for _qid, q in sorted(judged.items())[:10]:   # warm-up, unmeasured
            search(client, t, q["text"])
        for qid, q in sorted(judged.items()):
            ids, took = search(client, t, q["text"])
            relevant = set(q["ids"])
            rows.append({"variant": variant, "lang": lang, "qid": qid,
                         "ndcg": ndcg_at_k(ids, relevant, 10),
                         "found": len(relevant & set(ids[:10])), "judged": len(relevant),
                         "hits": len(ids), "took_ms": took})
        client.call("DELETE", f"/{built['index']}")
        mean = sum(r["ndcg"] for r in rows[-len(judged):]) / len(judged)
        print(json.dumps({"variant": variant, "lang": lang, "ndcg": round(mean, 4),
                          "store_bytes": built["store_bytes"]}), flush=True)
write_parquet_atomic(pa.Table.from_pylist(rows), out)
