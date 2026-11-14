"""Typed call schemas for the 66 core capabilities, using Indicator domains."""
import ast
import json
import math

TS = {
    'days_from_last_change': ('days_from_last_change', 1, (), {}),
    'hump': ('hump', 1, ('hump',), {'hump': .01}),
    'kth_element': ('kth_element', 1, ('period', 'k', 'ignore'), {'k': 1, 'ignore': 'NaN'}),
    'last_diff_value': ('last_diff_value', 1, ('period',), {}),
    'arg_max': ('ts_arg_max', 1, ('period',), {}),
    'arg_min': ('ts_arg_min', 1, ('period',), {}),
    'backfill': ('ts_backfill', 1, ('lookback', 'k'), {'k': 1}),
    'corr': ('ts_corr', 2, ('period',), {}),
    'count_nans': ('ts_count_nans', 1, ('period',), {}),
    'covariance': ('ts_covariance', 2, ('period',), {}),
    'product': ('ts_product', 1, ('period',), {}),
    'quantile': ('ts_quantile', 1, ('period', 'driver'), {'driver': 'gaussian'}),
    'rank': ('ts_rank', 1, ('period', 'constant'), {'constant': 0}),
    'regression': ('ts_regression', 2, ('period', 'lag', 'rettype'), {'lag': 0, 'rettype': 0}),
    'step': ('ts_step', 0, ('period',), {'period': 1}),
    'av_diff': ('ts_av_diff', 1, ('period',), {}),
    'scale': ('ts_scale', 1, ('period', 'constant'), {'constant': 0}),
    'zscore': ('ts_zscore', 1, ('period', 'ddof'), {'ddof': 0}),
    'decay_linear': ('ts_decay_linear', 1, ('period', 'dense'), {'dense': False}),
    'mean': ('ts_mean', 1, ('period',), {}),
    'sum': ('ts_sum', 1, ('period',), {}),
    'std': ('ts_std_dev', 1, ('period', 'ddof'), {'ddof': 0}),
    'delay': ('ts_delay', 1, ('period',), {}),
    'delta': ('ts_delta', 1, ('period',), {}),
}
OBSERVATION_TS = frozenset(('av_diff', 'scale', 'zscore', 'decay_linear', 'mean', 'sum', 'std', 'delay', 'delta'))
CORE_TS = set(TS) - OBSERVATION_TS
CS = {
    'densify': ('densify', 1, (), {}),
    'quantile': ('quantile', 1, ('driver', 'sigma'), {'driver': 'gaussian', 'sigma': 1}),
    'winsorize': ('winsorize', 1, ('std',), {'std': 4}),
    'normalize': ('normalize', 1, ('useStd', 'limit'), {'useStd': False, 'limit': 0}),
    'scale': ('scale', 1, ('scale', 'longscale', 'shortscale'), {'scale': 1}),
    'rank_unit': ('rank', 1, ('rate',), {'rate': 2}),
    'zscore': ('zscore', 1, (), {}),
}
GROUP = {'backfill':'group_backfill', 'mean':'group_mean', 'neutralize':'group_neutralize',
         'rank':'group_rank', 'scale':'group_scale', 'zscore':'group_zscore'}
VECTOR = {'avg':'vec_avg', 'sum':'vec_sum'}
TRANSFORM = {'bucket':'bucket', 'trade_when':'trade_when'}
_CROSS_KERNELS = {
    **CS,
    'bucket': ('bucket', 1, ('range', 'buckets', 'skipBegin', 'skipEnd', 'skipBoth', 'NaNGroup'), {}),
    'trade_when': ('trade_when', 3, (), {}),
    'group_backfill': ('group_backfill', 2, ('period', 'std'), {'std':4}),
    'group_mean': ('group_mean', 3, (), {}),
    'group_neutralize': ('group_neutralize', 2, (), {}),
    'group_rank': ('group_rank', 2, (), {}),
    'group_scale': ('group_scale', 2, (), {}),
    'group_zscore': ('group_zscore', 2, (), {}),
    'abs': ('abs', 1, (), {}),
}
VARIADIC = {'add', 'subtract', 'multiply', 'min', 'max'}

def literal(compiler, node):
    if isinstance(node, ast.Constant) and type(node.value) in (str, bool, int, float):
        value = node.value
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == compiler.self_name:
        value = compiler.attributes.get(node.attr)
        if type(value) not in (str, bool, int, float):
            raise compiler.error(node, 'operator options must be plain literal constructor values')
    else:
        value = compiler.scalar.expression(node)
    if type(value) not in (str, bool, int, float) or isinstance(value, float) and not math.isfinite(value):
        raise compiler.error(node, 'operator options must be finite literals')
    return value

