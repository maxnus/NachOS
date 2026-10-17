"""Drawing on the game's screen, cheats and debug commands, sent after each turn's orders."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, debug_pb2

from sc2nachos._checks import is_whole_number
from sc2nachos.debug._cheat import Cheat
from sc2nachos.debug._color import Color
from sc2nachos.debug._drawing import (
    Drawing,
    _Anchor,
    _Box,
    _draw,
    _Figure,
    _Line,
    _Rgb,
    _ScreenText,
    _Sphere,
    _Text,
)
from sc2nachos.debug._marker import Marker
from sc2nachos.geometry._point import coordinates
from sc2nachos.units import Alliance, Unit
from sc2nachos.units._units import _unit_tuple

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.gamemap import GameMap
    from sc2nachos.geometry import PointLike, Rectangle
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.protocol import Client

_VITALS = debug_pb2.DebugSetUnitValue.UnitValue
# A cross's arms run along the diagonals, so each end lies this far along both axes per unit of its radius.
_DIAGONAL = math.sqrt(0.5)


@final
class DebugCommands:
    """Drawing on the game's screen, cheats and debug commands for one game, all sent after the turn's orders in one
    debug request. The game takes them from a client that joined the game as from the one that created it (in game).

    Each draw method returns the `Drawing`, which lasts the `steps` it is given from the turn it is made in, at least
    that turn, or until removed if `steps` is `None`. A drawing at a unit follows the unit, read where it stands at each
    refresh, at where it was last seen while out of sight, and ends once the unit is dead.

    The drawing is refreshed at most once every `refresh_steps` steps, and the screen keeps the last drawing sent
    meanwhile, so one made between two refreshes waits for the next, and one that ends between them still shows until
    then. A refresh whose drawing is what was last sent sends nothing; a turn with a cheat or a command goes out at
    once, with the drawing as it stands then.

    A debug request the game refuses, as a ladder's own proxy may, raises `ProtocolError` out of `api.play`.
    """

    __slots__ = (
        "_cheats",
        "_client",
        "_commands",
        "_drawings",
        "_game_map",
        "_last_refresh",
        "_opponent_id",
        "_refresh_steps",
        "_shown",
        "_step",
        "_toggled",
    )

    def __init__(self, client: Client, game_map: GameMap, *, opponent_id: int | None, step: int) -> None:
        """Debug commands for the game `client` plays on `game_map`, against the player `opponent_id`, if there is
        one, from `step`."""
        self._client = client
        self._game_map = game_map
        self._opponent_id = opponent_id
        self._step = step
        self._refresh_steps = 1
        self._drawings: list[Drawing] = []
        # What the screen shows of ours as last sent, if anything, and the step of the last refresh, which sent the
        # drawing or found it unchanged.
        self._shown: debug_pb2.DebugDraw | None = None
        self._last_refresh: int | None = None
        self._commands: list[debug_pb2.DebugCommand] = []
        self._cheats: set[Cheat] = set()
        # The cheats the turn switched, each sent once.
        self._toggled: set[Cheat] = set()

    @property
    def refresh_steps(self) -> int:
        """The fewest steps between two refreshes of the drawing, 1 unless set; a `GameStartEvent` handler sets it for
        every game. Set to anything but a whole number of steps, at least 1, it raises `ValueError`."""
        return self._refresh_steps

    @refresh_steps.setter
    def refresh_steps(self, steps: int) -> None:
        if not is_whole_number(steps) or steps < 1:
            raise ValueError(f"drawings are refreshed every whole number of steps, at least 1, not {steps!r}")
        self._refresh_steps = steps

    # Drawing.

    def draw_text(
        self,
        text: str,
        *,
        at: PointLike | Unit[Any],
        color: Color | _Rgb = Color.WHITE,
        size: int = 8,
        steps: int | None = 1,
    ) -> Drawing:
        """Write `text` in the world at `at`, `size` pixels high.

        A point of two coordinates is drawn on the ground, as `api.map.height_at` gives it, which raises `IndexError`
        outside the playable area, and one of three at its own height. `color` is a `Color`, or red, green and blue,
        each from 0 to 255, and raises `ValueError` otherwise, as does a `steps` under 1.
        """
        drawing = _Text(text, self._anchor(at), _rgb(color), size=_checked_size(size), until=self._until(steps))
        return self._add(drawing)

    def draw_text_on_screen(
        self,
        text: str,
        *,
        at: tuple[float, float],
        color: Color | _Rgb = Color.WHITE,
        size: int = 8,
        steps: int | None = 1,
    ) -> Drawing:
        """Write `text` on the screen at `at`, from 0 to 1 across and down whatever the window's size, `size` pixels
        high. Raises `ValueError` for anything but two coordinates from 0 to 1, and as `draw_text` does."""
        if len(at) != 2 or not (0 <= at[0] <= 1 and 0 <= at[1] <= 1):
            raise ValueError(f"a point on the screen is two coordinates, each from 0 to 1, not {at!r}")
        drawing = _ScreenText(text, at, _rgb(color), size=_checked_size(size), until=self._until(steps))
        return self._add(drawing)

    def draw_line(
        self,
        start: PointLike | Unit[Any],
        end: PointLike | Unit[Any],
        *,
        color: Color | _Rgb = Color.WHITE,
        steps: int | None = 1,
    ) -> Drawing:
        """Draw a line from `start` to `end`, each placed as `draw_text` places its point. Raises as `draw_text`
        does."""
        return self._add(_Line(self._anchor(start), self._anchor(end), _rgb(color), until=self._until(steps)))

    def draw_box(self, rectangle: Rectangle, *, color: Color | _Rgb = Color.WHITE, steps: int | None = 1) -> Drawing:
        """Draw a box over `rectangle`, from the ground at its center up 1. Raises as `draw_text` does."""
        ground = self._game_map.height_at(rectangle.center)
        low = (rectangle.left, rectangle.bottom, ground)
        high = (rectangle.right, rectangle.top, ground + 1)
        return self._add(_Box(low, high, _rgb(color), until=self._until(steps)))

    def draw_sphere(
        self,
        center: PointLike | Unit[Any],
        radius: float,
        *,
        color: Color | _Rgb = Color.WHITE,
        steps: int | None = 1,
    ) -> Drawing:
        """Draw a sphere of `radius` around `center`, placed as `draw_text` places its point. Raises `ValueError` for
        a radius that is not a finite number above 0, and as `draw_text` does."""
        drawing = _Sphere(self._anchor(center), _checked_radius(radius), _rgb(color), until=self._until(steps))
        return self._add(drawing)

    def draw_polygon(
        self,
        center: PointLike | Unit[Any],
        radius: float,
        *,
        sides: int,
        color: Color | _Rgb = Color.WHITE,
        steps: int | None = 1,
    ) -> Drawing:
        """Draw a regular polygon of `sides` straight lines around `center`, placed as `draw_text` places its point,
        its corners `radius` from it, the first due east, all level with the center. Raises `ValueError` for fewer
        than 3 sides and for a radius that is not a finite number above 0, and as `draw_text` does."""
        if not is_whole_number(sides) or sides < 3:
            raise ValueError(f"a polygon has a whole number of sides, at least 3, not {sides!r}")
        radius = _checked_radius(radius)
        corners = [
            (radius * math.cos(math.tau * k / sides), radius * math.sin(math.tau * k / sides)) for k in range(sides)
        ]
        segments = list(zip(corners, corners[1:] + corners[:1], strict=True))
        return self._add(_Figure(self._anchor(center), segments, _rgb(color), until=self._until(steps)))

    def draw_marker(
        self,
        at: PointLike | Unit[Any],
        shape: Marker,
        *,
        radius: float = 0.5,
        color: Color | _Rgb = Color.WHITE,
        steps: int | None = 1,
    ) -> Drawing:
        """Mark `at` with `shape`, its arms reaching `radius` from it, placed as `draw_text` places its point. Raises
        `ValueError` for a radius that is not a finite number above 0, and as `draw_text` does."""
        radius = _checked_radius(radius)
        match shape:
            case Marker.CROSS:
                arm = radius * _DIAGONAL
                segments = [((-arm, -arm), (arm, arm)), ((-arm, arm), (arm, -arm))]
            case Marker.PLUS:
                segments = [((-radius, 0.0), (radius, 0.0)), ((0.0, -radius), (0.0, radius))]
        return self._add(_Figure(self._anchor(at), segments, _rgb(color), until=self._until(steps)))

    def _anchor(self, at: PointLike | Unit[Any]) -> _Anchor:
        """Where to draw at `at`: a unit itself, or a point fixed now, on the ground unless it has a height."""
        if isinstance(at, Unit):
            return at
        point = coordinates(at)
        if len(point) == 3:
            return (float(point[0]), float(point[1]), float(point[2]))
        return (float(point[0]), float(point[1]), self._game_map.height_at(point))

    def _until(self, steps: int | None) -> int | None:
        """The step a drawing made now that lasts `steps` ends at, or `None` if it lasts until removed."""
        if steps is None:
            return None
        if not is_whole_number(steps) or steps < 1:
            raise ValueError(f"a drawing lasts a whole number of steps, at least 1, or until removed, not {steps!r}")
        return self._step + steps

    def _add(self, drawing: Drawing) -> Drawing:
        self._drawings.append(drawing)
        return drawing

    # Cheats.

    @property
    def cheats(self) -> frozenset[Cheat]:
        """The cheats this player has turned on in this game, this turn's included. Each is on for the whole game, and
        in a game of two the other player can switch it too, which this does not see (in game)."""
        return frozenset(self._cheats)

    def turn_on(self, cheat: Cheat) -> None:
        """Turn `cheat` on for the rest of the game. One already on stays on, and nothing is sent."""
        if cheat not in self._cheats:
            self._cheats.add(cheat)
            self._toggled ^= {cheat}

    def turn_off(self, cheat: Cheat) -> None:
        """Turn `cheat` off. One already off stays off, and nothing is sent."""
        if cheat in self._cheats:
            self._cheats.discard(cheat)
            self._toggled ^= {cheat}

    def give_minerals(self) -> None:
        """Give this player 5000 minerals (in game)."""
        self._commands.append(debug_pb2.DebugCommand(game_state=debug_pb2.minerals))

    def give_resources(self) -> None:
        """Give this player 5000 minerals and 5000 vespene (in game)."""
        self._commands.append(debug_pb2.DebugCommand(game_state=debug_pb2.all_resources))

    def give_upgrades(self) -> None:
        """Grant this player the next level of every upgrade of every race: the researches and the first levels the
        first time, the second levels the next, and campaign upgrades with them (in game)."""
        self._commands.append(debug_pb2.DebugCommand(game_state=debug_pb2.upgrade))

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
        if not is_whole_number(count) or count < 1:
            raise ValueError(f"units are created a whole number at a time, at least 1, not {count!r}")
        point = coordinates(at)
        create = debug_pb2.DebugCreateUnit(
            unit_type=unit_type,
            owner=self._player(owner),
            pos=common_pb2.Point2D(x=point[0], y=point[1]),
            quantity=count,
        )
        self._commands.append(debug_pb2.DebugCommand(create_unit=create))

    def kill(self, units: Unit[Any] | Iterable[Unit[Any]]) -> None:
        """Kill `units`."""
        if tags := [unit.tag for unit in _unit_tuple(units)]:
            self._commands.append(debug_pb2.DebugCommand(kill_unit=debug_pb2.DebugKillUnit(tag=tags)))

    def set_vitals(
        self,
        units: Unit[Any] | Iterable[Unit[Any]],
        *,
        life: float | None = None,
        energy: float | None = None,
        shields: float | None = None,
    ) -> None:
        """Set the life, energy or shields given of each of `units`.

        Raises `ValueError` if no vital is given, and for a value that is not a finite number above 0: set to 0, a vital
        goes to the most the unit can have instead (in game). `kill` takes a unit's life.
        """
        given = {_VITALS.Life: life, _VITALS.Energy: energy, _VITALS.Shields: shields}
        values = {vital: value for vital, value in given.items() if value is not None}
        if not values:
            raise ValueError("give the life, the energy or the shields to set")
        for value in values.values():
            if not 0 < value < math.inf:
                raise ValueError(
                    f"a vital is set to a finite number above 0, not {value!r}: set to 0, the game sets it to its most"
                )
        for unit in _unit_tuple(units):
            for vital, value in values.items():
                set_value = debug_pb2.DebugSetUnitValue(unit_value=vital, value=value, unit_tag=unit.tag)
                self._commands.append(debug_pb2.DebugCommand(unit_value=set_value))

    def declare_victory(self) -> None:
        """End the game in this player's victory, by the next step (in game)."""
        end = debug_pb2.DebugEndGame(end_result=debug_pb2.DebugEndGame.DeclareVictory)
        self._commands.append(debug_pb2.DebugCommand(end_game=end))

    def surrender(self) -> None:
        """End the game in this player's defeat, by the next step, as a game ends: unlike `api.leave_game`, the result
        is reported (in game)."""
        end = debug_pb2.DebugEndGame(end_result=debug_pb2.DebugEndGame.Surrender)
        self._commands.append(debug_pb2.DebugCommand(end_game=end))

    def _player(self, owner: Alliance) -> int:
        """The id of the player `owner` names in this game."""
        match owner:
            case Alliance.OWN:
                if (player := self._client.player_id) is None:
                    raise ValueError("this player has no id until it joins a game")
                return player
            case Alliance.ENEMY:
                if self._opponent_id is None:
                    raise ValueError("this game has no single other player to create units for")
                return self._opponent_id
            case Alliance.NEUTRAL:
                # A unit created for player 0 belongs to the map (in game).
                return 0
            case _:
                raise ValueError(f"units are created for this player, its enemy or the map, not {owner!r}")

    # Sending.

    def _update(self, step: int) -> None:
        """Take in that the turn's observation was made at `step`."""
        self._step = step

    def _send(self) -> None:
        """Send the turn's cheats and commands, and the drawing if it is due and changed, in one debug request, or
        nothing if neither is to go out."""
        step = self._step
        self._drawings = [drawing for drawing in self._drawings if drawing._lasts_at(step)]
        commands = [
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.ValueType(cheat))
            for cheat in sorted(self._toggled)
        ]
        commands += self._commands
        self._toggled, self._commands = set(), []
        due = self._last_refresh is None or step - self._last_refresh >= self._refresh_steps
        if not commands and not due:
            return
        draw = _draw(self._drawings)
        shown = draw if draw.ListFields() else None
        if not commands and shown == self._shown:
            self._last_refresh = step
            return
        # Any debug request replaces what the screen shows, so the drawing goes with the commands.
        drawing = shown is not None or self._shown is not None
        if drawing:
            commands.append(debug_pb2.DebugCommand(draw=draw))
        self._client.debug(commands)
        if drawing:
            self._shown, self._last_refresh = shown, step


def _rgb(color: Color | _Rgb) -> _Rgb:
    """`color`'s red, green and blue, checked to be whole numbers from 0 to 255."""
    rgb = color.value if isinstance(color, Color) else color
    if len(rgb) != 3 or not all(is_whole_number(part) and 0 <= part <= 255 for part in rgb):
        raise ValueError(f"a color is red, green and blue, each a whole number from 0 to 255, not {color!r}")
    return rgb


def _checked_size(size: int) -> int:
    if not is_whole_number(size) or size < 1:
        raise ValueError(f"text is a whole number of pixels high, at least 1, not {size!r}")
    return size


def _checked_radius(radius: float) -> float:
    if not 0 < radius < math.inf:
        raise ValueError(f"a radius is a finite number above 0, not {radius!r}")
    return float(radius)
