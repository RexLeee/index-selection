"""PostgreSQL sessions with a read-only SQL gate and statement pacing.

crawlerdb's role is a superuser, so the database cannot stop an accidental
write. Two independent guards apply to every session: the server-side
`default_transaction_read_only=on` option, and a client-side allowlist that
rejects any statement that is not a single read.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import psycopg
from psycopg import sql

from isel.config import Config

_COMMENT_RE = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.S)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_READ_KEYWORDS = ("SELECT", "WITH", "SHOW", "EXPLAIN", "COPY")
_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|VACUUM|ANALYZE"
    r"|CLUSTER|REINDEX|LOCK|CALL|DO|REFRESH|COMMENT|SECURITY|IMPORT|NOTIFY|LISTEN)\b",
    re.I,
)


class ReadOnlyViolation(RuntimeError):
    """Raised when code tries to send a non-read statement."""


def require_env(name: str) -> str:
    """Returns the value of env var `name` (DSNs never live in config).

    Raises:
        RuntimeError: If the variable is unset or empty.
    """
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def _options(session: Mapping[str, str]) -> str:
    parts = []
    for key, value in session.items():
        escaped = str(value).replace("\\", "\\\\").replace(" ", "\\ ")
        parts.append(f"-c {key}={escaped}")
    return " ".join(parts)


def connect(dsn: str, *, app_name: str, session: Mapping[str, str],
            connect_timeout_s: int) -> psycopg.Connection:
    """Opens an autocommit connection with session GUCs set at startup."""
    return psycopg.connect(
        dsn,
        autocommit=True,
        application_name=app_name,
        options=_options(session),
        connect_timeout=connect_timeout_s,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def assert_read_only_sql(statement: str) -> None:
    """Raises ReadOnlyViolation unless `statement` is one read-only statement.

    Allowed: SELECT/WITH/SHOW/EXPLAIN (without ANALYZE) and `COPY (...) TO STDOUT`.
    """
    text = _STRING_RE.sub("''", _COMMENT_RE.sub(" ", statement)).strip().rstrip(";").strip()
    if ";" in text:
        raise ReadOnlyViolation("multiple statements are not allowed")
    head = text.split(None, 1)[0].upper() if text else ""
    if head not in _READ_KEYWORDS:
        raise ReadOnlyViolation(f"statement type {head!r} is not allowed")
    upper = text.upper()
    if head == "COPY" and not re.search(r"\bTO\s+STDOUT\b", upper):
        raise ReadOnlyViolation("COPY must be COPY ... TO STDOUT")
    if head == "EXPLAIN" and re.search(r"\bANALY[SZ]E\b", upper):
        raise ReadOnlyViolation("EXPLAIN ANALYZE executes the query")
    if head in ("SELECT", "WITH") and re.search(r"\bFOR\s+(UPDATE|SHARE|NO\s+KEY|KEY)\b", upper):
        raise ReadOnlyViolation("row locks are not allowed")
    body = re.split(r"\bTO\s+STDOUT\b", text, maxsplit=1, flags=re.I)[0] if head == "COPY" else text
    if _WRITE_WORDS.search(body):
        raise ReadOnlyViolation("write keyword found in a read-only statement")


class Pacer:
    """Keeps at least `min_interval_s` between the starts of consecutive statements."""

    def __init__(self, min_interval_s: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self._interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


class ReadOnlySession:
    """A connection wrapper that only lets paced read statements through."""

    def __init__(self, conn: psycopg.Connection, pacer: Pacer | None = None) -> None:
        self.conn = conn
        self._pacer = pacer or Pacer(0.0)

    def _text(self, query: str | sql.Composable) -> str:
        return query if isinstance(query, str) else query.as_string(self.conn)

    def fetchall(self, query: str | sql.Composable, params: Sequence[Any] | None = None) -> list:
        text = self._text(query)
        assert_read_only_sql(text)
        self._pacer.wait()
        with self.conn.cursor() as cur:
            cur.execute(text, params)
            return cur.fetchall()

    def fetchone(self, query: str | sql.Composable, params: Sequence[Any] | None = None) -> Any:
        rows = self.fetchall(query, params)
        return rows[0] if rows else None

    def copy_rows(self, query: str | sql.Composable, type_oids: Sequence[int]) -> Iterator[tuple]:
        """Streams rows of a `COPY ... TO STDOUT (FORMAT BINARY)` statement."""
        text = self._text(query)
        assert_read_only_sql(text)
        self._pacer.wait()
        with self.conn.cursor() as cur, cur.copy(text) as copy:
            copy.set_types(list(type_oids))
            yield from copy.rows()

    def close(self) -> None:
        self.conn.close()


def open_crawlerdb(cfg: Config) -> ReadOnlySession:
    c = cfg.crawlerdb
    conn = connect(require_env(c.dsn_env), app_name=f"{cfg.run.app_name}_crawler",
                   session=c.session, connect_timeout_s=c.connect_timeout_s)
    return ReadOnlySession(conn, Pacer(cfg.pull.min_interval_s))


def open_metricdb(cfg: Config) -> ReadOnlySession:
    m = cfg.metricdb
    conn = connect(require_env(m.dsn_env), app_name=f"{cfg.run.app_name}_metric",
                   session=m.session, connect_timeout_s=m.connect_timeout_s)
    return ReadOnlySession(conn)
