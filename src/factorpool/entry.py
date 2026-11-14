"""FT-style class.run(config) facade over the shared Indicator and research core."""
from copy import deepcopy
import inspect
import dis
import json
from pathlib import Path
import tempfile

from factortrader import PythonIndicator as SharedPythonIndicator
from .operator_frontend import DIRECT, DOMAINS, NAMESPACES, _native_only


class PythonIndicator(SharedPythonIndicator):
    ts = property(lambda self: NAMESPACES['ts'])
    cs = property(lambda self: NAMESPACES['cs'])
    vc = property(lambda self: NAMESPACES['vc'])
    gp = property(lambda self: NAMESPACES['gp'])
    tf = property(lambda self: NAMESPACES['tf'])
    abs = add = subtract = multiply = log = sqrt = min = max = _native_only
    sign = signed_power = is_nan = _native_only

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
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

    def _prepare(self):
        if self._prepared:
            return super()._prepare()
        from .operator_compiler import compile_pool_indicator
        prepared = compile_pool_indicator(self)
        if prepared is None:
            return super()._prepare()
        if prepared[1] is None:
            self._ir = prepared[0]
            self._prepared = True
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
