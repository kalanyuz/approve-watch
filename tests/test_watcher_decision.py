from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from approve_watch.config import (
    KIND_OTHER,
    KIND_SHELL,
    OTHER_PROMPT_REGEX,
    SHELL_COMMAND_REGEX,
)
from approve_watch.db import claim_decision, connect, recent, set_alarm
from approve_watch.detector import Detector
from approve_watch.sources.base import PaneId
from approve_watch.watcher import _SignatureCache, _handle_pane, watch_loop


SHELL_PROMPT = (
    "Run this command?\n"
    "Not in allowlist: ls -la\n"
    "→ Run (once) (y)\n"
    "  Skip (esc or n)\n"
)
DELETE_PROMPT = (
    "Delete this file?\n"
    "/tmp/some/file.txt\n"
    "→ Delete (y)\n"
    "  Skip (esc or n)\n"
)
CLEARED_TEXT = "ls -la\nfile1 file2\n"

# Per-test fast timeouts. Shell tier shrinks 3.2s -> 0.5s; "other" tier
# shrinks 1h -> 1.0s so we can verify it's distinct without sleeping for an
# hour.
FAST_TIMEOUTS = {KIND_SHELL: 0.5, KIND_OTHER: 1.0}


def make_detector() -> Detector:
    return Detector(SHELL_COMMAND_REGEX, OTHER_PROMPT_REGEX)


class FakeSource:
    name = "tmux"

    def __init__(self, panes: list[PaneId], prompt: str = SHELL_PROMPT) -> None:
        self._panes = panes
        self._buffers: dict[PaneId, str] = {p: prompt for p in panes}
        self.sent: list[tuple[PaneId, str]] = []

    def list_panes(self) -> list[PaneId]:
        return list(self._panes)

    def capture(self, pane: PaneId) -> str:
        return self._buffers.get(pane, "")

    def send_approve(self, pane: PaneId) -> None:
        self.sent.append((pane, "y"))
        self._buffers[pane] = CLEARED_TEXT

    def send_reject(self, pane: PaneId) -> None:
        self.sent.append((pane, "n"))
        self._buffers[pane] = CLEARED_TEXT


