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
    if data.get("provider") == "ft_daily_mmap":
        raise ValueError("ft_daily_mmap has migrated to market_matrix; new tasks must read a PostgreSQL market snapshot")
    if data.get("provider") not in ("fixture", "market_matrix"):
        raise ValueError("Supported providers: market_matrix or fixture")
    if data.get("frequency", "1d") != "1d" or data.get("adjustment", "raw") != "raw":
        raise ValueError("Daily basic source quotes are required")
    if data.get("factor_price", "quote_adjusted") != "quote_adjusted":
        raise ValueError("Factor prices only support forward adjustment (quote_adjusted)")
    if analysis.get("entry", "signal_close") != "signal_close" or analysis.get("exit", "next_close") != "next_close":
        raise ValueError("Supported return interval: signal_close to next_close, using close / prev_close - 1; legacy open-based requests are unsupported")
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
        policy = dict(version=("synthetic-price-ratio-v1" if payload["synthetic"]
                               else "legacy-price-ratio-v1"), formula="close/prev_close-1")
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
    elif version in ("legacy-price-ratio-v1", "synthetic-price-ratio-v1"):
        description = "close / prev_close - 1; no volume-based zeroing or additional corporate-action adjustment"
        price = "close / prev_close - 1"
    else:
        description = "Stored snapshot-derived daily returns; see return_policy; no reader-side recomputation"
        price = "stored snapshot returns; see return_policy"
    return policy, description, price


