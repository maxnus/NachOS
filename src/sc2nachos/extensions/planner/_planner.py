"""The build planner: what to make and when, for a reward the bot hands it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.extensions.planner._economy_model import EconomyModel, minerals_mined_a_second
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._plan_variables import NewBase
from sc2nachos.extensions.planner._planner_state import PlannerState, read_planner_state
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.extensions.planner._time_grid import TimeGrid
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from sc2nachos import Api, Expansion
    from sc2nachos.extensions.planner._build_plan import BuildPlan
    from sc2nachos.extensions.planner._reward import Reward
    from sc2nachos.extensions.planner._terran_model import Item
    from sc2nachos.gametime import TimeDependent
    from sc2nachos.ids import UpgradeId

_NO_LOSSES: Mapping[UnitTypeId, float | TimeDependent[float]] = MappingProxyType({})
_NO_LIMITS: Mapping[UnitTypeId | UpgradeId, int] = MappingProxyType({})


@final
class Planner:
    """The build planner, for Terran: given a reward, a horizon and the state of the game, what to make and when.

    `plan_now` solves a mixed-integer linear program over a grid of time intervals for the plan worth the most by the
    reward, and returns it as a `BuildPlan`: the items to make in order, each with the time it is expected to start. A
    bot carries it out in order, each item as soon as it can be afforded, and asks for a new plan as the game goes on.
    The planner orders nothing and holds no unit; where structures go and which SCV builds each stay the bot's.

    Build it once the game has started, in the bot's own object an api is given as `extensions`:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.planner import Planner


    class Extensions:
        def __init__(self, api: Api) -> None:
            self.planner = Planner(api)


    api = Api(extensions=Extensions)
    ```

    It plans from the `PlannerState` that `state()` reads as `plan_now` is called, unless handed another. The model
    of the game:

    - What each item costs, how long it takes, the supply it takes and gives, what makes it and what it needs first,
      from the game's data; an item is pruned unless the reward values it or it is needed for one that does, or it is
      the economy: SCVs, supply depots, command centers, orbital commands and refineries.
    - A unit counts as one type of its `equivalent_types`, in the reward, the loss rates and the state alike: the one an
      order makes rather than one morphed from another, and of two, the one needing fewer techs. So a flying barracks
      counts as a barracks, and a hellbat as a hellion, which needs no armory: a plan makes hellions, and an armory
      only if the reward values one. A rich refinery counts as a refinery, which its order makes.
    - What workers mine and collect, what each orbital command's MULEs add, and how far an SCV walks to build: the
      `EconomyModel` handed to `plan_now`. The workers are pooled over every field and refinery, as if each could work
      anywhere at once.
    - A new command center brings the mineral fields and the geysers of the next of the `expansions` handed to
      `plan_now`, and a plan starts none without them.
    - How many workers mine and how many collect vespene is the plan's choice in each interval, which the interval holds
      as its `miners` and `collectors`. A worker that starts to mine or to collect brings in nothing for the economy's
      `initial_harvest_delay`, a new SCV too, but a builder back from its walk: every rise of the miners or collectors,
      interval by interval, is such a start, from the state's collectors and every other SCV that builds nothing mining.
      Nothing but the reward's `stock_weight` keeps a worker at work where what it brings in buys nothing more.
    - An SCV is away from its work from the walk to a structure through the build to the walk back, the economy's walk
      each way.
    - A command center's morph keeps it from training SCVs, a reactor lets its host make two units at a time, and a
      tech lab makes the units that need one and researches on its own.
    - Nothing starts before `unlock_fraction` of what it needs first is done, a structure granting its tech or the
      upgrade before it: by default 1, no starport before a whole factory, though a factory's starts be fluid. A
      lower one unlocks on that share of one; under a half, on less of a structure than a plan lists as an item. With
      0 the fractions done count as they are, a hundredth of a factory unlocking a starport: the solve is faster, and
      a plan then makes its far units on a structure it never lists.
    - What starts in an interval is paid from the stock at the interval's start and `spendable_income_fraction` of
      what comes in during it, by default a half. An item that can be afforded in an interval's first half then counts
      as started at its start, and one that can only later waits for the next: a start is rounded to the nearer edge
      of the grid, at most half an interval early or late. With 0 every start waits for the stock, up to an interval
      late; with 1 every one is as early. It counts for most where intervals are long: a lower fraction makes a
      plan's far end more cautious, by several percent of its army over the default grid.
    - Starts sit on the first second of an interval of `grid`, by default `TimeGrid.doubling()`, and builds count as
      done from the first edge at or after their end. So production chained on one producer starts a whole number of
      intervals apart while its starts are whole. A build that runs into an interval by less than `max_ignored_overrun`
      of it, by default a quarter, leaves the interval free, and one that runs further into it takes all of it: on the
      default grid SCVs start every 272 game steps, as in the game, marauders every 476 for their 480, and marines every
      408 for their 400. With 0 no overrun is ignored; with 1 every one is. Fluid starts keep their producer busy for
      their build time alone.
    - An item starts in whole numbers until a time from the plan's start and is fluid after it, starting in fractions,
      so that the solver weighs the far term without branching over it; the bot carries out the near term and plans
      again before the rest. The time is what `fluid_after` returns for the unit type the item makes or the upgrade it
      researches, or where it returns `None`, or is `None`, a default by what the item costs and what the state brings
      in. At the income a game starts with, twelve workers on eight mineral fields, it is 60 seconds for each 100
      minerals and vespene: a command center 4:00, a factory or a starport 2:30, an upgrade of 100 and 100 2:00, a
      barracks or an orbital command 1:30, a supply depot 1:00. At a higher income it is shorter in proportion, a
      command center 1:12 at 3.3 times the income, since an item is then less of what comes in; at a lower one it is no
      longer. It is never under 30 seconds, an SCV's or a marine's at any income. The income is what the state's SCVs
      mine by the economy if every one mines, with its orbital commands' MULEs. `GameTime.INFINITE` keeps an item whole
      over any horizon. Longer times make a plan exact further ahead, and the solve slower.

    How long a solve may take is each `plan_now`'s to say.

    Raises `ValueError` for a player of a race other than Terran, or a `max_ignored_overrun`, an `unlock_fraction` or a
    `spendable_income_fraction` outside 0 to 1; and `NotPlayingError` if built before the game starts.
    """

    __slots__ = (
        "_api",
        "_grid",
        "_max_ignored_overrun",
        "_model",
        "_plan",
        "_fluid_after",
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
    ) -> None:
        if api.player.race is not Race.TERRAN:
            raise ValueError(f"the planner plans for Terran alone, and this player is {api.player.race.name}")
        if not 0 <= max_ignored_overrun <= 1:
            raise ValueError(f"the overrun ignored must be a share of an interval, 0 to 1, not {max_ignored_overrun}")
        if not 0 <= unlock_fraction <= 1:
            raise ValueError(f"the unlock fraction must be 0 to 1, not {unlock_fraction}")
        if not 0 <= spendable_income_fraction <= 1:
            raise ValueError(f"the spendable income fraction must be 0 to 1, not {spendable_income_fraction}")
        self._api = api
        self._grid = TimeGrid.doubling() if grid is None else grid
        self._fluid_after = fluid_after
        self._max_ignored_overrun = max_ignored_overrun
        self._unlock_fraction = unlock_fraction
        self._spendable_income_fraction = spendable_income_fraction
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
        """The plan worth the most by `reward` over `horizon` from `state`, which `plan` holds from then on. `state` is
        `state()` now if `None`, and its time is the plan's start. The horizon is 5 minutes by default: nothing that
        pays back only later is planned unless the reward values it itself, an expansion for one, and a longer horizon
        is a slower solve. The plan's workers mine, collect and walk as `economy` says. A unit of a type in `loss_rates`
        is expected lost at that share a second, those alive now and those the plan makes, so that only the share
        expected alive counts. A unit type's rate is that of the type it counts as, the highest of those of one type:
        the higher of a hellion's and a hellbat's. A rate is a number, or a `TimeDependent` that gives it for a game
        time, a `TimeCurve`, a `TimeSeries` or a function in a `TimeFunction`, read at each game step from the plan's
        start to its horizon by one call: none before an attack is expected, say, or a peak for a loss at once, as
        `-math.log(0.7)` for one second loses 30% of the units alive then, whatever the grid. Of a unit type or an
        upgrade in `start_limits` the plan starts at most that many, none for 0, beside what the state holds in
        production: a reactor the bot does not build, or an orbital command it does not morph. A unit type's limit is
        that of the type it counts as, the lowest of those of one type, and one on what the plan would not make anyway
        does nothing. `expansions` are the expansions the bot means to take next, in that order, from `api.expansions`,
        those not built on yet: a plan starts at most that many command centers, the first bringing the mineral fields
        left and the geysers of the first, and so on, and with none, the default, it starts no command center. A command
        center going up, or one an SCV is on its way to build, is not among them: the state holds what it brings, the
        fields and geysers of the expansion it stands at, or is aimed at, and nothing for one at no expansion. It solves
        before it returns, so a turn that plans is that much longer: for at most `time_limit` seconds of wall-clock
        time, 3 by default, or `node_limit` nodes of branching, each unlimited if `None`. A node limit makes the plan
        the same on every machine, a time limit does not. Before either, the solve stops once the plan is proven within
        `relative_gap` of the best there is, relative to what the plan is worth: by default 0.0001, a hundredth of a
        percent, and a larger gap ends the solve sooner with a plan that may be worth that much less. It returns `None`
        if the solver finds no plan within its limits, which leaves `plan` the one before.

        Raises `ValueError` for a unit type or an upgrade in the reward that Terran does not make, a horizon not longer
        than 0, a `time_limit` or `node_limit` not above 0, a negative `relative_gap`, loss rate or start limit, a loss
        rate from a `TimeDependent` that is negative or not finite, a negative time from `fluid_after` or from
        `economy`'s `builder_walk_time`, a factor from a term's `time_factor` or `count_factor` that is negative or not
        finite, or a `state` holding a unit in production that Terran does not make; `TypeError` for a loss rate that is
        neither a number nor gives one for each of many game steps, as a function not in a `TimeFunction`; and
        `IndexError` from a loss rate that does not cover a step of the plan, as a `TimeSeries` recorded for fewer.
        """
        if any(not callable(rate) and rate < 0 for rate in loss_rates.values()):
            raise ValueError(f"loss rates must not be negative: {dict(loss_rates)}")
        if any(limit < 0 for limit in start_limits.values()):
            raise ValueError(f"start limits must not be negative: {dict(start_limits)}")
        _check_solve_limits(time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap)
        edges = self._grid.edges(horizon)
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
            new_bases=[
                NewBase(mineral_fields=len(expansion.mineral_fields.alive), geysers=len(expansion.geysers))
                for expansion in expansions
            ],
            fluid_after_seconds=fluid_after_seconds_of(
                items, fluid_after=self._fluid_after, state=planned_from, economy=economy
            ),
            max_ignored_overrun=self._max_ignored_overrun,
            unlock_fraction=self._unlock_fraction,
            spendable_income_fraction=self._spendable_income_fraction,
        )
        plan = program.solve(time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap)
        if plan is not None:
            self._plan = plan
        return plan


def _check_solve_limits(*, time_limit: float | None, node_limit: int | None, relative_gap: float) -> None:
    """Raise `ValueError` for a `time_limit` or a `node_limit` not above 0, or a negative `relative_gap`."""
    if time_limit is not None and time_limit <= 0:
        raise ValueError(f"a time limit must be above 0 seconds, not {time_limit}")
    if node_limit is not None and node_limit <= 0:
        raise ValueError(f"a node limit must be above 0, not {node_limit}")
    if relative_gap < 0:
        raise ValueError(f"the relative gap must not be negative, not {relative_gap}")


def fluid_after_seconds_of(
    items: Sequence[Item],
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
