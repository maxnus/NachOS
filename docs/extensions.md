# Extensions

An extension is a feature a bot opts into: speed mining, a threat map, map analysis. It only adds to the api, and
changes nothing NachOS does. NachOS's own are in `sc2nachos.extensions`; anyone else's is a package of its own,
named `sc2nachos-<name>` by convention.

A bot gathers its extensions in an object of its own, whose class it hands its api: NachOS builds it once the game
has started, and `api.extensions` holds it.

```python
from sc2nachos import Api
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos_threat import ThreatMap  # someone else's, made up for this example


class Extensions:
    """This bot's extensions."""

    def __init__(self, api: Api) -> None:
        self.speed_mining = SpeedMining(api)
        self.threat = ThreatMap(api, decay=0.9)


api = Api(extensions=Extensions)
```

```python
@api.on(TurnEvent)
def defend(event: TurnEvent) -> None:
    if api.extensions.threat.damage_at(api.map.start_location) > 0:
        ...
```

## Building them

- **NachOS builds the bot's object once, with the api, after reading the game's map, tables and first observation
  and before `GameStartEvent`.** `api.map`, `api.expansions`, `api.units` and the rest answer while it is built, so an
  extension works out what it needs from the map in its constructor.
- **The object is the bot's own, and typed as its class**: a type checker knows `api.extensions.threat` is a
  `ThreatMap` and refuses `api.extensions.thret`. An `Api()` is an `Api[None]`, whose `extensions` is `None`.
  `api.extensions` raises `NotPlayingError` before the game starts, and `RuntimeError` while the object is built: an
  extension is handed the others it needs in its constructor, as `ThreatMap` would be handed a map analysis built on
  the line before.
- **An api plays one game**, so the extensions are built once, and nothing about them is reset.

## Where their handlers run

