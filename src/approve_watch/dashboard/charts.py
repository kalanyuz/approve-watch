from __future__ import annotations

from datetime import datetime, timedelta, timezone

from textual_plotext import PlotextPlot

from approve_watch.db import connect, hourly_counts_7d, total_before

HOURS = 7 * 24  # one week of hourly buckets


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
    """Cumulative approvals — monotonic line that only goes up. Seeded
    with the all-time count from before the 7-day window so the line is
    continuous with history rather than restarting at zero each week."""

    DEFAULT_CSS = "CumulativeChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = hourly_counts_7d(conn)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat(timespec="microseconds")
            baseline = total_before(conn, cutoff)
        x, y, day_ticks = _hourly_series_7d(points)

        running = baseline
        cum: list[int] = []
        for v in y:
            running += v
            cum.append(running)

        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.plot(x, cum, marker="braille")
        if day_ticks:
            plt.xticks([p for p, _ in day_ticks], [lbl for _, lbl in day_ticks])
        plt.title("Cumulative approvals")
        plt.ylabel("total")
        self.refresh()
