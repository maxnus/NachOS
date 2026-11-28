# Commands

A bot commands a unit through `unit.command` from its handlers, and several units at once through
`api.commands.command`: `unit.command(ability, ...)` is `api.commands.command(unit, ability, ...)`, and everything here
holds for both. Commands are sent in one request after the turn's last handler returns, so a turn that commands nothing
sends nothing. Every command goes out in the turn it was given, as given, and the game charges what costs then.

```python
@api.on(TurnEvent)
def on_turn(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        marine.command(AbilityId.ATTACK, target=enemy_base)
```

`command` returns a `Command`: what was asked and `withdraw()` to take it back before it is sent. What the game
refuses is listed in `api.action_failures`.

## Giving one

```python
command = marine.command(AbilityId.STIM)
api.commands.command(squad, AbilityId.MOVE, target=ramp)
scv.command(AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, queued=True)
scv.command(AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site, force=True)
```

- **`units`**, of `api.commands.command`, is one unit or any number of them, and each carries the command out; a unit
  given twice counts once. An ability every unit of a command carries out goes out as one action naming them all: a
  move, an attack, a burrow, a lift, a stim, a toggle, a siege of tanks or an unload (`AbilityData.multi_unit`, from
  what the game was seen to do).
  The game spreads such a group around the target point, where an action to each stacks them on the point, and commands
  of such an ability given apart to one target go out as one action too
  ([commands sent together](#commands-sent-together)). Any other ability goes out as an action to each unit, since one
  action naming several has only one of them carry it out: three barracks given a marine train three, and two orbital
  commands given a MULE at one field call down two. A research given to several units raises `ValueError`, and so does
  what puts something at its target, a build, an add-on, a landing, a rooting or a warp-in, given to several aimed at
  one point or geyser, which only one of them can carry out (`AbilityData.is_research`, `places_at_target`): the bot
  picks the unit.
- **`target`** is a point, a unit, or nothing, whichever the ability takes; `api.data.abilities[ability].target_type`
  says which. The wrong kind raises `TypeError` at the call site instead of being sent for the game to answer `ERROR`
  a turn later. A point is stored as the game will read it, rounded to the protocol's 32-bit float, so `command.target`
  is the point the game was given, not quite the one passed in.
- **`queued`** puts the command behind each unit's orders instead of replacing them.
- **`force`** sends the command even to units already carrying it out, which are otherwise left out
  ([one command a unit a turn](#one-command-a-unit-a-turn)). Re-sending a unit's current order unqueued drops what it
  has queued behind it (in game), so this is how a queue is dropped on purpose. A forced command still competes with the
  turn's other commands.

The turn's request also carries `api.camera.move_to(point)`, which centers this player's camera on a point after the
turn's commands, only the last move of a turn being sent, and then `api.chat(text)`, every message of the turn in order.
`api.camera.center` is where the turn's observation has the camera.

## Training, building and researching

`train`, `build` and `research` take what to make or research in place of the ability, and give the ability the
unit's type does it with, as `command` gives it:

```python
barracks.train(UnitTypeId.MARINE)  # BARRACKS_TRAIN_MARINE
warp_gate.train(UnitTypeId.ZEALOT, target=point)  # WARP_GATE_WARP_IN_ZEALOT
larva.train(UnitTypeId.DRONE)  # LARVA_MORPH_DRONE
scv.build(UnitTypeId.SUPPLY_DEPOT, target=site)  # SCV_BUILD_SUPPLY_DEPOT
drone.build(UnitTypeId.EXTRACTOR, target=geyser)  # DRONE_MORPH_EXTRACTOR
barracks.build(UnitTypeId.TECH_LAB_BARRACKS)  # BUILD_TECH_LAB
api.commands.train(api.units.own.of_type(UnitType.Barracks).idle, UnitTypeId.MARINE)
if (tech_lab := barracks.add_on) is not None:
    tech_lab.research(UpgradeId.STIMPACK)  # BARRACKS_TECH_LAB_RESEARCH_STIMPACK
```

- **`train`** gives what a structure or a larva makes that is not a structure: a train, a warp gate's warp-in, aimed
  at a point, and a larva's morph.
- **`build`** gives a structure a worker makes, at a point or on a geyser, and a barracks's, factory's or starport's
  add-on.
- **`research`** gives an upgrade's `research_ability`, given to one of its `researchers`. A barracks's upgrades are
  its tech lab's, so `barracks.research(UpgradeId.STIMPACK)` raises `ValueError` naming the tech lab.
- **`command`** gives the rest: a morph, as of an orbital command, a lair or a baneling, and a change of mode, as a
  lift or a siege.

A unit whose type does not make what it is given raises `ValueError` saying what does, or which of the three gives
it, and so do units whose types make it with different abilities, as a gateway and a warp gate a zealot: they are
commanded apart. Each returns the `Command`, whose `ability` is the one given.

Whether this player has what a product needs is `api.is_unlocked(product)`, a unit type or an upgrade: every tech and
upgrade its `tech_requirements` name, read against `api.techs`, what its finished structures grant, and `api.upgrades`.
It asks the player only, so the producer's own tech lab, `needs_tech_lab`, is left to the bot, and
`unit.production_capacity` says how many the producer makes at once.

## One command a unit a turn

A unit carries out only the last command it is given in a turn. Earlier ones are never sent to it. The game does the
same with two unqueued actions in one request, so NachOS sends only the one that would have stood.

The exception is `CommandBehavior.KEEPS_ORDERS`: an ability that leaves the unit doing what it was doing. That is stim,
the cloaks, Guardian Shield, both halves of every toggle a sweep could give a moving unit, whatever else a sweep saw a
moving unit carry out without breaking its move, and every ability except production that is offered only to types
that hold no order of their own — a structure's own rally, load, cancel and energy casts.

Such a command neither overrides nor is overridden, because the unit does both — a marine stims and keeps moving.

An action several types perform does to each unit what that type's own does: `CANCEL` leaves a
structure making what it was making, and takes a channeling ghost or infestor off what it is doing. A group of
several types is judged unit by unit. Hold fire keeps a ghost's orders and takes a burrowed lurker off its attack (in game).
Given to both, it leaves the ghost the move it was given earlier in the turn and takes the lurker from its attack. A
planetary fortress attacks and stops with the ids every unit does, yet trains on through both, and every structure
that trains does so through a smart, which sets its rally (in game), so for those types those keep their orders.
`api.data.abilities[ability].command_behavior_for(unit.type_id)` says what an ability does to a unit of that type.

**Some ids go out as another of the game's abilities for some unit types.** `api.data.abilities[ability].sent_as`
says which, and what each is aimed at. `UNLOAD` puts everyone down where the transport is: a bunker, a command
center, a planetary fortress and a nydus take it as it is, and a medivac, a warp prism and a transport overlord, which
answer it `Error`, are sent their unload at a point aimed at themselves, which unloads where they are and keeps their
move; aimed at a point, they fly there and unload (in game). So an unload keeps a transport's orders, and an unload at
a point replaces them:

```python
medivac.command(AbilityId.MOVE, target=retreat)
api.commands.command([bunker, medivac], AbilityId.UNLOAD)  # all three actions go out
```

`SIEGE` and `UNSIEGE` are custom ids, which the game has no ability for: `SIEGE` goes out as the tank's, the
observer's or the overseer's own, aimed at nothing, and `UNSIEGE` as theirs or the liberator's. A liberator's siege
takes a point, its zone, and only one of two liberators given one carries it out, so it is an id of its own,
`LIBERATOR_SIEGE_AT`, the game's. A custom id given a type it has nothing to be sent as for raises `ValueError`;
`AbilityId.is_custom` tells one from the game's.

A command goes out as one action for each ability it is sent as, or one per unit where the ability is not one every
unit of a command carries out, or where it is aimed at the unit itself, and such an action may join another command's
([commands sent together](#commands-sent-together)). An action the game refuses is listed in `api.action_failures` once
for each unit it named, under the id given. An unload at a point aimed at one of the transports given it raises
`TypeError`, naming `UNLOAD`.

**A structure is a unit like any other here**: it makes the last thing a turn told it to. The game would queue a
second train behind the first and charge for it from the step it was sent, money spent before the structure can
start on it, so NachOS sends only the last. To fill a queue on purpose — a reactor's second slot, say, which the
structure starts at once — pass `queued=True`:

```python
barracks.command(AbilityId.BARRACKS_TRAIN_MARINE)
barracks.command(AbilityId.BARRACKS_TRAIN_MARINE, queued=True)
```

Both go out, and a barracks with a reactor makes both marines at once.

Commands have no priority of their own. Handlers already run highest priority first, and a later handler checks
`unit.staged_commands` to leave alone a unit an earlier one has commanded:

```python
@api.on(TurnEvent, priority=EventPriority.LOW)
def keep_the_rest_together(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        if not marine.staged_commands:
            marine.command(AbilityId.MOVE, target=rally)
```

A bot that ranks its commands keeps the rank of each unit's command itself, in a `UnitDict` say, and empties it at
each turn's start: `staged_commands` holds the turn's commands alone, but a `UnitDict` keeps a value until its unit
dies.

A command is *staged* until its turn's end, when it is *sent*; the game takes it or refuses it. One it takes is
*pending* while the unit may not show it yet: on the ladder an order can show two observations after its command went
out. `unit.pending_commands` gives those, ahead of the staged ones, and is what a bot checks to leave alone a unit it
has commanded until the unit shows it. Nothing is checked against the observations: a command is pending for as long
as it may take to show, which holds what the unit carries out at once too, a stim, a rally or a MULE, whose effect
may not show yet either.

`unit.commands(since=time)` gives every command given to the unit since `time` that went out to it and that the game
did not refuse, then the staged ones, for up to 60 seconds of game time back. A bot that stims a marine once in a
stim's length checks it, and not the buff, which may show late:

```python
for marine in marines:
    recent = marine.commands(since=api.time - STIM_LASTS)
    if not any(command.ability is AbilityId.STIM for command in recent):
        marine.command(AbilityId.STIM)
```

**A command a unit is already carrying out is not sent to it.** In game, an unqueued action equal to a unit's first
order is answered `SUCCESS` and carries nothing out, but drops what the unit had queued behind it. So when the turn is
sent, an unqueued command that replaces a unit's orders leaves out each unit already doing it, and a command left with
no unit is not sent. What a unit is doing is the last such command NachOS sent it, while the observation after that
turn may not show it yet, as on the ladder; otherwise it is the unit's first reported order, whoever gave it. So a
command sent last turn counts though the unit still shows its old order, and in a stepped game a command the unit
finished, or the game dropped, within one turn still counts on the next: repeating it then goes out a turn late.

A bot that gives the same command every turn gets a new `Command` each time: the first is sent, and the rest are
left out for as long as the units carry it out. Until the turn is sent, a repeated command is staged like any other:
it shows in `staged_commands`, competes with the turn's other commands, and can be withdrawn. A group that gains a unit
is sent the command for the newcomer alone. To drop a unit's queue on purpose, give it its current order with
`force=True`, which goes out regardless.

**What a structure makes is not left out that way.** A train or a research goes behind what the structure is making
(in game), so one given every turn goes out every turn, and the game queues each and charges it then, up to 5, or 8
with a reactor, answering the rest `QueueIsFull`. A bot that wants one at a time trains at a structure that is idle
and whose `pending_commands` hold no train: a rally or a MULE sent lately shows there too, and the structure is idle
all the same. A morph or an add-on given to a structure making something is refused
`NotSupported`, and so is a morph or a train queued behind a lifted structure's landing (in game): give it again once
the structure is idle on the ground.

## Commands sent together

Commands given apart go out as one action where they could have been one command: of an ability that joins other
commands (`api.data.abilities[ability].joins_other_commands`), aimed at the same unit or at the same point as the game
keeps it, sent as the same one of the game's abilities and queued alike. A squad moved to one point a marine at a time
goes out as one move, and the game spreads it around the point as it would one command to them all:

```python
for marine in squad:
    marine.command(AbilityId.MOVE, target=rally)  # one action naming every marine
```

A command joins the last such action before it unless one of its units is named in that action or a later one. So
each unit's commands go out in the order it was given them, a queued one behind the unqueued one before it, and no
action names a unit twice; another unit's commands may go out ahead of commands given before them. A command aimed
at one of its own units joins nothing, and nothing joins it: a medivac unloading where it stands is sent its unload at
a point aimed at itself, which another medivac's unload at that one stays apart from. Every ability that is
`multi_unit` joins other commands but two: an archon's merge, since each merges the two templar it names, and a
load-all, which one action naming two command centers was seen to carry out for one of them.

The game answers each action once, and one naming several units `Success` if any of them can take it
([game behavior](reference/game-behavior/abilities-and-orders.md#one-command-to-several-units)). So a refused action is
listed in `api.action_failures` once for each unit of every command it carried, under that unit's command's ability.
An action taken counts as sent for every command it carried: a command whose units could not take it goes unlisted
where another command's units in the same action could, and the same command given them again is left out as one they
are carrying out until the observations would show it ([one command a unit a turn](#one-command-a-unit-a-turn)). A
single command naming several units works the same way.

## What a command needs

The game handles a command it cannot pay for in one of three ways: it refuses it, which `api.action_failures` lists
with the game's verdict; it answers `SUCCESS` and silently drops it, so nothing is listed and no unit ever shows it;
or, for a queued command the supply cap cannot feed, it accepts it, charges for it, and leaves it at no progress until
supply frees up.

A bot budgets for itself, since only it knows what matters most. `api.budget` is what it has left to spend, the
minerals and vespene and the supply free under its cap:

```python
cost = api.data.abilities[AbilityId.BARRACKS_TRAIN_MARINE].cost
if api.budget.covers(cost):
    barracks.command(AbilityId.BARRACKS_TRAIN_MARINE)
```

`covers` asks for supply only of a cost that takes some, so a player over its cap, once units that provided supply are
lost, can still build what provides more. `api.resources` and `api.supply` do not change as commands are given. They
are what the game last reported, and in a stepped game it charges the turn's commands by the next observation (in
game), so `api.budget` takes off what they will cost, `api.commands.staged_cost`, and read again after a command it
takes that one off too. A bot that sets money aside for what it cannot pay for yet keeps the budget it read and takes
the cost off itself, so that nothing cheaper spends it first:

```python
budget = api.budget
if budget.covers(depot_cost):
    scv.command(AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site)
budget -= depot_cost
if budget.covers(marine_cost):
    barracks.command(AbilityId.BARRACKS_TRAIN_MARINE)
```

`staged_cost` counts the staged commands as they will go out: the last of a unit's commands that replace one another,
and nothing for a unit already carrying a command out. `cost` is what the game charges when the ability is given: the
difference for a morph, 150 for an orbital command rather than its type's 550; `cost.supply` is what the ability takes
of the cap as it starts; and `cost.time` how long it takes at the normal rate, 560 steps for the orbital command, and
none for a switch that costs nothing, such as a siege.

A build is charged when it is given, not when the worker gets there, and so is an add-on given to a lifted
structure, which lands first (in game). A bot that would rather pay on arrival moves the worker itself and gives the
build once it is close: a depot given once the worker is within 8 of its site goes up no later than one given from
30 away (tool `sweep_orders`). `api.placement.reserve` keeps the site out of the placement grids meanwhile, here for
one step at a time, renewed each turn the worker walks:

```python
if scv.distance_to(site) > 2.5:
    scv.command(AbilityId.MOVE, target=site)
    api.placement.reserve(UnitTypeId.SUPPLY_DEPOT, at=site, max_time=GameTime(steps=1))
else:
    scv.command(AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=site)
```

Once given, the build reserves its site itself. A reservation without `max_time` lasts until it is withdrawn or
one of this player's structures stands on its site.

To take back a command before its turn is sent, withdraw it: what the game already has needs a cancel. A cancel is
a command like any other. `api.data.abilities[ability].cancelled_by` names the cancel to send to a
structure carrying out `ability`: `CANCEL_LAST` for a train or a research on any structure with a queue, a
tech lab included, and a morph's or an add-on's own cancel, since those answer `CANCEL_LAST` with `ERROR`. A
warp-in has none, since a warp gate keeps no queue. A cancel takes back only the structure's last item, and frees
neither a slot nor a mineral within the same step
([game behavior](reference/game-behavior/abilities-and-orders.md#cancels-and-refunds)).

## Walling a ramp

`api.player.start_location.main_ramp` is the ramp out of this player's main, and `api.enemy.start_location.main_ramp`
the ramp out of the enemy's. `main_ramp` raises `NoMainRampError` for a main no ramp leads out of, which no map of the
pool has. `api.placement.terran_wall(ramp)` and `protoss_wall(ramp)` say where structures close such a ramp at its
top, near and far going by its main's start:

```python
terran_wall = api.placement.terran_wall(api.player.start_location.main_ramp)
if terran_wall is not None and api.placement.is_free(UnitTypeId.SUPPLY_DEPOT, at=terran_wall.near_depot):
    scv.command(AbilityId.SCV_BUILD_SUPPLY_DEPOT, target=terran_wall.near_depot)
```

- **The Terran wall** is a supply depot at each corner of the top, `near_depot` and `far_depot` by their distance to
  the start, and between them `barracks_in_wall`, `barracks_with_add_on` or `middle_depot`. Raised, the depots keep
  out even a zergling; lowered, they let the bot's own units through. `barracks_with_add_on` leaves room for its
  `add_on`, but on a main left of its ramp it closes the wall only once the add-on stands, while `barracks_in_wall`
  closes it alone with no room for one ([game behavior](reference/game-behavior/map.md)).
- **The Protoss wall** is a `pylon` that powers two 3x3 buildings, gateways or a forge or a cybernetics core, and a
  `gap` beside the `near_building`, at the corner nearer the start, which one unit holding position closes. The
  `far_building` stands at the other corner.

Each is `None` for a ramp not of the shape every main ramp of the pool is: two tiles across its top, a `width` of 2,
and an `orientation` along a diagonal. A ramp out of no start location's main raises `ValueError`. Whether a structure
may stand at a site now is for `api.placement.is_free` to say.

## What the game did

A command goes out when the turn's request is sent, unless the turn left it out: withdrawn, overridden by a later
command to its units, or every unit it was given already carrying it out. Until then it shows in `staged_commands` and
`api.commands.staged`, and once sent in `pending_commands` and `commands(since=...)`. NachOS does not follow what
becomes of it.

The game answers each action as it is sent. One it refuses is listed in `api.action_failures` from the next turn,
once for each unit the action named, with the ability given and the game's verdict. One it takes need not be
carried out: the game can drop it silently, give up on it later, or have another unit carry it out, one larva for
another. What happened shows in the game itself:

- **`unit.orders`**: what each unit is doing now, its current order and then its queued ones.
- **`OwnConstructionStartedEvent`** and **`OwnUnitCreatedEvent`**: a structure placed, a unit made.
- **`OwnUnitDiedEvent`**: a unit of yours died, and with a producer, whatever it was making was lost.
- **`api.action_failures`**: every command the game refused when the last turn's were sent, and every one it accepted
  earlier and gave up on since the previous observation, with the unit and why.

A command's effect can show up an observation late, two on the ladder, so wait for it rather than expecting it in the
next observation.

## Reading a unit's orders

A `Command` is what the bot asked for; `unit.orders` is what the game says the unit is doing, one `Order` for
its current order, `unit.current_order`, and one for each queued. The two need not agree: the game snaps a build to its
site, and drops what it cannot carry out. The ids do agree: a unit reports its type's own id of an action several types
perform, and NachOS reads it as the action's, so a moving marine shows `MOVE` and a gathering SCV `GATHER`. Where the
game reports an id nothing links to the one given, NachOS reads it as the one given too: a sieging liberator shows
`LIBERATOR_SIEGE_AT`, and each templar of a merge `MORPH_ARCHON` aimed at the other.

The targets need not agree either. An SCV walking back to a mineral field out of sight shows its `GATHER` aimed at a
tag the game reports for no unit (in game), so NachOS reads it as aimed at the unit the SCV's last `GATHER` named,
the field it mined: any order aimed so reads as aimed at the unit the unit's last order of the same ability named,
even one dead since, and its `target` raises `NotReportedError` if there was none. A rally onto a unit out of sight
names no unit at all, so it reads as its point (in game).
