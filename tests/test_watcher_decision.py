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
from approve_watch.db import claim_decision, connect, recent
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
    await _handle_pane(src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS)
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
    await _handle_pane(src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS)
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
        _handle_pane(src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS),
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
        _handle_pane(src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS),
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
    await _handle_pane(src, "s:0.0", make_detector(), seen, tmp_db, FAST_TIMEOUTS)
    assert src.sent == []
    with connect(tmp_db) as conn:
        assert recent(conn) == []


async def test_watch_loop_can_stop(tmp_db: Path) -> None:
    src = FakeSource([])
    stop = asyncio.Event()
    task = asyncio.create_task(
        watch_loop(src, make_detector(), tmp_db, stop=stop, timeouts=FAST_TIMEOUTS)
    )
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