def prepare_core_call(compiler, domain, method, syntax, variables):
    values = []
    if domain == 'gp':
        if method == 'mean':
            if len(syntax.args) != 2 or any(k.arg != 'weight' for k in syntax.keywords) or len(syntax.keywords)>1:
                raise compiler.error(syntax, 'self.gp.mean(x, g, *, weight=1) requires exactly x and g; weight is keyword-only')
            weight=syntax.keywords[0].value if syntax.keywords else ast.copy_location(ast.Constant(value=1),syntax)
            syntax=ast.copy_location(ast.Call(func=syntax.func,args=[syntax.args[0],weight,syntax.args[1]],keywords=[]),syntax)
        method=GROUP[method]
    elif domain == 'vc':method=VECTOR[method]
    elif domain == 'tf':method=TRANSFORM[method]
    domain='ts' if domain=='ts' else 'cs'
    if domain == 'cs' and method in VARIADIC | set(VECTOR.values()):
        if method in VECTOR.values() and len(syntax.args) == 1 and isinstance(syntax.args[0], (ast.Tuple, ast.List)):
            expressions = syntax.args[0].elts
        else:
            expressions = syntax.args
        minimum = 1 if method in VECTOR.values() else 2
        if len(expressions) < minimum:
            raise compiler.error(syntax, method + ' has too few input expressions')
        values = [compiler.value(compiler.expression(arg, variables), arg) for arg in expressions]
        options = {}
        for keyword in syntax.keywords:
            if method not in {'add', 'subtract', 'multiply'} or keyword.arg != 'filter' or 'filter' in options:
                raise compiler.error(syntax, 'unsupported/repeated option for ' + method)
            options['filter'] = literal(compiler, keyword.value)
        name, depth = method, 1
    else:
        name, count, parameters, defaults = (TS if domain == 'ts' else _CROSS_KERNELS)[method]
        if len(syntax.args) < count:
            raise compiler.error(syntax, method + ' requires ' + str(count) + ' data expressions')
        values = [compiler.value(compiler.expression(arg, variables), arg) for arg in syntax.args[:count]]
        supplied = syntax.args[count:]
        if len(supplied) > len(parameters):
            raise compiler.error(syntax, 'too many options for ' + method)
        options = dict(zip(parameters, [literal(compiler, arg) for arg in supplied]))
        for keyword in syntax.keywords:
            key = keyword.arg
            if key not in parameters + ('clock',) or key in options or domain != 'ts' and key == 'clock':
                raise compiler.error(syntax, 'unsupported/repeated option ' + str(keyword.arg))
            options[key] = literal(compiler, keyword.value)
        options = {**defaults, **options}
        if domain == 'ts' and method == 'backfill':
            if 'lookback' not in options:
                raise compiler.error(syntax, 'backfill requires a lookback')
            options['period'] = options.pop('lookback')
        if domain == 'ts':options.setdefault('clock', 'valid_observations' if method in OBSERVATION_TS else 'trading_days')
        if 'period' in parameters and 'period' not in options:
            raise compiler.error(syntax, method + ' requires a period/lookback')
        if name == 'bucket' and sum(key in options for key in ('range', 'buckets')) != 1:
            raise compiler.error(syntax, 'bucket requires exactly one of range or buckets')
        if domain == 'cs' and name == 'group_backfill' and 'period' not in options:
            raise compiler.error(syntax, 'group_backfill requires a period')
        period = options.get('period', 1)
        if type(period) is not int or period < (0 if name in ('ts_delay', 'ts_delta') else 1) or period > 100000:
            raise compiler.error(syntax, 'period must be a bounded positive integer (delay/delta allow zero)')
        lag = options.get('lag', 0)
        if type(lag) is not int or not 0 <= lag <= 100000:
            raise compiler.error(syntax, 'lag must be a bounded nonnegative integer')
        depth = period + (1 if name in ('ts_delay', 'ts_delta') else 0) + lag
        if domain == 'cs' and name != 'group_backfill': depth = 1
        if name in ('hump', 'days_from_last_change', 'ts_step'): depth = 1
    text_options = {'driver', 'ignore', 'range', 'buckets', 'clock'}
    bool_options = {'filter', 'dense', 'useStd', 'skipBegin', 'skipEnd', 'skipBoth', 'NaNGroup'}
    int_options = {'period', 'k', 'lag', 'rettype', 'ddof', 'rate'}
    for key, option in options.items():
        expected = str if key in text_options else bool if key in bool_options else int if key in int_options else None
        if expected is not None and type(option) is not expected:
            raise compiler.error(syntax, key + ' has an invalid literal type')
        if expected is None and (type(option) not in (int, float) or not math.isfinite(option)):
            raise compiler.error(syntax, key + ' must be a finite number')
    for key in ('k', 'lag', 'rettype', 'ddof'):
        if key in options and (type(options[key]) is not int or options[key] < (1 if key == 'k' else 0)):
            raise compiler.error(syntax, key + ' must be a valid integer')
    for key in ('filter', 'dense', 'useStd', 'skipBegin', 'skipEnd', 'skipBoth', 'NaNGroup'):
        if key in options and type(options[key]) is not bool: raise compiler.error(syntax, key + ' must be bool')
    if options.get('driver', 'gaussian') not in ('gaussian', 'uniform', 'cauchy'):
        raise compiler.error(syntax, 'unknown quantile distribution')
    if options.get('clock', 'trading_days') not in ('trading_days', 'valid_observations'):
        raise compiler.error(syntax, 'unknown temporal clock')
    if options.get('rettype', 0) > 9: raise compiler.error(syntax, 'rettype must be between zero and nine')
    if any(value.kind == 'indices' for value in values): raise compiler.error(syntax, 'operators cannot consume index lists')
    compiler.used_cross_section = True
    value = compiler.node('brain_' + domain + '_' + name, tuple(value.node for value in values), field=json.dumps(options, sort_keys=True), value=options.get('period', 1))
    compiler.axes[value.node] = 'full'
    compiler.warmups[value.node] = max((compiler.warmups[item.node] for item in values), default=1) + depth - 1
    return value
