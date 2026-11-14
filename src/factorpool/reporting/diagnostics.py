"""PDF-style factor diagnostics and an explicit BRAIN-style research preset."""
from collections import defaultdict
import math
import _factorpool_native as native
from scipy.special import stdtr, ndtr


def options(analysis):
    periods = analysis.get("horizons", [1, 5, 20])
    if (not isinstance(periods, list) or not periods or len(periods) > 6
            or any(type(h) is not int or not 1 <= h <= 252 for h in periods)
            or len(set(periods)) != len(periods)):
        raise ValueError("horizons must contain 1..6 unique integer periods in 1..252")
    annual_days = analysis.get("annual_days", 252)
    steps = analysis.get("decay_steps", 10)
    if type(annual_days) is not int or not 1 <= annual_days <= 366:
        raise ValueError("annual_days must be an integer in 1..366")
    if type(steps) is not int or not 1 <= steps <= 10:
        raise ValueError("decay_steps must be an integer in 1..10")
    return sorted(periods), annual_days, steps


def inference(values, ddof, lag):
    result = native.series_stats(values, ddof, lag)
    for method in ("iid", "hac"):
        value = result[f"t_{method}"]
        p = None
        if value is not None:
            p = float(2 * (stdtr(result["n"]-1, -abs(value)) if method == "iid" else ndtr(-abs(value))))
            if p == 0:
                p = None
                result[f"p_{method}_status"] = "underflow"
        result[f"p_{method}"] = p
    result["inference_note"] = "IID ignores serial dependence; HAC uses Bartlett weights, complete equally spaced observations only"
    return result


def grouped_moments(dates, values, width, ddof):
    grouped = defaultdict(list)
    for date, value in zip(dates, values):
        if date is not None:
            grouped[date[:width]].append(value)
    return [{"period": key, **native.series_stats(grouped[key], ddof, 0)} for key in sorted(grouped)]


def rolling_mean(values, window=30):
    # Selection only. The shared report native kernel computes the mean.
    output = []
    for i in range(len(values)):
        subset = values[max(0, i-window+1):i+1]
        output.append(native.series_stats(subset, 1, 0)["mean"] if len(subset) == window and all(x is not None for x in subset) else None)
    return output


def cumulative_sum(values):
    # A prefix with missing observations has no complete cumulative IC.
    return native.prefix_ic_sum(values)


def portfolio_calendar(dates, returns, width=7):
    grouped = defaultdict(list)
    for date, value in zip(dates, returns):
        if date is not None:
            grouped[date[:width]].append(value)
    output = []
    for key in sorted(grouped):
        values = grouped[key]
        summary = native.series_stats(values, 1, 0)
        complete = summary["n"] == len(values) and summary["n"] > 0
        ret = native.compound_return(values) if complete else None
        output.append(dict(period=key, value=ret, count=summary["n"], missing=summary["missing"]))
    return output


