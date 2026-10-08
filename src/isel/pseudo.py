"""Pseudo golden: the official golden pipeline, rebuilt on free API tiers.

The official generator (WebSearchEngine `Metric/`) reads Google Trends
"trending now" for 10 geos over 168 hours, dedups queries by exact string,
sums `search_volume` across geos, takes the top N by that sum as `head` and a
uniform sample as `random`, and keeps the `link` of each query's Google top-10
organic results. This module reproduces those rules. Pseudo batches train
models and feed features; they are never evaluated (see `golden.py`).

Spending is guarded twice: every HTTP attempt is charged to the ledger before
it is sent, and every answer is cached per run, so resuming a run never pays
again for a query it already has.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from isel.config import COURSE_SERP_KEY_ENV, Config, SerpCfg, SerpProviderCfg, TrendsCfg
from isel.golden import PSEUDO, golden_table, pseudo_batch_path, read_golden
from isel.io import atomic_write_json, read_json, utc_id, write_parquet_atomic
from isel.pg import Pacer

_NO_RESULTS = "hasn't returned any results"

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    """Raised before a paid call that would exceed the provider's budget."""


@dataclass(frozen=True)
class TrendItem:
    keyword: str
    volume: int | None
    started_at: datetime | None = None


@dataclass(frozen=True)
class PoolQuery:
    keyword: str
    frequency: int


class SerpClient(Protocol):
    provider: str

    def search(self, query: str) -> list[str]: ...


class TrendSource(Protocol):
    provider: str

    def trending(self, geo: str, hours: int) -> list[TrendItem]: ...


def merge_trending(per_geo: Sequence[tuple[str, Sequence[TrendItem]]]) -> list[PoolQuery]:
    """Merges per-geo trending lists with the official rules.

    Queries are deduplicated by exact string, `frequency` is the sum of the
    volumes over every geo that lists the query (missing volume counts as 0),
    and the pool is sorted by frequency; ties keep first-seen order.
    """
    frequency: dict[str, int] = {}
    for _, items in per_geo:
        for item in items:
            frequency[item.keyword] = frequency.get(item.keyword, 0) + (item.volume or 0)
    pool = [PoolQuery(k, f) for k, f in frequency.items()]
    return sorted(pool, key=lambda q: q.frequency, reverse=True)


def select_head(pool: Sequence[PoolQuery], n: int) -> list[str]:
    """The `n` most frequent queries of a pool sorted by `merge_trending`."""
    return [q.keyword for q in pool[:n]]


def select_random(pool: Sequence[PoolQuery], n: int, seed: int, exclude: set[str]) -> list[str]:
    """A seeded uniform sample of up to `n` queries not in `exclude`."""
    candidates = [q.keyword for q in pool if q.keyword not in exclude]
    return random.Random(seed).sample(candidates, min(n, len(candidates)))


def parse_organic(payload: Mapping[str, Any], key: str) -> list[str]:
    """Organic result links under `key`, in rank order; entries without a link are skipped."""
    return [r["link"] for r in payload.get(key) or [] if r.get("link")]


def overlap_at_k(found: Sequence[str], reference: Sequence[str], k: int) -> float | None:
    """Share of the reference's top-k links that `found` also has in its top k."""
    ref = set(reference[:k])
    return len(set(found[:k]) & ref) / len(ref) if ref else None


def load_key(env_name: str) -> str:
    """Reads an API key from the environment; never the course's key.

    Raises:
        RuntimeError: If the variable is unset or holds the course key.
    """
    value = os.environ.get(env_name, "")
    if not value:
        raise RuntimeError(f"environment variable {env_name} is not set")
    if value == os.environ.get(COURSE_SERP_KEY_ENV):
        raise RuntimeError(f"{env_name} holds the course {COURSE_SERP_KEY_ENV}; use your own key")
    return value


