from __future__ import annotations

from datetime import datetime, timedelta

from textual_plotext import PlotextPlot

from approve_watch.db import connect, daily_counts_all, hourly_counts_7d

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


def _evenly_spaced(n: int, max_ticks: int = 6) -> list[int]:
    """Indices in [0, n-1] approximately evenly spaced. Used to pick a
    handful of X-axis tick positions when the cumulative chart spans
    many days."""
    if n <= max_ticks:
        return list(range(n))
    step = (n - 1) / (max_ticks - 1)
    return [round(i * step) for i in range(max_ticks)]


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
    """Cumulative approvals — monotonic line over the entire history.
    One point per day; cumsum starts at zero on the first recorded day."""

    DEFAULT_CSS = "CumulativeChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = daily_counts_all(conn)

        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.title("Cumulative approvals (all time)")
        plt.ylabel("total")

        if points:
            running = 0
            cum: list[int] = []
            for _, n in points:
                running += n
                cum.append(running)
            x = list(range(len(points)))
            tick_idx = _evenly_spaced(len(points))
            tick_lbl = [points[i][0][5:] for i in tick_idx]  # "MM-DD"
            plt.plot(x, cum, marker="braille")
            plt.xticks(tick_idx, tick_lbl)

        self.refresh()
