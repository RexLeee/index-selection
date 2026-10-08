"""Point-in-time features for every (URL, batch) pair, computed in DuckDB.

Leakage rules (see docs/DESIGN.md):

* The pool of batch b is the crawled rows with `last_fetch_ok < as_of_b`, where
  `as_of_b = min(created_b - lead_h, snapshot_at)`. Each URL is fetched once,
  so this rebuilds the crawl as it stood before the batch existed.
* Golden-derived features of batch b only use batches created before b whose
  rows were available (`available_at`) by `as_of_b`, decayed by age. Pseudo
  batches are fetched after the official batch they extend, so they only reach
  batches whose as-of time is later than their fetch time.
* Columns the crawler derives from golden labels are in `FORBIDDEN` and can
  never enter a feature set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa

from isel.config import Config
from isel.golden import OFFICIAL, golden_files
from isel.io import read_json, sql_str
from isel.urlnorm import DomainKeyer

HOST_RE = r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?([^:/?#]+)"
PATH_RE = r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)"


@dataclass(frozen=True)
class Feature:
    name: str
    group: str
    categorical: bool = False


FEATURES: dict[str, Feature] = {f.name: f for f in (
    Feature("url_len", "static"),
    Feature("path_depth", "static"),
    Feature("is_root", "static"),
    Feature("has_query", "static"),
    Feature("n_params", "static"),
    Feature("path_long_number", "static"),
    Feature("path_date", "static"),
    Feature("path_marker", "static", categorical=True),
    Feature("ext_class", "static", categorical=True),
    Feature("is_https", "static"),
    Feature("sub_www", "static"),
    Feature("sub_mobile", "static"),
    Feature("sub_amp", "static"),
    Feature("host_labels", "static"),
    Feature("title_null", "fetch"),
    Feature("title_len", "fetch"),
    Feature("has_anchor", "fetch"),
    Feature("discovery_source_type", "fetch", categorical=True),
    Feature("robots_bits", "fetch", categorical=True),
    Feature("is_redirect", "fetch"),
    Feature("redirect_hop_count", "fetch"),
    Feature("has_json_ld", "fetch"),
    Feature("hreflang_count", "fetch"),
    Feature("has_last_modified", "fetch"),
    Feature("has_cache_control", "fetch"),
    Feature("fetch_age_days", "fetch"),
    Feature("log_inlinks", "cumulative"),
    Feature("log_ext", "cumulative"),
    Feature("has_ext", "cumulative"),
    Feature("rel_inlinks", "cumulative"),
    Feature("rel_ext", "cumulative"),
    Feature("hist_decayed", "history"),
    Feature("hist_head", "history"),
    Feature("hist_random", "history"),
    Feature("hist_days_since", "history"),
    Feature("lift_tld", "domain"),
    Feature("lift_dkey", "domain"),
    Feature("lift_host", "domain"),
    Feature("lift_hp", "domain"),
    Feature("dem_dkey", "domain"),
    Feature("dem_host", "domain"),
    Feature("log_size_dkey", "domain"),
    Feature("log_size_host", "domain"),
    Feature("dkey_ext_share", "domain"),
    Feature("history_batches", "domain"),
)}

# Derived by the crawler from golden labels, or rolling windows relative to now.
FORBIDDEN = frozenset({
    "source", "url_score", "url_score_updated_at", "domain_score", "discovered_from",
    "should_crawl", "last_scheduled", "first_seen", "parent_page_score",
    "num_scheduled_90d", "num_fetch_ok_90d", "num_fetch_fail_90d", "num_content_update_90d",
    "num_consecutive_fail", "last_content_update",
})
ID_COLUMNS = ("url", "shard", "h", "dkey", "is_sample", "label", "in_head", "in_random",
              "url_score", "source")
# Pulled for diagnostics and later text features; never loaded into the store.
UNLOADED_COLUMNS = ("num_fetch_ok_90d", "title")
LEVELS = ("tld", "dkey", "host", "hp")


class LeakageError(ValueError):
    """Raised when a feature list contains a forbidden or unknown column."""


def resolve_set(cfg: Config, name: str) -> list[str]:
    """Returns the feature names of set `name`, rejecting forbidden or unknown ones."""
    if name not in cfg.features.sets:
        raise LeakageError(f"unknown feature set {name!r}")
    names = list(cfg.features.sets[name])
    assert_clean(names)
    return names


def assert_clean(names: list[str]) -> None:
    bad = sorted(set(names) & FORBIDDEN)
    if bad:
        raise LeakageError(f"forbidden columns in feature list: {bad}")
    unknown = sorted(set(names) - set(FEATURES))
    if unknown:
        raise LeakageError(f"unregistered features: {unknown}")


@dataclass(frozen=True)
class Batch:
    batch_id: int
    created_at: datetime
    as_of: datetime
    available_at: datetime
    official: bool


def _ts(value: datetime) -> str:
    return f"{sql_str(value.isoformat())}::TIMESTAMPTZ"


class FeatureStore:
    """Holds the crawl rows, golden set and per-batch aggregates in one DuckDB."""

    def __init__(self, cfg: Config, con: duckdb.DuckDBPyConnection, work_dir: Path,
                 next_batch: bool = False) -> None:
        self.cfg = cfg
        self.con = con
        self.work_dir = work_dir
        meta = read_json(work_dir / "sample" / "_meta.json")
        if not meta:
            raise RuntimeError("sample/_meta.json is missing; run pull-sample first")
        if "finished_at" not in meta:
            raise RuntimeError("the sample is unfinished; rerun pull-sample until it finishes")
        self.rate = float(meta["sample_percent"]) / 100.0
        self.snapshot_at = datetime.fromisoformat(meta["snapshot_at"])
        self.batches: list[Batch] = []
        self._prepare(next_batch)

    def _files(self, pattern: str) -> list[str]:
        return sorted(str(p) for p in self.work_dir.glob(pattern))

    def _prepare(self, next_batch: bool) -> None:
        con, cfg = self.con, self.cfg
        sample = self._files("sample/shard=*.parquet")
        if not sample:
            raise RuntimeError("no sample files; run pull-sample first")
        skip = ", ".join(UNLOADED_COLUMNS)
        parts = [f"SELECT * EXCLUDE ({skip}), true AS is_sample FROM read_parquet({sql_str(f)})"
                 for f in sample]
        parts += [f"SELECT * EXCLUDE ({skip}), false AS is_sample FROM read_parquet({sql_str(f)})"
                  for f in self._files("positives/batch=*.parquet")]
        con.execute(
            "CREATE OR REPLACE TEMP TABLE crawl0 AS SELECT *, "
            f"lower(regexp_extract(url, {sql_str(HOST_RE)}, 1)) AS host, "
            f"regexp_extract(url, {sql_str(PATH_RE)}, 1) AS path, md5_number_lower(url) AS h "
            f"FROM ({' UNION ALL BY NAME '.join(parts)}) "
            "QUALIFY row_number() OVER (PARTITION BY url, shard ORDER BY is_sample DESC) = 1")
        files = golden_files(self.work_dir, cfg.golden.use_pseudo)
        golden = f"[{', '.join(sql_str(str(f)) for f in files)}]"
        excluded = list(cfg.golden.exclude_batches) or [-1]
        rows = con.execute(
            f"SELECT batch_id, min(batch_created_at), max(available_at), "
            f"bool_and(label_source = {sql_str(OFFICIAL)}), count(DISTINCT label_source) "
            f"FROM read_parquet({golden}) WHERE batch_id NOT IN ({', '.join(map(str, excluded))}) "
            "GROUP BY batch_id ORDER BY batch_id").fetchall()
        mixed = [int(r[0]) for r in rows if r[4] > 1]
        if mixed:
            raise RuntimeError(f"batch ids {mixed} hold both official and pseudo rows; "
                               "raise pseudo.id_offset")
        lead = timedelta(hours=cfg.golden.lead_h)
        self.batches = [Batch(int(b), t, min(t - lead, self.snapshot_at), a, bool(o))
                        for b, t, a, o, _ in rows]
        missing = [b.batch_id for b in self.batches
                   if not (self.work_dir / "positives" / f"batch={b.batch_id}.parquet").exists()]
        if missing:
            raise RuntimeError(f"positives are missing for batches {missing}; run pull-positives")
        if next_batch:
            official = [b.batch_id for b in self.batches if b.official]
            t = self.snapshot_at + lead
            self.batches.append(Batch(max(official) + 1 if official else 1, t, self.snapshot_at,
                                      t, True))
        con.execute("CREATE OR REPLACE TEMP TABLE batches (batch_id BIGINT, "
                    "created_at TIMESTAMPTZ, as_of TIMESTAMPTZ, available_at TIMESTAMPTZ)")
        con.executemany("INSERT INTO batches VALUES (?, ?, ?, ?)",
                        [(b.batch_id, b.created_at, b.as_of, b.available_at)
                         for b in self.batches])
        con.execute(
            "CREATE OR REPLACE TEMP TABLE gold0 AS SELECT g.batch_id, g.query_id, g.raw_url, "
            "g.canonical_url, list_contains(g.tags, 'head') AS in_head, "
            "list_contains(g.tags, 'random') AS in_random, "
            f"lower(regexp_extract(g.canonical_url, {sql_str(HOST_RE)}, 1)) AS host, "
            f"regexp_extract(g.canonical_url, {sql_str(PATH_RE)}, 1) AS path "
            f"FROM read_parquet({golden}) g SEMI JOIN batches b USING (batch_id)")
        self._host_keys()
        self._key_columns("crawl", "crawl0")
        self._key_columns("gold", "gold0")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE spell AS SELECT batch_id, url, canonical_url, "
            "bool_or(in_head) AS in_head, bool_or(in_random) AS in_random FROM ("
            "SELECT batch_id, raw_url AS url, canonical_url, in_head, in_random FROM gold "
            "UNION ALL SELECT batch_id, canonical_url, canonical_url, in_head, in_random FROM gold"
            ") GROUP BY ALL")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE label_urls AS SELECT batch_id, url, "
            "bool_or(in_head) AS in_head, bool_or(in_random) AS in_random FROM spell GROUP BY ALL")
        half_life = float(cfg.features.half_life_days)
        con.execute(
            "CREATE OR REPLACE TEMP TABLE pairs AS SELECT s.batch_id AS src, d.batch_id AS dst, "
            f"pow(0.5, date_diff('second', s.created_at, d.as_of) / 86400.0 / {half_life}) AS w "
            "FROM batches s JOIN batches d "
            "ON s.created_at < d.created_at AND s.available_at <= d.as_of")
        self._aggregates()

    def _host_keys(self) -> None:
        split = self.work_dir / "dims" / "split_hosts.parquet"
        hosts = [] if not split.exists() else self.con.execute(
            f"SELECT host FROM read_parquet({sql_str(str(split))})").fetchall()
        keyer = DomainKeyer([h for (h,) in hosts], offline=self.cfg.routing.tldextract_offline)
        distinct = [h for (h,) in self.con.execute(
            "SELECT DISTINCT host FROM crawl0 UNION SELECT DISTINCT host FROM gold0").fetchall()]
        keys = [keyer.key_for_host(h or "") for h in distinct]
        table = pa.table({"host": distinct, "dkey": keys})
        self.con.register("host_keys_arrow", table)
        self.con.execute("CREATE OR REPLACE TEMP TABLE host_keys AS SELECT * FROM host_keys_arrow")
        self.con.unregister("host_keys_arrow")

    def _key_columns(self, name: str, source: str) -> None:
        self.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT s.*, k.dkey, "
            "s.host || '/' || regexp_extract(s.path, '^/([^/]*)', 1) AS hp, "
            "regexp_extract(s.host, '([^.]+)$', 1) AS tld "
            f"FROM {source} s LEFT JOIN host_keys k USING (host)")

    def _aggregates(self) -> None:
        """Per-level exposure, hits and demand, decayed into each destination batch."""
        con, rate = self.con, self.rate
        con.execute(
            "CREATE OR REPLACE TEMP TABLE matches AS SELECT s.batch_id, s.canonical_url, "
            "c.host, c.hp, c.dkey, c.tld FROM spell s JOIN crawl c ON c.url = s.url "
            "JOIN batches b ON b.batch_id = s.batch_id WHERE c.last_fetch_ok < b.as_of")
        for lvl in LEVELS:
            extra = (", avg(ln(1 + c.inlink_count_approx)) AS mean_li, "
                     "avg(ln(1 + c.inlink_count_external)) AS mean_le, "
                     "avg((c.inlink_count_external > 0)::DOUBLE) AS ext_share"
                     if lvl == "dkey" else "")
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE expo_{lvl} AS SELECT c.{lvl} AS k, b.batch_id, "
                f"count(*) AS n{extra} FROM crawl c JOIN batches b ON c.last_fetch_ok < b.as_of "
                "WHERE c.is_sample GROUP BY ALL")
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE hits_{lvl} AS SELECT {lvl} AS k, batch_id, "
                "count(DISTINCT canonical_url) AS g FROM matches GROUP BY ALL")
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE lvl_{lvl} AS SELECT k, dst AS batch_id, "
                f"sum(w * n) / {rate!r} AS e, sum(w * g) AS g FROM ("
                f"SELECT e.k, p.dst, p.w, e.n, 0 AS g FROM expo_{lvl} e "
                "JOIN pairs p ON p.src = e.batch_id UNION ALL "
                f"SELECT h.k, p.dst, p.w, 0, h.g FROM hits_{lvl} h "
                "JOIN pairs p ON p.src = h.batch_id) GROUP BY ALL")
        for lvl in ("dkey", "host"):
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE dem_{lvl} AS SELECT d.k, p.dst AS batch_id, "
                f"sum(p.w * d.n) AS dem FROM (SELECT {lvl} AS k, batch_id, "
                "count(DISTINCT canonical_url) AS n FROM gold GROUP BY ALL) d "
                "JOIN pairs p ON p.src = d.batch_id GROUP BY ALL")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE glob_stats AS SELECT p.dst AS batch_id, "
            f"sum(p.w * coalesce(x.n, 0)) / {rate!r} AS e, sum(p.w * coalesce(m.g, 0)) AS g, "
            "count(*) AS hb FROM pairs p "
            "LEFT JOIN (SELECT batch_id, sum(n) AS n FROM expo_tld GROUP BY ALL) x "
            "ON x.batch_id = p.src "
            "LEFT JOIN (SELECT batch_id, count(DISTINCT canonical_url) AS g FROM matches "
            "GROUP BY ALL) m ON m.batch_id = p.src GROUP BY ALL")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE uhist AS SELECT l.url, p.dst AS batch_id, "
            "sum(p.w) AS hist_decayed, max(l.in_head::INT) AS hist_head, "
            "max(l.in_random::INT) AS hist_random, "
            "min(date_diff('second', s.created_at, d.as_of)) / 86400.0 AS hist_days_since "
            "FROM label_urls l JOIN pairs p ON p.src = l.batch_id "
            "JOIN batches s ON s.batch_id = p.src JOIN batches d ON d.batch_id = p.dst "
            "GROUP BY ALL")

    def batch(self, batch_id: int) -> Batch:
        for b in self.batches:
            if b.batch_id == batch_id:
                return b
        raise KeyError(f"batch {batch_id} is not in the golden set")

    def _static_sql(self) -> str:
        f = self.cfg.features
        markers = " ".join(f"WHEN contains(c.path, {sql_str(m)}) THEN {i + 1}"
                           for i, m in enumerate(f.path_markers))
        marker_sql = f"CASE {markers} ELSE 0 END" if markers else "0"
        ext_cases = " ".join(f"WHEN {sql_str(k)} THEN {int(v)}"
                             for k, v in sorted(f.ext_classes.items()))
        ext = "regexp_extract(lower(c.path), '\\.([a-z0-9]{1,5})$', 1)"
        ext_other = max(f.ext_classes.values(), default=0) + 1
        query = "regexp_extract(c.url, '\\?([^#]*)', 1)"
        return (
            f"least(length(c.url), {int(f.url_len_cap)}) AS url_len, "
            "len(list_filter(string_split(c.path, '/'), x -> x <> '')) AS path_depth, "
            "(c.path IN ('', '/') AND NOT contains(c.url, '?'))::INT AS is_root, "
            "contains(c.url, '?')::INT AS has_query, "
            f"CASE WHEN {query} = '' THEN 0 ELSE length({query}) "
            f"- length(replace({query}, '&', '')) + 1 END AS n_params, "
            "regexp_matches(c.path, '[0-9]{5,}')::INT AS path_long_number, "
            f"regexp_matches(c.path, {sql_str(f.date_regex)})::INT AS path_date, "
            f"{marker_sql} AS path_marker, "
            f"CASE {ext} WHEN '' THEN 0 {ext_cases} ELSE {ext_other} END AS ext_class, "
            "starts_with(c.url, 'https://')::INT AS is_https, "
            "starts_with(c.host, 'www.')::INT AS sub_www, "
            "(starts_with(c.host, 'm.') OR starts_with(c.host, 'mobile.'))::INT AS sub_mobile, "
            "(starts_with(c.host, 'amp.') OR contains(c.path, '/amp'))::INT AS sub_amp, "
            "length(c.host) - length(replace(c.host, '.', '')) + 1 AS host_labels, "
            "(c.title_len IS NULL)::INT AS title_null, c.title_len, "
            "c.has_anchor::INT AS has_anchor, c.discovery_source_type, c.robots_bits, "
            "c.is_redirect::INT AS is_redirect, c.redirect_hop_count, "
            "c.has_json_ld::INT AS has_json_ld, c.hreflang_count, "
            "c.has_last_modified::INT AS has_last_modified, "
            "c.has_cache_control::INT AS has_cache_control, "
            "ln(1 + c.inlink_count_approx) AS log_inlinks, "
            "ln(1 + c.inlink_count_external) AS log_ext, "
            "(c.inlink_count_external > 0)::INT AS has_ext")

    def frame_sql(self, batch_id: int, mode: str) -> str:
        """SQL for the rows of batch `batch_id` with ids, label and every feature.

        Args:
            batch_id: The batch whose pool, label and as-of time apply.
            mode: "eval" keeps every sample row in the pool plus the batch's golden
                rows; "train" keeps the golden rows plus a hashed subsample of the
                other sample rows at rate `dataset.neg_rate`.
        """
        if mode not in ("eval", "train"):
            raise ValueError(f"unknown mode {mode!r}")
        b = self.batch(batch_id)
        a = float(self.cfg.features.eb_pseudo_hits)
        ppm = round(self.cfg.dataset.neg_rate * 1_000_000)
        salt = sql_str(f"{self.cfg.dataset.neg_salt}:{batch_id}")
        keep = ("(l.url IS NOT NULL OR c.is_sample)" if mode == "eval" else
                f"(l.url IS NOT NULL OR (c.is_sample AND "
                f"md5_number_lower(c.url || {salt}) % 1000000 < {ppm}))")
        rate = self.rate
        return f"""