class SerpLedger:
    """Counts paid calls per provider and budget period in a JSON file.

    A `month` budget counts per calendar month (UTC); a `total` budget never
    resets. The file is re-read before every charge, so ledgers that share a
    file stay consistent within one process.
    """

    def __init__(self, path: Path, providers: Mapping[str, SerpProviderCfg]) -> None:
        self._path = path
        self._providers = providers

    def _period(self, provider: str, now: datetime) -> str:
        monthly = self._providers[provider].budget_period == "month"
        return now.strftime("%Y-%m") if monthly else "total"

    def used(self, provider: str, now: datetime) -> int:
        state = read_json(self._path) or {}
        return state.get(provider, {}).get(self._period(provider, now), 0)

    def require(self, provider: str, calls: int, now: datetime) -> None:
        """Raises BudgetExceeded before a run that needs more calls than are left.

        Each query needs at least one call; retries can still use up the rest.
        """
        left = self._providers[provider].budget - self.used(provider, now)
        if calls > left:
            raise BudgetExceeded(f"{provider} needs {calls} calls but has {left} left; "
                                 "nothing was spent")

    def charge(self, provider: str, now: datetime) -> None:
        """Records one call; raises BudgetExceeded instead if it would pass the budget."""
        state = read_json(self._path) or {}
        period = self._period(provider, now)
        count = state.get(provider, {}).get(period, 0) + 1
        budget = self._providers[provider].budget
        if count > budget:
            raise BudgetExceeded(f"{provider} budget {budget} for {period} is used up")
        state.setdefault(provider, {})[period] = count
        atomic_write_json(self._path, state)


class SerpCache:
    """Append-only JSON-lines cache of organic links per query.

    It holds only the query, its links and the fetch time, never a raw payload.
    A crash can leave a torn last line; it is ignored on load and the next
    append starts on a fresh line.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rows: dict[str, tuple[list[str], datetime]] = {}
        self._needs_newline = False
        if path.exists():
            text = path.read_text(encoding="utf-8")
            self._needs_newline = bool(text) and not text.endswith("\n")
            for line in text.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._rows[row["query"]] = (row["links"], datetime.fromisoformat(row["fetched_at"]))

    def get(self, query: str) -> tuple[list[str], datetime] | None:
        return self._rows.get(query)

    def put(self, query: str, links: list[str], fetched_at: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"query": query, "links": links, "fetched_at": fetched_at.isoformat()},
                          ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if self._needs_newline else "") + line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._needs_newline = False
        self._rows[query] = (links, fetched_at)


def fetch_serp(client: SerpClient, cache: SerpCache, queries: Sequence[str],
               now: Callable[[], datetime] = lambda: datetime.now(UTC)
               ) -> dict[str, tuple[list[str], datetime]]:
    """Organic links for each query, calling the client only for queries not in the cache."""
    out = {}
    step = max(1, len(queries) // 10)
    for i, query in enumerate(queries, start=1):
        hit = cache.get(query)
        if hit is None:
            fetched_at = now()
            links = client.search(query)
            cache.put(query, links, fetched_at)
            hit = (links, fetched_at)
        out[query] = hit
        if i % step == 0:
            log.info("%s: %d/%d queries", client.provider, i, len(queries))
    return out


def last_fetch(serp: Mapping[str, tuple[list[str], datetime]], queries: Sequence[str]) -> datetime:
    """When the last of `queries` was fetched: the time its labels became known."""
    return max(serp[q][1] for q in queries)


def build_pseudo_batch(batch_id: int, created_at: datetime, selection: Mapping[str, list[str]],
                       frequency: Mapping[str, int | None],
                       serp: Mapping[str, tuple[list[str], datetime]]) -> pa.Table:
    """Golden rows of a pseudo batch; `query_id` is the query's position in `selection`."""
    rows = []
    for query_id, (keyword, tags) in enumerate(selection.items()):
        links, _ = serp[keyword]
        rows += [(batch_id, created_at, query_id, keyword, frequency.get(keyword), tags, rank, url)
                 for rank, url in enumerate(links, start=1)]
    return golden_table(rows, label_source=PSEUDO, available_at=last_fetch(serp, list(selection)))


def _http_json(request: urllib.request.Request, timeout_s: float) -> dict[str, Any]:
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


class _Client:
    provider = ""
    organic_key = ""

    def __init__(self, cfg: SerpCfg, ledger: SerpLedger, key: str,
                 transport: Callable[[urllib.request.Request, float], dict] = _http_json,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._cfg = cfg
        self._p = cfg.providers[self.provider]
        self._ledger = ledger
        self._key = key
        self._pacer = Pacer(self._p.min_interval_s, sleep=sleep)
        self._transport = transport
        self._sleep = sleep
        self._now = now

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        """Sends with retries on network errors, 429 and 5xx; other HTTP errors raise.

        Every attempt is charged before it is sent, because a request that timed
        out on our side may still have been served and billed.
        """
        error = ""
        for attempt in range(self._cfg.retries):
            self._pacer.wait()
            self._ledger.charge(self.provider, self._now())
            try:
                return self._transport(request, self._cfg.timeout_s)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(f"{self.provider} HTTP {exc.code}") from None
                error = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError) as exc:
                error = type(exc).__name__
            if attempt + 1 < self._cfg.retries:
                self._sleep(self._cfg.retry_backoff_s * (attempt + 1))
        raise RuntimeError(f"{self.provider} failed after {self._cfg.retries} attempts: {error}")


