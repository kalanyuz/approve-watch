from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from approve_watch.db import connect, list_pending, pending_count, total_today
from approve_watch.dashboard.cards import ApprovalCard
from approve_watch.dashboard.charts import CumulativeChart, TimelineChart
from approve_watch.dashboard.heatmap import HeatmapView
from approve_watch.dashboard.labels import LabelPane
from approve_watch.dashboard.recent_approved import RecentApprovedTable


class ApproveWatchApp(App[None]):
    """Two-row Dolphie-style dashboard:

    * Top — TabbedContent: Cumulative (default), Timeline (7d), Recent.
    * Bottom — pending-approvals queue with cursor-prompt-style cards.
    """

    CSS = """
    Screen { layers: base overlay; }
    #root { height: 100%; }

    #status-bar { height: 1; padding: 0 1; background: $boost; }

    #charts-tabs { height: 65%; padding: 0 1; }
    #charts-tabs ContentSwitcher { height: 1fr; }
    #charts-tabs TabbedContent { height: 100%; }
    #charts-tabs Tabs { background: $surface; }

    #queue-row { height: 35%; border-top: solid $primary; }
    #queue-label { width: 18; padding: 1 1; color: $text-muted; }
    #queue { height: 100%; }

    LabelPane { dock: right; layer: overlay; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("l", "toggle_labels", "Labels"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("1", "show_tab('cum')", "Cumulative"),
        Binding("2", "show_tab('timeline')", "7d"),
        Binding("3", "show_tab('recent')", "Recent"),
        Binding("4", "show_tab('heatmap')", "Heatmap"),
    ]

    POLL_PENDING_S = 0.2
    REFRESH_CHARTS_S = 5.0
    REFRESH_RECENT_S = 2.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            self._status = Static("loading…", id="status-bar")
            yield self._status

            self._timeline = TimelineChart()
            self._cumulative = CumulativeChart()
            self._recent = RecentApprovedTable()
            self._heatmap = HeatmapView()

            with TabbedContent(initial="cum", id="charts-tabs"):
                with TabPane("Cumulative", id="cum"):
                    yield self._cumulative
                with TabPane("Timeline (7d)", id="timeline"):
                    yield self._timeline
                with TabPane("Recent approvals", id="recent"):
                    yield self._recent
                with TabPane("Heatmap (365d)", id="heatmap"):
                    yield self._heatmap

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
        self._refresh_recent()
        self._refresh_status()
        self.set_interval(self.POLL_PENDING_S, self._poll_pending)
        self.set_interval(self.REFRESH_CHARTS_S, self._refresh_charts)
        self.set_interval(self.REFRESH_RECENT_S, self._refresh_recent)
        self.set_interval(1.0, self._refresh_status)

    def _refresh_charts(self) -> None:
        self._timeline.refresh_data()
        self._cumulative.refresh_data()
        self._heatmap.refresh_data()

    def _refresh_recent(self) -> None:
        self._recent.refresh_data()

    def _refresh_status(self) -> None:
        with connect() as conn:
            today = total_today(conn)
            pending = pending_count(conn)
        self._status.update(
            f"approve-watch · {today} today · {pending} pending · "
            f"q quit · l labels · 1 cum · 2 7d · 3 recent"
        )

    def _poll_pending(self) -> None:
        with connect() as conn:
            rows = list_pending(conn)
        for r in rows:
            rid = int(r["id"])
            if rid in self._known_pending:
                continue
            try:
                kind = r["kind"]
            except (IndexError, KeyError):
                kind = "shell_command"
            card = ApprovalCard(
                row_id=rid,
                command=r["command"],
                source=r["source"],
                asked_at=r["asked_at"],
                kind=kind or "shell_command",
            )
            self._queue.mount(card)
            self._known_pending.add(rid)

    def on_approval_card_resolved(self, message: ApprovalCard.Resolved) -> None:
        self._known_pending.discard(message.row_id)
        # A resolved card means the recent-approvals tab is stale.
        self._refresh_recent()

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one("#charts-tabs", TabbedContent).active = tab_id

    def action_toggle_labels(self) -> None:
        self._labels.toggle()

    def action_refresh_now(self) -> None:
        self._refresh_charts()
        self._refresh_recent()
        self._refresh_status()
