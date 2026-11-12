"""Amount labels and optional trace formatting around the existing matrix PnL."""
import math
from ..reporting_options import monetary_options


def _number(value):
    return float(value) if value is not None and math.isfinite(value) else None


def format_trace(traces, info, dates, price_dates, analysis):
    book, unit = monetary_options(analysis)
    output = []
    for trace in traces:
        signal, valuation = trace["signal_index"], trace["valuation_index"]
        detail = info.get("transforms", {}).get(signal)
        for row in trace["rows"]:
            n = row["symbol_index"]
            row["symbol"] = info["symbols"][n]
            if "factor_rows" in info:
                source = info["factor_rows"][signal*len(info["symbols"])+n]
                row.update(raw_factor_status=source["reason"], raw_factor_ready=source["ready"],
                           raw_factor_valid=source["valid"])
            if detail is not None:
                row["decayed_alpha"] = _number(detail["decayed"][n])
                row["neutralized"] = _number(detail["neutralized"][n])
            else:
                row["decayed_alpha"] = row["raw_alpha"]
            value = row["pnl_fraction"]
            row["pnl"] = None if value is None else _number(value*book)
        trace.update(signal_date=dates[signal], valuation_date=price_dates[valuation],
                     signal_time=dates[signal]+"T15:00:00+08:00", book_size=book, pnl_unit=unit,
                     daily_pnl=None if trace.get("pnl_fraction") is None else _number(trace["pnl_fraction"]*book))
        output.append(trace)
    return output


def add_amounts(diagnostics, analysis):
    book, unit = monetary_options(analysis)
    def amounts(alpha):
        if "book_pnl_series" not in alpha:
            return
        scale = lambda values: [None if value is None else _number(value*book) for value in values]
        alpha["pnl"] = dict(book_size=book, unit=unit, daily=scale(alpha["book_pnl_series"]),
                            cumulative=scale(alpha["constant_book_pnl"]))
    for horizon in diagnostics["horizons"].values():
        horizon["pnl_book"] = dict(size=book, unit=unit)
        amounts(horizon["portfolios"]["alpha"])
        for section in horizon.get("partitions", {}).values():
            amounts(section["alpha"])
    diagnostics["conventions"].update(book_size=book, pnl_unit=unit,
        book_policy="fixed nominal reference book; holding exposures drift with prices; PnL accumulates arithmetically; book_size scales amounts only, without currency conversion or a trading account",
        pnl_fields="alpha.pnl.daily/cumulative are nominal amounts; book_pnl_series/constant_book_pnl retain unit-gross-book fractions",
        max_weight_scope="max_weight_series is the rebalance target concentration, repeated within each holding interval; actual weights drift with prices")
