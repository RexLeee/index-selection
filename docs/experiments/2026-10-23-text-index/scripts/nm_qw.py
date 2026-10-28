"""NanoMIRACL on Quickwit 0.9.3, scored with the same isel.rankeval code as OpenSearch.

Usage: nm_qw.py NM_DIR OUT.parquet BASE_URL TOKENIZER[,TOKENIZER...] LANG[,LANG...]
One index per (tokenizer, language) over `body`, searched with BM25 (`sort_by: _score`;
Quickwit sorts by doc id otherwise), then dropped. The query is the question split on
whitespace, each piece quoted (so query syntax in the text cannot break the query) and
joined by OR. With chinese_compatible each Han character becomes its own quoted piece,
as the tokenizer indexes it; a quoted run of them would be an exact phrase.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from isel.io import write_parquet_atomic
from isel.rankeval import ndcg_at_k

nm, out, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
tokenizers, langs = sys.argv[4].split(","), sys.argv[5].split(",")
# chinese_compatible makes each Han character a token; kana and Hangul runs stay whole
# words that end at the next Han character or punctuation (probed on 0.9.3).
HAN = re.compile("([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])")
CHUNK_BYTES = 5_000_000


def call(method: str, path: str, body: object = None, ndjson: bytes | None = None) -> dict:
    data = ndjson if ndjson is not None else (None if body is None else json.dumps(body).encode())
    ctype = "application/x-ndjson" if ndjson is not None else "application/json"
    req = urllib.request.Request(f"{base}{path}", data=data, method=method,
                                 headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def pieces(text: str, tokenizer: str) -> list[str]:
    out_: list[str] = []
    for word in text.replace('"', " ").replace("\\", " ").split():
        parts = HAN.split(word) if tokenizer == "chinese_compatible" else [word]
        out_ += [p for p in parts if any(ch.isalnum() for ch in p)]
    return out_


def query(text: str, tokenizer: str) -> str:
    return " OR ".join(f'"{p}"' for p in pieces(text, tokenizer))


rows = []
for tokenizer in tokenizers:
    for lang in langs:
        index = f"nm-{tokenizer.replace('_', '-')}-{lang}"
        call("POST", "/api/v1/indexes", {
            "version": "0.9", "index_id": index,
            "doc_mapping": {"mode": "strict", "field_mappings": [
                {"name": "id", "type": "text", "tokenizer": "raw", "stored": True},
                {"name": "body", "type": "text", "tokenizer": tokenizer, "record": "position",
                 "fieldnorms": True, "stored": False}]},
            "search_settings": {"default_search_fields": ["body"]},
            "indexing_settings": {"commit_timeout_secs": 1}})
        docs = pq.read_table(nm / "text" / lang / "corpus.parquet",
                             columns=["doc_id", "body"]).to_pylist()
        lines = [json.dumps({"id": str(d["doc_id"]), "body": d["body"]},
                            ensure_ascii=False).encode() + b"\n" for d in docs]
        chunk: list[bytes] = []
        for n, line in enumerate(lines):
            chunk.append(line)
            if sum(map(len, chunk)) > CHUNK_BYTES or n == len(lines) - 1:
                last = n == len(lines) - 1
                call("POST", f"/api/v1/{index}/ingest?commit={'force' if last else 'auto'}",
                     ndjson=b"".join(chunk))
                chunk = []
        stats = call("GET", f"/api/v1/indexes/{index}/describe")
        assert stats["num_published_docs"] == len(docs), stats
        judged = json.loads((nm / "judged" / f"{lang}.json").read_text())
        errors = 0
        for warm, (qid, q) in [(True, kv) for kv in sorted(judged.items())[:10]] + \
                [(False, kv) for kv in sorted(judged.items())]:
            text = query(q["text"], tokenizer)
            ids, took = [], 0.0
            if text:
                try:
                    reply = call("POST", f"/api/v1/{index}/search", {
                        "query": text, "max_hits": 10, "sort_by": "_score",
                        "search_field": "body"})
                    ids = [h["id"] for h in reply["hits"]]
                    took = reply["elapsed_time_micros"] / 1000
                except urllib.error.HTTPError:
                    errors += 1
            if warm:
                continue
            relevant = set(q["ids"])
            rows.append({"variant": f"qw_{tokenizer}", "lang": lang, "qid": qid,
                         "ndcg": ndcg_at_k(ids, relevant, 10),
                         "found": len(relevant & set(ids[:10])), "judged": len(relevant),
                         "hits": len(ids), "took_ms": took})
        call("DELETE", f"/api/v1/indexes/{index}")
        mean = sum(r["ndcg"] for r in rows[-len(judged):]) / len(judged)
        print(json.dumps({"variant": f"qw_{tokenizer}", "lang": lang, "ndcg": round(mean, 4),
                          "store_bytes": stats["size_published_splits"],
                          "query_errors": errors}), flush=True)
write_parquet_atomic(pa.Table.from_pylist(rows), out)
