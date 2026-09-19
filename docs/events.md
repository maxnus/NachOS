# Events

A bot's code runs in handlers of the events a game hands out. This page says how to subscribe to them, how to select
some of them, and how to define and send events of your own. It then lists every event NachOS hands out: its fields,
how it can be selected, and when it comes. The docstrings in `sc2nachos.events` say the rest.

## Subscribing

```python
@api.event.on(TurnEvent)
def on_turn(event: TurnEvent) -> None: ...


class Economy:
    def __init__(self) -> None:
        api.event.subscribe(self)

    @api.event.on(OwnUnitCreatedEvent, priority=EventPriority.HIGH)
    def on_created(self, event: OwnUnitCreatedEvent) -> None: ...
```

A function is subscribed as it is defined, and a method is marked, then subscribed for each instance passed to
`api.event.subscribe`. `on` takes a `priority`, and holds a handler back with `every_steps`, `at_step` or `once`. A
handler that returns `Done` is called no more that game. Everything a handler has done starts afresh with each game.

## Selecting some events

```python
@api.event.on(AlertEvent.only(Alert.NUCLEAR_LAUNCH_DETECTED))
@api.event.on(UnitDiedEvent.only(UnitType.Structure))
@api.event.on(OwnUnitEnergyReachedEvent.of(UnitType.HighTemplar, 75))
@api.event.on(OwnUnitLifeFractionDroppedEvent.of(0.3))
@api.event.on(EnemyUnitEnteredAreaEvent.of(Circle(natural, 12)))
@api.event.on(OwnUnitDamagedEvent.where(lambda event: event.damage > 20))
```

- **`only(...)`** narrows an event a handler can also take whole: to some alerts, buffs, upgrades or unit types. A
  `UnitType` group stands for every type in it, and a unit's type is the one it has as the event is made. It takes
  one value or several.
- **`of(...)`** defines an event there is none of without its parameters: a unit's energy reaching a value, its life
  rising to a fraction or falling below it, a unit crossing the edge of an area. Such a type derives from
  `ParameterizedEvent`, and `on` refuses it bare, as a type checker does. One `of` takes one set of parameters; for
  several, stack the decorators.
- **`where(predicate)`** narrows any event, or what `only` or `of` selected, to those `predicate` passes. Chained
  twice, it passes those both pass. Each handler's predicate is called as its turn comes, after the handlers before it
  have run.

`once`, `every_steps`, `at_step` and `Done` count only the events a handler selects: `once` with `where` is the first
event the predicate passes. `only` and `of` also spare NachOS making the events no handler wants, and `of` watching
what no handler asks for. `where` cannot: a handler with a predicate has NachOS make every event of the type.

## Handlers of a class and its subclasses

An event is handed to the handlers of its class and of every class it derives from. So a handler of `Event` is handed
every event, which has NachOS make every type of event it would otherwise not, the comparison of every unit with the
observation before included. A handler of `Event` is never handed a parameterized event but of the parameters other
handlers asked for.

Handlers run highest priority first, and those of one priority in the order they subscribed, whichever class each
subscribed to.

## Events of your own

```python
class ExpansionTakenEvent(Event):
    base: Point
    by_enemy: bool = True


api.event.emit(ExpansionTakenEvent(api.step, natural))
```

A subclass of `Event` is a frozen, slotted dataclass of the fields it declares, `step` first, without a `@dataclass`
of its own, which fails. `api.event.emit` hands an event to its handlers, which have all run by the time it returns;
a handler may emit an event itself. It takes NachOS's own events too, so a bot's handlers can be tested without a
game.

## When events come

A game hands out `GameStartEvent`, then a turn for each observation but the last, then `GameEndEvent`. A turn hands
out `TurnStartEvent`, then what its observation reports has happened, in the order of the tables below, then
`TurnEvent`.

- **An event's step is when NachOS learned of it.** What happened out of sight is reported when it is next seen: a
  morph in the fog, a structure that died there, a unit's energy regenerated.
- **The first turn reports the units the game starts with as created.** The observation a game ends on gets no turn,
  so nothing it reports is handed out.
- **An event is made only for a handler still to run this game**, and a comparison or a watch is made only for one.

## Every event

"Selected by" says what `only` or `of` takes. Every event of a unit is selected by its unit's type.

### A game

