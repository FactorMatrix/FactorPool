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
        dates = horizon["portfolio_dates"]
        in_window = lambda day: (factor_start is None or day >= factor_start) and day <= factor_end
        outside = sum(not in_window(day) for day in dates)
        sections = {"all": list(range(len(dates)))} if boundary is None else {
            "train": [i for i, day in enumerate(dates) if in_window(day) and day < boundary],
            "test": [i for i, day in enumerate(dates) if in_window(day) and day >= boundary],
        }
        horizon["partitions"] = {}
        for name, indices in sections.items():
            alpha = horizon["portfolios"]["alpha"]
            # Default (no held-out period) already has a complete performance
            # report. Do not recompute/copy the entire daily result for it.
            stats = ({key: alpha.get(key) for key in
                      ("n", "missing", "annual_return", "annual_volatility", "sharpe", "cagr", "max_drawdown", "brain_style")}
                     if boundary is None else None)
            selected_dates = [dates[i] for i in indices]
            portfolios = {}
            if boundary is not None:
                for strategy, values in horizon["portfolios"].items():
                    subset = lambda key: [values[key][i] for i in indices]
                    part = native.performance_stats(subset("returns"), subset("turnover_series"),
                                                    subset("book_pnl_series"), annual_days)
                    for key in ("returns", "turnover_series", "book_pnl_series", "positions", "max_weight_series"):
                        if key in values:
                            part[key] = subset(key)
                    part["monthly"] = portfolio_calendar(selected_dates, part["returns"])
                    part["yearly"] = portfolio_calendar(selected_dates, part["returns"], 4)
                    if strategy == "alpha":
                        stats = part
                    else:
                        portfolios[strategy] = part
            horizon["partitions"][name] = dict(start=dates[indices[0]] if indices else None,
                                                end=dates[indices[-1]] if indices else None,
                                                count=len(indices), alpha=stats,
                                                outside_window_count=outside)
            if boundary is not None:
                horizon["partitions"][name].update(dates=selected_dates, portfolios=portfolios)
