# Orders

A bot orders its units through `api.order` while its handlers run. Nothing goes out until the last handler of the
turn has returned, and then it all goes out in one request, so a turn that orders nothing sends nothing.

```python
@api.event.on(TurnEvent)
def on_turn(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.order.issue(marine, AbilityId.GENERAL_ATTACK, target=enemy_base)
```

`issue` answers with an `Order`, which the bot holds on to for as long as it cares what became of it.

## Giving one

```python
order = api.order.issue(marine, AbilityId.MARINE_STIM)
api.order.issue(squad, AbilityId.GENERAL_MOVE, target=ramp)
api.order.issue(scv, AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, queued=True)
api.order.issue(marine, AbilityId.GENERAL_ATTACK, target=drone, data=Defending(base))
api.order.clear_queue(scv)
api.order.camera(base)
```

- **`units`** is one unit or any number of them. They are ordered together, in one command, which is what makes the
  game spread a group around the point it was sent to; ordering each on its own stacks them on the point instead.
- **`target`** is a point, a unit, or nothing, as the ability takes. One of the wrong kind raises `TypeError` at the
  call site rather than going out for the game to answer `ERROR` a turn later;
  `api.data.abilities[ability].target_type` says which an ability wants. A point is kept as the game will read it,
  since the protocol carries a coordinate as a 32-bit float, so `order.target` is the point the game was given
  rather than quite the one passed in. `camera` keeps its point the same way.
- **`queued`** sends the order to go behind what each unit already has, rather than to replace it.
- **`data`** is the bot's own: why the order was given, what plan it serves, anything. NachOS carries it and never
  reads it, and `api.order.issue(..., data=x)` answers an `Order[type of x]`, so a type checker follows it through.
- **`api.order.clear_queue(unit)`** drops what a unit has queued and leaves it at the order it is carrying out,
  by sending that order back unqueued. It answers the order it sent, or `None` where there was nothing to drop — a
  structure making something is not cleared this way, since the game would only put another of the same behind it.
- **`api.order.camera`** moves this player's camera with the turn's orders. Only the last move of a turn is sent.
- **`api.order.cancel(order)`** takes back what a structure is making, and **`api.order.budget`** says what the turn
  can still pay for. Both have a section of their own below.

## One order a unit a turn

A unit takes the last order it was given in a turn, and the ones before it read `OVERRIDDEN`. This is what the game
does with two unqueued orders in one request, so NachOS sends only the one that would have stood.

The exception is `OrderBehavior.KEEPS_ORDERS`, an ability that leaves the unit doing what it was doing: stim, the
cloaks, Guardian Shield, both halves of every toggle, and the rest a sweep saw a moving unit carry out without
breaking its move, and everything besides making something and cancelling that is offered only to a type the game
offers no move — a structure's own rally, load and energy casts, and the way back out of a sieged form.
`OrderBehavior.CANCELS`, a structure's own cancel, competes with nothing either: it takes the structure off nothing
but the last thing it queued. `GENERAL_CANCEL` is neither, being offered to a channeling ghost or infestor as well,
which it takes off what they are doing.

Those neither override nor are overridden, because the unit does both — a marine stims and goes on moving.
`order.behavior` says which an ability is.

**A structure is a unit like any other here**: it takes the last thing a turn told it to make. The game would put a
second train behind the first and pay for it from the step it was ordered, which is money spent before the
structure can start on it, so NachOS sends only the last. To fill a queue on purpose — a reactor's second slot, say,
which the structure starts at once — say so with `queued=True`:

```python
api.order.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)
api.order.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE, queued=True)
```

Both go out, and a barracks with a reactor makes both marines at once. Two such orders name the same ability on the
same structure, so NachOS cannot tell their reports apart: they run and finish together.

NachOS has no priority of its own. Handlers already run highest-priority-first, and a handler late in a turn reads
`api.order.issued(unit)` to leave a unit an earlier one has spoken for:

```python
@api.event.on(TurnEvent, priority=EventPriority.LOW)
def keep_the_rest_together(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        if not api.order.issued(marine):
            api.order.issue(marine, AbilityId.GENERAL_MOVE, target=rally)
```

A bot that wants its own ranking puts it in `data` and reads it back off the orders `issued` answers with.

An order a unit is already carrying out is not sent again. In game such an order is answered `SUCCESS`, carries
nothing out and is left out of the reported actions — its one effect is that the unit's queued orders are gone. So
NachOS holds it back, the order reads `RUNNING`, and dropping a queue on purpose is `clear_queue`.

## What a turn can pay for

The game charges a train, a research, a morph and a build as the order is taken, so a turn that orders more than it
has is a turn that burns it: what it cannot pay for is answered `SUCCESS` and then silently dropped, or hung in a
queue that never starts. `api.order.budget` counts what the turn has already ordered, and an order it cannot cover
is refused by NachOS and never sent.

```python
budget = api.order.budget
budget.resources  # Resources(minerals, vespene) left after what the turn has ordered
budget.supply_left  # what is left under the cap, the same way
budget.slots_left(barracks)  # what it is still to take: 5, or 8 with a finished reactor
budget.covers(AbilityId.BARRACKS_TRAIN_MARINE, barracks)  # whether the turn can pay for it
budget.refusal(AbilityId.BARRACKS_TRAIN_MARINE, barracks)  # what it would be answered, or None
```

