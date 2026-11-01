"""Batch-20 coverage of a 64M list whose hosts are capped at R req/s x 14 days.

A host above its cap keeps only its top-cap URLs by score; the freed places go to the
next-best URLs of other hosts, as a cap applied before `LIMIT k` in select_full would.
"""
import sys

import duckdb

sel, golden, tmp = sys.argv[1], sys.argv[2], sys.argv[3]
K, DAYS = 64_000_000, 14
con = duckdb.connect()
con.execute("SET threads = 8")
con.execute("SET memory_limit = '12GB'")
con.execute("SET preserve_insertion_order = false")
con.execute(f"SET temp_directory = '{tmp}'")
host = "lower(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?([^/:?#]+)', 1))"
con.execute(f"CREATE TABLE hs AS SELECT {host} AS host, score FROM read_parquet('{sel}')")
con.execute("CREATE TABLE hn AS SELECT host, count(*) AS n FROM hs GROUP BY 1")
con.execute("CREATE TABLE big AS SELECT host, score, row_number() OVER (PARTITION BY host ORDER BY score DESC) AS rn "
            "FROM hs WHERE host IN (SELECT host FROM hn WHERE n > 241920)")
print("rows in hosts above 241,920:", con.execute("SELECT count(*), count(DISTINCT host) FROM big").fetchone(), flush=True)
for tag in ("head", "random"):
    con.execute(f"CREATE TABLE g_{tag} AS SELECT DISTINCT canonical_url AS k FROM read_parquet('{golden}') "
                f"WHERE batch_id = 20 AND list_contains(tags, '{tag}')")
con.execute(f"CREATE TABLE gs AS SELECT url, score, {host} AS host FROM read_parquet('{sel}') "
            "WHERE url IN (SELECT k FROM g_head UNION SELECT k FROM g_random)")
for rate in (None, 0.2, 0.3, 0.4):
    cap = int(rate * DAYS * 86400) if rate else 10**12
    con.execute(f"""CREATE OR REPLACE TABLE thr AS
        SELECT host, min(score) AS hthr FROM big WHERE rn <= {cap}
        AND host IN (SELECT host FROM hn WHERE n > {cap}) GROUP BY host""")
    kept_cond = "(t.hthr IS NULL OR x.score >= t.hthr)"
    kept = con.execute(f"SELECT count(*) FROM hs x LEFT JOIN thr t USING (host) WHERE {kept_cond}").fetchone()[0]
    (t64,) = con.execute(f"SELECT quantile_disc(x.score, 1 - {K} / {kept}::DOUBLE) FROM hs x LEFT JOIN thr t USING (host) "
                         f"WHERE {kept_cond}").fetchone()
    n_list, capped, maxn = con.execute(f"""SELECT sum(c), count(*) FILTER (WHERE c >= {cap}), max(c) FROM
        (SELECT host, count(*) AS c FROM hs x LEFT JOIN thr t USING (host) WHERE {kept_cond} AND x.score >= {t64} GROUP BY 1)""").fetchone()
    cov = {}
    for tag in ("head", "random"):
        hit, tot = con.execute(f"""SELECT count(x.url), (SELECT count(*) FROM g_{tag}) FROM g_{tag} g
            JOIN gs x ON x.url = g.k LEFT JOIN thr t ON t.host = x.host WHERE {kept_cond} AND x.score >= {t64}""").fetchone()
        cov[tag] = hit / tot
    label = f"R={rate} req/s, cap {cap:,}" if rate else "uncapped"
    print(f"{label:28s} list {n_list:,} head {cov['head']:.2%} random {cov['random']:.2%} capped hosts {capped} "
          f"largest {maxn:,} = {maxn / (rate or 0.2) / 86400:.1f} d at {rate or 0.2} req/s", flush=True)
