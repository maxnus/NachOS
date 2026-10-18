"""List every docstring in `src` that runs over more than one line, to check each after a round of doc edits.

Prints the file and the name of each module, class, function and class attribute whose docstring does, one a line, a
module with no name. Runs without StarCraft II::

    uv run python tools/list_multiline_docstrings.py
"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"


def multiline_docstrings(tree: ast.Module) -> Iterator[str]:
    """The names of the module, classes, functions and class attributes in `tree` whose docstring runs over more than
    one line, the module's being empty."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            docstring = ast.get_docstring(node)
            if docstring and len(docstring.splitlines()) > 1:
                yield getattr(node, "name", "")
        if isinstance(node, ast.ClassDef):
            for field, after in zip(node.body, node.body[1:], strict=False):
                if not (isinstance(field, ast.AnnAssign) and isinstance(after, ast.Expr)):
                    continue
                text = after.value.value if isinstance(after.value, ast.Constant) else None
                if isinstance(text, str) and len(text.splitlines()) > 1:
                    yield f"{node.name}.{ast.unparse(field.target)}"


def main() -> None:
    for path in sorted(SRC.rglob("*.py")):
        for name in multiline_docstrings(ast.parse(path.read_text(encoding="utf-8"))):
            print(path.relative_to(SRC.parent), name)


if __name__ == "__main__":
    main()
