"""Pool's single public operator frontend lowers to existing native graph nodes."""
import ast
import copy
from dataclasses import replace
import inspect
import json
import textwrap

from factortrader.indicator.compiler import UnsupportedIndicator, _TimeSeries, _Compiler
from factortrader.indicator.update_compiler import _UpdateCompiler, _CrossSection, _Value
from .operators import CONTRACT_VERSION, EXTENSIONS
from .brain_schema import CORE_TS, OBSERVATION_TS, prepare_core_call, literal
from .operator_frontend import DIRECT, DOMAINS, validate_indicator_frontend


class _Namespace:
    def __init__(self, domain):self.domain=domain


class _Operator:
    def __init__(self, domain, method):self.domain,self.method=domain,method


def _check_contract(indicator):
    from .entry import PythonIndicator
    names=('__getattribute__','__getattr__','__setattr__','__dict__',
           'ts','cs','vc','gp','tf',*DIRECT,'value','current','previous',
           'samples','warm_up_period','Value','Current','Previous','IsReady','Samples',
           'WarmUpPeriod','updated','Updated','_consume')
    missing=object()
    for name in names:
        if inspect.getattr_static(type(indicator),name,missing) is not inspect.getattr_static(PythonIndicator,name,missing):
            raise UnsupportedIndicator('Custom '+name+' semantics require Python execution')


