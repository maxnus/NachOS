"""What is watched of one side's units, and what was last seen of each."""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.geometry import Area
    from sc2nachos.ids import UnitTypeId
    from sc2nachos.units._unit import Unit


@final
class _SideWatches[U: "Unit[Any]"]:
    """What is watched of one side's units, what was last seen of each, and what crossed it in the update being read."""

    __slots__ = (
        "areas",
        "energy",
        "energy_by_type",
        "energy_reached",
        "entered",
        "left",
        "life_dropped",
        "life_dropped_watches",
        "life_reached",
        "life_reached_watches",
        "watching",
    )

    def __init__(self) -> None:
        # The ids last seen below each energy of a unit type, and the same by raw unit type.
        self.energy: dict[tuple[UnitTypeId, float], set[int]] = {}
        self.energy_by_type: dict[int, list[tuple[float, set[int]]]] = {}
        # The ids last seen below each life fraction watched to be reached, and at or above each watched to be dropped
        # below.
        self.life_reached_watches: dict[float, set[int]] = {}
        self.life_dropped_watches: dict[float, set[int]] = {}
        self.areas: dict[Area, _AreaWatch] = {}
        self.watching = False
        self.energy_reached: list[tuple[U, float]] = []
        self.life_reached: list[tuple[U, float]] = []
        self.life_dropped: list[tuple[U, float]] = []
        self.entered: list[tuple[U, Area]] = []
        self.left: list[tuple[U, Area]] = []

    def watch(
        self,
        energy: Collection[tuple[UnitTypeId, float]],
        life_reached: Collection[float],
        life_dropped: Collection[float],
        areas: Collection[Area],
    ) -> None:
        """Watch these from now on, keeping what was last seen for what was watched before."""
        if _keep_only(self.energy, energy, _no_ids):
            self.energy_by_type = {}
            for (unit_type, value), below in self.energy.items():
                self.energy_by_type.setdefault(int(unit_type), []).append((value, below))
        _keep_only(self.life_reached_watches, life_reached, _no_ids)
        _keep_only(self.life_dropped_watches, life_dropped, _no_ids)
        _keep_only(self.areas, areas, _AreaWatch)
        self.watching = bool(self.energy or self.life_reached_watches or self.life_dropped_watches or self.areas)
        self.energy_reached, self.life_reached, self.life_dropped, self.entered, self.left = [], [], [], [], []

    def compare(self, unit: U, report: raw_pb2.Unit, *, in_vision: bool) -> None:
        """Record what `unit`, in sight and reading as `report`, crossed since it was last seen."""
        unit_id = unit._id
        if in_vision:
            if self.energy_by_type and (watches := self.energy_by_type.get(report.unit_type)) is not None:
                energy = report.energy
                for value, below in watches:
                    if energy < value:
                        below.add(unit_id)
                    elif unit_id in below:
                        below.discard(unit_id)
                        self.energy_reached.append((unit, value))
            if (self.life_reached_watches or self.life_dropped_watches) and (
                most := report.health_max + report.shield_max
            ):
                fraction = (report.health + report.shield) / most
                for value, below in self.life_reached_watches.items():
                    if fraction < value:
                        below.add(unit_id)
                    elif unit_id in below:
                        below.discard(unit_id)
                        self.life_reached.append((unit, value))
                for value, above in self.life_dropped_watches.items():
                    if fraction >= value:
                        above.add(unit_id)
                    elif unit_id in above:
                        above.discard(unit_id)
                        self.life_dropped.append((unit, value))
        if self.areas:
            position = unit._position
            x, y = position
            for area, watch in self.areas.items():
                if (bounds := watch.bounds) is None:
                    continue
                inside = watch.inside
                if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] and position in area:
                    if unit_id not in inside:
                        inside.add(unit_id)
                        if not watch.fresh:
                            self.entered.append((unit, area))
                elif unit_id in inside:
                    inside.discard(unit_id)
                    self.left.append((unit, area))

    def settle(self, dead: list[int]) -> None:
        """Forget the units in `dead`, and count every area watched as watched for a turn."""
        if not self.watching:
            return
        for watched in (
            *self.energy.values(),
            *self.life_reached_watches.values(),
            *self.life_dropped_watches.values(),
            *(watch.inside for watch in self.areas.values()),
        ):
            watched.difference_update(dead)
        for watch in self.areas.values():
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


def _keep_only[K: Hashable, V](watches: dict[K, V], wanted: Collection[K], start: Callable[[K], V]) -> bool:
    """Keep the watches of `wanted` only, starting one with `start` for each new key, and say whether any changed."""
    changed = False
    for key in [key for key in watches if key not in wanted]:
        del watches[key]
        changed = True
    for key in wanted:
        if key not in watches:
            watches[key] = start(key)
            changed = True
    return changed


def _no_ids(_: object) -> set[int]:
    """No unit's id, to start a watch of a value with."""
    return set()
