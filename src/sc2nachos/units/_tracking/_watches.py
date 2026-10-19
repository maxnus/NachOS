"""The watches on one side's units, and what was last seen of each unit."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import TYPE_CHECKING, Any, final

from sc2nachos.units._values import VitalType

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.geometry import Area
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units._tracking._tracker_changes import _ComparedChanges
    from sc2nachos.units._unit import Unit

# A vital watch: the reader, the vital, the value, whether the value is to be reached (rather than dropped below), and
# the ids of the units last seen on the far side of the value: below it for a reach watch, at or above it for a drop
# watch.
type _VitalWatch = tuple[Callable[[raw_pb2.Unit], float | None], VitalType, float, bool, set[int]]


@final
class _Watches[U: Unit[Any]]:
    """The watches on one side's units, vital values per unit type and areas, and what was last seen of each unit."""

    __slots__ = (
        "_area_watches",
        "_dropped_keys",
        "_reached_keys",
        "_vital_ids",
        "_vital_types",
        "_vital_watches_by_type",
        "watching",
    )

    def __init__(self) -> None:
        # The reach and drop watches as last given, each a vital, a value and a unit type, so the watches are rebuilt
        # only when they change.
        self._reached_keys: Collection[tuple[VitalType, float, UnitTypeId]] = ()
        self._dropped_keys: Collection[tuple[VitalType, float, UnitTypeId]] = ()
        # The unit ids of each vital watch, shared across the unit types it is watched for, those raw unit types, and
        # the watches by raw unit type.
        self._vital_ids: dict[tuple[VitalType, float, bool], set[int]] = {}
        self._vital_types: dict[tuple[VitalType, float, bool], set[int]] = {}
        self._vital_watches_by_type: dict[int, list[_VitalWatch]] = {}
        self._area_watches: dict[Area, _AreaWatch] = {}
        self.watching = False

    def watch(
        self,
        reached: Collection[tuple[VitalType, float, UnitTypeId]],
        dropped: Collection[tuple[VitalType, float, UnitTypeId]],
        areas: Collection[Area],
        *,
        units_by_id: Mapping[int, Unit[Any]],
    ) -> None:
        """Watch these from now on, keeping what was last seen for watches that continue. `units_by_id` holds every
        unit by id, to tell the type of each unit a watch has seen."""
        # The bus passes the same sets while its handlers stay the same, which saves comparing hundreds of keys a turn.
        if reached is not self._reached_keys or dropped is not self._dropped_keys:
            if reached != self._reached_keys or dropped != self._dropped_keys:
                self._watch_vitals(reached, dropped, units_by_id)
            self._reached_keys, self._dropped_keys = reached, dropped
        _keep_only(self._area_watches, areas)
        self.watching = bool(self._vital_ids or self._area_watches)

    def _watch_vitals(
        self,
        reached: Collection[tuple[VitalType, float, UnitTypeId]],
        dropped: Collection[tuple[VitalType, float, UnitTypeId]],
        units_by_id: Mapping[int, Unit[Any]],
    ) -> None:
        """Rebuild the vital watches from `reached` and `dropped`. A watch that continues keeps the unit ids it has seen
        of the types it watched before and still does, so it starts afresh on a type it watches anew."""
        kept: dict[tuple[VitalType, float, bool], set[int]] = {}
        types: dict[tuple[VitalType, float, bool], set[int]] = {}
        by_type: dict[int, list[_VitalWatch]] = {}
        for keys, reaching in ((reached, True), (dropped, False)):
            # Sorted, since a set of keys iterates in an order that differs from one process to the next, and this
            # order is the order of one unit's crossings in a turn.
            for vital, value, unit_type in sorted(keys, key=lambda key: (key[0].name, key[1], key[2])):
                watch = (vital, value, reaching)
                ids = kept.setdefault(watch, set())
                types.setdefault(watch, set()).add(int(unit_type))
                by_type.setdefault(int(unit_type), []).append((_VITAL_READERS[vital], vital, value, reaching, ids))
        for watch, ids in kept.items():
            if (seen := self._vital_ids.get(watch)) is not None:
                still = self._vital_types[watch] & types[watch]
                ids.update(unit_id for unit_id in seen if _type_of(units_by_id, unit_id) in still)
        self._vital_ids, self._vital_types, self._vital_watches_by_type = kept, types, by_type

    def compare(self, unit: U, report: raw_pb2.Unit, found: _ComparedChanges[U], *, in_vision: bool) -> None:
        """Record into `found` what `unit`, in sight with `report`, crossed since it was last seen."""
        unit_id = unit._id
        if in_vision and (watches := self._vital_watches_by_type.get(report.unit_type)) is not None:
            for read, vital, value, reaching, ids in watches:
                if (now := read(report)) is None:
                    continue
                if (now < value) is reaching:
                    ids.add(unit_id)
                elif unit_id in ids:
                    ids.discard(unit_id)
                    (found.vital_reached if reaching else found.vital_dropped).append((unit, vital, value))
        if self._area_watches:
            position = unit._position
            x, y = position
            for area, watch in self._area_watches.items():
                if (bounds := watch.bounds) is None:
                    continue
                inside = watch.inside
                # The area's bounding rectangle rules out most units at a fraction of what `in` costs.
                if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] and position in area:
                    if unit_id not in inside:
                        inside.add(unit_id)
                        if not watch.fresh:
                            found.entered_area.append((unit, area))
                elif unit_id in inside:
                    inside.discard(unit_id)
                    found.left_area.append((unit, area))

    def end_update(self, dead: list[int]) -> None:
        """Forget the units in `dead`, and mark every area watch as no longer fresh."""
        if not self.watching:
            return
        for watched in (*self._vital_ids.values(), *(watch.inside for watch in self._area_watches.values())):
            watched.difference_update(dead)
        for watch in self._area_watches.values():
            watch.fresh = False


