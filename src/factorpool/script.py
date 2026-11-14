"""Resolve a full research script through AST, without executing its code."""
import ast
from copy import deepcopy
import json
import re
import sys
from .config import merge_config, normalize_config


class ScriptError(ValueError):
    pass


def _fail(node, message):
    raise ScriptError(f"line {getattr(node, 'lineno', 1)}: {message}")


def _main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    return ((isinstance(left, ast.Name) and left.id == "__name__"
             and isinstance(right, ast.Constant) and right.value == "__main__")
            or (isinstance(right, ast.Name) and right.id == "__name__"
                and isinstance(left, ast.Constant) and left.value == "__main__"))


def _calls(statements):
    for node in statements:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            yield node
        yield from _calls(ast.iter_child_nodes(node))


def validate_frontend_source(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 131072:
        _fail(None, "source_code (max 128 KiB) is required")
    from .operator_frontend import validate_source_frontend
    try:
        validate_source_frontend(source)
    except SyntaxError as error:
        raise ScriptError(f"line {error.lineno or 1}: invalid Python syntax") from None
    except ValueError as error:
        raise ScriptError(str(error)) from None


def parse_source(source, workspace_config=None):
    validate_frontend_source(source)
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise ScriptError(f"line {getattr(error, 'lineno', 1) or 1}: invalid Python syntax") from None
    definitions = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "config" for target in node.targets):
            definitions.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "config":
            definitions.append(node)
    if len(definitions) > 1:
        _fail(definitions[-1], "Define at most one literal config dictionary")
    definition = definitions[0] if definitions else None
    config = {}
    if definition is not None:
        if isinstance(definition, ast.Assign) and len(definition.targets) != 1:
            _fail(definition, "Assign config to one name only")
        try:
            config = ast.literal_eval(definition.value)
        except (ValueError, TypeError, SyntaxError, RecursionError):
            _fail(definition, "config must be a literal dictionary; dynamic configuration is unsupported")
        for node in ast.walk(definition.value):
            if isinstance(node, ast.Dict):
                seen = set()
                for key in node.keys:
                    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                        _fail(key or node, "Configuration dictionaries need literal string keys")
                    if key.value in seen:
                        _fail(key, "Duplicate configuration option")
                    seen.add(key.value)
    candidates = []
    guards = [node for node in module.body if _main_guard(node)]
    for node in module.body:
        for call in _calls([node]):
            if isinstance(call.func, ast.Attribute) and call.func.attr == "run":
                candidates.append(call)
    if len(candidates) != 1:
        _fail(candidates[-1] if candidates else module, "Use one guarded IndicatorClass.run(config) entry")
    entry = candidates[0]
    guarded = any(entry in tuple(_calls(guard.body)) for guard in guards)
    valid_args = (len(entry.args) == 1 and isinstance(entry.args[0], ast.Name)
                  and entry.args[0].id == "config") if definition is not None else not entry.args
    if not guarded or not isinstance(entry.func.value, ast.Name) or not valid_args or entry.keywords:
        _fail(entry, "Use guarded IndicatorClass.run(config), or IndicatorClass.run() without a config definition")
    name = entry.func.value.id
    classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
    if name not in classes or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        _fail(entry, "Entry Indicator class must be defined in this script")
    aliases, modules = set(), set()
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.module == "factorpool":
            aliases.update(alias.asname or alias.name for alias in node.names
                           if alias.name == "PythonIndicator")
        elif isinstance(node, ast.Import):
            modules.update(alias.asname or "factorpool" for alias in node.names if alias.name == "factorpool")
    def indicator_class(cls_name, seen):
        if cls_name in seen:
            return False
        seen = seen | {cls_name}
        for base in classes[cls_name].bases:
            if isinstance(base, ast.Name):
                if base.id in aliases or (base.id in classes and indicator_class(base.id, seen)):
                    return True
            elif (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                  and base.value.id in modules and base.attr == "PythonIndicator"):
                return True
        return False
    if not indicator_class(name, set()):
        _fail(classes[name], "Use the shared Indicator research facade from factorpool")
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
           for node in classes[name].body):
        _fail(classes[name], "Do not override the research run entry")
    allowed_config_nodes = ({id(node) for node in ast.walk(definition)} | {id(entry.args[0])}
                            if definition is not None else set())
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id == "config" and id(node) not in allowed_config_nodes:
            _fail(node, "Keep config literal; edit the dictionary instead of mutating or aliasing it")
    workspace = {} if workspace_config is None else workspace_config
    try:
        result = normalize_config(merge_config(workspace, config))
    except ValueError as error:
        _fail(definition, str(error))
    result["source_config"] = deepcopy(config)
    parsed = dict(factor_class=name, workspace_config=deepcopy(workspace), **result)
    from .config import effective_config
    parsed["effective_config"] = effective_config(parsed)
    return parsed


def bind_request(request):
    """Recheck source authority in the worker; only a newly pinned ID may differ."""
    allowed = {"id", "name", "source_code", "workspace_config", "factor_class", "factor_params",
               "data", "analysis", "source_config", "brain_settings", "simulation_type", "effective_config"}
    unknown = set(request) - allowed
    if unknown:
        raise ScriptError(f"line 1: unsupported research request field: {sorted(unknown)[0]}")
    parsed = parse_source(request["source_code"], request.get("workspace_config"))
    for key in ("factor_class", "factor_params", "analysis", "source_config", "brain_settings"):
        if key in request and request[key] != parsed[key]:
            raise ScriptError(f"line 1: {key} conflicts with source config")
    supplied_data = request.get("data", parsed["data"])
    expected_data = deepcopy(parsed["data"])
    if "snapshot_id" not in expected_data and "snapshot_id" in supplied_data:
        expected_data["snapshot_id"] = supplied_data["snapshot_id"]
    if supplied_data != expected_data:
        raise ScriptError("line 1: data conflicts with source config")
    result = dict(request, **parsed)
    result["data"] = expected_data
    from .config import effective_config
    effective = effective_config(result)
    if "effective_config" in request and request["effective_config"] != effective:
        raise ScriptError("line 1: effective_config conflicts with source config")
    result["effective_config"] = effective
    return result


def main():
    try:
        body = json.loads(sys.stdin.read(1048577))
        if not isinstance(body, dict):
            raise ScriptError("line 1: expected a source_code object")
        unknown = set(body) - {"source_code", "workspace_config"}
        if unknown:
            raise ScriptError(f"line 1: unsupported script request field: {sorted(unknown)[0]}")
        if "workspace_config" in body and not isinstance(body["workspace_config"], dict):
            raise ScriptError("line 1: workspace_config must be an object")
        result = parse_source(body.get("source_code"), body.get("workspace_config"))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ValueError, RecursionError):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, ScriptError) else "line 1: invalid configuration input"
        print(message.splitlines()[0][:1000], file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
