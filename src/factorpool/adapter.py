"""Only binding, axis and result adaptation. No indicator arithmetic."""
import math
import numpy as np
from datetime import datetime, timedelta
from factortrader import PythonIndicator, TradeBar
from factortrader.binding.loading import _load_native_rows
import os


class IndicatorAdapter:
    def __init__(self, indicator):
        if not isinstance(indicator, PythonIndicator):
            raise TypeError("Factor must inherit factortrader.PythonIndicator")
        self.indicator = indicator
        indicator._prepare()
        if indicator._batch_dependencies:
            raise ValueError("Research factors must be self-contained; external Indicator dependencies are not yet supported")
        self.mode = "native" if indicator._ir is not None else "python"
        self.fields = ()
        self.slots = {}
        self.axis = None
        self.params = None
        self.binding = _load_native_rows(os.environ["FACTORTRADER_NATIVE_LIBRARY"])
        if self.binding is None:
            raise RuntimeError("Matching native Indicator binding is unavailable")
        self.graph = None

    def update(self, frame, factory):
        ids = tuple(sorted(frame["symbols"]))
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate symbol in frame")
        stamp = datetime.fromisoformat(frame["date"] + "T15:00:00")
        rows = frame["rows"]
        if self.mode == "native":
            indicator = self.indicator
            if self.axis is None:
                self.graph = self.binding.graph_create(indicator._ir.to_dict(), ids)
                fields = list(indicator._ir.fields)
                if indicator._batch_ir is not None:
                    self.binding.graph_configure_batch(self.graph, indicator._batch_ir.to_dict(), (), False)
                    fields.extend(indicator._batch_ir.fields)
                    for port in indicator._batch_ir.temporals:
                        fields.extend(field for field in port.fields if field not in port.bindings)
                self.fields = tuple(dict.fromkeys(fields))
                supported = {"open", "high", "low", "close", "value", "volume", "is_st", "is_suspended"}
                missing = set(self.fields) - supported
                if missing:
                    raise ValueError(f"Provider has no point-in-time fields: {sorted(missing)}")
            self.axis = ids
            columns = {field: [rows.get(s, {}).get(field, float("nan")) for s in ids] for field in self.fields}
            columns = {k: [float("nan") if x is None else x for x in v] for k, v in columns.items()}
            present = [s in rows for s in ids]
            binding = self.binding
            binding.graph_set_symbols(self.graph, ids)
            binding.graph_update(self.graph, int(stamp.strftime("%Y%m%d%H%M%S")), columns, present)
            state = binding.graph_snapshot(self.graph)
            # One native snapshot/column transfer per time frame.
            arrays = {name: np.frombuffer(binding.native_vector_export(state[name]), dtype="float64" if name == "current" else "bool").tolist() for name in ("current", "valid", "ready")}
            out = []
            for i, symbol in enumerate(state["symbols"]):
                value = arrays["current"][i]
                valid, ready = bool(arrays["valid"][i]), bool(arrays["ready"][i])
                usable = valid and ready and math.isfinite(value) and symbol in rows
                reason = ("ok" if usable else "missing_bar" if symbol not in rows else
                          "source_missing" if any(rows[symbol].get(f) is None for f in self.fields) else
                          "not_ready" if valid and not ready else "native_unavailable")
                out.append(dict(symbol=symbol, value=value if usable else None, valid=usable, ready=ready, reason=reason))
            return out
        # Explicit single-symbol Python callbacks; no .cs fallback is possible.
        self.slots = {s: self.slots[s] for s in ids if s in self.slots}
        out = []
        for symbol in ids:
            slot = self.slots.setdefault(symbol, None)
            if slot is None:
                slot = self.slots[symbol] = factory()
                slot._prepare()
                if slot._ir is not None:
                    raise ValueError("Execution mode changed across instances")
            row = rows.get(symbol)
            usable = False
            if row is not None:
                required = ("open", "high", "low", "close", "volume")
                if any(row.get(k) is None for k in required):
                    raise ValueError("Python TradeBar callback requires actual OHLCV fields")
                bar = TradeBar(stamp - timedelta(hours=5, minutes=30), symbol,
                               row["open"], row["high"], row["low"], row["close"], row["volume"],
                               timedelta(hours=5, minutes=30))
                slot.update(bar)
                usable = slot._wrapped_is_ready() and math.isfinite(slot.current.value)
            out.append(dict(symbol=symbol, value=slot.current.value if usable else None,
                            valid=usable, ready=usable, reason="ok" if usable else "missing_bar" if row is None else "unready_or_invalid"))
        return out
