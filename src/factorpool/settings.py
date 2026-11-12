"""Validate observed BRAIN setting names against FactorPool's research scope.

Defaults here are local research choices, not official BRAIN defaults. Public
field evidence is recorded separately; unsupported controls never become no-ops.
"""
from copy import deepcopy
import math
import re


_BASE_FIELDS = frozenset((
    "instrumentType", "region", "universe", "language", "delay", "decay",
    "truncation", "neutralization", "pasteurization", "unitHandling",
    "nanHandling", "testPeriod", "visualization", "maxTrade", "maxPosition",
    "lookback", "simulationMode",
))
_SUPER_FIELDS = frozenset((
    "selectionHandling", "selectionLimit", "componentActivation", "universeOverride",
))
_ML_LABELS = frozenset((
    "model", "lossFunction", "epochCount", "earlyStop", "trainingPeriod", "randomSeed",
))
_DATE_DURATION = re.compile(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?\Z")
_WEEK_DURATION = re.compile(r"P\d+W\Z")


def _finite_json(value):
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _finite_json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _finite_json(item)
        return
    raise ValueError("settings must contain finite JSON values only")


def _choice(settings, key, allowed):
    value = settings[key]
    if type(value) is not str or value not in allowed:
        raise ValueError(f"settings.{key} must be one of {', '.join(allowed)}")
    return value


def normalize_settings(supplied, data):
    """Return all supported camelCase controls without mutating either input."""
    if not isinstance(supplied, dict) or any(not isinstance(key, str) for key in supplied):
        raise ValueError("settings must be an object with string keys")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    _finite_json(supplied)
    unknown = set(supplied) - _BASE_FIELDS - _SUPER_FIELDS - _ML_LABELS
    if unknown:
        raise ValueError(f"Unsupported settings option: {sorted(unknown)[0]}")
    for key in sorted(set(supplied) & _SUPER_FIELDS):
        raise ValueError(f"settings.{key} requires a SuperAlpha component/selection provider; its execution contract is unsupported")
    for key in sorted(set(supplied) & _ML_LABELS):
        raise ValueError(f"settings.{key} belongs to an unverified ML simulation contract and is unsupported")
    if "lookback" in supplied:
        raise ValueError("settings.lookback is recognized, but the official Python lookback execution contract is unavailable")

    universe = data.get("universe")
    if isinstance(universe, list):
        if (not universe or any(type(symbol) is not str or not symbol for symbol in universe)
                or len(set(universe)) != len(universe)):
            raise ValueError("data.universe must contain unique symbols")
    elif universe not in ("fixture", "all_a_dynamic"):
        raise ValueError("settings.universe requires data.universe to be fixture, all_a_dynamic or an explicit symbol list; TOPxxx liquidity universes need a qualified provider")
    result = dict(instrumentType="EQUITY", region="CHN", universe=deepcopy(universe),
                  language="PYTHONINDICATOR", delay=1, decay=0, truncation=0,
                  neutralization="MARKET", pasteurization="ON", unitHandling="OFF",
                  nanHandling="OFF", testPeriod="P0Y0M", visualization=True,
                  maxTrade="OFF", maxPosition="OFF", simulationMode="FULL")
    result.update(deepcopy(supplied))
    if result["instrumentType"] != "EQUITY" or type(result["instrumentType"]) is not str:
        raise ValueError("settings.instrumentType supports EQUITY only; other instrument datasets are unavailable")
    if result["region"] != "CHN" or type(result["region"]) is not str:
        raise ValueError("settings.region supports CHN only; other regional datasets are unavailable")
    if result["universe"] != universe:
        raise ValueError("settings.universe must match data.universe; TOPxxx liquidity universes have no qualified provider")
    if result["language"] != "PYTHONINDICATOR" or type(result["language"]) is not str:
        raise ValueError("settings.language supports PYTHONINDICATOR only")
    if type(result["delay"]) is not int or result["delay"] not in (0, 1):
        raise ValueError("settings.delay must be integer 0 or 1")
    if result["delay"] == 0:
        raise ValueError("settings.delay=0 official same-evening execution prices cannot be mapped to this daily return matrix")
    if type(result["decay"]) is not int or not 0 <= result["decay"] <= 512:
        raise ValueError("settings.decay must be an integer in 0..512")
    cap = result["truncation"]
    if type(cap) not in (int, float) or not 0 <= cap <= 1 or not math.isfinite(cap):
        raise ValueError("settings.truncation must be a finite number in 0..1")
    neutralization = _choice(result, "neutralization", (
        "NONE", "MARKET", "COUNTRY", "SECTOR", "INDUSTRY", "SUBINDUSTRY",
        "STATISTICAL", "RAM", "CROWDING", "FAST", "SLOW", "SLOW_AND_FAST",
    ))
    if neutralization in ("COUNTRY", "SECTOR", "INDUSTRY", "SUBINDUSTRY"):
        raise ValueError(f"settings.neutralization={neutralization} requires a qualified historical classification provider")
    if neutralization not in ("NONE", "MARKET"):
        raise ValueError(f"settings.neutralization={neutralization} requires verified risk-model rules and historical risk exposures")
    _choice(result, "pasteurization", ("ON", "OFF"))
    if _choice(result, "unitHandling", ("OFF", "VERIFY")) != "OFF":
        raise ValueError("settings.unitHandling=VERIFY requires operator dimensional analysis, which is unavailable")
    if _choice(result, "nanHandling", ("OFF", "ON")) != "OFF":
        raise ValueError("settings.nanHandling=ON requires verified operator-level NaN semantics; terminal zero filling is unsupported")
    for key in ("maxTrade", "maxPosition"):
        if _choice(result, key, ("OFF", "ON")) != "OFF":
            raise ValueError(f"settings.{key}=ON requires ADV thresholds, a reference book and an official execution oracle")
    if _choice(result, "simulationMode", ("FULL", "QUICK")) != "FULL":
        raise ValueError("settings.simulationMode=QUICK has no supported execution contract; use FULL")
    period = result["testPeriod"]
    duration = _DATE_DURATION.fullmatch(period) if type(period) is str else None
    if not ((duration and any(part is not None for part in duration.groups()))
            or (type(period) is str and _WEEK_DURATION.fullmatch(period))):
        raise ValueError("settings.testPeriod must be an ISO daily duration such as P1Y6M, P30D or P2W; intraday, negative and fractional durations are unsupported")
    if type(result["visualization"]) is not bool:
        raise ValueError("settings.visualization must be boolean")
    return result
