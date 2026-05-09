from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, ProgressBar, Static

from approve_watch.config import KIND_DANGEROUS, KIND_DASHBOARD_TIMEOUTS_S, KIND_SHELL
from approve_watch.db import claim_decision, connect, fetch_decision

TICK_S = 0.1


class ApprovalCard(Vertical):
    """A single approval prompt awaiting decision. Layout mirrors
    cursor-agent's own confirmation TUI: command at the top, then a
    countdown bar, then a vertical stack of outlined Yes/No choices.
    On expiry, auto-approves; the watcher in another process picks up
    the row decision and injects the keystroke."""

    DEFAULT_CSS = """
    ApprovalCard {
        width: 38;
        height: 100%;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        background: $boost;
    }
    ApprovalCard.kind-other     { border: round $warning; }
    ApprovalCard.kind-dangerous {
        border: heavy $error;
        background: $error 15%;
    }
    ApprovalCard.resolved       { border: round $success; }
    ApprovalCard .meta { color: $text-muted; }
    ApprovalCard .cmd  { color: $text; text-style: bold; padding: 1 0; }
    ApprovalCard.kind-dangerous .cmd { color: $error; text-style: bold; }
    ApprovalCard .kind-badge { color: $warning; text-style: bold; }
    ApprovalCard.kind-dangerous .kind-badge {
        color: $error; text-style: bold reverse;
    }
    ApprovalCard ProgressBar { width: 100%; height: 1; padding: 0 0 1 0; }

    ApprovalCard .actions { height: auto; width: 100%; }
    ApprovalCard .actions Button {
        width: 100%;
        height: 3;
        margin: 0 0 1 0;
        border: round $accent;
        background: $boost;
        color: $text;
    }
    ApprovalCard .actions Button#yes {
        border: round $success;
        color: $success;
    }
    ApprovalCard .actions Button#no {
        border: round $error;
        color: $error;
    }
    ApprovalCard .actions Button:focus { text-style: bold; }
    """

    elapsed: reactive[float] = reactive(0.0)

    class Resolved(Message):
        def __init__(self, row_id: int) -> None:
            super().__init__()
            self.row_id = row_id

    def __init__(
        self,
        row_id: int,
        command: str,
        source: str,
        asked_at: str,
        kind: str = KIND_SHELL,
    ) -> None:
        super().__init__(id=f"card-{row_id}")
        self.row_id = row_id
        self.command = command
        self.source = source
        self.asked_at = asked_at
        self.kind = kind
        self._timeout = KIND_DASHBOARD_TIMEOUTS_S.get(
            kind, KIND_DASHBOARD_TIMEOUTS_S[KIND_SHELL]
        )
        self._timer = None
        self._resolved = False
        if kind == KIND_DANGEROUS:
            self.add_class("kind-dangerous")
        elif kind != KIND_SHELL:
            self.add_class("kind-other")

    def compose(self) -> ComposeResult:
        yield Static(f"#{self.row_id}  {self.asked_at[:19]}", classes="meta")
        yield Static(f"src: {self.source}", classes="meta")
        if self.kind == KIND_DANGEROUS:
            yield Static(
                "⚠  DANGEROUS — manual decision required",
                classes="kind-badge",
            )
        elif self.kind != KIND_SHELL:
            mins = int(self._timeout // 60)
            yield Static(
                f"[{self.kind}]  auto-approve in ~{mins}m",
                classes="kind-badge",
            )
        yield Static(self.command, classes="cmd")
        self._bar = ProgressBar(
            total=self._timeout, show_eta=False, show_percentage=False
        )
        yield self._bar
        yield Vertical(
            Button("Yes  (y)", id="yes"),
            Button("No  (esc or n)", id="no"),
            classes="actions",
        )

    def on_mount(self) -> None:
        self._bar.update(progress=0)
        self._timer = self.set_interval(TICK_S, self._tick)

    def _tick(self) -> None:
        if self._resolved:
            return
        self.elapsed = round(self.elapsed + TICK_S, 2)
        self._bar.update(progress=min(self.elapsed, self._timeout))
        if self.elapsed >= self._timeout:
            self._decide(approved=1, by="auto-dashboard")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self._decide(approved=1, by="user")
        elif event.button.id == "no":
            self._decide(approved=0, by="user")

    def _decide(self, *, approved: int, by: str) -> None:
        if self._resolved:
            return
        with connect() as conn:
            existing_approved, _ = fetch_decision(conn, self.row_id)
            if existing_approved is not None:
                self._resolved = True
            elif claim_decision(conn, self.row_id, approved=approved, decided_by=by):
                self._resolved = True
            else:
                self._resolved = True
        if self._timer is not None:
            self._timer.stop()
        self.add_class("resolved")
        self.post_message(self.Resolved(self.row_id))
        self.set_timer(0.25, self.remove)
