"""Turns NanoMIRACL splits into TEXT_SCHEMA Parquet and judgments; checks the metric gate.

Usage: nm_prepare.py DATA_DIR OUT_DIR LANG [LANG ...]
For each language: OUT/text/{lang}/corpus.parquet (body = the passage, which starts with
its article title), OUT/judged/{lang}.json ({query_id: {"text", "ids"}}), with ids in
the index's form str(md5_number_lower("miracl:{lang}:{docid}")).
Gate: nDCG@10 of the split's own BM25 run, computed with isel.rankeval.ndcg_at_k, must
equal the published value within 1e-4.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from isel.rankeval import ndcg_at_k

from isel.io import write_parquet_atomic
from isel.recrawl import TEXT_SCHEMA
from isel.urlnorm import md5_number_lower

data, out, langs = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
FETCHED = datetime(2026, 10, 23, tzinfo=UTC)


def doc_id(lang: str, docid: str) -> int:
    return md5_number_lower(f"miracl:{lang}:{docid}")


gate = {}
for lang in langs:
    corpus = pq.read_table(data / "corpus" / f"{lang}.parquet").to_pylist()
    rows = [{"doc_id": doc_id(lang, d["_id"]), "batch_id": 0,
             "url": f"miracl://{lang}/{d['_id']}", "final_url": None, "fetched_at": FETCHED,
             "http_status": 200, "lang": lang, "title": None, "description": None,
             "body": d["text"], "text_len": len(d["text"]), "payload_sha1": ""} for d in corpus]
    write_parquet_atomic(pa.Table.from_pylist(rows, schema=TEXT_SCHEMA),
                         out / "text" / lang / "corpus.parquet")
    queries = {q["_id"]: q["text"] for q in pq.read_table(data / "queries" / f"{lang}.parquet")
               .to_pylist()}
    relevant: dict[str, set[str]] = {}
    for r in pq.read_table(data / "qrels" / f"{lang}.parquet").to_pylist():
        relevant.setdefault(r["query-id"], set()).add(r["corpus-id"])
    judged = {q: {"text": queries[q], "ids": sorted(str(doc_id(lang, d)) for d in ids)}
              for q, ids in relevant.items()}
    (out / "judged").mkdir(parents=True, exist_ok=True)
    (out / "judged" / f"{lang}.json").write_text(json.dumps(judged, ensure_ascii=False))
    runs = {r["query-id"]: list(r["corpus-ids"])
            for r in pq.read_table(data / "bm25" / f"{lang}.parquet").to_pylist()}
    ours = sum(ndcg_at_k(runs.get(q, []), ids, 10) for q, ids in relevant.items()) / len(relevant)
    published = json.loads((data / "metadata" / f"{lang}.json").read_text())["bm25"]["ndcg_at_10"]
    gate[lang] = {"docs": len(rows), "queries": len(judged), "ours": round(ours, 6),
                  "published": round(published, 6), "pass": abs(ours - published) <= 1e-4}
print(json.dumps(gate, indent=1))
sys.exit(0 if all(g["pass"] for g in gate.values()) else 1)
