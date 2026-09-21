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

## One order a unit a turn

A unit takes the last order it was given in a turn, and the ones before it read `OVERRIDDEN`. This is what the game
does with two unqueued orders in one request, so NachOS sends only the one that would have stood.

The exception is `OrderBehavior.KEEPS_ORDERS`, an ability that leaves the unit doing what it was doing: stim, the
cloaks, Guardian Shield, both halves of every toggle, and the rest a sweep saw a moving unit carry out without
breaking its move, and everything besides making something that is offered only to a type the game offers no move —
a structure's own rally, load, cancel and energy casts, and the way back out of a sieged form. `GENERAL_CANCEL` is
not one of them, being offered to a channeling ghost or infestor as well, which it takes off what they are doing.

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

## What an order needs

NachOS sends every order as it was given, and checks nothing it needs: minerals, vespene, supply, room in a
structure's queue, tech. The game judges those, and the state says how it did. An order it refuses reads `REFUSED`
with its verdict, one it takes and silently drops reads `DROPPED`, and a queued order the supply cap cannot feed is
taken, charged, and left at no progress until supply frees up.

A bot budgets for itself, since only it knows what matters most:

```python
cost = api.data.abilities[AbilityId.BARRACKS_TRAIN_MARINE].cost
if api.resources.covers(cost):
    api.order.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)
```

`api.resources` does not change as orders are given. It is what the game last reported, so a bot giving several
orders in one turn keeps its own tally. `cost` is what the game charges as the ability is ordered: the difference
for a morph, 150 for an orbital command rather than its type's 550. `supply_cost` is what it takes of the cap as it
starts.

A cancel is an order like any other. `api.data.abilities[ability].cancelled_by` names the one to send to a structure
carrying `ability` out: `GENERAL_CANCEL_LAST` for a train or a research, whatever the structure, and a morph's or an
add-on's own cancel, since `GENERAL_CANCEL_LAST` is answered `ERROR` by those. It takes back only the structure's last
item, and frees neither a slot nor a mineral within the same step
([game behavior](game-behavior.md#abilities-and-orders)).

## What became of it

`order.state` is where an order has got to, and `api.order.running` holds every order NachOS is still following.

| State | What it means |
| --- | --- |
| `GIVEN` | Given this turn, and nothing sent yet. |
| `SENT` | Sent and answered `SUCCESS`, with no observation since to say what came of it. |
| `RUNNING` | The game reported carrying it out, or the unit was already doing it. |
| `DONE` | No unit it was given to is carrying it out any more. |
| `LOST` | Every unit seen carrying it out died before it was done, so nothing came of it: of three barracks given one train, the one that took it. An ability whose effect is its unit's death, a baneling exploding, reads `LOST` too. A larva's order is `DONE` instead, since the egg it became is reported dead as what it makes hatches, and so an egg killed first reads `DONE` too. |
| `REFUSED` | Answered something else: `order.verdict` says what. |
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
