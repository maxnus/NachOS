"""Extensions: what an api given `extensions` builds as the game starts, where their handlers run, and what NachOS's own
extensions may import."""

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sc2nachos import Api, NotPlayingError
from sc2nachos.events import EventPriority, GameStartEvent, TurnEvent
from sc2nachos.ids import AbilityId
from sc2nachos.units import Unit
from support import ScriptedGame, make_observation, make_unit

_NACHOS_SOURCE = Path(__file__).parents[1] / "src" / "sc2nachos"
_EXTENSIONS_SOURCE = _NACHOS_SOURCE / "extensions"


def _play_turns(api: Api[object], *, turns: int = 2) -> ScriptedGame:
    """Play `turns` turns of one step with `api`, this player's marine, tag 1, standing on open ground, and return the
    game."""
    game = ScriptedGame([make_observation(step, units=[make_unit(1)]) for step in range(turns + 1)])
    api.play(game.joined())
    return game


@dataclass
class _Built:
    """What a test's extensions saw as they were built."""

    step: int
    units: list[Unit[Any]]


class TestBuildingTheExtensions:
    def test_an_api_without_extensions_holds_none_once_the_game_starts(self) -> None:
        api = Api()
        seen: list[None] = []
        api.on(GameStartEvent)(lambda event: seen.append(api.extensions))

        with pytest.raises(NotPlayingError):
            _ = api.extensions
        _play_turns(api)

        assert seen == [None]
        assert api.extensions is None

    def test_they_are_built_once_with_the_api_after_the_first_observation_and_before_the_game_starts(self) -> None:
        happened: list[str] = []

        def build_extensions(api: Api[object]) -> _Built:
            happened.append("built")
            return _Built(api.step, list(api.units))

        api = Api(extensions=build_extensions)
        api.on(GameStartEvent)(lambda event: happened.append("started"))
        api.on(TurnEvent)(lambda event: happened.append("turn"))

        with pytest.raises(NotPlayingError):
            _ = api.extensions
        _play_turns(api)

        assert happened == ["built", "started", "turn", "turn"]
        assert api.extensions.step == 0
        assert [unit.tag for unit in api.extensions.units] == [1]

    def test_a_class_taking_the_api_builds_them_itself(self) -> None:
        class Recorder:
            def __init__(self, api: Api[object]) -> None:
                self.turns = 0
                api.on(TurnEvent, priority=EventPriority.BEFORE)(self.count)

            def count(self, event: TurnEvent) -> None:
                self.turns += 1

        api = Api(extensions=Recorder)
        _play_turns(api, turns=3)

        assert api.extensions.turns == 3

    def test_reading_them_while_they_are_built_raises(self) -> None:
        def build_extensions(api: Api[object]) -> object:
            return api.extensions

        api = Api(extensions=build_extensions)
        with pytest.raises(RuntimeError, match="not built"):
            _play_turns(api)
        with pytest.raises(RuntimeError, match="not built"):
            _ = api.extensions


