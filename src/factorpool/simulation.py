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
    from factorpool import PythonIndicator
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
    # Axes and buffers are prepared once. The pinned shared Graph owns the
    # observation clock and performs the complete chronological date loop.
    width = len(str(max(0, factors.shape[1]-1)))
    symbols = tuple(f"factor-{n:0{width}d}" for n in range(factors.shape[1]))
    graph = adapter.binding.graph_create(adapter.indicator._ir.to_dict(), ())
    if adapter.indicator._batch_ir is not None:
        adapter.binding.graph_configure_batch(graph, adapter.indicator._batch_ir.to_dict(), (), False)
    present = np.ascontiguousarray(members & np.isfinite(factors), dtype=bool)
    axes = [np.flatnonzero(row).astype(np.intp, copy=False) for row in members]
    epoch = date(2000, 1, 1)
    times = [int((epoch+timedelta(days=t)).strftime("%Y%m%d")+"150000")
             for t in range(factors.shape[0])]
    state = adapter.binding.graph_evaluate_many(graph, symbols,
        {"value": np.ascontiguousarray(factors, dtype=np.float64)}, present,
        list(range(factors.shape[0])), axes, times, True)
    if state is None:
        raise ValueError("Alpha decay requires the shared chronological native matrix bridge")
    return np.frombuffer(state['current'], dtype=np.float64).reshape(factors.shape)


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
    import _factorpool_native
    requested = list(dict.fromkeys(int(index) for index in trace_indices)) if details is not None else []
    result = _factorpool_native.alpha_targets(
        np.ascontiguousarray(transformed), np.ascontiguousarray(membership),
        neutralization, cap, requested, details is not None)
    if details is not None:
        details.update(
            trace={index: dict(decayed=transformed[index].copy(), neutralized=values)
                   for index, values in result['neutralized'].items()},
            constraints=result['constraints'])
    return result['targets']
