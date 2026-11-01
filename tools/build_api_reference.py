"""Build NachOS's API reference with pdoc: a page for each public module, from its docstrings.

A public module is one whose name has no part that starts with an underscore, and each package's page lists those
under it, through the template in `api_reference_templates`. The pages go to `build/api-reference`, or to `--output`.
Runs without StarCraft II::

    uv run python tools/build_api_reference.py

The `api-reference` workflow builds it on every pull request that changes the source, and publishes it to GitHub Pages
at each release.
"""

import argparse
import pkgutil
import warnings
from pathlib import Path

import pdoc
import pdoc.render

import sc2nachos

DEFAULT_OUTPUT = Path(__file__).parents[1] / "build" / "api-reference"
TEMPLATES = Path(__file__).parent / "api_reference_templates"


def public_modules() -> list[str]:
    """The names of NachOS's public modules, the package first."""
    names = ["sc2nachos"]
    for module in pkgutil.walk_packages(sc2nachos.__path__, "sc2nachos."):
        if not any(part.startswith("_") for part in module.name.split(".")):
            names.append(module.name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="the folder to write the pages to")
    arguments = parser.parse_args()
    # An annotation naming a type variable, or a type imported only for the type checker, cannot be evaluated, and
    # pdoc shows it as written.
    warnings.filterwarnings("ignore", message="Error parsing type annotation", category=UserWarning)
    pdoc.render.configure(
        docformat="markdown", footer_text=f"NachOS {sc2nachos.__version__}", template_directory=TEMPLATES
    )
    pdoc.pdoc(*public_modules(), output_directory=arguments.output)


if __name__ == "__main__":
    main()
