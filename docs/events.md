# Events

A bot's code runs in handlers of the events a game hands out. This page covers subscribing to an event, selecting a
subset of one, and defining and emitting your own, then lists every event NachOS hands out: its fields, what selects
it, and when it comes. The docstrings of `api.on` and of `sc2nachos.events` cover the rest.

## Subscribing

```python
@api.on(TurnEvent)
def on_turn(event: TurnEvent) -> None: ...


class Economy:
    def __init__(self) -> None:
        api.subscribe(self)

    @api.on(OwnUnitCreatedEvent, priority=EventPriority.HIGH)
    def on_created(self, event: OwnUnitCreatedEvent) -> None: ...
```

A function is subscribed when it is defined, a static method included. A method, which takes `self` and the event, is
marked, then subscribed for each instance passed to `api.subscribe`, and `api.unsubscribe` takes a function, a method or
an instance off again. `on` takes a `priority`, and `every_steps`, `at_step` or `once` to limit when the handler runs. A
handler that returns `Done` is not called again that game. All of this starts over with each game. An api made with
`time_handlers=True` says how long each handler took in `api.handler_timings`.

## Selecting some events

```python
@api.on(AlertEvent.only(Alert.NUCLEAR_LAUNCH_DETECTED))
@api.on(AnyUnitDiedEvent.only(UnitType.Structure))
@api.on(OwnUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitType.HighTemplar))
@api.on(OwnUnitVitalDroppedEvent.of(VitalType.LIFE_FRACTION, 0.3))
@api.on(EnemyUnitEnteredAreaEvent.of(Circle(natural, 12)))
@api.on(OwnUnitDamagedEvent).where(lambda event: event.damage > 20)
```

- **`only(...)`** narrows an event type a handler could also take whole, to some alerts, buffs, upgrades or unit
  types. It takes one value or several. A `UnitType` group stands for every type in it, and a unit is matched by its
  type at the time of the event.
- **`of(...)`** parameterizes an event that does not exist without its parameters: a unit's health, shields or energy,
  as an amount or a fraction of its maximum, reaching a value or dropping below it; or a unit crossing the edge of an
  area. Such a type derives from `ParameterizedEvent`, and `on` rejects it bare, as does a type checker. One `of` takes
  one set of parameters; stack the decorators for several.
- **`where(predicate)`**, called on the `EventSubscriber` that `on` returns, keeps only the events `predicate` passes,
  out of the type or the `only` or `of` selection. Chained twice, an event must pass both. The decorator it returns
  subscribes every handler it decorates. A handler's predicate is called when the handler's turn comes, after the
  handlers before it have run.

`once`, `every_steps`, `at_step` and `Done` count only the events a handler selects: `once` with `where` fires on
the first event the predicate passes. `only` and `of` also save NachOS from making events no handler wants, and `of`
from watching what no handler asked for. `where` cannot: a handler with a predicate makes NachOS produce every event
of the type.

## Handlers of a class and its subclasses

```python
@api.on(AnyUnitEvent.only(UnitType.Structure))
@api.on(AnyBuffEvent.only(BuffId.MARINE_STIMMED))
@api.on(AnyVitalEvent.of(VitalType.LIFE_FRACTION, 0.5))
@api.on(AnyAreaEvent.of(Circle(natural, 12)))
```

An event is handed to the handlers of its class and of every class it derives from. Eight bases each cover a whole
kind of event, and NachOS never hands one out itself, only the kinds under it:

- `AnyUnitEvent`: every event about one unit but the buff, vital, area and upgrade events. `only` selects by the unit's
  type.
- `AnyUnitTypeChangedEvent` and `AnyUnitDiedEvent`: a type change and a death, this player's or the enemy's, and a
  death of the map's units too.
- `AnyBuffEvent`, `AnyVitalEvent` and `AnyAreaEvent`: a buff gained or lost, a vital reaching a value or dropping below
  it, and the edge of an area crossed either way, each for this player's units and the enemy's alike.
