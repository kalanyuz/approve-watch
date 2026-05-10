from __future__ import annotations

from approve_watch.db import (
    claim_decision,
    clear_alarm,
    connect,
    daily_counts_since,
    fetch_decision,
    get_row,
    hourly_counts_7d,
    insert_pending,
    is_alarmed,
    last_approved,
    list_alarms,
    list_pending,
    minute_counts_60m,
    pending_count,
    prompt_rate_per_minute,
    recent_approved,
    set_alarm,
    set_label,
    total_before,
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


def test_recent_approved_orders_by_decided_at_desc(tmp_db) -> None:
    """Drives the 'Recent approvals' tab's tabular view."""
    with connect(tmp_db) as conn:
        # Empty DB.
        assert recent_approved(conn) == []

        a = insert_pending(conn, "ls", "tmux:s:0.0")
        b = insert_pending(conn, "pwd", "tmux:s:0.0")
        c = insert_pending(conn, "rm -rf /tmp/x", "tmux:s:0.0", kind="other")
        d = insert_pending(conn, "still-pending", "tmux:s:0.0")

        # a, then c, then b — but rejecting one should exclude it.
        claim_decision(conn, a, approved=1, decided_by="user")
        claim_decision(conn, c, approved=0, decided_by="user")  # rejected
        claim_decision(conn, b, approved=1, decided_by="auto-watcher")

        rows = recent_approved(conn)
        # `b` decided last → first; `a` decided first → second; `c` and
        # `d` excluded (rejected / still pending).
        assert [r["id"] for r in rows] == [b, a]

        # Limit honours the smaller bound.
        assert len(recent_approved(conn, limit=1)) == 1


def test_hourly_counts_7d_and_minute_counts_60m(tmp_db) -> None:
    with connect(tmp_db) as conn:
        # Empty DB — both queries return empty.
        assert hourly_counts_7d(conn) == []
        assert minute_counts_60m(conn) == []

        for cmd in ["a", "b", "c"]:
            insert_pending(conn, cmd, "tmux:s:0.0")

        # All three rows fall in the same hour bucket and the same
        # minute bucket, so each query returns a single bucket of size 3.
        hours = hourly_counts_7d(conn)
        minutes = minute_counts_60m(conn)
        assert sum(n for _, n in hours) == 3
        assert len(minutes) == 1
        assert minutes[0][1] == 3


def test_total_before_seeds_cumulative_chart(tmp_db) -> None:
    """The CumulativeChart needs an all-time count from before its
    60-minute viewport so the visible line continues from history rather
    than restarting at zero each time."""
    with connect(tmp_db) as conn:
        assert total_before(conn, "9999-01-01") == 0

        for cmd in ["a", "b", "c"]:
            insert_pending(conn, cmd, "tmux:s:0.0")

        # All three rows are before any future timestamp.
        assert total_before(conn, "9999-01-01") == 3
        # None are before the unix epoch.
        assert total_before(conn, "1970-01-01") == 0


def test_pane_alarm_set_clear_check(tmp_db) -> None:
    """Per-pane alarm state for the runaway-loop detector."""
    with connect(tmp_db) as conn:
        assert is_alarmed(conn, "tmux:s:0.0") is False
        assert list_alarms(conn) == []

        set_alarm(conn, "tmux:s:0.0", rate_per_min=12.5)
        assert is_alarmed(conn, "tmux:s:0.0") is True
        assert is_alarmed(conn, "tmux:other:0.0") is False
        rows = list_alarms(conn)
        assert len(rows) == 1
        assert rows[0]["pane"] == "tmux:s:0.0"
        assert rows[0]["rate_per_min"] == 12.5

        # Re-setting on the same pane updates the rate (UPSERT).
        set_alarm(conn, "tmux:s:0.0", rate_per_min=20.0)
        rows = list_alarms(conn)
        assert len(rows) == 1
        assert rows[0]["rate_per_min"] == 20.0

        # Targeted clear.
        assert clear_alarm(conn, "tmux:s:0.0") == 1
        assert is_alarmed(conn, "tmux:s:0.0") is False

        # Bulk clear.
        set_alarm(conn, "tmux:a:0.0", 11.0)
        set_alarm(conn, "tmux:b:0.0", 13.0)
        assert clear_alarm(conn) == 2
        assert list_alarms(conn) == []


def test_prompt_rate_per_minute_counts_only_target_pane(tmp_db) -> None:
    """The runaway alarm fires per-pane; the rate query must scope by source."""
    with connect(tmp_db) as conn:
        # Empty DB.
        assert prompt_rate_per_minute(conn, "tmux:s:0.0", window_minutes=2) == 0.0

        # 8 prompts on s:0.0 within the last 2 minutes → 4/min.
        for _ in range(8):
            insert_pending(conn, "cmd", "tmux:s:0.0")
        # 2 prompts on a different pane in the same window — must not
        # count toward s:0.0's rate.
        for _ in range(2):
            insert_pending(conn, "cmd", "tmux:other:0.0")

        assert prompt_rate_per_minute(conn, "tmux:s:0.0", window_minutes=2) == 4.0
        assert prompt_rate_per_minute(conn, "tmux:other:0.0", window_minutes=2) == 1.0
        assert prompt_rate_per_minute(conn, "tmux:nope:0.0", window_minutes=2) == 0.0


def test_insert_pending_stores_context(tmp_db) -> None:
    """The flight recorder writes the surrounding pane snippet into the
    `context` column at insert time."""
    snippet = "$ ls\nfile1\nRun this command?\n→ Run (y)\n  Skip (esc or n)"
    with connect(tmp_db) as conn:
        rid = insert_pending(
            conn, "ls", "tmux:s:0.0", kind="shell_command", context=snippet
        )
        row = get_row(conn, rid)
    assert row is not None
    assert row["context"] == snippet


def test_get_row_returns_none_for_missing(tmp_db) -> None:
    with connect(tmp_db) as conn:
        assert get_row(conn, 999) is None


def test_daily_counts_since_window_and_grouping(tmp_db) -> None:
    """Drives the 365-day heatmap. Counts within the window are bucketed
    by day; rows older than the window are excluded."""
    with connect(tmp_db) as conn:
        # Empty.
        assert daily_counts_since(conn, 365) == []

        # Three rows today, one row "long ago" (manually backdated).
        for _ in range(3):
            insert_pending(conn, "x", "tmux:s:0.0")
        conn.execute(
            "INSERT INTO approvals (asked_at, command, source, kind) "
            "VALUES (datetime('now', '-2 years'), 'old', 'tmux:s:0.0', 'shell_command')"
        )

        # 365-day window excludes the 2-year-old row.
        rows_year = daily_counts_since(conn, 365)
        assert sum(n for _, n in rows_year) == 3
        assert len(rows_year) == 1  # all three of today fall in one bucket

        # 30-day window same behavior here.
        rows_month = daily_counts_since(conn, 30)
        assert sum(n for _, n in rows_month) == 3
