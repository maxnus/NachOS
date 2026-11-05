"""How an ability goes out for one unit type, where that is another of the game's abilities."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sc2nachos.ids import UnitTypeId


@final
@dataclass(frozen=True, slots=True)
class SentAs:
    """The game's ability an ability goes out as for one unit type, aimed at nothing or at the unit itself."""

    ability: int
    """The game's ability id. It may be one no curated id names, such as a tank's siege mode, which reads as
    `SIEGE`."""
    aimed_at_itself: bool = False
    """Whether it goes out aimed at the unit itself, one command per unit: a medivac's unload at a point, aimed at the
    medivac, unloads where it is (in game). Otherwise it is aimed at nothing, as a tank's siege mode is."""


SENT_UNCHANGED: Mapping[UnitTypeId, SentAs] = MappingProxyType({})
"""What an ability goes out as where no type is sent another: itself, for every type."""
