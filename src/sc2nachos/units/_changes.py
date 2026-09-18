"""What one update of the unit tracker found changed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.ids import UnitTypeId, UpgradeId
    from sc2nachos.units._own_unit import OwnUnit
    from sc2nachos.units._unit import Unit
    from sc2nachos.units._values import Alliance


@final
class _Changes:
    """What one update of the unit tracker found changed, each in the order it was found."""

    __slots__ = (
        "alliance_changes",
        "created",
        "died",
        "entered_sight",
        "finished",
        "first_seen",
        "found_dead",
        "left_sight",
        "type_changes",
        "upgrades",
    )

    def __init__(self) -> None:
        self.created: list[OwnUnit[Any]] = []
        """This player's units first seen."""
        self.first_seen: list[Unit[Any]] = []
        """The enemy's units first seen, in sight or in the fog."""
        self.type_changes: list[tuple[Unit[Any], UnitTypeId]] = []
        """The units whose type changed, each with the type it was."""
        self.alliance_changes: list[tuple[Unit[Any], Alliance]] = []
        """The units that changed sides to or from this player's, each with the alliance it was."""
        self.finished: list[OwnUnit[Any]] = []
        """This player's units first seen unfinished that have finished: structures, add-ons and warp-ins."""
        self.upgrades: list[UpgradeId] = []
        """This player's upgrades new to the observation, in the order of their ids."""
        self.entered_sight: list[Unit[Any]] = []
        """The enemy's units in sight that were not in the observation before."""
        self.left_sight: list[Unit[Any]] = []
        """The enemy's units that were in sight in the observation before and are not now, the dead left out."""
        self.died: list[Unit[Any]] = []
        """The units the game reported dead."""
        self.found_dead: list[Unit[Any]] = []
        """The units found dead that the game did not report: a structure in the fog gone from its spot, and a unit
        that became a structure, if the game does not report it as the structure finishes or dies."""
