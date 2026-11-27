"""What the bot assumes of its economy: what its workers bring in, and how far they walk to build."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EconomyModel:
    """What the bot assumes of its economy, which the planner plans with: what each worker mines or collects, what each
    orbital command's MULEs mine, and how far a worker walks to build.

    The workers are pooled: in each interval the planner splits them anew among every mineral field and gas building
    there is, as if each could work anywhere at once. So a new worker mines from when it is done, even one that has to
    walk to another base first, and a new base's fields count from when its townhall is done.

    Raises `ValueError` for a negative rate or delay, or a rate above the one before it.
    """

    # 2315 minerals in 180 seconds from 12 SCVs.
    minerals_per_miner_second: Sequence[float] = (2315 / 180 / 12,) * 2
    """The minerals each worker at a mineral field mines a second, one number for each worker a field takes, the first
    worker's first. The plan puts no more workers at a field than there are numbers. By default two workers a field,
    each at the rate twelve SCVs speed mined a main's eight fields (in game)."""
    # 780 vespene in 180 seconds from 4 SCVs, two a refinery, and 984 from 6, three a refinery.
    vespene_per_collector_second: Sequence[float] = (780 / 180 / 4, 780 / 180 / 4, (984 - 780) / 180 / 2)
    """The vespene each worker at a gas building collects a second, one number for each worker it takes, the first
    worker's first. By default three workers: the first two each at the rate two SCVs a refinery speed collected, and
    the third at what a third added, about half of that (in game)."""
    # A MULE's 225 minerals for each 50 energy, at 0.7875 energy a second.
    mule_minerals_per_orbital_second: float = 225 / (50 / 0.7875)
    """The minerals each orbital command's MULEs mine a second, by default a MULE's whole haul for each 50 energy an
    orbital command gains (stated); 0 for a bot that spends the energy otherwise."""
    initial_harvest_delay: GameTime = GameTime(seconds=3)
    """How long a worker brings in nothing when it starts to mine or to collect, by default 3 seconds (assumed). It
    applies to a new worker on its first walk, to a worker moved between minerals and vespene, and to a builder back
    from its structure at either, whose walk back is that much shorter for it, so that the two together take its walk
    back. With 0 a worker brings in from when it starts."""
    builder_walk_time: Callable[[UnitTypeId], GameTime | None] | None = None
    """How long a worker walks to build a structure of a unit type, and walks back after: what the function returns for
    the type. The last `initial_harvest_delay` of the walk back is that delay, which the builder pays as it starts to
    mine or to collect, all of it if the walk is shorter. Where it returns `None`, or is `None`, the default applies: 10
    seconds to a townhall and 6 to any other structure. A build a worker has not begun, or has left, takes that walk
    first. The function is asked about every structure a worker builds, not only those a plan values, so a lookup
    returns `None` for those it does not hold."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for a field that holds what the class's docstring refuses."""
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
        if self.initial_harvest_delay < GameTime.ZERO:
            raise ValueError(f"initial_harvest_delay must not be negative, not {self.initial_harvest_delay}")


def rate_tiers(rates: Sequence[float]) -> tuple[tuple[int, float], ...]:
    """`rates`, each worker's at a field or refinery, as tiers: each run of one rate above 0, as how many workers it
    takes and that rate."""
    return tuple((len(list(run)), rate) for rate, run in itertools.groupby(rates) if rate > 0)


def minerals_mined_a_second(
    economy: EconomyModel, *, workers: int, mineral_fields: int, orbital_commands: int
) -> float:
    """The minerals `workers` mine a second by `economy` if every one mines, at `mineral_fields`, as many at each
    field's better rates as the fields take, with what the MULEs of `orbital_commands` mine."""
    mined = economy.mule_minerals_per_orbital_second * orbital_commands
    workers_left = workers
    for workers_a_field, rate in rate_tiers(economy.minerals_per_miner_second):
        mining = min(workers_left, workers_a_field * mineral_fields)
        mined += rate * mining
        workers_left -= mining
    return mined


def walk_seconds_of(economy: EconomyModel, unit_types: Iterable[UnitTypeId]) -> dict[UnitTypeId, float]:
    """The seconds a worker walks to build each of `unit_types`, by `economy`: by default 10 to a townhall, about the
    walk from a main's mineral line to its natural on the maps of the pool (corpus), and 6 to any other structure.

    Raises `ValueError` for a negative time from its `builder_walk_time`.
    """
    seconds: dict[UnitTypeId, float] = {}
    for unit_type in unit_types:
        chosen = None if economy.builder_walk_time is None else economy.builder_walk_time(unit_type)
        if chosen is not None and chosen < GameTime.ZERO:
            raise ValueError(
                f"builder_walk_time must not return a negative time, but returned {chosen} for {unit_type.name}"
            )
        is_townhall = unit_type is UnitTypeId.COMMAND_CENTER
        default = GameTime(seconds=10) if is_townhall else GameTime(seconds=6)
        seconds[unit_type] = (default if chosen is None else chosen).seconds
    return seconds
