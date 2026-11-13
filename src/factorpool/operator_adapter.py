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

    def graph_update(self, graph, time, columns, present, fresh=False):
        return self.native.update(graph, time, columns, present)

    def graph_snapshot(self, graph):
        return self.native.snapshot(graph)

    def graph_snapshot_columns(self, graph):
        return self.native.snapshot_columns(graph)

    def graph_evaluate_many(self, graph, *args):
        return self.native.evaluate_matrix(graph, *args)

    @staticmethod
    def native_vector_export(value):
        return value


class SharedGraphBinding:
    """Pool buffer conversion around the existing pinned shared Graph object."""
    def __init__(self, original):
        import _factorpool_operators
        self.original, self.native = original, _factorpool_operators

    def __getattr__(self, name):
        return getattr(self.original, name)

    def graph_set_symbols(self, graph, symbols):
        return self.native.shared_set_symbols(graph, symbols)

    def graph_update(self, graph, time, columns, present, fresh=False):
        return self.native.shared_update(graph, time, columns, present, fresh)

    def graph_snapshot(self, graph):
        return self.original.graph_snapshot(graph)

    def graph_snapshot_columns(self, graph):
        return self.native.shared_snapshot_columns(graph)

    def graph_evaluate_many(self, graph, *args):
        return self.native.evaluate_matrix(graph, *args)

    def native_vector_export(self, value):
        return value if isinstance(value, (bytes, bytearray, memoryview)) else self.original.native_vector_export(value)


def select_operator_binding(indicator, default_binding):
    ir = getattr(indicator, "_pool_ir", None)
    return PoolOperatorBinding(ir) if ir is not None else SharedGraphBinding(default_binding)
