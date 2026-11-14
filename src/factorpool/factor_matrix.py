"""Rebalance-day factors over basic quotes; daily valuation only uses R."""
import json
from datetime import datetime
import numpy as np

from .adapter import IndicatorAdapter, indicator_fields
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
            fields = FIELDS
            self._present = None
        else:
            self.symbols, self.values = quotes['symbols'], quotes['values']
            fields = tuple(quotes.get('fields', FIELDS))
            self._present = quotes.get('present')
        self.field_positions = {field: n for n, field in enumerate(fields)}
        if (self.values.ndim != 3 or self.values.shape[0] != len(fields)
                or self.values.shape[1] < len(self.frames) or self.values.shape[2] != len(self.symbols)):
            raise ValueError('Historical quote matrices differ from their axes')
        if self._present is not None and self._present.shape != self.values.shape[1:]:
            raise ValueError('Historical presence matrix differs from its axes')
        if fields != FIELDS and self._present is None:
            raise ValueError('Projected quote matrices require an explicit bar-presence mask')
        self.values = self.values[:, :len(self.frames)]
        if self._present is not None:
            self._present = self._present[:len(self.frames)]
        self.columns = {s: n for n, s in enumerate(self.symbols)}
        self._axis = tuple(self.symbols)
        self._axis_values = np.asarray(self._axis, dtype=object)
        self._axis_values.setflags(write=False)
        self._full_columns = np.arange(len(self.symbols), dtype=np.intp)
        self._full_columns.setflags(write=False)
        self._selected_columns = self._selected_symbols = None
        irs = [indicator._ir] if indicator._ir is not None else []
        if indicator._batch_ir is not None:
            irs += [indicator._batch_ir, *indicator._batch_ir.temporals]
        nodes = [node for ir in irs for node in ir.nodes]
        self.fields = set(indicator_fields(indicator))
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
        self._stored_adjustment = None if quotes is None else quotes.get('adjustment')
        self._fixture_input = payload.get('provider') == 'fixture'
        self._source_member = None if quotes is None else quotes.get('source_member')
        self._adapter, self._last = None, -1
        self._matrix_plan = self._matrix_adapter = None
        self.evaluations = self.input_frames = 0

    def _prepare_adjustment(self):
        if self._adjustment is not None:
            return
        shape = self.values.shape[1:]
        if self._stored_adjustment is not None:
            q = np.asarray(self._stored_adjustment['q'])
            segments = np.asarray(self._stored_adjustment['segment'])
            if q.shape != shape or segments.shape != shape or q.dtype != np.float64 or segments.dtype != np.int64:
                raise ValueError('Stored adjustment matrices differ from the historical quote axes or dtypes')
            self._adjustment, self._segments = q.view(), segments.view()
            self._adjustment.setflags(write=False)
            self._segments.setflags(write=False)
            return
        if not self._fixture_input:
            raise ValueError('Market factor inputs require persisted Q and segment matrices')
        from .market_adjustment import derive_adjustment
        close, previous, volume, turnover = (self.values[self.field_positions[field]]
            for field in ('close', 'prev_close', 'volume', 'total_turnover'))
        member = self._source_member
        if member is None and 'member' in self.field_positions:
            member = self.values[self.field_positions['member']]
        suspended = (self.values[self.field_positions['is_suspended']]
                     if 'is_suspended' in self.field_positions else None)
        result = derive_adjustment(close, previous, volume, turnover, member, suspended,
            dates=np.fromiter((int(frame['date'].replace('-', '')) for frame in self.frames),
                              dtype=np.int64, count=len(self.frames)))
        self._adjustment, self._segments = result.q, result.segment

    def _frame(self, t, basis, *, as_columns=False):
        original = self.frames[t]
        values = self.values[:, t]
        present = (self._present[t] if self._present is not None else
                   np.any(np.isfinite(values[[self.field_positions[field] for field in FIELDS[:7]
                                              if field in self.field_positions]]), axis=0))
        supplied = original.get('symbol_columns')
        if supplied is not None:
            columns = np.asarray(supplied, dtype=np.intp)
            if (self._selected_columns is not None
                    and np.array_equal(columns, self._selected_columns)):
                selected = self._selected_symbols
            else:
                if (columns.ndim != 1 or np.any(columns < 0) or np.any(columns >= len(self.symbols))
                        or np.any(columns[1:] <= columns[:-1])):
                    raise ValueError('Frame symbol columns must be ordered unique quote positions')
                selected = (self._axis if len(columns) == len(self._axis)
                            else tuple(self._axis_values[columns].tolist()))
                # Keep an owned snapshot: a caller may reuse and mutate its
                # column array between frames without invalidating our cache.
                self._selected_columns = columns.copy()
                self._selected_columns.setflags(write=False)
                self._selected_symbols = selected
        elif original['symbols'] is self.symbols or (
                isinstance(original['symbols'], (list, tuple)) and tuple(original['symbols']) == self._axis):
            selected, columns = self._axis, self._full_columns
        else:
            selected = tuple(sorted(s for s in original['symbols'] if s in self.columns))
            columns = np.fromiter((self.columns[s] for s in selected), dtype=np.intp, count=len(selected))
        full_axis = len(columns) == len(self.symbols)
        take = slice(None) if full_axis else columns
        adjust = False
        if self.adjusted and t != basis:
            self._prepare_adjustment()
            adjust = True
        fields = sorted(self.fields)
        arrays = []
        for field in fields:
            source = 'close' if field == 'value' else field
            if source not in self.field_positions:
                raise ValueError(f'Provider has no point-in-time field: {field}')
            row = values[self.field_positions[source]]
            if adjust and (field in PRICE_FIELDS or field == 'volume'):
                from .market_adjustment import adjust_values
                row = adjust_values(row, self._adjustment[t], self._adjustment[basis],
                                    self._segments[t], self._segments[basis],
                                    inverse=field == 'volume')
            column = np.asarray(row[take], dtype=np.float64)
            if not np.all(np.isfinite(column)):
                # Quote matrices are immutable. Normalize infinities in a new
                # column, never mutate a borrowed raw market buffer.
                column = np.where(np.isfinite(column), column, np.nan)
            arrays.append(column)
        self.input_frames += 1
        if as_columns:
            return dict(original, symbols=selected, columns=dict(zip(fields, arrays)),
                        present=present[take], symbol_columns=columns, rows={})
        cells = np.asarray(arrays).T if arrays else np.empty((len(columns), 0))
        cells = np.where(np.isfinite(cells), cells, None).tolist()
        rows = {s: dict(zip(fields, cell)) for s, n, cell in zip(selected, columns, cells)
                if present[n]}
        return dict(original, rows=rows)

    def _input(self, t, basis):
        return self._frame(t, basis, as_columns=self.indicator._ir is not None)

    def evaluate_arrays(self, t):
        return self._evaluate(t, arrays=True)

    def evaluate_many(self, indices):
        """Native date loop; temporal basic-data plans retain one ordered state."""
        requested = np.asarray(indices)
        if (requested.ndim != 1 or (requested.size and requested.dtype.kind not in 'iu')
                or np.any(requested < 0) or np.any(requested >= len(self.frames))
                or np.any(requested[1:] <= requested[:-1])):
            raise ValueError('Factor indices must be strictly increasing integers within the history')
        requested = np.array(requested, dtype=np.intp, copy=True)
        requested.setflags(write=False)
        if self.indicator._ir is None:
            return None
        if self.indicator._batch_ir is not None and getattr(self.indicator, '_pool_ir', None) is None:
            return None
        shape = (len(requested), len(self.symbols))
        if not len(requested):
            return dict(indices=requested, symbols=self._axis, value=np.empty(shape),
                        valid=np.empty(shape, dtype=bool), ready=np.empty(shape, dtype=bool),
                        reason=np.empty(shape, dtype=np.uint8))
        if self._matrix_plan is None:
            adapter = IndicatorAdapter(self.indicator)
            graph = adapter.binding.graph_create(self.indicator._ir.to_dict(), ())
            adapter.graph, adapter.fields = graph, indicator_fields(self.indicator)
            self._matrix_adapter = adapter
            self._matrix_plan = adapter.binding, graph
        binding, graph = self._matrix_plan
        fields = {field: np.ascontiguousarray(self.values[self.field_positions[
                    'close' if field == 'value' else field]], dtype=np.float64)
                  for field in sorted(self.fields)}
        present = self._present
        if present is None:
            present = np.any(np.isfinite(self.values[[self.field_positions[field]
                for field in FIELDS[:7] if field in self.field_positions]]), axis=0)
        present = np.ascontiguousarray(present, dtype=bool)
        chronological = self.temporal
        history = range(int(requested[-1]) + 1) if chronological else requested
        axes, times = [], []
        for index in history:
            frame = self.frames[index]
            columns = frame.get('symbol_columns')
            if columns is None:
                selected = sorted(s for s in frame['symbols'] if s in self.columns)
                columns = np.fromiter((self.columns[s] for s in selected), dtype=np.intp,
                                      count=len(selected))
            axes.append(np.ascontiguousarray(columns, dtype=np.intp))
            times.append(int(datetime.fromisoformat(frame['date'] + 'T15:00:00').strftime('%Y%m%d%H%M%S')))
        asof = self.temporal and self.adjusted
        if asof:
            self._prepare_adjustment()
            state = binding.graph_evaluate_asof(graph, self._axis, fields, present,
                requested.tolist(), axes, times,
                np.ascontiguousarray(self._adjustment, dtype=np.float64),
                np.ascontiguousarray(self._segments, dtype=np.int64))
        else:
            state = binding.graph_evaluate_many(graph, self._axis, fields, present,
                                               requested.tolist(), axes, times, chronological)
        if state is None:
            return None
        result = dict(indices=requested, symbols=self._axis)
        for name, native_name, dtype in [('value', 'current', np.float64),
                                          ('valid', 'valid', np.bool_),
                                          ('ready', 'ready', np.bool_),
                                          ('reason', 'reason', np.uint8)]:
            result[name] = np.frombuffer(state[native_name], dtype=dtype).reshape(shape)
        self.evaluations += len(requested)
        if asof:
            self._cohort_stats = state['cohort_stats']
            self.input_frames += self._cohort_stats['graph_updates']
            # Cohorts belong to the batch's numeric worker threads. Ordinary
            # later calls rebuild their original streaming state, rather than
            # treating the suffix Program's prepared publications as raw state.
            self._last, self._adapter = -1, None
            return result
        self.input_frames += len(history)
        self._last, self._adapter = int(requested[-1]), None
        if chronological:
            columns = axes[-1]
            self._matrix_adapter.axis = (self._axis if len(columns) == len(self._axis)
                else tuple(self._axis_values[columns].tolist()))
            self._adapter = self._matrix_adapter
        return result

    def evaluate(self, t):
        return self._evaluate(t, arrays=False)

    def _evaluate(self, t, *, arrays):
        """Only called for a requested factor date (or an explicit decay input)."""
        self.evaluations += 1
        def update(adapter, day, basis, *, emit=True):
            frame = self._input(day, basis)
            method = adapter.update_arrays if arrays else adapter.update
            options = dict(emit=emit)
            options['fresh' if arrays else '_fresh'] = not self.temporal
            result = method(frame, self.factory, **options)
            if arrays and emit:
                columns = frame.get('symbol_columns')
                if columns is None:
                    columns = np.fromiter((self.columns[s] for s in result['symbols']),
                                          dtype=np.intp, count=len(result['symbols']))
                result['columns'] = columns
            return result
        if not self.temporal:
            # There is no numerical state in this plan. Reuse the configured
            # native graph on increasing dates instead of rebuilding it daily.
            if self._adapter is None or t <= self._last:
                self._adapter = IndicatorAdapter(self.indicator)
            result = update(self._adapter, t, t)
            self._last = t
            return result
        if not self.adjusted:
            if self._adapter is None:
                self._adapter = IndicatorAdapter(self.indicator)
            for day in range(self._last + 1, t):
                update(self._adapter, day, day, emit=False)
            out = update(self._adapter, t, t)
            self._last = t
            return out
        self._prepare_adjustment()
        previous = [self.columns[s] for s in self.frames[self._last]['symbols']
                    if s in self.columns] if self._last >= 0 else []
        if (self._adapter is not None
                and np.array_equal(self._adjustment[t, previous], self._adjustment[self._last, previous])
                and np.array_equal(self._segments[t, previous], self._segments[self._last, previous])):
            for day in range(self._last + 1, t):
                update(self._adapter, day, t, emit=False)
            out = update(self._adapter, t, t)
            self._last = t
            return out
        # Completed-session windows have an exact finite depth. A
        # valid-observation window may skip derived NaNs even when raw inputs
        # are finite; only a complete prefix proves its state after rebasing.
        start = max(0, t - self.window + 1) if self._bounded else 0
        adapter = IndicatorAdapter(self.indicator)
        for day in range(start, t):
            update(adapter, day, t, emit=False)
        out = update(adapter, t, t)
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
