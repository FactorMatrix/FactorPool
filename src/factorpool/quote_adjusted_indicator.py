"""Quote-only price-basis orchestration over existing native Indicator math."""
from .adapter import IndicatorAdapter
from .quote_adjustment import QuoteAdjustment, PRICE_FIELDS


REPLAY_CELL_LIMIT = 20_000_000
_WINDOWS = frozenset(("sma", "sum", "std", "min", "max", "delay", "decay_linear"))
_FINITE_PRICE_NODES = _WINDOWS | {"input", "constant", "add", "sub", "mul", "div", "ready"}
_RECURSIVE = frozenset(("ema", "rsi", "atr"))
_FROZEN_CS = frozenset(("rank", "rank_unit", "zscore", "normalize", "scale", "scale_down",
                        "group_neutralize", "group_rank", "group_sum", "group_count", "group_min",
                        "group_max", "group_std", "group_median"))


def _fields(ir):
    return {node.field for node in ir.nodes if node.op == "input"}


def _price_history(ir):
    return any(fields & PRICE_FIELDS for fields in _history_dependencies(ir))


def _history_dependencies(ir):
    fields, histories = [], []
    for node in ir.nodes:
        source = set().union(*(fields[index] for index in node.inputs)) if node.inputs else set()
        if node.op == "input":
            source.add(node.field)
        if node.op in _WINDOWS | _RECURSIVE | {"roc", "pct_change"}:
            histories.append(source)
        fields.append(source)
    return histories


def _unfrozen_fields(batch):
    # Synthetic temporal bindings can hide `ranked + close`. Follow typed IR
    # dependencies, stopping at actual sealed cross-section snapshots.
    fields = []
    for node in batch.nodes:
        if node.op in _FROZEN_CS or node.op == "dependency":
            value = set()
        elif node.op == "input":
            value = {node.field}
        elif node.op == "temporal":
            port = batch.temporals[node.dependency]
            value = set() if port.deferred else _fields(port)
        else:
            value = set().union(*(fields[index] for index in node.inputs)) if node.inputs else set()
        fields.append(value)
    return fields


def _plan(indicator):
    if getattr(indicator, "_pool_ir", None) is not None:
        raise ValueError("quote_adjusted does not yet support Pool extension/calendar IR; use raw factor prices for this expression")
    if indicator._ir is None:
        raise ValueError("quote_adjusted requires compiled native Indicator IR; Python/custom state is unsupported")
    if indicator._batch_dependencies:
        raise ValueError("quote_adjusted does not support external Indicator dependencies")
    batch = indicator._batch_ir
    if batch is None:
        histories, deferred, mode = [indicator._ir], [], "finite_price_ts"
    else:
        histories = [port for port in batch.temporals if not port.deferred]
        deferred = [port for port in batch.temporals if port.deferred]
        mode = "price_ts_then_final_cs"
        for port in deferred:
            if (_fields(port) - set(port.bindings)) & PRICE_FIELDS:
                raise ValueError("quote_adjusted cannot rebase raw prices inside a CS-bound historical window")
        raw = _unfrozen_fields(batch)
        for node in batch.nodes:
            if node.op == "temporal" and batch.temporals[node.dependency].deferred:
                if any(raw[index] & PRICE_FIELDS for index in node.inputs):
                    raise ValueError("quote_adjusted cannot mix raw historical prices with frozen CS snapshots")
    needs_replay = any(_price_history(ir) for ir in histories)
    if not needs_replay:
        return ("frozen_cs_history" if deferred else "current_or_unaffected_fields"), False
    if deferred:
        raise ValueError("quote_adjusted price TS -> CS -> TS is not yet supported; historical CS must remain frozen")
    for ir in histories:
        if any(not fields & PRICE_FIELDS for fields in _history_dependencies(ir)):
            raise ValueError("quote_adjusted cannot reset price history while preserving independent nonprice temporal ports or constant-only window state")
        unsupported = {node.op for node in ir.nodes} - _FINITE_PRICE_NODES
        if unsupported:
            raise ValueError(f"quote_adjusted supports finite price windows; recursive/stateful/conditional nodes unsupported: {sorted(unsupported)}")
        if _fields(ir) - PRICE_FIELDS:
            raise ValueError("quote_adjusted cannot selectively reset price windows while preserving independent nonprice temporal ports; use raw factor prices for mixed history")
    return mode, True