class TestWhereTheirHandlersRun:
    def test_before_every_handler_of_the_bots_or_after_every_one_each_in_the_order_they_subscribed(self) -> None:
        """The extension's handler subscribed later, on `GameStartEvent`, runs at a priority of the bot's."""
        ran: list[str] = []

        class Extension:
            def __init__(self, api: Api[object]) -> None:
                api.on(TurnEvent, priority=EventPriority.AFTER)(lambda event: ran.append("extension, after"))
                api.on(TurnEvent, priority=EventPriority.BEFORE)(lambda event: ran.append("extension, before"))
                api.on(TurnEvent, priority=EventPriority.BEFORE)(lambda event: ran.append("extension, before again"))
                api.on(GameStartEvent, priority=EventPriority.BEFORE)(self.subscribe_later)
                self.api = api

            def subscribe_later(self, event: GameStartEvent) -> None:
                self.api.on(TurnEvent)(lambda event: ran.append("extension, later"))

        api = Api(extensions=Extension)
        api.on(TurnEvent, priority=EventPriority.LOWEST)(lambda event: ran.append("bot, lowest"))
        api.on(TurnEvent, priority=EventPriority.HIGHEST)(lambda event: ran.append("bot, highest"))
        api.on(TurnEvent)(lambda event: ran.append("bot"))
        _play_turns(api, turns=1)

        assert ran == [
            "extension, before",
            "extension, before again",
            "bot, highest",
            "bot",
            "extension, later",
            "bot, lowest",
            "extension, after",
        ]

    @pytest.mark.parametrize("priority", [EventPriority.HIGHEST, EventPriority.MEDIUM, EventPriority.LOWEST])
    def test_an_extensions_handler_at_a_priority_of_the_bots_raises(self, priority: EventPriority) -> None:
        api = Api(extensions=lambda api: api.on(TurnEvent, priority=priority)(lambda event: None))
        with pytest.raises(ValueError, match="runs at BEFORE or AFTER"):
            _play_turns(api)

    @pytest.mark.parametrize("priority", [EventPriority.BEFORE, EventPriority.AFTER])
    def test_a_bots_handler_at_an_extensions_priority_raises(self, priority: EventPriority) -> None:
        api = Api()
        with pytest.raises(ValueError, match="while NachOS builds the extensions"):
            api.on(TurnEvent, priority=priority)(lambda event: None)

    def test_a_handler_an_extension_subscribes_later_at_an_extensions_priority_raises(self) -> None:
        def build_extensions(api: Api[object]) -> None:
            def subscribe_later(event: GameStartEvent) -> None:
                api.on(TurnEvent, priority=EventPriority.BEFORE)(lambda turn: None)

            api.on(GameStartEvent, priority=EventPriority.BEFORE)(subscribe_later)

        with pytest.raises(ValueError, match="while NachOS builds the extensions"):
            _play_turns(Api(extensions=build_extensions))

    def test_an_instance_with_a_method_marked_at_an_extensions_priority_is_refused_whole(self) -> None:
        """The method marked first, at a priority of the bot's, is not subscribed either."""
        api = Api()
        ran: list[str] = []

        class Watcher:
            @api.on(TurnEvent)
            def count(self, event: TurnEvent) -> None:
                ran.append("count")

            @api.on(TurnEvent, priority=EventPriority.BEFORE)
            def watch_first(self, event: TurnEvent) -> None:
                ran.append("watch first")

        with pytest.raises(ValueError, match="while NachOS builds the extensions"):
            api.subscribe(Watcher())
        _play_turns(api, turns=1)
        assert ran == []

    @pytest.mark.parametrize(
        ("extensions_priority", "standing"),
        [(EventPriority.BEFORE, AbilityId.STOP), (EventPriority.AFTER, AbilityId.MOVE)],
        ids=["before the bot, the bot's", "after the bot, the extension's"],
    )
    def test_of_an_order_the_bot_and_an_extension_give_a_unit_the_last_goes_out_and_stands(
        self, extensions_priority: EventPriority, standing: AbilityId
    ) -> None:
        """A unit takes the last unqueued order of a turn."""

        def build_extensions(api: Api[object]) -> None:
            api.on(TurnEvent, priority=extensions_priority)(
                lambda event: api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(3.0, 3.0))
            )

        api = Api(extensions=build_extensions)

        @api.on(TurnEvent)
        def stop(event: TurnEvent) -> None:
            api.orders.issue(api.units.own[0], AbilityId.STOP)

        game = _play_turns(api, turns=1)

        (sent,) = game.commands_by_turn()[0]
        assert sent.ability_id == standing


def test_an_api_of_any_extensions_is_taken_where_any_api_is() -> None:
    """Checked by the type checker: a parameter typed `Api[object]` takes an api of any extensions."""

    def takes_any_api(api: Api[object]) -> bool:
        return isinstance(api, Api)

    assert takes_any_api(Api(extensions=lambda api: 3))
    assert takes_any_api(Api())


def _package_of(path: Path) -> str:
    """The dotted name of the package the NachOS module at `path` is in."""
    return ".".join(path.parent.relative_to(_NACHOS_SOURCE.parent).parts)


