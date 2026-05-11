from __future__ import annotations

from datetime import date, timedelta

from approve_watch.dashboard.charts import DAYS, _cumulative_series_7d


def test_cumulative_series_uses_baseline_and_per_day_counts() -> None:
    """The cumulative chart's Y-axis must be the all-time running total —
    the line starts at the baseline (count of rows before the window),
    then cumsums per-day counts onto it. Series length is always DAYS."""
    today = date(2026, 5, 9)
    window_start = today - timedelta(days=DAYS - 1)  # 2026-05-03
    counts = {
        window_start.isoformat(): 2,           # 2026-05-03
        (window_start + timedelta(days=2)).isoformat(): 5,   # 2026-05-05
        (window_start + timedelta(days=4)).isoformat(): 1,   # 2026-05-07
        today.isoformat(): 3,                  # 2026-05-09
    }

    x, cum, labels = _cumulative_series_7d(counts, baseline=100, today=today)

    assert len(x) == DAYS
    assert x == list(range(DAYS))
    # baseline=100, then +2, +0, +5, +0, +1, +0, +3
    assert cum == [102, 102, 107, 107, 108, 108, 111]
    assert len(labels) == DAYS
    # Right-most label is today; left-most is 6 days ago.
    assert labels[-1].endswith("09")
    assert labels[0].endswith("03")


def test_cumulative_series_with_empty_counts_stays_at_baseline() -> None:
    """Quiet 7 days → flat line at the baseline."""
    today = date(2026, 5, 9)
    x, cum, labels = _cumulative_series_7d({}, baseline=42, today=today)
    assert cum == [42] * DAYS
    assert len(labels) == DAYS


def test_cumulative_series_with_zero_baseline_starts_at_zero() -> None:
    """A brand-new install with no history shows a line starting at 0."""
    today = date(2026, 5, 9)
    counts = {today.isoformat(): 4}
    x, cum, _ = _cumulative_series_7d(counts, baseline=0, today=today)
    assert cum == [0, 0, 0, 0, 0, 0, 4]
