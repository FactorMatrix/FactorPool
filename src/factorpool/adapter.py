"""Pool input/axis adaptation; Indicator arithmetic stays in native programs."""
import math
import numpy as np
from datetime import datetime, timedelta
from factortrader import TradeBar
from .entry import PythonIndicator
from factortrader.binding.loading import _load_native_rows
import os
from .result_matrix import REASONS


def indicator_fields(indicator):
    """Required raw inputs, before loading a task's projected quote matrices."""
    indicator._prepare()
    if indicator._ir is None:
        return ("open", "high", "low", "close", "prev_close", "volume",
                "total_turnover", "is_st", "is_suspended")
    fields = list(indicator._ir.fields)
    if indicator._batch_ir is not None:
        fields.extend(indicator._batch_ir.fields)
        for port in indicator._batch_ir.temporals:
            fields.extend(field for field in port.fields if field not in port.bindings)
    return tuple(dict.fromkeys(fields))


def rows_from_arrays(state):
    """Materialize row dictionaries only for callers explicitly requesting rows."""
    return [dict(symbol=symbol, value=float(value) if valid else None,
                 valid=bool(valid), ready=bool(ready), reason=REASONS[int(reason)])
            for symbol, value, valid, ready, reason in zip(
                state['symbols'], state['value'], state['valid'], state['ready'], state['reason'])]


class IndicatorAdapter:
    def __init__(self, indicator):
        if not isinstance(indicator, PythonIndicator):
            raise TypeError("Use from factorpool import PythonIndicator for the FactorPool operator frontend")
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
        from .operator_adapter import select_operator_binding
        self.binding = select_operator_binding(indicator, self.binding)
        self.graph = None

    def update_arrays(self, frame, factory, *, emit=True, fresh=False):
        return self.update(frame, factory, emit=emit, _arrays=True, _fresh=fresh)

    def update(self, frame, factory, *, emit=True, _arrays=False, _fresh=False):
        supplied = frame["symbols"]
        ids = supplied if isinstance(supplied, tuple) else tuple(supplied)
        # A calculation axis usually spans many adjacent sessions unchanged.
        # Tuple equality is implemented in C and avoids per-symbol Python
        # ordering/duplicate callbacks on every published numerical frame.
        if ids != self.axis:
            ids = tuple(sorted(ids))
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate symbol in frame")
        stamp = datetime.fromisoformat(frame["date"] + "T15:00:00")
        rows = frame["rows"]
        if self.mode == "native":
            indicator = self.indicator
            if self.axis is None:
                self.graph = self.binding.graph_create(indicator._ir.to_dict(), ids)
                if indicator._batch_ir is not None:
                    self.binding.graph_configure_batch(self.graph, indicator._batch_ir.to_dict(), (), False)
                self.fields = indicator_fields(indicator)
                supported = {"open", "high", "low", "close", "prev_close", "value", "volume", "total_turnover", "is_st", "is_suspended"}
                missing = set(self.fields) - supported
                if missing:
                    raise ValueError(f"Provider has no point-in-time fields: {sorted(missing)}")
            matrix = "columns" in frame
            if matrix:
                if tuple(frame['symbols']) != ids:
                    raise ValueError("Matrix columns must follow the sorted symbol axis")
                columns = {field: np.asarray(frame['columns'][field], dtype=np.float64)
                           for field in self.fields}
                observed = np.asarray(frame['present'], dtype=bool)
                if observed.shape != (len(ids),) or any(v.shape != observed.shape for v in columns.values()):
                    raise ValueError("Matrix input columns do not match their symbol axis")
            else:
                columns = {field: [rows.get(s, {}).get(field, float("nan")) for s in ids] for field in self.fields}
                columns = {k: [float("nan") if x is None else x for x in v] for k, v in columns.items()}
                observed = np.array([s in rows for s in ids], dtype=bool)
            present = observed.copy()
            if indicator._batch_ir is None and getattr(indicator, "_pool_ir", None) is None:
                # A scalar shared Graph requires visible inputs to be finite.
                # Preserve the axis, but do not feed invalid observations into
                # its valid-observation clock. Pool operators keep column masks
                # separately so is_nan/if_else can inspect an observed NaN.
                for field in self.fields:
                    present &= np.isfinite(columns[field])
            binding = self.binding
            if self.axis != ids:
                binding.graph_set_symbols(self.graph, ids)
                self.axis = ids
            binding.graph_update(self.graph, int(stamp.strftime("%Y%m%d%H%M%S")), columns, present, _fresh)
            if not emit:
                return None
            state = binding.graph_snapshot_columns(self.graph)
            # One native snapshot/column transfer per time frame.
            arrays = {name: np.frombuffer(binding.native_vector_export(state[name]),
                      dtype="float64" if name == "current" else "bool")
                      for name in ("current", "valid", "ready")}
            current, valid, ready = (arrays[name] for name in ('current', 'valid', 'ready'))
            if any(array.shape != observed.shape for array in arrays.values()):
                raise ValueError("Native publication does not match its symbol axis")
            usable = valid & ready & np.isfinite(current) & observed
            source_missing = np.zeros(len(ids), dtype=bool)
            for column in columns.values():
                source_missing |= ~np.isfinite(column)
            reason = np.full(len(ids), 4, dtype=np.uint8)
            reason[valid & ~ready] = 3
            reason[source_missing] = 2
            reason[~observed] = 1
            reason[usable] = 0
            out = dict(symbols=ids, value=np.where(usable, current, np.nan), valid=usable,
                       ready=ready, reason=reason)
            return out if _arrays else rows_from_arrays(out)
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
                for field in ("prev_close", "total_turnover", "is_st", "is_suspended"):
                    if field in row:
                        setattr(bar, field, row[field])
                slot.update(bar)
                usable = slot._wrapped_is_ready() and math.isfinite(slot.current.value)
            if emit:
                out.append(dict(symbol=symbol, value=slot.current.value if usable else None,
                                valid=usable, ready=usable, reason="ok" if usable else "missing_bar" if row is None else "unready_or_invalid"))
        if _arrays and emit:
            return dict(symbols=ids,
                        value=np.array([np.nan if row['value'] is None else row['value'] for row in out]),
                        valid=np.array([row['valid'] for row in out], dtype=bool),
                        ready=np.array([row['ready'] for row in out], dtype=bool),
                        reason=np.array([REASONS.index(row['reason']) for row in out], dtype=np.uint8))
        return out
