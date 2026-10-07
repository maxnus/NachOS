# Orders

A bot orders its units through `api.orders` from its handlers. Orders are sent in one request after the turn's last
handler returns, so a turn that orders nothing sends nothing.

```python
@api.events.on(TurnEvent)
def on_turn(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.orders.issue(marine, AbilityId.ATTACK, target=enemy_base)
```

`issue` returns the `Order`: what was asked, the bot's own `data`, and `withdraw()` to take it back before it is
sent. What the game refuses is listed in `api.action_failures`.

## Giving one

```python
order = api.orders.issue(marine, AbilityId.STIM)
api.orders.issue(squad, AbilityId.MOVE, target=ramp)
api.orders.issue(scv, AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, queued=True)
api.orders.issue(marine, AbilityId.ATTACK, target=drone, data=Defending(base))
api.orders.issue(scv, AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, force=True)
api.orders.camera(base)
```

- **`units`** is one unit or any number of them. They are ordered together, in one command, so the game spreads a
  group around the target point; ordering each separately stacks them on the point instead.
- **`target`** is a point, a unit, or nothing, whichever the ability takes; `api.data.abilities[ability].target_type`
  says which. The wrong kind raises `TypeError` at the call site instead of being sent for the game to answer `ERROR`
  a turn later. A point is stored as the game will read it, rounded to the protocol's 32-bit float, so `order.target`
  is the point the game was given, not quite the one passed in. `camera` stores its point the same way.
