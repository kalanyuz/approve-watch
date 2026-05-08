from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.widgets import Footer, Header, Static

from approve_watch.db import connect, list_pending, pending_count, total_today
from approve_watch.dashboard.cards import ApprovalCard
from approve_watch.dashboard.charts import DailyChart, HourlyChart
from approve_watch.dashboard.labels import LabelPane


class ApproveWatchApp(App[None]):
    """Two-row dashboard: charts on top, approval-card queue on bottom."""

    CSS = """
    Screen { layers: base overlay; }
    #root { height: 100%; }
    #charts { height: 50%; padding: 0 1; }
    #charts > * { width: 1fr; height: 100%; padding: 0 1; }
    #queue-row { height: 50%; border-top: solid $primary; }
    #queue-label { width: 18; padding: 1 1; color: $text-muted; }
    #queue { height: 100%; }
    #status-bar { height: 1; padding: 0 1; background: $boost; }
    LabelPane { dock: right; layer: overlay; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("l", "toggle_labels", "Labels"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    POLL_PENDING_S = 0.2
    REFRESH_CHARTS_S = 5.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            self._status = Static("loading…", id="status-bar")
            yield self._status
            with Horizontal(id="charts"):
                self._hourly = HourlyChart()
                self._daily = DailyChart()
                yield self._hourly
                yield self._daily
            with Horizontal(id="queue-row"):
                yield Static("Pending →", id="queue-label")
                self._queue = HorizontalScroll(id="queue")
                yield self._queue
        self._labels = LabelPane()
        yield self._labels
        yield Footer()

    def on_mount(self) -> None:
        self._known_pending: set[int] = set()
        self._refresh_charts()
        self._refresh_status()
        self.set_interval(self.POLL_PENDING_S, self._poll_pending)
        self.set_interval(self.REFRESH_CHARTS_S, self._refresh_charts)
        self.set_interval(1.0, self._refresh_status)

    def _refresh_charts(self) -> None:
        self._hourly.refresh_data()
        self._daily.refresh_data()

    def _refresh_status(self) -> None:
        with connect() as conn:
            today = total_today(conn)
            pending = pending_count(conn)
        self._status.update(
            f"approve-watch · {today} today · {pending} pending · q quit · l labels"
        )

    def _poll_pending(self) -> None:
        with connect() as conn:
            rows = list_pending(conn)
        for r in rows:
            rid = int(r["id"])
            if rid in self._known_pending:
                continue
            card = ApprovalCard(
                row_id=rid,
                command=r["command"],
                source=r["source"],
                asked_at=r["asked_at"],
            )
            self._queue.mount(card)
            self._known_pending.add(rid)

    def on_approval_card_resolved(self, message: ApprovalCard.Resolved) -> None:
        self._known_pending.discard(message.row_id)

    def action_toggle_labels(self) -> None:
        self._labels.toggle()

    def action_refresh_now(self) -> None:
        self._refresh_charts()
        self._refresh_status()
