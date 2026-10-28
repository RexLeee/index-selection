"""NanoMIRACL with body `index_options: freqs` against the recorded `icu` runs.

Usage: nm_freqs_check.py NM_DIR RECORDED.parquet OUT.json
One index per language with config/text_index/icu.json plus body freqs, built and
searched through the product code with the query over `body` alone, as nm_os.py did.
Each question's nDCG@10 and found count are compared with its recorded `icu` row.
"""
import dataclasses
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
from isel.rankeval import ndcg_at_k
from isel.textindex import OpenSearch, build, search

from isel.config import load_config

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
nm, recorded_file, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
recorded = {(r["lang"], r["qid"]): r for r in pq.read_table(recorded_file).to_pylist()
            if r["variant"] == "icu"}
spec = json.loads((V / "config/text_index/icu.json").read_text())
spec["mappings"]["properties"]["body"]["index_options"] = "freqs"
settings_file = nm / "settings" / "icu-freqs.json"
settings_file.write_text(json.dumps(spec, ensure_ascii=False))

base = load_config([V / "config/isel.toml"])
client = OpenSearch(base.text_index.url, 300.0, 3, 1.0)
result = {"settings": "icu.json + body index_options freqs", "langs": {}}
for lang in sorted({lang for lang, _ in recorded}):
    t = dataclasses.replace(base.text_index, alias=f"nmf-{lang}", fields=["body"],
                            settings_file=str(settings_file), lease_wait_s=90.0,
                            keep_prev=False, warmup_queries=0)
    cfg = dataclasses.replace(base, text_index=t,
                              run=dataclasses.replace(base.run, work_dir=str(nm / "work")))
    built = build(cfg, client, nm / "text" / lang)
    judged = json.loads((nm / "judged" / f"{lang}.json").read_text())
    same, changed, total = 0, [], 0.0
    try:
        for qid, q in sorted(judged.items()):
            ids, _ = search(client, t, q["text"])
            ndcg = ndcg_at_k(ids, set(q["ids"]), 10)
            total += ndcg
            if ndcg == recorded[(lang, qid)]["ndcg"]:
                same += 1
            else:
                changed.append({"qid": qid, "was": recorded[(lang, qid)]["ndcg"], "now": ndcg})
    finally:
        client.call("DELETE", f"/{built['index']}")
    result["langs"][lang] = {"questions": len(judged), "same_ndcg": same, "changed": changed,
                             "ndcg": total / len(judged),
                             "recorded_ndcg": sum(recorded[(lang, q)]["ndcg"] for q in judged)
                             / len(judged)}
    print(json.dumps({lang: {k: v for k, v in result["langs"][lang].items()
                             if k != "changed"}}), flush=True)
result["same_ndcg"] = sum(v["same_ndcg"] for v in result["langs"].values())
result["questions"] = sum(v["questions"] for v in result["langs"].values())
out.write_text(json.dumps(result, indent=1))
print(json.dumps({"same_ndcg": result["same_ndcg"], "questions": result["questions"]}))