def _output_local(syntax, indicator=None, *, implicit_ready=True, warmup=None):
    """Read this update's assigned output as SSA, never prior instance state."""
    name='__pool_current_output'
    names={n.id for n in ast.walk(syntax) if isinstance(n,ast.Name)}
    while name in names:name+='_'
    own=syntax.args.args[0].arg if syntax.args.args else 'self'
    custom = None
    if indicator is not None:
        from .entry import PythonIndicator
        ready = inspect.getattr_static(type(indicator), 'is_ready', None)
        if ready is not inspect.getattr_static(PythonIndicator, 'is_ready', None):
            if not isinstance(ready, property) or not inspect.isfunction(ready.fget):
                raise UnsupportedIndicator('Custom is_ready must be a property; unsupported native readiness requires Python execution')
            try:
                getter = ast.parse(textwrap.dedent(inspect.getsource(ready.fget))).body[0]
            except (OSError, TypeError, SyntaxError):
                raise UnsupportedIndicator('Custom is_ready source is unavailable for native compilation') from None
            statements = [s for s in getter.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                          and isinstance(s.value.value, str)) and not isinstance(s, ast.Pass)]
            if len(statements) != 1 or not isinstance(statements[0], ast.Return) or statements[0].value is None:
                raise UnsupportedIndicator('Custom is_ready needs one pure return expression for native execution')
            custom = copy.deepcopy(statements[0].value)
            getter_self = getter.args.args[0].arg
            for node in ast.walk(custom):
                if isinstance(node, ast.Name) and node.id == getter_self: node.id = own
    def internal(kind, args, location):
        node = ast.Call(func=ast.Name(id='__pool_indicator_state', ctx=ast.Load()), args=args, keywords=[])
        node._pool_state = kind
        return ast.copy_location(node, location)
    def output(node):
        return isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==own and node.attr=='value'
    class OutputLocal(ast.NodeTransformer):
        reading_custom = False
        reading_return = False
        reads_warmup = False
        def visit_Attribute(self,node):
            if output(node) and isinstance(node.ctx,ast.Load):
                return ast.copy_location(ast.Name(id=name,ctx=ast.Load()),node)
            if indicator is not None and isinstance(node.value, ast.Name) and node.value.id == own:
                if node.attr == 'is_ready':
                    if custom is not None:
                        if self.reading_custom:
                            raise UnsupportedIndicator('Custom is_ready cannot recursively read itself')
                        self.reading_custom = True
                        try: return self.visit(copy.deepcopy(custom))
                        finally: self.reading_custom = False
                    return internal('ready', [ast.Name(id=name, ctx=ast.Load())], node)
                if node.attr == 'samples': return internal('samples', [], node)
                if node.attr == 'warm_up_period':
                    self.reads_warmup = True
                    value = warmup if warmup is not None else object.__getattribute__(indicator, '__dict__').get('_warm_up_period', 0)
                    return ast.copy_location(ast.Constant(value=value), node)
            return self.generic_visit(node)
        def visit_BoolOp(self, node):
            if not (self.reading_custom or self.reading_return): return self.generic_visit(node)
            values = [self.visit(value) for value in node.values]
            result = values[-1]
            for value in reversed(values[:-1]):
                # Preserve per-symbol short circuiting: an unused missing
                # branch must not invalidate an already decided predicate.
                yes, no = (result, value) if isinstance(node.op, ast.And) else (value, result)
                result = ast.copy_location(ast.IfExp(test=value, body=yes, orelse=no), node)
            return result
        def visit_UnaryOp(self, node):
            if (self.reading_custom or self.reading_return) and isinstance(node.op, ast.Not):
                return ast.copy_location(ast.UnaryOp(op=ast.Invert(), operand=self.visit(node.operand)), node)
            return self.generic_visit(node)
        def visit_Return(self, node):
            if indicator is None: return self.generic_visit(node)
            fallback = node.value is None or isinstance(node.value, ast.Constant) and node.value.value is None
            if fallback:
                node.value = ast.Attribute(value=ast.Name(id=own, ctx=ast.Load()), attr='is_ready', ctx=ast.Load())
            self.reading_return = True
            try: result = self.generic_visit(node)
            finally: self.reading_return = False
            # LEAN's wrapper first accepts a true Update return, otherwise it
            # consults a custom readiness property. Output dependency masks
            # still gate this result inside the native Program.
            if custom is not None and not fallback:
                prop = self.visit(ast.Attribute(value=ast.Name(id=own, ctx=ast.Load()), attr='is_ready', ctx=ast.Load()))
                result.value = ast.copy_location(ast.IfExp(test=result.value, body=ast.Constant(value=True), orelse=prop), node)
            return result
        def visit_Assign(self,node):
            if len(node.targets)==1 and output(node.targets[0]):
                value=self.visit(node.value)
                local=ast.copy_location(ast.Assign(targets=[ast.Name(id=name,ctx=ast.Store())],value=value),node)
                publish=ast.copy_location(ast.Assign(targets=node.targets,value=ast.Name(id=name,ctx=ast.Load())),node)
                return [local,publish]
            return self.generic_visit(node)
    syntax = copy.deepcopy(syntax)
    if indicator is not None and implicit_ready and (not syntax.body or not isinstance(syntax.body[-1], ast.Return)):
        syntax.body.append(ast.copy_location(ast.Return(value=None), syntax))
    lowering = OutputLocal()
    syntax = ast.fix_missing_locations(lowering.visit(syntax))
    syntax._pool_reads_warmup = lowering.reads_warmup
    return syntax


class _ScalarCompiler(_Compiler):
    def call(self, syntax):
        if getattr(syntax, '_pool_state', None) == 'ready':
            value = self.series(self.expression(syntax.args[0]))
            return self.node('ready', (value.node,))
        return super().call(syntax)

    def compile(self):
        result = super().compile()
        # A user's true return cannot make an unfinished native dependency
        # ready. This is the same conjunction used by Pool's batch Program.
        node = type(result.nodes[0])
        nodes = (*result.nodes, node('ready', (result.output,)))
        ready = len(nodes)
        nodes = (*nodes, node('and', (result.ready, ready - 1)))
        return replace(result, nodes=nodes, ready=ready)