def build(factors, returns, membership, dates, price_dates, analysis, *, primary=None, trace_info=None,
          rebalance_only=False, horizon_targets=None, target_offset=0):
    periods, annual_days, steps = options(analysis)
    ddof, groups, minimum = analysis.get("icir_ddof", 1), analysis.get("groups", 3), analysis.get("min_ic_pairs", 3)
    prepared = factors if returns is None else native.prepare_research(factors, returns, membership)
    out = {}
    for h in periods:
        stride = h if rebalance_only else 1
        signal_dates = dates[::stride]
        if h == 1 and primary is not None:
            ics = [day["ic"] for day in primary["daily"]]
            rics = [day["rank_ic"] for day in primary["daily"]]
        else:
            series = native.ic_series(prepared, h, minimum, stride)
            ics, rics = series["ic"], series["rank_ic"]
        lag = 0 if rebalance_only else h-1
        ic = inference(ics, ddof, lag)
        rank_ic = inference(rics, ddof, lag)
        trace_indices = trace_info.get("indices", []) if trace_info else []
        args = (prepared, None, None, groups, h, annual_days)
        current_trace = trace_info
        constraints = []
        if horizon_targets is not None and h != 1:
            weights, transforms, constraints = horizon_targets(h)
            portfolios = native.portfolio_report(*args, trace_indices, weights)
            if trace_info:
                current_trace = dict(trace_info, transforms=transforms)
        else:
            portfolios = native.portfolio_report(*args, trace_indices) if trace_indices else native.portfolio_report(*args)
        traces = portfolios.pop("trace", [])
        indices = [int(v) for v in portfolios.pop("signal_indices")]
        daily_indices = [int(v) for v in portfolios.pop("daily_indices")]
        signals = [dates[i] for i in indices]
        exits = [price_dates[i+h] if i+h < len(price_dates) else None for i in indices]
        portfolio_dates = [price_dates[i] for i in daily_indices]
        for strategy in portfolios["strategies"].values():
            strategy["monthly"] = portfolio_calendar(portfolio_dates, strategy["returns"])
            strategy["yearly"] = portfolio_calendar(portfolio_dates, strategy["returns"], 4)
        out[str(h)] = dict(horizon=h, dates=signal_dates, ic=ic, rank_ic=rank_ic,
            series=dict(ic=ics, rank_ic=rics, ic_rolling30=rolling_mean(ics), rank_ic_rolling30=rolling_mean(rics),
                        ic_cumulative=cumulative_sum(ics), rank_ic_cumulative=cumulative_sum(rics)),
            monthly=dict(ic=grouped_moments(signal_dates, ics, 7, ddof),rank_ic=grouped_moments(signal_dates, rics, 7, ddof)),
            yearly=dict(ic=grouped_moments(signal_dates, ics, 4, ddof),rank_ic=grouped_moments(signal_dates, rics, 4, ddof)),
            decay=native.ic_decay(prepared, None, None, h, steps, minimum, stride),
            portfolio_dates=portfolio_dates, portfolio_signal_dates=signals, portfolio_exit_dates=exits, portfolios=portfolios["strategies"])
        if trace_info and trace_indices:
            from .pnl import format_trace
            out[str(h)]["alpha_trace"] = format_trace(traces, current_trace, dates, price_dates, analysis)
            out[str(h)]["trace_status"] = dict(
                requested_signal_dates=[dates[i] for i in trace_indices],
                not_rebalance_dates=[dates[i] for i in trace_indices if i % h],
                no_future_dates=[dates[i] for i in trace_indices if i % h == 0 and i+1 >= len(price_dates)],
                policy="only requested rebalance signals; full holding intervals; no default per-symbol export")
        if constraints:
            out[str(h)]["alpha_constraints"] = [
                dict(item, index=item["index"]-target_offset, date=dates[item["index"]-target_offset])
                for item in constraints
                if 0 <= item["index"]-target_offset < len(dates)
                and (item["index"]-target_offset) % h == 0
            ]
    return dict(version=3, horizons=out, conventions=dict(
        annual_days=annual_days, ic_ddof=ddof, return_price="close / prev_close - 1", signal_delay=1,
        return_matrix="signal t uses daily returns R[t+1]..R[t+h]; no additional corporate-action adjustment",
        portfolio_rebalance="nonoverlapping; first factor date anchors each horizon; positions drift naturally within each holding segment",
        portfolio_valuation="daily mark-to-market; compound each security then sum initial weighted gains; daily return is consecutive portfolio NAV ratio",
        long_short="gross exposure 1; long 0.5 and short 0.5", turnover="L1 target minus price-drifted prior weights; initial entry included; daily axis, zero on days without rebalance",
        brain_returns="annualized mean daily fixed-gross-book PnL change / 0.5; not CAGR",
        brain_drawdown="peak-to-trough cumulative arithmetic PnL / 0.5",
        brain_fitness="Sharpe * sqrt(abs(returns) / max(daily_turnover,0.125))",
        brain_margin="total normalized PnL / total normalized value traded; display in bp",
        brain_status="research preset; official BRAIN numerical parity unverified",
        alpha_weights="visible finite factor, cross-sectional arithmetic demean, L1 normalize; no industry, decay or truncation",
        missing_returns="missing nonzero position daily returns remain invalid; no zero filling or hindsight reweighting",
        monthly_returns="compound daily mark-to-market returns assigned to their actual trading dates",
        costs="no commissions, slippage or taxes",
        decay="fixed signal, hold horizon h; entry lag 0,h,...,(steps-1)*h",
        missing_datasets={"market_cap":"no qualified provider", "industry":"no qualified provider", "self_correlation":"no selected alpha pool", "production_correlation":"no BRAIN production pool access"}),
        unavailable=dict(market_cap=None,industry=None,self_correlation=None,production_correlation=None))
