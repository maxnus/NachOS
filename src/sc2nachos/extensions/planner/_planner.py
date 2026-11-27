"""The build planner: what to make and when, for a reward the bot hands it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.extensions.planner._economy_model import EconomyModel, minerals_mined_a_second
from sc2nachos.extensions.planner._milp import Solver, check_gurobi_starts
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._plan_variables import NewBase
from sc2nachos.extensions.planner._planner_state import (
    PlannerState,
    fields_and_free_geysers_of,
    read_planner_state,
)
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.extensions.planner._time_grid import TimeGrid, edge_seconds_of
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from sc2nachos import Api, Expansion
    from sc2nachos.extensions.planner._build_plan import BuildPlan
    from sc2nachos.extensions.planner._reward import Reward
    from sc2nachos.extensions.planner._terran_model import ProductionItem
    from sc2nachos.gametime import TimeDependent
    from sc2nachos.ids import UpgradeId

_NO_LOSSES: Mapping[UnitTypeId, float | TimeDependent[float]] = MappingProxyType({})
_NO_LIMITS: Mapping[UnitTypeId | UpgradeId, int] = MappingProxyType({})


@final
class Planner:
    """The build planner, for Terran: it works out what to make and when.

    A bot hands `plan_now` a `Reward`, which says what units and upgrades are worth to it. The planner finds the plan
    worth the most and returns it as a `BuildPlan`: the things to make, in order, each with the time it is expected to
    start. A bot carries the plan out in that order, starting each thing as soon as it can pay for it, and asks for a
    new plan as the game goes on. The planner gives no orders and holds no unit: where a structure goes and which worker
    builds it stay the bot's.

    Build it once the game has started, in the bot's own object an api is given as `extensions`:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.planner import Planner


    class Extensions:
        def __init__(self, api: Api) -> None:
            self.planner = Planner(api)


    api = Api(extensions=Extensions)
    ```

    Its arguments say how the planner lays a plan out in time. Each has a default that suits a normal game.

    `grid` is the grid of time intervals a plan is laid out on, by default `TimeGrid.doubling()`: short intervals at
    first and longer ones later. Whatever a plan starts, it starts at the beginning of an interval, and a build counts
    as done from the first boundary between intervals at or after its end.

    `fluid_after` says for how long each thing is planned in whole numbers. The planner plans the near future exactly:
    one barracks, then another. Further ahead it plans in fractions, 0.4 of a barracks, say, which is far faster to
    solve and loses little, since the bot carries out only the first steps before it plans again. `fluid_after` is a
    function that takes a unit type or an upgrade and returns the time from the plan's start after which it may be
    planned in fractions, or `None` for the default; `GameTime.INFINITE` keeps it whole throughout. The default grows
    with what the thing costs and shrinks as the income grows. At the income a game starts with, it is 60 seconds for
    each 100 minerals and vespene: 4:00 for a command center, 2:30 for a factory or a starport, 2:00 for an upgrade of
    100 and 100, 1:30 for a barracks or an orbital command, and 1:00 for a supply depot. At three times that income it
    is a third of each, and it is never under 30 seconds. A longer time makes a plan exact further ahead, and the solve
    slower.

    `max_ignored_overrun` is how far a build may run into the next interval and still leave that interval free for the
    next build of the same producer, as a share of the interval: by default a quarter. It matters where a build time is
    not a whole number of intervals. On the default grid, SCVs then follow each other every 272 game steps, as in the
    game, marauders every 476 for their 480, and marines every 408 for their 400. With 0 no overrun is ignored, and with
    1 every one is.

    `unlock_fraction` is how much of a requirement must be done before what needs it may start, by default 1: no
    starport before a whole factory. It matters only far ahead, where structures are planned in fractions. A lower value
    lets that share of a factory unlock a starport. With 0 any fraction does: the solve is faster, but a plan may then
    make units on a structure it never lists.

    `spendable_income_fraction` is how much of an interval's income may pay for what starts in that interval, beside
    what is in the bank at its start: by default a half. A thing the bot can afford in the first half of an interval
    then counts as started at the interval's start, and one it can afford only later waits for the next, so a start is
    at most half an interval early or late. With 0 every start waits until the bank alone pays for it, up to an interval
    late, and with 1 every start is as early as it could be. It matters most where intervals are long.

    `solver` is the `Solver` that finds a plan, HiGHS by default.

    What the planner assumes of the game:

    - What each thing costs, how long it takes, the supply it takes and gives, what makes it and what it needs first
      come from the game's data.
    - A plan considers only what the reward values, what that needs, and the economy: SCVs, supply depots, command
      centers, orbital commands and refineries.
    - A unit counts as one type whatever form it is in, in the reward, the loss rates and the state alike: a flying
      barracks as a barracks, a sieged tank as a siege tank, a rich refinery as a refinery. A hellbat counts as a
      hellion, which needs no armory, so a plan makes hellions, and an armory only if the reward values one.
    - Workers are pooled: the planner does not know which base a worker, a mineral field or a refinery is at, and takes
      every worker to be able to work anywhere. How many mine and how many collect vespene is the plan's choice in each
      interval, which the interval holds as its `miners` and `collectors`.
    - What workers bring in, and how long a worker walks to build, is the `EconomyModel` handed to `plan_now`. A worker
      that builds is away from its work from the walk to the structure, through the build, to the walk back, the last of
      which it spends as any worker starting work does, at minerals or vespene alike.
    - A command center that morphs trains no SCV meanwhile. A reactor lets its host make two units at a time. A tech lab
      makes the units that need one, and researches by itself.
    - A new command center brings the mineral fields and the geysers of the next of the `expansions` handed to
      `plan_now`. With none, a plan starts no command center.

    Raises `ValueError` for a player of a race other than Terran, or a `max_ignored_overrun`, an `unlock_fraction` or a
    `spendable_income_fraction` outside 0 to 1; `NotPlayingError` if built before the game starts; and, for
    `Solver.GUROBI`, `ImportError` without gurobipy and highspy installed and `RuntimeError` where Gurobi cannot start,
    as without a licence.
    """

    __slots__ = (
        "_api",
        "_grid",
        "_max_ignored_overrun",
        "_model",
        "_plan",
        "_fluid_after",
        "_solver",
        "_spendable_income_fraction",
        "_unlock_fraction",
    )

    def __init__(
        self,
        api: Api[object],
        *,
        grid: TimeGrid | None = None,
        fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None = None,
        max_ignored_overrun: float = 0.25,
        unlock_fraction: float = 1.0,
        spendable_income_fraction: float = 0.5,
        solver: Solver = Solver.HIGHS,
    ) -> None:
        if api.player.race is not Race.TERRAN:
            raise ValueError(f"the planner plans for Terran alone, and this player is {api.player.race.name}")
        if not 0 <= max_ignored_overrun <= 1:
            raise ValueError(f"the overrun ignored must be a share of an interval, 0 to 1, not {max_ignored_overrun}")
        if not 0 <= unlock_fraction <= 1:
            raise ValueError(f"the unlock fraction must be 0 to 1, not {unlock_fraction}")
        if not 0 <= spendable_income_fraction <= 1:
            raise ValueError(f"the spendable income fraction must be 0 to 1, not {spendable_income_fraction}")
        if solver is Solver.GUROBI:
            check_gurobi_starts()
        self._api = api
        self._grid = TimeGrid.doubling() if grid is None else grid
        self._fluid_after = fluid_after
        self._max_ignored_overrun = max_ignored_overrun
        self._unlock_fraction = unlock_fraction
        self._spendable_income_fraction = spendable_income_fraction
        self._solver = solver
        self._model = TerranModel(api.data)
        self._plan: BuildPlan | None = None

    @property
    def grid(self) -> TimeGrid:
        """The grid of time intervals plans start on."""
        return self._grid

    @property
    def plan(self) -> BuildPlan | None:
        """The plan `plan_now` found last, or `None` before it found one."""
        return self._plan

    def state(self) -> PlannerState:
        """The state of the game now, as the planner reads it: what `plan_now` plans from unless handed another."""
        return read_planner_state(self._api, model=self._model)

    def plan_now(
        self,
        reward: Reward,
        *,
        horizon: GameTime = GameTime(minutes=5),
        state: PlannerState | None = None,
        economy: EconomyModel = EconomyModel(),
        loss_rates: Mapping[UnitTypeId, float | TimeDependent[float]] = _NO_LOSSES,
        start_limits: Mapping[UnitTypeId | UpgradeId, int] = _NO_LIMITS,
        expansions: Sequence[Expansion] = (),
        time_limit: float | None = 3.0,
        node_limit: int | None = None,
        relative_gap: float = 1e-4,
    ) -> BuildPlan | None:
        """The plan worth the most by `reward`, or `None` if the solver finds no plan within its limits. `plan` holds
        the last plan found, so a call that finds none leaves it as it was.

        `horizon` is how far ahead the plan looks, 5 minutes by default. Nothing that pays back only after the horizon
        is planned unless the reward values it directly, an expansion for one. A longer horizon takes longer to solve.

        `state` is the state of the game the plan starts from: by default what `state()` reads now. Its time is the
        plan's start.

        `economy` says what workers bring in and how long they walk to build.

        `loss_rates` says how fast the bot expects to lose units of each type: the share of them lost each second. The
        plan then counts only the units it expects to still be alive, of those alive now and of those it makes. A rate
        is a number, or a `TimeDependent` for a rate that changes during the game: a `TimeCurve`, a `TimeSeries` that
        covers the plan, or a function in a `TimeFunction`. It is read at every game step of the plan, so a short peak
        works as a loss all at once: `-math.log(0.7)` for one second loses 30% of the units alive then. Two types that
        count as one unit, a hellion and a hellbat, take the higher of their rates.

        `start_limits` is the most a plan may start of a unit type or an upgrade, none for 0, not counting what is
        already in production. It is for what the bot will not make: a reactor it does not build, or an orbital command
        it does not morph. Two types that count as one unit take the lower of their limits, and a limit on something the
        plan would not make anyway does nothing.

        `expansions` are the expansions the bot means to take next, from `api.expansions`, in the order it means to take
        them, without those already being built on. A plan starts at most that many command centers: the first brings
        the mineral fields left and the free geysers of the first expansion, and so on. An expansion with a townhall on
        it already, anyone's, is left out. With none, the default, a plan starts no command center. A command center
        already going up, or one a worker is on its way to build, needs no entry: the state holds what it brings, which
        is nothing for one built at no expansion.

        `time_limit`, `node_limit` and `relative_gap` say when the solve may stop. `plan_now` solves before it returns,
        so a turn that plans is that much longer. `time_limit` is the most seconds of wall-clock time the solve may
        take, 3 by default, and `node_limit` the most nodes of branching; `None` for either is no limit. A node limit
        gives the same plan on every machine, and a time limit does not. Before either, the solve stops once the plan is
        proven within `relative_gap` of the best there is, as a share of what the plan is worth: 0.0001 by default. A
        larger gap ends the solve sooner, with a plan that may be worth that much less.

        Raises `ValueError` for a unit type or an upgrade in the reward that Terran does not make; a horizon not longer
        than 0; a `time_limit` or `node_limit` not above 0; a negative `relative_gap`, loss rate or start limit; a loss
        rate from a `TimeDependent` that is negative or not finite; a negative time from `fluid_after` or from
        `economy`'s `builder_walk_time`; a factor from a term's `time_factor` or `count_factor` that is negative or not
        finite; or a `state` holding a unit in production that Terran does not make. Raises `TypeError` for a loss rate
        that is neither a number nor gives one for each of many game steps, as a function not in a `TimeFunction`, and
        `IndexError` from a loss rate that does not cover a step of the plan, as a `TimeSeries` recorded for fewer.
        """
        if any(not callable(rate) and rate < 0 for rate in loss_rates.values()):
            raise ValueError(f"loss rates must not be negative: {dict(loss_rates)}")
        if any(limit < 0 for limit in start_limits.values()):
            raise ValueError(f"start limits must not be negative: {dict(start_limits)}")
        _check_solve_limits(time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap)
        edges = edge_seconds_of(self._grid, horizon)
        items = self._model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrades)
        planned_from = self.state() if state is None else state
        program = PlanProgram(
            items=items,
            model=self._model,
            edges=edges,
            state=planned_from,
            reward=reward,
            economy=economy,
            loss_rates=loss_rates,
            start_limits=start_limits,
            new_bases=new_bases_of(self._api, expansions),
            fluid_after_seconds=fluid_after_seconds_of(
                items, fluid_after=self._fluid_after, state=planned_from, economy=economy
            ),
            max_ignored_overrun=self._max_ignored_overrun,
            unlock_fraction=self._unlock_fraction,
            spendable_income_fraction=self._spendable_income_fraction,
        )
        plan = program.solve(
            solver=self._solver, time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap
        )
        if plan is not None:
            self._plan = plan
        return plan


def new_bases_of(api: Api[object], expansions: Sequence[Expansion]) -> list[NewBase]:
    """What a townhall at each of `expansions` would bring, in their order: the mineral fields the expansion has left
    and its geysers with no gas building on them. An expansion with a townhall on it already, this player's or
    another's, finished or going up, is left out, since none can be built there."""
    new_bases: list[NewBase] = []
    for expansion in expansions:
        if expansion.townhall is None:
            mineral_fields, free_geysers = fields_and_free_geysers_of(api, expansion)
            new_bases.append(NewBase(mineral_fields=mineral_fields, geysers=free_geysers))
    return new_bases


def _check_solve_limits(*, time_limit: float | None, node_limit: int | None, relative_gap: float) -> None:
    """Raise `ValueError` for a `time_limit` or a `node_limit` not above 0, or a negative `relative_gap`."""
    if time_limit is not None and time_limit <= 0:
        raise ValueError(f"a time limit must be above 0 seconds, not {time_limit}")
    if node_limit is not None and node_limit <= 0:
        raise ValueError(f"a node limit must be above 0, not {node_limit}")
    if relative_gap < 0:
        raise ValueError(f"the relative gap must not be negative, not {relative_gap}")


def fluid_after_seconds_of(
    items: Sequence[ProductionItem],
    *,
    fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None,
    state: PlannerState,
    economy: EconomyModel,
) -> tuple[float, ...]:
    """The seconds from the plan's start after which each of `items` is fluid, starting in whole numbers until then:
    what `fluid_after` returns for what it makes or researches, or where that is `None`, its default for a plan from
    `state` by `economy`, as `Planner` says.

    Raises `ValueError` for a negative time from `fluid_after`.
    """
    # By default an item starts whole for 60 seconds for each 100 minerals and vespene it costs at the income a game
    # starts with, less in proportion at a higher income, but for 30 seconds at least.
    seconds_a_hundred_resources = 60.0 * _start_income_as_a_share_of_the_income(state, economy)
    seconds: list[float] = []
    for item in items:
        chosen = None if fluid_after is None or item.made is None else fluid_after(item.made)
        if chosen is None:
            by_cost = seconds_a_hundred_resources * (item.minerals + item.vespene) / 100
            seconds.append(max(30.0, by_cost))
        elif chosen < GameTime.ZERO:
            raise ValueError(f"fluid_after must not return a negative time, but returned {chosen} for {item.name}")
        else:
            seconds.append(chosen.seconds)
    return tuple(seconds)


def _start_income_as_a_share_of_the_income(state: PlannerState, economy: EconomyModel) -> float:
    """The income a game starts with, that of twelve workers on eight mineral fields, as a share of that of `state`,
    at most 1, each by `economy`: what the SCVs mine if every one mines, with the orbital commands' MULEs. It is 1
    where the state brings in nothing."""
    start_income = minerals_mined_a_second(economy, workers=12, mineral_fields=8, orbital_commands=0)
    income = minerals_mined_a_second(
        economy,
        workers=state.units.get(UnitTypeId.SCV, 0),
        mineral_fields=state.mineral_fields,
        orbital_commands=state.units.get(UnitTypeId.ORBITAL_COMMAND, 0),
    )
    return min(1.0, start_income / income) if income > 0 else 1.0