class PoolCompiler(_UpdateCompiler):
    def __init__(self, indicator, function, filename, first_line):
        super().__init__(indicator, function, filename, first_line)
        self.scalar = _ScalarCompiler(indicator, function)

    def extension(self, method, syntax, variables):
        if syntax.keywords or len(syntax.args)!=EXTENSIONS[method]:
            raise self.error(syntax,f'{method} requires {EXTENSIONS[method]} positional arguments')
        args=[self.value(self.expression(arg,variables),arg) for arg in syntax.args]
        if any(arg.kind=='indices' for arg in args):raise self.error(syntax,'Numeric operators do not consume positions')
        self.used_cross_section=True
        return self.node(method,tuple(arg.node for arg in args),kind='vector' if any(arg.kind=='vector' for arg in args) else 'scalar')

    def expression(self,syntax,variables):
        if isinstance(syntax,ast.Attribute):
            if isinstance(syntax.value,ast.Name) and syntax.value.id==self.self_name:
                if syntax.attr in ('vc','gp','tf'):return _Namespace(syntax.attr)
                if syntax.attr in DIRECT:return _Operator('self',syntax.attr)
            if syntax.attr in set().union(*DOMAINS.values()):
                source=self.expression(syntax.value,variables)
                domain=('ts' if isinstance(source,_TimeSeries) else 'cs' if isinstance(source,_CrossSection)
                        else source.domain if isinstance(source,_Namespace) else None)
                if domain:
                    self.used_cross_section=True
                    return _Operator(domain,syntax.attr)
        if isinstance(syntax,ast.BinOp) and isinstance(syntax.op,ast.Pow):
            call=ast.copy_location(ast.Call(func=ast.Name(id='unused',ctx=ast.Load()),args=[syntax.left,syntax.right],keywords=[]),syntax)
            return self.extension('power',call,variables)
        if isinstance(syntax,ast.IfExp):
            call=ast.copy_location(ast.Call(func=ast.Name(id='unused',ctx=ast.Load()),args=[syntax.test,syntax.body,syntax.orelse],keywords=[]),syntax)
            return self.extension('if_else',call,variables)
        return super().expression(syntax,variables)

    def _defaults(self,domain,method,syntax):
        defaults=({'std':{'ddof':0},'zscore':{'ddof':0},'scale':{'constant':0},
                   'decay_linear':{'dense':False}} if domain=='ts' else
                  {'normalize':{'useStd':False,'limit':0},'scale':{'scale':1}}).get(method,{})
        cleaned=copy.copy(syntax);cleaned.keywords=[]
        for keyword in syntax.keywords:
            if keyword.arg in defaults:
                value=literal(self,keyword.value);default=defaults[keyword.arg]
                same=(type(value) is type(default) if keyword.arg in ('ddof','dense','useStd') else type(value) in (int,float)) and value==default
                if same:continue
            cleaned.keywords.append(keyword)
        return cleaned

    def _ts(self,method,syntax,variables):
        clocks=[kw for kw in syntax.keywords if kw.arg=='clock']
        if len(clocks)>1:raise self.error(syntax,'Repeated clock')
        clock=literal(self,clocks[0].value) if clocks else ('valid_observations' if method in OBSERVATION_TS or method in ('min','max') else 'trading_days')
        if clock not in ('trading_days','valid_observations'):raise self.error(syntax,'Unknown temporal clock')
        cleaned=copy.copy(syntax);cleaned.keywords=[kw for kw in syntax.keywords if kw.arg!='clock']
        if method in CORE_TS:return prepare_core_call(self,'ts',method,syntax,variables)
        cleaned=self._defaults('ts',method,cleaned)
        if clock=='trading_days':
            if method in ('mean','sum','std','delay','delta','decay_linear','min','max') and not any(k.arg=='dense' for k in cleaned.keywords):
                return self.calendar(cleaned,variables)
            cooked=copy.copy(cleaned);cooked.keywords=[*cleaned.keywords,ast.keyword(arg='clock',value=ast.Constant(value=clock))]
            return prepare_core_call(self,'ts',method,cooked,variables)
        if method=='scale':
            constants=[k for k in cleaned.keywords if k.arg=='constant']
            if constants:
                if len(constants)!=1:raise self.error(syntax,'Repeated constant')
                value=literal(self,constants[0].value)
                if type(value) not in (int,float):raise self.error(syntax,'constant must be finite numeric')
                base=copy.copy(cleaned);base.keywords=[k for k in cleaned.keywords if k.arg!='constant']
                source=self.value(super().call(base,variables),syntax);offset=self.value(value,syntax)
                self.used_cross_section=True
                return self.node('add',(source.node,offset.node),kind=source.kind)
        if any(k.arg=='dense' for k in cleaned.keywords):
            cooked=copy.copy(cleaned);cooked.keywords=[*cleaned.keywords,ast.keyword(arg='clock',value=ast.Constant(value=clock))]
            return prepare_core_call(self,'ts',method,cooked,variables)
        # The scalar compiler sees the same canonical options, rather than
        # taking a second parameter-dependent route.
        for node in ast.walk(self.function):
            if isinstance(node,ast.Call) and node.lineno==syntax.lineno and node.col_offset==syntax.col_offset:
                node.keywords=cleaned.keywords
        return super().call(cleaned,variables)

    def _rank(self,syntax,variables):
        if len(syntax.args)>2 or not syntax.args:raise self.error(syntax,'rank_unit requires a value and optional rate')
        options={}
        if len(syntax.args)==2:options['rate']=literal(self,syntax.args[1])
        for kw in syntax.keywords:
            if kw.arg not in ('rate','normalization','ascending') or kw.arg in options:raise self.error(syntax,'Invalid/repeated rank_unit option')
            options[kw.arg]=literal(self,kw.value)
        mode,ascending,rate=options.get('normalization','unit'),options.get('ascending',True),options.get('rate',2)
        if mode not in ('unit','percentile','ordinal') or type(mode) is not str:raise self.error(syntax,'normalization must be unit, percentile or ordinal')
        if type(ascending) is not bool or type(rate) is not int or rate<0:raise self.error(syntax,'ascending must be bool and rate a nonnegative integer')
        value=self.value(self.expression(syntax.args[0],variables),syntax.args[0])
        if value.kind!='vector':raise self.error(syntax,'rank_unit requires an axis-bearing vector')
        self.used_cross_section=True
        if mode!='unit':
            if rate!=2:raise self.error(syntax,'non-unit rank normalization requires default rate=2')
            return self.node('rank',(value.node,),field='ascending' if ascending else 'descending',value=mode=='percentile')
        if not ascending:value=self.node('neg',(value.node,))
        if rate==2:return self.node('rank_unit',(value.node,))
        cooked=copy.copy(syntax);cooked.args=[syntax.args[0]];cooked.keywords=[ast.keyword(arg='rate',value=ast.Constant(value=rate))]
        if not ascending:
            cooked.args=[ast.copy_location(ast.UnaryOp(op=ast.USub(),operand=syntax.args[0]),syntax.args[0])]
        return prepare_core_call(self,'cs','rank_unit',cooked,variables)

    def call(self,syntax,variables):
        state = getattr(syntax, '_pool_state', None)
        if state == 'samples':
            self.used_cross_section = True
            result = self.node('indicator_samples')
            self.axes[result.node] = 'full'
            return result
        if state == 'ready':
            from factortrader.indicator.update_compiler import _Temporal
            value = self.expression(syntax.args[0], variables)
            if isinstance(value, _Temporal):
                return self.temporal(value.series, syntax, field='ready', guard=value.guard)
            value = self.value(value, syntax)
            self.used_cross_section = True
            result = self.node('brain_ready', (value.node,))
            self.axes[result.node] = self.axes[value.node] or 'full'
            return result
        target=None
        if isinstance(syntax.func,ast.Attribute):
            if isinstance(syntax.func.value,ast.Name) and syntax.func.value.id==self.self_name and syntax.func.attr in DIRECT:
                target=_Operator('self',syntax.func.attr)
            else:
                owner=self.expression(syntax.func.value,variables)
                domain=('ts' if isinstance(owner,_TimeSeries) else 'cs' if isinstance(owner,_CrossSection)
                        else owner.domain if isinstance(owner,_Namespace) else None)
                if domain:target=_Operator(domain,syntax.func.attr)
        elif isinstance(syntax.func,ast.Name) and isinstance(variables.get(syntax.func.id),_Operator):target=variables[syntax.func.id]
        if target is None:return super().call(syntax,variables)
        domain,method=target.domain,target.method
        canonical=copy.copy(syntax)
        owner=ast.Name(id=self.self_name,ctx=ast.Load())
        if domain!='self':owner=ast.Attribute(value=owner,attr=domain,ctx=ast.Load())
        canonical.func=ast.Attribute(value=owner,attr=method,ctx=ast.Load())
        syntax=canonical
        if method not in (DIRECT if domain=='self' else DOMAINS[domain]):
            raise self.error(syntax,f'self.{domain}.{method} is not a public operator')
        if domain=='ts':return self._ts(method,syntax,variables)
        if domain=='self':
            if method in EXTENSIONS:return self.extension(method,syntax,variables)
            return prepare_core_call(self,'math',method,syntax,variables)
        if domain in ('vc','gp','tf'):
            if domain=='gp' and method in ('neutralize','rank') and len(syntax.args)==2 and not syntax.keywords:
                args=[self.value(self.expression(a,variables),a) for a in syntax.args]
                if all(v.kind=='vector' for v in args):
                    self.used_cross_section=True
                    return self.node('group_'+method,tuple(v.node for v in args))
            return prepare_core_call(self,domain,method,syntax,variables)
        if method=='rank_unit':return self._rank(syntax,variables)
        cleaned=self._defaults('cs',method,syntax)
        if method=='scale' and any(k.arg=='scale' for k in cleaned.keywords) and not any(k.arg in ('longscale','shortscale') for k in cleaned.keywords):
            if len(cleaned.args)!=1 or len(cleaned.keywords)!=1:raise self.error(syntax,'scale requires one value and scale=...')
            multiplier=literal(self,cleaned.keywords[0].value)
            if type(multiplier) not in (int,float):raise self.error(syntax,'scale must be numeric')
            value=self.value(self.expression(cleaned.args[0],variables),cleaned.args[0]);self.used_cross_section=True
            scaled=self.node('scale',(value.node,));number=self.value(multiplier,syntax)
            return self.node('mul',(scaled.node,number.node))
        if method in ('normalize','scale','zscore') and len(cleaned.args)==1 and not cleaned.keywords:
            value=self.value(self.expression(cleaned.args[0],variables),cleaned.args[0])
            if value.kind!='vector':raise self.error(syntax,'self.cs.'+method+' requires a vector')
            self.used_cross_section=True
            return self.node(method,(value.node,))
        return prepare_core_call(self,'cs',method,cleaned,variables)

    def calendar(self,syntax,variables):
        method=syntax.func.attr
        period_options=[kw for kw in syntax.keywords if kw.arg=='period']
        if len(syntax.args)==1 and len(period_options)==1:
            syntax=copy.copy(syntax);syntax.args=[*syntax.args,period_options[0].value]
            syntax.keywords=[kw for kw in syntax.keywords if kw.arg!='period']
        if len(syntax.args)!=2:raise self.error(syntax,'trading_days windows require an input and period')
        options={}
        for kw in syntax.keywords:
            if kw.arg!='ddof' or method!='std' or kw.arg in options:raise self.error(syntax,'Only std accepts ddof=0/1')
            options[kw.arg]=literal(self,kw.value)
        period=literal(self,syntax.args[1]);ddof=options.get('ddof',0)
        if type(period) is not int or period<(0 if method in ('delay','delta') else 1) or period>2147483647:raise self.error(syntax,'Invalid trading_days period')
        if type(ddof) is not int or ddof not in (0,1):raise self.error(syntax,'std ddof must be 0 or 1')
        value=self.value(self.expression(syntax.args[0],variables),syntax.args[0])
        if value.kind!='vector' or self.axes[value.node]!='full':raise self.error(syntax,'trading_days windows require a full-axis vector')
        self.used_cross_section=True
        result=self.node('calendar_'+method,(value.node,),value=period,dependency=ddof)
        self.warmups[result.node]=self.warmups[value.node]+period-(0 if method in ('delay','delta') else 1)
        return result


