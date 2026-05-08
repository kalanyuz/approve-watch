from __future__ import annotations

from approve_watch.db import (
    claim_decision,
    connect,
    daily_counts_all,
    fetch_decision,
    hourly_counts_7d,
    insert_pending,
    last_approved,
    list_pending,
    pending_count,
    set_label,
)


def test_insert_pending_then_decide(tmp_db) -> None:
    with connect(tmp_db) as conn:
        rid = insert_pending(conn, command="ls -la", source="tmux:demo:0.0")
        assert rid > 0
        assert pending_count(conn) == 1
        approved, by = fetch_decision(conn, rid)
        assert approved is None and by is None

        ok = claim_decision(conn, rid, approved=1, decided_by="user")
        assert ok is True
        approved, by = fetch_decision(conn, rid)
        assert approved == 1 and by == "user"

        # second claim is a no-op (atomicity guard)
        ok = claim_decision(conn, rid, approved=0, decided_by="auto-watcher")
        assert ok is False
        approved, _ = fetch_decision(conn, rid)
        assert approved == 1


def test_list_pending_excludes_decided(tmp_db) -> None:
    with connect(tmp_db) as conn:
        a = insert_pending(conn, "echo a", "tmux:s:0.0")
        b = insert_pending(conn, "echo b", "tmux:s:0.0")
        claim_decision(conn, a, approved=1, decided_by="user")
        rows = list_pending(conn)
        assert [r["id"] for r in rows] == [b]


def test_set_label(tmp_db) -> None:
    with connect(tmp_db) as conn:
        rid = insert_pending(conn, "rm -rf /", "tmux:s:0.0")
        set_label(conn, rid, "danger")
        row = conn.execute("SELECT label FROM approvals WHERE id=?", (rid,)).fetchone()
        assert row["label"] == "danger"

        set_label(conn, rid, None)
        row = conn.execute("SELECT label FROM approvals WHERE id=?", (rid,)).fetchone()
        assert row["label"] is None


def test_last_approved_returns_most_recent_approval(tmp_db) -> None:
    """Drives the dashboard's middle-row 'last approved' panel."""
    with connect(tmp_db) as conn:
        # No approvals yet.
        assert last_approved(conn) is None

        a = insert_pending(conn, "ls -la", "tmux:s:0.0")
        b = insert_pending(conn, "rm -rf /tmp/x", "tmux:s:0.0", kind="other")
        c = insert_pending(conn, "git status", "tmux:s:0.0")

        # Only `b` is approved.
        claim_decision(conn, b, approved=1, decided_by="user")
        row = last_approved(conn)
        assert row is not None
        assert row["id"] == b
        assert row["command"] == "rm -rf /tmp/x"
        assert row["decided_by"] == "user"

        # Now approve `c` afterwards — must surface the more recent one.
        claim_decision(conn, c, approved=1, decided_by="auto-watcher")
        row = last_approved(conn)
        assert row is not None
        assert row["id"] == c

        # Rejected rows do not count.
        claim_decision(conn, a, approved=0, decided_by="user")
        row = last_approved(conn)
        assert row is not None
        assert row["id"] == c


def test_hourly_counts_7d_and_daily_counts_all(tmp_db) -> None:
    with connect(tmp_db) as conn:
        # Empty DB — both queries return empty.
        assert hourly_counts_7d(conn) == []
        assert daily_counts_all(conn) == []

        for cmd in ["a", "b", "c"]:
            insert_pending(conn, cmd, "tmux:s:0.0")

        # All three rows fall in the same hour bucket and the same day
        # bucket, so each query returns a single bucket of size 3.
        hours = hourly_counts_7d(conn)
        days = daily_counts_all(conn)
        assert sum(n for _, n in hours) == 3
        assert len(days) == 1
        assert days[0][1] == 3