def _modules_imported_by(source: str, package: str) -> list[tuple[str, str]]:
    """Each module `source`, a module of `package`, imports from, by its absolute name, with the name it imports, or
    `""` for the module itself."""
    imported: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [(alias.name, "") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                module = base if node.module is None else f"{base}.{node.module}"
            else:
                module = node.module or ""
            imported += [(module, alias.name) for alias in node.names]
    return imported


def _extension_of(path: Path) -> str:
    """The dotted name of the extension the module at `path` under `_EXTENSIONS_SOURCE` belongs to: the module itself,
    or the package it is in."""
    first_part = path.relative_to(_EXTENSIONS_SOURCE).parts[0]
    return f"sc2nachos.extensions.{first_part.removesuffix('.py')}"


def _private_names_imported(source: str, package: str, *, extension: str) -> list[tuple[str, str]]:
    """Each module of NachOS `source` imports from, or name it imports, that starts with an underscore, but those of
    `extension`, the extension `source` belongs to."""
    return [
        (module, name)
        for module, name in _modules_imported_by(source, package)
        if module.split(".")[0] == "sc2nachos"
        and module != extension
        and not module.startswith(f"{extension}.")
        and (any(part.startswith("_") for part in module.split(".")) or name.startswith("_"))
    ]


def _private_attributes_not_off_self_or_cls(path: Path) -> list[str]:
    """Each attribute `path` reads with a leading underscore on anything but `self` or `cls`, dunders aside."""
    return [
        node.attr
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
        and node.attr.startswith("_")
        and not node.attr.startswith("__")
        and not (isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"))
    ]


@pytest.mark.parametrize(
    "path",
    sorted(_EXTENSIONS_SOURCE.rglob("*.py")),
    ids=lambda path: path.relative_to(_EXTENSIONS_SOURCE).as_posix(),
)
def test_an_extension_uses_only_what_nachos_makes_public(path: Path) -> None:
    """So it could move to a package of its own unchanged."""
    source = path.read_text(encoding="utf-8")
    assert _private_names_imported(source, _package_of(path), extension=_extension_of(path)) == []
    assert _private_attributes_not_off_self_or_cls(path) == []


def test_a_private_attribute_counts_unless_read_off_self_or_cls(tmp_path: Path) -> None:
    """`cls` reads the extension's own class, as `self` reads its instance."""
    source = tmp_path / "extension.py"
    source.write_text("self._held\ncls._shown\napi._game\nUpgradeInference._shown\nself.__slots__\n", encoding="utf-8")
    assert _private_attributes_not_off_self_or_cls(source) == ["_game", "_shown"]


def test_a_relative_import_counts_by_the_module_it_names() -> None:
    source = (
        "from .. import _shared\nfrom ..api import _game\nfrom ..api._game import _Game\nfrom . import speed_mining\n"
    )
    assert _private_names_imported(source, "sc2nachos.extensions", extension="sc2nachos.extensions.speed_mining") == [
        ("sc2nachos", "_shared"),
        ("sc2nachos.api", "_game"),
        ("sc2nachos.api._game", "_Game"),
    ]


def test_an_extension_package_may_import_its_own_private_modules() -> None:
    source = (
        "from . import _movement_windows\n"
        "from sc2nachos.extensions.upgrade_inference._upgrade_inference import UpgradeInference\n"
        "from sc2nachos.extensions.speed_mining import _SpeedMined\n"
    )
    package = "sc2nachos.extensions.upgrade_inference"
    assert _private_names_imported(source, package, extension=package) == [
        ("sc2nachos.extensions.speed_mining", "_SpeedMined")
    ]


def test_nachos_imports_no_extension() -> None:
    core = [path for path in _NACHOS_SOURCE.rglob("*.py") if _EXTENSIONS_SOURCE not in path.parents]
    importing = [
        path.relative_to(_NACHOS_SOURCE).as_posix()
        for path in core
        if any(
            module.startswith("sc2nachos.extensions")
            for module, _ in _modules_imported_by(path.read_text(encoding="utf-8"), _package_of(path))
        )
    ]
    assert importing == []


def _names_of_nachos_extensions() -> set[str]:
    """The name of each extension of NachOS's own, and every name each makes public."""
    names: set[str] = set()
    for path in _EXTENSIONS_SOURCE.iterdir():
        if path.name.startswith("_"):
            continue
        module = importlib.import_module(f"sc2nachos.extensions.{path.stem}")
        names |= {path.stem, *module.__all__}
    return names


def test_nachos_names_no_extension() -> None:
    """Not even in a docstring: the core says what an extension may do, never which one does it."""
    extension_name = re.compile(r"\b(" + "|".join(sorted(_names_of_nachos_extensions())) + r")\b")
    core = [path for path in _NACHOS_SOURCE.rglob("*.py") if _EXTENSIONS_SOURCE not in path.parents]
    naming = [
        f"{path.relative_to(_NACHOS_SOURCE).as_posix()}: {found.group()}"
        for path in core
        for found in extension_name.finditer(path.read_text(encoding="utf-8"))
    ]
    assert naming == []
