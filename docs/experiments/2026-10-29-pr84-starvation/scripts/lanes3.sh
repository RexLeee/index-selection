#!/usr/bin/env bash
# Runs round-3 lanes at the same time, one run3.sh per lane.
# Usage: lanes3.sh <lane table> <out dir> <result root>
# Table lines: <name> <WebCrawler tree> <policy> <warm s> <after s> [KEY=VALUE ...]; '#' starts
# a comment. Lane i gets PORT=PORT0+i and, when CPU0 is set, its crawler on CPU CPU0+i.
# Env: PORT0 (18080), CPU0 (unset = no pinning), HELPER_PIN, and anything run3.sh reads.
# Writes <out dir>/done with 0 when every lane exited cleanly, else 1.
set -euo pipefail
SIM=$(cd "$(dirname "$0")" && pwd)
TABLE=$1 OUT=$2 ROOT=$3
mkdir -p "$OUT" "$ROOT"
rm -f "$OUT/done"
i=0
pids=()
while read -r name tree policy warm after rest; do
  case "$name" in '' | '#'*) continue ;; esac
  (
    if [ -n "${CPU0:-}" ]; then export PIN=$(( CPU0 + i )); fi
    export PORT=$(( ${PORT0:-18080} + i )) WC=$tree RESULT_ROOT=$ROOT/$name
    for kv in $rest; do export "$kv"; done
    exec "$SIM/run3.sh" "$OUT/$name" "$policy" "$warm" "$after"
  ) < /dev/null > "$OUT/$name.out" 2>&1 &
  pids+=($!)
  i=$(( i + 1 ))
done < "$TABLE"
status=0
for p in "${pids[@]}"; do wait "$p" || status=1; done
echo "$status" > "$OUT/done"
