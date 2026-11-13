"""Rebalance-day factors over basic quotes; daily valuation only uses R."""
import json
import numpy as np

from .adapter import IndicatorAdapter
from .data_client.contract import FIELDS
PRICE_FIELDS = frozenset(("open", "high", "low", "close", "value", "prev_close"))


def signal_indices(count, horizons):
    return {t for h in horizons for t in range(0, count, h)}


class FactorMatrix:
    def __init__(self, payload, indicator, factory, end):
        indicator._prepare()
        self.indicator, self.factory = indicator, factory
        self.frames = [frame for frame in payload['frames'] if frame['date'] <= end]
        quotes = payload.get('quotes')
        if quotes is None:
            self.symbols = sorted({s for frame in self.frames for s in frame['symbols']})
            self.values = np.asarray([
                [[frame['rows'].get(s, {}).get(field) for s in self.symbols]
                 for frame in self.frames] for field in FIELDS], dtype=float)
        else:
            self.symbols, self.values = quotes['symbols'], quotes['values']
        self.columns = {s: n for n, s in enumerate(self.symbols)}
        irs = [indicator._ir] if indicator._ir is not None else []
        if indicator._batch_ir is not None:
            irs += [indicator._batch_ir, *indicator._batch_ir.temporals]
        nodes = [node for ir in irs for node in ir.nodes]
        self.fields = set(indicator._ir.fields) if indicator._ir is not None else set()
        if indicator._batch_ir is not None:
            self.fields.update(indicator._batch_ir.fields)
            for port in indicator._batch_ir.temporals:
                self.fields.update(field for field in port.fields if field not in port.bindings)
        if indicator._ir is None:
            self.fields = set(FIELDS) - {'member', 'return'}
        self.adjusted = bool(self.fields & (PRICE_FIELDS | {'volume'}))
        self.window = max(1, indicator.warm_up_period)
        ops = {node.op for node in nodes}
        unbounded = {'ema', 'rsi', 'atr', 'brain_ts_hump',
                     'brain_ts_days_from_last_change', 'brain_ts_ts_step',
                     'brain_cs_trade_when'}
        self.unbounded = indicator._ir is None or bool(ops & unbounded)
        valid_clock = {'sma', 'sum', 'std', 'min', 'max', 'delay',
                       'decay_linear', 'roc', 'pct_change'}
        valid_observations = bool(ops & valid_clock) or any(
            node.op.startswith('brain_ts_')
            and json.loads(node.field).get('clock', 'trading_days') == 'valid_observations'
            for node in nodes)
        self.temporal = self.unbounded or any(
            op in valid_clock or op in {'temporal', 'brain_cs_group_backfill'}
            or op.startswith(('calendar_', 'brain_ts_')) for op in ops)
        self._bounded = not self.unbounded and not valid_observations
        self._adjustment = self._segments = None
        self._adapter, self._last = None, -1
        self.evaluations = self.input_frames = 0

    def _prepare_adjustment(self):
        if self._adjustment is not None:
            return
        close, previous, volume, turnover = (self.values[FIELDS.index(field)]
            for field in ('close', 'prev_close', 'volume', 'total_turnover'))
        shape = close.shape
        self._adjustment = np.ones(shape)
        self._segments = np.zeros(shape, dtype=np.int64)
        for t in range(1, len(close)):
            valid = (np.isfinite(close[t-1]) & (close[t-1] > 0)
                     & np.isfinite(close[t]) & (close[t] > 0)
                     & np.isfinite(previous[t]) & (previous[t] > 0)
                     & np.isfinite(volume[t-1]) & (volume[t-1] > 0)
                     & np.isfinite(volume[t]) & (volume[t] > 0))
            # Absent optional turnover is not a fabricated zero.
            for row in (turnover[t-1], turnover[t]):
                valid &= np.isnan(row) | (np.isfinite(row) & (row > 0))
            with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
                ratio = previous[t] / close[t-1]
                product = self._adjustment[t-1] * ratio
            valid &= np.isfinite(product) & (product > 0)
            self._adjustment[t] = np.where(valid, product, 1.)
            self._segments[t] = np.where(valid, self._segments[t-1], t)

    def _frame(self, t, basis, *, as_columns=False):
        original = self.frames[t]
        values = self.values[:, t]
        present = np.any(np.isfinite(values[:7]), axis=0)
        selected = sorted(s for s in original['symbols'] if s in self.columns)
        columns = [self.columns[s] for s in selected]
        scale = None
        if self.adjusted and t != basis:
            self._prepare_adjustment()
            with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
                scale = self._adjustment[basis] / self._adjustment[t]
            scale = np.where(self._segments[t] == self._segments[basis], scale, np.nan)
        fields = sorted(self.fields)
        arrays = []
        for field in fields:
            source = 'close' if field == 'value' else field
            if source not in FIELDS:
                raise ValueError(f'Provider has no point-in-time field: {field}')
            row = values[FIELDS.index(source)]
            if scale is not None:
                if field in PRICE_FIELDS:
                    row = row * scale
                elif field == 'volume':
                    row = row / scale
            column = np.asarray(row[columns], dtype=np.float64)
            column[~np.isfinite(column)] = np.nan
            arrays.append(column)
        self.input_frames += 1
        if as_columns:
            return dict(original, symbols=selected, columns=dict(zip(fields, arrays)),
                        present=present[columns], rows={})
        cells = np.asarray(arrays).T if arrays else np.empty((len(columns), 0))
        cells = np.where(np.isfinite(cells), cells, None).tolist()
        rows = {s: dict(zip(fields, cell)) for s, n, cell in zip(selected, columns, cells)
                if present[n]}
        return dict(original, rows=rows)

    def _input(self, t, basis):
        return self._frame(t, basis, as_columns=self.indicator._ir is not None)

    def evaluate(self, t):
        """Only called for a requested factor date (or an explicit decay input)."""
        self.evaluations += 1
        if not self.temporal:
            adapter = IndicatorAdapter(self.indicator)
            return adapter.update(self._input(t, t), self.factory)
        if not self.adjusted:
            if self._adapter is None:
                self._adapter = IndicatorAdapter(self.indicator)
            for day in range(self._last + 1, t):
                self._adapter.update(self._input(day, day), self.factory, emit=False)
            out = self._adapter.update(self._input(t, t), self.factory)
            self._last = t
            return out
        self._prepare_adjustment()
        previous = [self.columns[s] for s in self.frames[self._last]['symbols']
                    if s in self.columns] if self._last >= 0 else []
        if (self._adapter is not None
                and np.array_equal(self._adjustment[t, previous], self._adjustment[self._last, previous])
                and np.array_equal(self._segments[t, previous], self._segments[self._last, previous])):
            for day in range(self._last + 1, t):
                self._adapter.update(self._input(day, t), self.factory, emit=False)
            out = self._adapter.update(self._input(t, t), self.factory)
            self._last = t
            return out
        # Completed-session windows have an exact finite depth. A legacy
        # valid-observation window may skip derived NaNs even when raw inputs
        # are finite; only a complete prefix proves its state after rebasing.
        start = max(0, t - self.window + 1) if self._bounded else 0
        adapter = IndicatorAdapter(self.indicator)
        for day in range(start, t):
            adapter.update(self._input(day, t), self.factory, emit=False)
        out = adapter.update(self._input(t, t), self.factory)
        self._adapter, self._last = adapter, t
        return out

    @property
    def contract(self):
        return dict(mode='forward_adjusted', price_basis='rebalance session close',
                    price='raw historical price * Q[signal] / Q[history]',
                    volume='raw historical volume * Q[history] / Q[signal]',
                    other_fields='unchanged', returns='independent stored close returns R',
                    unknown_adjustment='separate comparable segments; no inferred multiplier',
                    factor_evaluations=self.evaluations, native_input_frames=self.input_frames)
