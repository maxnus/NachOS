"""One command a bot gave."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from sc2nachos.gametime import GameTime
    from sc2nachos.ids import AbilityId
    from sc2nachos.units import OwnUnit, Target, Units


@final
class Command:
    """A command a bot gave. A refusal by the game shows in `api.action_failures`, and what a unit then reports doing
    in its `sc2nachos.units.Order`s."""

    __slots__ = (
        "_ability",
        "_time",
        "_queued",
        "_target",
        "_units",
        "_withdrawn",
    )

    def __init__(
        self,
        ability: AbilityId,
        units: Units[OwnUnit[Any]],
        target: Target | None,
        *,
        queued: bool,
        time: GameTime,
    ) -> None:
        """A command of `ability` to `units`, given at `time`. Made by the command book, never by a bot."""
        self._ability = ability
        self._units = units
        self._target = target
        self._queued = queued
        self._time = time
        self._withdrawn = False

    def __repr__(self) -> str:
        units = self._units[0] if len(self._units) == 1 else f"{len(self._units)} units"
        return f"Command({self._ability.name}, {units})"

    @property
    def ability(self) -> AbilityId:
        """The ability commanded, as it was given. A unit carrying it out reads as running the same one."""
        return self._ability

    @property
    def units(self) -> Units[OwnUnit[Any]]:
        """The units the command was given to."""
        return self._units

    @property
    def target(self) -> Target | None:
        """The point or unit the command is aimed at, or `None`."""
        return self._target

    @property
    def queued(self) -> bool:
        """Whether the command goes behind each unit's orders instead of replacing them."""
        return self._queued

    @property
    def time(self) -> GameTime:
        """The time of the observation the command was given in."""
        return self._time

    def withdraw(self) -> None:
        """Take the command back while its turn's handlers run, so it never goes out. After its turn it does nothing."""
        self._withdrawn = True
