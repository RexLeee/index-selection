#!/usr/bin/env bash
# Round-6 batch on Host3: main6.lanes at once, a warm-up check on R0, then the analysis.
# Results: /data/r14725023/r3/main6/{warmup_check.txt,analysis.md}.
set -u
R=/data/r14725023/r3/main6
SHM=/dev/shm/r14725023-r3/main6
S=$HOME/r3/scripts
Z=$HOME/r3/sizes
# Unpinned: since 2026-10-31 Host3 also serves a production YugabyteDB node (~28 % busy on
# every core), so no core is free to pin; the warm-up check guards the rate.
export PATH=$HOME/.local/bin:$PATH PYVER=3.12.14 PORT0=18080
PY="uv run --no-project --python 3.12.14 python"
"$S/lanes3.sh" "$HOME/r3/main6.lanes" "$R" "$SHM" &
LANES=$!
# Normal-only rate and crawler CPU over minutes 5-14 of each lane's warm-up.
sleep 845
for d in "$R"/*/; do
  n=$(basename "$d"); t0=$(cat "$d/t0"); a=$((t0 + 300)); b=$((t0 + 840))
  rate=$(awk -v a=$a -v b=$b '$3 == "n" && $1 >= a && $1 < b {n++} END {printf "%.1f", n / (b - a)}' "$d/access.log")
  cpu=$(awk -v a=$a -v b=$b 'NF > 3 && $1 >= a && $1 < b {if (!f) {f = $1; c = $4} l = $1; e = $4}
        END {printf "%.0f", (l > f) ? 100 * (e - c) / (l - f) : -1}' "$d/cpu.log")
  echo "$n $rate req/s, cpu $cpu %"
done > "$R/warmup_check.txt"
if ! awk '$1 == "R0" {exit !($2 >= 31 && $2 <= 38 && $5 >= 95)}' "$R/warmup_check.txt"; then
  echo "ABORT: R0 is outside 31-38 req/s or below 95 % CPU" >> "$R/warmup_check.txt"
  pkill -INT -f "html_spider -a crawler_id=0 .*$R/"
  sleep 8
  pkill -f "$R/"
  wait
  rm -rf "$SHM"
  exit 1
fi
wait "$LANES"
{
  echo "# Round-6 batch, $(date -u +%FT%TZ)"
  echo; echo "lanes3 exit status: $(cat "$R/done")"
  echo; echo "## Warm-up check (minutes 5-14)"; echo; sed 's/^/    /' "$R/warmup_check.txt"
  for pair in "R0 R0 B1 B2" "T0 T0 T4" "F0 F0 F1"; do
    set -- $pair; ref=$1; shift
    echo; echo "## C1-C4, 59 min after submit + 60 s, against $ref"; echo
    $PY "$S/analyze3.py" --ref "$R/$ref" --minutes 59 $(for n in "$@"; do echo "$R/$n"; done)
  done
  echo; echo "## Big hosts (C3)"; echo; echo '```'
  for n in B1 B2 T4 F1; do $PY "$S/diag4_bighost.py" "$R/$n" "$Z/w03_sizes.txt" 40; echo; done
  echo '```'
  echo; echo "## Per-host concurrency and 429 hosts"; echo; echo '```'
  for n in B1 B2 T4 F1; do $PY "$S/diag6_concurrency.py" "$R/$n"; done
  echo '```'
} > "$R/analysis.md" 2>&1
rm -rf "$SHM"
echo finished > "$R/main6.finished"
