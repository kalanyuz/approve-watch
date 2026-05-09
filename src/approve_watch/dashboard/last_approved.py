from __future__ import annotations

from datetime import datetime, timezone

from textual.widgets import Static

from approve_watch.db import connect, last_approved


def _humanise(iso: str | None) -> str:
    """Render an ISO timestamp as 'just now / Nm ago / 2026-05-08 09:30'.
    Mirrors the compactness of Dolphie's metric panels — readable at a
    glance without taking up the whole row."""
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


class LastApprovedPanel(Static):
    """Middle-row panel: most recent approved command + metadata. A
    single Static with Rich markup — earlier label/value cell layout
    collapsed to zero width inside a Horizontal, leaving the panel
    visually empty even though its widgets had mounted."""

    DEFAULT_CSS = """
    LastApprovedPanel {
        height: 5;
        border: round $accent;
        padding: 0 1;
        background: $boost;
    }
    LastApprovedPanel.empty { border: round $surface; color: $text-muted; }
    """

    BORDER_TITLE = "Last approved"

    def __init__(self) -> None:
        super().__init__("loading…", id="last-approved", markup=True)
        self.border_title = self.BORDER_TITLE

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        with connect() as conn:
            row = last_approved(conn)

        if row is None:
            self.add_class("empty")
            self.update("[i]nothing approved yet[/i]")
            return
        self.remove_class("empty")

        when = _humanise(row["decided_at"])
        kind = row["kind"] if "kind" in row.keys() else "shell_command"
        decided_by = row["decided_by"] or "—"
        source = row["source"] or "—"
        command = (row["command"] or "—").strip()

        self.update(
            f"[b]WHEN[/b] {when}    "
            f"[b]KIND[/b] {kind}    "
            f"[b]BY[/b] {decided_by}    "
            f"[b]SRC[/b] {source}\n"
            f"[b]CMD[/b] [green b]{command}[/green b]"
        )