- `AnyUpgradeEvent`: a research of this player's started, stopped or finished. `only` selects by the upgrade.
- `AnyChatEvent`: a message from this player or another.

`AnyVitalEvent` and `AnyAreaEvent` are parameterized, so a handler takes them through `of` and is handed both directions
of the crossing it asked for. A handler of a base is typed as taking it: a handler of `AnyUnitEvent` reads
`event.unit`, and `event.unit.alliance` for the side.

A handler of `Event` is handed every event, which makes NachOS produce every event type it otherwise would not,
including comparing every unit with the previous observation. A handler of `Event` is handed parameterized events
only with the parameters other handlers asked for.

## Events of your own

```python
class ExpansionTakenEvent(Event):
    base: Point
    by_enemy: bool = True


api.emit(ExpansionTakenEvent(natural))
```

A subclass of `Event` is a frozen, slotted dataclass of the fields it declares; a `@dataclass` decorator of its own
fails. Every event has a `step`, passed by keyword; an event made without one is handed out as a copy with the current
step of the game being played, so one made once can be emitted every turn. `api.emit` hands an event to its handlers in
priority order and returns once they have all run. A handler may itself emit an event. `emit` takes NachOS's own events
too, so a bot's handlers can be tested without a game, given a step.

## When events come

A game hands out `GameStartEvent`, then a turn for each observation but the last, then `GameEndEvent`. A turn hands
out `TurnStartEvent`, then the events its observation reports, in the order of the tables below, then `TurnEvent`.

- **A turn goes out priority first.** After `TurnStartEvent`, every handler of the highest priority is handed each
  event of the turn it selects, `TurnEvent` last, before any handler of the next priority is handed anything.
  Priorities run from `EventPriority.HIGHEST` to `LOWEST`, with `MEDIUM` the default. Within one priority, the
  handlers of an event run in the order they subscribed, whichever class each subscribed to. So a handler at `HIGH`
  sees every death and sighting of a turn before any handler at `MEDIUM` acts on one, and a bot's `TurnEvent` handler
  runs after the turn's other events at its priority. An event a handler emits goes out at once, in its own handlers'
  priority order.
- **An event's step is the step NachOS learned of it.** What happened out of sight is reported when it is next seen:
  a morph in the fog, a structure that died there, energy a unit regenerated.
- **The first turn reports the units the game starts with as created.** The last observation of a game gets no turn,
  so nothing it reports is handed out.
- **An event, a comparison or a watch is made only for a handler that can still run this game.**

## Every event

"Selected by" is what `only` or `of` takes. Every unit event is selected by its unit's type. Each table names the
base its events share, for a handler that wants the whole kind.

### A game

| event | fields | comes |
|---|---|---|
| `GameStartEvent` | `step` | once a game has started |
| `TurnStartEvent` | `step` | as a turn starts, before anything else of it |
| `TurnEvent` | `step` | a bot's turn, after what the observation reports |
| `GameEndEvent` | `step`, `result` | once the game has ended and its last observation has been read |
| `EnemyRaceKnownEvent` | `step`, `race` | once the enemy's race is known: on the first turn, unless the enemy is a computer that asked for Random, which comes on the turn its first unit is seen; never in a game with no other player |

### Units coming and changing

All `AnyUnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitCreatedEvent` | `unit` | `only(*unit_types)` | a unit of this player's is first seen |
| `EnemyUnitFirstSeenEvent` | `unit` | `only(*unit_types)` | an enemy unit is first seen, in sight or in the fog |
| `OwnUnitTypeChangedEvent`, `EnemyUnitTypeChangedEvent` | `unit`, `previous_type` | `only(*unit_types)`, the type changed to | a unit of this player's or of the enemy's changes type |
| `UnitAllianceChangedEvent` | `unit`, `previous_alliance` | `only(*unit_types)` | a unit changes sides to or from this player's |

