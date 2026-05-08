from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from approve_watch import config as cfg_mod
from approve_watch.config import DEFAULT_PROMPT_REGEX
from approve_watch.db import claim_decision, connect, recent
from approve_watch.detector import Detector
from approve_watch.sources.base import PaneId
from approve_watch.watcher import _SignatureCache, _handle_pane, watch_loop


PROMPT_TEXT = (
    "Some output\n"
    "Run this command in your shell?\n"
    "$ ls -la\n"
    "(y/N): "
)
CLEARED_TEXT = "ls -la\nfile1 file2\n"


class FakeSource:
    name = "tmux"

    def __init__(self, panes: list[PaneId]) -> None:
        self._panes = panes
        self._buffers: dict[PaneId, str] = {p: PROMPT_TEXT for p in panes}
        self.sent: list[tuple[PaneId, str]] = []

    def list_panes(self) -> list[PaneId]:
        return list(self._panes)

    def capture(self, pane: PaneId) -> str:
        return self._buffers.get(pane, "")

    def send_enter(self, pane: PaneId) -> None:
        self.sent.append((pane, "Enter"))
        self._buffers[pane] = CLEARED_TEXT

    def send_reject(self, pane: PaneId) -> None:
        self.sent.append((pane, "n+Enter"))
        self._buffers[pane] = CLEARED_TEXT


@pytest.fixture
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the watcher's 3.2s timeout so tests complete quickly."""
    monkeypatch.setattr(cfg_mod, "WATCHER_TIMEOUT_S", 0.5)
    monkeypatch.setattr(cfg_mod, "DECISION_POLL_S", 0.02)
    # Re-import the watcher's module-level constants we shadowed.
    import approve_watch.watcher as w

    monkeypatch.setattr(w, "WATCHER_TIMEOUT_S", 0.5)
    monkeypatch.setattr(w, "DECISION_POLL_S", 0.02)


async def test_auto_approves_after_timeout(tmp_db: Path, fast_timeouts: None) -> None:
    src = FakeSource(["s:0.0"])
    detector = Detector(DEFAULT_PROMPT_REGEX)
    seen = _SignatureCache()
    t0 = time.monotonic()
    await _handle_pane(src, "s:0.0", detector, seen, tmp_db)
    elapsed = time.monotonic() - t0
    assert 0.4 <= elapsed <= 1.5

    assert src.sent == [("s:0.0", "Enter")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert len(rows) == 1
    assert rows[0]["approved"] == 1
    assert rows[0]["decided_by"] == "auto-watcher"
    assert rows[0]["command"] == "ls -la"
    assert rows[0]["source"] == "tmux:s:0.0"


async def test_user_approval_preempts(tmp_db: Path, fast_timeouts: None) -> None:
    src = FakeSource(["s:0.0"])
    detector = Detector(DEFAULT_PROMPT_REGEX)
    seen = _SignatureCache()

    async def user_decides() -> None:
        await asyncio.sleep(0.1)
        # The watcher inserted a row by now; find and decide it.
        with connect(tmp_db) as conn:
            rid = conn.execute("SELECT id FROM approvals").fetchone()[0]
            assert claim_decision(conn, rid, approved=1, decided_by="user")

    await asyncio.gather(
        _handle_pane(src, "s:0.0", detector, seen, tmp_db),
        user_decides(),
    )

    assert src.sent == [("s:0.0", "Enter")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert rows[0]["decided_by"] == "user"


async def test_user_reject_sends_n(tmp_db: Path, fast_timeouts: None) -> None:
    src = FakeSource(["s:0.0"])
    detector = Detector(DEFAULT_PROMPT_REGEX)
    seen = _SignatureCache()

    async def user_decides() -> None:
        await asyncio.sleep(0.1)
        with connect(tmp_db) as conn:
            rid = conn.execute("SELECT id FROM approvals").fetchone()[0]
            assert claim_decision(conn, rid, approved=0, decided_by="user")

    await asyncio.gather(
        _handle_pane(src, "s:0.0", detector, seen, tmp_db),
        user_decides(),
    )

    assert src.sent == [("s:0.0", "n+Enter")]
    with connect(tmp_db) as conn:
        rows = recent(conn)
    assert rows[0]["approved"] == 0
    assert rows[0]["decided_by"] == "user"


async def test_no_match_no_action(tmp_db: Path, fast_timeouts: None) -> None:
    src = FakeSource(["s:0.0"])
    src._buffers["s:0.0"] = "boring output\n$ pwd\n/home/me\n"
    detector = Detector(DEFAULT_PROMPT_REGEX)
    seen = _SignatureCache()
    await _handle_pane(src, "s:0.0", detector, seen, tmp_db)
    assert src.sent == []
    with connect(tmp_db) as conn:
        assert recent(conn) == []


async def test_watch_loop_can_stop(tmp_db: Path, fast_timeouts: None) -> None:
    src = FakeSource([])
    detector = Detector(DEFAULT_PROMPT_REGEX)
    stop = asyncio.Event()
    task = asyncio.create_task(watch_loop(src, detector, tmp_db, stop=stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
