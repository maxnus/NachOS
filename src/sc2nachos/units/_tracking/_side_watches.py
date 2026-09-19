"""What is watched of one side's units, and what was last seen of each."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any, final

from sc2nachos.units._values import VitalType

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.geometry import Area
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units._tracking._tracker_changes import _ComparedChanges
    from sc2nachos.units._unit import Unit

# A vital watched: how to read it, which it is, the value, whether it is to be reached rather than dropped below, and
# the ids last seen on the far side of the value: below it to be reached, at or above it to be dropped below.
type _VitalWatch = tuple[Callable[[raw_pb2.Unit], float | None], VitalType, float, bool, set[int]]


@final
class _SideWatches[U: Unit[Any]]:
    """What is watched of one side's units, and what was last seen of each: the vitals of each type, and areas."""

    __slots__ = (
        "_area_watches",
        "_dropped_keys",
        "_reached_keys",
        "_vital_ids",
        "_vital_watches_by_type",
        "watching",
    )

    def __init__(self) -> None:
        # The keys of the vitals watched to be reached and to be dropped below, each a vital, a value and a unit type,
        # as last given, so that the watches are made anew only when they change.
        self._reached_keys: Collection[tuple[VitalType, float, UnitTypeId]] = ()
        self._dropped_keys: Collection[tuple[VitalType, float, UnitTypeId]] = ()
        # The ids of each vital watch, shared by the unit types it is watched for, and the watches by raw unit type.
        self._vital_ids: dict[tuple[VitalType, float, bool], set[int]] = {}
        self._vital_watches_by_type: dict[int, list[_VitalWatch]] = {}
        self._area_watches: dict[Area, _AreaWatch] = {}
        self.watching = False

    def watch(
        self,
        reached: Collection[tuple[VitalType, float, UnitTypeId]],
        dropped: Collection[tuple[VitalType, float, UnitTypeId]],
        areas: Collection[Area],
    ) -> None:
        """Watch these from now on, keeping what was last seen for what was watched before."""
        # The bus hands over the same sets while its handlers stay the same, sparing a comparison of hundreds a turn.
        if reached is not self._reached_keys or dropped is not self._dropped_keys:
            if reached != self._reached_keys or dropped != self._dropped_keys:
                self._watch_vitals(reached, dropped)
            self._reached_keys, self._dropped_keys = reached, dropped
        _keep_only(self._area_watches, areas)
        self.watching = bool(self._vital_ids or self._area_watches)

    def _watch_vitals(
        self,
        reached: Collection[tuple[VitalType, float, UnitTypeId]],
        dropped: Collection[tuple[VitalType, float, UnitTypeId]],
    ) -> None:
        """Make the vital watches of `reached` and `dropped` anew, keeping the ids of each still watched."""
        kept: dict[tuple[VitalType, float, bool], set[int]] = {}
        by_type: dict[int, list[_VitalWatch]] = {}
        for keys, reaching in ((reached, True), (dropped, False)):
            for vital, value, unit_type in keys:
                if (ids := kept.get((vital, value, reaching))) is None:
                    ids = kept[vital, value, reaching] = self._vital_ids.get((vital, value, reaching), set())
                by_type.setdefault(int(unit_type), []).append((_VITAL_READERS[vital], vital, value, reaching, ids))
        self._vital_ids, self._vital_watches_by_type = kept, by_type

    def compare(self, unit: U, report: raw_pb2.Unit, found: _ComparedChanges[U], *, in_vision: bool) -> None:
        """Record in `found` what `unit`, in sight and reading as `report`, crossed since it was last seen."""
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
                # The rectangle around the area rules out most units for a fraction of what `in` costs.
                if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] and position in area:
                    if unit_id not in inside:
                        inside.add(unit_id)
                        if not watch.fresh:
                            found.entered_area.append((unit, area))
                elif unit_id in inside:
                    inside.discard(unit_id)
                    found.left_area.append((unit, area))

    def end_update(self, dead: list[int]) -> None:
        """Forget the units in `dead`, and count every area watched as watched for a turn."""
        if not self.watching:
            return
        for watched in (*self._vital_ids.values(), *(watch.inside for watch in self._area_watches.values())):
            watched.difference_update(dead)
        for watch in self._area_watches.values():
            watch.fresh = False


@final
class _AreaWatch:
    """An area watched: the ids last seen inside it, the corners of the rectangle around it, and whether it has yet to
    be watched for a turn."""

    __slots__ = ("bounds", "fresh", "inside")

    def __init__(self, area: Area) -> None:
        self.inside: set[int] = set()
        self.fresh = True
        try:
            box = area.bounding_rectangle()
        except ValueError:
            # An area covering nothing, which no unit is ever inside.
            self.bounds: tuple[float, float, float, float] | None = None
        else:
            self.bounds = (box.left, box.bottom, box.right, box.top)


def _keep_only(watches: dict[Area, _AreaWatch], wanted: Collection[Area]) -> None:
    """Keep the watches of the areas of `wanted` only, starting one for each new area."""
    for area in [area for area in watches if area not in wanted]:
        del watches[area]
    for area in wanted:
        if area not in watches:
            watches[area] = _AreaWatch(area)


def _fraction(amount: float, most: float) -> float | None:
    """`amount` as a fraction of `most`, or `None` for a unit with none of it, whose most is 0."""
    return amount / most if most else None


# How to read each vital off a unit's report, `None` for a unit that has none of it.
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
