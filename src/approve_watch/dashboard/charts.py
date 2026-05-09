from __future__ import annotations

from datetime import datetime, timedelta

from textual_plotext import PlotextPlot

from approve_watch.db import connect, hourly_counts_7d, minute_counts_60m

HOURS = 7 * 24      # one week of hourly buckets (left chart)
MINUTES = 60        # rolling cumulative window (right chart)


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
    handful of X-axis tick positions on the rolling cumulative chart."""
    if n <= max_ticks:
        return list(range(n))
    step = (n - 1) / (max_ticks - 1)
    return [round(i * step) for i in range(max_ticks)]


def _minute_series_60m(
    points: list[tuple[str, int]],
) -> tuple[list[int], list[int], list[datetime]]:
    """Densify ``points`` (sparse minute buckets) into a contiguous 60-min
    series ending at the current minute. Returns (x_indices, per-minute
    counts, per-minute timestamps). The returned series always has length
    ``MINUTES``, even when the DB has fewer rows — keeps the X-axis
    width fixed so the chart visibly slides as time passes."""
    counts: dict[str, int] = {b: n for b, n in points}
    now = datetime.now().replace(second=0, microsecond=0)
    x: list[int] = []
    y: list[int] = []
    stamps: list[datetime] = []
    for i in range(MINUTES - 1, -1, -1):
        t = now - timedelta(minutes=i)
        bucket = t.strftime("%Y-%m-%d %H:%M")
        idx = MINUTES - 1 - i
        x.append(idx)
        y.append(counts.get(bucket, 0))
        stamps.append(t)
    return x, y, stamps


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
    """Rolling cumulative approvals — per-minute cumsum across the last
    60 minutes. The X-axis is fixed-width (60 minute buckets) and slides
    forward as time passes, so the line always extends to the right edge
    even during quiet stretches. Tick labels are full ``HH:MM``
    timestamps at evenly-spaced minute marks."""

    DEFAULT_CSS = "CumulativeChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = minute_counts_60m(conn)
        x, per_minute, stamps = _minute_series_60m(points)

        running = 0
        cum: list[int] = []
        for n in per_minute:
            running += n
            cum.append(running)

        tick_idx = _evenly_spaced(len(stamps))
        tick_lbl = [stamps[i].strftime("%H:%M") for i in tick_idx]

        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.plot(x, cum, marker="braille")
        plt.xticks(tick_idx, tick_lbl)
        plt.title("Cumulative approvals (last 60 min)")
        plt.ylabel("total / 60m")
        self.refresh()
