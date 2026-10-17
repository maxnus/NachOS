"""Pathing distances over the map as it stands now, as the game answers them."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import common_pb2, query_pb2

from sc2nachos.geometry import Point
from sc2nachos.protocol import ProtocolError
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client


@final
class Pathing:
    """How units get around the map as it stands now, as the game answers."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        """Pathing that asks the game `client` plays."""
        self._client = client

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
            end_point = Point._as_sent(end)
            request = query_pb2.RequestQueryPathing(end_pos=common_pb2.Point2D(x=end_point.x, y=end_point.y))
            if isinstance(start, Unit):
                if start.is_stale:
                    raise ValueError(f"the last observation did not report {start!r}, so the game cannot path from it")
                request.unit_tag = start.tag
                start_point = start.position
            else:
                start_point = Point._as_sent(start)
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