def _compile_prepared(indicator, syntax, filename, first):
    declared_warmup = object.__getattribute__(indicator, '__dict__').get('_warm_up_period', 0)
    compiler=PoolCompiler(indicator,syntax,filename,first)
    result=compiler.compile()
    if not compiler.used_cross_section:
        result=_ScalarCompiler(indicator,syntax).compile()
        return replace(result,warm_up_period=max(declared_warmup,result.warm_up_period)),None
    if result.dependencies:raise UnsupportedIndicator('Pool expressions must be self-contained')
    depths=[]
    for node in result.nodes:
        depth=max((depths[source] for source in node.inputs),default=1)
        if node.op.startswith('calendar_'):depth+=int(node.value)-(0 if node.op in ('calendar_delay','calendar_delta') else 1)
        elif node.op=='temporal':depth+=result.temporals[node.dependency].warm_up_period-1
        elif node.op.startswith('brain_ts_') or node.op=='brain_cs_group_backfill':
            options=json.loads(node.field);name=node.op[9:]
            if name not in ('hump','days_from_last_change','ts_step'):
                depth+=int(options.get('period',1))-1+int(options.get('lag',0))
                if name in ('ts_delay','ts_delta'):depth+=1
        if depth>2147483647:raise UnsupportedIndicator('Pool expression warmup exceeds supported range')
        depths.append(depth)
    result=replace(result,warm_up_period=max(declared_warmup,depths[result.output],depths[result.ready]))
    payload={**result.to_dict(),'version':CONTRACT_VERSION,'warm_up_period':result.warm_up_period}
    for node in payload['nodes']:
        if node['op'].startswith(('brain_ts_','brain_cs_')):node['options']=json.loads(node['field'])
    return result,payload


