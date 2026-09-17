"""A collection of units."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, final, overload

from sc2nachos.geometry import Point
from sc2nachos.geometry._point import coordinates
from sc2nachos.ids import UnitTypeId
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._unit import Unit
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Alliance

if TYPE_CHECKING:
    from sc2nachos.geometry import Area, PointLike


@final
class Units[U: Unit[Any]](Sequence[U]):
    """Units in a fixed order, which a filter keeps.

    Never changes: every filter answers a new collection. A query that picks one unit, such as `closest_to`, raises
    `ValueError` on an empty collection.
    """

    __slots__ = ("_by_id", "_units")

    _units: tuple[U, ...]
    _by_id: dict[int, U] | None

    def __init__(self, units: Iterable[U] = ()) -> None:
        self._units = tuple(units)
        self._by_id = None

    @classmethod
    def combined[V: Unit[Any]](cls, *collections: Iterable[V]) -> Units[V]:
        """Every unit of `collections` in one collection, in the order they come, each kept where it first appears.

        A unit is the one with its id, so a unit in two of them is in the answer once, and the collections a bot
        keeps of what it has seen combine whether or not they overlap.
        """
        index: dict[int, V] = {}
        for collection in collections:
            for unit in collection:
                if unit.id not in index:
                    index[unit.id] = unit
        combined = Units(index.values())
        combined._by_id = index
        return combined

    def __len__(self) -> int:
        return len(self._units)

    def __iter__(self) -> Iterator[U]:
        return iter(self._units)

    @overload
    def __getitem__(self, index: int) -> U: ...

    @overload
    def __getitem__(self, index: slice) -> Units[U]: ...

    def __getitem__(self, index: int | slice) -> U | Units[U]:
        if isinstance(index, slice):
            return Units(self._units[index])
        return self._units[index]

    def __contains__(self, unit: object) -> bool:
        return unit in self._units

    def __repr__(self) -> str:
        return f"Units({list(self._units)!r})"

    # By id.

    def _index(self) -> dict[int, U]:
        """Every unit filed under its id, built on first use."""
        if self._by_id is None:
            self._by_id = {unit.id: unit for unit in self._units}
        return self._by_id

    @property
    def ids(self) -> frozenset[int]:
        """The id of every unit."""
        return frozenset(self._index())

    def by_id(self, unit_id: int) -> U:
        """The unit with `unit_id`. Raises `KeyError` where there is none."""
        return self._index()[unit_id]

    def get(self, unit_id: int) -> U | None:
        """The unit with `unit_id`, or `None` where there is none."""
        return self._index().get(unit_id)

    def with_ids(self, unit_ids: Iterable[int]) -> Units[U]:
        """The units whose id is one of `unit_ids`."""
        wanted = set(unit_ids)
        return Units(unit for unit in self._units if unit.id in wanted)

    def without_ids(self, unit_ids: Iterable[int]) -> Units[U]:
        """The units whose id is none of `unit_ids`."""
        unwanted = set(unit_ids)
        return Units(unit for unit in self._units if unit.id not in unwanted)

    # By what they are.

    def filter(self, predicate: Callable[[U], bool]) -> Units[U]:
        """The units `predicate` is true of."""
        return Units(unit for unit in self._units if predicate(unit))

    @overload
    def of_type[K: UnitType.AnyType](
        self: Units[OwnUnit[Any]], unit_type: type[K], /, *unit_types: type[K]
    ) -> Units[OwnUnit[K]]: ...

    @overload
    def of_type[K: UnitType.AnyType](
        self: Units[Unit[Any]], unit_type: type[K], /, *unit_types: type[K]
    ) -> Units[Unit[K]]: ...

    @overload
    def of_type(self, type_ids: UnitTypeId | Iterable[UnitTypeId], /) -> Units[U]: ...

    def of_type(
        self,
        types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId],
        /,
        *unit_types: type[UnitType.AnyType],
    ) -> Units[Any]:
        """The units of a `UnitType` or of several, each a type or a group, typed as those.

        Also takes a `UnitTypeId` or several, for a type chosen as the bot runs, and then keeps the collection's type.
        """
        if isinstance(types, UnitTypeId):
            return Units(unit for unit in self._units if unit.type_id is types)
        wanted = _type_ids(types, unit_types)
        return Units(unit for unit in self._units if unit.type_id in wanted)

    @overload
    def excluding_type(self, unit_type: type[UnitType.AnyType], /, *unit_types: type[UnitType.AnyType]) -> Units[U]: ...

    @overload
    def excluding_type(self, type_ids: UnitTypeId | Iterable[UnitTypeId], /) -> Units[U]: ...

    def excluding_type(
        self,
        types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId],
        /,
        *unit_types: type[UnitType.AnyType],
    ) -> Units[U]:
        """The units of neither a `UnitType` nor any of several, each a type or a group, or of no `UnitTypeId` given."""
        if isinstance(types, UnitTypeId):
            return Units(unit for unit in self._units if unit.type_id is not types)
        unwanted = _type_ids(types, unit_types)
        return Units(unit for unit in self._units if unit.type_id not in unwanted)

    def _of_alliance(self, alliance: Alliance) -> Units[U]:
        return Units(unit for unit in self._units if unit.alliance is alliance)

    @property
    def own[K: UnitType.AnyType](self: Units[Unit[K]]) -> Units[OwnUnit[K]]:
        """This player's units."""
        return Units(unit for unit in self._units if isinstance(unit, OwnUnit))

    @property
    def allied(self) -> Units[U]:
        """The units of this player's allies."""
        return self._of_alliance(Alliance.ALLY)

    @property
    def neutral(self) -> Units[U]:
        """The units that belong to the map."""
        return self._of_alliance(Alliance.NEUTRAL)

    @property
    def enemy(self) -> Units[U]:
        """The enemies' units."""
        return self._of_alliance(Alliance.ENEMY)

    @property
    def structures(self) -> Units[U]:
        """The units whose type is a structure."""
        return Units(unit for unit in self._units if unit.is_structure)

    @property
    def complete(self) -> Units[U]:
        """The units that have finished being built, or warping in. Raises `NotReportedError` for one never in sight."""
        return Units(unit for unit in self._units if unit.is_complete)

    @property
    def idle[O: OwnUnit[Any]](self: Units[O]) -> Units[O]:
        """This player's units without orders."""
        return Units(unit for unit in self._units if unit.is_idle)

    # By where they are. Written out as loops over each unit's position, which measured three times quicker than
    # `min` with a key, and quicker than building an array of the positions, which a query would have to build anew
    # each time since the units move.

    def in_area(self, area: Area) -> Units[U]:
        """The units standing in `area`."""
        return Units(unit for unit in self._units if unit._position in area)

    def sorted_by_distance_to(self, point: PointLike) -> Units[U]:
        """The units from the nearest to `point` to the furthest, those at equal distance kept in their order."""
        x, y = _ground(point)
        distances = []
        for unit in self._units:
            position = unit._position
            dx = position[0] - x
            dy = position[1] - y
            distances.append(dx * dx + dy * dy)
        order = sorted(range(len(distances)), key=distances.__getitem__)
        return Units(self._units[index] for index in order)

    def closest(self, count: int, point: PointLike) -> Units[U]:
        """The `count` units nearest to `point`, nearest first, or all of them where there are fewer."""
        if count < 0:
            raise ValueError(f"cannot pick {count} units")
        return self.sorted_by_distance_to(point)[:count]

    def closest_to(self, point: PointLike) -> U:
        """The unit nearest to `point`, the first of them at equal distance."""
        if not self._units:
            raise ValueError("no units to choose from")
        x, y = _ground(point)
        closest = self._units[0]
        nearest = math.inf
        for unit in self._units:
            position = unit._position
            dx = position[0] - x
            dy = position[1] - y
            distance = dx * dx + dy * dy
            if distance < nearest:
                nearest = distance
                closest = unit
        return closest

    def closest_distance_to(self, point: PointLike) -> float:
        """The distance from `point` to the nearest unit's position."""
        if not self._units:
            raise ValueError("no units to measure to")
        x, y = _ground(point)
        nearest = math.inf
        for unit in self._units:
            position = unit._position
            dx = position[0] - x
            dy = position[1] - y
            distance = dx * dx + dy * dy
            if distance < nearest:
                nearest = distance
        return math.sqrt(nearest)

    @property
    def center(self) -> Point:
        """The mean of the units' positions."""
        if not self._units:
            raise ValueError("no units to find the center of")
        x = y = 0.0
        for unit in self._units:
            position = unit._position
            x += position[0]
            y += position[1]
        count = len(self._units)
        return Point((x / count, y / count))


def _type_ids(
    types: type[UnitType.AnyType] | Iterable[UnitTypeId], unit_types: tuple[type[UnitType.AnyType], ...]
) -> frozenset[UnitTypeId]:
    """The types a `UnitType` and further ones name, or the types in an iterable of them."""
    if not isinstance(types, type):
        return frozenset(types)
    if not unit_types:
        return types._type_ids
    return types._type_ids.union(*(unit_type._type_ids for unit_type in unit_types))


def _ground(point: PointLike) -> tuple[float, float]:
    """The ground-plane coordinates of `point`."""
    position = coordinates(point)
    return position[0], position[1]
