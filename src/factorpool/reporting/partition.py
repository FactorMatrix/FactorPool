"""Calendar validation windows; this partitions evaluation, never refits factors."""
import _factorpool_native as native
from .diagnostics import portfolio_calendar
from ..config import test_period_start as test_start


def add_test_period(diagnostics, period, factor_end, annual_days, factor_start=None):
    boundary = test_start(factor_end, period)
    diagnostics["test_period"] = dict(duration=period, start=boundary,
                                      evaluation_start=factor_start, evaluation_end=factor_end,
                                      policy="partition observed portfolio dates within the requested research window; full results retain future holding returns; no refit or selection")
    for horizon in diagnostics["horizons"].values():
        horizon["partitions"] = {}
        if boundary is None:
            continue
        dates = horizon["portfolio_dates"]
        inside = [(factor_start is None or day >= factor_start) and day <= factor_end
                  for day in dates]
        outside = len(dates) - sum(inside)
        alpha = horizon["portfolios"]["alpha"]
        for name, selected in (("train", lambda day: day < boundary),
                               ("test", lambda day: day >= boundary)):
            indices = [i for i, day in enumerate(dates) if inside[i] and selected(day)]
            selected_dates = [dates[i] for i in indices]
            subset = lambda key: [alpha[key][i] for i in indices]
            book, turnover = subset("book_pnl_series"), subset("turnover_series")
            if name == "train":
                part = native.performance_stats(subset("returns"), turnover, book, annual_days)
                for key in ("returns", "turnover_series", "book_pnl_series",
                            "long_positions", "short_positions"):
                    part[key] = subset(key)
                part["monthly"] = portfolio_calendar(selected_dates, part["returns"])
                part["yearly"] = portfolio_calendar(selected_dates, part["returns"], 4)
            else:
                part = {"brain_style": native.brain_summary(book, turnover, annual_days)}
            horizon["partitions"][name] = dict(
                start=selected_dates[0] if selected_dates else None,
                end=selected_dates[-1] if selected_dates else None,
                count=len(indices), alpha=part, dates=selected_dates,
                outside_window_count=outside)