def compile_pool_indicator(indicator):
    declared=validate_indicator_frontend(indicator)
    function=inspect.getattr_static(type(indicator),'_indicator_function',None)
    if not inspect.isfunction(function):return None
    try:lines,first=inspect.getsourcelines(function)
    except (OSError,TypeError):return None
    filename=inspect.getsourcefile(function) or '<unknown>'
    tree=ast.parse(textwrap.dedent(''.join(lines)))
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):return None
    native_state = declared or getattr(type(indicator), '_indicator_native_domains', False)
    lower_current_ready = native_state
    if not native_state:
        from .entry import PythonIndicator
        if inspect.getattr_static(type(indicator), 'is_ready', None) is not inspect.getattr_static(PythonIndicator, 'is_ready', None):
            return None
        returns = [node for node in ast.walk(tree.body[0]) if isinstance(node, ast.Return)]
        if not returns or any(node.value is None or isinstance(node.value, ast.Constant)
                              and node.value.value is None for node in returns):
            return None
        own = tree.body[0].args.args[0].arg
        lower_current_ready = any(isinstance(node, ast.Attribute) and node.attr == 'is_ready'
                                  and isinstance(node.value, ast.Name) and node.value.id == own
                                  for statement in returns for node in ast.walk(statement))
    try:
        _check_contract(indicator)
        warmup = None
        # Only explicit warmup reads need this static resolution. No user
        # callback or graph update is executed while preparing the program.
        for _ in range(3):
            syntax=_output_local(tree.body[0], indicator if lower_current_ready else None,
                                 implicit_ready=native_state, warmup=warmup)
            result,payload=_compile_prepared(indicator,syntax,filename,first)
            if not syntax._pool_reads_warmup or warmup == result.warm_up_period:
                return result,payload
            warmup=result.warm_up_period
        raise UnsupportedIndicator('Native warm_up_period has a circular dependency; declare an independent window/readiness expression')
    except UnsupportedIndicator:
        if declared or getattr(type(indicator),'_indicator_native_domains',False):raise
        return None