async def test_auto_approves_after_shell_timeout(tmp_db: Path) -> None:
    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()
    t0 = time.monotonic()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    elapsed = time.monotonic() - t0
    assert 0.4 <= elapsed <= 1.5

    assert src.sent == [("s:0.0", "y")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1
    assert rows[0]["approved"] == 1
    assert rows[0]["decided_by"] == "auto-watcher"
    assert rows[0]["command"] == "ls -la"
    assert rows[0]["source"] == "tmux:s:0.0"
    assert rows[0]["kind"] == KIND_SHELL


async def test_other_tier_uses_longer_timeout(tmp_db: Path) -> None:
    """A Delete-style prompt (other tier) waits longer than shell tier."""
    src = FakeSource(["s:0.0"], prompt=DELETE_PROMPT)
    seen = _SignatureCache()
    t0 = time.monotonic()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    elapsed = time.monotonic() - t0
    # 1.0s tier-2 vs 0.5s tier-1 — so we expect ~1.0s, never less than 0.9s.
    assert 0.9 <= elapsed <= 2.0
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert rows[0]["kind"] == KIND_OTHER
    assert rows[0]["command"] == "/tmp/some/file.txt"
    assert rows[0]["decided_by"] == "auto-watcher"


async def test_user_approval_preempts(tmp_db: Path) -> None:
    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()

    async def user_decides() -> None:
        await asyncio.sleep(0.1)
        with connect(tmp_db) as conn:
            rid = conn.execute("SELECT id FROM approvals").fetchone()[0]
            assert claim_decision(conn, rid, approved=1, decided_by="user")

    await asyncio.gather(
        _handle_pane(
            src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
            post_decision_timeout=0.3,
        ),
        user_decides(),
    )

    assert src.sent == [("s:0.0", "y")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert rows[0]["decided_by"] == "user"


async def test_user_reject_sends_n(tmp_db: Path) -> None:
    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()

    async def user_decides() -> None:
        await asyncio.sleep(0.1)
        with connect(tmp_db) as conn:
            rid = conn.execute("SELECT id FROM approvals").fetchone()[0]
            assert claim_decision(conn, rid, approved=0, decided_by="user")

    await asyncio.gather(
        _handle_pane(
            src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
            post_decision_timeout=0.3,
        ),
        user_decides(),
    )

    assert src.sent == [("s:0.0", "n")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert rows[0]["approved"] == 0
    assert rows[0]["decided_by"] == "user"


async def test_no_match_no_action(tmp_db: Path) -> None:
    src = FakeSource(["s:0.0"])
    src._buffers["s:0.0"] = "boring output\n$ pwd\n/home/me\nNo prompt to approve here.\n"
    seen = _SignatureCache()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    assert src.sent == []
    with connect(tmp_db) as conn:
        assert recent(conn) == []


async def test_watch_loop_can_stop(tmp_db: Path) -> None:
    src = FakeSource([])
    stop = asyncio.Event()
    task = asyncio.create_task(
        watch_loop(
            src, make_detector(), tmp_db, stop=stop, timeouts=FAST_TIMEOUTS,
            post_decision_timeout=0.3,
        )
    )
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_no_duplicate_when_buffer_does_not_clear(tmp_db: Path) -> None:
    """Regression: cursor-agent's TUI sometimes hasn't repainted by the time
    we capture again after sending y. Previously the watcher unconditionally
    retired the signature after a 2s window, so the next poll would see the
    stale prompt, treat it as new, insert a second row, and send a second y
    (which landed in the now-empty input box). The fix keeps the signature
    in `seen` until we observe the prompt is actually gone."""

    class StaleSource(FakeSource):
        def send_approve(self, pane: PaneId) -> None:
            # Record the keystroke but leave the buffer unchanged —
            # simulates a slow repaint or a dropped keystroke.
            self.sent.append((pane, "y"))

    src = StaleSource(["s:0.0"])
    seen = _SignatureCache()
    detector = make_detector()

    # First call: detect, decide, send y. Post-decision window expires
    # while the buffer still shows the prompt, so the signature stays in
    # `seen`.
    await _handle_pane(
        src, "s:0.0", detector, seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    assert src.sent == [("s:0.0", "y")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1

    # Second call against the still-stale buffer: the signature is in
    # `seen`, so we must not insert another row or send another keystroke.
    await _handle_pane(
        src, "s:0.0", detector, seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    assert src.sent == [("s:0.0", "y")], "no extra y should be sent"
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1, "no duplicate row should be inserted"


async def test_signature_retired_after_prompt_clears(tmp_db: Path) -> None:
    """Counterpart to the dedup test: when the buffer DOES clear (normal
    case), the signature is retired so a legitimate re-run of the same
    command is handled fresh."""
    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()
    detector = make_detector()

    await _handle_pane(
        src, "s:0.0", detector, seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    # After the call, FakeSource.send_approve cleared the buffer, so the
    # signature should have been retired.
    m = detector.match(SHELL_PROMPT)
    assert m is not None
    assert m.signature not in seen


async def test_alarmed_pane_auto_rejects_at_timeout(tmp_db: Path) -> None:
    """When a pane is marked as alarmed, the watcher's timeout-default
    flips from approve to reject so a runaway agent gets `n`'d back."""
    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()

    # Pre-arm the alarm so the very first prompt is rejected.
    with connect(tmp_db) as conn:
        set_alarm(conn, "tmux:s:0.0", rate_per_min=20.0)

    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )

    assert src.sent == [("s:0.0", "n")], "alarmed pane should send n, not y"
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1
    assert rows[0]["approved"] == 0
    assert rows[0]["decided_by"] == "auto-watcher-alarmed"


async def test_runaway_loop_trips_alarm_inline(tmp_db: Path, monkeypatch) -> None:
    """A burst of prompts on the same pane within the runaway window must
    set an alarm row so subsequent prompts auto-reject."""
    from approve_watch import config as cfg
    from approve_watch.db import is_alarmed

    # Tighten the threshold so two pre-existing rows are enough.
    monkeypatch.setattr(cfg, "RUNAWAY_THRESHOLD_PER_MIN", 1.0)
    import approve_watch.watcher as w
    monkeypatch.setattr(w, "RUNAWAY_THRESHOLD_PER_MIN", 1.0)

    # Seed the DB with prior rows on the target pane so the in-window
    # rate already exceeds threshold.
    with connect(tmp_db) as conn:
        for _ in range(5):
            conn.execute(
                "INSERT INTO approvals (asked_at, command, source, kind) "
                "VALUES (datetime('now'), 'x', 'tmux:s:0.0', 'shell_command')"
            )

    src = FakeSource(["s:0.0"])
    seen = _SignatureCache()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )

    with connect(tmp_db) as conn:
        assert is_alarmed(conn, "tmux:s:0.0") is True
    # And because the alarm was set during this very call, the default
    # decision factory ran with alarmed=True → n was sent.
    assert src.sent == [("s:0.0", "n")]
