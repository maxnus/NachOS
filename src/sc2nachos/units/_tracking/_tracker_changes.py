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
    """What one update of the unit tracker found changed, each in the order it was found.

    What the comparer and the watcher find is kept for each side in `own` and `enemy`.
    """

    __slots__ = (
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

    def __init__(self) -> None:
        self.own_units_created: list[OwnUnit[Any]] = []
        """This player's units first seen."""
        self.enemy_units_first_seen: list[Unit[Any]] = []
        """The enemy's units first seen, in sight or in the fog."""
        self.units_type_changed: list[tuple[Unit[Any], UnitTypeId]] = []
        """The units whose type changed, each with the type it was."""
        self.units_alliance_changed: list[tuple[Unit[Any], Alliance]] = []
        """The units that changed sides to or from this player's, each with the alliance it was."""
        self.own_units_finished: list[OwnUnit[Any]] = []
        """This player's units first seen unfinished that have finished: structures, add-ons and warp-ins."""
        self.own_upgrades_finished: list[UpgradeId] = []
        """This player's upgrades new to the observation, in the order of their ids."""
        self.own: _SideChanges[OwnUnit[Any]] = _SideChanges()
        """What the comparer and the watcher found of this player's units."""
        self.enemy: _SideChanges[Unit[Any]] = _SideChanges()
        """What the comparer and the watcher found of the enemy's units."""
        self.enemy_units_entered_sight: list[Unit[Any]] = []
        """The enemy's units in sight that were not in the observation before."""
        self.enemy_units_left_sight: list[Unit[Any]] = []
        """The enemy's units that were in sight in the observation before and are not now, the dead left out."""
        self.units_died: list[Unit[Any]] = []
        """The units the game reported dead."""
        self.units_found_dead: list[Unit[Any]] = []
        """The units found dead that the game did not report: a structure in the fog gone from its spot, and a drone
        that became a structure, should the game not report it by an update after the structure finishes or dies."""


@final
class _SideChanges[U: Unit[Any]]:
    """What the comparer and the watcher found of one side's units in one update, each in the order it was found."""

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
        """The units that reached a value of a vital watched, each with the vital and the value."""
        self.vital_dropped: list[tuple[U, VitalType, float]] = []
        """The units that dropped below a value of a vital watched, each with the vital and the value."""
        self.cloak_changed: list[tuple[U, CloakState]] = []
        """The units whose cloak changed, each with the state it was."""
        self.gained_buff: list[tuple[U, BuffId]] = []
        """The units that wear a buff they did not, each with the buff, a unit's several in the order of their ids."""
        self.lost_buff: list[tuple[U, BuffId]] = []
        """The units that no longer wear a buff they did, each with the buff, a unit's several in the order of their
        ids."""
        self.entered_area: list[tuple[U, Area]] = []
        """The units that came inside an area watched, each with the area."""
        self.left_area: list[tuple[U, Area]] = []
        """The units that came outside an area watched, each with the area."""
