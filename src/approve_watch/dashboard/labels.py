from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Input, Static

from approve_watch.db import connect, recent, set_label


class LabelPane(Vertical):
    """Sidebar for labeling historical approvals.

    Toggle with `l` from the main app. Pick a row, type a label, press Enter.
    """

    DEFAULT_CSS = """
    LabelPane { display: none; width: 60; border: round $primary; padding: 0 1; }
    LabelPane.-visible { display: block; }
    LabelPane DataTable { height: 1fr; }
    LabelPane Input { dock: bottom; }
    """

    def compose(self) -> ComposeResult:
        yield Static("Labels  (select row, type, Enter)", classes="meta")
        self._table = DataTable(zebra_stripes=True, cursor_type="row")
        yield self._table
        self._input = Input(placeholder="label for selected row…", id="label-input")
        yield self._input
        yield Footer()

    def on_mount(self) -> None:
        self._table.add_columns("id", "approved", "command", "label")
        self.refresh_rows()

    def refresh_rows(self) -> None:
        self._table.clear()
        with connect() as conn:
            rows = recent(conn, limit=200)
        for r in rows:
            ap = "?" if r["approved"] is None else ("Y" if r["approved"] == 1 else "N")
            self._table.add_row(
                str(r["id"]),
                ap,
                (r["command"] or "")[:50],
                r["label"] or "",
                key=str(r["id"]),
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "label-input":
            return
        if self._table.cursor_row < 0:
            return
        try:
            row_key = self._table.coordinate_to_cell_key((self._table.cursor_row, 0)).row_key
            row_id = int(row_key.value)
        except Exception:
            return
        label = event.value.strip() or None
        with connect() as conn:
            set_label(conn, row_id, label)
        event.input.value = ""
        self.refresh_rows()

    def toggle(self) -> None:
        self.toggle_class("-visible")
        if self.has_class("-visible"):
            self.refresh_rows()
            self._input.focus()
