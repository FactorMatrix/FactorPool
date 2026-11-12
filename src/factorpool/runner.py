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
    spec = importlib.util.spec_from_file_location("factorpool_user_factor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cls = getattr(module, class_name)
    return lambda: cls(**params)


def validate(request):
    data, analysis = request["data"], request["analysis"]
    if data.get("provider") == "ft_daily_mmap":
        raise ValueError("ft_daily_mmap has migrated to market_matrix; new tasks must read a PostgreSQL market snapshot")
    if data.get("provider") not in ("fixture", "market_matrix"):
        raise ValueError("Supported providers: market_matrix or fixture")
    if data.get("frequency", "1d") != "1d" or data.get("adjustment", "raw") != "raw":
        raise ValueError("This release supports daily, raw prices only")
    if analysis.get("entry", "signal_close") != "signal_close" or analysis.get("exit", "next_close") != "next_close":
        raise ValueError("Supported return interval: signal_close to next_close, using close / prev_close - 1; legacy open-based requests are unsupported")
    if analysis.get("annualize_icir", False) or analysis.get("group_weight", "equal") != "equal":
        raise ValueError("Supported analysis: equal weights, unannualized ICIR")
    if data["factor_start"] > data["factor_end"]:
        raise ValueError("End precedes start")


def returns_matrix(frames, symbols):
    """Build daily returns from each bar's own close and previous close."""
    closes = np.asarray([[frame["rows"].get(symbol, {}).get("close") for symbol in symbols]
                         for frame in frames], dtype=np.float64)
    previous = np.asarray([[frame["rows"].get(symbol, {}).get("prev_close") for symbol in symbols]
                           for frame in frames], dtype=np.float64)
    valid = np.isfinite(closes) & np.isfinite(previous) & (previous > 0) & (closes >= 0)
    returns = np.full(closes.shape, np.nan, dtype=np.float64, order="C")
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        np.divide(closes, previous, out=returns, where=valid)
        np.subtract(returns, 1, out=returns, where=valid)
    returns[~np.isfinite(returns)] = np.nan
    return returns


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


def run(directory, *, factory=None):
    import _factorpool_native as native
    directory = Path(directory)
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
    # Only the shared compiler/Graph binding is used; no Engine/Runtime instance.
    indicator = factory()
    adapter = IndicatorAdapter(indicator)
    warmup = indicator.warm_up_period
    settings = request.get("brain_settings")
    simulation_warmup = max(0, settings["decay"] - 1) if settings is not None else 0
    timings["prepare"] = time.perf_counter() - started
    log("prepared", mode=adapter.mode, warmup=warmup)
    checkpoint = time.perf_counter()
    data = request["data"]
    provider = data["provider"]
    if provider == "fixture":
        payload = fixture()
        if data.get("universe") not in (None, "fixture"):
            raise ValueError("Fixture universe is A/B/C")
    elif provider == "market_matrix":
        from .market_store import load
        extra = {"pasteurization": "OFF"} if settings is not None and settings["pasteurization"] == "OFF" else {}
        payload = load(data, max(1, warmup + simulation_warmup), max(horizons)*decay_steps, **extra)
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
    matrices, rows, dates, membership = [], [], [], []
    warm_factors, warm_membership = [], []
    for index, frame in enumerate(frames):
        if frame["date"] > data["factor_end"]:
            continue  # Future prices never enter Indicator state.
        values = adapter.update(frame, factory)
        lookup = {r["symbol"]: r for r in values}
        current_members = set(frame.get("research_symbols", frame["symbols"]))
        current_factors = [lookup[s]["value"] if s in current_members and s in lookup else None for s in symbols]
        current_membership = [s in current_members for s in symbols]
        if settings is not None:
            warm_factors.append(current_factors)
            warm_membership.append(current_membership)
        if frame["date"] < data["factor_start"]:
            continue
        matrices.append(current_factors)
        dates.append(frame["date"])
        membership.append(current_membership)
        for symbol in symbols:
            cell = lookup.get(symbol) if symbol in current_members else None
            if cell is None:
                cell = dict(symbol=symbol, value=None, valid=False, ready=False, reason="outside_universe")
            rows.append(dict(date=frame["date"], **cell))
        if index % 20 == 0:
            log("calculate", progress=round((index+1)/len(frames), 4))
    if not dates:
        raise ValueError("No factor observations in requested date range")
    # Include every calendar day from first factor date and its future tail.
    tail = [f for f in frames if f["date"] >= dates[0]]
    if [f["date"] for f in tail[:len(dates)]] != dates:
        raise ValueError("Factor and price date axes differ")
    factors = np.ascontiguousarray(matrices, dtype=np.float64)
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
    members = np.ascontiguousarray(membership, dtype=np.float64)
    timings["calculate"] = time.perf_counter() - checkpoint
    checkpoint = time.perf_counter()
    options = request["analysis"]
    alpha_weights = None
    if settings is not None:
        from .simulation import alpha_targets
        all_targets = alpha_targets(np.asarray(warm_factors, dtype=np.float64),
                                    np.asarray(warm_membership, dtype=np.float64), settings)
        if all_targets is not None:
            alpha_weights = np.ascontiguousarray(all_targets[-len(dates):])
    prepared = (native.prepare_research(factors, returns, members) if alpha_weights is None
                else native.prepare_research(factors, returns, members, alpha_weights))
    result = native.analyze(prepared, None, options.get("groups", 3), options.get("min_ic_pairs", 3), options.get("icir_ddof", 1))
    result["diagnostics"] = build_diagnostics(prepared, None, None, dates, [f["date"] for f in tail], options, primary=result)
    conventions = result["diagnostics"]["conventions"]
    conventions.update(return_policy=deepcopy(return_policy), daily_return=daily_return,
                       return_price=return_price,
                       return_matrix="signal t uses daily returns R[t+1]..R[t+h]; derivation follows return_policy",
                       missing_returns="missing nonzero position daily returns remain invalid; no reader-side zero filling or hindsight reweighting; source derivation follows return_policy")
    if settings is not None:
        from .simulation import ALPHA_POLICY, SETTINGS_SCOPE
        conventions.update(brain_settings=deepcopy(settings), alpha_weights=ALPHA_POLICY,
                           settings_scope=SETTINGS_SCOPE)
        from .reporting.partition import add_test_period
        add_test_period(result["diagnostics"], settings["testPeriod"], data["factor_end"], annual_days, data["factor_start"])
    for date, day in zip(dates, result["daily"]):
        day["date"] = date
    returns, groups = result.pop("forward_returns"), result.pop("groups")
    with (directory / "values.jsonl").open("w") as output:
        for index, row in enumerate(rows):
            t, n = divmod(index, len(symbols))
            row.update(forward_return=returns[t][n], group=groups[t][n])
            output.write(json.dumps(row, allow_nan=False) + "\n")
    timings["analysis_export"] = time.perf_counter() - checkpoint
    build_info = json.loads(Path(os.environ.get("FACTORPOOL_BUILD_INFO", "/opt/factorpool/build-info.json")).read_text())
    result["metadata"] = dict(build=build_info, execution_mode=adapter.mode, warm_up_period=warmup,
        source_sha256=hashlib.sha256(request["source_code"].encode()).hexdigest(), snapshot_id=payload["snapshot_id"],
        provider=provider, synthetic=payload["synthetic"], frequency="1d", adjustment="raw", timezone="Asia/Shanghai",
        return_interval="signal close to next close; daily R[t+1] follows return_policy", return_policy=deepcopy(return_policy), daily_return=daily_return, group_ties="value then symbol; split ties", group_insufficient="empty groups allowed",
        group_weight="equal among available returns", cumulative="nonoverlapping one-day; invalid after a missing interval",
        ic_rank_ties="average", icir_ddof=options.get("icir_ddof", 1), annualized=False,
        annualized_scope="legacy ICIR only; portfolio annualization is defined in diagnostics.conventions",
        operator_contract="ft-pool-1", forward_return_horizon=1, report_version=3, report_horizons=horizons, coverage_axis="historical universe membership", stage_seconds=timings, value_count=len(rows))
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
        run(args.directory)
    except Exception as error:
        log("failed", error=f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    main()
