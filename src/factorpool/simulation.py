"""Research Alpha targets; all temporal calculations use the shared Indicator."""
from datetime import date, timedelta

import numpy as np

from .settings import normalize_settings


ALPHA_POLICY = (
    "Shared FT linear decay over valid factor observations, then finite in-pool "
    "NONE/MARKET arithmetic neutralization and L1 normalization; truncation clips "
    "absolute single-name weights without renormalizing, leaving unused gross "
    "unallocated. Missing cross sections, MARKET constants and zero L1 norm "
    "are invalid. Local research "
    "semantics; official BRAIN numerical parity is unverified."
)
SETTINGS_SCOPE = (
    "Decay, neutralization and truncation transform only Alpha portfolio targets; "
    "raw Indicator IC and factor groups stay unchanged. Settings defaults are "
    "FactorPool research choices, not official BRAIN defaults."
)


def _decayed(factors, members, period):
    from factortrader import PythonIndicator
    from .adapter import IndicatorAdapter

    class LinearDecay(PythonIndicator):
        def __init__(self, period):
            super().__init__()
            self.period = period

        def update(self, input):
            value = self.ts.decay_linear(input.value, self.period)
            self.value = value
            return self.ts.is_ready(value)

    factory = lambda: LinearDecay(period)
    adapter = IndicatorAdapter(factory())
    if adapter.mode != "native":
        raise ValueError("Alpha decay requires the shared native ts.decay_linear operator")
    output = np.full(factors.shape, np.nan, dtype=np.float64)
    symbols = [f"factor-{n}" for n in range(factors.shape[1])]
    lookup = {symbol: n for n, symbol in enumerate(symbols)}
    epoch = date(2000, 1, 1)
    for t in range(factors.shape[0]):
        axis = [symbol for n, symbol in enumerate(symbols) if members[t, n]]
        rows = {symbol: {"value": float(factors[t, n])}
                for n, symbol in enumerate(symbols)
                if members[t, n] and np.isfinite(factors[t, n])}
        # Only ordered observations matter to this count-based shared window.
        frame = dict(date=(epoch + timedelta(days=t)).isoformat(), symbols=axis, rows=rows)
        for item in adapter.update(frame, factory):
            if item["valid"] and item["value"] is not None:
                output[t, lookup[item["symbol"]]] = item["value"]
    return output


def alpha_targets(full_factors, full_membership, settings, *, details=None, trace_indices=()):
    """Return contiguous target weights, or None for the existing native Alpha."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    settings = normalize_settings(settings, {"universe": settings.get("universe", "fixture")})
    factors = np.asarray(full_factors, dtype=np.float64)
    membership = np.asarray(full_membership, dtype=np.float64)
    if factors.ndim != 2 or membership.shape != factors.shape:
        raise ValueError("Alpha factors and membership must have the same date x symbol shape")
    period, neutralization, cap = settings["decay"], settings["neutralization"], settings["truncation"]
    if period <= 1 and neutralization == "MARKET" and cap == 0:
        return None
    members = np.isfinite(membership) & (membership > 0)
    transformed = _decayed(factors, members, period) if period > 1 else factors
    targets = np.full(factors.shape, np.nan, dtype=np.float64, order="C")
    traces = set(trace_indices)
    if details is not None:
        details.update(trace={}, constraints=[])
    for t in range(factors.shape[0]):
        valid = members[t] & np.isfinite(transformed[t])
        values = transformed[t, valid].astype(np.longdouble)
        if details is not None and t in traces:
            details["trace"][t] = dict(decayed=transformed[t].copy(),
                                       neutralized=np.full(factors.shape[1], np.nan))
        if values.size == 0 or (neutralization == "MARKET" and np.all(values == values[0])):
            if details is not None and t in traces and values.size:
                details["trace"][t]["neutralized"][valid] = 0
            if details is not None and cap > 0:
                details["constraints"].append(dict(index=t, valid_count=int(values.size),
                    capacity=float(values.size*cap), full_gross_feasible=bool(values.size*cap >= 1-1e-12),
                    target_gross=None, target_net=None, unallocated_gross=None, status="no_valid_weights"))
            continue
        if neutralization == "MARKET":
            values -= values.mean(dtype=np.longdouble)
        if details is not None and t in traces:
            details["trace"][t]["neutralized"][valid] = values
        norm = np.abs(values).sum(dtype=np.longdouble)
        if not np.isfinite(norm) or norm <= 0:
            if details is not None and cap > 0:
                details["constraints"].append(dict(index=t, valid_count=int(values.size),
                    capacity=float(values.size*cap), full_gross_feasible=bool(values.size*cap >= 1-1e-12),
                    target_gross=None, target_net=None, unallocated_gross=None, status="no_valid_weights"))
            continue
        weights = np.asarray(values / norm, dtype=np.float64)
        if cap > 0:
            weights = np.clip(weights, -cap, cap)
        targets[t] = 0
        targets[t, valid] = weights
        if details is not None and cap > 0:
            gross = float(np.abs(weights).sum())
            feasible = values.size*cap >= 1-1e-12
            details["constraints"].append(dict(index=t, valid_count=int(values.size),
                capacity=float(values.size*cap), full_gross_feasible=bool(feasible),
                target_gross=gross, target_net=float(weights.sum()), unallocated_gross=max(0., 1-gross),
                status="unit_gross_capacity_infeasible" if not feasible else ("unallocated_gross" if gross < 1-1e-12 else "ok")))
    return targets
