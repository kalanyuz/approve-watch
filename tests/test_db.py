from __future__ import annotations

from approve_watch.db import (
    claim_decision,
    connect,
    fetch_decision,
    insert_pending,
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
