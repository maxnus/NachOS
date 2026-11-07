"""A plan: what to make, in order, and what the planner expects of it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedItem:
    """One item of a plan: what to make or research, and when the planner expects it to start."""

    item: UnitTypeId | UpgradeId
    """The unit type to make, or the upgrade to research."""
    start: GameTime
    """When the planner expects it to start, the game time of the first second of a bucket of its grid."""


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Plan:
    """What to make, in order, with when each is expected to start, and what the planner expects at each edge of its
    grid: the minerals and vespene in stock, and how many of each unit type are alive.

    A bot carries it out in order, each item as soon as it can be afforded, rather than to the second: the times are the
    model's, which rounds each start to a bucket's first second and plans far starts in fractions, each item of those
    starting once their sum reaches it, rounded.
    """

    made_at: GameTime
    """The game time of the state it was planned from."""
    items: tuple[PlannedItem, ...]
    """What to make, in the order the planner expects each to start; of those expected at one time, in the order the
    game's data lists their types, then the upgrades."""
    times: tuple[GameTime, ...]
    """The edges of its grid, `made_at` first and the horizon last."""
    minerals: tuple[float, ...]
    """The minerals expected in stock at each of `times`, before what starts there is paid."""
    vespene: tuple[float, ...]
    """The vespene expected in stock at each of `times`, before what starts there is paid."""
    counts: Mapping[UnitTypeId, tuple[float, ...]]
    """The number of each unit type the planner considered expected alive at each of `times`, done and survived."""
    objective: float
    """What the reward makes of the plan."""
    gap: float
    """The solver's bound on how far `objective` may fall short of the best plan's, relative to it."""
    is_optimal: bool
    """Whether the solver proved it the best plan, to a relative gap of 0.0001, rather than stopping at a limit."""
    solve_seconds: float
    """The wall-clock seconds the solve took."""