With 50 minerals and two barracks, the second marine of the turn comes back refused:

```python
first = api.order.issue(one, AbilityId.BARRACKS_TRAIN_MARINE)  # GIVEN
second = api.order.issue(other, AbilityId.BARRACKS_TRAIN_MARINE)  # REFUSED, NOT_ENOUGH_MINERALS
```

- **The verdict is what the game would have answered**: `NOT_ENOUGH_MINERALS`, `NOT_ENOUGH_VESPENE`,
  `NOT_ENOUGH_FOOD`, `QUEUE_IS_FULL`, or `NOT_SUPPORTED` for an ability this unit's type cannot run, whose tech is
  not in, or that a structure must be idle to take.
- **Nothing is held over.** A refused order is final; the bot decides again next turn, from next turn's state.
- **An order withdrawn or overridden stops counting at once**, since the budget is summed from the orders the turn
  still holds rather than kept as a running total.
- **One command is charged once, however many units it names**: one of them carries it out, whether it is a storm,
  a pylon, a train or a research (in game).
- **`slots_left` counts what the last observation says a structure is making**, not what this turn has ordered it.
  Only one command a turn can add to a structure's queue unqueued, so a bot filling one on purpose says
  `queued=True` and watches the next observation.
- **A cancel frees neither a slot nor a mineral in the same turn**, which is what the game does (in game), so the
  budget counts nothing back for one until the next observation.
- **`issue(..., checked=False)`** sends the order whatever the budget says. That is the way past a tech-tree row
  that has gone stale on a new build, and the way to fill a queue the supply cap cannot feed yet on purpose.

Two things to know about what it counts:

- **Supply is where NachOS is stricter than the game.** The game charges supply as a unit *starts*, so it takes a
  queued order the cap cannot feed, charges its minerals and hangs it at no progress for as long as it is left
  there, with no error and nothing dropped. NachOS refuses it instead, so a bot is never left holding an order that
  will never start.
- **A research ordered by its general id is budgeted at the cheapest level it stands for**, since which level the
  game will run is not knowable until it is ordered, and the cheapest refuses nothing the game would take.

In a stepped game the check is exact: the turn's orders go out before the game steps, so the minerals NachOS read
are the minerals the game charges against. In a realtime game the game moves on while the handlers run, so a
refusal there is conservative.

## Taking one back

```python
cancel = api.order.cancel(order)
```

`api.order.cancel` sends the cancel the game offers the structure for what it is making — its own, since
`GENERAL_CANCEL_LAST` is answered `ERROR` by a morph and by an add-on — and answers the order that goes out, whose
`cancels` names the one it takes back. That handle settles `CANCELLED` as soon as the game takes the cancel.

- **Only the last thing a structure is making can be cancelled**: a raw command reaches no further, so an order
  behind another raises `ValueError`, as does one given to more than one unit. Two cancels in a turn walk back two
  items.
- It answers `None` where there is nothing to cancel: an order the book is done with, one whose structure is gone,
  one the structure is not carrying out, and one this turn has not sent yet — that last is `order.withdraw()`'s,
  which drops it before it costs anything.
- What comes back is the game's own rule — all of a train or a research, three quarters of a morph, an add-on or a
  structure going up — and it comes back in a later observation, not this turn.

## What became of it

`order.state` is where an order has got to, and `api.order.running` holds every order NachOS is still following.

| State | What it means |
| --- | --- |
| `GIVEN` | Given this turn, and nothing sent yet. |
| `SENT` | Sent and answered `SUCCESS`, with no observation since to say what came of it. |
| `RUNNING` | The game reported carrying it out, or the unit was already doing it. |
| `DONE` | No unit it was given to is carrying it out any more. |
| `LOST` | Every unit it went out for died before it was done, so nothing came of it. An order whose unit is used up carrying it out, a larva becoming a drone, is `DONE` instead. |
| `CANCELLED` | Taken back with `api.order.cancel`, which the game took. |
| `REFUSED` | Answered something else, or never sent because the turn could not pay for it: `order.verdict` says what the game would have answered. |
| `DROPPED` | Answered `SUCCESS` and never carried out, which the game does silently for an order that no longer fits by the time it steps. |
| `FAILED` | Carried out and then given up on: `order.error` holds the action error. |
| `OVERRIDDEN` | A later order took every unit this one was given to, in this turn before it was sent, or in a later one. |
| `WITHDRAWN` | Taken back with `order.withdraw()`. One already sent is only forgotten, and the unit goes on with it. |

An order's effect can show up an observation late, so wait for the state rather than expecting it in the next
observation. `api.action_errors` lists every order the game gave up on since the observation before, whether or not
NachOS was still following it.

## Reading a unit's orders

`order` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `UnitOrder` for what
it is at and one for each it has queued. The two need not agree: the game reports the exact ability it runs, snaps a
build to its site, and drops what it cannot carry out.
