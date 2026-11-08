"""Build NachOS's API reference with pdoc: a page for each public module, from its docstrings.

A public module is one whose name has no part that starts with an underscore, and each package's page lists those
under it, through the template in `api_reference_templates`. A variable a package imports from another module shows
the docstring written after it there. Any warning pdoc gives fails the build, but for the annotations it cannot
evaluate, and so does a `type` alias shown without the docstring written after it. The pages go to
`build/api-reference`, or to `--output`. Runs without StarCraft II::

    uv run python tools/build_api_reference.py

The `api-reference` workflow builds it on every pull request that changes what it is built from, and publishes it to
GitHub Pages at each release.
"""

import argparse
import ast
import importlib
import pkgutil
import types
import warnings
from pathlib import Path

import pdoc.doc
import pdoc.doc_ast
import pdoc.extract
import pdoc.render

import sc2nachos

DEFAULT_OUTPUT = Path(__file__).parents[1] / "build" / "api-reference"
PDOC_TEMPLATE_DIRECTORY = Path(__file__).parent / "api_reference_templates"
# A pdoc specification leaving out every module with a part that starts with an underscore, those pdoc walks into
# itself included: it walks every submodule of a package with no `__all__`.
EXCLUDING_PRIVATE_MODULES = r"!.*\._"


def public_modules() -> list[str]:
    """The names of NachOS's public modules, the package first."""
    names = ["sc2nachos"]
    for module in pkgutil.walk_packages(sc2nachos.__path__, "sc2nachos."):
        if not any(part.startswith("_") for part in module.name.split(".")):
            names.append(module.name)
    return names


def docstring_where_defined(module: types.ModuleType, name: str) -> str:
    """The docstring written after the statement that gives `name` its value in `module`, or in the module `module`
    imports it from, followed through each import. Empty if there is none."""
    docstring = pdoc.doc_ast.walk_tree(module).var_docstrings.get(name)
    if docstring is not None:
        return docstring
    for statement in pdoc.doc_ast.parse(module).body:
        if isinstance(statement, ast.ImportFrom):
            for imported in statement.names:
                if (imported.asname or imported.name) == name:
                    imported_from = "." * statement.level + (statement.module or "")
                    source_module = importlib.import_module(imported_from, module.__package__)
                    return docstring_where_defined(source_module, imported.name)
    return ""


def documented_modules() -> dict[str, pdoc.doc.Module]:
    """pdoc's documentation of each public module, by name, a variable a module imports given the docstring written
    where it is defined."""
    modules: dict[str, pdoc.doc.Module] = {}
    for name in pdoc.extract.walk_specs([*public_modules(), EXCLUDING_PRIVATE_MODULES]):
        module = pdoc.doc.Module.from_name(name)
        for member in module.members.values():
            # pdoc reads a variable's docstring from the module it documents alone (its issue 365).
            if isinstance(member, pdoc.doc.Variable) and not member.docstring:
                member.docstring = docstring_where_defined(module.obj, member.name)
        modules[name] = module
    return modules


def check_type_alias_docstrings(modules: dict[str, pdoc.doc.Module]) -> None:
    """Fail the build if a page shows a `type` alias without the docstring written after its statement, or if no page
    shows an alias that has one."""
    checked_aliases: list[str] = []
    missing: list[str] = []
    for module in modules.values():
        for member in module.members.values():
            if not (isinstance(member, pdoc.doc.Variable) and member.is_type_alias_type):
                continue
            alias = member.default_value
            # Through the alias's own `__module__`, apart from how `documented_modules` found the docstring.
            defining_module = importlib.import_module(alias.__module__)
            written_docstring = pdoc.doc_ast.walk_tree(defining_module).var_docstrings.get(alias.__name__)
            if written_docstring is None:
                continue
            checked_aliases.append(member.fullname)
            if member.docstring != written_docstring:
                missing.append(member.fullname)
    if not checked_aliases:
        raise SystemExit("The API reference shows no `type` alias with a docstring, so nothing checks that one shows")
    if missing:
        raise SystemExit(f"The API reference leaves out the docstring of {', '.join(missing)}")


def write_pages(modules: dict[str, pdoc.doc.Module], output: Path) -> None:
    """Write each module's page, the index and the search index to `output`, as `pdoc.pdoc` does."""
    for module in modules.values():
        page = output / f"{module.fullname.replace('.', '/')}.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(pdoc.render.html_module(module, modules).encode())
    index_page = pdoc.render.html_index(modules)
    if index_page:
        (output / "index.html").write_bytes(index_page.encode())
    search_index = pdoc.render.search_index(modules)
    if search_index:
        (output / "search.js").write_bytes(search_index.encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="the folder to write the pages to")
    arguments = parser.parse_args()
    warnings.filterwarnings("error", category=UserWarning)
    # An annotation naming a type variable, or a type imported only for the type checker, cannot be evaluated, and
    # pdoc shows it as written.
    warnings.filterwarnings("ignore", message="Error parsing type annotation", category=UserWarning)
    pdoc.render.configure(
        docformat="markdown",
        footer_text=f"NachOS {sc2nachos.__version__}",
        template_directory=PDOC_TEMPLATE_DIRECTORY,
    )
    modules = documented_modules()
    check_type_alias_docstrings(modules)
    write_pages(modules, arguments.output)


if __name__ == "__main__":
    main()