| event | fields | comes |
|---|---|---|
| `GameStartEvent` | `step` | once a game has started |
| `TurnStartEvent` | `step` | as a turn starts, before anything else of it |
| `TurnEvent` | `step` | a bot's turn, after what the observation reports |
| `GameEndEvent` | `step`, `result` | once the game has ended, the observation it ended on read |

### Units coming and changing

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitCreatedEvent` | `unit` | `only(*unit_types)` | a unit of this player's is first seen |
| `EnemyUnitFirstSeenEvent` | `unit` | `only(*unit_types)` | an enemy unit is first seen, in sight or in the fog |
| `UnitTypeChangedEvent` | `unit`, `previous_type` | `only(*unit_types)`, the type changed to | a unit changes type |
| `UnitAllianceChangedEvent` | `unit`, `previous_alliance` | `only(*unit_types)` | a unit changes sides to or from this player's |

### Construction and research

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnConstructionStartedEvent` | `unit` | `only(*unit_types)` | a structure of this player's is first seen unfinished |
| `OwnConstructionFinishedEvent` | `unit` | `only(*unit_types)` | one of those finishes |
| `OwnWarpInFinishedEvent` | `unit` | `only(*unit_types)` | a unit of this player's finishes warping in |
| `OwnUpgradeFinishedEvent` | `upgrade` | `only(*upgrades)` | this player finishes an upgrade |

### What units went through

Compared with the observation before, for units in vision in both, or watched from the turn the watch starts on.

| event | fields | selected by | comes when |
|---|---|---|---|
| `OwnUnitDamagedEvent`, `EnemyUnitDamagedEvent` | `unit`, `damage` | `only(*unit_types)` | a unit lost health or shields, and kept its type |
| `OwnUnitEnergyLostEvent`, `EnemyUnitEnergyLostEvent` | `unit`, `energy_lost` | `only(*unit_types)` | a unit lost energy, and kept its type |
| `OwnUnitEnergyReachedEvent`, `EnemyUnitEnergyReachedEvent` | `unit`, `energy` | `of(unit_type, energy)` | a unit's energy is at or above the value, having last been below it |
| `OwnUnitLifeFractionReachedEvent`, `EnemyUnitLifeFractionReachedEvent` | `unit`, `fraction` | `of(fraction)` | a unit's health and shields are at or above the fraction, having last been below it: `of(1.0)` is back to full |
| `OwnUnitLifeFractionDroppedEvent`, `EnemyUnitLifeFractionDroppedEvent` | `unit`, `fraction` | `of(fraction)` | a unit's health and shields are below the fraction, having last been at or above it |
| `OwnUnitCloakChangedEvent`, `EnemyUnitCloakChangedEvent` | `unit`, `previous_cloak` | `only(*unit_types)` | a unit cloaked or uncloaked, or an enemy unit came to be detected or no longer is |
| `OwnUnitGainedBuffEvent`, `EnemyUnitGainedBuffEvent` | `unit`, `buff` | `only(*buffs)` | a unit wears a buff it did not |
| `OwnUnitLostBuffEvent`, `EnemyUnitLostBuffEvent` | `unit`, `buff` | `only(*buffs)` | a unit no longer wears a buff it did |
| `EnemyUnitEnteredSightEvent` | `unit` | `only(*unit_types)` | an enemy unit came into sight |
| `EnemyUnitLeftSightEvent` | `unit` | `only(*unit_types)` | an enemy unit in sight is not now, and is not dead |
| `OwnUnitEnteredAreaEvent`, `EnemyUnitEnteredAreaEvent` | `unit`, `area` | `of(area)` | a unit in sight is inside the area, having last been outside it or never seen |
| `OwnUnitLeftAreaEvent`, `EnemyUnitLeftAreaEvent` | `unit`, `area` | `of(area)` | a unit in sight is outside the area, having last been inside it |

- **Reached and dropped never come for what a unit was first seen with**, and not again until the unit has been on the
  other side of the value. A value itself counts as reached.
- **A watch takes what it finds on its first turn as it stands**, and reports from the turn after. An area watch then
  reports a unit first seen inside it as entering.
- **A unit that dies, or leaves the observation, inside an area has not left it.** One seen elsewhere later has.
- **An enemy unit counts only in sight**, and one listed in the fog for none of these. Burrowing is no cloak: a
  burrowed enemy unit nothing detects is not in the observation at all.

### Deaths

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
