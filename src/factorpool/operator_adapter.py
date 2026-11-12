"""Small binding protocol adapter for the Pool-owned native expression IR."""


class PoolOperatorBinding:
    backend_name = "pool_native"

    def __init__(self, ir):
        import _factorpool_operators
        self.native = _factorpool_operators
        self.ir = ir

    def graph_create(self, ignored_scalar_ir, symbols):
        return self.native.create(self.ir, symbols)

    def graph_configure_batch(self, graph, ir, dependencies, scalar):
        if dependencies or scalar:
            raise ValueError("Pool operator programs require self-contained typed temporal ports")

    def graph_set_symbols(self, graph, symbols):
        return self.native.set_symbols(graph, symbols)

    def graph_update(self, graph, time, columns, present):
        return self.native.update(graph, time, columns, present)

    def graph_snapshot(self, graph):
        return self.native.snapshot(graph)

    @staticmethod
    def native_vector_export(value):
        return value


def select_operator_binding(indicator, default_binding):
    ir = getattr(indicator, "_pool_ir", None)
    return PoolOperatorBinding(ir) if ir is not None else default_binding
