"""What the bot assumes of its economy: what its workers bring in, what a new base holds, and how far its SCVs walk."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId

# Twelve SCVs speed mined Pylon's main's eight fields for 2315 minerals in three minutes (in game, build 75689): each
# of the first two workers a field mines this much a second.
_SPEED_MINED_MINERALS_PER_MINER_SECOND: Final = 2315 / 180 / 12
# Three SCVs a refinery collected 163 to 164 vespene a minute (in game, build 75689).
_VESPENE_PER_COLLECTOR_SECOND: Final = 163.5 / 60 / 3
# A MULE mines 225 minerals in its life, and an orbital command's energy, 0.7875 a second, calls one every 50
# (stated, not measured).
_MULE_MINERALS_PER_ORBITAL_SECOND: Final = 225 / (50 / 0.7875)
# How long an SCV walks to build by default: to a command center, about the walk from a main's mineral line to its
# natural on every map of the pool, 8.0 to 10.9 seconds by the pathing distance and an SCV's speed (corpus), and to any
# other structure.
_DEFAULT_COMMAND_CENTER_WALK: Final = GameTime(seconds=10)
_DEFAULT_WALK: Final = GameTime(seconds=6)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Economy:
    """What the bot assumes of its economy, which the planner plans with: what each worker mines or collects, what each
    orbital command's MULEs mine, what a new command center brings, and how far an SCV walks to build.

    The workers are pooled: in each bucket the planner splits them anew among every mineral field and refinery there
    is, as if each could work anywhere at once. So a new SCV mines from when it is done, even one that has to walk to
    another base first, and a new base's fields count from when its command center is done.

    Raises `ValueError` for a negative rate or count, or a rate above the one before it.
    """

    minerals_per_miner_second: Sequence[float] = (
        _SPEED_MINED_MINERALS_PER_MINER_SECOND,
        _SPEED_MINED_MINERALS_PER_MINER_SECOND,
        _SPEED_MINED_MINERALS_PER_MINER_SECOND / 2,
    )
    """The minerals each worker at a mineral field mines a second, the first worker's first; a worker past the last
    mines nothing. By default two at the rate twelve SCVs speed mined a main's eight fields (in game), and a third at
    half that (assumed)."""
    vespene_per_collector_second: Sequence[float] = (_VESPENE_PER_COLLECTOR_SECOND,) * 3
    """The vespene each worker at a refinery collects a second, the first worker's first; a worker past the last
    collects nothing. By default three at the rate three SCVs collected (in game)."""
    mule_minerals_per_orbital_second: float = _MULE_MINERALS_PER_ORBITAL_SECOND
    """The minerals each orbital command's MULEs mine a second, by default a MULE's 225 for each 50 energy, the game's
    stated figures; 0 for a bot that spends the energy otherwise."""
    mineral_fields_per_new_base: int = 8
    """The mineral fields a new command center brings, by default a full base's, as every main and natural of the pool
    has (corpus)."""
    geysers_per_new_base: int = 2
    """The geysers a new command center brings."""
    walk_time: Callable[[UnitTypeId], GameTime | None] | None = None
    """How long an SCV walks to build a structure of a unit type, and walks back after: what it returns, or where it
    returns `None`, or is `None`, 10 seconds to a command center and 6 to any other. A build an SCV has not begun, or
    has left, takes that walk first."""

    def __post_init__(self) -> None:
        for name, rates in (
            ("minerals_per_miner_second", self.minerals_per_miner_second),
            ("vespene_per_collector_second", self.vespene_per_collector_second),
        ):
            if any(rate < 0 for rate in rates):
                raise ValueError(f"{name} must not be negative: {list(rates)}")
            if any(later > earlier for earlier, later in itertools.pairwise(rates)):
                raise ValueError(f"{name} must each be at most the one before: {list(rates)}")
        if self.mule_minerals_per_orbital_second < 0:
            raise ValueError(
                f"mule_minerals_per_orbital_second must not be negative, not {self.mule_minerals_per_orbital_second}"
            )
        if self.mineral_fields_per_new_base < 0 or self.geysers_per_new_base < 0:
            raise ValueError(
                f"a new base's fields and geysers must not be negative, not {self.mineral_fields_per_new_base} and "
                f"{self.geysers_per_new_base}"
            )


def rate_tiers(rates: Sequence[float]) -> tuple[tuple[int, float], ...]:
    """`rates`, each worker's at a field or refinery, as tiers: each run of one rate above 0, as how many workers it
    takes and that rate."""
    return tuple((len(list(run)), rate) for rate, run in itertools.groupby(rates) if rate > 0)


def walk_seconds_of(economy: Economy, unit_types: Iterable[UnitTypeId]) -> dict[UnitTypeId, float]:
    """The seconds an SCV walks to build each of `unit_types`, by `economy`.

    Raises `ValueError` for a negative time from its `walk_time`.
    """
    seconds: dict[UnitTypeId, float] = {}
    for unit_type in unit_types:
        chosen = None if economy.walk_time is None else economy.walk_time(unit_type)
        if chosen is not None and chosen < GameTime.ZERO:
            raise ValueError(f"walk_time must not return a negative time, but returned {chosen} for {unit_type.name}")
        default = _DEFAULT_COMMAND_CENTER_WALK if unit_type == UnitTypeId.COMMAND_CENTER else _DEFAULT_WALK
        seconds[unit_type] = (default if chosen is None else chosen).seconds
    return seconds
