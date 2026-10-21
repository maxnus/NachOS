"""Scaffold tests: the package imports, and its dependencies are present and self-owned."""

import ast
import functools
import importlib
import inspect
import pkgutil
import re
import sys
import typing
from collections.abc import Callable, Iterator

import sc2nachos
from sc2nachos import enemy, match

# A name with a leading underscore, other than an attribute in a dotted path.
_PRIVATE_NAME = re.compile(r"(?<![\w.])_[A-Za-z]\w*")


def test_version_is_exposed() -> None:
    """The package exposes a version string."""
    assert isinstance(sc2nachos.__version__, str)
    assert sc2nachos.__version__


def test_protobuf_protocol_is_importable() -> None:
    """The s2clientprotocol bindings NachOS is built on are installed."""
    from s2clientprotocol import sc2api_pb2

    assert sc2api_pb2.Request is not None


def test_no_dependency_on_burnysc2() -> None:
    """NachOS never imports the library it replaces.

    AvocaDOS installs both during the migration, so an accidental import would go unnoticed until burnysc2 is
    removed.
    """
    import sys

    assert "sc2" not in sys.modules, "importing sc2nachos must not pull in burnysc2"


def test_what_starts_a_game_is_exported_from_the_top_level() -> None:
    """The README's first example needs only `sc2nachos`: the api, the runner, the match vocabulary and the one
    `Api` setting that is an enum."""
    assert sc2nachos.Race is match.Race
    assert sc2nachos.Computer is match.Computer
    assert sc2nachos.Difficulty is match.Difficulty
    assert sc2nachos.AIBuild is match.AIBuild
    assert sc2nachos.Result is match.Result
    assert sc2nachos.UpgradeInference is enemy.UpgradeInference
    assert set(sc2nachos.__all__) >= {"Api", "run_local", "run_ladder", "Race", "Computer", "Difficulty"}


def test_no_public_signature_names_a_private_type() -> None:
    """What every package exports takes, returns and holds only public types. Constructors, which only NachOS calls,
    are left out."""
    leaks = [
        f"{where}: {sorted(names)}"
        for where, text in _public_signatures()
        if (names := set(_PRIVATE_NAME.findall(text)))
    ]
    assert not leaks


def _public_signatures() -> Iterator[tuple[str, str]]:
    """Each public function, method, property and attribute every package exports, with its signature or type."""
    names = ["sc2nachos"] + [
        module.name
        for module in pkgutil.walk_packages(sc2nachos.__path__, "sc2nachos.")
        if not any(part.startswith("_") for part in module.name.split("."))
    ]
    seen: set[int] = set()
    for name in names:
        module = importlib.import_module(name)
        for public in getattr(module, "__all__", ()):
            exported = getattr(module, public)
            if id(exported) in seen:
                continue
            seen.add(id(exported))
            if isinstance(exported, typing.TypeAliasType):
                yield f"{name}.{public}", _alias_value(exported)
            elif inspect.isclass(exported):
                yield from _members(f"{name}.{public}", exported)
            elif callable(exported):
                yield from _signatures(f"{name}.{public}", exported)


def _members(where: str, cls: type) -> Iterator[tuple[str, str]]:
    """The signatures and types of `cls`'s public members, and its `__call__`."""
    for attribute, annotation in vars(cls).get("__annotations__", {}).items():
        if not attribute.startswith("_"):
            yield f"{where}.{attribute}", str(annotation)
    for attribute, value in vars(cls).items():
        if attribute.startswith("_") and attribute != "__call__":
            continue
        if isinstance(value, property):
            value = value.fget
        elif isinstance(value, functools.cached_property):
            value = value.func
        elif isinstance(value, (classmethod, staticmethod)):
            value = value.__func__
        if inspect.isfunction(value):
            yield from _signatures(f"{where}.{attribute}", value)


def _alias_value(alias: typing.TypeAliasType) -> str:
    """What `alias` stands for, as its module's source spells it, unevaluated."""
    assert (module := alias.__module__) is not None
    tree = ast.parse(inspect.getsource(sys.modules[module]))
    (value,) = (
        node.value for node in ast.walk(tree) if isinstance(node, ast.TypeAlias) and node.name.id == alias.__name__
    )
    return ast.unparse(value)


def _signatures(where: str, function: Callable[..., object]) -> Iterator[tuple[str, str]]:
    """The signature of `function`, and of each of its overloads."""
    for each in (function, *typing.get_overloads(function)):
        try:
            yield where, str(inspect.signature(each))
        except (TypeError, ValueError):
            continue
