"""What one update of the unit tracker found changed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
    from sc2nachos.units._own_unit import OwnUnit
    from sc2nachos.units._unit import Unit
    from sc2nachos.units._values import Alliance, CloakState


@final
class _TrackerChanges:
    """What one update of the unit tracker found changed, each in the order it was found."""

    __slots__ = (
        "own_units_created",
        "enemy_units_first_seen",
        "units_type_changed",
        "units_alliance_changed",
        "own_units_finished",
        "own_upgrades_finished",
        "own_units_damaged",
        "enemy_units_damaged",
        "own_units_energy_lost",
        "enemy_units_energy_lost",
        "own_units_cloak_changed",
        "enemy_units_cloak_changed",
        "own_units_gained_buff",
        "enemy_units_gained_buff",
        "own_units_lost_buff",
        "enemy_units_lost_buff",
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
        self.own_units_damaged: list[tuple[OwnUnit[Any], float]] = []
        """This player's units that lost health or shields, each with how much. Filled only by `compare_units`."""
        self.enemy_units_damaged: list[tuple[Unit[Any], float]] = []
        """The same for the enemy's units."""
        self.own_units_energy_lost: list[tuple[OwnUnit[Any], float]] = []
        """This player's units that lost energy, each with how much. Filled only by `compare_units`."""
        self.enemy_units_energy_lost: list[tuple[Unit[Any], float]] = []
        """The same for the enemy's units."""
        self.own_units_cloak_changed: list[tuple[OwnUnit[Any], CloakState]] = []
        """This player's units whose cloak changed, each with the state it was. Filled only by `compare_units`."""
        self.enemy_units_cloak_changed: list[tuple[Unit[Any], CloakState]] = []
        """The same for the enemy's units."""
        self.own_units_gained_buff: list[tuple[OwnUnit[Any], BuffId]] = []
        """This player's units that wear a buff they did not, each with the buff, a unit's several in the order of
        their ids. Filled only by `compare_units`."""
        self.enemy_units_gained_buff: list[tuple[Unit[Any], BuffId]] = []
        """The same for the enemy's units."""
        self.own_units_lost_buff: list[tuple[OwnUnit[Any], BuffId]] = []
        """This player's units that no longer wear a buff they did, each with the buff, a unit's several in the order of
        their ids. Filled only by `compare_units`."""
        self.enemy_units_lost_buff: list[tuple[Unit[Any], BuffId]] = []
        """The same for the enemy's units."""
        self.enemy_units_entered_sight: list[Unit[Any]] = []
        """The enemy's units in sight that were not in the observation before."""
        self.enemy_units_left_sight: list[Unit[Any]] = []
        """The enemy's units that were in sight in the observation before and are not now, the dead left out."""
        self.units_died: list[Unit[Any]] = []
        """The units the game reported dead."""
        self.units_found_dead: list[Unit[Any]] = []
        """The units found dead that the game did not report: a structure in the fog gone from its spot, and a drone
        that became a structure, should the game not report it by an update after the structure finishes or dies."""
