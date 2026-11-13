"""The sole public Indicator operator surface and static migration checks."""
import ast
import builtins
import inspect
import math
import textwrap

FRONTEND_VERSION = "pool-single-entry-v1"


class RemovedOperatorInterface(ValueError):
    """Obsolete user syntax must never silently become Python execution."""

from .brain_schema import TS

DIRECT = frozenset(('abs', 'add', 'subtract', 'multiply', 'log', 'sqrt', 'min',
                    'max', 'sign', 'signed_power', 'is_nan'))
DOMAINS = {
    'ts': frozenset(TS) | {'min', 'max', 'is_ready'},
    'cs': frozenset(('densify', 'normalize', 'quantile', 'rank_unit', 'scale', 'winsorize', 'zscore')),
    'vc': frozenset(('avg', 'sum')),
    'tf': frozenset(('bucket', 'trade_when')),
    'gp': frozenset(('backfill', 'mean', 'neutralize', 'rank', 'scale', 'zscore')),
}
_MATH = {'abs':'abs', 'fabs':'abs', 'log':'log', 'sqrt':'sqrt', 'min':'min',
         'max':'max', 'sign':'sign', 'signed_power':'signed_power', 'isnan':'is_nan'}
_REMOVED_CS = {
    'group_backfill':'self.gp.backfill(x, g, d, std=4)',
    'group_mean':'self.gp.mean(x, g, weight=w)',
    'group_neutralize':'self.gp.neutralize(x, g)', 'group_rank':'self.gp.rank(x, g)',
    'group_scale':'self.gp.scale(x, g)', 'group_zscore':'self.gp.zscore(x, g)',
    'vec_avg':'self.vc.avg((x, y))', 'vec_sum':'self.vc.sum((x, y))',
    'bucket':'self.tf.bucket(x, ...)', 'trade_when':'self.tf.trade_when(x, y, z)',
    'divide':'x / y', 'inverse':'1 / x', 'reverse':'-x', 'power':'x ** y',
    'if_else':'x if condition else y',
    'rank':"self.cs.rank_unit(x, normalization='unit'/'percentile'/'ordinal')",
    **{name:'self.'+name+'(...)' for name in DIRECT},
}


def migration_hint(domain, name):
    if domain == 'cs' and name in _REMOVED_CS:
        return _REMOVED_CS[name]
    if domain == 'self' and name in ('if_else', 'power', 'inverse', 'reverse'):
        return {'if_else':'x if condition else y', 'power':'x ** y',
                'inverse':'1 / x', 'reverse':'-x'}[name]
    return 'one of ' + ', '.join('self.'+domain+'.'+item for item in sorted(DOMAINS.get(domain, ())))


def _native_only(*args, **kwargs):
    raise RemovedOperatorInterface('Indicator operators require a prepared native expression; Python operator fallback is not supported')


class OperatorNamespace:
    __slots__ = ('_domain',)
    def __init__(self, domain):
        self._domain = domain
    def __getattr__(self, name):
        if name in DOMAINS[self._domain]:
            return _native_only
        raise AttributeError(f'self.{self._domain}.{name} is not a public operator; use {migration_hint(self._domain, name)}')
    def __dir__(self):
        return sorted(DOMAINS[self._domain])


NAMESPACES = {name:OperatorNamespace(name) for name in DOMAINS}


