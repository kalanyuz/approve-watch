from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, ProgressBar, Static

from approve_watch.config import DASHBOARD_TIMEOUT_S
from approve_watch.db import claim_decision, connect, fetch_decision

TICK_S = 0.1


class ApprovalCard(Vertical):
    """A single approval prompt awaiting user decision.

    Has a 3 s countdown; on expiry, auto-approves. The watcher in another
    process will see the row decision and inject the keystroke.
    """

    DEFAULT_CSS = """
    ApprovalCard {
        width: 40;
        height: 100%;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        background: $boost;
    }
    ApprovalCard.resolved { border: round $success; }
    ApprovalCard .cmd { color: $text; text-style: bold; }
    ApprovalCard .meta { color: $text-muted; }
    ApprovalCard Horizontal { height: 3; }
    ApprovalCard Button { width: 1fr; }
    ApprovalCard ProgressBar { width: 100%; height: 1; }
    """

    elapsed: reactive[float] = reactive(0.0)

    class Resolved(Message):
        def __init__(self, row_id: int) -> None:
            super().__init__()
            self.row_id = row_id

    def __init__(self, row_id: int, command: str, source: str, asked_at: str) -> None:
        super().__init__(id=f"card-{row_id}")
        self.row_id = row_id
        self.command = command
        self.source = source
        self.asked_at = asked_at
        self._timer = None
        self._resolved = False

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal

        yield Static(f"#{self.row_id}  {self.asked_at[:19]}", classes="meta")
        yield Static(f"src: {self.source}", classes="meta")
        yield Static(self.command, classes="cmd")
        self._bar = ProgressBar(total=DASHBOARD_TIMEOUT_S, show_eta=False, show_percentage=False)
        yield self._bar
        yield Horizontal(
            Button("Yes", id="yes", variant="success"),
            Button("No", id="no", variant="error"),
        )

    def on_mount(self) -> None:
        self._bar.update(progress=0)
        self._timer = self.set_interval(TICK_S, self._tick)

    def _tick(self) -> None:
        if self._resolved:
            return
        self.elapsed = round(self.elapsed + TICK_S, 2)
        self._bar.update(progress=min(self.elapsed, DASHBOARD_TIMEOUT_S))
        if self.elapsed >= DASHBOARD_TIMEOUT_S:
            self._decide(approved=1, by="auto-dashboard")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self._decide(approved=1, by="user")
        elif event.button.id == "no":
            self._decide(approved=0, by="user")

    def _decide(self, *, approved: int, by: str) -> None:
        if self._resolved:
            return
        # First check whether some other process already decided this row.
        with connect() as conn:
            existing_approved, existing_by = fetch_decision(conn, self.row_id)
            if existing_approved is not None:
                # Already decided elsewhere; just mark and animate out.
                self._resolved = True
            else:
                if claim_decision(conn, self.row_id, approved=approved, decided_by=by):
                    self._resolved = True
                else:
                    # Lost the race; refresh and treat as resolved.
                    self._resolved = True
        if self._timer is not None:
            self._timer.stop()
        self.add_class("resolved")
        self.post_message(self.Resolved(self.row_id))
        self.set_timer(0.25, self.remove)