def run(directory, *, factory=None, values_format="jsonl"):
    import _factorpool_native as native
    directory = Path(directory)
    if values_format not in ("matrix", "jsonl"):
        raise ValueError("values_format must be matrix or jsonl")
    request = json.loads((directory / "request.json").read_text())
    if request.get("source_mode") == "script":
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
                       matrix_only=True, fields=indicator_fields(indicator), **extra)
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
    all_factors = np.full((count, width), np.nan, dtype=np.float64)
    valid = np.zeros((len(dates), width), dtype=bool)
    ready = np.zeros_like(valid)
    reasons = np.full(valid.shape, 6, dtype=np.uint8)  # outside_universe
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
    calculation_to_research = np.full(len(evaluator.symbols), -1, dtype=np.intp)
    calculation_lookup = evaluator.columns
    for n, symbol in enumerate(symbols):
        index = calculation_lookup.get(symbol)
        if index is not None:
            calculation_to_research[index] = n
    evaluation_indices = [index for index in range(count)
                          if index-first_signal in rebalance or simulation_warmup > 0]
    batch = evaluator.evaluate_many(evaluation_indices)
    if batch is not None:
        indices = np.asarray(batch['indices'], dtype=np.intp)
        columns = np.flatnonzero(calculation_to_research >= 0)
        targets = calculation_to_research[columns]
        full_axis = (len(columns) == len(evaluator.symbols)
                     and np.array_equal(targets, np.arange(width)))
        member = all_members[indices[:, None], targets]
        values = batch['value'] if full_axis else batch['value'][:, columns]
        all_factors[indices[:, None], targets] = np.where(member, values, np.nan)
        offsets = indices-first_signal
        published = (offsets >= 0) & np.isin(offsets, list(rebalance))
        output_rows = offsets[published, None]
        observed = member[published]
        for name, output in [('valid', valid), ('ready', ready), ('reason', reasons)]:
            values = batch[name] if full_axis else batch[name][:, columns]
            values = values[published]
            output[output_rows, targets] = (np.where(observed, values, 6) if name == 'reason'
                                            else values & observed)
        del batch, member, values
        log("calculate", progress=1)
    else:
        for index in evaluation_indices:
            offset = index - first_signal
            state = evaluator.evaluate_arrays(index)
            columns = np.asarray(state['columns'], dtype=np.intp)
            targets = calculation_to_research[columns]
            keep = targets >= 0
            keep[keep] &= all_members[index, targets[keep]]
            selected = targets[keep]
            all_factors[index, selected] = state['value'][keep]
            if offset >= 0 and offset in rebalance:
                valid[offset, selected] = state['valid'][keep]
                ready[offset, selected] = state['ready'][keep]
                reasons[offset, selected] = state['reason'][keep]
            if index % 100 == 0:
                log("calculate", progress=round((index+1)/len(frames), 4))
    factors = all_factors[first_signal:]
    members = np.ascontiguousarray(all_members[first_signal:] & signal_mask[:, None])
    # Daily observations needed only by explicit Alpha decay remain in all_factors;
    # the primary IC/group matrix still contains only the union of rebalance dates.
    if simulation_warmup > 0 and not np.all(signal_mask):
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
    settings_details = {}
    if settings is not None:
        from .simulation import alpha_targets
        offset = first_signal
        all_targets = alpha_targets(all_factors, all_members, settings,
                                    details=settings_details if trace_indices or settings["truncation"] > 0 else None,
                                    trace_indices=[i+offset for i in trace_indices
                                                   if i+1 < len(tail) and any(i % h == 0 for h in horizons)])
        if all_targets is not None:
            alpha_weights = np.ascontiguousarray(all_targets[-len(dates):])
        transforms = {i-offset: values for i, values in settings_details.get("trace", {}).items()}
    prepared = (native.prepare_research(factors, returns, members) if alpha_weights is None
                else native.prepare_research(factors, returns, members, alpha_weights))
    result = native.analyze(prepared, None, options.get("groups", 3), options.get("min_ic_pairs", 3),
                            options.get("icir_ddof", 1), None, 1, 1, True)
    trace_info = dict(indices=trace_indices, symbols=symbols, transforms=transforms,
                      factor_arrays=dict(reason=reasons, valid=valid, ready=ready)) if trace_indices else None
    result["diagnostics"] = build_diagnostics(prepared, None, None, dates, [f["date"] for f in tail], options,
                                             primary=result, trace_info=trace_info, rebalance_only=True)
    conventions = result["diagnostics"]["conventions"]
    conventions.update(factor_sampling="per-horizon rebalance dates; primary 1D IC uses their union",
                       price_adjustment="forward only: historical price times Q ratio; volume divided by the same ratio",
                       return_policy=deepcopy(return_policy), daily_return=daily_return,
                       return_price=return_price,
                       return_matrix="signal t uses daily returns R[t+1]..R[t+h]; derivation follows return_policy",
                       missing_returns="missing nonzero position daily returns remain invalid; no reader-side zero filling or hindsight reweighting; source derivation follows return_policy")
    if settings is not None:
        from .simulation import ALPHA_POLICY, SETTINGS_SCOPE
        conventions.update(brain_settings=deepcopy(settings), alpha_weights=ALPHA_POLICY,
                           settings_scope=SETTINGS_SCOPE,
                           long_short="Group spread targets use long 0.5/short 0.5; Alpha gross/net depend on effective settings and need not remain 1/0 after truncation")
        from .reporting.partition import add_test_period
        add_test_period(result["diagnostics"], settings["testPeriod"], data["factor_end"], annual_days, data["factor_start"])
        constraints = []
        for item in settings_details.get("constraints", []):
            index = item["index"]-offset
            if index in rebalance:
                constraints.append(dict(item, date=dates[index], index=index))
        if settings["truncation"] > 0:
            result["diagnostics"]["alpha_constraints"] = dict(
                truncation=settings["truncation"], target_gross_reference=1,
                policy="rebalance target cap without redistribution; infeasible full gross is reported, residual remains unallocated; no forced full investment",
                feasibility_scope="total capacity N*cap only; not a side/net/redistribution solver; actual target gross and net are reported separately",
                daily=constraints,
                infeasible_dates=[item["date"] for item in constraints if not item["full_gross_feasible"]])
    from .reporting.pnl import add_amounts
    add_amounts(result["diagnostics"], options)
    for date, day in zip(dates, result["daily"]):
        day["date"] = date
    timings["analysis"] = time.perf_counter() - checkpoint
    export_started = time.perf_counter()
    forward_returns, groups = result.pop("forward_returns"), result.pop("groups")
    result['daily'] = [day for i, day in enumerate(result['daily']) if i in rebalance]
    from .result_matrix import write_result_matrix, write_jsonl
    value_artifact = write_result_matrix(directory / "values.matrix", dates, symbols, factors,
                                        valid, ready, reasons, forward_returns, groups,
                                        signal_indices=rebalance, threads=native.compute_threads())
    if values_format == "jsonl":
        write_jsonl(directory / "values.jsonl", directory / "values.matrix")
    timings["export"] = time.perf_counter() - export_started
    timings["analysis_export"] = time.perf_counter() - checkpoint
    build_info = json.loads(Path(os.environ.get("FACTORPOOL_BUILD_INFO", "/opt/factorpool/build-info.json")).read_text())
    result["metadata"] = dict(build=build_info, execution_mode=adapter.mode, warm_up_period=warmup,
        source_sha256=hashlib.sha256(request["source_code"].encode()).hexdigest(), snapshot_id=payload["snapshot_id"],
        provider=provider, synthetic=payload["synthetic"], frequency="1d", adjustment="raw", timezone="Asia/Shanghai",
        return_interval="signal close to next close; daily R[t+1] follows return_policy", return_policy=deepcopy(return_policy), daily_return=daily_return, group_ties="value then symbol; split ties", group_insufficient="empty groups allowed",
        group_weight="equal among available returns", cumulative="nonoverlapping one-day; invalid after a missing interval",
        ic_rank_ties="average", icir_ddof=options.get("icir_ddof", 1), annualized=False,
        annualized_scope="legacy ICIR only; portfolio annualization is defined in diagnostics.conventions",
        operator_contract="pool-indicator-2", shared_primitive_contract="ft-pool-1", forward_return_horizon=1, report_version=3, report_horizons=horizons, coverage_axis="historical universe membership", stage_seconds=timings, value_count=value_artifact["count"])
    result["metadata"]["indicator_backend"] = "pool_native" if getattr(indicator, "_pool_ir", None) is not None else adapter.mode
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
    result["metadata"]["source_mode"] = request.get("source_mode", "class")
    if request.get("source_mode") == "script":
        for key in ("source_config", "effective_config", "brain_settings"):
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
        run(args.directory, values_format="matrix")
    except Exception as error:
        log("failed", error=f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    main()
