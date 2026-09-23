"""What one update of the unit tracker found changed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.geometry import Area
    from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
    from sc2nachos.units._own_unit import OwnUnit
    from sc2nachos.units._unit import Unit
    from sc2nachos.units._values import Alliance, CloakState, VitalType


@final
class _TrackerChanges:
    """What one update of the unit tracker found changed, each list in the order it was found.

    What the comparer and the watcher find goes in `own` and `enemy`, by side.
    """

    __slots__ = (
        "update",
        "own_units_created",
        "enemy_units_first_seen",
        "units_type_changed",
        "units_alliance_changed",
        "own_units_finished",
        "own_upgrades_finished",
        "own",
        "enemy",
        "enemy_units_entered_sight",
        "enemy_units_left_sight",
        "units_died",
        "units_found_dead",
    )

    def __init__(self, update: int) -> None:
        self.update = update
        """The number of this update, 1 for a game's first."""
        self.own_units_created: list[OwnUnit[Any]] = []
        """This player's units seen for the first time."""
        self.enemy_units_first_seen: list[Unit[Any]] = []
        """The enemy's units seen for the first time, in sight or in the fog."""
        self.units_type_changed: list[tuple[Unit[Any], UnitTypeId]] = []
        """The units whose type changed, each with its previous type."""
        self.units_alliance_changed: list[tuple[Unit[Any], Alliance]] = []
        """The units that changed sides to or from this player's, each with its previous alliance."""
        self.own_units_finished: list[OwnUnit[Any]] = []
        """This player's units first seen unfinished that have now finished: structures, add-ons and warp-ins."""
        self.own_upgrades_finished: list[UpgradeId] = []
        """This player's upgrades new to the observation, in order of id."""
        self.own: _ComparedChanges[OwnUnit[Any]] = _ComparedChanges()
        """What the comparer and the watcher found of this player's units."""
        self.enemy: _ComparedChanges[Unit[Any]] = _ComparedChanges()
        """What the comparer and the watcher found of the enemy's units."""
        self.enemy_units_entered_sight: list[Unit[Any]] = []
        """The enemy's units in sight now that were not in the previous observation."""
        self.enemy_units_left_sight: list[Unit[Any]] = []
        """The enemy's units in sight in the previous observation and not now, the dead left out."""
        self.units_died: list[Unit[Any]] = []
        """The units the game reported dead."""
        self.units_found_dead: list[Unit[Any]] = []
        """The units found dead without the game reporting it: a structure in the fog gone from its spot, and a drone
        that became a structure, if the game has not reported it dead by the update after the structure finishes or
        dies."""


@final
class _ComparedChanges[U: Unit[Any]]:
    """What the comparer and the watcher found of one side's units in one update, each list in the order it was
    found."""

    __slots__ = (
        "damaged",
        "energy_lost",
        "vital_reached",
        "vital_dropped",
        "cloak_changed",
        "gained_buff",
        "lost_buff",
        "entered_area",
        "left_area",
    )

    def __init__(self) -> None:
        self.damaged: list[tuple[U, float]] = []
        """The units that lost health or shields, each with how much."""
        self.energy_lost: list[tuple[U, float]] = []
        """The units that lost energy, each with how much."""
        self.vital_reached: list[tuple[U, VitalType, float]] = []
        """The units that reached a watched value of a vital, each with the vital and the value."""
        self.vital_dropped: list[tuple[U, VitalType, float]] = []
        """The units that dropped below a watched value of a vital, each with the vital and the value."""
        self.cloak_changed: list[tuple[U, CloakState]] = []
        """The units whose cloak state changed, each with its previous state."""
        self.gained_buff: list[tuple[U, BuffId]] = []
        """The units that gained a buff, each with the buff. A unit's several buffs are in order of id."""
        self.lost_buff: list[tuple[U, BuffId]] = []
        """The units that lost a buff, each with the buff. A unit's several buffs are in order of id."""
        self.entered_area: list[tuple[U, Area]] = []
        """The units that entered a watched area, each with the area."""
        self.left_area: list[tuple[U, Area]] = []
        """The units that left a watched area, each with the area."""
