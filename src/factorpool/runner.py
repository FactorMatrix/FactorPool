"""One task per process; native Indicator and native research statistics."""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import numpy as np
from .adapter import IndicatorAdapter
from .datasource import fixture
from .reporting.diagnostics import build as build_diagnostics, options as report_options


def log(stage, **values):
    print(json.dumps(dict(stage=stage, **values), ensure_ascii=False, allow_nan=False), flush=True)


def load_factor(path, class_name, params):
    from .operator_frontend import validate_source_frontend
    from .entry import PythonIndicator
    validate_source_frontend(Path(path).read_text())
    spec = importlib.util.spec_from_file_location("factorpool_user_factor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, PythonIndicator):
        raise TypeError("Use from factorpool import PythonIndicator; the shared Trader research base is not a Pool frontend")
    return lambda: cls(**params)


def validate(request):
    data, analysis = request["data"], request["analysis"]
    if data.get("provider") not in ("fixture", "market_matrix"):
        raise ValueError("Supported providers: market_matrix or fixture")
    if data.get("frequency", "1d") != "1d" or data.get("adjustment", "raw") != "raw":
        raise ValueError("Daily basic source quotes are required")
    if data.get("factor_price", "quote_adjusted") != "quote_adjusted":
        raise ValueError("Factor prices only support forward adjustment (quote_adjusted)")
    if analysis.get("entry", "signal_close") != "signal_close" or analysis.get("exit", "next_close") != "next_close":
        raise ValueError("Supported return interval: signal_close to next_close, using close / prev_close - 1")
    if analysis.get("annualize_icir", False) or analysis.get("group_weight", "equal") != "equal":
        raise ValueError("Supported analysis: equal weights, unannualized ICIR")
    if data["factor_start"] > data["factor_end"]:
        raise ValueError("End precedes start")
    from .reporting_options import monetary_options, trace_dates
    monetary_options(analysis)
    trace_dates(analysis, data["factor_start"], data["factor_end"])


def returns_matrix(frames, symbols):
    """Build daily returns from each bar's own close and previous close."""
    import _factorpool_native as native
    closes = np.asarray([[frame["rows"].get(symbol, {}).get("close") for symbol in symbols]
                         for frame in frames], dtype=np.float64)
    previous = np.asarray([[frame["rows"].get(symbol, {}).get("prev_close") for symbol in symbols]
                           for frame in frames], dtype=np.float64)
    return native.price_returns(closes.reshape(len(frames), len(symbols)),
                                previous.reshape(len(frames), len(symbols)))


def return_contract(payload):
    """Describe the stored derivation without changing any return values."""
    if "return_policy" not in payload:
        if not payload["synthetic"]:
            raise ValueError("Market input requires the current return_policy")
        policy = dict(version="synthetic-price-ratio-v1", formula="close/prev_close-1")
    else:
        policy = payload["return_policy"]
    if not isinstance(policy, dict):
        raise ValueError("Input return_policy must be an object")
    policy = deepcopy(policy)
    version = policy.get("version")
    if version == "volume-zero-v1":
        description = ("Derived at import: finite volume=0 and finite total_turnover=0 with finite member>0 give R=0, even if a quote is missing; "
                       "finite volume>0 with finite close>=0 and finite prev_close>0 uses close / prev_close - 1; "
                       "missing, nonfinite or negative volume, invalid required prices, and zero volume with nonzero or unknown turnover remain null. "
                       "Raw OHLCV/status are unchanged; no additional corporate-action adjustment")
        price = "volume-aware derived return; positive-volume close / prev_close - 1"
    elif version == "synthetic-price-ratio-v1" and payload["synthetic"]:
        description = "close / prev_close - 1; no volume-based zeroing or additional corporate-action adjustment"
        price = "close / prev_close - 1"
    else:
        raise ValueError("Unsupported return policy; use volume-zero-v1 market data or the synthetic fixture")
    return policy, description, price


def factor_publications(evaluator, symbols, membership, first_signal, rebalance, use_daily_history):
    """Evaluate factor values and status on the research axes."""
    count, width = membership.shape
    history_start = 0 if use_daily_history else first_signal
    dense_signals = len(rebalance) == count - first_signal
    indices = (list(range(history_start, count)) if use_daily_history or dense_signals
               else [first_signal + index for index in sorted(rebalance)])
    batch = evaluator.evaluate_many(indices)
    if (batch is not None and dense_signals and batch['symbols'] == tuple(symbols)
            and not np.any((batch['reason'] != 6) & ~membership[history_start:])):
        # Native publications already mark securities outside the calculation
        # axis as missing with false readiness and reason 6. When the research
        # axis needs no further masking, borrow those immutable buffers.
        start = first_signal - history_start
        log("calculate", progress=1)
        return (batch['value'], batch['value'][start:], batch['valid'][start:],
                batch['ready'][start:], batch['reason'][start:])

    all_factors = np.full((count, width), np.nan, dtype=np.float64)
    valid = np.zeros((count - first_signal, width), dtype=bool)
    ready = np.zeros_like(valid)
    reasons = np.full(valid.shape, 6, dtype=np.uint8)  # outside_universe
    calculation_to_research = np.full(len(evaluator.symbols), -1, dtype=np.intp)
    for n, symbol in enumerate(symbols):
        index = evaluator.columns.get(symbol)
        if index is not None:
            calculation_to_research[index] = n
    if batch is not None:
        indices = batch['indices']
        columns = np.flatnonzero(calculation_to_research >= 0)
        targets = calculation_to_research[columns]
        full_axis = (len(columns) == len(evaluator.symbols)
                     and np.array_equal(targets, np.arange(width)))
        member = membership[indices[:, None], targets]
        values = batch['value'] if full_axis else batch['value'][:, columns]
        all_factors[indices[:, None], targets] = np.where(member, values, np.nan)
        published = np.array([index - first_signal in rebalance for index in indices], dtype=bool)
        output_rows = (indices[published] - first_signal)[:, None]
        observed = member[published]
        for name, output in [('valid', valid), ('ready', ready), ('reason', reasons)]:
            values = batch[name] if full_axis else batch[name][:, columns]
            values = values[published]
            output[output_rows, targets] = (np.where(observed, values, 6) if name == 'reason'
                                            else values & observed)
        log("calculate", progress=1)
    else:
        for index in indices:
            offset = index - first_signal
            state = evaluator.evaluate_arrays(index)
            targets = calculation_to_research[state['columns']]
            keep = targets >= 0
            keep[keep] &= membership[index, targets[keep]]
            selected = targets[keep]
            all_factors[index, selected] = state['value'][keep]
            if offset in rebalance:
                valid[offset, selected] = state['valid'][keep]
                ready[offset, selected] = state['ready'][keep]
                reasons[offset, selected] = state['reason'][keep]
            if index % 100 == 0:
                log("calculate", progress=round((index + 1) / len(evaluator.frames), 4))
    return all_factors, all_factors[first_signal:], valid, ready, reasons


def run(directory, *, factory=None):
    import _factorpool_native as native
    directory = Path(directory)
    values_ready = directory / "values-ready.json"
    values_ready.unlink(missing_ok=True)
    request = json.loads((directory / "request.json").read_text())
    from .script import bind_request
    request = bind_request(request)
    validate(request)
    horizons, annual_days, decay_steps = report_options(request["analysis"])
    started = time.perf_counter()
    timings = {}
    (directory / "factor.py").write_text(request["source_code"])
    if factory is None:
        factory = load_factor(directory / "factor.py", request["factor_class"], request.get("factor_params", {}))
    # Pool owns the Indicator contract, reusing native numerical primitives;
    # research never creates an Engine/Runtime or a trading account.
    indicator = factory()
    data = request["data"]
    adapter = IndicatorAdapter(indicator)
    warmup = indicator.warm_up_period
    settings = request.get("brain_settings")
    simulation_warmup = max(0, settings["decay"] - 1) if settings is not None else 0
    timings["prepare"] = time.perf_counter() - started
    log("prepared", mode=adapter.mode, warmup=warmup)
    checkpoint = time.perf_counter()
    provider = data["provider"]
    if provider == "fixture":
        payload = fixture()
        if data.get("universe") not in (None, "fixture"):
            raise ValueError("Fixture universe is A/B/C")
    elif provider == "market_matrix":
        from .market_loader import load
        extra = {"pasteurization": "OFF"} if settings is not None and settings["pasteurization"] == "OFF" else {}
        from .adapter import indicator_fields
        payload = load(data, max(1, warmup + simulation_warmup), max(horizons)*decay_steps,
                       fields=indicator_fields(indicator), **extra)
    else:
        raise ValueError(f"Unknown/unconfigured provider: {provider}")
    timings["read"] = time.perf_counter() - checkpoint
    return_policy, daily_return, return_price = return_contract(payload)
    log("loaded", provider=provider, frames=len(payload["frames"]), snapshot_id=payload["snapshot_id"])
    checkpoint = time.perf_counter()
    frames = payload["frames"]
    if any(a["date"] >= b["date"] for a, b in zip(frames, frames[1:])):
        raise ValueError("Input dates must be unique and strictly increasing")
    source_symbols = payload["symbols"]
    if len(source_symbols) != len(set(source_symbols)):
        raise ValueError("Input symbols must be unique")
    symbols = sorted(source_symbols)
    from .factor_matrix import FactorMatrix, signal_indices
    evaluator = FactorMatrix(payload, indicator, factory, data['factor_end'])
    first_signal = next((i for i, frame in enumerate(evaluator.frames)
                         if frame['date'] >= data['factor_start']), None)
    if first_signal is None:
        raise ValueError("No factor observations in requested date range")
    historical = evaluator.frames
    research_frames = historical[first_signal:]
    dates = [frame["date"] for frame in research_frames]
    if not dates:
        raise ValueError("No factor observations in requested date range")
    rebalance = signal_indices(len(dates), horizons)
    signal_mask = np.zeros(len(dates), dtype=bool)
    signal_mask[list(rebalance)] = True
    count, width = len(historical), len(symbols)
    stored_members = payload.get("membership")
    if stored_members is not None:
        stored_members = np.asarray(stored_members, dtype=bool)
        if stored_members.shape != (len(frames), len(source_symbols)):
            raise ValueError("Stored membership does not match date/symbol axes")
        if source_symbols == symbols:
            all_members = stored_members[:count]
        else:
            source_lookup = {symbol: n for n, symbol in enumerate(source_symbols)}
            all_members = np.ascontiguousarray(stored_members[:count, [source_lookup[s] for s in symbols]])
    else:
        all_members = np.zeros((count, width), dtype=bool)
        research_lookup = {symbol: n for n, symbol in enumerate(symbols)}
        for t, frame in enumerate(historical):
            for symbol in frame.get("research_symbols", frame["symbols"]):
                n = research_lookup.get(symbol)
                if n is not None:
                    all_members[t, n] = True
    all_factors, factors, valid, ready, reasons = factor_publications(
        evaluator, symbols, all_members, first_signal, rebalance, simulation_warmup > 0)
    members = np.ascontiguousarray(all_members[first_signal:])
    # Daily observations needed only by explicit Alpha decay remain in all_factors;
    # the primary IC/group matrix still contains only the union of rebalance dates.
    if len(rebalance) < len(dates):
        members = members & signal_mask[:, None]
        if simulation_warmup > 0:
            factors = factors.copy()
            factors[~signal_mask] = np.nan
    # Include every calendar day from first factor date and its future tail.
    tail = [f for f in frames if f["date"] >= dates[0]]
    if [f["date"] for f in tail[:len(dates)]] != dates:
        raise ValueError("Factor and price date axes differ")
    if "returns" in payload:
        stored = np.asarray(payload["returns"], dtype=np.float64)
        if stored.shape != (len(frames), len(source_symbols)):
            raise ValueError("Stored return matrix does not match its date and symbol axes")
        first = next(i for i, frame in enumerate(frames) if frame["date"] == dates[0])
        if source_symbols == symbols:
            returns = np.ascontiguousarray(stored[first:], dtype=np.float64)
        else:
            source_columns = {symbol: index for index, symbol in enumerate(source_symbols)}
            columns = [source_columns[symbol] for symbol in symbols]
            returns = np.ascontiguousarray(stored[first:, columns], dtype=np.float64)
    else:
        returns = returns_matrix(tail, symbols)
    terminal_rows = payload.get("terminal_exit_rows")
    if terminal_rows is not None:
        source_columns = {symbol: index for index, symbol in enumerate(source_symbols)}
        terminal_rows = [terminal_rows[source_columns[symbol]] for symbol in symbols]
        full_terminal_rows = np.asarray(terminal_rows, dtype=np.int64)
        terminal_rows = [max(0, row-first_signal) if row >= 0 else -1 for row in terminal_rows]
    else:
        full_terminal_rows = None
    timings["calculate"] = time.perf_counter() - checkpoint
    checkpoint = time.perf_counter()
    options = request["analysis"]
    from .reporting_options import trace_dates
    requested_trace = trace_dates(options, data["factor_start"], data["factor_end"])
    if any(day not in dates for day in requested_trace):
        raise ValueError("analysis.trace_dates must be actual signal dates in the loaded data")
    trace_indices = [dates.index(day) for day in requested_trace]
    detail_rows = sum(len(symbols)*min(h, max(0, len(tail)-t-1))
                      for h in horizons for t in trace_indices if t % h == 0)
    if detail_rows > 50000:
        raise ValueError("Requested Alpha trace exceeds 50,000 rows; reduce signal dates, horizons or universe")
    transforms = {}
    alpha_weights = None
    target_constraints = []
    horizon_targets = None
    if settings is not None and (settings["decay"] > 1 or settings["neutralization"] != "MARKET"
                                 or settings["truncation"] != 0):
        from .simulation import alpha_targets, _decayed
        has_terminal_rows = full_terminal_rows is not None and np.any(full_terminal_rows >= 0)
        # Decay sees daily observations (including warmup) once. Target
        # normalization only needs the actual rebalance signals.
        transformed = (_decayed(all_factors, all_members, settings["decay"])[first_signal:]
                       if simulation_warmup else factors)
        target_members = all_members[first_signal:]

        def targets_for_signals(h, rows):
            dense = len(rows) == len(dates)
            observations = transformed if dense else transformed[rows]
            membership = target_members if dense else target_members[rows]
            selection = None
            if has_terminal_rows:
                selection = ((full_terminal_rows < 0)[None, :]
                             | (rows[:, None] + first_signal + h < full_terminal_rows[None, :]))
            positions = {int(row): pos for pos, row in enumerate(rows)} if trace_indices else {}
            requested = [positions[i] for i in trace_indices if i in positions and i+1 < len(tail)]
            details = {} if requested or settings["truncation"] > 0 else None
            targets = alpha_targets(observations, membership, settings, details=details,
                                    trace_indices=requested, selection_membership=selection,
                                    transformed=observations)
            if not dense:
                full_targets = np.full(factors.shape, np.nan, dtype=np.float64)
                full_targets[rows] = targets
                targets = full_targets
            details = details or {}
            trace_transforms = {int(rows[i]): values for i, values in details.get("trace", {}).items()}
            constraints = [dict(item, index=int(rows[item["index"]]))
                           for item in details.get("constraints", [])]
            return targets, trace_transforms, constraints

        alpha_weights, transforms, target_constraints = targets_for_signals(1, np.flatnonzero(signal_mask))
        if has_terminal_rows:
            def horizon_targets(h):
                return targets_for_signals(h, np.arange(0, len(dates), h))
    prepared = native.prepare_research(factors, returns, members, alpha_weights, terminal_rows)
    result = native.analyze(prepared, options.get("groups", 3), options.get("min_ic_pairs", 3),
                            options.get("icir_ddof", 1), 1, 1)
    # All value columns are complete after primary analysis. Publish only the
    # closed immutable artifact; the worker still waits for the full report
    # before making this task successful.
    export_started = time.perf_counter()
    forward_returns, groups = result.pop("forward_returns"), result.pop("groups")
    from .result_matrix import write_result_matrix
    value_artifact = write_result_matrix(directory / "values.matrix", dates, symbols, factors,
                                        valid, ready, reasons, forward_returns, groups,
                                        signal_indices=rebalance, threads=native.compute_threads())
    del forward_returns, groups
    temporary = values_ready.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(version=1, artifact="values.matrix",
                                         value_count=value_artifact["count"],
                                         sha256=value_artifact["sha256"]), allow_nan=False))
    temporary.replace(values_ready)
    timings["export"] = time.perf_counter() - export_started
    trace_info = dict(indices=trace_indices, symbols=symbols, transforms=transforms,
                      factor_arrays=dict(reason=reasons, valid=valid, ready=ready)) if trace_indices else None
    result["diagnostics"] = build_diagnostics(prepared, dates, [f["date"] for f in tail], options,
                                             primary=result, trace_info=trace_info, rebalance_only=True,
                                             horizon_targets=horizon_targets)
    conventions = result["diagnostics"]["conventions"]
    conventions.update(factor_sampling="per-horizon rebalance dates; primary 1D IC uses their union",
                       price_adjustment="forward only: historical price times Q ratio; volume divided by the same ratio",
                       return_policy=deepcopy(return_policy), daily_return=daily_return,
                       return_price=return_price,
                       return_matrix="signal t uses daily returns R[t+1]..R[t+h]; derivation follows return_policy",
                       missing_returns="missing nonzero position daily returns remain invalid; no reader-side zero filling or hindsight reweighting; source derivation follows return_policy")
    if terminal_rows is not None:
        conventions["delisting_filter"] = (
            "Before each h-session holding period, exclude stocks whose known exclusive lifetime end "
            "is at or before signal+h; close prior holdings at the scheduled rebalance. "
            "Raw factors and stored daily returns are unchanged.")
    if settings is not None:
        from .simulation import ALPHA_POLICY, SETTINGS_SCOPE
        conventions.update(brain_settings=deepcopy(settings), alpha_weights=ALPHA_POLICY,
                           settings_scope=SETTINGS_SCOPE,
                           long_short="Group spread targets use long 0.5/short 0.5; Alpha gross/net depend on effective settings and need not remain 1/0 after truncation")
        from .reporting.partition import add_test_period
        add_test_period(result["diagnostics"], settings["testPeriod"], data["factor_end"], annual_days, data["factor_start"])
        if settings["truncation"] > 0:
            constraints = [dict(item, date=dates[item["index"]]) for item in target_constraints]
            result["diagnostics"]["alpha_constraints"] = dict(
                truncation=settings["truncation"], target_gross_reference=1,
                policy="rebalance target cap without redistribution; infeasible full gross is reported, residual remains unallocated; no forced full investment",
                feasibility_scope="total capacity N*cap only; not a side/net/redistribution solver; actual target gross and net are reported separately",
                daily=constraints,
                infeasible_dates=[item["date"] for item in constraints if not item["full_gross_feasible"]])
    from .reporting.pnl import add_amounts
    add_amounts(result["diagnostics"], options)
    timings["analysis"] = time.perf_counter() - checkpoint - timings["export"]
    result.pop('daily')
    timings["analysis_export"] = time.perf_counter() - checkpoint
    build_info = json.loads(Path(os.environ.get("FACTORPOOL_BUILD_INFO", "/opt/factorpool/build-info.json")).read_text())
    result["metadata"] = dict(build=build_info, execution_mode=adapter.mode, warm_up_period=warmup,
        source_sha256=hashlib.sha256(request["source_code"].encode()).hexdigest(), snapshot_id=payload["snapshot_id"],
        provider=provider, synthetic=payload["synthetic"], frequency="1d", adjustment="raw", timezone="Asia/Shanghai",
        return_interval="signal close to next close; daily R[t+1] follows return_policy", return_policy=deepcopy(return_policy), daily_return=daily_return, group_ties="value then symbol; split ties", group_insufficient="empty groups allowed",
        group_weight="equal among available returns", cumulative="nonoverlapping one-day; invalid after a missing interval",
        ic_rank_ties="average", icir_ddof=options.get("icir_ddof", 1), annualized=False,
        annualized_scope="ICIR only; portfolio annualization is defined in diagnostics.conventions",
        operator_contract="pool-indicator-2", shared_primitive_contract="ft-pool-1", forward_return_horizon=1, report_version=3, report_horizons=horizons, coverage_axis="historical universe membership", stage_seconds=timings, value_count=value_artifact["count"])
    result["metadata"]["indicator_backend"] = "pool_native" if getattr(indicator, "_pool_ir", None) is not None else adapter.mode
    if terminal_rows is not None:
        result["metadata"]["delisting_filter"] = "before each holding period; exclusive lifetime end > signal+h"
    from .operator_frontend import FRONTEND_VERSION
    result["metadata"]["public_operator_api"] = FRONTEND_VERSION
    if getattr(indicator, "_pool_ir", None) is not None:
        from .operators import CONTRACT
        result["metadata"]["pool_operator_contract"] = deepcopy(CONTRACT)
    result["metadata"]["value_artifact"] = value_artifact
    result["metadata"]["compute_threads"] = native.compute_threads()
    result["metadata"]["factor_price"] = 'quote_adjusted'
    result["metadata"]["price_contract"] = evaluator.contract
    result["metadata"]["rebalance_dates"] = [dates[i] for i in sorted(rebalance)]
    result["metadata"]["factor_sampling"] = 'rebalance dates only; each holding horizon uses its own signal dates'
    result["metadata"]["factor_state_inputs"] = ('daily factor observations required by configured Alpha decay'
        if simulation_warmup > 0 else 'only the history needed at rebalance signals')
    result["metadata"]["signal_clock"] = dict(
        date_axis="signal session", price_basis="same signal session close",
        visibility="only completed input frames through the signal session",
        delay=1, returns="next trading session onward; no additional input shift")
    for key in ("source_config", "workspace_config", "effective_config", "brain_settings"):
        result["metadata"][key] = deepcopy(request[key])
    result["metadata"]["simulation_warm_up_period"] = simulation_warmup
    temporary = directory / "result.tmp.json"
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    temporary.rename(directory / "result.json")
    log("complete", elapsed=time.perf_counter()-started, summary=result["summary"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    try:
        run(args.directory)
    except Exception as error:
        log("failed", error=f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    main()