def _homogeneous_degree(ir):
    """Prove a small scalar subset; None means use ordinary native replay.

    Only price windows and exact zero/sign operations are accepted. Real-number
    degree arithmetic alone does not preserve IEEE validity: a normalized
    difference denominator can round to zero. General arithmetic therefore
    stays on the reference replay path, even when algebraically homogeneous.
    """
    degrees = []
    zero = object()
    for node in ir.nodes:
        args = [degrees[index] for index in node.inputs]
        if node.op == "input":
            if node.field not in PRICE_FIELDS:
                return None
            degree = 1
        elif node.op == "constant":
            degree = zero if node.value == 0 else 0
        elif node.op == "ready":
            degree = 0
        elif node.op in _WINDOWS:
            degree = args[0]
        elif node.op in ("add", "sub"):
            left, right = args
            if left is zero:
                degree = right
            elif right is zero:
                degree = left
            else:
                return None
        else:
            return None
        if degree is not zero and degree not in (0, 1):
            return None
        degrees.append(degree)
    # Readiness itself must be independent of price units.
    if degrees[ir.ready] not in (zero, 0):
        return None
    return 0 if degrees[ir.output] is zero else degrees[ir.output]


class QuoteAdjustedIndicatorAdapter:
    def __init__(self, indicator, *, replay_cell_limit=REPLAY_CELL_LIMIT):
        if type(replay_cell_limit) is not int or replay_cell_limit <= 0:
            raise ValueError("replay_cell_limit must be a positive integer")
        indicator._prepare()
        self.execution_mode, self._needs_replay = _plan(indicator)
        self._degree = (_homogeneous_degree(indicator._ir)
                        if self._needs_replay and indicator._batch_ir is None else None)
        if self._degree is not None:
            self.execution_mode = "homogeneous_scalar_incremental"
        self._indicator = indicator
        self._adapter = IndicatorAdapter(indicator)
        self.mode = self._adapter.mode
        self.quotes = QuoteAdjustment()
        self._frames = []  # References to already loaded immutable raw frames.
        self.replay_count = self.replayed_frames = self.replayed_cells = 0
        self.window_reset_count = 0
        self.axis_reset_batches = 0
        self.replay_cell_limit = replay_cell_limit

    @property
    def contract(self):
        return dict(self.quotes.contract, execution_mode=self.execution_mode,
                    price_basis="current allowed signal session close; no extra delay shift",
                    history_policy="finite price windows use current known quote segment; published factor and CS snapshots stay frozen",
                    input_ownership="borrows immutable loaded raw frames; temporary views use copy-on-write",
                    replay_policy="proved homogeneous scalar programs incrementally consume raw/Q and restore output units; other finite programs replay only on non-unit bridges; unknown bridges reset affected symbols without historical replay",
                    homogeneous_output_degree=self._degree, axis_reset_batches=self.axis_reset_batches,
                    window_reset_count=self.window_reset_count,
                    replay_count=self.replay_count, replayed_frames=self.replayed_frames,
                    replayed_cells=self.replayed_cells, replay_cell_limit=self.replay_cell_limit)

    @property
    def graph(self):
        return self._adapter.graph

    @property
    def binding(self):
        return self._adapter.binding

    def update(self, frame, factory):
        changes, resets = self.quotes.advance(frame)
        replay = self._needs_replay and self._degree is None and bool(changes) and bool(self._frames)
        if replay:
            cells = sum(len(item["symbols"]) for item in self._frames) + len(frame["symbols"])
            if self.replayed_cells + cells > self.replay_cell_limit:
                raise ValueError("quote_adjusted replay exceeds task cell budget; reduce universe/date range or use raw factor prices")
            adapter = IndicatorAdapter(self._indicator)
            for index, previous in enumerate(self._frames):
                adapter.update(self.quotes.view(previous, index), factory)
            output = adapter.update(frame, factory)  # Baseline/current bar remains raw.
            self._adapter = adapter
            self.replay_count += 1
            self.replayed_frames += len(self._frames) + 1
            self.replayed_cells += cells
        else:
            if self._needs_replay and resets and self._adapter.graph is not None:
                # Existing dynamic-axis semantics release only these symbols'
                # finite states, while all other native windows stay intact.
                ids = sorted(frame["symbols"])
                self.binding.graph_set_symbols(self.graph, [symbol for symbol in ids if symbol not in resets])
                self.binding.graph_set_symbols(self.graph, ids)
                self.axis_reset_batches += 1
            source = self.quotes.normalized_view(frame) if self._degree is not None else frame
            output = self._adapter.update(source, factory)
            if self._degree is not None:
                for row in output:
                    row["value"] = self.quotes.restore(row["value"], row["symbol"], self._degree)
        if self._needs_replay:
            self.window_reset_count += len(resets)
        self._frames.append(frame)
        return output
