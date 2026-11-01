"""Extensions: what an api given `extensions` builds as the game starts, where their handlers run, and what NachOS's own
extensions may import."""

import ast
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
                api.on(TurnEvent)(self.count)

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
    def test_before_the_bots_of_their_priority_wherever_the_bot_subscribed_and_priorities_first(self) -> None:
        ran: list[str] = []

        class Extension:
            def __init__(self, api: Api[object]) -> None:
                api.on(TurnEvent)(lambda event: ran.append("extension"))
                api.on(TurnEvent, priority=EventPriority.LOW)(lambda event: ran.append("extension, low"))
                api.on(GameStartEvent)(self.subscribe_later)
                self.api = api

            def subscribe_later(self, event: GameStartEvent) -> None:
                self.api.on(TurnEvent)(lambda event: ran.append("extension, later"))

        api = Api(extensions=Extension)
        api.on(TurnEvent, priority=EventPriority.HIGH)(lambda event: ran.append("bot, high"))
        api.on(TurnEvent)(lambda event: ran.append("bot"))
        _play_turns(api, turns=1)

        assert ran == ["bot, high", "extension", "bot", "extension, later", "extension, low"]

    def test_a_bots_order_to_a_unit_an_extension_ordered_at_its_priority_goes_out_and_stands(self) -> None:
        """A unit takes the last unqueued order of a turn."""

        def build_extensions(api: Api[object]) -> None:
            api.on(TurnEvent)(lambda event: api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(3.0, 3.0)))

        api = Api(extensions=build_extensions)

        @api.on(TurnEvent)
        def stop(event: TurnEvent) -> None:
            api.orders.issue(api.units.own[0], AbilityId.STOP)

        game = _play_turns(api, turns=1)

        (sent,) = game.commands_by_turn()[0]
        assert sent.ability_id == AbilityId.STOP


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


def _private_names_imported(source: str, package: str) -> list[tuple[str, str]]:
    """Each module of NachOS `source` imports from, or name it imports, that starts with an underscore."""
    return [
        (module, name)
        for module, name in _modules_imported_by(source, package)
        if module.split(".")[0] == "sc2nachos"
        and (any(part.startswith("_") for part in module.split(".")) or name.startswith("_"))
    ]


def _private_attributes_off_self(path: Path) -> list[str]:
    """Each attribute `path` reads with a leading underscore on anything but `self`, dunders aside."""
    return [
        node.attr
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
        and node.attr.startswith("_")
        and not node.attr.startswith("__")
        and not (isinstance(node.value, ast.Name) and node.value.id == "self")
    ]


@pytest.mark.parametrize("path", sorted(_EXTENSIONS_SOURCE.glob("*.py")), ids=lambda path: path.name)
def test_an_extension_uses_only_what_nachos_makes_public(path: Path) -> None:
    """So it could move to a package of its own unchanged."""
    assert _private_names_imported(path.read_text(encoding="utf-8"), _package_of(path)) == []
    assert _private_attributes_off_self(path) == []


def test_a_relative_import_counts_by_the_module_it_names() -> None:
    source = "from .. import _game\nfrom .._game import _Game\nfrom . import speed_mining\n"
    assert _private_names_imported(source, "sc2nachos.extensions") == [
        ("sc2nachos", "_game"),
        ("sc2nachos._game", "_Game"),
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
