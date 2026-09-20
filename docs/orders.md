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
- **`target`** is a point, a unit, or nothing, as the ability takes. One of the wrong kind raises `TypeError` at
  the call site rather than going out for the game to answer `ERROR` a turn later;
  `api.data.abilities[ability].target_type` says which an ability wants.
- **`queued`** sends the order to go behind what each unit already has, rather than to replace it.
- **`data`** is the bot's own: why the order was given, what plan it serves, anything. NachOS carries it and never
  reads it, and `api.order.issue(..., data=x)` answers an `Order[type of x]`, so a type checker follows it through.
- **`api.order.clear_queue(unit)`** drops what a unit has queued and leaves it at the order it is carrying out,
  by sending that order back unqueued. It answers the order it sent, or `None` where the unit had nothing queued.
- **`api.order.camera`** moves this player's camera with the turn's orders. Only the last move of a turn is sent.

## One order a unit a turn

A unit takes the last order it was given in a turn, and the ones before it read `OVERRIDDEN`. This is what the game
does with two unqueued orders in one request, so NachOS sends only the one that would have stood.

The exception is an ability the unit carries out at once, which reads `OrderBehavior.AT_ONCE`: stim, the cloaks,
Guardian Shield and the ten others that were seen to leave a moving unit's orders as they were. Those neither
override nor are overridden, because the unit does both — a marine stims and goes on moving. `order.behavior` says
which an ability is.

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

## What became of it

`order.state` is where an order has got to, and `api.order.running` holds every order NachOS is still following.

| State | What it means |
| --- | --- |
| `GIVEN` | Given this turn, and nothing sent yet. |
| `SENT` | Sent and answered `SUCCESS`, with no observation since to say what came of it. |
| `RUNNING` | The game reported carrying it out, or the unit was already doing it. |
| `DONE` | No unit it was given to is carrying it out any more. |
| `REFUSED` | Answered something else: `order.verdict` says what. |
| `DROPPED` | Answered `SUCCESS` and never carried out, which the game does silently for an order that no longer fits by the time it steps. |
| `FAILED` | Carried out and then given up on: `order.error` holds the action error. |
| `OVERRIDDEN` | A later order took every unit this one was given to. |
| `WITHDRAWN` | Taken back with `order.withdraw()`. One already sent is only forgotten, and the unit goes on with it. |

An order's effect can show up an observation late, so wait for the state rather than expecting it in the next
observation. `api.action_errors` lists every order the game gave up on since the observation before, whether or not
NachOS was still following it.

## Reading a unit's orders

`order` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `UnitOrder` for what
it is at and one for each it has queued. The two need not agree: the game reports the exact ability it runs, snaps a
build to its site, and drops what it cannot carry out.
