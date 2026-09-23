# Orders

A bot orders its units through `api.orders` from its handlers. Orders are sent in one request after the turn's last
handler returns, so a turn that orders nothing sends nothing.

```python
@api.events.on(TurnEvent)
def on_turn(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.orders.issue(marine, AbilityId.GENERAL_ATTACK, target=enemy_base)
```

`issue` returns an `Order`. Keep it if you want to know what became of the order.

## Giving one

```python
order = api.orders.issue(marine, AbilityId.MARINE_STIM)
api.orders.issue(squad, AbilityId.GENERAL_MOVE, target=ramp)
api.orders.issue(scv, AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, queued=True)
api.orders.issue(marine, AbilityId.GENERAL_ATTACK, target=drone, data=Defending(base))
api.orders.clear_queue(scv)
api.orders.camera(base)
```

- **`units`** is one unit or any number of them. They are ordered together, in one command, so the game spreads a
  group around the target point; ordering each separately stacks them on the point instead.
- **`target`** is a point, a unit, or nothing, whichever the ability takes; `api.data.abilities[ability].target_type`
  says which. The wrong kind raises `TypeError` at the call site instead of being sent for the game to answer `ERROR`
  a turn later. A point is stored as the game will read it, rounded to the protocol's 32-bit float, so `order.target`
  is the point the game was given, not quite the one passed in. `camera` stores its point the same way.
- **`queued`** puts the order behind each unit's current orders instead of replacing them.
- **`data`** is the bot's own: why the order was given, what plan it serves, anything. NachOS carries it and never
  reads it. `api.orders.issue(..., data=x)` returns an `Order[type of x]`, so a type checker follows it through.
- **`api.orders.clear_queue(unit)`** drops a unit's queued orders and leaves it on its current one, by sending that
  order again unqueued. It returns the order it sent, or `None` if there was nothing to drop. A structure making
  something is not cleared this way, since the game would only queue another of the same behind it.
- **`api.orders.camera`** moves this player's camera with the turn's orders. Only the last move of a turn is sent.

## One order a unit a turn

A unit carries out only the last order it is given in a turn. Earlier ones become `OVERRIDDEN`. The game does the
same with two unqueued orders in one request, so NachOS sends only the one that would have stood.

The exception is `OrderBehavior.KEEPS_ORDERS`: an ability that leaves the unit doing what it was doing. That is stim,
the cloaks, Guardian Shield, both halves of every toggle, whatever else a sweep saw a moving unit carry out without
breaking its move, and every ability except production that is offered only to types the game offers no move — a
structure's own rally, load, cancel and energy casts, and the way back out of a sieged form. `GENERAL_CANCEL` is not
one of them, since it is also offered to a channeling ghost or infestor, which it takes off what they are doing.

Such an order neither overrides nor is overridden, because the unit does both — a marine stims and keeps moving.
`order.order_behavior` says which kind an ability is.

**A structure is a unit like any other here**: it makes the last thing a turn told it to. The game would queue a
second train behind the first and charge for it from the step it was ordered, money spent before the structure can
start on it, so NachOS sends only the last. To fill a queue on purpose — a reactor's second slot, say, which the
structure starts at once — pass `queued=True`:

```python
api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)
api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE, queued=True)
```

Both go out, and a barracks with a reactor makes both marines at once. Two such orders name the same ability on the
same structure, so NachOS cannot tell their reports apart: they run and finish together.

Orders have no priority of their own. Handlers already run highest priority first, and a later handler checks
`api.orders.issued_to(unit)` to leave alone a unit an earlier one has ordered:

```python
@api.events.on(TurnEvent, priority=EventPriority.LOW)
def keep_the_rest_together(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        if not api.orders.issued_to(marine):
            api.orders.issue(marine, AbilityId.GENERAL_MOVE, target=rally)
```

A bot that wants its own ranking puts it in `data` and reads it back from the orders `issued_to` returns.

An order a unit is already carrying out is not sent again. In game such an order is answered `SUCCESS`, does
nothing and is left out of the reported actions — its one effect is to drop the unit's queued orders. So NachOS
holds it back and the order reads `RUNNING`; to drop a queue on purpose, use `clear_queue`.

## What an order needs

The game handles an order it cannot pay for in one of three ways: it refuses it, which reads `REFUSED` with the
game's verdict; it accepts it and silently drops it, `DROPPED`; or, for a queued order the supply cap cannot feed, it
accepts it, charges for it, and leaves it at no progress until supply frees up.

A bot budgets for itself, since only it knows what matters most:

```python
cost = api.data.abilities[AbilityId.BARRACKS_TRAIN_MARINE].cost
if api.resources.covers(cost.resources) and api.supply.left >= cost.supply:
    api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)
```

`api.resources` and `api.supply` do not change as orders are given. They are what the game last reported, so a bot
giving several orders in one turn keeps its own tally. `cost` is what the game charges when the ability is ordered:
the difference for a morph, 150 for an orbital command rather than its type's 550; and `cost.supply` is what the
ability takes of the cap as it starts.

A cancel is an order like any other. `api.data.abilities[ability].cancelled_by` names the cancel to send to a
structure carrying out `ability`: `GENERAL_CANCEL_LAST` for a train or a research on any structure with a queue, a
tech lab included, and a morph's or an add-on's own cancel, since those answer `GENERAL_CANCEL_LAST` with `ERROR`. A
warp-in has none, since a warp gate keeps no queue. A cancel takes back only the structure's last item, and frees
neither a slot nor a mineral within the same step ([game behavior](game-behavior.md#abilities-and-orders)).

## What became of it

`order.state` is the order's current state, and `api.orders.running` holds every order NachOS is still following.

| State | What it means |
| --- | --- |
| `GIVEN` | Given this turn, and nothing sent yet. |
| `SENT` | Sent and answered `SUCCESS`, with no observation since to say what came of it. |
| `RUNNING` | The game reported carrying it out, or the unit was already doing it. |
| `DONE` | No unit it was given to is carrying it out any more. |
| `LOST` | Every unit seen carrying it out died before it was done, or at least before the next observation: of three barracks given one train, the one that took it. A larva's order reads `DONE`, since its egg is reported dead when what it makes hatches. |
| `REFUSED` | Answered something else: `order.action_result` says what. |
| `DROPPED` | Answered `SUCCESS` and never carried out, which the game does silently to an order that no longer fits by the time it steps. |
| `FAILED` | Carried out and then given up on: `order.failure` says why. |
| `OVERRIDDEN` | A later order took every unit this one was given to, either in the same turn before it was sent or in a later turn. |
| `WITHDRAWN` | Taken back with `order.withdraw()`. An order already sent is only forgotten; the unit goes on with it. |

An order's effect can show up an observation late, so wait for the state rather than expecting it in the next
observation. `api.action_failures` lists every order the game gave up on since the previous observation, whether or
not NachOS was still following it.

## Reading a unit's orders

An `Order` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `UnitOrder` for its
current order and one for each queued. The two need not agree: the game reports the exact ability it runs, snaps a
build to its site, and drops what it cannot carry out.
