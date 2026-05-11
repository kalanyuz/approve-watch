from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from textual_plotext import PlotextPlot

from approve_watch.db import (
    connect,
    daily_counts_since,
    hourly_counts_7d,
    total_before,
)

HOURS = 7 * 24      # one week of hourly buckets (left chart)
DAYS = 7            # daily cumulative chart window (right chart)


def _hourly_series_7d(
    points: list[tuple[str, int]],
) -> tuple[list[int], list[int], list[tuple[int, str]]]:
    """Densify ``points`` (sparse hourly buckets) into a contiguous 7-day
    series. Returns (x_indices, hourly_counts, day_ticks). ``day_ticks``
    maps the index of each midnight to its weekday label so the X-axis
    only shows day boundaries — keeps the chart readable while the line
    itself has hourly resolution."""
    counts: dict[str, int] = {b: n for b, n in points}
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    x: list[int] = []
    y: list[int] = []
    day_ticks: list[tuple[int, str]] = []
    for i in range(HOURS - 1, -1, -1):
        t = now - timedelta(hours=i)
        bucket = t.strftime("%Y-%m-%d %H:00")
        idx = HOURS - 1 - i
        x.append(idx)
        y.append(counts.get(bucket, 0))
        if t.hour == 0:
            day_ticks.append((idx, t.strftime("%a")))
    return x, y, day_ticks


def _cumulative_series_7d(
    counts_by_day: dict[str, int],
    baseline: int,
    today: date | None = None,
) -> tuple[list[int], list[int], list[str]]:
    """Build the cumulative line for the last 7 days. ``baseline`` is the
    all-time total before the window starts; the series cumsums per-day
    counts onto that baseline so the Y-axis tracks the all-time running
    total (it never resets to zero). ``counts_by_day`` keys are
    ``YYYY-MM-DD`` strings (UTC, matching daily_counts_since).
    ``today`` defaults to the current UTC date."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=DAYS - 1)

    x: list[int] = []
    cum: list[int] = []
    labels: list[str] = []
    running = baseline
    for i in range(DAYS):
        d = window_start + timedelta(days=i)
        running += counts_by_day.get(d.isoformat(), 0)
        x.append(i)
        cum.append(running)
        labels.append(d.strftime("%a %d"))  # "Mon 09"
    return x, cum, labels


class TimelineChart(PlotextPlot):
    """Approvals over time — hourly resolution across the last 7 days,
    with day-boundary tick labels so the X-axis stays legible."""

    DEFAULT_CSS = "TimelineChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = hourly_counts_7d(conn)
        x, y, day_ticks = _hourly_series_7d(points)

        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.plot(x, y, marker="braille")
        if day_ticks:
            plt.xticks([p for p, _ in day_ticks], [lbl for _, lbl in day_ticks])
        plt.title("Approvals (last 7d, hourly)")
        plt.ylabel("count / hr")
        self.refresh()


class CumulativeChart(PlotextPlot):
    """All-time cumulative approvals, viewed through a 7-day daily
    window. Y is the running total of every approval ever recorded — the
    line never drops to zero — and X shows the last 7 days at daily
    granularity. Today's point grows visibly as new approvals come in
    (the dashboard pokes refresh_data on every resolution)."""

    DEFAULT_CSS = "CumulativeChart { height: 100%; }"

    def refresh_data(self) -> None:
        today = datetime.now(timezone.utc).date()
        window_start = today - timedelta(days=DAYS - 1)
        cutoff_iso = (
            datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc)
            .isoformat(timespec="microseconds")
        )

        with connect() as conn:
            counts_by_day = dict(daily_counts_since(conn, DAYS))
            baseline = total_before(conn, cutoff_iso)

        x, cum, labels = _cumulative_series_7d(counts_by_day, baseline, today=today)

        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.plot(x, cum, marker="braille")
        plt.xticks(x, labels)
        plt.title("Cumulative approvals (all time, last 7d)")
        plt.ylabel("total")
        self.refresh()
