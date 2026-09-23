# Events

A bot's code runs in handlers of the events a game hands out. This page covers subscribing to an event, selecting a
subset of one, and defining and emitting your own, then lists every event NachOS hands out: its fields, what selects
it, and when it comes. The docstrings in `sc2nachos.events` cover the rest.

## Subscribing

```python
@api.events.on(TurnEvent)
def on_turn(event: TurnEvent) -> None: ...


class Economy:
    def __init__(self) -> None:
        api.events.subscribe(self)

    @api.events.on(OwnUnitCreatedEvent, priority=EventPriority.HIGH)
    def on_created(self, event: OwnUnitCreatedEvent) -> None: ...
```

A function is subscribed when it is defined. A method is marked, then subscribed for each instance passed to
`api.events.subscribe`. `on` takes a `priority`, and `every_steps`, `at_step` or `once` to limit when the handler
runs. A handler that returns `Done` is not called again that game. All of this starts over with each game.

A bot with one api can alias the decorator, `on = api.events.on`, and write `@on(TurnEvent)` on functions and
methods alike. `api.events` also holds `subscribe`, `unsubscribe`, `emit` and `timings`.

## Selecting some events

```python
@api.events.on(AlertEvent.only(Alert.NUCLEAR_LAUNCH_DETECTED))
@api.events.on(UnitDiedEvent.only(UnitType.Structure))
@api.events.on(OwnUnitVitalReachedEvent.of(VitalType.ENERGY, 75, UnitType.HighTemplar))
@api.events.on(OwnUnitVitalDroppedEvent.of(VitalType.LIFE_FRACTION, 0.3))
@api.events.on(EnemyUnitEnteredAreaEvent.of(Circle(natural, 12)))
@api.events.on(OwnUnitDamagedEvent).where(lambda event: event.damage > 20)
```

- **`only(...)`** narrows an event type a handler could also take whole, to some alerts, buffs, upgrades or unit
  types. It takes one value or several. A `UnitType` group stands for every type in it, and a unit is matched by its
  type at the time of the event.
- **`of(...)`** parameterizes an event that does not exist without its parameters: a unit's health, shields or energy,
  as an amount or a fraction of its maximum, reaching a value or dropping below it; or a unit crossing the edge of an
  area. Such a type derives from `ParameterizedEvent`, and `on` rejects it bare, as does a type checker. One `of` takes
  one set of parameters; stack the decorators for several.
- **`where(predicate)`**, called on the decorator `on` returns, keeps only the events `predicate` passes, out of the
  type or the `only` or `of` selection. Chained twice, an event must pass both. The decorator it returns subscribes
  every handler it decorates. A handler's predicate is called when the handler's turn comes, after the handlers
  before it have run.

`once`, `every_steps`, `at_step` and `Done` count only the events a handler selects: `once` with `where` fires on
the first event the predicate passes. `only` and `of` also save NachOS from making events no handler wants, and `of`
from watching what no handler asked for. `where` cannot: a handler with a predicate makes NachOS produce every event
of the type.

## Handlers of a class and its subclasses

```python
@api.events.on(UnitEvent.only(UnitType.Structure))
@api.events.on(BuffEvent.only(BuffId.MARINE_STIMMED))
@api.events.on(VitalEvent.of(VitalType.LIFE_FRACTION, 0.5))
@api.events.on(AreaEvent.of(Circle(natural, 12)))
```

An event is handed to the handlers of its class and of every class it derives from. Four bases each cover a whole
kind of event, for this player's units and the enemy's alike: `UnitEvent` is every event with a `unit`, which `only`
selects by the unit's type; `BuffEvent` a buff gained or lost; `VitalEvent` a vital reaching a value or dropping below
it; and `AreaEvent` the edge of an area crossed either way. The last two are parameterized, so a handler takes them
through `of` and is handed both directions of the crossing it asked for. A handler of a base is typed as taking it:
a handler of `UnitEvent` reads `event.unit`, and `event.unit.alliance` for the side.

A handler of `Event` is handed every event, which makes NachOS produce every event type it otherwise would not,
including comparing every unit with the previous observation. A handler of `Event` or `UnitEvent` is handed
parameterized events only with the parameters other handlers asked for.

## Events of your own

```python
class ExpansionTakenEvent(Event):
    base: Point
    by_enemy: bool = True


api.events.emit(ExpansionTakenEvent(natural))
```

A subclass of `Event` is a frozen, slotted dataclass of the fields it declares; a `@dataclass` decorator of its own
fails. Every event has a `step`, passed by keyword; an event made without one takes the current step of the game
being played when it is emitted. `api.events.emit` hands an event to its handlers in priority order and returns
once they have all run. A handler may itself emit an event. `emit` takes NachOS's own events too, so a bot's
handlers can be tested without a game, given a step.

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

### Units coming and changing

All `UnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitCreatedEvent` | `unit` | `only(*unit_types)` | a unit of this player's is first seen |
| `EnemyUnitFirstSeenEvent` | `unit` | `only(*unit_types)` | an enemy unit is first seen, in sight or in the fog |
| `UnitTypeChangedEvent` | `unit`, `previous_type` | `only(*unit_types)`, the type changed to | a unit changes type |
| `UnitAllianceChangedEvent` | `unit`, `previous_alliance` | `only(*unit_types)` | a unit changes sides to or from this player's |

### Construction and research

`UnitEvent`s but `OwnUpgradeFinishedEvent`.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnConstructionStartedEvent` | `unit` | `only(*unit_types)` | a structure of this player's is first seen unfinished |
| `OwnConstructionFinishedEvent` | `unit` | `only(*unit_types)` | one of those finishes |
| `OwnWarpInFinishedEvent` | `unit` | `only(*unit_types)` | a unit of this player's finishes warping in |
| `OwnUpgradeFinishedEvent` | `upgrade` | `only(*upgrades)` | this player finishes an upgrade |

### What units went through

Compared with the previous observation, for units in vision in both, or watched from the turn the watch starts on.
The buff events are `BuffEvent`s, the vital events `VitalEvent`s, the area events `AreaEvent`s, and the rest
`UnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitDamagedEvent`, `EnemyUnitDamagedEvent` | `unit`, `damage` | `only(*unit_types)` | a unit lost health or shields, and kept its type |
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

### Deaths

Both `UnitEvent`s.

| event | fields | selected by | comes when |
|---|---|---|---|
| `UnitDiedEvent` | `unit` | `only(*unit_types)` | the game reports a unit dead |
| `UnitFoundDeadEvent` | `unit` | `only(*unit_types)` | NachOS finds a unit dead the game did not report |

A unit created and dead within one observation gets both its creation and its death, in that order.

### Actions, chat and alerts

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnActionEvent` | `action` | | this player did something, as the game carried it out |
| `ChatEvent` | `player_id`, `text` | | any player sent a message to the chat |
| `AlertEvent` | `alert` | `only(*alerts)` | the game alerted this player, several in the order it raised them |

What raises each alert is in the docstring of each `Alert` member, and in [game-behavior.md](game-behavior.md).
