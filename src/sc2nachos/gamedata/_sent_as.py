"""How an ability goes out for one unit type, where that is another of the game's abilities."""

from dataclasses import dataclass
from typing import final


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
