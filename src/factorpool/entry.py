"""FT-style class.run(config) facade over the shared Indicator and research core."""
from copy import deepcopy
import inspect
import dis
import json
from pathlib import Path
import tempfile
import os
from functools import wraps

from factortrader import PythonIndicator as SharedPythonIndicator
from .operator_frontend import DIRECT, DOMAINS, NAMESPACES, _native_only
from .indicator_state import IndicatorSymbolView, IndicatorBatchView
from factortrader import IndicatorDataPoint
from factortrader.indicator.python_indicator import _input_member, _input_time, _input_ticks

_MISSING_INPUT_FIELD = object()


class PythonIndicator(SharedPythonIndicator):
    ts = property(lambda self: NAMESPACES['ts'])
    cs = property(lambda self: NAMESPACES['cs'])
    vc = property(lambda self: NAMESPACES['vc'])
    gp = property(lambda self: NAMESPACES['gp'])
    tf = property(lambda self: NAMESPACES['tf'])
    abs = add = subtract = multiply = log = sqrt = min = max = _native_only
    sign = signed_power = is_nan = _native_only

    def __new__(cls, *args, **kwargs):
        value = super().__new__(cls, *args, **kwargs)
        if os.environ.get('FACTORTRADER_NATIVE_LIBRARY'):
            # Custom __init__ may create a standalone LEAN Consolidator. Load
            # the pinned SDK before user construction, without an Engine.
            from .indicator_state import native_binding
            native_binding()
        value._pool_state = None
        value._pool_managed = False
        value._pool_pending_current = None
        value._pool_resetting = False
        value._pool_reset_done = False
        value._pool_reset_callback = None
        value._pool_manual_adapter = None
        return value

    def __init_subclass__(cls, **kwargs):
        reset = cls.__dict__.get('reset', cls.__dict__.get('Reset'))
        super().__init_subclass__(**kwargs)
        if reset is not None:
            shared_reset = cls.reset
            @wraps(reset)
            def reset_wrapper(self):
                outer = not self._pool_resetting
                if outer:
                    self._pool_resetting, self._pool_reset_done = True, False
                try:
                    return shared_reset(self)
                finally:
                    if outer:
                        try:
                            self._reset_pool_state()
                        finally:
                            self._pool_resetting = False
            cls.reset = reset_wrapper
            if 'Reset' in cls.__dict__:
                cls.Reset = reset_wrapper
        pending=[inspect.getattr_static(cls,'_indicator_function',None)];seen=set()
        while pending:
            function=pending.pop()
            if isinstance(function,(staticmethod,classmethod)):function=function.__func__
            if isinstance(function,property):function=function.fget
            if not inspect.isfunction(function) or function in seen:continue
            seen.add(function);code=function.__code__
            instructions=tuple(dis.get_instructions(function))
            for left,right in zip(instructions,instructions[1:]):
                if (left.opname=='LOAD_FAST' and code.co_varnames and left.argval==code.co_varnames[0]
                        and right.opname in ('LOAD_ATTR','LOAD_METHOD')):
                    if right.argval in DOMAINS or right.argval in DIRECT:
                        cls._indicator_native_domains=True
                    else:pending.append(inspect.getattr_static(cls,right.argval,None))

    @property
    def current(self):
        if self._pool_pending_current is not None:
            return self._pool_pending_current
        state = self.__dict__.get('_pool_state')
        if state is None:
            return SharedPythonIndicator.current.fget(self)
        symbols = state.symbols
        return state.point(symbols[0]) if len(symbols) == 1 else state.batch_point()

    @current.setter
    def current(self, point):
        if not isinstance(point, IndicatorDataPoint):
            raise TypeError('Indicator.current must be an IndicatorDataPoint')
        if self.__dict__.get('_pool_state') is not None:
            raise ValueError('Native Indicator.current is a read-only graph publication')
        if self._consuming:
            self._pool_pending_current = point
        else:
            self._publish_point(point)

    @property
    def previous(self):
        state = self.__dict__.get('_pool_state')
        if state is None:
            return SharedPythonIndicator.previous.fget(self)
        symbols = state.symbols
        return state.point(symbols[0], True) if len(symbols) == 1 else state.batch_point(previous=True)

    @property
    def value(self):
        if self.__dict__.get('_pool_state') is not None:
            return self.current.value
        return self._value

    @value.setter
    def value(self, value):
        self._value = value

    @property
    def is_ready(self):
        state = self.__dict__.get('_pool_state')
        if state is None:
            return SharedPythonIndicator.is_ready.fget(self)
        return bool(state.column('ready', sticky=True)[0]) if len(state.symbols) == 1 else state.vector('ready')

    @property
    def samples(self):
        state = self.__dict__.get('_pool_state')
        if state is None:
            return SharedPythonIndicator.samples.fget(self)
        values = state.column('samples')
        return int(values[0]) if len(values) == 1 else state.vector('samples')

    @property
    def window(self):
        if self.__dict__.get('_pool_state') is not None:
            raise NotImplementedError('Pool research Indicator output Window and integer history '
                                      'are unavailable; current and previous expose actual publications')
        return SharedPythonIndicator.window.fget(self)

    def __getitem__(self, symbols):
        if self._pool_state is None or type(symbols) is int:
            return super().__getitem__(symbols)
        if isinstance(symbols, str):
            return IndicatorSymbolView(self, symbols)
        ids = tuple(symbols)
        if any(type(symbol) is not str for symbol in ids) or len(set(ids)) != len(ids):
            raise ValueError('Indicator selection requires unique symbol strings')
        return IndicatorBatchView(self, ids)

    def _accept_input(self, input):
        # LEAN IndicatorBase skips the same object and backwards time. Distinct
        # bars at the same EndTime are valid Consolidator observations.
        symbol = _input_member(input, 'symbol', 'Symbol', '')
        stamp = _input_time(input)
        previous = self._previous_inputs.get(symbol)
        if previous is not None and (input is previous or
                _input_ticks(input, stamp) < _input_ticks(previous, _input_time(previous))):
            return None
        self._previous_inputs[symbol] = input
        return stamp, symbol

    def _publish_point(self, point):
        if self._output_window is None:
            if self._output_samples >= 2:
                self._last_removed_output = self._previous
            self._previous, self._current = self._current, point
        else:
            self._output_window.add(point)
        self._output_samples += 1

    def _consume(self, function, input):
        if self._consuming:
            return function(self, input)
        if self._pool_managed:
            return self._consume_managed(function, input)
        if self._manual_native or (self._pool_state is not None and self._pool_state.adapter.mode == 'native'):
            return self._receive(input)
        # A direct Python override is LEAN's manual path: the user owns
        # current/on_updated. Research delivery uses the managed wrapper below.
        return function(self, input)

    def _consume_managed(self, function, input):
        accepted = self._accept_input(input)
        if accepted is None:
            return self._wrapped_is_ready()
        stamp, symbol = accepted
        self._samples += 1
        self._consuming = True
        try:
            readiness = function(self, input)
        finally:
            self._consuming = False
            self._pool_pending_current = None
        if readiness is not None and type(readiness) is not bool:
            raise TypeError('PythonIndicator.update must return bool or None')
        if readiness is None:
            readiness = self.is_ready
            if type(readiness) is not bool:
                raise TypeError('PythonIndicator.is_ready must be bool')
        self._ready = readiness
        point = IndicatorDataPoint(stamp, float(self.value), symbol)
        self._publish_point(point)
        self._last_input = input
        self.updated.fire(point)
        return self._wrapped_is_ready()

    def _receive(self, input):
        self._prepare()
        if self._ir is not None:
            if self._pool_state is not None and self._pool_state.adapter is not self._pool_manual_adapter:
                raise ValueError('Use the owning research adapter to update a bound Indicator')
            symbol = _input_member(input, 'symbol', 'Symbol', '')
            identity = str(symbol) or '__factorpool_scalar__'
            if self._pool_manual_adapter is not None and self._pool_manual_adapter.axis not in (None, (identity,)):
                raise ValueError('Manual native updates require one symbol; use a complete research frame for multiple symbols')
            accepted = self._accept_input(input)
            if accepted is None:
                return self.is_ready
            stamp, symbol = accepted
            from .adapter import IndicatorAdapter, indicator_fields
            if self._pool_manual_adapter is None:
                self._pool_manual_adapter = IndicatorAdapter(self)
            row = {}
            for name in indicator_fields(self):
                upper = ''.join(part.title() for part in name.split('_'))
                value = _input_member(input, name, upper, _MISSING_INPUT_FIELD)
                if value is _MISSING_INPUT_FIELD:
                    raise TypeError(f'Indicator input has no {name} field')
                row[name] = value
            frame = dict(date=stamp.date().isoformat(), end_time=stamp, symbols=(identity,), rows={identity: row})
            self._pool_manual_adapter.update_arrays(frame, lambda: self)
            return self.is_ready
        previous = self._pool_managed
        self._pool_managed = True
        try:
            return self.update(input)
        finally:
            self._pool_managed = previous

    def _deliver_consolidated(self, input):
        return self._receive(input)

    def on_updated(self, point):
        if not self._consuming:
            super().on_updated(point)

    def _reset_pool_state(self):
        if self._pool_reset_done:
            return
        self._pool_reset_done = True
        if self._pool_reset_callback is not None:
            self._pool_reset_callback()
        elif self._pool_state is not None:
            self._pool_state.reset()
        self._pool_pending_current = None

    def reset(self):
        if not self._pool_resetting:
            self._pool_reset_done = False
        try:
            self._reset_pool_state()
        finally:
            super().reset()

    def _prepare(self):
        if self._prepared:
            return self._ir
        from .operator_compiler import compile_pool_indicator
        prepared = compile_pool_indicator(self)
        if prepared is None:
            if self._manual_native:
                from factortrader.indicator.compiler import UnsupportedIndicator
                raise UnsupportedIndicator('Pool operator expressions require a supported native program')
            self._prepared = True
            self.execution_mode = 'python'
            self.native_failure_reason = 'Ordinary Python Indicator callback'
            return None
        if prepared[1] is None:
            self._ir = prepared[0]
            self._prepared = True
            self.execution_mode = 'native'
            self.native_failure_reason = None
            return self._ir
        from factortrader.indicator.ir import IndicatorIR, Node
        self._batch_ir, self._pool_ir = prepared
        self._batch_dependencies = ()
        self._has_scalar_stage = False
        self._ir = IndicatorIR((Node("constant", value=0), Node("constant", value=1)),
                               0, 1, self._batch_ir.warm_up_period)
        self._prepared = True
        self.execution_mode = "pool_native"
        self.native_failure_reason = None
        return self._ir

    Current = property(lambda self: self.current,
                       lambda self, point: setattr(self, 'current', point))

    def Reset(self):
        return PythonIndicator.reset(self) if self._resetting or self._pool_resetting else self.reset()

    @classmethod
    def run(cls, config=None):
        from .config import effective_config
        from .script import parse_source
        from .runner import run
        filename = inspect.getsourcefile(cls)
        if filename is None or not Path(filename).is_file():
            raise ValueError("Run the Indicator from a complete Python script file")
        source = Path(filename).read_text()
        parsed = parse_source(source)
        supplied = {} if config is None else config
        if parsed["factor_class"] != cls.__name__ or parsed["source_config"] != supplied:
            raise ValueError("run(config) must match this script's literal config and entry class")
        request = dict(name=cls.__name__, source_code=source, **parsed)
        if request["data"]["provider"] == "market_matrix":
            from .data_client import DataClient
            with DataClient() as client:
                snapshot = client.snapshot(request["data"].get("snapshot_id"))
            request["data"]["snapshot_id"] = snapshot["snapshot_id"]
        request["effective_config"] = effective_config(request)
        # The class is already loaded. Reuse it rather than reimporting the file
        # and accidentally evaluating the user's module a second time.
        with tempfile.TemporaryDirectory(prefix="factorpool-research-") as directory:
            root = Path(directory)
            (root / "request.json").write_text(json.dumps(request, ensure_ascii=False, allow_nan=False))
            run(root, factory=lambda: cls(**deepcopy(request["factor_params"])))
            return json.loads((root / "result.json").read_text())
