#!/usr/bin/env bash
# Round-4 batch on Host3: main4.lanes at once, a warm-up check on R0, then the analysis.
# Results: /data/r14725023/r3/main4/{warmup_check.txt,analysis.md}.
set -u
R=/data/r14725023/r3/main4
SHM=/dev/shm/r14725023-r3/main4
S=$HOME/r3/scripts
Z=$HOME/r3/sizes
export PATH=$HOME/.local/bin:$PATH PYVER=3.12.14 CPU0=39 HELPER_PIN=58-65,67-69,71 PORT0=18080
PY="uv run --no-project --python 3.12.14 python"
"$S/lanes3.sh" "$HOME/r3/main4.lanes" "$R" "$SHM" &
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
# 5 lanes run with more turbo headroom than round 3's 10, so allow up to 38 req/s.
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
  echo "# Round-4 batch, $(date -u +%FT%TZ)"
  echo; echo "lanes3 exit status: $(cat "$R/done")"
  echo; echo "## Warm-up check (minutes 5-14)"; echo; sed 's/^/    /' "$R/warmup_check.txt"
  echo; echo "## C1-C4, 59 min after submit + 60 s, against R0"; echo
  $PY "$S/analyze3.py" --ref "$R/R0" --minutes 59 "$R/R0" "$R/R120" "$R/R30" "$R/H30" "$R/H120"
  echo; echo "## Big hosts (C3)"; echo; echo '```'
  $PY "$S/diag4_bighost.py" "$R/R120" "$Z/w03_sizes.txt"
  echo
  $PY "$S/diag4_bighost.py" "$R/R30" "$Z/w03_sizes.txt"
  echo
  $PY "$S/diag4_bighost.py" "$R/H30" "$Z/hot20x10k.txt" 20
  echo
  $PY "$S/diag4_bighost.py" "$R/H120" "$Z/hot20x10k.txt" 20
  echo '```'
  echo; echo "## Busiest minute against slot rate (C2)"; echo; echo '```'
  $PY "$S/diag3_burst.py" "$R/R120" 120
  echo
  $PY "$S/diag3_burst.py" "$R/R30" 30
  echo
  $PY "$S/diag3_burst.py" "$R/H30" 30
  echo
  $PY "$S/diag3_burst.py" "$R/H120" 120
  echo '```'
} > "$R/analysis.md" 2>&1
rm -rf "$SHM"
echo finished > "$R/main4.finished"
