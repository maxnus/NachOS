"""A collection of units."""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast, final, overload

from sc2nachos.geometry import Area, Point, Tile
from sc2nachos.ids import AbilityId, BuffId, UnitTypeId
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._unit import Unit
from sc2nachos.units._unit_type import UnitType
from sc2nachos.units._values import Alliance, Visibility

if TYPE_CHECKING:
    from sc2nachos.geometry import PointLike


# How far from a structure's position, along each axis, a point in its footprint can lie: half the widest footprint, a
# huge rock's 12 tiles, and the tile it centers on.
_FOOTPRINT_REACH = 7.0

# A collection is covariant in its units, as an immutable sequence is: a collection of own units is a collection of
# units, and `own` and `orderable` read one. The type parameter syntax would infer the variance, and infers it
# invariant through those two self types, so the variable declares it.
U = TypeVar("U", bound=Unit[Any], covariant=True)


@final
class Units(Sequence[U]):
    """An immutable, ordered collection of units. Filters keep the order.

    Every filter returns a new collection. A query that picks the nearest unit, such as `closest_to`, raises
    `ValueError` on an empty collection; `structure_at` answers `None` where no structure stands.
    """

    __slots__ = ("_by_id", "_units")

    _units: tuple[U, ...]
    _by_id: dict[int, U] | None

    def __init__(self, units: Iterable[U] = ()) -> None:
        self._units = tuple(units)
        self._by_id = None

    @classmethod
    def combined[V: Unit[Any]](cls, *collections: Iterable[V]) -> Units[V]:
        """Every unit of `collections` in one collection, in order, each unit at its first appearance.

        Units are matched by id, so a unit in two collections appears once, and collections that overlap combine
        cleanly.
        """
        index: dict[int, V] = {}
        for collection in collections:
            for unit in collection:
                if unit.id not in index:
                    index[unit.id] = unit
        combined = Units(index.values())
        combined._by_id = index
        return combined

    def combined_with[V: Unit[Any]](self, units: V | Iterable[V]) -> Units[U | V]:
        """These units and then `units`, one unit or many, each unit at its first appearance, matched by id as
        `combined` matches them."""
        return Units.combined(self._units, _unit_tuple(units))

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
        """The units by id, built on first use."""
        if self._by_id is None:
            self._by_id = {unit.id: unit for unit in self._units}
        return self._by_id

    @property
    def ids(self) -> frozenset[int]:
        """The id of every unit."""
        return frozenset(self._index())

    def by_id(self, unit_id: int) -> U:
        """The unit with `unit_id`. Raises `KeyError` if there is none."""
        return self._index()[unit_id]

    def get(self, unit_id: int) -> U | None:
        """The unit with `unit_id`, or `None` if there is none."""
        return self._index().get(unit_id)

    def with_ids(self, unit_ids: int | Iterable[int]) -> Units[U]:
        """The units whose id is `unit_ids`, one id, or in it, a collection of them."""
        wanted = {unit_ids} if isinstance(unit_ids, int) else set(unit_ids)
        return Units(unit for unit in self._units if unit.id in wanted)

    def without_ids(self, unit_ids: int | Iterable[int]) -> Units[U]:
        """The units whose id is not `unit_ids`, one id, or not in it, a collection of them."""
        unwanted = {unit_ids} if isinstance(unit_ids, int) else set(unit_ids)
        return Units(unit for unit in self._units if unit.id not in unwanted)

    def without(self, units: Unit[Any] | Iterable[Unit[Any]]) -> Units[U]:
        """These units, leaving out `units`, one unit or many, matched by NachOS id, so a structure remembered under a
        new tag still matches."""
        return self.without_ids(unit.id for unit in _unit_tuple(units))

    # By what they are.

    def filter(self, predicate: Callable[[U], bool]) -> Units[U]:
        """The units for which `predicate` is true."""
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
    def of_type(
        self, unit_type: type[UnitType.AnyType] | UnitTypeId, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> Units[U]: ...

    @overload
    def of_type(self, type_ids: Iterable[UnitTypeId], /) -> Units[U]: ...

    def of_type(
        self,
        types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId],
        /,
        *unit_types: type[UnitType.AnyType] | UnitTypeId,
    ) -> Units[Any]:
        """The units of the given types, each a `UnitType`, a group of them, or a `UnitTypeId`, or of the
        `UnitTypeId`s in one collection given alone. Typed as those types when every one is a `UnitType`; the
        collection keeps its type otherwise. Raises `TypeError` for anything else.
        """
        if isinstance(types, UnitTypeId) and not unit_types:
            return Units(unit for unit in self._units if unit.type_id is types)
        wanted = _type_ids(types, unit_types)
        return Units(unit for unit in self._units if unit.type_id in wanted)

    @overload
    def excluding_type(
        self, unit_type: type[UnitType.AnyType] | UnitTypeId, /, *unit_types: type[UnitType.AnyType] | UnitTypeId
    ) -> Units[U]: ...

    @overload
    def excluding_type(self, type_ids: Iterable[UnitTypeId], /) -> Units[U]: ...

    def excluding_type(
        self,
        types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId],
        /,
        *unit_types: type[UnitType.AnyType] | UnitTypeId,
    ) -> Units[U]:
        """The units not of any of the given types, taken as `of_type` takes them."""
        if isinstance(types, UnitTypeId) and not unit_types:
            return Units(unit for unit in self._units if unit.type_id is not types)
        unwanted = _type_ids(types, unit_types)
        return Units(unit for unit in self._units if unit.type_id not in unwanted)

    def _of_alliance(self, alliance: Alliance) -> Units[U]:
        return Units(unit for unit in self._units if unit._raw_alliance == alliance)

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
        """The map's own units."""
        return self._of_alliance(Alliance.NEUTRAL)

    @property
    def enemy(self) -> Units[U]:
        """The enemies' units."""
        return self._of_alliance(Alliance.ENEMY)

    @property
    def structures(self) -> Units[U]:
        """The structures, as `UnitType.Structure` has them: the types the game's tables give the structure
        attribute."""
        return self._in_group(UnitType.Structure)

    @property
    def non_structures(self) -> Units[U]:
        """The units that are not structures, as `UnitType.Structure` has them: larvae, eggs and a sieged tank among
        them, and not a lifted barracks."""
        return self.excluding_type(UnitType.Structure)

    @property
    def workers(self) -> Units[U]:
        """The workers: SCVs, probes and drones, burrowed or not, as `UnitType.Worker` has them. A MULE is none."""
        return self._in_group(UnitType.Worker)

    @property
    def townhalls(self) -> Units[U]:
        """The townhalls, a lifted command center among them, as `UnitType.Townhall` has them."""
        return self._in_group(UnitType.Townhall)

    @property
    def mineral_fields(self) -> Units[U]:
        """The mineral fields of every kind, as `UnitType.AnyMineralField` has them."""
        return self._in_group(UnitType.AnyMineralField)

    @property
    def geysers(self) -> Units[U]:
        """The vespene geysers of every kind, as `UnitType.AnyVespeneGeyser` has them."""
        return self._in_group(UnitType.AnyVespeneGeyser)

    @property
    def gas_buildings(self) -> Units[U]:
        """The refineries, assimilators and extractors, as `UnitType.GasBuilding` has them."""
        return self._in_group(UnitType.GasBuilding)

    def _in_group(self, group: type[UnitType.AnyType]) -> Units[U]:
        """The units of a type in `group`."""
        wanted = group.type_ids
        return Units(unit for unit in self._units if unit.type_id in wanted)

    @property
    def complete(self) -> Units[U]:
        """The units that have finished building or warping in. Raises `NotReportedError` for a unit never shown
        visible."""
        return Units(unit for unit in self._units if unit.is_complete)

    @property
    def not_complete(self) -> Units[U]:
        """The units still building or warping in: those `complete` leaves out. Raises `NotReportedError` as it
        does."""
        return Units(unit for unit in self._units if not unit.is_complete)

    @property
    def alive(self) -> Units[U]:
        """The units not known to be dead, as `Unit.is_alive` has it."""
        return Units(unit for unit in self._units if unit.is_alive)

    @property
    def dead(self) -> Units[U]:
        """The units known to be dead: those `alive` leaves out."""
        return Units(unit for unit in self._units if unit.is_dead)

    @property
    def listed(self) -> Units[U]:
        """The units the last observation listed, as `Unit.is_listed` has it."""
        return Units(unit for unit in self._units if unit.is_listed)

    @property
    def not_listed(self) -> Units[U]:
        """The units the last observation did not list: those `listed` leaves out."""
        return Units(unit for unit in self._units if not unit.is_listed)

    @property
    def visible(self) -> Units[U]:
        """The units the last observation showed visible, as `Unit.is_visible` has it: in it, and seen, not cloaked or
        detected."""
        return Units(unit for unit in self._units if unit.is_visible)

    @property
    def orderable[K: UnitType.AnyType](self: Units[Unit[K]]) -> Units[OwnUnit[K]]:
        """The units this player can order now, as `Unit.is_orderable` has it."""
        return Units(unit for unit in self._units if isinstance(unit, OwnUnit) and unit.is_listed)

    @property
    def ground(self) -> Units[U]:
        """The units on the ground: those not flying, and the structures remembered in the fog, whose flying the game
        does not report, as `Unit.is_ground_structure` takes them."""
        return Units(unit for unit in self._units if _is_on_the_ground(unit))

    @property
    def air(self) -> Units[U]:
        """The units in the air: those `ground` leaves out."""
        return Units(unit for unit in self._units if not _is_on_the_ground(unit))

    @property
    def idle[O: OwnUnit[Any]](self: Units[O]) -> Units[O]:
        """This player's units without orders."""
        return Units(unit for unit in self._units if unit.is_idle)

    def doing[O: OwnUnit[Any]](self: Units[O], abilities: AbilityId | Collection[AbilityId]) -> Units[O]:
        """This player's units whose current order carries out `abilities`, one ability or any of several."""
        return Units(unit for unit in self._units if unit.is_doing(abilities))

    def with_buff(self, buff_ids: BuffId | Collection[BuffId]) -> Units[U]:
        """The units with `buff_ids`, one buff or any of several, among their buffs. Raises `NotReportedError` for a
        unit never shown visible."""
        return Units(unit for unit in self._units if unit.has_buff(buff_ids))

    # By what they can hit.

    def having_in_range(self, target: Unit[Any], *, margin: float = 0.0) -> Units[U]:
        """The units that have `target` in range of a weapon, as `Unit.has_in_range` has it. Raises
        `NotReportedError` as it does."""
        return Units(unit for unit in self._units if unit.has_in_range(target, margin=margin))

    def in_range_of(self, attacker: Unit[Any], *, margin: float = 0.0) -> Units[U]:
        """The units `attacker` has in range of a weapon, as `Unit.has_in_range` has it. Raises `NotReportedError` as
        it does."""
        return Units(unit for unit in self._units if attacker.has_in_range(unit, margin=margin))

    # By where they are. Written as plain loops over each unit's position: measured three times faster than `min`
    # with a key, and faster than building an array of positions, which would have to be rebuilt on every query
    # since the units move.

    def inside(self, areas: Area | Iterable[Area]) -> Units[U]:
        """The units standing in `areas`: in one area, or in any of several."""
        if isinstance(areas, Area):
            return Units(unit for unit in self._units if unit._position in areas)
        regions = tuple(areas)
        return Units(unit for unit in self._units if any(unit._position in region for region in regions))

    def outside(self, areas: Area | Iterable[Area]) -> Units[U]:
        """The units standing in none of `areas`: those `inside` leaves out."""
        if isinstance(areas, Area):
            return Units(unit for unit in self._units if unit._position not in areas)
        regions = tuple(areas)
        return Units(unit for unit in self._units if not any(unit._position in region for region in regions))

    def within(self, distance: float, *, of: PointLike | Unit[Any] | Iterable[PointLike | Unit[Any]]) -> Units[U]:
        """The units at most `distance` from `of`, a point or a unit, or from any of several, measured between
        positions. Raises `ValueError` for a distance that is negative or not a number."""
        if not distance >= 0:
            raise ValueError(f"a distance is at least 0, not {distance}")
        centers = _positions(of)
        distance_squared = distance * distance
        kept = []
        for unit in self._units:
            position = unit._position
            x, y = position[0], position[1]
            for cx, cy in centers:
                dx = x - cx
                dy = y - cy
                if dx * dx + dy * dy <= distance_squared:
                    kept.append(unit)
                    break
        return Units(kept)

    def beyond(self, distance: float, *, of: PointLike | Unit[Any] | Iterable[PointLike | Unit[Any]]) -> Units[U]:
        """The units farther than `distance` from `of`, a point or a unit, and from every one of several: those
        `within` leaves out. Raises `ValueError` as `within` does."""
        return self.without(self.within(distance, of=of))

    def sorted(self, key: Callable[[U], Any], *, reverse: bool = False) -> Units[U]:
        """The units in the order of `key`, ascending unless `reverse`. Units of equal key keep their order."""
        return Units(sorted(self._units, key=key, reverse=reverse))

    def sorted_by_distance_to(self, point_or_unit: PointLike | Unit[Any]) -> Units[U]:
        """The units from nearest to `point_or_unit`, a point or a unit's position, to furthest. Units at equal
        distance keep their order."""
        x, y = _ground_position_of(point_or_unit)
        distances = []
        for unit in self._units:
            position = unit._position
            dx = position[0] - x
            dy = position[1] - y
            distances.append(dx * dx + dy * dy)
        order = sorted(range(len(distances)), key=distances.__getitem__)
        return Units(self._units[index] for index in order)

    def closest_n_to(self, count: int, point_or_unit: PointLike | Unit[Any]) -> Units[U]:
        """The `count` units nearest to `point_or_unit`, a point or a unit's position, nearest first, or all of them if
        there are fewer."""
        if count < 0:
            raise ValueError(f"cannot pick {count} units")
        return self.sorted_by_distance_to(point_or_unit)[:count]

    def closest_to(self, point_or_unit: PointLike | Unit[Any]) -> U:
        """The unit nearest to `point_or_unit`, a point or a unit's position. At equal distance, the first in the
        collection: a unit in it is at distance 0 from itself, and is picked unless another on the same spot comes
        first."""
        if not self._units:
            raise ValueError("no units to choose from")
        x, y = _ground_position_of(point_or_unit)
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

    def closest_distance_to(self, point_or_unit: PointLike | Unit[Any]) -> float:
        """The distance from `point_or_unit`, a point or a unit's position, to the nearest unit's position."""
        if not self._units:
            raise ValueError("no units to measure to")
        x, y = _ground_position_of(point_or_unit)
        nearest = math.inf
        for unit in self._units:
            position = unit._position
            dx = position[0] - x
            dy = position[1] - y
            distance = dx * dx + dy * dy
            if distance < nearest:
                nearest = distance
        return math.sqrt(nearest)

    def structure_at(self, point: PointLike | Tile) -> U | None:
        """The structure on the ground whose footprint holds `point`, a tile reading as its center, or `None` if none of
        these does. Of one in sight and a snapshot over the same ground, the one in sight; a lifted structure covers no
        ground."""
        tile = Tile.at(point)
        x, y = _ground_position_of(tile.center if isinstance(point, Tile) else point)
        remembered = None
        for unit in self._units:
            position = unit._position
            if abs(position[0] - x) > _FOOTPRINT_REACH or abs(position[1] - y) > _FOOTPRINT_REACH:
                continue
            if not unit.is_ground_structure or (footprint := unit.footprint) is None:
                continue
            if tile not in footprint.tiles(at=position):
                continue
            if unit.is_listed and unit.visibility.in_sight:
                return unit
            if remembered is None:
                remembered = unit
        return remembered

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