Where an event comes in an own and an enemy kind, its side is the one the unit is on at the time, and the unit has
that side's class: a unit a neural parasite holds is an `OwnUnit` of the holder's while held, and `unit.first_alliance`
says whose it was. A handler of `AnyUnitTypeChangedEvent` gets both kinds; a unit of the map's or of an ally's gets
neither.

A unit that changes sides gets `UnitAllianceChangedEvent`, and the other events take it by their own rules:

- **Sight does not change with the side.** A unit taken over in sight neither leaves it nor enters it, so a unit of
  this player's the enemy holds can get `EnemyUnitLeftSightEvent` with no `EnemyUnitEnteredSightEvent` before it.
- **The vital and area watches keep each side's units apart.** To the side a unit leaves, it is a unit gone from the
  observation, whose crossings meanwhile come when that side has it back. To the side it joins, it is a unit first
  seen, so one taken over inside an area gets the holder's entered event.

### Construction, warp-ins and research

`AnyUnitEvent`s, but the upgrade events, which are `AnyUpgradeEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnConstructionStartedEvent` | `unit` | `only(*unit_types)` | a structure of this player's is first seen unfinished |
| `OwnWarpInStartedEvent` | `unit` | `only(*unit_types)` | a unit of this player's is first seen warping in |
| `OwnConstructionFinishedEvent` | `unit` | `only(*unit_types)` | a structure that got `OwnConstructionStartedEvent` finishes |
| `OwnWarpInFinishedEvent` | `unit` | `only(*unit_types)` | a unit of this player's finishes warping in |
| `OwnUpgradeStartedEvent` | `upgrade`, `unit` | `only(*upgrades)` | a research first shows in a structure's orders, queued behind another included |
| `OwnUpgradeStoppedEvent` | `upgrade`, `unit` | `only(*upgrades)` | a research shows nowhere and did not finish: it was cancelled, or its structure died |
| `OwnUpgradeFinishedEvent` | `upgrade` | `only(*upgrades)` | this player finishes an upgrade |

