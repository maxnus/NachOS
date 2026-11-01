# Extensions

An extension is a feature a bot opts into: speed mining, a threat map, map analysis. It only adds to the api, and
changes nothing NachOS does. NachOS's own are in `sc2nachos.extensions`; anyone else's is a package of its own,
named `sc2nachos-<name>` by convention.

A bot hands its api one function, which NachOS calls once the game has started and whose result `api.extensions`
holds:

```python
from dataclasses import dataclass

from sc2nachos import Api
from sc2nachos.extensions.speed_mining import SpeedMining
from sc2nachos_threat import ThreatMap  # someone else's


@dataclass(frozen=True)
class BotExtensions:
    speed_mining: SpeedMining
    threat: ThreatMap


def build_extensions(api: Api) -> BotExtensions:
    return BotExtensions(SpeedMining(api), ThreatMap(api, decay=0.9))


api = Api(extensions=build_extensions)
```

```python
@api.on(TurnEvent)
def defend(event: TurnEvent) -> None:
    if api.extensions.threat.damage_at(api.map.start_location) > 0:
        ...
```

A bot with one extension hands over its class: `Api(extensions=SpeedMining)`, and `api.extensions` is the
`SpeedMining`.

## Building them

- **NachOS calls the function once, with the api, after reading the game's map, tables and first observation and
  before `GameStartEvent`.** `api.map`, `api.expansions`, `api.units` and the rest answer in it, so an extension works
  out what it needs from the map in its constructor.
- **What the function returns is the bot's own**, typed by the function: a type checker knows
  `api.extensions.threat` is a `ThreatMap` and refuses `api.extensions.thret`. An `Api()` is an `Api[None]`, whose
  `extensions` is `None`. `api.extensions` raises `NotPlayingError` before the game starts, and `RuntimeError` while
  the function runs: an extension is handed the others it needs in its constructor, as `ThreatMap` would be handed a
  map analysis.
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

    def damage_at(self, point: PointLike) -> float:
        ...

    def _update(self, event: TurnEvent) -> None:
        ...
```

- **Take the api as `Api[object]`**, which takes an api of any extensions. Under a type checker's standard mode a bare
  `Api` does too.
- **Use only what NachOS makes public**: no module, function or attribute whose name starts with an underscore. An
  extension that does is broken by any release. NachOS's own are held to this by a test, so any of them could move to
  a package of its own unchanged, and NachOS itself never imports one.
- **Keep a value for each unit in a `UnitDict`**, which forgets a unit once it is dead, as speed mining keeps each
  worker's field.
- **Never order a unit the bot has not handed over.** Speed mining orders only the workers the bot gives it, and leaves
  alone one the bot ordered that turn.

## NachOS's own

- **Speed mining**, `sc2nachos.extensions.speed_mining.SpeedMining`: the workers the bot hands over mine their
  mineral fields, each trip ending in a move with the gather or return queued behind, which keeps the worker's speed
  into it. Twelve SCVs on a main mine some 12% more. Which worker mines which field stays the bot's:

  ```python
  api = Api(extensions=SpeedMining)


  @api.on(GameStartEvent)
  def start_mining(event: GameStartEvent) -> None:
      fields = api.expansions[0].mineral_fields
      for index, worker in enumerate(api.units.own.workers):
          api.extensions.mine(worker, fields[index % len(fields)])
  ```

  A worker the bot sends to build is left alone until it is idle or mining again, and then goes back to its field.
  `release(worker)` takes one back. The API reference says the rest.
