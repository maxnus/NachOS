"""The build planner: what to make and when, for a reward the bot hands it."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final

from sc2nachos.events import EventPriority, TurnStartEvent
from sc2nachos.extensions.planner._errors import NoPlanFoundError
from sc2nachos.extensions.planner._plan_program import PlanProgram
from sc2nachos.extensions.planner._snapshot import Snapshot
from sc2nachos.extensions.planner._terran_model import TerranModel
from sc2nachos.extensions.planner._time_grid import TimeGrid
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId
from sc2nachos.match import Race
from sc2nachos.units import OwnUnit, UnitDict

if TYPE_CHECKING:
    from sc2nachos import Api
    from sc2nachos.extensions.planner._plan import Plan
    from sc2nachos.extensions.planner._reward import Reward

_NO_LOSSES: Mapping[UnitTypeId, float] = MappingProxyType({})
_TOWNHALL_MORPHS = (UnitTypeId.ORBITAL_COMMAND, UnitTypeId.PLANETARY_FORTRESS)


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

    It reads the game as `plan_now` is called, and on each `TurnStartEvent`, at `EventPriority.BEFORE`, notes when it
    first saw each command center begin a morph, since the game reports no progress for one. The model of the game:

    - What each item costs, how long it takes, the supply it takes and gives, what makes it and what it needs first,
      from the game's data; an item is pruned unless the reward values it or it is needed for one that does, or it is
      the economy: SCVs, supply depots, command centers, orbital commands and refineries.
    - Workers mine two a field at the rate twelve SCVs speed mined a main's eight fields, a third a field at half that,
      collect three a refinery, and each orbital command adds a MULE's income. A new command center goes to the nearest
      free expansion, which brings eight fields and two geysers.
    - An SCV is away from its work from the walk to a structure through the build to the walk back: `walk_time` each
      way, or for a command center the walk from this player's start to the nearest free expansion.
    - A command center's morph keeps it from training SCVs, a reactor lets its host make two units at a time, and a
      tech lab makes the units that need one and researches on its own.
    - Starts sit on the first second of a bucket of `grid`, by default `TimeGrid.doubling()`, and builds count as done
      from the first edge at or after their end; build times are never rounded. So production chained on one producer
      starts a whole number of buckets apart while its starts are whole: SCVs every 14 seconds on 2-second buckets.

    The solve stops at `time_limit` seconds of wall-clock time, or after `node_limit` nodes of branching, each
    unlimited if `None`: a node limit makes the plan the same on every machine, a time limit does not.

    Raises `ValueError` for a player of a race other than Terran, a `time_limit` or `node_limit` not above 0, or a
    negative `walk_time`; and `NotPlayingError` if built before the game starts.
    """

    __slots__ = ("_api", "_grid", "_model", "_morph_starts", "_node_limit", "_plan", "_time_limit", "_walk_time")

    def __init__(
        self,
        api: Api[object],
        *,
        grid: TimeGrid | None = None,
        time_limit: float | None = 3.0,
        node_limit: int | None = None,
        walk_time: GameTime = GameTime(seconds=6),
    ) -> None:
        if api.player.race != Race.TERRAN:
            raise ValueError(f"the planner plans for Terran alone, and this player is {api.player.race.name}")
        if time_limit is not None and time_limit <= 0:
            raise ValueError(f"a time limit must be above 0 seconds, not {time_limit}")
        if node_limit is not None and node_limit <= 0:
            raise ValueError(f"a node limit must be above 0, not {node_limit}")
        if walk_time < GameTime.ZERO:
            raise ValueError(f"walk_time must not be negative, not {walk_time}")
        self._api = api
        self._grid = TimeGrid.doubling() if grid is None else grid
        self._time_limit = time_limit
        self._node_limit = node_limit
        self._walk_time = walk_time
        self._model = TerranModel(api.data)
        self._morph_starts: UnitDict[OwnUnit[Any], GameTime] = UnitDict()
        self._plan: Plan | None = None
        api.on(TurnStartEvent, priority=EventPriority.BEFORE)(self._note_morph_starts)

    @property
    def grid(self) -> TimeGrid:
        """The grid of time buckets plans start on."""
        return self._grid

    @property
    def plan(self) -> Plan | None:
        """The plan `plan_now` returned last, or `None` before it has."""
        return self._plan

    def plan_now(
        self, reward: Reward, *, horizon: GameTime, loss_rates: Mapping[UnitTypeId, float] = _NO_LOSSES
    ) -> Plan:
        """The plan worth the most by `reward` over `horizon` from now, which `plan` holds from then on. A unit of a
        type in `loss_rates` is expected lost at that share a second, those alive now and those the plan makes, so
        that only the share expected alive counts. It solves within the planner's limits, before it returns.

        Raises `ValueError` for a unit type or an upgrade in the reward that Terran does not make, a horizon not longer
        than 0, or a negative loss rate; and `NoPlanFoundError` if the solver finds no plan within its limits.
        """
        if any(rate < 0 for rate in loss_rates.values()):
            raise ValueError(f"loss rates must not be negative: {dict(loss_rates)}")
        edges = self._grid.edges(horizon)
        items = self._model.items_for(unit_types=reward.unit_types, upgrades=reward.upgrade_weights)
        snapshot = Snapshot.from_api(
            self._api,
            model=self._model,
            morph_starts=self._morph_starts,
            walk_time=self._walk_time,
            loss_rates=loss_rates,
        )
        program = PlanProgram(
            items=items,
            model=self._model,
            edges=edges,
            snapshot=snapshot,
            reward=reward,
            whole_until_seconds=self._grid.whole_until.seconds,
        )
        plan = program.solve(time_limit=self._time_limit, node_limit=self._node_limit)
        if plan is None:
            raise NoPlanFoundError(f"the solver found no plan within {self._time_limit} s and {self._node_limit} nodes")
        self._plan = plan
        return plan

    def _note_morph_starts(self, _: TurnStartEvent) -> None:
        """Note when each command center morphing now was first seen to, and forget those that finished or stopped."""
        noted = self._morph_starts
        morphing: UnitDict[OwnUnit[Any], GameTime] = UnitDict()
        for in_production in self._api.in_production(_TOWNHALL_MORPHS):
            if (producer := in_production.producer) is not None:
                morphing[producer] = noted.get(producer, self._api.time)
        self._morph_starts = morphing
