"""Public example entry points with settings supplied by each isolated test."""
import ast
from pathlib import Path


def example_source(name):
    source = (Path(__file__).resolve().parents[1]/'src/examples/scripts'/f'{name}.py').read_text()
    definition = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'config'
                              for target in node.targets))
    return source.replace(ast.get_source_segment(source, definition.value), '{}', 1)


def result_values(directory):
    """Decode small fixture results through the same bounded matrix contract."""
    from factorpool.result_matrix import decode_axes, decode_chunk, iter_result_chunks, rows_from_chunk
    rows = []
    for _, count, payload, _ in iter_result_chunks(Path(directory)/'values.matrix', validate_values=False):
        if count:
            rows.extend(rows_from_chunk(axes, decode_chunk(payload, axes)))
        else:
            axes = decode_axes(payload)
    return rows
