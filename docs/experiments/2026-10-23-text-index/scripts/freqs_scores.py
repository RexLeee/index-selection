"""Where body `freqs` changes a score: explains the top hit of a few keywords on the live
index and on a freqs build of the same text, and measures every score difference.

Usage: freqs_scores.py TEXT_ROOT WORK_DIR OUT.json
"""
import dataclasses
import json
import sys
from pathlib import Path

import pyarrow.compute as pc
from isel.textindex import OpenSearch, build, query_body

from isel.config import load_config
from isel.golden import read_golden

V = Path("/Users/rex/ntu/碩二/二上/系統架構師的鍛造與培育/wse-idxsel/IndexSelectionV2")
root, work, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
base = load_config([V / "config/isel.toml"])
live = base.text_index
client = OpenSearch(live.url, 600.0, 5, 2.0)
keywords = sorted(set(read_golden(V / "work").filter(pc.field("batch_id") == 20)
                      .column("keyword").to_pylist()))
spec = json.loads((V / live.settings_file).read_text())
spec["mappings"]["properties"]["body"]["index_options"] = "freqs"
settings_file = work / "settings" / "scores_freqs.json"
settings_file.parent.mkdir(parents=True, exist_ok=True)
settings_file.write_text(json.dumps(spec, ensure_ascii=False))
t = dataclasses.replace(live, alias="chk-scores", keep_prev=False,
                        settings_file=str(settings_file))
cfg = dataclasses.replace(base, text_index=t,
                          run=dataclasses.replace(base.run, work_dir=str(work)))
manifest = build(cfg, client, root)


def top(alias: str, kw: str) -> list[tuple[str, float]]:
    reply = client.call("POST", f"/{alias}/_search", query_body(live, kw))
    return [(h["_id"], h["sort"][0]) for h in reply["hits"]["hits"]]


def field_scores(alias: str, kw: str, doc: str) -> dict:
    body = {"query": query_body(live, kw)["query"]}
    ex = client.call("POST", f"/{alias}/_explain/{doc}", body)["explanation"]
    # dis_max: one detail per field that matched, each "sum of" its terms' weights.
    return {d["description"][:60]: d["value"] for d in ex.get("details", [])}


diffs, examples = [], []
for kw in keywords:
    a, b = top(live.alias, kw), top(t.alias, kw)
    for (ia, sa), (ib, sb) in zip(a, b, strict=True):
        assert ia == ib, kw
        if sa != sb:
            diffs.append(abs(sa - sb) / max(abs(sa), 1e-30))
            if len(examples) < 3:
                examples.append({"keyword": kw, "doc": ia, "live": sa, "freqs": sb,
                                 "live_fields": field_scores(live.alias, kw, ia),
                                 "freqs_fields": field_scores(t.alias, kw, ia)})
client.call("DELETE", f"/{manifest['index']}")
diffs.sort()
result = {"scores_compared": sum(len(top(live.alias, kw)) for kw in keywords[:0]),
          "scores_differing": len(diffs),
          "relative_diff_max": diffs[-1] if diffs else 0.0,
          "relative_diff_median": diffs[len(diffs) // 2] if diffs else 0.0,
          "examples": examples}
out.write_text(json.dumps(result, indent=1, ensure_ascii=False))
print(json.dumps(result, indent=1, ensure_ascii=False))
