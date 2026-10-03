# Orders

A bot orders its units through `api.orders` from its handlers. Orders are sent in one request after the turn's last
handler returns, so a turn that orders nothing sends nothing.

```python
@api.events.on(TurnEvent)
def on_turn(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.orders.issue(marine, AbilityId.GENERAL_ATTACK, target=enemy_base)
```

`issue` returns an `Order`, which says whether it was sent and what the game answered.

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
the cloaks, Guardian Shield, both halves of every toggle a sweep could give a moving unit, whatever else a sweep saw a
moving unit carry out without breaking its move, and every ability except production that is offered only to types
that hold no order of their own — a structure's own rally, load, cancel and energy casts.

Such an order neither overrides nor is overridden, because the unit does both — a marine stims and keeps moving.

A general ability does to each unit what the exact ability its type performs does: `GENERAL_CANCEL` leaves a
structure making what it was making, and takes a channeling ghost or infestor off what it is doing. A group of
several types is judged unit by unit. Hold fire keeps a ghost's orders (in game), and is taken to replace a burrowed
lurker's, which could not be tried, since a burrowed lurker holds an attack and no move. Given to both, it leaves the
ghost the move it was given earlier in the turn and takes the lurker from its attack.
`api.data.abilities[ability].order_behavior_for(unit.type_id)` says what an ability does to a unit of that type, and
`order.order_behavior` what it does to the order's units: the behavior their types share, or `REPLACES` where they
differ.

**A structure is a unit like any other here**: it makes the last thing a turn told it to. The game would queue a
second train behind the first and charge for it from the step it was ordered, money spent before the structure can
start on it, so NachOS sends only the last. To fill a queue on purpose — a reactor's second slot, say, which the
structure starts at once — pass `queued=True`:

```python
api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE)
api.orders.issue(barracks, AbilityId.BARRACKS_TRAIN_MARINE, queued=True)
```

Both go out, and a barracks with a reactor makes both marines at once.

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

**An order a unit is already carrying out is not sent to it.** In game, an unqueued order equal to a unit's first
is answered `SUCCESS` and carries nothing out, but drops what the unit had queued behind it. So when the turn is sent,
an unqueued order that replaces a unit's orders leaves out each unit already doing it, and an order left with no unit
reads `REDUNDANT`. What a unit is doing is the last such order NachOS sent it, while the observation after that turn
may not show it yet, as on the ladder; otherwise it is the unit's first reported order, whoever gave it. So an order
sent last turn counts though the unit still shows its old one, and in a stepped game an order the unit finished, or
the game dropped, within one turn still counts on the next: repeating it then goes out a turn late.

A bot that gives the same order every turn gets a new `Order` each time: the first reads `SENT`, and the rest
`REDUNDANT` for as long as the units carry it out. Until the turn is sent, a repeated order is pending like any other:
it shows in `issued_to`, competes with the turn's other orders, and can be withdrawn. A group that gains a unit is
sent the order for the newcomer alone. To drop a unit's queue on purpose, use `clear_queue`, whose order goes out
regardless.

## What an order needs

The game handles an order it cannot pay for in one of three ways: it refuses it, which reads `REFUSED` with the
game's verdict; it answers `SUCCESS` and silently drops it, so the order reads `SENT` and no unit ever shows it; or,
for a queued order the supply cap cannot feed, it accepts it, charges for it, and leaves it at no progress until
supply frees up.

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

`order.state` says what became of the order in its turn. Every state but `PENDING` is final: an order settles when
the turn's request goes out, and NachOS does not follow it after.

| State | What it means |
| --- | --- |
| `PENDING` | Issued this turn, and nothing sent yet. |
| `SENT` | Sent and answered `SUCCESS`. |
| `REFUSED` | Sent and answered something else: `order.action_result` says what. |
| `OVERRIDDEN` | Never sent: a later order of the same turn took every unit this one was given to. |
| `WITHDRAWN` | Never sent: taken back with `order.withdraw()` while pending. |
| `REDUNDANT` | Never sent: every unit it held was already carrying out the same order, as its first. |

### Learning what the game did

`SENT` says the game took the order, not that a unit carried it out. The game can drop it silently, give up on it
later, or have another unit carry it out, one larva for another. What happened shows in the game itself:

- **`unit.orders`**: what each unit is doing now, its current order and then its queued ones.
- **`OwnConstructionStartedEvent`** and **`OwnUnitCreatedEvent`**: a structure placed, a unit made.
- **`UnitDiedEvent`**: a unit died, and with a producer, whatever it was making was lost.
- **`api.action_failures`**: every order the game gave up on since the previous observation, with the unit and why.

An order's effect can show up an observation late, two on the ladder, so wait for it rather than expecting it in the
next observation.

## Reading a unit's orders

An `Order` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `UnitOrder` for its
current order and one for each queued. The two need not agree: the game reports the exact ability it runs, snaps a
build to its site, and drops what it cannot carry out.