class SerpApiClient(_Client):
    """SerpApi: the official golden's own source."""

    provider = "serpapi"
    organic_key = "organic_results"

    def _get(self, params: Mapping[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({**params, "api_key": self._key})
        payload = self._send(urllib.request.Request(f"{self._p.url}?{query}"))
        error = payload.get("error")
        if error and _NO_RESULTS not in error:
            raise RuntimeError(f"serpapi error: {error}")
        return payload

    def search(self, query: str) -> list[str]:
        """Same parameters as the official call: engine=google, q, num; no gl/hl."""
        payload = self._get({"engine": "google", "q": query, "num": self._cfg.num})
        return parse_organic(payload, self.organic_key)

    def trending(self, geo: str, hours: int) -> list[TrendItem]:
        payload = self._get({"engine": "google_trends_trending_now", "geo": geo, "hours": hours})
        return [TrendItem(t["query"], t.get("search_volume"),
                          _from_epoch(t.get("start_timestamp")))
                for t in payload.get("trending_searches") or []]


class SerperClient(_Client):
    """Serper.dev: one-time free credits; its defaults must be calibrated against SerpApi."""

    provider = "serper"
    organic_key = "organic"

    def search(self, query: str) -> list[str]:
        body = json.dumps({"q": query, "num": self._cfg.num}).encode("utf-8")
        request = urllib.request.Request(
            self._p.url, data=body, method="POST",
            headers={"X-API-KEY": self._key, "Content-Type": "application/json"})
        return parse_organic(self._send(request), self.organic_key)


CLIENT_TYPES: dict[str, type[_Client]] = {c.provider: c for c in (SerpApiClient, SerperClient)}


def make_client(cfg: Config, provider: str, ledger: SerpLedger) -> _Client:
    """A client for `provider` with our own key from the configured environment variable."""
    return CLIENT_TYPES[provider](cfg.serp, ledger, load_key(cfg.serp.providers[provider].key_env))


class TrendspyClient:
    """Google Trends "trending now" through trendspy (unofficial, free)."""

    provider = "trendspy"

    def __init__(self, cfg: TrendsCfg) -> None:
        from trendspy import Trends

        self._trends = Trends(request_delay=cfg.request_delay_s)

    def trending(self, geo: str, hours: int) -> list[TrendItem]:
        return [TrendItem(k.keyword, k.volume,
                          _from_epoch(k.started_timestamp[0] if k.started_timestamp else None))
                for k in self._trends.trending_now(geo=geo, hours=hours)]


def _from_epoch(seconds: Any) -> datetime | None:
    return datetime.fromtimestamp(int(seconds), tz=UTC) if seconds else None


SNAPSHOT_SCHEMA = pa.schema([
    ("geo", pa.string()),
    ("keyword", pa.string()),
    ("volume", pa.int64()),
    ("started_at", pa.timestamp("us", tz="UTC")),
])


def trends_snapshot(cfg: Config, client: TrendSource, out_dir: Path,
                    now: datetime | None = None) -> dict[str, Any]:
    """Pulls every configured geo once and writes `snapshot=<id>/` with its raw rows."""
    now = now or datetime.now(UTC)
    per_geo = [(geo, client.trending(geo, cfg.trends.hours)) for geo in cfg.trends.geos]
    rows = [{"geo": geo, "keyword": i.keyword, "volume": i.volume, "started_at": i.started_at}
            for geo, items in per_geo for i in items]
    snap = out_dir / f"snapshot={utc_id(now)}"
    write_parquet_atomic(pa.Table.from_pylist(rows, schema=SNAPSHOT_SCHEMA),
                         snap / "per_geo.parquet")
    meta = {"source": client.provider, "taken_at": now.isoformat(), "hours": cfg.trends.hours,
            "geos": {geo: len(items) for geo, items in per_geo},
            "queries": len(merge_trending(per_geo)), "dir": str(snap)}
    atomic_write_json(snap / "_meta.json", meta)
    return meta


def _snapshot_per_geo(snap: Path) -> dict[str, list[TrendItem]]:
    per_geo: dict[str, list[TrendItem]] = {}
    for r in pq.read_table(snap / "per_geo.parquet").to_pylist():
        per_geo.setdefault(r["geo"], []).append(TrendItem(r["keyword"], r["volume"]))
    return per_geo


def read_snapshot_pool(snap: Path) -> list[PoolQuery]:
    """The merged pool of a snapshot, rebuilt from its raw per-geo rows in order."""
    return merge_trending(list(_snapshot_per_geo(snap).items()))


def _jaccard(a: set[str], b: set[str]) -> float | None:
    return len(a & b) / len(a | b) if a | b else None


def _average_ranks(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    ranks = np.empty(len(x))
    ranks[np.argsort(x, kind="mergesort")] = np.arange(1, len(x) + 1)
    _, inverse = np.unique(x, return_inverse=True)
    return (np.bincount(inverse, weights=ranks) / np.bincount(inverse))[inverse]


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Rank correlation with tied values sharing their average rank; None if undefined."""
    if len(a) < 3:
        return None
    ra, rb = _average_ranks(a), _average_ranks(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def compare_snapshots(cfg: Config, trends_dir: Path, a_id: str, b_id: str) -> dict[str, Any]:
    """Compares two trending snapshots and writes `compare/<a>_vs_<b>.json` (numbers only).

    Taken at about the same time from trendspy and SerpApi, they show whether
    the free unofficial source returns the pool the official generator sees.
    The gate is the mean per-geo keyword Jaccard.
    """
    snaps = [trends_dir / f"snapshot={x}" for x in (a_id, b_id)]
    geo_a, geo_b = (_snapshot_per_geo(s) for s in snaps)
    per_geo = {g: _jaccard({i.keyword for i in geo_a.get(g, [])},
                           {i.keyword for i in geo_b.get(g, [])})
               for g in sorted(set(geo_a) | set(geo_b))}
    pool_a, pool_b = ({q.keyword: q.frequency for q in read_snapshot_pool(s)} for s in snaps)
    common = sorted(set(pool_a) & set(pool_b))
    mean_jaccard = _mean(list(per_geo.values()))
    gate = cfg.pseudo.gate_trend_jaccard
    out = {
        "a": {k: read_json(snaps[0] / "_meta.json")[k] for k in ("source", "taken_at")},
        "b": {k: read_json(snaps[1] / "_meta.json")[k] for k in ("source", "taken_at")},
        "geo_jaccard": per_geo, "mean_geo_jaccard": mean_jaccard,
        "pool_jaccard": _jaccard(set(pool_a), set(pool_b)), "common_queries": len(common),
        "frequency_spearman": spearman([pool_a[k] for k in common], [pool_b[k] for k in common]),
        "threshold": gate, "passed": mean_jaccard is not None and mean_jaccard >= gate,
    }
    atomic_write_json(trends_dir / "compare" / f"{a_id}_vs_{b_id}.json", out)
    return out


def snapshot_selection(pool: Sequence[PoolQuery], head_n: int, random_n: int,
                       seed: int) -> dict[str, list[str]]:
    """Official job on a fresh pool: head = top by frequency, random = uniform over all."""
    chosen: dict[str, list[str]] = {k: ["head"] for k in select_head(pool, head_n)}
    for keyword in select_random(pool, random_n, seed, exclude=set()):
        chosen.setdefault(keyword, []).append("random")
    return chosen


def backfill_selection(pool: pa.Table, batch_id: int, n: int, seed: int
                       ) -> tuple[datetime, dict[str, list[str]], dict[str, int | None]]:
    """Up to `n` random queries the official job left unused in a batch's pool.

    Returns the batch creation time, the chosen queries tagged `random`, and the
    frequency of every query in that batch's pool.
    """
    # The seeded sample must not depend on the row order pull-golden happened to write.
    rows = sorted((r for r in pool.to_pylist() if r["batch_id"] == batch_id),
                  key=lambda r: r["query_id"])
    if not rows:
        raise ValueError(f"batch {batch_id} has no trending pool")
    used = {r["keyword"] for r in rows if r["tags"]}
    queries = [PoolQuery(r["keyword"], r["frequency"] or 0) for r in rows]
    chosen = select_random(queries, n, seed, exclude=used)
    return (rows[0]["batch_created_at"], {k: ["random"] for k in chosen},
            {r["keyword"]: r["frequency"] for r in rows})


def _mean(values: Sequence[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


def calibration_report(cfg: Config, official: Mapping[str, list[str]],
                       serpapi: Mapping[str, tuple[list[str], datetime]],
                       serper: Mapping[str, tuple[list[str], datetime]]) -> dict[str, Any]:
    """Splits label noise into a provider effect and a time-drift effect.

    provider: Serper vs SerpApi on the same queries at about the same time.
    drift: SerpApi now vs the URLs the official job stored at batch time.
    """
    k = cfg.serp.num
    per_query = {q: {"provider": overlap_at_k(serper[q][0], serpapi[q][0], k),
                     "drift": overlap_at_k(serpapi[q][0], official[q], k),
                     "serper_vs_official": overlap_at_k(serper[q][0], official[q], k)}
                 for q in official if q in serpapi and q in serper}
    provider = _mean([v["provider"] for v in per_query.values()])
    drift = _mean([v["drift"] for v in per_query.values()])
    p = cfg.pseudo
    return {
        "queries": len(per_query), "k": k,
        "provider_overlap": provider, "drift_overlap": drift,
        "serper_vs_official_overlap": _mean([v["serper_vs_official"]
                                             for v in per_query.values()]),
        "gates": {"provider": provider is not None and provider >= p.gate_provider_overlap,
                  "drift": drift is not None and drift >= p.gate_drift_overlap},
        "thresholds": {"provider": p.gate_provider_overlap, "drift": p.gate_drift_overlap},
        "per_query": per_query,
    }


def serp_ledger(cfg: Config, work_dir: Path) -> SerpLedger:
    return SerpLedger(work_dir / "pseudo" / "ledger.json", cfg.serp.providers)


def serp_cache(work_dir: Path, run: str, provider: str) -> SerpCache:
    """The cache of one run, so a resumed run reuses its own answers and no other run's."""
    return SerpCache(work_dir / "pseudo" / "serp" / run / f"{provider}.jsonl")


def _uncached(cache: SerpCache, queries: Sequence[str]) -> int:
    return sum(cache.get(q) is None for q in queries)


def run_pseudo_batch(cfg: Config, work_dir: Path, source: str, batch_id: int, head_n: int,
                     random_n: int, client: SerpClient, ledger: SerpLedger,
                     now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Selects queries, fetches their SERPs and writes `golden/pseudo/batch=<id>.parquet`.

    `source` is `official:<batch>` to backfill queries the official job left
    unused in that batch's pool (random only), or `snapshot:<id>` to run the
    official head/random job on a trends snapshot.

    Raises:
        ValueError: On a bad source or a batch id below `pseudo.id_offset`.
        FileExistsError: If the pseudo batch was already written.
        BudgetExceeded: Before any call, if the budget cannot cover the uncached queries.
    """
    if batch_id < cfg.pseudo.id_offset:
        raise ValueError(f"pseudo batch id must be >= pseudo.id_offset ({cfg.pseudo.id_offset})")
    final = pseudo_batch_path(work_dir, batch_id)
    if final.exists():
        raise FileExistsError(f"{final} exists; pseudo batches are written once")
    kind, _, ref = source.partition(":")
    if kind == "official":
        if head_n:
            raise ValueError("an official backfill only adds random queries")
        pool = pq.read_table(work_dir / "golden" / "pool.parquet")
        created_at, chosen, frequency = backfill_selection(pool, int(ref), random_n,
                                                           cfg.pseudo.seed)
    elif kind == "snapshot":
        snap = work_dir / "trends" / f"snapshot={ref}"
        queries = read_snapshot_pool(snap)
        created_at = datetime.fromisoformat(read_json(snap / "_meta.json")["taken_at"])
        chosen = snapshot_selection(queries, head_n, random_n, cfg.pseudo.seed)
        frequency = {q.keyword: q.frequency for q in queries}
    else:
        raise ValueError(f"unknown source {source!r}; use official:<batch> or snapshot:<id>")
    cache = serp_cache(work_dir, f"batch={batch_id}", client.provider)
    ledger.require(client.provider, _uncached(cache, list(chosen)), now())
    serp = fetch_serp(client, cache, list(chosen), now)
    table = build_pseudo_batch(batch_id, created_at, chosen, frequency, serp)
    write_parquet_atomic(table, final)
    meta = {"batch_id": batch_id, "source": source, "provider": client.provider,
            "seed": cfg.pseudo.seed, "head_n": head_n, "random_n": random_n,
            "queries": len(chosen), "queries_with_links": sum(bool(serp[k][0]) for k in chosen),
            "rows": table.num_rows, "created_at": created_at.isoformat(),
            "available_at": last_fetch(serp, list(chosen)).isoformat()}
    atomic_write_json(final.with_suffix(".json"), meta)
    return meta


def run_calibration(cfg: Config, work_dir: Path, batch: int, tag: str, n: int,
                    serpapi: SerpClient, serper: SerpClient, ledger: SerpLedger,
                    now: Callable[[], datetime] = lambda: datetime.now(UTC)
                    ) -> dict[str, Any]:
    """Re-fetches a seeded sample of an official batch's queries with both providers.

    The two providers fetch each query back to back, so the provider overlap
    compares answers from the same moment. Writes
    `pseudo/calibration/batch=<b>_<tag>.json` (numbers only) and a per-query
    `.parquet` next to it; query text never goes into JSON.

    Raises:
        ValueError: If `tag` is not one of `golden.tags`.
        BudgetExceeded: Before any call, if either budget cannot cover its uncached queries.
    """
    if tag not in cfg.golden.tags:
        raise ValueError(f"tag must be one of {cfg.golden.tags}")
    rows = [r for r in read_golden(work_dir).to_pylist()
            if r["batch_id"] == batch and tag in r["tags"]]
    official: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        official.setdefault(r["keyword"], []).append((r["rank"], r["raw_url"]))
    chosen = random.Random(cfg.pseudo.seed).sample(sorted(official), min(n, len(official)))
    run = f"calibration=batch{batch}_{tag}"
    caches = {c.provider: serp_cache(work_dir, run, c.provider) for c in (serpapi, serper)}
    for provider, cache in caches.items():
        ledger.require(provider, _uncached(cache, chosen), now())
    a: dict[str, tuple[list[str], datetime]] = {}
    b: dict[str, tuple[list[str], datetime]] = {}
    for query in chosen:
        a.update(fetch_serp(serpapi, caches[serpapi.provider], [query], now))
        b.update(fetch_serp(serper, caches[serper.provider], [query], now))
    report = calibration_report(cfg, {q: [u for _, u in sorted(official[q])] for q in chosen},
                                a, b)
    out = work_dir / "pseudo" / "calibration" / f"batch={batch}_{tag}"
    detail = [{"query": q, **v} for q, v in report.pop("per_query").items()]
    write_parquet_atomic(pa.Table.from_pylist(detail, schema=pa.schema([
        ("query", pa.string()), ("provider", pa.float64()), ("drift", pa.float64()),
        ("serper_vs_official", pa.float64())])), out.with_suffix(".parquet"))
    summary = {**report, "batch": batch, "tag": tag, "requested": n}
    atomic_write_json(out.with_suffix(".json"), summary)
    return summary


def positive_gate(cfg: Config, summary: Mapping[str, Mapping[str, Any]],
                  pseudo_meta: Mapping[str, Any], pseudo_id: int) -> dict[str, Any]:
    """Crawled-positive rate of a backfill vs its own official batch (build-dataset summary).

    Only `official:<b>` backfills are gated: they share the official batch's
    pool, so a much lower rate means broken labels. A snapshot batch is newer
    than the frozen crawl and gets `passed = None`.
    """
    kind, _, ref = str(pseudo_meta["source"]).partition(":")
    if kind != "official":
        return {"passed": None, "reason": "only official backfills are gated"}

    def rate(key: str) -> float:
        entry = summary[key]
        return entry["positive_rows"] / entry["golden_urls"] if entry["golden_urls"] else 0.0

    official, pseudo = rate(ref), rate(str(pseudo_id))
    ratio = pseudo / official if official else None
    return {"official_rate": official, "pseudo_rate": pseudo, "ratio": ratio,
            "threshold": cfg.pseudo.gate_positive_ratio,
            "passed": ratio is not None and ratio >= cfg.pseudo.gate_positive_ratio}
