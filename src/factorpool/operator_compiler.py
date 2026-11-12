"""Pool-owned AST extensions producing data-only native IR.

The existing shared compiler still owns statement validation, temporal ports,
axes, warmup and TS lowering. No user source runs during this preparation.
"""
import ast
import builtins
import copy
from dataclasses import replace
import inspect
import math
import textwrap

from factortrader.indicator.compiler import check_native_contract, UnsupportedIndicator, _TimeSeries, _Compiler
from factortrader.indicator.update_compiler import _UpdateCompiler, _CrossSection
from .operators import CONTRACT_VERSION, EXTENSIONS


def _locals(function):
    code = function.__code__
    return set(code.co_varnames) | set(code.co_cellvars)


def _declares_extension(syntax, function):
    """Identify reserved namespaces, not unrelated same-named Python calls.

    Domain aliases must be statically unambiguous local assignments. Objects
    and descriptors are never inspected or executed to guess a namespace.
    """
    globals_, locals_ = function.__globals__, _locals(function)
    own_name = syntax.args.args[0].arg if syntax.args.args else "self"
    aliases, assignments = {}, {}
    for node in ast.walk(syntax):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assignments.setdefault(node.target.id, []).append(None)

    def domain(node):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == own_name and node.attr in ("cs", "ts")):
            return node.attr
        return aliases.get(node.id) if isinstance(node, ast.Name) else None

    changed = True
    while changed:
        changed = False
        for name, values in assignments.items():
            origins = {domain(value) for value in values}
            if None not in origins and len(origins) == 1 and name not in aliases:
                aliases[name] = next(iter(origins))
                changed = True
    for node in ast.walk(syntax):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            return True
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            if (node.func.id == "pow" and "pow" not in locals_
                    and globals_.get("pow", builtins.pow) is builtins.pow):
                return True
        elif isinstance(node.func, ast.Attribute):
            owner, method = node.func.value, node.func.attr
            if domain(owner) == "cs" and method in EXTENSIONS:
                return True
            if domain(owner) == "ts" and any(keyword.arg == "clock" for keyword in node.keywords):
                return True
            if (isinstance(owner, ast.Name) and owner.id not in locals_
                    and globals_.get(owner.id) is math and method in ("sqrt", "log", "pow", "isnan")):
                return True
    return False


class PoolCompiler(_UpdateCompiler):
    def extension(self, method, syntax, variables):
        if syntax.keywords or len(syntax.args) != EXTENSIONS[method]:
            raise self.error(syntax, f"{method} requires {EXTENSIONS[method]} positional arguments")
        args = [self.value(self.expression(arg, variables), arg) for arg in syntax.args]
        if any(arg.kind == "indices" for arg in args):
            raise self.error(syntax, "Pool numeric operators do not accept positions")
        self.used_cross_section = True
        kind = "vector" if any(arg.kind == "vector" for arg in args) else "scalar"
        return self.node(method, tuple(arg.node for arg in args), kind=kind)

    def expression(self, syntax, variables):
        if isinstance(syntax, ast.BinOp) and isinstance(syntax.op, ast.Pow):
            # Convert syntax only. Operands remain subject to the shared typed
            # compiler and are never evaluated as Python during preparation.
            call = ast.copy_location(ast.Call(func=ast.Name(id="unused"), args=[syntax.left, syntax.right], keywords=[]), syntax)
            return self.extension("power", call, variables)
        return super().expression(syntax, variables)

    def call(self, syntax, variables):
        method = None
        function = inspect.getattr_static(type(self.indicator), "_indicator_function", None)
        globals_ = function.__globals__ if inspect.isfunction(function) else {}
        locals_ = _locals(function) if inspect.isfunction(function) else set()
        if isinstance(syntax.func, ast.Attribute):
            owner = syntax.func.value
            clocks = [keyword for keyword in syntax.keywords if keyword.arg == "clock"]
            if clocks:
                if len(clocks) != 1 or not isinstance(clocks[0].value, ast.Constant) or clocks[0].value.value not in ("trading_days", "valid_observations"):
                    raise self.error(syntax, "clock must be 'trading_days' or 'valid_observations'")
                source = self.expression(owner, variables)
                if not isinstance(source, _TimeSeries):
                    raise self.error(syntax, "clock is only supported on temporal operators")
                cleaned = copy.copy(syntax)
                cleaned.keywords = [keyword for keyword in syntax.keywords if keyword.arg != "clock"]
                if clocks[0].value.value == "valid_observations":
                    return super().call(cleaned, variables)
                return self.calendar(cleaned, variables)
            if (isinstance(owner, ast.Name) and owner.id not in locals_ and globals_.get(owner.id) is math
                    and syntax.func.attr in ("sqrt", "log", "pow", "isnan")):
                method = {"pow": "power", "isnan": "is_nan"}.get(syntax.func.attr, syntax.func.attr)
            elif syntax.func.attr in EXTENSIONS:
                source = self.expression(owner, variables)
                if isinstance(source, _CrossSection):
                    method = syntax.func.attr
        elif isinstance(syntax.func, ast.Name) and syntax.func.id == "pow":
            if "pow" not in locals_ and globals_.get("pow", builtins.pow) is builtins.pow:
                method = "power"
        if method is not None:
            return self.extension(method, syntax, variables)
        return super().call(syntax, variables)

    def calendar(self, syntax, variables):
        method = syntax.func.attr
        allowed = {"delay", "delta", "mean", "sum", "min", "max", "std", "decay_linear"}
        if method not in allowed:
            raise self.error(syntax, "trading_days clock is unavailable for " + method)
        if len(syntax.args) != 2:
            raise self.error(syntax, "trading_days operators require an input and period")
        options = {}
        for keyword in syntax.keywords:
            if keyword.arg != "ddof" or method != "std" or keyword.arg in options:
                raise self.error(syntax, "Only std accepts a single ddof=0/1 option")
            options[keyword.arg] = self.scalar.expression(keyword.value)
        period = self.scalar.expression(syntax.args[1])
        if type(period) is not int or period < (0 if method in ("delay", "delta") else 1) or period > 2147483647:
            raise self.error(syntax, "trading_days period must be a representable nonnegative/positive integer")
        ddof = options.get("ddof", 0)
        if type(ddof) is not int or ddof not in (0, 1):
            raise self.error(syntax, "std ddof must be 0 or 1")
        value = self.value(self.expression(syntax.args[0], variables), syntax.args[0])
        if value.kind != "vector" or self.axes[value.node] != "full":
            raise self.error(syntax, "trading_days operators require a full-axis vector")
        self.used_cross_section = True
        result = self.node("calendar_" + method, (value.node,), value=period, dependency=ddof)
        self.warmups[result.node] = self.warmups[value.node] + period - (0 if method in ("delay", "delta") else 1)
        return result