An extension subscribes its handlers in its constructor, with `api.on`, **at `EventPriority.BEFORE` or
`EventPriority.AFTER`**, which only a handler subscribed while NachOS builds the extensions takes, and which it must
take: any other priority raises `ValueError`, and so does one of these for any other handler. Events go out priority
first ([events](events.md#when-events-come)), so a handler before the bot runs ahead of every handler of the bot's of
the events handed out with its own, and one after the bot behind them all, in the order they subscribed. Those are a
turn's events but `TurnStartEvent`, which goes out alone, before the rest, as an event a handler emits goes out alone,
at once: an extension reading each observation before any handler of the bot's does so on `TurnStartEvent`.

- **Before the bot**, an extension works out what the bot's handlers read, as upgrade inference does on
  `TurnStartEvent`. An order the bot then gives a unit the extension ordered goes out last and stands, as a unit takes
  the last order it is given in a turn ([orders](orders.md)).
- **After the bot**, an extension acts on what the bot did in the turn, as speed mining does, seeing every order the
  bot gave. Its own orders go out last, so it leaves alone a unit the bot ordered in the turn, as `api.orders.issued_to`
  reads.

A handler an extension subscribes later, on `GameStartEvent` say, is subscribed as the bot's are, at a priority of the
bot's.

## Writing one

An extension is a class whose constructor takes the api, then what it depends on and its options:

```python
from typing import Any

from sc2nachos import Api, GameTime
from sc2nachos.events import EventPriority, TurnEvent
from sc2nachos.geometry import PointLike
from sc2nachos.units import Unit, UnitDict


class ThreatMap:
    """The damage enemies could deal at each point, decaying as they go out of sight."""

    def __init__(self, api: Api[object], *, decay: float) -> None:
        self._api = api
        self._decay = decay
        self._last_seen: UnitDict[Unit[Any], GameTime] = UnitDict()
        api.on(TurnEvent, priority=EventPriority.BEFORE)(self._update)

    def damage_at(self, point: PointLike) -> float: ...

    def _update(self, event: TurnEvent) -> None: ...
```

- **Take the api as `Api[object]`**, which takes an api of any extensions. Under a type checker's standard mode a bare
  `Api` does too.
- **Use only what NachOS makes public**: no module, function or attribute of NachOS's whose name starts with an
  underscore; an extension's own are its business. An extension that does is broken by any release. NachOS's own are
  held to this by a test, so any of them could move to a package of its own unchanged, and NachOS itself never imports
  one.
- **Keep a value for each unit in a `UnitDict`**, which forgets a unit once it is dead, as speed mining keeps each
  worker's field.
- **Never order a unit the bot has not handed over.** Speed mining orders only the workers the bot gives it, and lets
  one go once the bot orders it.

## NachOS's own

- **Speed mining**, `sc2nachos.extensions.speed_mining.SpeedMining`: the workers the bot hands over mine their
  mineral fields, or collect vespene from their gas buildings, each trip ending in a move with the gather or return
  queued behind, which keeps the worker's speed into it
  ([measured](reference/game-behavior/abilities-and-orders.md#what-an-order-does-to-a-units-orders)). How many
  workers mine and how many collect stays the bot's, and which field or gas building each works may be:

  ```python
  @api.on(GameStartEvent)
  def start_mining(event: GameStartEvent) -> None:
      for worker in api.units.own.workers:
          api.extensions.speed_mining.mine(worker)
  ```

  Given no field, `mine` picks one at the base nearest the worker, its nearer fields first, and given one field or
  several, `mine(worker, expansion.mineral_fields)` say, it picks among them alike. It mines only at a base with a
  finished townhall of this player's, an orbital finishing its morph among them, and lets a base's workers go, their
  orders as they are, once its townhall is gone, dead or lifted off: long-distance mining is the bot's to do. A field
  takes two workers at most: with three, speed mining mined less than plain gathers. Where there is no room, `mine`
  returns `None` and leaves the worker as it was.
  `mine` sends the worker to its field at once, its cargo handed in first, unless it is mining minerals already, so a
  worker pulled into a fight or collecting vespene mines from that turn. A worker it cannot send, building, burrowed or
  inside a refinery, it does not take: `mine` returns `None`, and the bot hands it over once it can be sent. A worker
  held that the bot orders elsewhere, to build say, or a drone it burrows, is let go, its orders as they are, and the
  bot hands it over again once it is done; `release(workers)` takes one or several back, and `miners` and
  `workers_at(field)` read which worker mines which field. A worker on a move, unlike one on a gather or a return, can
  be stopped by other units, so no move is given while a unit stands in a worker's way. A worker attacks an enemy it
  can hit as it stands, then goes back to mining, unless speed mining is built with `attack_enemies=False`.

  `collect(worker)` hands a worker over to collect vespene instead, each trip ending in a move as a miner's does. Where
  one coming out of the gas building would meet one going in, both on their moves, the one coming out keeps its plain
  return through the other and is given its move once past, as two on their moves push each other aside. So one or
  two workers a refinery collected 9 to 13% more than on plain gathers, two as much each as one alone, and three as
  much as on plain gathers, since a refinery takes one worker at a time
  ([measured](reference/game-behavior/abilities-and-orders.md#what-an-order-does-to-a-units-orders)). Given no gas
  building, `collect` picks a finished one of this player's with vespene left, and given some, among them: two
  workers at each, at the base nearest the worker with one under two, nearest its townhall first, before a third at
  any, and then a third at the base nearest the worker, farthest first, which is where a third adds the most. A gas
  building takes three workers at most, at a base with a finished townhall of this player's, and its workers are let
  go once it dies or runs dry, or its base's townhall is gone. As `mine` does, `collect` sends the worker at once,
  takes none it cannot send, and lets go of one the bot orders elsewhere; a collector attacks as a miner does, and
  `collectors` and `workers_at(gas_building)` read them, and `workers` every worker held, miners and collectors. A
  worker is held at one field or gas building at a time: `mine` moves a collector to minerals, and `collect` a miner
  to gas. How many collect stays the bot's, and `mine` given no field never picks gas.

  So a bot can keep a number of collectors without naming a gas building: it hands a worker to `collect(worker)`
  while it holds fewer, and calls `release_collectors(count)` when it wants fewer, which releases that many, those
  whose leaving costs the least vespene, and returns them for the bot to hand out again. One the bot has ordered
  elsewhere in the turn leaves first, then a third, at the gas building nearest its townhall first, where it adds the
  least, and then the others, farthest first, the reverse of how `collect` fills them. `release_miners(count)` does
  the same for miners, those at the farther half of a base's fields first, the reverse of how `mine` picks. The API
  reference says the rest.
- **Upgrade inference**, `sc2nachos.extensions.upgrade_inference.UpgradeInference`: the enemy's upgrades read off
  what only an upgrade can cause, beyond the attack, armor and shield levels NachOS reads into `api.enemy.upgrades`
  itself: an enemy warp gate or burrowed unit, a stimmed, charging, lunging or cloaked enemy, a marine at 55 health, a
  unit of the bot's slowed by Concussive Shells or matrixed, an enemy storm, and the speed upgrades enemy units move
  at. It adds what it reads with `api.enemy.assume_upgrades`, as a bot would, so every enemy unit's weapons and speed
  count it. It reads each turn's observation on `TurnStartEvent`, at `EventPriority.BEFORE`, before any handler of the
  bot's, and not the one the game ended on, which gets no turn. It has nothing to call, and options for its speed
  upgrades alone:

  ```python
  class Extensions:
      def __init__(self, api: Api) -> None:
          self.upgrade_inference = UpgradeInference(api)
  ```

  With `include_speed_upgrades`, on unless `UpgradeInference(api, include_speed_upgrades=False)` turns it off, it also
  reads ten speed upgrades off how fast enemy units move, from Metabolic Boost to Flux Vanes, as its docstring lists.
  A unit that moved steadily at its speed with the upgrade through one window shows it, and one window that shows it
  assumes the upgrade for good, from the game's first step. Steadily means within 5% of the difference between its
  speeds without the upgrade and with it at every observation, or within `speed_tolerance`, and the windows are 22
  steps long, or as long as `speed_window`, a `GameTime`, says; the replays checked these defaults alone. The rule was
  checked over 1,296 playbacks of
  AI Arena's games observed at every step, with no wrong assumption in the 433 in which one was possible (tool
  `replay_movement`), counting only from the earliest each upgrade could finish, so a game's first minutes went
  unchecked; a bot that observes fewer steps tells less well a unit that moved steadily from one that stopped and
  started again. It measures every visible enemy unit of the types those upgrades speed up, every turn.

  It hands out an `EnemyUpgradeAssumedEvent` for each upgrade it adds to `api.enemy.upgrades`, an `AnyUpgradeEvent`,
  which `only` selects by the upgrade:

  ```python
  from sc2nachos.extensions.upgrade_inference import EnemyUpgradeAssumedEvent


  @api.on(EnemyUpgradeAssumedEvent.only(UpgradeId.ZERGLING_SPEED))
  def expect_fast_zerglings(event: EnemyUpgradeAssumedEvent) -> None: ...
  ```

  It does not come for what the bot assumes itself, nor for a level NachOS reads off the enemy's units, and comes at
  once, as upgrade inference reads, so before any of the bot's handlers of `TurnStartEvent`.

  It reads the buffs on every visible unit, so a buff the curated ids leave out raises `UncuratedIdError`, as reading
  `Unit.buffs` does.
- **The build planner**, `sc2nachos.extensions.planner.Planner`, for Terran: given a reward, a horizon and the state of
  the game, what to make and when, as a list of items in order, each with the time it is expected to start. It solves a
  mixed-integer linear program over a grid of time intervals, which scipy's HiGHS solves, so whether to expand or make
  marines now follows from the economy it models, not from a rule. The objective stays the bot's: it hands the planner a
  `Reward` and never the other way round.

  ```python
  from sc2nachos.extensions.planner import CurveSegment, Planner, Reward, WeightCurve


  class Extensions:
      def __init__(self, api: Api) -> None:
          self.planner = Planner(api)


  @api.on(TurnEvent)
  def replan(event: TurnEvent) -> None:
      if api.step % 448 == 0:
          marines = WeightCurve([CurveSegment(weight=1.0, end_count=30), CurveSegment(weight=0.3)])
          reward = Reward.weighted({UnitTypeId.MARINE: marines, UnitTypeId.MEDIVAC: 2.0})
          plan = api.extensions.planner.plan_now(reward)
  ```

  What the bot supplies is the reward, summed a second at a time over the horizon and discounted by its `half_life`, 3
  minutes by default, so that a unit 3 minutes into the plan counts half of one now, and `None` for no discount. What
  units are worth is a list of `terms`, which add up. A `RewardTerm` counts a group of unit types together, each unit
  for what the group says, and gives their count a `WeightCurve`: a list of `CurveSegment`, each a `weight` for every
  unit up to its `end_count`, the weights never rising, so that more of them are worth less each and a plan buys a mix
  rather than only the best type for its cost. A term counts from a game time, its `start`, to its `end`, the whole game
  by default. Three classmethods build the usual terms. `RewardTerm.weight(units, weight)` is one worth for every unit,
  of one unit type or of several counted together, a mapping of how much one unit of each counts: with `end_count=...`
  for each unit up to a count the bot wants and none past it, such as one detector from a raven or a missile turret, or
  as many as the enemy's air units from marines and vikings; with `start=...` and `end=...` for the game times it counts
  between; with `time_factor=...` for a function of the game time that gives what its worth is multiplied by then, never
  negative, such as `lambda time: 1 + time.seconds / 600` for a worth that doubles over 10 minutes, which a plan asks at
  the end of each of its intervals; with `count_factor=...` for such a function that `end_count` is multiplied by, for
  more units wanted as the game goes on where `time_factor` makes the same units worth more, such as `lambda time:
  time.seconds / 60` with `end_count=1` for a viking to each minute gone; and with `requirements=[...]` for upgrades it
  counts only once done, such as what marines are worth more with combat shield, which a plan then starts whole wherever
  in its horizon. `RewardTerm.curve(units, weights)` takes the same for a `WeightCurve`. `RewardTerm.combination(groups,
  weight)` is for units worth something in combination: each group is counted, and each unit of the smallest count is
  worth the weight, so that the scarcest group limits what the others are worth. `[{MEDIVAC: 8}, {MARINE: 1, MARAUDER:
  2}]` with 0.3 is 0.3 for each marine a medivac heals, of which it heals 8, a marauder for two, beside the terms for
  what each is worth without the other: a plan with 24 marines finds a third medivac worth making and a fourth not. It
  takes `end_count`, `start`, `end`, `time_factor`, `count_factor` and `requirements` as `weight` does, and no negative
  weight. What units are worth changes at a game time where one term ends and another starts, the time in both: a term
  counts from its `start` and no longer at its `end`. `Reward.weighted({unit_type: weight})` is a reward of a term for
  each type, a number or a curve, and takes more `terms` beside. A weight may be negative, for a unit a plan should make
  only where something else needs it, and a curve's weights may not rise from piece to piece. Beside its terms a reward
  holds what each upgrade done is worth, and how long the state at the horizon counts for after it. Each mineral and
  each vespene left in stock at the horizon is worth the reward's `stock_weight` besides, 0.0001 by default: far less
  than what it could have bought under weights around 1, and enough that a plan's workers bring in what they can where
  it buys nothing more. A reward of much smaller weights wants it smaller in proportion. `plan_now` takes the horizon, 5
  minutes by default, and the share of each unit type the bot expects lost a second, so that only what is expected to
  survive counts: a number, or a [value that depends on the game time](time-dependent-values.md) for losses that start
  later or come at once, a `TimeCurve`, a `TimeSeries` that covers the plan, or a function in a `TimeFunction`, which a
  plan reads at each game step of its horizon, and which passes as a term's `time_factor` too. A plan makes nothing that
  pays back only past its horizon: over 5 minutes from a game's start, a reward on army units alone takes no expansion,
  and one that also values SCVs, or command centers, takes it. A longer horizon weighs such things itself and solves
  slower, half again to twice as long over 10 minutes; under 4 minutes a plan's first steps get worse. A unit counts as
  one type of its `equivalent_types`, the one an order makes: a flying barracks as a barracks, and a hellbat as a
  hellion, which needs no armory, so a plan makes hellions, and an armory only if the reward values one.

  Everything else a plan rests on is the bot's to read and change. `planner.state()` returns the `PlannerState`
  `plan_now` plans from, plain values read off the game: stock, supply, finished units, what is in production and when
  it is done, the structures SCVs build and whether one still walks there, and the mineral fields, refineries, workers
  collecting vespene and free geysers at its bases, pooled, since the planner never knows which base a worker is at.
  `plan_now(state=...)` plans from one the bot changed instead. `plan_now(economy=...)` takes an `EconomyModel`: what
  each worker at a field or a refinery brings in a second, a list from the first worker on, what each orbital command's
  MULEs mine, what a new command center brings, how long an SCV walks to build each structure, and how long a worker
  that starts to mine or to collect brings in nothing. Its defaults are the rates measured speed mining, with two
  workers a field and three a refinery, the third collecting about half of what each of the first two does, a walk of 10
  seconds to a command center and 6 to anything else, and 3 seconds before a worker's first income.
  `plan_now(start_limits=...)` takes the most a plan may start of a unit type or an upgrade, none of it for 0. A plan
  makes only what the reward values and what that needs, with SCVs, supply depots, command centers, orbital commands,
  refineries and add-ons, so the limits are for those the bot will not make itself, a reactor or an orbital command, and
  for its own count of command centers, such as the expansions it means to take less those an SCV is already on its way
  to build on, which the state holds as worker builds. A base about to fall, with no refinery yet, left out, the MULEs'
  energy spent on scans, one expansion more and no reactor on a starport:

  ```python
  import dataclasses

  from sc2nachos.extensions.planner import EconomyModel

  planner = api.extensions.planner
  state = planner.state()
  state = dataclasses.replace(
      state,
      mineral_fields=state.mineral_fields - len(falling_base.mineral_fields.alive),
      free_geysers=state.free_geysers - len(falling_base.geysers),
  )
  economy = EconomyModel(mule_minerals_per_orbital_second=0.0)
  start_limits = {UnitTypeId.COMMAND_CENTER: 1, UnitTypeId.REACTOR_STARPORT: 0}
  plan = planner.plan_now(reward, horizon=GameTime(minutes=10), state=state, economy=economy, start_limits=start_limits)
  ```

  What it reads back is a `BuildPlan`: its `items`, each a `PlanItem` of a unit type or an upgrade, when it is expected
  to `start`, and its `duration` and `end`, which a bot carries out in order, each as soon as it can be afforded, rather
  than to the second; and its `intervals`, each a `PlanInterval` with the workers the plan has in it and its `duration`,
  from its `start` to its `end`. Each of the two is a `PlanMoment`, what the planner expects at that `time`: the
  `resources` in stock, the minerals and vespene `collected` in the game by then, the `units` of each type alive, and
  how much of each of the `upgrades` is done, 1 for one done. A `BuildPlan` also holds its `reward`, what it is worth by
  the reward it was planned for, and a `solve_report`: the solver's `gap`, whether it proved the plan the best,
  `is_optimal`, and the `seconds` the solve took. One interval's end is the next one's start, the same object; the first
  interval's start is the state the plan was made from, and the last one's end its horizon, so
  `plan.intervals[-1].end.units` is what the plan comes to. `plan.interval_at(time)` is the interval a game time is in,
  or `None` outside the plan: its workers are those the plan has at that time, and between its start and its end the
  plan says nothing of the stock, the units or the upgrades. `plan.moment_at(time)` is what the planner's model has
  there, which jumps at the edges: the units and upgrades of the interval's start, what its income has brought in by
  then, and the stock with all that starts in the interval paid at its start, which is less than nothing just after an
  interval's start by what the plan has spent of income yet to come. `plan_now` returns `None` if the solver finds no
  plan within its limits, and `planner.plan` holds the last one it found. The planner orders nothing and holds no unit:
  where a structure goes, which SCV builds it, and what to drop once the game moves on stay the bot's.

  How far a plan was off shows some seconds on. `plan.deviation_of(state)` takes a later `PlannerState`, such as
  `planner.state()` just before the next `plan_now`, and returns a `PlanDeviation`: the game's numbers less what
  the plan expected at that time, so that the plan was optimistic where one is negative. Its three parts say what to
  change. Less `collected` than expected is the economy's: the workers brought in less than `EconomyModel` has them
  bring, so lower its rates or raise its `initial_harvest_delay`. As much collected and more `resources` in stock is the
  bot's, which started less than the plan had it start. As much of both and fewer `units` of a type is units lost, which
  `loss_rates` are for. It is `None` for a state from before the plan or after its horizon. Within an interval the
  plan's stock is the model's, everything that starts in it paid at its start, where a bot pays for each item as it can.
  From the planner's `spendable_income_fraction` of an interval on, a half by default, that is what a bot holds that
  starts each item as soon as it can pay; before it the bot may still be saving up, and hold up to what starts in the
  interval more. So the stock and the units are best compared soon after a plan is made, where intervals are short, and
  what is collected at any time.

  How many workers mine and how many collect vespene is the plan's to choose, interval by interval, and each interval
  holds them as its `miners` and `collectors`. A bot holds its collectors at the first interval's,
  `plan.intervals[0].collectors`, moving a worker when the next plan says another number, and has every worker it needs
  nowhere else mine. Where a plan needs no more vespene, its workers mine rather than collect it while a field brings in
  more than a refinery. With a `stock_weight` of 0 a plan may leave workers idle where what they would bring in buys
  nothing before its horizon, as in its last intervals, and its stocks may read less than its workers would bring in. A
  plan moves no worker for nothing. One that starts to mine or to collect brings in nothing for the economy's
  `initial_harvest_delay`, 3 seconds by default, whatever the interval's length: a worker moved between minerals and
  vespene, and a new SCV on its first walk. A builder back from its structure pays its walk back instead. The plan
  starts from the state's `vespene_collectors`, the workers the game counts at its refineries, with every other SCV that
  builds nothing mining, so a plan that keeps them pays nothing.

  `plan_now` solves before it returns, within 3 seconds of wall-clock time by default, so a turn that plans is that much
  longer. `Planner(api, node_limit=...)` stops the solver after so many nodes of branching instead, which makes a plan
  the same on every machine, for tests and recorded games. Before either limit the solver stops once the plan is proven
  within `relative_gap` of the best, 0.0001 of what it is worth by default, and `Planner(api, relative_gap=...)` takes a
  larger one for a faster solve. `Planner`'s docstring says what the model holds: the items from the game's data, pruned
  to what the reward needs, the workers pooled over every field and refinery, the SCV away from the walk to a structure
  through the build to the walk back, and starts on the first second of an interval of its `TimeGrid`, by default 34
  game steps long at first, 1.5 seconds, and doubling every time the time from the plan's start doubles from 544 steps,
  24 seconds. A worker's 272 steps are eight of the first intervals, so workers trained one after another start as far
  apart as in the game. A build no interval divides is planned to the interval where one follows another: one that runs
  into an interval by less than a quarter of it leaves the interval free, so marauders follow each other every 476 steps
  for their 480, and one that runs further takes all of it, marines every 408 for their 400. `Planner(api,
  max_ignored_overrun=...)` takes another share of an interval, 0 for none: a higher one lets a plan start the next
  build up to that much of an interval before the game could.

  An item starts in whole numbers for a time from the plan's start and is fluid after it, starting in fractions: the
  solver weighs fractions far faster, and the bot carries out only the near term before it plans again. By default that
  time grows with what the item costs and shrinks as the income grows. At the income a game starts with it is 4 minutes
  for a command center, 2 for an upgrade of 100 minerals and 100 vespene, 1 for a supply depot and 30 seconds for an
  SCV; at three times that income it is a third of each, but never under 30 seconds. `Planner(api, fluid_after=...)`
  takes a function that returns another time for a unit type or an upgrade, or `None` for its default:

  ```python
  def fluid_after(item: UnitTypeId | UpgradeId) -> GameTime | None:
      return GameTime(seconds=20) if item == UnitTypeId.SUPPLY_DEPOT else None
  ```

  `GameTime.INFINITE` keeps an item whole throughout. Longer makes a plan exact further ahead, and the solve slower. A
  `PlanItem` that starts where its item is fluid reads `is_fluid`: it stands for the fractions whose sum reached a
  whole number there, rounded, so a bot can tell what the plan started in whole from what it only expects to follow.
  What an item needs first, a structure that unlocks it or the upgrade before it, it needs whole, fluid or not: no
  starport starts before a whole factory is done. `Planner(api, unlock_fraction=...)` takes the share of one that
  unlocks, 1 by default. Under a half it is less of a structure than a plan lists as an item. With 0 the fractions done
  count as they are, for a faster solve: a plan then makes its far units on a hundredth of a factory, which it never
  lists, so a bot that follows it never builds one.

  What starts in an interval is paid from the stock at the interval's start and a fraction of what comes in during it,
  `Planner(api, spendable_income_fraction=...)`, a half by default: an item that can be afforded in an interval's first
  half counts as started at its start, and one that can only later waits for the next, so a start is rounded to the
  nearer edge of the grid. With 0 every start waits for the stock and is up to an interval late; with 1 every one is as
  early. It counts for most in the long far intervals, where a half of an interval's income is much: against a finer
  grid, the army a plan of the default grid ends with was within 0.3% from a game's start and from 6:00, and 4.7% too
  large from 3:00. A bot whose plans prove optimistic, by `plan.deviation_of`, lowers it.
