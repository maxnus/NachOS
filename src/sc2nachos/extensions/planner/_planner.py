"""The build planner: what to make and when, for a reward the bot hands it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, final

from sc2nachos.extensions.planner._economy import Economy, minerals_mined_a_second
from sc2nachos.extensions.planner._errors import NoPlanFoundError
from sc2nachos.extensions.planner._game_state import GameState, read_game_state
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.extensions.planner._time_grid import TimeGrid
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Race

if TYPE_CHECKING:
    from sc2nachos import Api
    from sc2nachos.extensions.planner._plan import Plan
    from sc2nachos.extensions.planner._reward import Reward
    from sc2nachos.extensions.planner._terran_model import Item
    from sc2nachos.ids import UpgradeId

_NO_LOSSES: Mapping[UnitTypeId, float] = MappingProxyType({})
_NO_LIMITS: Mapping[UnitTypeId | UpgradeId, int] = MappingProxyType({})
# By default an item starts whole for this long for each 100 minerals and vespene it costs at the income a game starts
# with, that of so many workers on so many mineral fields, less in proportion at a higher income, but at least the
# shortest.
_DEFAULT_WHOLE_SECONDS_A_HUNDRED_RESOURCES: Final = 60.0
_SHORTEST_DEFAULT_WHOLE_SECONDS: Final = 30.0
_WORKERS_AS_A_GAME_STARTS: Final = 12
_MINERAL_FIELDS_AS_A_GAME_STARTS: Final = 8


@final
class Planner:
    """The build planner, for Terran: given a reward, a horizon and the state of the game, what to make and when.

    `plan_now` solves a mixed-integer linear program over a grid of time buckets for the plan worth the most by the
    reward, and returns it as a `Plan`: the items to make in order, each with the time it is expected to start. A bot
    carries it out in order, each item as soon as it can be afforded, and asks for a new plan as the game goes on.
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

    It plans from the `GameState` that `game_state()` reads as `plan_now` is called, unless handed another. The model of
    the game:

    - What each item costs, how long it takes, the supply it takes and gives, what makes it and what it needs first,
      from the game's data; an item is pruned unless the reward values it or it is needed for one that does, or it is
      the economy: SCVs, supply depots, command centers, orbital commands and refineries.
    - A unit counts as one type of its `equivalent_types`, in the reward, the loss rates and the state alike: the one an
      order makes rather than one morphed from another, and of two, the one needing fewer techs. So a flying barracks
      counts as a barracks, and a hellbat as a hellion, which needs no armory: a plan makes hellions, and an armory
      only if the reward values one. A rich refinery counts as a refinery, which its order makes.
    - What workers mine and collect, what each orbital command's MULEs add, what a new command center brings, and how
      far an SCV walks to build: the `Economy` handed to `plan_now`. The workers are pooled over every field and
      refinery, as if each could work anywhere at once.
    - An SCV is away from its work from the walk to a structure through the build to the walk back, the economy's walk
      each way.
    - A command center's morph keeps it from training SCVs, a reactor lets its host make two units at a time, and a
      tech lab makes the units that need one and researches on its own.
    - Starts sit on the first second of a bucket of `grid`, by default `TimeGrid.doubling()`, and builds count as done
      from the first edge at or after their end; build times are never rounded. So production chained on one producer
      starts a whole number of buckets apart while its starts are whole: SCVs every 14 seconds on 2-second buckets.
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

    The solve stops at `time_limit` seconds of wall-clock time, or after `node_limit` nodes of branching, each
    unlimited if `None`: a node limit makes the plan the same on every machine, a time limit does not.

    Raises `ValueError` for a player of a race other than Terran, or a `time_limit` or `node_limit` not above 0; and
    `NotPlayingError` if built before the game starts.
    """

    __slots__ = (
        "_api",
        "_grid",
        "_model",
        "_node_limit",
        "_plan",
        "_time_limit",
        "_fluid_after",
    )

    def __init__(
        self,
        api: Api[object],
        *,
        grid: TimeGrid | None = None,
        time_limit: float | None = 3.0,
        node_limit: int | None = None,
        fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None = None,
    ) -> None:
        if api.player.race != Race.TERRAN:
            raise ValueError(f"the planner plans for Terran alone, and this player is {api.player.race.name}")
        if time_limit is not None and time_limit <= 0:
            raise ValueError(f"a time limit must be above 0 seconds, not {time_limit}")
        if node_limit is not None and node_limit <= 0:
            raise ValueError(f"a node limit must be above 0, not {node_limit}")
        self._api = api
        self._grid = TimeGrid.doubling() if grid is None else grid
        self._time_limit = time_limit
        self._node_limit = node_limit
        self._fluid_after = fluid_after
        self._model = TerranModel(api.data)
        self._plan: Plan | None = None

    @property
    def grid(self) -> TimeGrid:
        """The grid of time buckets plans start on."""
        return self._grid

    @property
    def plan(self) -> Plan | None:
        """The plan `plan_now` returned last, or `None` before it has."""
        return self._plan

    def game_state(self) -> GameState:
        """The state of the game now, as the planner reads it: what `plan_now` plans from unless handed another."""
        return read_game_state(self._api, model=self._model)

    def plan_now(
        self,
        reward: Reward,
        *,
        horizon: GameTime,
        state: GameState | None = None,
        economy: Economy = Economy(),
        loss_rates: Mapping[UnitTypeId, float] = _NO_LOSSES,
        start_limits: Mapping[UnitTypeId | UpgradeId, int] = _NO_LIMITS,
        previous_plan: Plan | None = None,
        hysteresis_bonus: float = 0.0,
    ) -> Plan:
        """The plan worth the most by `reward` over `horizon` from `state`, which `plan` holds from then on. `state` is
        `game_state()` now if `None`, and its time is the plan's start. The plan's workers mine, collect and walk as
        `economy` says. A unit of a type in `loss_rates` is expected lost at that share a second, those alive now and
        those the plan makes, so that only the share expected alive counts. Of a unit type or an upgrade in
        `start_limits` the plan starts at most that many, none for 0, beside what the state holds in production: a
        reactor the bot does not build, an orbital command it does not morph, or the expansions it means to take less
        those an SCV is already on its way to build on. A unit type's limit is that of the type it counts as, the lowest
        of those of one type, and one on what the plan would not make anyway does nothing. With a `previous_plan`, such
        as `plan`, each of its items the new plan starts no later than that one expected, counting those of its kind
        before it, is worth `hysteresis_bonus` once, beside the reward, so that the new plan differs from it only for
        more than that. It solves within the planner's limits, before it returns.

        Raises `ValueError` for a unit type or an upgrade in the reward that Terran does not make, a horizon not longer
        than 0, a negative loss rate, start limit or `hysteresis_bonus`, a `hysteresis_bonus` with no `previous_plan`,
        or a negative time from `fluid_after` or from `economy`'s `walk_time`; and `NoPlanFoundError` if the solver
        finds no plan within its limits.
        """
        if any(rate < 0 for rate in loss_rates.values()):
            raise ValueError(f"loss rates must not be negative: {dict(loss_rates)}")
        if any(limit < 0 for limit in start_limits.values()):
            raise ValueError(f"start limits must not be negative: {dict(start_limits)}")
        if hysteresis_bonus < 0:
            raise ValueError(f"hysteresis_bonus must not be negative, not {hysteresis_bonus}")
        if hysteresis_bonus and previous_plan is None:
            raise ValueError("a hysteresis bonus needs the previous plan it favors")
        edges = self._grid.edges(horizon)
        items = self._model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
        planned_from = self.game_state() if state is None else state
        program = PlanProgram(
            items=items,
            model=self._model,
            edges=edges,
            state=planned_from,
            reward=reward,
            economy=economy,
            loss_rates=loss_rates,
            start_limits=start_limits,
            fluid_after_seconds=fluid_after_seconds_of(
                items, fluid_after=self._fluid_after, state=planned_from, economy=economy
            ),
            previous_plan=previous_plan,
            hysteresis_bonus=hysteresis_bonus,
        )
        plan = program.solve(time_limit=self._time_limit, node_limit=self._node_limit)
        if plan is None:
            raise NoPlanFoundError(f"the solver found no plan within {self._time_limit} s and {self._node_limit} nodes")
        self._plan = plan
        return plan


def fluid_after_seconds_of(
    items: Sequence[Item],
    *,
    fluid_after: Callable[[UnitTypeId | UpgradeId], GameTime | None] | None,
    state: GameState,
    economy: Economy,
) -> tuple[float, ...]:
    """The seconds from the plan's start after which each of `items` is fluid, starting in whole numbers until then:
    what `fluid_after` returns for what it makes or researches, or where that is `None`, its default for a plan from
    `state` by `economy`, as `Planner` says.

    Raises `ValueError` for a negative time from `fluid_after`.
    """
    seconds_a_hundred_resources = _DEFAULT_WHOLE_SECONDS_A_HUNDRED_RESOURCES * _start_income_as_a_share_of_the_income(
        state, economy
    )
    seconds: list[float] = []
    for item in items:
        chosen = None if fluid_after is None or item.made is None else fluid_after(item.made)
        if chosen is None:
            by_cost = seconds_a_hundred_resources * (item.minerals + item.vespene) / 100
            seconds.append(max(_SHORTEST_DEFAULT_WHOLE_SECONDS, by_cost))
        elif chosen < GameTime.ZERO:
            raise ValueError(f"fluid_after must not return a negative time, but returned {chosen} for {item.name}")
        else:
            seconds.append(chosen.seconds)
    return tuple(seconds)


def _start_income_as_a_share_of_the_income(state: GameState, economy: Economy) -> float:
    """The income a game starts with as a share of that of `state`, at most 1, each by `economy`: what the SCVs mine if
    every one mines, with the orbital commands' MULEs. It is 1 where the state brings in nothing."""
    start_income = minerals_mined_a_second(
        economy, workers=_WORKERS_AS_A_GAME_STARTS, mineral_fields=_MINERAL_FIELDS_AS_A_GAME_STARTS, orbital_commands=0
    )
    income = minerals_mined_a_second(
        economy,
        workers=state.counts.get(UnitTypeId.SCV, 0),
        mineral_fields=state.mineral_fields,
        orbital_commands=state.counts.get(UnitTypeId.ORBITAL_COMMAND, 0),
    )
    return min(1.0, start_income / income) if income > 0 else 1.0
