from __future__ import annotations

from datetime import date, timedelta

from rich.text import Text
from textual.widgets import Static

from approve_watch.db import connect, daily_counts_since

DAYS = 365
WEEKS = (DAYS + 6) // 7  # ~53 columns
WEEKDAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# GitHub's contribution graph palette. (count_at_least, hex). Cells use
# the highest band whose threshold the count clears.
LEVELS: list[tuple[int, str]] = [
    (0, "#1b1f23"),    # empty
    (1, "#0e4429"),
    (3, "#006d32"),
    (10, "#26a641"),
    (30, "#39d353"),
]
CELL = "■"


def _level_for(n: int) -> str:
    last = LEVELS[0][1]
    for thresh, color in LEVELS:
        if n >= thresh:
            last = color
    return last


class HeatmapView(Static):
    """GitHub-style 365-day contribution graph. 7 rows × ~53 cols of
    color-mapped cells, one per day. The newest week is on the right
    edge so the most-recent activity sits where the eye lands first."""

    DEFAULT_CSS = """
    HeatmapView {
        height: 100%;
        padding: 1 2;
        background: $surface;
        color: $text;
    }
    """

    BORDER_TITLE = "Approvals — last 365 days"

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        with connect() as conn:
            counts = dict(daily_counts_since(conn, DAYS))

        today = date.today()
        # Anchor the right edge on today so the newest week is the last
        # column. The top row is Monday by convention, matching GitHub.
        # Each cell index in [0, DAYS) maps to a (weekday, week) pair.
        grid: list[list[Text]] = [[Text("  ") for _ in range(WEEKS)] for _ in range(7)]

        oldest = today - timedelta(days=DAYS - 1)
        # Start of the oldest week (Monday of that week).
        oldest_monday = oldest - timedelta(days=oldest.weekday())

        # Track month-label transitions so we can render a month strip on top.
        month_at_col: dict[int, str] = {}
        for offset in range(DAYS):
            d = oldest + timedelta(days=offset)
            week_idx = (d - oldest_monday).days // 7
            wd = d.weekday()  # 0..6, Monday..Sunday
            n = counts.get(d.isoformat(), 0)
            cell = Text(CELL + " ", style=_level_for(n))
            if 0 <= week_idx < WEEKS:
                grid[wd][week_idx] = cell
                if d.day == 1 and week_idx not in month_at_col:
                    month_at_col[week_idx] = MONTHS[d.month - 1]

        body = Text()

        # Month strip — sparse labels at week-columns where a month begins.
        month_row = Text("    ")  # left padding for weekday labels (4 chars)
        for w in range(WEEKS):
            label = month_at_col.get(w, "")
            month_row.append((label + "    ")[:4] if label else "  ")
        body.append_text(month_row)
        body.append("\n")

        # 7 cell rows, prefixed with weekday labels.
        for wd in range(7):
            label = WEEKDAY_LABELS[wd]
            body.append(f"{label:<4}", style="dim")
            for w in range(WEEKS):
                body.append_text(grid[wd][w])
            body.append("\n")

        # Legend.
        body.append("\n     less ", style="dim")
        for _, color in LEVELS:
            body.append(CELL + " ", style=color)
        body.append("more", style="dim")

        self.update(body)
