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

An extension subscribes its handlers in its constructor, with `api.on`. **A handler subscribed while NachOS builds the
extensions runs before the handlers of its priority subscribed otherwise**, in the order they subscribed; priorities
still come first ([events](events.md#when-events-come)). So what an extension works out on a `TurnEvent` is up to
date when the bot's `TurnEvent` handlers of its priority read it, and an order the bot gives a unit an extension also
ordered goes out last and stands, as a unit takes the last order it is given in a turn ([orders](orders.md)). A
handler an extension subscribes later, on `GameStartEvent` say, takes its place in subscription order like any other.

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
        api.on(TurnEvent, priority=EventPriority.HIGH)(self._update)

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
  `workers_at(field)` read which worker mines which field. A worker on a move, unlike one on a gather or a return, can be stopped by other units, so no move is
  given while a unit stands in a worker's way. A worker attacks an enemy it can hit as it stands, then goes back to
  mining, unless speed mining is built with `attack_enemies=False`.

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
  least, and then the others, farthest first, the reverse of how `collect` fills them. `release_miners(count)` does the same for miners, those at the farther half of a
  base's fields first, the reverse of how `mine` picks. The API reference says the rest.
- **Upgrade inference**, `sc2nachos.extensions.upgrade_inference.UpgradeInference`: the enemy's upgrades read off
  what only an upgrade can cause, beyond the attack, armor and shield levels NachOS reads into `api.enemy.upgrades`
  itself: an enemy warp gate or burrowed unit, a stimmed, charging, lunging or cloaked enemy, a marine at 55 health, a
  unit of the bot's slowed by Concussive Shells or matrixed, an enemy storm, and the speed upgrades enemy units move
  at. It adds what it reads with `api.enemy.assume_upgrades`, as a bot would, so every enemy unit's weapons and speed
  count it. It reads each turn's observation on `TurnStartEvent`, and the one the game ended on, which gets no turn,
  on `GameEndEvent`, both at `EventPriority.HIGHEST`, before any handler of the bot's. It has nothing to call, and
  options for its speed upgrades alone:

  ```python
  class Extensions:
      def __init__(self, api: Api) -> None:
          self.upgrade_inference = UpgradeInference(api)
  ```

  With `include_speed_upgrades`, on unless `UpgradeInference(api, include_speed_upgrades=False)` turns it off, it also
  reads ten speed upgrades off how fast enemy units move, from Metabolic Boost to Flux Vanes, as its docstring lists.
  Every 22 steps a unit that moved steadily at its speed with the upgrade votes for it, and one at its speed without
  against it, and each upgrade keeps the probability that the enemy has it, from a floor of 1%. It assumes the upgrade
  at 99%, as one block of 22 steps with a vote for it does, and forgets it below 50%, as one with only votes against
  after that does. The three are arguments, `speed_upgrade_floor`, `assume_speed_upgrade_at` and
  `forget_speed_upgrade_below`, which set how much evidence it asks for, not what a block of votes weighs: from 0.1%
  assuming at 99.9%, it takes two blocks. The replays checked the defaults alone. It forgets only a speed upgrade it
  assumed itself: one the bot assumed first stays, but one the bot assumes after it did, it may forget too. The rule was
  checked over 1,296 playbacks of AI Arena's games observed at every step, with no wrong assumption in the 433 in which
  one was possible (tool `replay_movement`); a bot that observes fewer steps tells less well a unit that moved steadily
  from one that stopped and started again. It measures every visible enemy unit of the types those upgrades speed up,
  every turn.

  It reads the buffs on every visible unit, so a buff the curated ids leave out raises `UncuratedIdError`, as reading
  `Unit.buffs` does.
