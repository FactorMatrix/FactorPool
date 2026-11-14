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
           'ts','cs','vc','gp','tf',*DIRECT,'value','current','previous','is_ready',
           'samples','warm_up_period','Value','Current','Previous','IsReady','Samples',
           'WarmUpPeriod','updated','Updated','_consume')
    missing=object()
    for name in names:
        if inspect.getattr_static(type(indicator),name,missing) is not inspect.getattr_static(PythonIndicator,name,missing):
            raise UnsupportedIndicator('Custom '+name+' semantics require Python execution')


def _output_local(syntax):
    """Read this update's assigned output as SSA, never prior instance state."""
    name='__pool_current_output'
    names={n.id for n in ast.walk(syntax) if isinstance(n,ast.Name)}
    while name in names:name+='_'
    own=syntax.args.args[0].arg if syntax.args.args else 'self'
    def output(node):
        return isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==own and node.attr=='value'
    class OutputLocal(ast.NodeTransformer):
        def visit_Attribute(self,node):
            if output(node) and isinstance(node.ctx,ast.Load):
                return ast.copy_location(ast.Name(id=name,ctx=ast.Load()),node)
            return self.generic_visit(node)
        def visit_Assign(self,node):
            if len(node.targets)==1 and output(node.targets[0]):
                value=self.visit(node.value)
                local=ast.copy_location(ast.Assign(targets=[ast.Name(id=name,ctx=ast.Store())],value=value),node)
                publish=ast.copy_location(ast.Assign(targets=node.targets,value=ast.Name(id=name,ctx=ast.Load())),node)
                return [local,publish]
            return self.generic_visit(node)
    return ast.fix_missing_locations(OutputLocal().visit(copy.deepcopy(syntax)))


class PoolCompiler(_UpdateCompiler):
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
        if method=='is_ready':
            if len(syntax.args)!=1 or syntax.keywords:raise self.error(syntax,'self.ts.is_ready takes exactly one value')
            value=self.expression(syntax.args[0],variables)
            if isinstance(value,_Value):return self.node('brain_ready',(value.node,))
            return super().call(syntax,variables)
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


def compile_pool_indicator(indicator):
    declared=validate_indicator_frontend(indicator)
    function=inspect.getattr_static(type(indicator),'_indicator_function',None)
    if not inspect.isfunction(function):return None
    try:lines,first=inspect.getsourcelines(function)
    except (OSError,TypeError):return None
    filename=inspect.getsourcefile(function) or '<unknown>'
    tree=ast.parse(textwrap.dedent(''.join(lines)))
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):return None
    syntax=_output_local(tree.body[0])
    try:
        _check_contract(indicator)
        compiler=PoolCompiler(indicator,syntax,filename,first);result=compiler.compile()
        if not compiler.used_cross_section:return _Compiler(indicator,syntax).compile(),None
    except UnsupportedIndicator:
        if declared or getattr(type(indicator),'_indicator_native_domains',False):raise
        return None
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
    result=replace(result,warm_up_period=max(depths[result.output],depths[result.ready]))
    payload={**result.to_dict(),'version':CONTRACT_VERSION}
    for node in payload['nodes']:
        if node['op'].startswith(('brain_ts_','brain_cs_')):node['options']=json.loads(node['field'])
    return result,payload
