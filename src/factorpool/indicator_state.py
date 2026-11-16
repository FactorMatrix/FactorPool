"""Read-only Indicator publications over Pool-owned native graphs.

No getter advances an input clock or replays history. Native snapshots are
exported only when a caller explicitly inspects a public Indicator property.
"""
from datetime import datetime
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
from factortrader import IndicatorDataPoint
from factortrader.indicator.vector import NativeVector


@lru_cache(maxsize=4)
def _native_module(path):
    from factortrader.binding.loading import _load_native_rows
    binding = _load_native_rows(path)
    if binding is None:
        raise RuntimeError("Matching native Indicator binding is unavailable")
    return binding


def native_binding():
    """Initialize the existing shared standalone bridge, without an Engine."""
    import os
    from factortrader.binding import engine
    path = os.environ.get("FACTORTRADER_NATIVE_LIBRARY")
    if not path:
        raise RuntimeError("Set FACTORTRADER_NATIVE_LIBRARY to the matching Pool SDK")
    binding = _native_module(path)
    existing = getattr(engine, "_INDICATOR_EXTENSION", None)
    if existing is None:
        engine._INDICATOR_EXTENSION = binding
    elif existing is not binding:
        previous = getattr(existing, "bridge_path", lambda: None)()
        current = getattr(binding, "bridge_path", lambda: None)()
        if previous != current:
            raise RuntimeError("A different native Indicator SDK is already active")
    return binding


def _stamp(value):
    if not value:
        return datetime.min
    return datetime.strptime(str(int(value)), "%Y%m%d%H%M%S")


class IndicatorState:
    """One adapter's actual publication; optional matrix snapshot is immutable."""
    def __init__(self, adapter):
        self.adapter = adapter
        self.publication = None
        self._snapshot = None

    def invalidate(self):
        self.publication = None
        self._snapshot = None

    def publish(self, snapshot):
        self.publication = snapshot
        self._snapshot = None

    def snapshot(self):
        if self._snapshot is not None:
            return self._snapshot
        adapter = self.adapter
        if self.publication is not None:
            result = self.publication
        elif adapter.mode == "native":
            if adapter.graph is None:
                result = {"symbols": adapter.axis or ()}
            else:
                result = adapter.binding.graph_snapshot(adapter.graph)
        else:
            symbols = adapter.axis or ()
            slots = [adapter.slots.get(symbol) for symbol in symbols]
            current = [slot.current.value if slot is not None else np.nan for slot in slots]
            ready = [slot._wrapped_is_ready() if slot is not None else False for slot in slots]
            result = {"symbols": symbols,
                      "current": [value if symbol in adapter.observed_rows else np.nan
                                  for symbol, value in zip(symbols, current)],
                      "publication_current": current,
                      "previous": [slot.previous.value if slot is not None else np.nan for slot in slots],
                      "ready": [value and symbol in adapter.observed_rows
                                for symbol, value in zip(symbols, ready)],
                      "publication_ready": ready,
                      "samples": [slot.samples if slot is not None else 0 for slot in slots],
                      "end_time": [int(slot.current.end_time.strftime("%Y%m%d%H%M%S"))
                                   if slot is not None and slot.samples else 0 for slot in slots],
                      "previous_end_time": [int(slot.previous.end_time.strftime("%Y%m%d%H%M%S"))
                                            if slot is not None and slot.previous.end_time != datetime.min else 0
                                            for slot in slots],
                      "state_scope": "streaming"}
        self._snapshot = result
        return result

    @property
    def symbols(self):
        return tuple(self.snapshot().get("symbols", self.adapter.axis or ()))

    def column(self, name, symbols=None, *, sticky=False):
        state = self.snapshot()
        key = {"current": "publication_current", "ready": "publication_ready"}.get(name) if sticky else None
        key = key if key in state else name
        boolean = name in ("ready", "valid")
        dtype = np.bool_ if boolean else np.int64 if name in (
            "samples", "end_time", "previous_end_time") else np.float64
        ids = self.symbols
        value = state.get(key)
        if value is None:
            if ids and self.adapter.graph is not None:
                raise RuntimeError(f"Native Indicator publication has no {name} state")
            values = np.zeros(len(ids), dtype=dtype)
            if name in ("current", "previous"):
                values.fill(np.nan)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            values = np.frombuffer(value, dtype=dtype)
        elif isinstance(value, (tuple, list, np.ndarray)):
            values = np.asarray(value, dtype=dtype)
        else:
            # Shared Graph numeric columns are opaque NativeVector handles.
            buffer = self.adapter.vector_binding.native_vector_export(value)
            values = np.frombuffer(buffer, dtype=np.bool_ if boolean else np.float64).astype(dtype, copy=False)
        if values.shape != (len(ids),):
            raise RuntimeError(f"Native Indicator {name} differs from its symbol axis")
        if symbols is None or tuple(symbols) == ids:
            return values
        positions = {symbol: index for index, symbol in enumerate(ids)}
        wanted = tuple(symbols)
        result = np.zeros(len(wanted), dtype=dtype)
        if name in ("current", "previous"):
            result.fill(np.nan)
        for index, symbol in enumerate(wanted):
            if symbol in positions:
                result[index] = values[positions[symbol]]
        return result

    def vector(self, name, symbols=None):
        ids = self.symbols if symbols is None else tuple(symbols)
        return NativeVector._from_array(self.column(name, ids), ids, self.adapter.vector_binding)

    def point(self, symbol, previous=False):
        name, time_name = ("previous", "previous_end_time") if previous else ("current", "end_time")
        return IndicatorDataPoint(_stamp(self.column(time_name, (symbol,), sticky=True)[0]),
                                  float(self.column(name, (symbol,), sticky=True)[0]), symbol)

    def batch_point(self, previous=False, symbols=None):
        name, time_name = ('previous', 'previous_end_time') if previous else ('current', 'end_time')
        stamp = self.snapshot().get('batch_time', 0) if not previous else 0
        if not stamp:
            times = self.column(time_name, symbols)
            stamp = int(times.max()) if len(times) else 0
        return SimpleNamespace(value=self.vector(name, symbols), end_time=_stamp(stamp))

    def reset(self):
        self.adapter.reset()


class IndicatorSymbolView:
    def __init__(self, owner, symbol):
        self.owner, self.symbol = owner, symbol

    @property
    def state(self):
        return self.owner._pool_state

    @property
    def current(self): return self.state.point(self.symbol)

    @property
    def previous(self): return self.state.point(self.symbol, True)

    @property
    def is_ready(self): return bool(self.state.column("ready", (self.symbol,), sticky=True)[0])

    @property
    def samples(self): return int(self.state.column("samples", (self.symbol,))[0])


class IndicatorBatchView:
    def __init__(self, owner, symbols):
        self.owner, self.symbols = owner, tuple(symbols)

    @property
    def current(self): return self.owner._pool_state.batch_point(symbols=self.symbols)

    @property
    def previous(self): return self.owner._pool_state.batch_point(previous=True, symbols=self.symbols)

    @property
    def is_ready(self): return self.owner._pool_state.vector("ready", self.symbols)

    @property
    def samples(self): return self.owner._pool_state.vector("samples", self.symbols)