WITH base AS (
  SELECT c.*, l.url IS NOT NULL AS label,
    coalesce(l.in_head, false) AS in_head, coalesce(l.in_random, false) AS in_random
  FROM crawl c
  LEFT JOIN (SELECT * FROM label_urls WHERE batch_id = {batch_id}) l ON l.url = c.url
  WHERE c.last_fetch_ok < {_ts(b.as_of)} AND {keep}
), g AS (
  SELECT e, g, hb, nullif(g, 0) / nullif(e, 0) AS rg FROM glob_stats WHERE batch_id = {batch_id}
),
s1 AS (
  SELECT base.*, g.rg, g.hb,
    (coalesce(t.g, 0) + {a}) / (coalesce(t.e, 0) + {a} / g.rg) AS r_tld,
    t.g AS g_tld, d.g AS g_dkey, d.e AS e_dkey, ho.g AS g_host, ho.e AS e_host,
    hp.g AS g_hp, hp.e AS e_hp
  FROM base LEFT JOIN g ON true
  LEFT JOIN (SELECT * FROM lvl_tld WHERE batch_id = {batch_id}) t ON t.k = base.tld
  LEFT JOIN (SELECT * FROM lvl_dkey WHERE batch_id = {batch_id}) d ON d.k = base.dkey
  LEFT JOIN (SELECT * FROM lvl_host WHERE batch_id = {batch_id}) ho ON ho.k = base.host
  LEFT JOIN (SELECT * FROM lvl_hp WHERE batch_id = {batch_id}) hp ON hp.k = base.hp
), s2 AS (
  SELECT s1.*, (coalesce(g_dkey, 0) + {a}) / (coalesce(e_dkey, 0) + {a} / r_tld) AS r_dkey
  FROM s1
), s3 AS (
  SELECT s2.*, (coalesce(g_host, 0) + {a}) / (coalesce(e_host, 0) + {a} / r_dkey) AS r_host
  FROM s2
), s4 AS (
  SELECT s3.*, (coalesce(g_hp, 0) + {a}) / (coalesce(e_hp, 0) + {a} / r_host) AS r_hp
  FROM s3
)
SELECT c.url, c.shard, c.h, c.dkey, c.is_sample, c.label::INT AS label, c.in_head, c.in_random,
  c.url_score, c.source,
  {self._static_sql()},
  date_diff('second', c.last_fetch_ok, {_ts(b.as_of)}) / 86400.0 AS fetch_age_days,
  ln(1 + c.inlink_count_approx) - ds.mean_li AS rel_inlinks,
  ln(1 + c.inlink_count_external) - ds.mean_le AS rel_ext,
  coalesce(u.hist_decayed, 0) AS hist_decayed, coalesce(u.hist_head, 0) AS hist_head,
  coalesce(u.hist_random, 0) AS hist_random, u.hist_days_since,
  ln(c.r_tld / c.rg) AS lift_tld, ln(c.r_dkey / c.rg) AS lift_dkey,
  ln(c.r_host / c.rg) AS lift_host, ln(c.r_hp / c.rg) AS lift_hp,
  ln(1 + coalesce(dd.dem, 0)) AS dem_dkey, ln(1 + coalesce(dh.dem, 0)) AS dem_host,
  ln(1 + coalesce(ds.n, 0) / {rate!r}) AS log_size_dkey,
  ln(1 + coalesce(hs.n, 0) / {rate!r}) AS log_size_host,
  ds.ext_share AS dkey_ext_share, c.hb AS history_batches