- **`queued`** puts the order behind each unit's current orders instead of replacing them.
- **`force`** sends the order even to units already carrying it out, which are otherwise left out
  ([one order a unit a turn](#one-order-a-unit-a-turn)). Re-sending a unit's current order unqueued drops what it has
  queued behind it (in game), so this is how a queue is dropped on purpose. A forced order still competes with the
  turn's other orders.
- **`data`** is the bot's own: why the order was given, what plan it serves, anything. NachOS carries it and never
  reads it. `api.orders.issue(..., data=x)` returns an `Order[type of x]`, so a type checker follows it through.
- **`api.orders.camera`** moves this player's camera with the turn's orders. Only the last move of a turn is sent.

## One order a unit a turn

A unit carries out only the last order it is given in a turn. Earlier ones are never sent to it. The game does the
same with two unqueued orders in one request, so NachOS sends only the one that would have stood.

The exception is `OrderBehavior.KEEPS_ORDERS`: an ability that leaves the unit doing what it was doing. That is stim,
the cloaks, Guardian Shield, both halves of every toggle a sweep could give a moving unit, whatever else a sweep saw a
moving unit carry out without breaking its move, and every ability except production that is offered only to types
that hold no order of their own — a structure's own rally, load, cancel and energy casts.

Such an order neither overrides nor is overridden, because the unit does both — a marine stims and keeps moving.

An action several types perform does to each unit what that type's own does: `CANCEL` leaves a
structure making what it was making, and takes a channeling ghost or infestor off what it is doing. A group of
several types is judged unit by unit. Hold fire keeps a ghost's orders and takes a burrowed lurker off its attack (in game).
Given to both, it leaves the ghost the move it was given earlier in the turn and takes the lurker from its attack. A
planetary fortress attacks and stops with the ids every unit does, yet trains on through both, and every structure
that trains does so through a smart, which sets its rally (in game), so for those types those keep their orders.
`api.data.abilities[ability].order_behavior_for(unit.type_id)` says what an ability does to a unit of that type, and
`order.order_behavior` what it does to the order's units: the behavior their types share, or `REPLACES` where they
differ.

**Some ids go out as another of the game's abilities for some unit types.** `api.data.abilities[ability].sent_as`
says which, and what each is aimed at. `UNLOAD` puts everyone down where the transport is: a bunker, a command
center, a planetary fortress and a nydus take it as it is, and a medivac, a warp prism and a transport overlord, which
answer it `Error`, are sent their unload at a point aimed at themselves, which unloads where they are and keeps their
move; aimed at a point, they fly there and unload (in game). So an unload keeps a transport's orders, and an unload at
a point replaces them:

```python
api.orders.issue(medivac, AbilityId.MOVE, target=retreat)
api.orders.issue([bunker, medivac], AbilityId.UNLOAD)  # all three commands go out
```

`SIEGE` and `UNSIEGE` are custom ids, which the game has no ability for: each goes out as the tank's,
the liberator's, the observer's or the overseer's own. The siege takes a point, which only the liberator's is aimed at,
for its zone, so an order naming a liberator needs one and the rest ignore it. A custom id given a type it has nothing
to be sent as for raises `ValueError`; `AbilityId.is_custom` tells one from the game's.

An order goes out as one command for each ability it is sent as, or one per unit where that is aimed at the unit
itself. A command the game refuses is listed in `api.action_failures` once for each unit it named, under the id
ordered. An unload at a point aimed at one of the transports ordered raises `TypeError`, naming `UNLOAD`.

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
            api.orders.issue(marine, AbilityId.MOVE, target=rally)
```

A bot that wants its own ranking puts it in `data` and reads it back from the orders `issued_to` returns.

**An order a unit is already carrying out is not sent to it.** In game, an unqueued order equal to a unit's first
is answered `SUCCESS` and carries nothing out, but drops what the unit had queued behind it. So when the turn is sent,
an unqueued order that replaces a unit's orders leaves out each unit already doing it, and an order left with no unit
is not sent. What a unit is doing is the last such order NachOS sent it, while the observation after that turn
may not show it yet, as on the ladder; otherwise it is the unit's first reported order, whoever gave it. So an order
sent last turn counts though the unit still shows its old one, and in a stepped game an order the unit finished, or
the game dropped, within one turn still counts on the next: repeating it then goes out a turn late.

A bot that gives the same order every turn gets a new `Order` each time: the first is sent, and the rest are left
out for as long as the units carry it out. Until the turn is sent, a repeated order is pending like any other:
it shows in `issued_to`, competes with the turn's other orders, and can be withdrawn. A group that gains a unit is
sent the order for the newcomer alone. To drop a unit's queue on purpose, issue its current order with `force=True`,
which goes out regardless.

## What an order needs

The game handles an order it cannot pay for in one of three ways: it refuses it, which `api.action_failures` lists
with the game's verdict; it answers `SUCCESS` and silently drops it, so nothing is listed and no unit ever shows it;
or, for a queued order the supply cap cannot feed, it accepts it, charges for it, and leaves it at no progress until
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
structure carrying out `ability`: `CANCEL_LAST` for a train or a research on any structure with a queue, a
tech lab included, and a morph's or an add-on's own cancel, since those answer `CANCEL_LAST` with `ERROR`. A
warp-in has none, since a warp gate keeps no queue. A cancel takes back only the structure's last item, and frees
neither a slot nor a mineral within the same step ([game behavior](game-behavior.md#abilities-and-orders)).

## What the game did

An order goes out when the turn's request is sent, unless the turn left it out: withdrawn, overridden by a later order
to its units, or every unit it was given already carrying it out. Until then it shows in `issued_to` and `pending`.
NachOS does not follow it after.

The game answers each command as it is sent. One it refuses is listed in `api.action_failures` from the next turn,
once for each unit the command named, with the ability ordered and the game's verdict. One it takes need not be
carried out: the game can drop it silently, give up on it later, or have another unit carry it out, one larva for
another. What happened shows in the game itself:

- **`unit.orders`**: what each unit is doing now, its current order and then its queued ones.
- **`OwnConstructionStartedEvent`** and **`OwnUnitCreatedEvent`**: a structure placed, a unit made.
- **`UnitDiedEvent`**: a unit died, and with a producer, whatever it was making was lost.
- **`api.action_failures`**: every order the game refused when the last turn's were sent, and every one it accepted
  earlier and gave up on since the previous observation, with the unit and why.

An order's effect can show up an observation late, two on the ladder, so wait for it rather than expecting it in the
next observation.

## Reading a unit's orders

An `Order` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `UnitOrder` for its
current order and one for each queued. The two need not agree: the game snaps a build to its site, and drops what it
cannot carry out. The ids do agree: a unit reports its type's own id of an action several types perform, and NachOS
reads it as the action's, so a moving marine shows `MOVE` and a gathering SCV `GATHER`. Where the game
reports an id nothing links to the one ordered, NachOS reads it as the one ordered too: a sieging liberator shows
`SIEGE`, and each templar of a merge `MORPH_ARCHON` aimed at the other.
