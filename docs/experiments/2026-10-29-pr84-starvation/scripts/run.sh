#!/usr/bin/env bash
# One crawler process of PR #84 with production settings, normal feed, then isel-shaped jobs.
# Usage: run.sh <run dir> <policy> <warm s> <after s> <jobs> <hosts per job> <docs per host>
set -euo pipefail
SIM=$(cd "$(dirname "$0")" && pwd)
WC=$(cd "${WC:-$SIM/../wc-cc96500}" && pwd)
RUN=$1 POLICY=$2 WARM=$3 AFTER=$4 JOBS=$5 HPJ=$6 DOCS=$7
PORT=18080
rm -rf "$RUN" && mkdir -p "$RUN/queue/crawler_00" "$RUN/mb/pending" "$RUN/mb/results"
PY="uv run --no-project --python 3.12"
CRAWLER_DEPS="--with scrapy==2.16.0 --with twisted==26.4.0 --with tldextract --with pqdict==1.5.1 --with PyYAML==6.0.2 --with python-json-logger==2.0.7"

$PY --with aiohttp python "$SIM/mockweb.py" $PORT "$RUN/access.log" "${MEDIAN_S:-3.5}" "${SIGMA:-0.55}" > "$RUN/mockweb.out" 2>&1 &
MOCK=$!
$PY python "$SIM/feeder.py" "$RUN/queue/crawler_00" $PORT 3000 180 > "$RUN/feeder.out" 2>&1 &
FEED=$!
sleep 3
(
  cd "$WC/containers/crawler"
  # SHARE is read only by the weighted policy (branch pr84-weighted-dispatch).
  export PYTHONPATH="$WC:$SIM" MANUAL_BULK_CRAWL_SCHEDULING_POLICY=$POLICY \
    MANUAL_BULK_CRAWL_SHARE_PERCENT=${SHARE:-50} \
    CRAWLER_MANUAL_BULK_CRAWL_QUEUE_DIR="$RUN/mb/pending" \
    CRAWLER_USE_AUTOTHROTTLE=true CRAWLER_CONCURRENT_REQUESTS=${CONC:-256} \
    CRAWLER_CONCURRENT_REQUESTS_PER_DOMAIN=16 CRAWLER_DOWNLOAD_DELAY=0.25 \
    CRAWLER_AUTOTHROTTLE_START_DELAY=0.5 CRAWLER_AUTOTHROTTLE_MAX_DELAY=10.0 \
    CRAWLER_AUTOTHROTTLE_TARGET_CONCURRENCY=2.0 CRAWLER_AUTOTHROTTLE_DEBUG=false
  exec $PY $CRAWLER_DEPS scrapy crawl html_spider -a crawler_id=0 --loglevel WARNING \
    -s "URL_QUEUE_TEMPLATE=$RUN/queue/crawler_{id:02d}" \
    -s "RESULT_DIR_TEMPLATE=$RUN/crawl_result/crawler_{id:02d}" \
    -s DNS_RESOLVER=simdns.LoopbackResolver -s TELNETCONSOLE_ENABLED=False
) > "$RUN/crawler.log" 2>&1 &
CRAWL=$!
T0=$(date +%s)
echo "$T0" > "$RUN/t0"
cpu() { pgrep -f "html_spider -a crawler_id=0" | tail -1; }
while [ $(( $(date +%s) - T0 )) -lt "$WARM" ]; do
  echo "$(date +%s) $(ps -o %cpu= -p "$(cpu)" 2>/dev/null || echo NA)" >> "$RUN/cpu.log"; sleep 5
done
date +%s > "$RUN/t_submit"
# KEEP=1 plays isel's planner: a completed job is replaced at once, so $JOBS stay open.
$PY python "$SIM/submit.py" "$WC" "$RUN/mb/pending" "$RUN/mb/results" $PORT "$JOBS" "$HPJ" "$DOCS" \
  "$(( ${KEEP:-0} > 0 ? AFTER : 0 ))" > "$RUN/jobs.txt" &
SUBMIT=$!
[ "${KEEP:-0}" -gt 0 ] || wait $SUBMIT
while [ $(( $(date +%s) - T0 )) -lt $(( WARM + AFTER )) ]; do
  echo "$(date +%s) $(ps -o %cpu= -p "$(cpu)" 2>/dev/null || echo NA)" >> "$RUN/cpu.log"; sleep 5
done
date +%s > "$RUN/t_end"
pkill -INT -f "html_spider -a crawler_id=0" || true
sleep 8
pkill -KILL -f "html_spider -a crawler_id=0" || true
kill $FEED $MOCK $SUBMIT 2>/dev/null || true
wait 2>/dev/null || true
rm -rf "$RUN/crawl_result"
echo done
