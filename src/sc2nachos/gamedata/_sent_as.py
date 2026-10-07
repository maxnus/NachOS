"""How an ability goes out for one unit type, where that is another of the game's abilities."""

from dataclasses import dataclass
from enum import Enum
from typing import final


class Aim(Enum):
    """What an ability sent as another is aimed at."""

    TARGET = "target"
    """The order's target. The order must have one."""
    NOTHING = "nothing"
    """Nothing, whatever the order is aimed at: a tank's siege in an order aimed at a liberator's zone."""
    ITSELF = "itself"
    """The unit itself, one command per unit: a medivac's unload at a point, aimed at the medivac, unloads where it is
    (in game)."""


@final
@dataclass(frozen=True, slots=True)
class SentAs:
    """The game's ability an ability goes out as for one unit type, and what it is aimed at."""

    ability: int
    """The game's ability id. It may be one no curated id names, such as a tank's siege mode, which reads as
    `GENERAL_SIEGE`."""
    aim: Aim
    """What it is aimed at."""