@final
class _AreaWatch:
    """A watch on an area: the ids of the units last seen inside it, its bounding rectangle, and whether it has yet to
    run for a full turn."""

    __slots__ = ("bounds", "fresh", "inside")

    def __init__(self, area: Area) -> None:
        self.inside: set[int] = set()
        self.fresh = True
        try:
            box = area.bounding_rectangle()
        except ValueError:
            # An empty area. No unit is ever inside it.
            self.bounds: tuple[float, float, float, float] | None = None
        else:
            self.bounds = (box.left, box.bottom, box.right, box.top)


def _keep_only(watches: dict[Area, _AreaWatch], wanted: Collection[Area]) -> None:
    """Keep only the watches on areas in `wanted`, starting one for each new area."""
    for area in [area for area in watches if area not in wanted]:
        del watches[area]
    for area in wanted:
        if area not in watches:
            watches[area] = _AreaWatch(area)


def _type_of(units_by_id: Mapping[int, Unit[Any]], unit_id: int) -> int | None:
    """The raw type the unit with `unit_id` was last reported as, or `None` if there is none."""
    unit = units_by_id.get(unit_id)
    return None if unit is None else unit._latest_report.unit_type


def _fraction(amount: float, most: float) -> float | None:
    """`amount` as a fraction of `most`, or `None` if `most` is 0, as for a unit without that vital."""
    return amount / most if most else None


# How to read each vital from a unit's report. `None` for a unit that lacks the vital.
_VITAL_READERS: dict[VitalType, Callable[[raw_pb2.Unit], float | None]] = {
    VitalType.HEALTH: lambda report: report.health,
    VitalType.SHIELD: lambda report: report.shield if report.shield_max else None,
    VitalType.LIFE: lambda report: report.health + report.shield,
    VitalType.ENERGY: lambda report: report.energy if report.energy_max else None,
    VitalType.HEALTH_FRACTION: lambda report: _fraction(report.health, report.health_max),
    VitalType.SHIELD_FRACTION: lambda report: _fraction(report.shield, report.shield_max),
    VitalType.LIFE_FRACTION: lambda report: _fraction(
        report.health + report.shield, report.health_max + report.shield_max
    ),
    VitalType.ENERGY_FRACTION: lambda report: _fraction(report.energy, report.energy_max),
}
