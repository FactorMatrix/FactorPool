"""Thin buffer wrapper for the prebuilt C++ daily-adjustment kernel."""
from dataclasses import dataclass
import numpy as np


def adjustment_policy():
    return dict(
        version="prev-close-last-actual-v2",
        factor="prev_close / last_actual_close on a consistent actual trading row",
        source="same-source prev_close includes reference adjustments through the resumed actual trading day",
        actual="finite positive volume and finite close>=0; observed zero close advances the actual anchor",
        amount="optional NaN is accepted; known nonpositive/nonfinite turnover with positive volume is inconsistent",
        suspension="finite zero volume plus zero turnover or same-source suspension with missing turnover; lifetime required; no quote multiplier is consumed",
        suspension_output="factor and q are NaN, segment=-1, known=false; internal actual anchor is retained",
        unknown="missing/invalid volume, inconsistent amount, invalid close or unknown denominator breaks continuity",
        anchor="q=1 starts a new comparable segment; its daily factor remains NaN and known=false",
        zero="actual close=0 remains real data and makes the next denominator unusable; no earlier positive close is substituted",
        retry="same last date and identical raw inputs reuse the published row; revisions or older dates require a new version and replay",
        raw_fields="read-only; returns and Indicator observation clocks are unchanged",
    )


@dataclass(frozen=True)
class AdjustmentResult:
    factor: np.ndarray
    q: np.ndarray
    segment: np.ndarray
    known: np.ndarray
    state: object


def derive_adjustment(close, previous, volume, turnover, member=None, suspended=None,
                      *, dates, state=None):
    """Return owned readonly matrices plus an isolated next-state capsule."""
    import _factorpool_market
    arrays = [np.asarray(value, dtype=np.float64)
              for value in (close, previous, volume, turnover)]
    shape = arrays[0].shape
    if len(shape) != 2 or any(value.shape != shape for value in arrays[1:]):
        raise ValueError("Adjustment inputs require matching date-by-symbol matrices")
    arrays.extend((np.ones(shape) if member is None else np.asarray(member, dtype=np.float64),
                   np.zeros(shape) if suspended is None else np.asarray(suspended, dtype=np.float64)))
    if any(value.shape != shape for value in arrays[4:]):
        raise ValueError("Adjustment status matrix axes differ")
    day = np.asarray(dates)
    if day.ndim != 1 or len(day) != shape[0] or (day.size and day.dtype.kind not in 'iu'):
        raise ValueError("Adjustment dates require one integer key per row")
    if day.dtype.kind == 'u' and day.size and np.any(day > np.iinfo(np.int64).max):
        raise ValueError("Adjustment date key exceeds int64")
    result = _factorpool_market.adjustment(np.ascontiguousarray(day, dtype=np.int64),
        *(np.ascontiguousarray(value) for value in arrays), state)
    return AdjustmentResult(
        *(np.frombuffer(result[name], dtype=dtype).reshape(shape)
          for name, dtype in (('factor', np.float64), ('q', np.float64),
                              ('segment', np.int64), ('known', np.bool_))),
        result['state'])


def adjust_values(row, q_history, q_basis, segment_history, segment_basis, *, inverse=False):
    """Price multiplication or inverse volume scaling on a comparable Q segment."""
    import _factorpool_market
    arrays = [np.asarray(value, dtype=dtype)
              for value, dtype in ((row, np.float64), (q_history, np.float64),
                                   (q_basis, np.float64), (segment_history, np.int64),
                                   (segment_basis, np.int64))]
    shape = arrays[0].shape
    if len(shape) != 1 or any(value.shape != shape for value in arrays[1:]):
        raise ValueError("Adjustment rows require matching one-dimensional symbol axes")
    result = _factorpool_market.adjust_values(
        *(np.ascontiguousarray(value) for value in arrays), bool(inverse))
    return np.frombuffer(result, dtype=np.float64)
