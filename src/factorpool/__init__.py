"""Research Indicator frontend over the shared native graph and Pool kernels."""

__all__ = ["PythonIndicator", "IndicatorDataPoint", "BaseData", "TradeBar", "TradeBarConsolidator"]


def __getattr__(name):
    # Keep the AST-only configuration CLI independent of native/scientific imports.
    if name == 'PythonIndicator':
        from .entry import PythonIndicator
        return PythonIndicator
    if name in ('IndicatorDataPoint', 'BaseData', 'TradeBar'):
        import factortrader
        return getattr(factortrader, name)
    if name == 'TradeBarConsolidator':
        from .indicator_state import native_binding
        native_binding()
        from factortrader.consolidators import TradeBarConsolidator
        return TradeBarConsolidator
    raise AttributeError(name)
