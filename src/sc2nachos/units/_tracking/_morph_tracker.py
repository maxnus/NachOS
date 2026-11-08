"""When each morph the game reports no progress for began."""

from __future__ import annotations

import itertools
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, final

from sc2nachos._shared.errors import NotReportedError
from sc2nachos.gamedata._techtree import COCOON_PRODUCTS, TECH_TREE
from sc2nachos.gametime import GameTime
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._progress import Progress

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos.units._tracking._tracker import _Tracker
    from sc2nachos.units._unit import Unit

# The morphs a structure carries out as its one order, which reads progress 0 from start to end (in game).
_STRUCTURE_MORPHS = (
    AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND,
    AbilityId.COMMAND_CENTER_MORPH_PLANETARY_FORTRESS,
    AbilityId.HATCHERY_MORPH_LAIR,
    AbilityId.LAIR_MORPH_HIVE,
    AbilityId.SPIRE_MORPH_GREATER_SPIRE,
)
# What each of those makes, by the raw id the structure's order reports it under.
_PRODUCT_BY_STRUCTURE_MORPH: Final[Mapping[int, UnitTypeId]] = MappingProxyType(
    {
        int(ability): product
        for ability in _STRUCTURE_MORPHS
        for product in TECH_TREE.ability(ability).products.values()
        if isinstance(product, UnitTypeId)
    }
)
# The structure types that carry one of them out: a command center, a hatchery, a lair and a spire.
_MORPHING_STRUCTURE_TYPES: Final[frozenset[UnitTypeId]] = frozenset(
    performer for ability in _STRUCTURE_MORPHS for performer in TECH_TREE.ability(ability).products
)


@final
class _MorphTracker:
    """When each morph under way began, of those the game reports no progress for: a structure's of this player's,
    and a cocoon's or a lurker egg's of either side.

    A morph began at the step of the observation before the first that shows it, which for one of this player's is the
    step its order was sent after (in game): a structure's shows as its order, a cocoon's as its type. A morph shown in
    the first observation of its unit, or of its unit back in the observation, was not seen to begin.
    """

    __slots__ = ("_morphs", "_step", "_structures", "_tracker")

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker
        # Each unit morphing, by id: what it becomes, and the step its morph began at, `None` where that was not seen.
        self._morphs: dict[int, tuple[UnitTypeId, int | None]] = {}
        # This player's structures of the types that morph this way, by id.
        self._structures: dict[int, OwnUnit[Any]] = {}
        # The step of the last update, `None` before the first.
        self._step: int | None = None

    def update(self, step: int) -> None:
        """Take in what the tracker's update at `step` found: the units new to it, those whose type changed, the
        orders of this player's structures that morph this way, and the dead."""
        previous_step = self._step
        self._step = step
        changes = self._tracker.last_changes
        if changes.own_units_created:
            self._watch_structures(changes.own_units_created)
        if changes.units_type_changed:
            self._read_type_changes(changes.units_type_changed, previous_step)
        if self._structures:
            self._read_structure_orders(previous_step)
        if changes.units_died or changes.units_found_dead:
            for unit in itertools.chain(changes.units_died, changes.units_found_dead):
                self._morphs.pop(unit._id, None)
                self._structures.pop(unit._id, None)

    def progress_of(self, unit: Unit[Any]) -> Progress[UnitTypeId] | None:
        """How far `unit`'s morph has got, or `None` for a unit not morphing, a dead one among them: the steps since it
        began over its product's build time, unrounded, and that build time less those steps, 0 once past it.

        Raises `NotReportedError` for a structure not this player's, whose morph the game does not show, and for a
        morph not seen to begin.
        """
        if unit._dead:
            return None
        if not unit._own and unit._type_id not in COCOON_PRODUCTS:
            raise NotReportedError(f"the game shows nothing of the morph of {unit!r}, which is not this player's")
        morph = self._morphs.get(unit._id)
        if morph is None:
            if unit._type_id in COCOON_PRODUCTS:
                raise NotReportedError(f"{unit!r} was first seen a cocoon, so its morph was not seen to begin")
            return None
        product, began = morph
        if began is None or self._step is None:
            raise NotReportedError(f"{unit!r} was morphing when first seen, so its morph was not seen to begin")
        row = self._tracker.game_data.units[product]
        steps_done = self._step - began
        steps_left = max(row.build_time.nearest_step - steps_done, 0)
        fraction = min(steps_done / row._unrounded_build_time.steps, 1.0)
        return Progress(product, fraction, row.build_time, GameTime(steps=steps_left))

    def _watch_structures(self, created: Iterable[OwnUnit[Any]]) -> None:
        """Watch the orders of each of `created`, this player's units new to the update, of a type that morphs this
        way."""
        for unit in created:
            if unit._type_id in _MORPHING_STRUCTURE_TYPES:
                self._structures[unit._id] = unit

    def _read_type_changes(self, changed: Iterable[tuple[Unit[Any], UnitTypeId]], previous_step: int | None) -> None:
        """Begin the morph of each of `changed` that became a cocoon, at `previous_step`, and end that of each that
        became anything else; and watch the orders of this player's units that became a structure that morphs this
        way, and no longer those that ceased to be one."""
        for unit, _ in changed:
            unit_type = unit._type_id
            if (product := COCOON_PRODUCTS.get(unit_type)) is not None:
                self._morphs[unit._id] = (product, _start_step(unit, previous_step))
                continue
            self._morphs.pop(unit._id, None)
            if isinstance(unit, OwnUnit) and unit_type in _MORPHING_STRUCTURE_TYPES:
                self._structures[unit._id] = unit
            else:
                self._structures.pop(unit._id, None)

    def _read_structure_orders(self, previous_step: int | None) -> None:
        """Begin the morph of each watched structure whose order is one it did not show before, at `previous_step`,
        and end that of each whose order is no morph."""
        morphs = self._morphs
        for unit_id, unit in self._structures.items():
            orders = unit._latest_report.orders
            product = _PRODUCT_BY_STRUCTURE_MORPH.get(orders[0].ability_id) if orders else None
            if product is None:
                morphs.pop(unit_id, None)
            elif (morph := morphs.get(unit_id)) is None or morph[0] is not product:
                morphs[unit_id] = (product, _start_step(unit, previous_step))


def _start_step(unit: Unit[Any], previous_step: int | None) -> int | None:
    """The step a morph `unit` shows for the first time began at: that of the observation before, `previous_step`, if
    that observation listed it, and otherwise `None`, not seen."""
    return None if unit._previous_report is None else previous_step