def _unit_tuple[T: Unit[Any]](units: T | Iterable[T]) -> tuple[T, ...]:
    """The units given, one unit or any number of them, as a tuple."""
    return (units,) if isinstance(units, Unit) else tuple(units)


def _type_ids(
    types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId],
    unit_types: tuple[type[UnitType.AnyType] | UnitTypeId, ...],
) -> frozenset[UnitTypeId]:
    """The ids `types` and `unit_types` stand for, as `of_type` takes them: each a `UnitType` or a group, expanded to
    its types, or a `UnitTypeId`; or a collection of `UnitTypeId`s as `types` alone. Raises `TypeError` for anything
    else."""
    if isinstance(types, type) and issubclass(types, UnitType.AnyType) and not unit_types:
        return types.type_ids
    if unit_types or isinstance(types, UnitTypeId | type | str) or not isinstance(types, Iterable):
        return _unit_type_ids_in((types, *unit_types))
    ids = frozenset(types)
    if not all(isinstance(type_id, UnitTypeId) for type_id in ids):
        raise TypeError(f"a collection of unit types holds UnitTypeIds alone, not {sorted(map(repr, ids))}")
    return ids


def _unit_type_ids_in(unit_types: Iterable[object]) -> frozenset[UnitTypeId]:
    """The ids `unit_types` stand for, each a `UnitTypeId`, or a `UnitType` or a group expanded to its types. Raises
    `TypeError` for anything else, a collection among them."""
    ids: set[UnitTypeId] = set()
    for unit_type in unit_types:
        if isinstance(unit_type, UnitTypeId):
            ids.add(unit_type)
        elif isinstance(unit_type, type) and issubclass(unit_type, UnitType.AnyType):
            ids.update(unit_type.type_ids)
        else:
            raise TypeError(f"a unit type is a UnitTypeId, a UnitType or a group of them, not {unit_type!r}")
    return frozenset(ids)


def _is_on_the_ground(unit: Unit[Any]) -> bool:
    """Whether `unit` stands on the ground: one not flying, and a structure remembered in the fog, whose flying the game
    does not report."""
    return unit.visibility is Visibility.IN_FOG or not unit.is_flying


def _positions(of: PointLike | Unit[Any] | Iterable[PointLike | Unit[Any]]) -> list[tuple[float, float]]:
    """The ground positions of `of`: a point, a unit, or several of either."""
    # A point is a tuple of numbers; several are anything else that iterates.
    if isinstance(of, Unit) or (isinstance(of, tuple) and of and not isinstance(of[0], (tuple, Unit))):
        return [_ground_position_of(cast("PointLike | Unit[Any]", of))]
    several = cast("Iterable[PointLike | Unit[Any]]", of)
    return [_ground_position_of(item) for item in several]


def _ground_position_of(point_or_unit: PointLike | Unit[Any]) -> tuple[float, float]:
    """The ground-plane coordinates of `point_or_unit`: a point, or a unit's position."""
    if isinstance(point_or_unit, Unit):
        return point_or_unit._position[0], point_or_unit._position[1]
    return point_or_unit[0], point_or_unit[1]
