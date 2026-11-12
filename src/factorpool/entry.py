"""FT-style class.run(config) facade over the shared Indicator and research core."""
from copy import deepcopy
import inspect
import json
from pathlib import Path
import tempfile

from factortrader import PythonIndicator as SharedPythonIndicator


class PythonIndicator(SharedPythonIndicator):
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
    def run(cls, config):
        from .config import effective_config, normalize_config
        from .script import parse_source
        from .runner import run
        filename = inspect.getsourcefile(cls)
        if filename is None or not Path(filename).is_file():
            raise ValueError("Run the Indicator from a complete Python script file")
        source = Path(filename).read_text()
        parsed = parse_source(source)
        normalized = normalize_config(config)
        if parsed["factor_class"] != cls.__name__ or parsed["source_config"] != normalized["source_config"]:
            raise ValueError("run(config) must match this script's literal config and entry class")
        request = dict(name=cls.__name__, source_code=source, **parsed)
        if request["data"]["provider"] == "market_matrix":
            from .market_store import transaction
            requested = request["data"].get("snapshot_id")
            with transaction(readonly=True) as connection:
                row = (connection.execute("SELECT id FROM market_snapshots WHERE id=%s", (requested,)).fetchone()
                       if requested else connection.execute("SELECT id FROM market_snapshots ORDER BY created_at DESC,id DESC LIMIT 1").fetchone())
            if row is None:
                raise ValueError("Market snapshot is unavailable; import a snapshot explicitly first")
            request["data"]["snapshot_id"] = row[0]
        request["effective_config"] = effective_config(request)
        # The class is already loaded. Reuse it rather than reimporting the file
        # and accidentally evaluating the user's module a second time.
        with tempfile.TemporaryDirectory(prefix="factorpool-research-") as directory:
            root = Path(directory)
            (root / "request.json").write_text(json.dumps(request, ensure_ascii=False, allow_nan=False))
            run(root, factory=lambda: cls(**deepcopy(request["factor_params"])))
            return json.loads((root / "result.json").read_text())
