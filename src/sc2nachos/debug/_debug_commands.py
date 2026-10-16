"""Drawing on the game's screen, cheats and debug commands, sent after each turn's orders."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos.debug._cheat import Cheat
from sc2nachos.debug._drawing import _Anchor, _Color, _draw_command, _Drawing, _Shape, _Spec
from sc2nachos.geometry._footprint import _is_count
from sc2nachos.geometry._point import coordinates
from sc2nachos.units import Alliance, Unit

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sc2nachos._game import _Game
    from sc2nachos.geometry import PointLike, Rectangle
    from sc2nachos.ids import UnitTypeId

_WHITE: _Color = (255, 255, 255)
# A unit created for player 0 belongs to the map (in game).
_NEUTRAL_PLAYER = 0
_VITALS = debug_pb2.DebugSetUnitValue.UnitValue


@final
class DebugCommands:
    """Drawing on the game's screen, cheats and debug commands, all sent after the turn's orders in one debug request.
    The game takes them from a client that joined the game as from the one that created it (in game).

    A drawing lasts the `steps` it is given from the turn it is made in, at least that turn. It is sent at most once
    every `refresh_steps` steps, and the screen keeps the last drawing sent meanwhile, so one made between two
    refreshes waits for the next, and one that ends between them still shows until then. A refresh whose drawing is
    what was last sent sends nothing; a turn with a cheat or a command goes out at once, with the drawing as it
    stands then. A drawing at a unit follows the unit, read where it stands at each refresh.

    Everything starts afresh with each game. Every method raises `NotPlayingError` until the first game starts.
    """

    __slots__ = (
        "_cheats",
        "_commands",
        "_current_game",
        "_drawings",
        "_game",
        "_last_sent",
        "_refresh_steps",
        "_shown",
        "_toggled",
    )

    def __init__(self, current_game: Callable[[], _Game]) -> None:
        """Debug commands for the game `current_game` returns, which raises `NotPlayingError` before the first."""
        self._current_game = current_game
        self._refresh_steps = 1
        self._game: _Game | None = None
        self._start(None)

    def _start(self, game: _Game | None) -> None:
        """Forget everything of the last game, and keep to `game`."""
        self._game = game
        self._drawings: list[_Drawing] = []
        # What the screen shows, as last sent, and the step it was sent at.
        self._shown: list[_Spec] = []
        self._last_sent: int | None = None
        self._commands: list[debug_pb2.DebugCommand] = []
        self._cheats: set[Cheat] = set()
        # The cheats the turn switched, each sent once.
        self._toggled: set[Cheat] = set()

    def _playing(self) -> _Game:
        """The game being played, everything of an earlier one forgotten."""
        game = self._current_game()
        if game is not self._game:
            self._start(game)
        return game

    @property
    def refresh_steps(self) -> int:
        """The fewest steps between two drawings sent, 1 unless set, which stays from one game to the next. Set to
        anything but a whole number of steps, at least 1, it raises `ValueError`."""
        return self._refresh_steps

    @refresh_steps.setter
    def refresh_steps(self, steps: int) -> None:
        if not _is_count(steps) or steps < 1:
            raise ValueError(f"drawings are refreshed every whole number of steps, at least 1, not {steps!r}")
        self._refresh_steps = steps

    # Drawing.

    def draw_text(
        self, text: str, *, at: PointLike | Unit[Any], color: _Color = _WHITE, size: int = 8, steps: int = 1
    ) -> None:
        """Write `text` in the world at `at`, `size` pixels high.

        A point of two coordinates is drawn on the ground, as `api.map.height_at` gives it, which raises `IndexError`
        outside the playable area, and one of three at its own height. A unit is drawn where it stands. `color` is
        red, green and blue, each from 0 to 255, and raises `ValueError` otherwise, as does a `steps` under 1.
        """
        game = self._playing()
        self._add("text", (self._anchor(game, at),), color, steps, game, text=text, size=_checked_size(size))

    def draw_text_on_screen(
        self, text: str, *, at: tuple[float, float], color: _Color = _WHITE, size: int = 8, steps: int = 1
    ) -> None:
        """Write `text` on the screen at `at`, from 0 to 1 across and down whatever the window's size, `size` pixels
        high. Raises `ValueError` for a coordinate outside 0 to 1, and as `draw_text` does."""
        game = self._playing()
        x, y = at
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError(f"a point on the screen runs from 0 to 1 each way, not {at!r}")
        self._add("screen_text", ((x, y, 0.0),), color, steps, game, text=text, size=_checked_size(size))

    def draw_line(
        self, start: PointLike | Unit[Any], end: PointLike | Unit[Any], *, color: _Color = _WHITE, steps: int = 1
    ) -> None:
        """Draw a line from `start` to `end`, each placed as `draw_text` places its point."""
        game = self._playing()
        self._add("line", (self._anchor(game, start), self._anchor(game, end)), color, steps, game)

    def draw_box(self, rectangle: Rectangle, *, color: _Color = _WHITE, steps: int = 1) -> None:
        """Draw a box over `rectangle`, from the ground at its center up 1. Raises as `draw_text` does."""
        game = self._playing()
        ground = game.game_map.height_at(rectangle.center)
        low = (rectangle.left, rectangle.bottom, ground)
        high = (rectangle.right, rectangle.top, ground + 1)
        self._add("box", (low, high), color, steps, game)

    def draw_sphere(
        self, center: PointLike | Unit[Any], radius: float, *, color: _Color = _WHITE, steps: int = 1
    ) -> None:
        """Draw a sphere of `radius` around `center`, placed as `draw_text` places its point. Raises `ValueError` for
        a radius that is not more than 0, and as `draw_text` does."""
        game = self._playing()
        if not radius > 0 or not math.isfinite(radius):
            raise ValueError(f"a sphere's radius is more than 0, not {radius!r}")
        self._add("sphere", (self._anchor(game, center),), color, steps, game, size=float(radius))

    def _anchor(self, game: _Game, at: PointLike | Unit[Any]) -> _Anchor:
        """Where to draw at `at`: a unit itself, or a point fixed now, on the ground unless it has a height."""
        if isinstance(at, Unit):
            return at
        point = coordinates(at)
        if len(point) == 3:
            return (float(point[0]), float(point[1]), float(point[2]))
        return (float(point[0]), float(point[1]), game.game_map.height_at(point))

    def _add(
        self,
        shape: _Shape,
        anchors: tuple[_Anchor, ...],
        color: _Color,
        steps: int,
        game: _Game,
        *,
        text: str = "",
        size: float = 0.0,
    ) -> None:
        if not _is_count(steps) or steps < 1:
            raise ValueError(f"a drawing lasts a whole number of steps, at least 1, not {steps!r}")
        drawing = _Drawing(shape, anchors, _checked_color(color), game.step + steps, text=text, size=size)
        self._drawings.append(drawing)

    # Cheats.

    @property
    def cheats(self) -> frozenset[Cheat]:
        """The cheats this player has turned on in this game, this turn's included. Each is on for the whole game, and
        in a game of two the other player can switch it too, which this does not see (in game)."""
        self._playing()
        return frozenset(self._cheats)

    def turn_on(self, cheat: Cheat) -> None:
        """Turn `cheat` on for the rest of the game. One already on stays on, and nothing is sent."""
        self._playing()
        if cheat not in self._cheats:
            self._cheats.add(cheat)
            self._toggled ^= {cheat}

    def turn_off(self, cheat: Cheat) -> None:
        """Turn `cheat` off. One already off stays off, and nothing is sent."""
        self._playing()
        if cheat in self._cheats:
            self._cheats.discard(cheat)
            self._toggled ^= {cheat}

    def give_minerals(self) -> None:
        """Give this player 5000 minerals (in game)."""
        self._command(debug_pb2.DebugCommand(game_state=debug_pb2.minerals))

    def give_resources(self) -> None:
        """Give this player 5000 minerals and 5000 vespene (in game)."""
        self._command(debug_pb2.DebugCommand(game_state=debug_pb2.all_resources))

    def give_upgrades(self) -> None:
        """Grant this player the next level of every upgrade of every race: the researches and the first levels the
        first time, the second levels the next, and campaign upgrades with them (in game)."""
        self._command(debug_pb2.DebugCommand(game_state=debug_pb2.upgrade))

    # Commands.

    def create_units(
        self, unit_type: UnitTypeId, *, at: PointLike, count: int = 1, owner: Alliance = Alliance.OWN
    ) -> None:
        """Create `count` units of `unit_type` at `at` for `owner`: this player, the one player it plays against, or
        the map, whose units read owner 16. The game can make them a few steps late, beside `at`, or not at all, as
        for a structure on ground it cannot stand on (in game).

        Raises `ValueError` for a count under 1, for `Alliance.ALLY`, and for `Alliance.ENEMY` in a game without one
        other player.
        """
        game = self._playing()
        if not _is_count(count) or count < 1:
            raise ValueError(f"units are created a whole number at a time, at least 1, not {count!r}")
        point = coordinates(at)
        create = debug_pb2.DebugCreateUnit(
            unit_type=unit_type,
            owner=_player(game, owner),
            pos=common_pb2.Point2D(x=point[0], y=point[1]),
            quantity=count,
        )
        self._command(debug_pb2.DebugCommand(create_unit=create))

    def kill(self, units: Unit[Any] | Iterable[Unit[Any]]) -> None:
        """Kill `units`."""
        self._playing()
        tags = [units.tag] if isinstance(units, Unit) else [unit.tag for unit in units]
        if tags:
            self._command(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=tags)))

    def set_vitals(
        self,
        units: Unit[Any] | Iterable[Unit[Any]],
        *,
        life: float | None = None,
        energy: float | None = None,
        shields: float | None = None,
    ) -> None:
        """Set the life, energy or shields given of each of `units`.

        Raises `ValueError` for a value of 0 or less, which the game would set to the most the unit can have instead
        (in game): `kill` takes a unit's life.
        """
        self._playing()
        given = {_VITALS.Life: life, _VITALS.Energy: energy, _VITALS.Shields: shields}
        values = {vital: value for vital, value in given.items() if value is not None}
        for value in values.values():
            if not value > 0:
                raise ValueError(f"a vital set to {value!r} would be set to its most instead; set it above 0")
        tags = [units.tag] if isinstance(units, Unit) else [unit.tag for unit in units]
        for tag in tags:
            for vital, value in values.items():
                set_value = debug_pb2.DebugSetUnitValue(unit_value=vital, value=value, unit_tag=tag)
                self._command(debug_pb2.DebugCommand(unit_value=set_value))

    def declare_victory(self) -> None:
        """End the game in this player's victory, by the next step (in game)."""
        end = debug_pb2.DebugEndGame(end_result=debug_pb2.DebugEndGame.DeclareVictory)
        self._command(debug_pb2.DebugCommand(end_game=end))

    def surrender(self) -> None:
        """End the game in this player's defeat, by the next step, as a game ends: unlike `api.leave_game`, the result
        is reported (in game)."""
        end = debug_pb2.DebugEndGame(end_result=debug_pb2.DebugEndGame.Surrender)
        self._command(debug_pb2.DebugCommand(end_game=end))

    def _command(self, command: debug_pb2.DebugCommand) -> None:
        self._playing()
        self._commands.append(command)

    # Sending.

    def _send(self, game: _Game) -> None:
        """Send the turn's cheats and commands, and the drawing if it is due and changed, in one debug request, or
        nothing if neither is to go out."""
        if game is not self._game:
            self._start(game)
        step = game.step
        self._drawings = [drawing for drawing in self._drawings if step < drawing.until]
        commands = [
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.ValueType(cheat))
            for cheat in sorted(self._toggled)
        ]
        commands += self._commands
        self._toggled, self._commands = set(), []
        due = self._last_sent is None or step - self._last_sent >= self._refresh_steps
        if not commands and not due:
            return
        specs = [drawing.spec() for drawing in self._drawings]
        if not commands and specs == self._shown:
            return
        # Any debug request replaces what the screen shows, so the drawing goes with the commands.
        if specs or self._shown:
            commands.append(_draw_command(specs))
        game.client.debug(commands)
        self._shown, self._last_sent = specs, step


def _checked_color(color: _Color) -> _Color:
    """`color`, checked to be red, green and blue, each a whole number from 0 to 255."""
    if len(color) != 3 or not all(_is_count(part) and 0 <= part <= 255 for part in color):
        raise ValueError(f"a color is red, green and blue, each a whole number from 0 to 255, not {color!r}")
    return color


def _checked_size(size: int) -> int:
    if not _is_count(size) or size < 1:
        raise ValueError(f"text is a whole number of pixels high, at least 1, not {size!r}")
    return size


def _player(game: _Game, owner: Alliance) -> int:
    """The id of the player `owner` names in `game`."""
    match owner:
        case Alliance.OWN:
            if (player := game.client.player_id) is None:
                raise ValueError("this player has no id until it joins a game")
            return player
        case Alliance.ENEMY:
            if game.opponent_id is None:
                raise ValueError("this game has no single other player to create units for")
            return game.opponent_id
        case Alliance.NEUTRAL:
            return _NEUTRAL_PLAYER
        case _:
            raise ValueError(f"units are created for this player, its enemy or the map, not {owner!r}")
