from __future__ import annotations

from datetime import datetime, timedelta

from textual_plotext import PlotextPlot

from approve_watch.db import connect, daily_counts_30d, hourly_counts_24h


def _fill_hours_24h(points: list[tuple[str, int]]) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {b: n for b, n in points}
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    labels: list[str] = []
    values: list[int] = []
    for i in range(23, -1, -1):
        t = now - timedelta(hours=i)
        bucket = t.strftime("%Y-%m-%d %H:00")
        labels.append(t.strftime("%H"))
        values.append(counts.get(bucket, 0))
    return labels, values


def _fill_days_30d(points: list[tuple[str, int]]) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {b: n for b, n in points}
    today = datetime.now().date()
    labels: list[str] = []
    values: list[int] = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        bucket = d.strftime("%Y-%m-%d")
        labels.append(d.strftime("%m-%d"))
        values.append(counts.get(bucket, 0))
    return labels, values


class HourlyChart(PlotextPlot):
    """Approvals per hour over the last 24 hours."""

    DEFAULT_CSS = "HourlyChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = hourly_counts_24h(conn)
        labels, values = _fill_hours_24h(points)
        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.bar(labels, values)
        plt.title("Approvals per hour (last 24h)")
        plt.xlabel("hour")
        self.refresh()


class DailyChart(PlotextPlot):
    """Approvals per day over the last 30 days."""

    DEFAULT_CSS = "DailyChart { height: 100%; }"

    def refresh_data(self) -> None:
        with connect() as conn:
            points = daily_counts_30d(conn)
        labels, values = _fill_days_30d(points)
        plt = self.plt
        plt.clear_figure()
        plt.theme("pro")
        plt.bar(labels, values)
        plt.title("Approvals per day (last 30d)")
        plt.xlabel("day")
        self.refresh()
