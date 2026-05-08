from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from approve_watch.db import connect, last_approved


def _humanise(iso: str | None) -> str:
    """Render an ISO timestamp as 'just now / Nm ago / hh:mm:ss'. Mirrors
    the compactness of Dolphie's metric panels — readable at a glance
    without taking up the whole row."""
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:19]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return ts.astimezone().strftime("%Y-%m-%d %H:%M")


class _Stat(Vertical):
    """A single Dolphie-style label-over-value cell."""

    DEFAULT_CSS = """
    _Stat { width: auto; height: 3; padding: 0 2 0 0; }
    _Stat .stat-label { color: $text-muted; text-style: bold; }
    _Stat .stat-value { color: $text; }
    """

    def __init__(self, label: str, value: str, value_style: str = "") -> None:
        super().__init__()
        self._label = label
        self._value = value
        self._value_style = value_style

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="stat-label")
        cls = "stat-value"
        if self._value_style:
            cls += f" {self._value_style}"
        yield Static(self._value or "—", classes=cls)


class LastApprovedPanel(Horizontal):
    """Middle-row panel: most recent approved command + metadata, laid out
    as side-by-side label/value cells. Repolls itself; tells the user at a
    glance what just got auto-approved (or by them, manually)."""

    DEFAULT_CSS = """
    LastApprovedPanel {
        height: 5;
        border: round $accent;
        padding: 0 1;
        background: $boost;
    }
    LastApprovedPanel.empty { border: round $surface; }
    LastApprovedPanel .stat-cmd { color: $success; text-style: bold; }
    LastApprovedPanel .stat-empty {
        color: $text-muted; text-style: italic;
        width: 100%; content-align: center middle;
    }
    """

    BORDER_TITLE = "Last approved"

    def __init__(self) -> None:
        super().__init__(id="last-approved")
        self.border_title = self.BORDER_TITLE

    def compose(self) -> ComposeResult:
        # Filled in by refresh_data on mount.
        yield Static("loading…", classes="stat-empty")

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        with connect() as conn:
            row = last_approved(conn)

        for child in list(self.children):
            child.remove()

        if row is None:
            self.add_class("empty")
            self.mount(Static("nothing approved yet", classes="stat-empty"))
            return
        self.remove_class("empty")

        when = _humanise(row["decided_at"])
        kind = row["kind"] if "kind" in row.keys() else "shell_command"
        decided_by = row["decided_by"] or "—"
        source = row["source"] or "—"
        command = (row["command"] or "—").strip()

        self.mount(_Stat("WHEN", when))
        self.mount(_Stat("KIND", kind))
        self.mount(_Stat("BY", decided_by))
        self.mount(_Stat("SOURCE", source))
        self.mount(_Stat("COMMAND", command, value_style="stat-cmd"))
