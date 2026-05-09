from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from approve_watch.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  asked_at    TEXT    NOT NULL,
  command     TEXT    NOT NULL,
  source      TEXT    NOT NULL,
  kind        TEXT    NOT NULL DEFAULT 'shell_command',
  approved    INTEGER,
  decided_at  TEXT,
  decided_by  TEXT,
  label       TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_asked_at ON approvals(asked_at);
CREATE INDEX IF NOT EXISTS idx_approvals_pending  ON approvals(approved) WHERE approved IS NULL;

CREATE TABLE IF NOT EXISTS pane_alarms (
  pane          TEXT PRIMARY KEY,
  alarmed_at    TEXT NOT NULL,
  rate_per_min  REAL NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_db(path: Path | None = None) -> Path:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.executescript(SCHEMA)
        # Lightweight forward migration for DBs created before `kind` existed.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
        if "kind" not in cols:
            conn.execute(
                "ALTER TABLE approvals ADD COLUMN kind TEXT NOT NULL DEFAULT 'shell_command'"
            )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return p


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or db_path()
    conn = sqlite3.connect(p, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def insert_pending(
    conn: sqlite3.Connection, command: str, source: str, kind: str = "shell_command"
) -> int:
    cur = conn.execute(
        "INSERT INTO approvals (asked_at, command, source, kind) VALUES (?, ?, ?, ?)",
        (now_iso(), command, source, kind),
    )
    rid = cur.lastrowid
    assert rid is not None
    return rid


def fetch_decision(conn: sqlite3.Connection, row_id: int) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT approved, decided_by FROM approvals WHERE id=?", (row_id,)
    ).fetchone()
    if row is None:
        return None, None
    return row["approved"], row["decided_by"]


def claim_decision(
    conn: sqlite3.Connection, row_id: int, approved: int, decided_by: str
) -> bool:
    """Set the decision only if still pending. Returns True if this caller wrote it."""
    cur = conn.execute(
        "UPDATE approvals SET approved=?, decided_by=?, decided_at=? "
        "WHERE id=? AND approved IS NULL",
        (approved, decided_by, now_iso(), row_id),
    )
    return cur.rowcount == 1


def list_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM approvals WHERE approved IS NULL ORDER BY id ASC"
    ).fetchall()


def set_label(conn: sqlite3.Connection, row_id: int, label: str | None) -> None:
    conn.execute("UPDATE approvals SET label=? WHERE id=?", (label, row_id))


def hourly_counts_24h(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%d %H:00', asked_at) AS bucket, COUNT(*) AS n
        FROM approvals
        WHERE asked_at >= datetime('now', '-24 hours')
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    return [(r["bucket"], r["n"]) for r in rows]


def hourly_counts_7d(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Counts per hour bucket over the last 7 days. Used by the timeline
    chart (continuous line at hourly resolution across days) and as the
    base for the cumulative chart."""
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%d %H:00', asked_at) AS bucket, COUNT(*) AS n
        FROM approvals
        WHERE asked_at >= datetime('now', '-7 days')
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    return [(r["bucket"], r["n"]) for r in rows]


def minute_counts_60m(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Counts per minute over the last 60 minutes. Drives the cumulative
    rolling-window chart (per-minute cumsum across a 60 min wide x-axis
    that slides forward as time passes)."""
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%d %H:%M', asked_at) AS bucket, COUNT(*) AS n
        FROM approvals
        WHERE asked_at >= datetime('now', '-60 minutes')
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    return [(r["bucket"], r["n"]) for r in rows]


def total_before(conn: sqlite3.Connection, iso_cutoff: str) -> int:
    """Count of approvals strictly before the given ISO timestamp. Used
    to seed the cumulative chart so its Y-axis tracks the all-time
    running total even though only the last 60 minutes are visible."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM approvals WHERE asked_at < ?",
        (iso_cutoff,),
    ).fetchone()
    return int(row["n"])


def daily_counts_all(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """All-time counts per day. Drives the cumulative chart."""
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%d', asked_at) AS bucket, COUNT(*) AS n
        FROM approvals
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    return [(r["bucket"], r["n"]) for r in rows]


def last_approved(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Most recent row that resolved as approved (1)."""
    return conn.execute(
        """
        SELECT * FROM approvals
        WHERE approved = 1 AND decided_at IS NOT NULL
        ORDER BY decided_at DESC
        LIMIT 1
        """
    ).fetchone()


def prompt_rate_per_minute(
    conn: sqlite3.Connection, pane: str, window_minutes: int
) -> float:
    """Average prompts-per-minute on ``pane`` over the last ``window_minutes``.
    Used by the runaway-loop alarm: a rate sustained above some threshold
    is the signal that an agent is stuck retrying the same gate."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM approvals
        WHERE source = ?
          AND asked_at >= datetime('now', '-{int(window_minutes)} minutes')
        """,
        (pane,),
    ).fetchone()
    return float(row["n"]) / max(window_minutes, 1)


def set_alarm(conn: sqlite3.Connection, pane: str, rate_per_min: float) -> None:
    conn.execute(
        """
        INSERT INTO pane_alarms (pane, alarmed_at, rate_per_min)
        VALUES (?, ?, ?)
        ON CONFLICT(pane) DO UPDATE SET
          alarmed_at = excluded.alarmed_at,
          rate_per_min = excluded.rate_per_min
        """,
        (pane, now_iso(), rate_per_min),
    )


def clear_alarm(conn: sqlite3.Connection, pane: str | None = None) -> int:
    """Clear one alarm, or all alarms if ``pane`` is None. Returns the
    number of rows removed."""
    if pane is None:
        return conn.execute("DELETE FROM pane_alarms").rowcount
    return conn.execute("DELETE FROM pane_alarms WHERE pane = ?", (pane,)).rowcount


def is_alarmed(conn: sqlite3.Connection, pane: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pane_alarms WHERE pane = ?", (pane,)
    ).fetchone()
    return row is not None


def list_alarms(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pane_alarms ORDER BY alarmed_at DESC"
    ).fetchall()


def recent_approved(
    conn: sqlite3.Connection, limit: int = 100
) -> list[sqlite3.Row]:
    """Most recently approved rows, newest first. Drives the
    'Recent approvals' tab (tabular view)."""
    return conn.execute(
        """
        SELECT * FROM approvals
        WHERE approved = 1 AND decided_at IS NOT NULL
        ORDER BY decided_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def daily_counts_30d(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%d', asked_at) AS bucket, COUNT(*) AS n
        FROM approvals
        WHERE asked_at >= datetime('now', '-30 days')
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    return [(r["bucket"], r["n"]) for r in rows]


def total_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM approvals WHERE asked_at >= datetime('now', 'start of day')"
    ).fetchone()
    return int(row["n"])


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE approved IS NULL").fetchone()
    return int(row["n"])


def recent(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
