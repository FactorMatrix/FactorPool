"""Serializable research configuration, independent of the trading runtime."""
from copy import deepcopy
from datetime import date, timedelta
import calendar
import math
import re
from .settings import normalize_settings


def test_period_start(end, period):
    """Inclusive first validation date; zero duration leaves the full period."""
    last = date.fromisoformat(end)
    if not isinstance(period, str):
        raise ValueError("testPeriod must be an ISO calendar duration")
    try:
        if re.fullmatch(r"P\d+W", period):
            days = int(period[1:-1]) * 7
            return (last - timedelta(days=days - 1)).isoformat() if days else None
        match = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?", period)
        if match is None or all(part is None for part in match.groups()):
            raise ValueError("testPeriod must be an ISO calendar duration")
        years, months, days = (int(part or 0) for part in match.groups())
        if not (years or months or days):
            return None
        total = last.year * 12 + last.month - 1 - years * 12 - months
        year, month = divmod(total, 12)
        month += 1
        if not 1 <= year <= 9999:
            raise ValueError("testPeriod exceeds the supported calendar")
        boundary = date(year, month, min(last.day, calendar.monthrange(year, month)[1]))
        return (boundary - timedelta(days=days - 1)).isoformat()
    except OverflowError:
        raise ValueError("testPeriod exceeds the supported calendar") from None


def _object(value, label):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return value


def _keys(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unsupported {label} option: {sorted(unknown)[0]}")


def _json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json(item)
        return
    raise ValueError("Configuration must contain finite JSON values only")


def normalize_config(config):
    config = deepcopy(_object(config, "config"))
    _json(config)
    _keys(config, ("type", "base", "data", "factor", "analysis", "settings", "selection", "combo"), "config")
    simulation_type = config.get("type", "REGULAR")
    if simulation_type not in ("REGULAR", "SUPER"):
        raise ValueError("config.type must be REGULAR or SUPER")
    if simulation_type != "REGULAR":
        raise ValueError("config.type=SUPER requires a candidate Alpha pool, selection and combination engine; this mode is not implemented")
    if "selection" in config or "combo" in config:
        raise ValueError("config.selection/config.combo belong to SuperAlpha; REGULAR research cannot execute them")
    base = _object(config.get("base"), "config.base")
    _keys(base, ("start_date", "end_date", "frequency"), "base")
    normalized_dates = {}
    for key in ("start_date", "end_date"):
        if not isinstance(base.get(key), str):
            raise ValueError(f"config.base.{key} is required")
        try:
            normalized_dates[key] = date.fromisoformat(base[key]).isoformat()
        except ValueError:
            raise ValueError(f"config.base.{key} must be an ISO date") from None
    if normalized_dates["end_date"] < normalized_dates["start_date"]:
        raise ValueError("config.base.end_date precedes start_date")
    if base.get("frequency", "1d") != "1d":
        raise ValueError("Only daily frequency is supported")
    data = deepcopy(_object(config.get("data"), "config.data"))
    _keys(data, ("provider", "universe", "adjustment", "factor_price", "snapshot_id"), "data")
    if data.get("provider") not in ("market_matrix", "fixture"):
        raise ValueError("config.data.provider must be market_matrix or fixture")
    if data.get("adjustment", "raw") != "raw":
        raise ValueError("Only raw source quotes are supported")
    factor_price = data.get("factor_price", "raw")
    if factor_price not in ("raw", "quote_adjusted") or type(factor_price) is not str:
        raise ValueError("config.data.factor_price must be raw or quote_adjusted")
    universe = data.get("universe")
    if data["provider"] == "fixture":
        if universe != "fixture":
            raise ValueError("Fixture universe must be fixture")
    elif universe != "all_a_dynamic":
        if (not isinstance(universe, list) or not universe
                or any(not isinstance(symbol, str) or not symbol for symbol in universe)
                or len(set(universe)) != len(universe)):
            raise ValueError("Universe must be unique symbols or all_a_dynamic")
    if "snapshot_id" in data and (not isinstance(data["snapshot_id"], str) or not data["snapshot_id"]):
        raise ValueError("snapshot_id must be a nonempty string")
    data.update(factor_start=normalized_dates["start_date"], factor_end=normalized_dates["end_date"],
                frequency="1d", adjustment="raw")
    # Preserve old immutable script definitions: an omitted additive option
    # keeps its old shape. The runner records its effective raw price contract.
    if "factor_price" in data:
        data["factor_price"] = factor_price
    params = deepcopy(_object(config.get("factor", {}), "config.factor"))
    analysis = dict(entry="signal_close", exit="next_close", groups=3,
                    group_weight="equal", min_ic_pairs=3, icir_ddof=1,
                    annualize_icir=False, horizons=[1, 5, 20], annual_days=252,
                    decay_steps=10, book_size=1, pnl_unit="BOOK", trace_dates=[])
    supplied = _object(config.get("analysis", {}), "config.analysis")
    _keys(supplied, analysis, "analysis")
    analysis.update(deepcopy(supplied))
    for key, minimum, maximum in (("groups", 1, 100), ("min_ic_pairs", 2, 2147483647),
                                   ("icir_ddof", 0, 1), ("annual_days", 1, 366),
                                   ("decay_steps", 1, 10)):
        if (type(analysis[key]) is not int or analysis[key] < minimum
                or (maximum is not None and analysis[key] > maximum)):
            raise ValueError(f"Invalid analysis.{key}")
    periods = analysis["horizons"]
    if (not isinstance(periods, list) or not 1 <= len(periods) <= 6
            or any(type(h) is not int or not 1 <= h <= 252 for h in periods)
            or len(set(periods)) != len(periods)):
        raise ValueError("analysis.horizons must contain 1..6 unique periods in 1..252")
    if (analysis["entry"] != "signal_close" or analysis["exit"] != "next_close"
            or analysis["group_weight"] != "equal" or analysis["annualize_icir"] is not False):
        raise ValueError("Only signal-close returns, equal groups and unannualized ICIR are supported")
    from .reporting_options import monetary_options, trace_dates
    monetary_options(analysis)
    analysis["trace_dates"] = trace_dates(analysis, normalized_dates["start_date"], normalized_dates["end_date"])
    settings = normalize_settings(config.get("settings", {}), data)
    test_period_start(data["factor_end"], settings["testPeriod"])
    return dict(simulation_type=simulation_type, factor_params=params, data=data, analysis=analysis,
                source_config=config, brain_settings=settings)


def effective_config(request):
    data = request["data"]
    result = deepcopy(request.get("source_config", {}))
    result.update(base=dict(start_date=data["factor_start"], end_date=data["factor_end"],
                            frequency=data["frequency"]),
                  data={key: deepcopy(data[key]) for key in
                        ("provider", "universe", "adjustment", "factor_price", "snapshot_id") if key in data},
                  factor=deepcopy(request["factor_params"]), analysis=deepcopy(request["analysis"]))
    if "brain_settings" in request:
        result["settings"] = deepcopy(request["brain_settings"])
    return result