def compile_pool_indicator(indicator):
    """Return None for legacy source so its old preparation path is unchanged."""
    function = inspect.getattr_static(type(indicator), "_indicator_function", None)
    if not inspect.isfunction(function):
        return None
    try:
        lines, first = inspect.getsourcelines(function)
        filename = inspect.getsourcefile(function) or "<unknown>"
    except (OSError, TypeError):
        return None  # The shared strict-source checks retain their old behavior.
    tree = ast.parse(textwrap.dedent("".join(lines)))
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return None
    syntax = tree.body[0]
    # Ordinary math is eligible for native lowering, but does not reserve an
    # Indicator domain. Unsupported Python state/control flow keeps its old
    # explicit callback path; self.ts/self.cs declarations remain strict.
    if not _declares_extension(syntax, function):
        return None
    try:
        check_native_contract(indicator)
        compiler = PoolCompiler(indicator, syntax, filename, first)
        result = compiler.compile()
    except UnsupportedIndicator:
        # The shared metaclass also follows statically named user helpers, so
        # a hidden self.ts/self.cs call cannot turn into a Python callback.
        if inspect.getattr_static(type(indicator), "_indicator_native_domains", False):
            raise
        return None  # Shared preparation retains its dependency/input.valid checks.
    if not compiler.used_cross_section:
        # Adding an explicit spelling for the old clock must not turn a
        # scalar LEAN-style program into a stricter temporal-port program.
        # Reuse the shared scalar compiler after validating/removing that one
        # syntactic option; even its existing partial-output rules are kept.
        class ClockSpelling(ast.NodeTransformer):
            def visit_Call(self, node):
                node = self.generic_visit(node)
                node.keywords = [keyword for keyword in node.keywords if keyword.arg != "clock"]
                return node
        scalar_syntax = ClockSpelling().visit(copy.deepcopy(syntax))
        return _Compiler(indicator, scalar_syntax).compile(), None
    if result.dependencies:
        raise UnsupportedIndicator("Pool expression extensions require a self-contained Indicator")
    depths = []
    for node in result.nodes:
        depth = max((depths[source] for source in node.inputs), default=1)
        if node.op.startswith("calendar_"):
            depth += int(node.value) - (0 if node.op in ("calendar_delay", "calendar_delta") else 1)
        elif node.op == "temporal":
            depth += result.temporals[node.dependency].warm_up_period - 1
        if depth > 2147483647:
            raise compiler.error(syntax, "Pool expression warmup exceeds the supported range")
        depths.append(depth)
    result = replace(result, warm_up_period=max(depths[result.output], depths[result.ready]))
    return result, {**result.to_dict(), "version": CONTRACT_VERSION}