A finished research never reads as stopped: its order leaves the structure in the step its upgrade arrives
([game behavior](reference/game-behavior/abilities-and-orders.md#builds-and-work-lost)). A research that leaves one
structure and shows in another between two observations is neither stopped nor started. A handler of the research
events that starts mid-game is handed every research already running as started, on its first turn.

### What units went through

Compared with the previous observation, for units visible in both, or in sight in both for a cloak change, or watched
from the turn the watch starts on.
The buff events are `AnyBuffEvent`s, the vital events `AnyVitalEvent`s, the area events `AnyAreaEvent`s, and the rest
`AnyUnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitDamagedEvent`, `EnemyUnitDamagedEvent` | `unit`, `damage` | `only(*unit_types)` | a unit lost health or shields, and kept its type; `unit.damage_taken` reads the same at any time |
| `OwnUnitEnergyLostEvent`, `EnemyUnitEnergyLostEvent` | `unit`, `energy_lost` | `only(*unit_types)` | a unit lost energy, and kept its type |
| `OwnUnitVitalReachedEvent`, `EnemyUnitVitalReachedEvent` | `unit`, `vital`, `value` | `of(vital, value, *unit_types)` | a unit's vital is at or above the value, having last been below it: `of(VitalType.LIFE_FRACTION, 1.0)` is back to full |
| `OwnUnitVitalDroppedEvent`, `EnemyUnitVitalDroppedEvent` | `unit`, `vital`, `value` | `of(vital, value, *unit_types)` | a unit's vital is below the value, having last been at or above it |
| `OwnUnitCloakChangedEvent`, `EnemyUnitCloakChangedEvent` | `unit`, `previous_cloak_state` | `only(*unit_types)` | a unit cloaked or uncloaked, or an enemy unit became detected or stopped being |
| `OwnUnitGainedBuffEvent`, `EnemyUnitGainedBuffEvent` | `unit`, `buff` | `only(*buffs)` | a unit has a buff it did not have before |
| `OwnUnitLostBuffEvent`, `EnemyUnitLostBuffEvent` | `unit`, `buff` | `only(*buffs)` | a unit no longer has a buff it had |
| `EnemyUnitEnteredSightEvent` | `unit` | `only(*unit_types)` | an enemy unit came into sight |
| `EnemyUnitLeftSightEvent` | `unit` | `only(*unit_types)` | an enemy unit was in sight and now is not, and is not dead |
| `OwnUnitEnteredAreaEvent`, `EnemyUnitEnteredAreaEvent` | `unit`, `area` | `of(area)` | a unit in sight is inside the area, having last been outside it or never seen |
| `OwnUnitLeftAreaEvent`, `EnemyUnitLeftAreaEvent` | `unit`, `area` | `of(area)` | a unit in sight is outside the area, having last been inside it |

- **A vital is a `VitalType`**: health, shields, life (the two together) or energy, as an amount or a fraction of
  its maximum. `of` takes the unit types to watch, or watches every type when given none. A unit without shields or
  energy, whose maximum is 0, has no value to cross.
- **Reached and dropped never come for the value a unit was first seen with**, and not again until the unit has been
  on the other side of the value. A vital equal to the value counts as reached.
- **A watch takes its first turn as the baseline** and reports from the turn after. An area watch then reports a
  unit first seen inside the area as entering.
- **A unit that dies, or leaves the observation, inside an area has not left it.** A unit seen elsewhere later has.
- **An enemy unit counts only while in sight**; one listed in the fog counts for none of these. Burrowing is not
  cloaking: a burrowed enemy unit nothing detects is not in the observation at all.

### Idle units

An `AnyUnitEvent`.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitBecameIdleEvent` | `unit` | `only(*unit_types)` | a finished unit of this player's has no orders, and none NachOS sent it that may not show yet, having had orders or such an order, been unfinished or another side's, or not been listed in the observation before |

A unit trained with no rally gets it, as do a structure just finished, a worker whose mineral field ran out, a unit
whose order the game dropped, and one a neural parasite just took over. A unit just ordered does not, though on the
ladder its order can take two observations to show
([game behavior](reference/game-behavior/protocol.md#time-and-steps)). A larva, or a depot even as it lowers, holds no
order and gets it once. A sieged tank and a burrowed lurker show the order that made the form for as long as they keep
it, so neither is idle in it
([game behavior](reference/game-behavior/abilities-and-orders.md#what-an-order-does-to-a-units-orders)). A handler
that starts mid-game hears of every unit idle then.

### Deaths

`AnyUnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitDiedEvent`, `EnemyUnitDiedEvent`, `NeutralUnitDiedEvent` | `unit` | `only(*unit_types)` | the game reports a unit dead: this player's, the enemy's, or the map's, such as a mineral field |
| `UnitFoundDeadEvent` | `unit` | `only(*unit_types)` | NachOS finds a unit dead the game did not report |

A handler of `AnyUnitDiedEvent` gets all three kinds; a unit of an ally's gets none. The game reports a mineral field
killed dead ([game behavior](reference/game-behavior/units.md#units-coming-changing-and-going)). A unit created
and dead within one observation gets both its creation and its death, in that order.

### Actions, chat and alerts

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnActionEvent` | `action` | | this player did something, as the game carried it out |
| `OwnChatEvent`, `OpponentChatEvent` | `player_id`, `text` | | this player sent a message, through `api.chat` or typed into its game window, or another player did |
| `AlertEvent` | `alert` | `only(*alerts)` | the game alerted this player, several in the order it raised them |

A handler of `AnyChatEvent` gets both kinds. A message from a player the game's info lists as an observer comes as
neither, though whether the game lists observers is [not known yet](reference/game-behavior/not-understood-yet.md). The
game names no teams, so with more than two players an ally's message is an `OpponentChatEvent` too. What raises each
alert is in the docstring of each `Alert` member, and in
[game behavior](reference/game-behavior/alerts-and-chat.md#alerts-and-the-camera).
