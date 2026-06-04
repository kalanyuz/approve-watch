from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from approve_watch.db import (
    claim_decision,
    connect,
    init_db,
    insert_pending,
)


@pytest.fixture
def isolated_db(monkeypatch) -> Path:
    """Each card test gets its own DB file, set via APPROVE_WATCH_DB so
    the dashboard's connect() picks it up implicitly."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    p = Path(path)
    monkeypatch.setenv("APPROVE_WATCH_DB", str(p))
    init_db(p)
    yield p


async def test_card_dismisses_when_row_decided_externally(
    isolated_db: Path,
) -> None:
    """Regression: a row decided externally (user typing y in the tmux
    pane, watcher pre-claiming an alarm rejection, etc.) used to leave
    the card on the queue until its full timeout elapsed — most
    visibly for the 24h `dangerous` tier. The card's _tick now polls
    the DB and dismisses on external decisions."""
    from approve_watch.dashboard.app import ApproveWatchApp
    from approve_watch.dashboard.cards import ApprovalCard

    # Seed one pending row.
    with connect(isolated_db) as conn:
        rid = insert_pending(conn, "ls -la", "tmux:demo:0.0", kind="shell_command")

    app = ApproveWatchApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        cards = list(app.query(ApprovalCard))
        assert len(cards) == 1, "card should have mounted for the pending row"
        assert cards[0].row_id == rid

        # Simulate an external decision (the user pressed y in tmux).
        with connect(isolated_db) as conn:
            assert claim_decision(
                conn, rid, approved=1, decided_by="user-via-pane"
            )

        # _tick polls every TICK_S=0.1s; allow enough time for it to
        # notice and for the 0.25s self.remove timer to fire.
        await pilot.pause(0.6)

        cards_after = list(app.query(ApprovalCard))
        assert len(cards_after) == 0, (
            "card should have dismissed itself after the external decision"
        )


async def test_card_dismisses_when_dangerous_row_decided_externally(
    isolated_db: Path,
) -> None:
    """Same path as the previous test, but for the `dangerous` tier.
    Without the poll, the card would have sat for 24h waiting on its
    own timer."""
    from approve_watch.dashboard.app import ApproveWatchApp
    from approve_watch.dashboard.cards import ApprovalCard

    with connect(isolated_db) as conn:
        rid = insert_pending(
            conn, "rm -rf /etc/passwd", "tmux:demo:0.0", kind="dangerous"
        )

    app = ApproveWatchApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        cards = list(app.query(ApprovalCard))
        assert len(cards) == 1
        # The dangerous card's own auto-decision timer is 24 hours;
        # only external-decision polling can dismiss it in this test
        # window.
        assert cards[0]._timeout >= 86400

        with connect(isolated_db) as conn:
            claim_decision(
                conn, rid, approved=0, decided_by="user-via-pane"
            )

        await pilot.pause(0.6)

        cards_after = list(app.query(ApprovalCard))
        assert len(cards_after) == 0
