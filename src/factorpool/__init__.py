"""Research Indicator frontend over the shared native graph and Pool kernels."""

__all__ = ["PythonIndicator"]


def __getattr__(name):
    # Keep the AST-only configuration CLI independent of native/scientific imports.
    if name in __all__:
        from .entry import PythonIndicator
        return PythonIndicator
    raise AttributeError(name)
