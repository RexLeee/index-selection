"""Score differences between the live index and a body-freqs build, at 10 and 11 hits.

Usage: freqs_scores2.py TEXT_ROOT WORK_DIR OUT.json
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
settings_file = work / "settings" / "scores2_freqs.json"
settings_file.write_text(json.dumps(spec, ensure_ascii=False))
t = dataclasses.replace(live, alias="chk-scores2", keep_prev=False,
                        settings_file=str(settings_file))
cfg = dataclasses.replace(base, text_index=t,
                          run=dataclasses.replace(base.run, work_dir=str(work)))
manifest = build(cfg, client, root)
segments = client.call("GET", f"/{manifest['index']}/_segments")


def top(alias: str, kw: str, size: int) -> list[tuple[str, float]]:
    reply = client.call("POST", f"/{alias}/_search", {**query_body(live, kw), "size": size})
    return [(h["_id"], h["sort"][0]) for h in reply["hits"]["hits"]][:10]


pairs = {"live10_vs_freqs10": ((live.alias, 10), (t.alias, 10)),
         "live11_vs_freqs11": ((live.alias, 11), (t.alias, 11)),
         "freqs10_vs_freqs11": ((t.alias, 10), (t.alias, 11))}
result = {"segments": manifest["segments"], "docs": manifest["docs"]}
for name, (a, b) in pairs.items():
    same_ids = same_scores = 0
    worst = 0.0
    for kw in keywords:
        x, y = top(a[0], kw, a[1]), top(b[0], kw, b[1])
        same_ids += [i for i, _ in x] == [i for i, _ in y]
        same_scores += x == y
        for (_, sx), (_, sy) in zip(x, y, strict=False):
            worst = max(worst, abs(sx - sy) / max(abs(sx), 1e-30))
    result[name] = {"same_ids": same_ids, "same_scores": same_scores,
                    "max_relative_diff": worst}
client.call("DELETE", f"/{manifest['index']}")
out.write_text(json.dumps(result, indent=1))
print(json.dumps(result, indent=1))
