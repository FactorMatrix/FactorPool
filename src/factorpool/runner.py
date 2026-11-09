"""One task per process; native Indicator and native research statistics."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
from .adapter import IndicatorAdapter
from .datasource import fingerprint, fixture
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
    if data.get("frequency", "1d") != "1d" or data.get("adjustment", "raw") != "raw":
        raise ValueError("This release supports daily, raw prices only")
    if analysis.get("entry", "next_open") != "next_open" or analysis.get("exit", "following_open") != "following_open":
        raise ValueError("Supported return interval: T+1 open to T+2 open")
    if analysis.get("annualize_icir", False) or analysis.get("group_weight", "equal") != "equal":
        raise ValueError("Supported analysis: equal weights, unannualized ICIR")
    if data["factor_start"] > data["factor_end"]:
        raise ValueError("End precedes start")


def run(directory):
    import _factorpool_native as native
    directory = Path(directory)
    request = json.loads((directory / "request.json").read_text())
    validate(request)
    horizons, annual_days, decay_steps = report_options(request["analysis"])
    started = time.perf_counter()
    timings = {}
    (directory / "factor.py").write_text(request["source_code"])
    factory = load_factor(directory / "factor.py", request["factor_class"], request.get("factor_params", {}))
    # Only the shared compiler/Graph binding is used; no Engine/Runtime instance.
    indicator = factory()
    adapter = IndicatorAdapter(indicator)
    warmup = indicator.warm_up_period
    timings["prepare"] = time.perf_counter() - started
    log("prepared", mode=adapter.mode, warmup=warmup)
    checkpoint = time.perf_counter()
    data = request["data"]
    root = os.environ.get("FACTORPOOL_MMAP_ROOT", "/data/market")
    provider = data["provider"]
    before = None
    if provider == "fixture":
        payload = fixture()
        if data.get("universe") not in (None, "fixture"):
            raise ValueError("Fixture universe is A/B/C")
    elif provider == "ft_daily_mmap":
        before = fingerprint(root)
        universe = data.get("universe")
        dynamic = universe == "all_a_dynamic"
        if not dynamic and (not isinstance(universe, list) or not universe or len(universe) != len(set(universe))):
            raise ValueError("Specify unique symbol list or all_a_dynamic")
        payload = native.read_mmap([] if dynamic else universe, root, data["factor_start"], data["factor_end"], max(1, warmup), int(dynamic), max(horizons)*decay_steps+1)
        payload.update(snapshot_id=before, synthetic=False)
    else:
        raise ValueError(f"Unknown/unconfigured provider: {provider}")
    timings["read"] = time.perf_counter() - checkpoint
    log("loaded", provider=provider, frames=len(payload["frames"]), snapshot_id=payload["snapshot_id"])
    checkpoint = time.perf_counter()
    frames = payload["frames"]
    if any(a["date"] >= b["date"] for a, b in zip(frames, frames[1:])):
        raise ValueError("Input dates must be unique and strictly increasing")
    symbols = sorted(payload["symbols"])
    matrices, prices, rows, dates, membership = [], [], [], [], []
    for index, frame in enumerate(frames):
        if frame["date"] > data["factor_end"]:
            continue  # Future prices never enter Indicator state.
        values = adapter.update(frame, factory)
        if frame["date"] < data["factor_start"]:
            continue
        lookup = {r["symbol"]: r for r in values}
        matrices.append([lookup[s]["value"] if s in lookup else None for s in symbols])
        dates.append(frame["date"])
        membership.append([s in frame["symbols"] for s in symbols])
        for symbol in symbols:
            cell = lookup.get(symbol, dict(symbol=symbol, value=None, valid=False, ready=False, reason="outside_universe"))
            rows.append(dict(date=frame["date"], **cell))
        if index % 20 == 0:
            log("calculate", progress=round((index+1)/len(frames), 4))
    if not dates:
        raise ValueError("No factor observations in requested date range")
    # Include every calendar day from first factor date and its future tail.
    tail = [f for f in frames if f["date"] >= dates[0]]
    prices = [[f["rows"].get(s, {}).get("open") for s in symbols] for f in tail]
    if [f["date"] for f in tail[:len(dates)]] != dates:
        raise ValueError("Factor and price date axes differ")
    timings["calculate"] = time.perf_counter() - checkpoint
    checkpoint = time.perf_counter()
    options = request["analysis"]
    result = native.analyze(matrices, prices, options.get("groups", 3), options.get("min_ic_pairs", 3), options.get("icir_ddof", 1), membership)
    result["diagnostics"] = build_diagnostics(matrices, prices, membership, dates, [f["date"] for f in tail], options)
    for date, day in zip(dates, result["daily"]):
        day["date"] = date
    returns, groups = result.pop("forward_returns"), result.pop("groups")
    with (directory / "values.jsonl").open("w") as output:
        for index, row in enumerate(rows):
            t, n = divmod(index, len(symbols))
            row.update(forward_return=returns[t][n], group=groups[t][n])
            output.write(json.dumps(row, allow_nan=False) + "\n")
    timings["analysis_export"] = time.perf_counter() - checkpoint
    if before is not None and fingerprint(root) != before:
        raise ValueError("Market snapshot changed during task; result rejected")
    build_info = json.loads(Path(os.environ.get("FACTORPOOL_BUILD_INFO", "/opt/factorpool/build-info.json")).read_text())
    result["metadata"] = dict(build=build_info, execution_mode=adapter.mode, warm_up_period=warmup,
        source_sha256=hashlib.sha256(request["source_code"].encode()).hexdigest(), snapshot_id=payload["snapshot_id"],
        provider=provider, synthetic=payload["synthetic"], frequency="1d", adjustment="raw", timezone="Asia/Shanghai",
        return_interval="T+1 open to T+2 open", group_ties="value then symbol; split ties", group_insufficient="empty groups allowed",
        group_weight="equal among available returns", cumulative="nonoverlapping one-day; invalid after a missing interval",
        ic_rank_ties="average", icir_ddof=options.get("icir_ddof", 1), annualized=False,
        annualized_scope="legacy ICIR only; portfolio annualization is defined in diagnostics.conventions",
        operator_contract="ft-pool-1", forward_return_horizon=1, report_version=2, report_horizons=horizons, coverage_axis="historical universe membership", stage_seconds=timings, value_count=len(rows))
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