FROM s4 c
LEFT JOIN (SELECT * FROM expo_dkey WHERE batch_id = {batch_id}) ds ON ds.k = c.dkey
LEFT JOIN (SELECT * FROM expo_host WHERE batch_id = {batch_id}) hs ON hs.k = c.host
LEFT JOIN (SELECT * FROM dem_dkey WHERE batch_id = {batch_id}) dd ON dd.k = c.dkey
LEFT JOIN (SELECT * FROM dem_host WHERE batch_id = {batch_id}) dh ON dh.k = c.host
LEFT JOIN (SELECT * FROM uhist WHERE batch_id = {batch_id}) u ON u.url = c.url
"""

    def frame(self, batch_id: int, mode: str) -> pa.Table:
        return self.con.execute(self.frame_sql(batch_id, mode)).to_arrow_table()

    def pool_size(self, batch_id: int) -> int:
        """Sample rows in the pool of `batch_id` (deduplicated by URL)."""
        (n,) = self.con.execute(
            f"SELECT count(DISTINCT url) FROM crawl WHERE is_sample "
            f"AND last_fetch_ok < {_ts(self.batch(batch_id).as_of)}").fetchone()
        return int(n)

    def has_history(self, batch_id: int) -> bool:
        """True when some earlier batch with crawled golden feeds this batch's priors."""
        row = self.con.execute(
            f"SELECT g, hb FROM glob_stats WHERE batch_id = {batch_id}").fetchone()
        return bool(row) and row[0] > 0 and row[1] >= 1 and not math.isnan(row[0])
