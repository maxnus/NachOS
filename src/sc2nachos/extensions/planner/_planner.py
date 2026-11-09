"""The build planner: what to make and when, for a reward the bot hands it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from sc2nachos.extensions.planner._economy import Economy
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

_NO_LOSSES: Mapping[UnitTypeId, float] = MappingProxyType({})


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
    - An item starts in whole numbers until a time from the plan's start, and in fractions after it, so that the solver
      weighs the far term without branching over it; the bot carries out the near term and plans again before the
      rest. The time is what `whole_until` returns for the item's unit type, or where it returns `None`, or is `None`,
      60 seconds for each 100 minerals and vespene the item costs, but at least 30: a command center 4:00, a factory or
      a starport 2:30, a barracks or an orbital command 1:30, a supply depot 1:00, an SCV or a marine 0:30. An upgrade
      starts whole over any horizon, since each is made once at most, and `GameTime.INFINITE` keeps a unit type so.
      Longer times make a plan exact further ahead, and the solve slower.

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
        "_whole_until",
    )

    def __init__(
        self,
        api: Api[object],
        *,
        grid: TimeGrid | None = None,
        time_limit: float | None = 3.0,
        node_limit: int | None = None,
        whole_until: Callable[[UnitTypeId], GameTime | None] | None = None,
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
        self._whole_until = whole_until
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
        new_command_center_limit: int | None = None,
    ) -> Plan:
        """The plan worth the most by `reward` over `horizon` from `state`, which `plan` holds from then on. `state` is
        `game_state()` now if `None`, and its time is the plan's start. The plan's workers mine, collect and walk as
        `economy` says. A unit of a type in `loss_rates` is expected lost at that share a second, those alive now and
        those the plan makes, so that only the share expected alive counts. The plan starts at most
        `new_command_center_limit` command centers, any number if `None`: the bot's own count, such as the expansions it
        means to take less those an SCV is already on its way to build on, which `state` holds as worker builds. It
        solves within the planner's limits, before it returns.

        Raises `ValueError` for a unit type or an upgrade in the reward that Terran does not make, a horizon not longer
        than 0, a negative loss rate or `new_command_center_limit`, or a negative time from `whole_until` or from
        `economy`'s `walk_time`; and `NoPlanFoundError` if the solver finds no plan within its limits.
        """
        if any(rate < 0 for rate in loss_rates.values()):
            raise ValueError(f"loss rates must not be negative: {dict(loss_rates)}")
        if new_command_center_limit is not None and new_command_center_limit < 0:
            raise ValueError(f"new_command_center_limit must not be negative, not {new_command_center_limit}")
        edges = self._grid.edges(horizon)
        items = self._model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
        program = PlanProgram(
            items=items,
            model=self._model,
            edges=edges,
            state=self.game_state() if state is None else state,
            reward=reward,
            economy=economy,
            loss_rates=loss_rates,
            new_command_center_limit=new_command_center_limit,
            whole_until_seconds=whole_until_seconds_of(items, whole_until=self._whole_until),
        )
        plan = program.solve(time_limit=self._time_limit, node_limit=self._node_limit)
        if plan is None:
            raise NoPlanFoundError(f"the solver found no plan within {self._time_limit} s and {self._node_limit} nodes")
        self._plan = plan
        return plan


def whole_until_seconds_of(
    items: Sequence[Item], *, whole_until: Callable[[UnitTypeId], GameTime | None] | None
) -> tuple[float, ...]:
    """The seconds from the plan's start until which each of `items` starts in whole numbers: for a unit, what
    `whole_until` returns for its type, or its default where that is `None`; for an upgrade, its default.

    Raises `ValueError` for a negative time from `whole_until`.
    """
    seconds: list[float] = []
    for item in items:
        chosen = None if whole_until is None or item.unit_type is None else whole_until(item.unit_type)
        if chosen is not None and chosen < GameTime.ZERO:
            raise ValueError(f"whole_until must not return a negative time, but returned {chosen} for {item.name}")
        seconds.append((item.default_whole_until if chosen is None else chosen).seconds)
    return tuple(seconds)
