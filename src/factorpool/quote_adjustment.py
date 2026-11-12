"""Causal factor-price views derived only from adjacent basic quotes.

No corporate-action or vendor adjustment-factor data is read. An unknown
quote bridge starts a new comparable price segment; it is never replaced by 1.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
import math
from numbers import Real


CONTRACT = "factorpool-quote-adjustment-v1"
PRICE_FIELDS = frozenset(("open", "high", "low", "close", "value", "prev_close"))


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isfinite(value) and value > 0
    except (OverflowError, ValueError):
        return False


def validate_frame(frame):
    day = frame.get("date")
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise ValueError("Quote-adjusted frame.date must be an ISO date") from None
    if len(frame["symbols"]) != len(set(frame["symbols"])):
        raise ValueError("Duplicate symbol in frame")
    if "session_index" in frame and (type(frame["session_index"]) is not int or frame["session_index"] < 0):
        raise ValueError("Quote-adjusted session_index must be a nonnegative integer")


def quote_bridge(previous, current, symbol):
    """Return (q, reason); q=None is a genuinely unknown bridge."""
    before, after = previous.get("session_index"), current.get("session_index")
    if before is not None or after is not None:
        if before is None or after is None:
            return None, "session_index_missing"
        if after != before + 1:
            return None, "session_gap"
    left, right = previous["rows"].get(symbol), current["rows"].get(symbol)
    if left is None or right is None:
        return None, "missing_bar"
    if not _positive(left.get("close")) or not _positive(right.get("close")):
        return None, "invalid_close"
    if not _positive(right.get("prev_close")):
        return None, "invalid_prev_close"
    if not _positive(left.get("volume")) or not _positive(right.get("volume")):
        return None, "nonpositive_or_unknown_volume"
    if any("total_turnover" in row and not _positive(row["total_turnover"]) for row in (left, right)):
        return None, "nonpositive_or_unknown_turnover"
    ratio = float(right["prev_close"]) / float(left["close"])
    return (ratio, "known") if _positive(ratio) else (None, "unrepresentable_bridge")


@dataclass
class _Segment:
    start: int
    indices: list = field(default_factory=list)
    products: list = field(default_factory=list)

    @property
    def current(self):
        return self.products[-1] if self.products else 1.0

    def scale(self, historical_index):
        slot = bisect_right(self.indices, historical_index)
        old = self.products[slot-1] if slot else 1.0
        value = self.current / old
        if not _positive(value):
            raise ValueError("Quote-adjusted historical multiplier is not representable")
        return value


class QuoteAdjustment:
    """Small per-symbol bridge state; raw task frames stay caller-owned."""
    def __init__(self):
        self._previous = None
        self._segments = {}
        self.position = -1
        self.bridge_count = self.non_unit_bridge_count = self.unknown_bridge_count = 0
        self.reset_reasons = {}
        self.reset_examples = []
        self._indexed = self._unindexed = False

    def advance(self, frame):
        validate_frame(frame)
        previous = self._previous
        if previous is not None:
            if frame["date"] <= previous["date"]:
                raise ValueError("Quote-adjusted dates must be unique and strictly increasing")
            if ("session_index" in previous and "session_index" in frame
                    and frame["session_index"] <= previous["session_index"]):
                raise ValueError("Quote-adjusted session_index must be strictly increasing")
        self.position += 1
        self._indexed |= "session_index" in frame
        self._unindexed |= "session_index" not in frame
        resets, changes = {}, {}
        retained = {}
        for symbol in frame["symbols"]:
            segment = self._segments.get(symbol)
            if segment is None:
                retained[symbol] = _Segment(self.position)
                continue
            ratio, reason = quote_bridge(previous, frame, symbol)
            if ratio is not None and not _positive(segment.current * ratio):
                ratio, reason = None, "unrepresentable_bridge_product"
            if ratio is None:
                retained[symbol] = _Segment(self.position)
                resets[symbol] = reason
                self.unknown_bridge_count += 1
                self.reset_reasons[reason] = self.reset_reasons.get(reason, 0) + 1
                if len(self.reset_examples) < 100:
                    self.reset_examples.append(dict(date=frame["date"], symbol=symbol, reason=reason))
            else:
                self.bridge_count += 1
                retained[symbol] = segment
                if ratio != 1:
                    segment.indices.append(self.position)
                    segment.products.append(segment.current * ratio)
                    changes[symbol] = ratio
                    self.non_unit_bridge_count += 1
        self._segments = retained
        self._previous = frame
        return changes, resets

    def view(self, frame, historical_index):
        """Transient current-basis view; only the current known segment counts."""
        if historical_index > self.position:
            raise ValueError("A future quote cannot enter a historical factor view")
        symbols, rows = [], dict(frame["rows"])
        for symbol in frame["symbols"]:
            segment = self._segments.get(symbol)
            if segment is None or historical_index < segment.start:
                continue
            symbols.append(symbol)
            original = frame["rows"].get(symbol)
            if original is None:
                continue
            scale = segment.scale(historical_index)
            if scale == 1:
                continue
            row = dict(original)
            for key in PRICE_FIELDS:
                value = row.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise ValueError(f"Quote-adjusted {key} must be numeric or missing")
                adjusted = value * scale
                if math.isfinite(value) and not math.isfinite(adjusted):
                    raise ValueError(f"Quote-adjusted {key} is not representable")
                if value != 0 and adjusted == 0:
                    raise ValueError(f"Quote-adjusted {key} underflows; cannot publish a false zero")
                row[key] = adjusted
            rows[symbol] = row
        return dict(frame, symbols=symbols, rows=rows)

    def normalized_view(self, frame):
        """One current raw/Q input for proved homogeneous scalar programs."""
        rows = dict(frame["rows"])
        for symbol in frame["symbols"]:
            original = frame["rows"].get(symbol)
            if original is None:
                continue
            product = self._segments[symbol].current
            if product == 1:
                continue
            row = dict(original)
            for key in PRICE_FIELDS:
                value = row.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise ValueError(f"Quote-adjusted {key} must be numeric or missing")
                normalized = value / product
                if math.isfinite(value) and not math.isfinite(normalized):
                    raise ValueError(f"Quote-adjusted normalized {key} overflows")
                if value != 0 and normalized == 0:
                    raise ValueError(f"Quote-adjusted normalized {key} underflows; cannot publish a false zero")
                row[key] = normalized
            rows[symbol] = row
        return dict(frame, rows=rows)

    def restore(self, value, symbol, degree):
        if degree == 0 or value is None:
            return value
        restored = value * self._segments[symbol].current
        if not math.isfinite(restored):
            raise ValueError("Quote-adjusted restored output overflows")
        if value != 0 and restored == 0:
            raise ValueError("Quote-adjusted restored output underflows; cannot publish a false zero")
        return restored

    @property
    def contract(self):
        axis = ("mixed indices: missing-index boundaries are unknown" if self._indexed and self._unindexed else
                "snapshot session_index" if self._indexed else "caller-declared complete update sequence")
        return dict(version=CONTRACT, mode="quote_adjusted",
                    source="basic close, prev_close, volume and optional total_turnover quotes only",
                    bridge_formula="q[t] = raw_prev_close[t] / raw_close[t-1] for adjacent known trading sessions",
                    history_formula="raw_price[s] * Q[b] / Q[s] within one known segment; Q is cumulative q",
                    price_fields=sorted(PRICE_FIELDS), current_bar="unchanged raw quote at the signal close",
                    session_axis=axis,
                    bridge_eligibility="both bars, both closes and current prev_close positive finite; both volumes positive finite; turnover positive finite when present",
                    unknown_bridge="start a new comparable segment; affected finite price windows rewarm; no assumed 1, no cross-gap stitching",
                    volume="raw, never adjusted", total_turnover="raw, never adjusted",
                    returns="independent stored raw daily R; no recomputation or zero-volume policy change",
                    interpretation="quote-implied price bridge only; cannot identify or separate splits, dividends, corrections or other causes",
                    bridge_count=self.bridge_count, non_unit_bridge_count=self.non_unit_bridge_count,
                    unknown_bridge_count=self.unknown_bridge_count,
                    reset_reasons=dict(self.reset_reasons), reset_examples=[dict(row) for row in self.reset_examples],
                    reset_examples_truncated=self.unknown_bridge_count > len(self.reset_examples))
