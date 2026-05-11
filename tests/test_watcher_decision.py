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


async def test_flight_recorder_captures_context(tmp_db: Path) -> None:
    """The watcher persists the matched prompt block plus a few
    surrounding lines from the pane buffer at decision time."""
    pane_buffer = (
        "$ ls -la\n"
        "drwxr-xr-x  3 user user  4096 May  9 09:00 .\n"
        "drwxr-xr-x 12 user user  4096 May  9 08:00 ..\n"
        "$ git diff --cached\n"
        "[diff output…]\n"
        + SHELL_PROMPT
        + "$ \n"
    )
    src = FakeSource(["s:0.0"])
    src._buffers["s:0.0"] = pane_buffer
    seen = _SignatureCache()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1
    ctx = rows[0]["context"]
    assert ctx is not None
    assert "Run this command?" in ctx, "matched block missing from context"
    assert "git diff --cached" in ctx, "pre-context missing"


def _shell_prompt(cmd: str) -> str:
    return (
        f"Run this command?\n"
        f"Not in allowlist: {cmd}\n"
        f"→ Run (once) (y)\n"
        f"  Skip (esc or n)\n"
    )


class QueueSource(FakeSource):
    """FakeSource that simulates a queue of distinct cursor-agent prompts.
    Each y/n keystroke advances to the next buffer in the list (or to a
    cleared state when the queue is empty)."""

    def __init__(self, pane: PaneId, queue: list[str]) -> None:
        super().__init__([pane])
        self._pane = pane
        self._queue = list(queue)
        self._buffers[pane] = self._queue[0] if self._queue else CLEARED_TEXT

    def _advance(self, pane: PaneId) -> None:
        # Pop the head, advance to the next prompt, or clear when done.
        if self._queue:
            self._queue.pop(0)
        self._buffers[pane] = self._queue[0] if self._queue else CLEARED_TEXT

    def send_approve(self, pane: PaneId) -> None:
        self.sent.append((pane, "y"))
        self._advance(pane)

    def send_reject(self, pane: PaneId) -> None:
        self.sent.append((pane, "n"))
        self._advance(pane)


async def test_drains_consecutive_prompts_in_one_call(tmp_db: Path) -> None:
    """When cursor-agent shows a queue of approval prompts back-to-back
    on the same pane, the watcher must answer all of them inside one
    _handle_pane call rather than returning after the first and waiting
    for the next watch_loop poll (which dropped prompts in practice)."""
    queue = [
        _shell_prompt("git log --oneline -3"),
        _shell_prompt("git status --short --branch"),
        _shell_prompt("git diff --cached"),
    ]
    src = QueueSource("s:0.0", queue)
    seen = _SignatureCache()

    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )

    # Three keystrokes (one per queued prompt), three rows inserted, all
    # auto-approved by the watcher.
    assert src.sent == [("s:0.0", "y"), ("s:0.0", "y"), ("s:0.0", "y")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 3
    commands = sorted(r["command"] for r in rows)
    assert commands == [
        "git diff --cached",
        "git log --oneline -3",
        "git status --short --branch",
    ]
    for r in rows:
        assert r["decided_by"] == "auto-watcher"
        assert r["approved"] == 1


async def test_drain_stops_at_first_unhandled_prompt(tmp_db: Path) -> None:
    """If the same-signature prompt is still showing after the
    post-decision window (slow repaint / dropped keystroke), the drain
    bails out cleanly without spinning."""

    class StuckQueueSource(FakeSource):
        def send_approve(self, pane: PaneId) -> None:
            self.sent.append((pane, "y"))
            # Buffer never changes — simulates cursor-agent never
            # repainting after our keystroke.

    src = StuckQueueSource(["s:0.0"])  # initial buffer is SHELL_PROMPT
    seen = _SignatureCache()
    await _handle_pane(
        src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS,
        post_decision_timeout=0.3,
    )
    assert src.sent == [("s:0.0", "y")]  # exactly one
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1
