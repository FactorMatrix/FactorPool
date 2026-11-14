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
