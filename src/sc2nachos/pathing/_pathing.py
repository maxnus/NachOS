"""Pathing over the map as it stands now: the game's grid of where ground units walk, paths over it, and distances
the game answers."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, query_pb2

from sc2nachos.gamemap._game_map import _pathing_grid_of
from sc2nachos.geometry import Point, Tile
from sc2nachos.protocol import ProtocolError
from sc2nachos.units import Unit, Units

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.geometry import CheapestPaths, Grid, PointLike, TilePath
    from sc2nachos.protocol import Client


# How far from a structure's position, along each axis, a point in its footprint can lie: half the widest footprint, a
# huge rock's 12 tiles, and the tile it centers on.
_FOOTPRINT_REACH = 7.0


@final
class Pathing:
    """How units get around the map as it stands now, as the game answers."""

    __slots__ = ("_client", "_grid", "_present_units")

    _grid: Grid[bool] | None
    _present_units: Units[Unit[Any]]

    def __init__(self, client: Client) -> None:
        """Pathing that asks the game `client` plays."""
        self._client = client
        self._grid = None
        self._present_units = Units(())

    @property
    def grid(self) -> Grid[bool]:
        """Where a ground unit can walk now, as the game's pathing grid has it at the turn's observation, over the
        playable area, reading false beyond it; read-only. It is asked of the game the first time it is read in a turn,
        and the same grid is handed out for the rest of the turn.

        Every structure in sight blocks the tiles it stands on, this player's and the enemy's, and so does every mineral
        field and geyser in sight; a rock blocks in sight or not. A structure out of sight blocks nothing, its snapshot
        included, nor does a lifted structure, nor a lowered supply depot from some 20 steps after it reads lowered; a
        unit that is no structure blocks nothing, but a sieged tank its own tile (in game).
        """
        if self._grid is None:
            self._grid = _pathing_grid_of(self._client.game_info().start_raw)
        return self._grid

    def cheapest_path(
        self, start: PointLike | Unit[Any], end: PointLike, *, leave_structure: bool = True
    ) -> TilePath | None:
        """The shortest path over `grid` from `start` to `end`, as `Grid.cheapest_path` finds it, or `None` if there is
        none. From a unit, it starts where the unit stands.

        With `leave_structure`, a path whose start lies in the footprint of a structure in sight, or that starts from a
        structure given, leaves it: the footprint is open to it, as the game's pathing query leaves the structure it
        starts in. A rock blocks more than its footprint covers, so a path from its outer tiles stays inside it. A path
        never enters a structure, so an end inside one has none.
        """
        return self._grid_leaving(start, leave_structure).cheapest_path(_point_of(start), end)

    def cheapest_paths_from(
        self, start: PointLike | Unit[Any], *, leave_structure: bool = True, outside: float | None = None
    ) -> CheapestPaths:
        """The shortest path over `grid` from `start` to every tile, as `Grid.cheapest_paths_from` finds them, leaving
        the structure the start lies in as `cheapest_path` does. `outside` is what their lengths read beyond the grid.
        """
        grid = self._grid_leaving(start, leave_structure)
        return grid.cheapest_paths_from(_point_of(start), outside=outside)

    def _grid_leaving(self, start: PointLike | Unit[Any], leave_structure: bool) -> Grid[bool]:
        """`grid`, with the footprint of the structure `start` lies in open if `leave_structure`, on a copy."""
        structure = self._structure_holding(start) if leave_structure else None
        footprint = None if structure is None else structure.footprint
        if structure is None or footprint is None:
            return self.grid
        grid = self.grid.copy()
        grid.fill_footprint(footprint, at=structure.position, value=True)
        return grid

    def _structure_holding(self, start: PointLike | Unit[Any]) -> Unit[Any] | None:
        """The structure on the ground and in sight whose footprint holds `start`: a unit given is its own, if it is
        one. `None` if there is none."""
        if isinstance(start, Unit):
            return start if _stands_on_the_ground(start) else None
        x, y = start[0], start[1]
        tile = Tile.containing(start)
        for unit in self._present_units:
            position = unit.position
            if abs(position.x - x) > _FOOTPRINT_REACH or abs(position.y - y) > _FOOTPRINT_REACH:
                continue
            if not unit.is_visible or not _stands_on_the_ground(unit) or (footprint := unit.footprint) is None:
                continue
            if tile in footprint.tiles(at=position):
                return unit
        return None

    def _update(self, present_units: Units[Unit[Any]]) -> None:
        """Take in a new turn's observation, which reports `present_units`: the grid is asked for again when next
        read."""
        self._grid = None
        self._present_units = present_units

    def query_distance(self, start: PointLike | Unit[Any], end: PointLike) -> float:
        """The pathing distance from `start` to `end`, as `query_distances` asks the game for it for one pair."""
        return self.query_distances([(start, end)])[0]

    def query_distances(self, pairs: Iterable[tuple[PointLike | Unit[Any], PointLike]]) -> list[float]:
        """The pathing distance from each start to its end, asked of the game in one request.

        The game paths over the map as of the turn's observation: a structure in the way is walked around, and a
        lowered supply depot walked over from some 20 steps after the order. From a unit, the distance is that unit's
        own: a reaper's jumps cliffs, and a flying unit's is the straight line. A path leaves a structure its start
        lies in, but never enters one, so an end inside one reads `math.inf`, as does any end no path reaches (in
        game).

        Raises `ValueError` for a unit the last observation did not report.
        """
        requests: list[query_pb2.RequestQueryPathing] = []
        coinciding: list[bool] = []
        for start, end in pairs:
            end_point = Point._rounded_to_float32(end)
            request = query_pb2.RequestQueryPathing(end_pos=common_pb2.Point2D(x=end_point.x, y=end_point.y))
            if isinstance(start, Unit):
                if start.is_stale:
                    raise ValueError(f"the last observation did not report {start!r}, so the game cannot path from it")
                request.unit_tag = start.tag
                start_point = start.position
            else:
                start_point = Point._rounded_to_float32(start)
                request.start_pos.x, request.start_pos.y = start_point.x, start_point.y
            requests.append(request)
            coinciding.append(start_point == end_point)
        if not requests:
            return []
        answers = self._client.query(query_pb2.RequestQuery(pathing=requests)).pathing
        if len(answers) != len(requests):
            raise ProtocolError(f"the game answered {len(answers)} of the {len(requests)} pathing distances asked")
        # The game answers 0 both for no path and from a point to itself (in game).
        return [
            answer.distance if answer.distance > 0 else 0.0 if same else math.inf
            for answer, same in zip(answers, coinciding, strict=True)
        ]


def _point_of(start: PointLike | Unit[Any]) -> PointLike:
    """Where `start` is: the point itself, or where the unit stands."""
    return start.position if isinstance(start, Unit) else start


def _stands_on_the_ground(unit: Unit[Any]) -> bool:
    """Whether `unit` is a structure that is not flying, and so blocks the ground beneath it."""
    return unit.is_structure and not unit.is_flying
