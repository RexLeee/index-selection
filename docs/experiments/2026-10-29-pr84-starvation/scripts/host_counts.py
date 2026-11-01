"""Per-host URL counts of the 64M list capped at 0.2 req/s x 14 d (CSV: host,n)."""
import sys

import duckdb

sel, out, tmp = sys.argv[1], sys.argv[2], sys.argv[3]
con = duckdb.connect()
con.execute("SET threads = 8")
con.execute("SET memory_limit = '12GB'")
con.execute(f"SET temp_directory = '{tmp}'")
host = "lower(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?([^/:?#]+)', 1))"
con.execute(f"CREATE TABLE hs AS SELECT {host} AS host, score FROM read_parquet('{sel}')")
con.execute("CREATE TABLE hn AS SELECT host, count(*) AS n FROM hs GROUP BY 1")
cap = 241920
con.execute(f"""CREATE TABLE thr AS SELECT host, min(score) AS hthr FROM (
    SELECT host, score, row_number() OVER (PARTITION BY host ORDER BY score DESC) AS rn
    FROM hs WHERE host IN (SELECT host FROM hn WHERE n > {cap})) WHERE rn <= {cap} GROUP BY host""")
cond = "(t.hthr IS NULL OR x.score >= t.hthr)"
kept = con.execute(f"SELECT count(*) FROM hs x LEFT JOIN thr t USING (host) WHERE {cond}").fetchone()[0]
(t64,) = con.execute(f"SELECT quantile_disc(x.score, 1 - 64000000 / {kept}::DOUBLE) FROM hs x LEFT JOIN thr t USING (host) WHERE {cond}").fetchone()
con.execute(f"COPY (SELECT host, least(count(*), {cap}) AS n FROM hs x LEFT JOIN thr t USING (host) WHERE {cond} AND x.score >= {t64} "
            f"GROUP BY 1) TO '{out}' (HEADER)")
print(con.execute(f"SELECT count(*), sum(n) FROM read_csv('{out}')").fetchone())