def _scan_function(syntax, imports, globals_=None):
    """Resolve only static names; never run descriptors or user functions."""
    globals_ = globals_ or {}
    imports=dict(imports)
    for node in ast.walk(syntax):
        if isinstance(node,ast.Import):
            for alias in node.names:
                if alias.name=='math':imports[alias.asname or alias.name]=('math','')
        elif isinstance(node,ast.ImportFrom) and node.module=='math':
            for alias in node.names:imports[alias.asname or alias.name]=('math_function',alias.name)
    own = syntax.args.args[0].arg if syntax.args.args else 'self'
    assigned = {}
    for node in ast.walk(syntax):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else (node.target,):
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            assigned.setdefault(node.target.id, []).append(None)
    arguments = {arg.arg for arg in (*syntax.args.args, *syntax.args.kwonlyargs)}
    aliases = {}
    for name, value in imports.items():
        if name not in arguments and name not in assigned:
            aliases[name] = value
    def origin(node):
        if isinstance(node, ast.Name):
            if node.id == own:
                return ('self', '')
            if node.id in aliases:
                return aliases[node.id]
            if (node.id in ('abs','min','max','pow') and node.id not in assigned
                    and node.id not in arguments
                    and globals_.get(node.id, getattr(builtins,node.id)) is getattr(builtins,node.id)):
                return ('builtin', node.id)
        if isinstance(node, ast.Attribute):
            source = origin(node.value)
            if source == ('self', ''):
                if node.attr in DOMAINS:
                    return ('domain', node.attr)
                if node.attr in DIRECT or node.attr in ('if_else','power','reverse','inverse'):
                    return ('method', 'self', node.attr)
            if source and source[0] == 'domain':
                return ('method', source[1], node.attr)
            if source == ('math', ''):
                return ('math_function', node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'getattr' and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant) and type(node.args[1].value) is str):
            source = origin(node.args[0])
            if source and source[0] == 'domain':
                return ('method', source[1], node.args[1].value)
        return None
    for _ in range(len(assigned)+1):
        changed = False
        for name, values in assigned.items():
            origins = {origin(value) for value in values}
            if None not in origins and len(origins) == 1 and name not in aliases:
                aliases[name] = next(iter(origins));changed=True
        if not changed:break
    declared = False
    for node in ast.walk(syntax):
        value = origin(node)
        if value and value[0] in ('domain','method'):
            declared = True
        if value and value[0] == 'method':
            _, domain, method = value
            allowed = DIRECT if domain == 'self' else DOMAINS[domain]
            if method not in allowed:
                raise RemovedOperatorInterface(f'line {node.lineno}: self.{domain+"." if domain != "self" else ""}{method} was removed; use {migration_hint(domain,method)}')
        if isinstance(node, ast.Call):
            called = origin(node.func)
            if called and called[0] == 'builtin':
                name = called[1];hint = 'x ** y' if name=='pow' else 'self.'+name+'(...)'
                raise RemovedOperatorInterface(f'line {node.lineno}: bare {name}(...) was removed; use {hint}')
            if called and called[0] == 'math_function' and called[1] in (_MATH.keys() | {'pow'}):
                name=called[1];hint='x ** y' if name=='pow' else 'self.'+_MATH[name]+'(...)'
                raise RemovedOperatorInterface(f'line {node.lineno}: math.{name}(...) was removed; use {hint}')
    return declared


def validate_source_frontend(source):
    """Reject obsolete public calls in source without executing that source."""
    try:tree=ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f'line {error.lineno}: {error.msg}') from None
    imports={};foreign_bases=set();foreign_modules=set()
    for node in tree.body:
        if isinstance(node,ast.ImportFrom) and node.module=='factortrader':
            foreign_bases.update(alias.asname or alias.name for alias in node.names if alias.name in ('PythonIndicator','Indicator'))
        elif isinstance(node,ast.Import):
            foreign_modules.update(alias.asname or alias.name for alias in node.names if alias.name=='factortrader')
    for cls in (node for node in ast.walk(tree) if isinstance(node,ast.ClassDef)):
        for base in cls.bases:
            if (isinstance(base,ast.Name) and base.id in foreign_bases
                    or isinstance(base,ast.Attribute) and isinstance(base.value,ast.Name)
                    and base.value.id in foreign_modules and base.attr in ('PythonIndicator','Indicator')):
                raise RemovedOperatorInterface(f'line {cls.lineno}: FactorPool factors must use from factorpool import PythonIndicator')
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name=='math':imports[alias.asname or 'math']=('math','')
        elif isinstance(node, ast.ImportFrom) and node.module=='math':
            for alias in node.names:imports[alias.asname or alias.name]=('math_function',alias.name)
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            imports[node.name]=('python_function',node.name)
    for function in (node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))):
        _scan_function(function,imports)
    return tree


def validate_indicator_frontend(indicator):
    pending=[inspect.getattr_static(type(indicator),'_indicator_function',None)];seen=set();declared=False
    while pending:
        function=pending.pop()
        if isinstance(function,(staticmethod,classmethod)):function=function.__func__
        if isinstance(function,property):function=function.fget
        if not inspect.isfunction(function) or function in seen:continue
        seen.add(function)
        try:syntax=ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
        except (OSError,TypeError,SyntaxError):continue
        imports={}
        for name,value in function.__globals__.items():
            if value is math:imports[name]=('math','')
            elif inspect.isbuiltin(value) and getattr(value,'__module__',None)=='math':
                imports[name]=('math_function',value.__name__)
            elif inspect.isfunction(value):imports[name]=('python_function',name)
        declared |= _scan_function(syntax,imports,function.__globals__)
        own=syntax.args.args[0].arg if syntax.args.args else 'self'
        for node in ast.walk(syntax):
            if (isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==own
                    and node.attr not in DIRECT and node.attr not in DOMAINS
                    and node.attr not in ('value','current','previous','samples','is_ready','warm_up_period')):
                child=inspect.getattr_static(type(indicator),node.attr,None)
                if inspect.isfunction(child) or isinstance(child,(staticmethod,classmethod,property)):pending.append(child)
    return declared
