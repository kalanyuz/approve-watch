from __future__ import annotations

from textual.widgets import DataTable

from approve_watch.db import connect, recent_approved

MAX_ROWS = 100
COMMAND_TRUNC = 80


class RecentApprovedTable(DataTable):
    """Tabular view of the most recently approved commands. Columns mirror
    the dense Dolphie processlist layout: numeric id, time-of-decision,
    kind, who decided, pane source, and the command itself."""

    DEFAULT_CSS = """
    RecentApprovedTable {
        height: 100%;
        background: $surface;
    }
    """

    COLUMNS = ("ID", "Decided", "Kind", "By", "Source", "Command")

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(*self.COLUMNS)
        self.refresh_data()

    def refresh_data(self) -> None:
        # Re-render in place rather than rebuilding to keep the cursor's
        # row stable when the user is browsing.
        self.clear()
        with connect() as conn:
            rows = recent_approved(conn, limit=MAX_ROWS)
        for r in rows:
            decided = (r["decided_at"] or "")[11:19] or "—"
            kind = r["kind"] if "kind" in r.keys() else "shell_command"
            command = (r["command"] or "—").strip()
            if len(command) > COMMAND_TRUNC:
                command = command[: COMMAND_TRUNC - 1] + "…"
            self.add_row(
                str(r["id"]),
                decided,
                kind or "shell_command",
                r["decided_by"] or "—",
                r["source"] or "—",
                command,
            )
