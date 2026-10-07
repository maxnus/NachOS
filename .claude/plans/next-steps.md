# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` is at `c0bceb2` (PR #59). `uv run pytest` passes 1477 tests; `uv run pytest -m integration` passes
  19 against a real game (run 2026-09-21).
- **M4 slice 4, orders, is done**: PRs #42, #43 and #45 swept how the game takes orders, cancels and production;
  #44 is the order machinery (`api.orders`); #46 added `Cost`, `AbilityData.cost`, `AbilityData.cancelled_by` and
  `OrderState.LOST`, since removed. User docs: `docs/orders.md`. What the game was seen to do:
  `docs/game-behavior.md`, section "Abilities and orders".
- PRs #47 and #53 to #59 were housekeeping after a code review, mostly renames: `api.events`, `api.orders`,
  `api.orders.issued_to`, `ActionFailure` / `api.action_failures`, `order.action_result`,
  `AbilityData.order_behavior`, `UpgradeData.upgrade_type`, `Unit.cloak_state`, `Units.closest_n_to`,
  `launch.MapFile`, `PlaybackTransport`, `_from_proto`.
- The consuming bot is AvocaDOS ([github.com/maxnus/AvocaDOS](https://github.com/maxnus/AvocaDOS), branch
  `main`). Its `docs/plans/nachOS-plan.md` is the milestone plan (M1 to M6) and records every nachOS PR; its
  `tests/test_nachos_parity.py` compares NachOS with python-sc2 over this repo's corpus and passes again as of
  AvocaDOS `51d0ed1`.

## Decided, so not to be proposed again

- **NachOS checks nothing an order needs** (#46). Minerals, vespene, supply, queue room and tech are the game's to
  judge and the bot's to budget. NachOS sends what it is given, and the order's state and `action_result` say what
  became of it. No budget, no refusal by NachOS itself, no `can_afford`, no `api.orders.cancel`. A built version
  with all of that was dropped as too complicated for what it bought: an advanced bot ranks its own spending anyway.
- **NachOS has no order priority of its own.** A unit takes the last order it is given in a turn. Handlers already
  run priority first, and a bot that wants a rank puts it in the order's `data` and reads `api.orders.issued_to`.
- **Production orders get no "replace".** A cancel frees no queue slot and no minerals in the same step, so a
  replace would leave a structure idle for a turn (#43).
- **`api.data` is static**: the game's tables, read once. Anything that depends on the game's progress is read
  from the state, never written into `api.data`.
- **Reversed on 2026-10-07, done in step 2's PR 2: the general research ids are gone.** Before, a general research id
  cost its first level (`ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS` was 100/100), since it runs the first level until
  that is done. The owner asked whether they are needed at all; a bot researches by level or by `UpgradeId`.
- **Reversed on 2026-09-26: an order keeps only what NachOS knows for certain** (#69): its turn (`PENDING`,
  `OVERRIDDEN`, `WITHDRAWN`, `REDUNDANT`) and the game's answer (`SENT`, `REFUSED`). `RUNNING`, `DONE`, `DROPPED`,
  `LOST` and `FAILED` went, with `api.orders.running`, `order.taken_by` and `order.failure`: each was read from later
  observations, and the code review of 2026-09-24 found each could be wrong. The owner asked: *"Why do we actually
  need to know if the order went through successfully. Could we leave it to the user to check?"* A bot reads its
  units, events and `api.action_failures`, as with python-sc2. This reverses part of #44 and #46.
- **Every call to `issue` is an order of its own; one a unit is already carrying out is not sent to it** (#68).
  With nothing followed across turns, a repeated order costs one small object a turn. The owner first had a repeat
  hand back the order already sent, then reversed that in the review of #77, since the one object could describe
  only its first turn: *"I'm confused why the repeat says sent. I thought repeats are never send?"* Returning `None`
  was weighed and dropped, since `issued_to` and "the last order wins" could not see the call. An order left with no
  unit reads `REDUNDANT`; `GIVEN` became `PENDING`, and `given_step` `issued_step`, since an order is not given
  until it is sent.
- **A general ability does to each unit what the exact ability its type performs does** (#70), and a group of
  several types is judged unit by unit. `KEEPS_ORDERS` is inferred only for types that hold no order of their own. An
  exact id the tables offer to no type has no say in its general's own behavior, so a command center's and a
  medivac's unload keep orders as a bunker's does (review of #77, reversing "a medivac falls back to `REPLACES`").
- **No `clear_queue`; `issue(..., force=True)` instead** (review of #77, 2026-10-06). The owner asked why it was
  needed at all, with a queue in NachOS coming that leaves the game's queue little to do, and had it removed on
  condition that an order can still be sent though a unit is carrying it out. A forced order is never `REDUNDANT`,
  and re-sending a unit's current order that way drops its queue as `clear_queue` did.
- **An ability whose behavior depends on its target is split into custom ids, one per behavior** (review of #77,
  2026-10-06), so that a behavior stays one per ability and type. The owner's idea. Done for the unload in #101: one
  `GENERAL_UNLOAD_IN_PLACE` for every transport, drawn from a counter in `AbilityId` that starts at 1,000,000, and an
  unload at a point aimed at the transport itself raises `TypeError` (the owner's choices, 2026-10-06, in review:
  one id rather than one per transport, "custom ids" rather than "NachOS ids"). PR 3 below folded
  `GENERAL_UNLOAD_IN_PLACE` back into `GENERAL_UNLOAD`, which is sent as each transport's own unload. A load, three spells and a fortress's attack were swept and
  need no split (docs/game-behavior.md).
- **One public id per action** (2026-10-07, step 2). A family whose members do one thing for different performers
  collapses to one id, which is issued, offered and read back; its per-unit ids stay only in `RawAbilityId`. The leveled
  researches keep one id per level. `GENERAL_CANCEL`'s family collapses whole into `CANCEL` (the owner, on the
  measurement; it replaced an earlier choice of one custom `CANCEL_ADD_ON` for the add-on cancels). The liberator's and
  the archon's `_EXACT` ids are read as the id ordered; a repeated `MORPH_ARCHON` is sent again rather than
  special-cased. A field that varies by performer is a mapping from unit type on every row (`row.products[unit_type]`).
  `GENERAL_` goes from every name. The owner's choices, in conversation, after asking *"why is there a `LIBERATOR_SIEGE`
  and a `LIBERATOR_SIEGE_EXACT`"*.
- **Reversed on 2026-09-21: NachOS will keep a queue per unit** (step 1 below). Until then it sent every order in
  the turn it was given and kept nothing across turns; that was a decision of 2026-09-19, which the owner has
  overturned.

## 1. A queue per unit, kept in NachOS

The owner's words (2026-09-21): *"we want a custom queue for every unit within nachos. There is never a point to
queue anything in game (exception: terran production with reactor) for a bot, so we move the queue system into the
library."* And earlier the same day: *"a worker walking to a location to build something but the build is not yet
sent to the game to avoid paying upfront. The intention to build is known to nachos however (i.e. unit type and
location) and as soon as the worker is close enough, they will start the build. Similar for the flying barracks +
build add-on."*

**Status: agreed in principle, not planned.** Start with a plan on a new branch off `origin/main`, and settle it
with the owner (Claude Code's plan mode) before writing code.

### Why: what the game does with an order it cannot carry out yet

All measured, in `docs/game-behavior.md` under "Abilities and orders" and "Units coming, changing and going":

- **A build is charged when ordered**, not when the builder arrives: a depot ordered 35 away took its 100 minerals
  by the next observation. A build queued behind a move is charged when given too, while the worker is still on
  its first leg.
- **A flying barracks given an add-on** needs a point (where to land), queues `BARRACKS_LAND` and the build, and is
  charged at once: the reactor's 50/50 was gone 430 steps before it landed. Given a point whose add-on spot is
  blocked, it answers `CantFindPlacementLocation`. A factory and a starport were not tried.
- **A train or research queued behind what a structure is making** is paid from the step it is ordered. With the
  supply cap full, a train is taken and charged, then sits at no progress as long as the cap stays full.
- **Refused now, allowed later**: a morph or add-on on a busy structure is `NotSupported`; a structure researching
  is offered no research at all; a warp gate and a larva keep no queue; a spell queued behind a move is dropped
  silently if the energy is gone by then.

### Cases, as the previous agent grouped them for the owner

1. **Charged when given, carried out later (the money case)**: a worker walking to build; a build behind any number
   of moves (scouting worker, proxy); a flying barracks, factory or starport and its add-on; a train or research
   queued behind what a structure is making (give the next item when a slot frees; on a reactor, two slots); a train
   with the supply cap full.
2. **Refused now, allowed later (the timing case)**: an orbital as soon as the SCV being trained finishes; +2 as soon
   as +1 finishes; a warp-in when the gate is ready; a larva morph when a larva is free; a spell on arrival; something
   whose tech requirement is still going up.
3. **Sequences the game may not queue at all (not measured)**: a flying command center that lands at an expansion
   and then becomes an orbital or planetary fortress; a lifted barracks that lands and then trains.

### What the plan has to settle

- **How it looks to a bot.** What `queued=True` means now (behind NachOS's queue, not the game's); how a bot adds,
  reads, reorders and withdraws items; what an `Order` in NachOS's queue reads as (a new state before `SENT`?); and
  the one exception, a structure with a reactor, where the game's own second slot is wanted.
- **What an unqueued order does to the queue**: presumably replaces it, as the game does.
- **When an item goes out.** Conditions on position and on the unit's state ("close enough", "landed", "idle", "gate
  ready", "larva free") fit the rule that NachOS checks nothing an order needs. Waiting on minerals, supply or tech
  would bring those checks back; the previous agent recommended leaving them to the bot and sending the item
  anyway, letting the game refuse it. The owner has not ruled on this yet: ask.
- **What ends a queue**: the unit dying, morphing, being taken over; an item refused or failed (does the rest go
  on?); observation lag, since an order's effect can show up an observation late. An order's state no longer says
  whether a unit carried it out (decided 2026-09-26), so an item is released on the unit's condition, read from
  the unit itself. The order book already keeps, per unit, the last order sent that replaced its orders and the
  observation its turn read, and takes it for what the unit is doing while the next observation may not show it
  (PR #77): that record is the queue's head, and that rule is how the queue tells an item not yet shown from one
  finished or dropped.
- **What stays in the game's queue.** Speed mining needs the game's own queue so the next leg starts on the exact
  step; a queue held by the library advances only at a turn boundary. Micro re-decides every step on purpose, one
  order per unit. So the game's queue has to stay reachable for those.
- **What it rewrites**: #44's machinery (`orders/_order_book.py`, `_order.py`, `_order_state.py`), and
  `docs/orders.md`, where `queued=True`, the reactor example and the cancel paragraph change: taking back an item
  NachOS still holds becomes a plain withdraw, and only what the game already has needs a real cancel
  (`AbilityData.cancelled_by`).

### Measurements it needs (`tools/sweep_orders.py`, findings into `docs/game-behavior.md`)

- **"Close enough" for a build**: send the build at several distances and record the steps the worker loses. Too
  late and it stops and waits; too early and the minerals are spent while it walks.
- Whether a drone and a probe are charged at the order as an SCV is.
- What happens to a build whose site is blocked before the worker arrives (expected: an error and no charge).
- A factory's and a starport's add-on while flying, assumed to behave as a barracks's.
- Whether a flying command center can be given land and then a morph, and what a lifted barracks does with land and
  then a train.
- What a flying command center's load-all does to its move. It reads `REPLACES`; the landed command center's and the
  planetary fortress's, which keep their training, were measured (docs/game-behavior.md).

### Suggested first pull request

The three money cases whose state is plain to observe: the worker build (with its behind-a-move form), the flying
add-on, and the queued train or research. The timing cases follow the same pattern and can come after. Confirm the
scope with the owner.

## 2. One id per action: the families and the `_EXACT` ids

Settled with the owner on 2026-10-07 (see "Decided" above); not started. The owner's words: *"why is there a
`LIBERATOR_SIEGE` and a `LIBERATOR_SIEGE_EXACT` - could we do with one? [...] is it really necessary to have a
separate `BARRACKS_LIFT` and `STARPORT_LIFT`?"* `OwnUnit` methods such as `barracks.lift()` (step 3, slice 7) would
not make this unnecessary: ids also show in `unit.orders`, `unit.abilities` and `api.orders.issue`, and fewer of
them also shorten NachOS's own code.

### What there is now

485 ability ids. 188 of them are per-unit ids that the game remaps onto one of 49 general ids. A unit is offered and
reports its own per-unit id. The game accepts the general id as an order and runs each unit's per-unit id (#70). So
`GENERAL_LIFT` can be issued to a barracks and a starport together, but a bot cannot read it back: to ask whether a
worker is gathering, it tests `SCV_GATHER`, `PROBE_GATHER`, `DRONE_GATHER` and `MULE_GATHER`. Inside the order book,
`_general_ability` already compares every order by its general id. python-sc2 did half of this: its
`AbilityData.id` returns the general id.

The `_EXACT` names are two different things:

- `GENERAL_MOVE_EXACT`, `GENERAL_ATTACK_EXACT`, `GENERAL_STOP_EXACT`, `GENERAL_HOLD_POSITION_EXACT` and
  `GENERAL_PATROL_EXACT` are ordinary family members. They are the per-unit id of every ordinary unit at once, so
  there was no unit to name them after.
- `LIBERATOR_SIEGE_EXACT`, `LIBERATOR_UNSIEGE_EXACT` and `GENERAL_MORPH_ARCHON_EXACT` were the oddities: the id
  ordered differs from the id reported, and `remaps_to` links neither. Done in #103, which reads each as the id
  ordered. The agent had expected a repeated siege to be sent instead of reading `REDUNDANT` because of them; in game
  the repeat is never the same order anyway, since the liberator's report is aimed at itself and not at the point
  ordered, and the game refuses it `NotSupported`.

### What changes

One public id per action. It is the id issued, the one offered in `unit.abilities`, and the one read back in
`unit.orders`.

- **A family whose members do one thing for different performers collapses to one id.** The per-unit ids leave
  `AbilityId` and stay only in `RawAbilityId`, for translating what the game sends. That is every family below.
- **The leveled researches keep one id per level, and their general ids go** (13 ids). A structure is offered and
  reports the level, and `api.data.upgrades[upgrade].research_ability` names it. AvocaDOS researches by `UpgradeId`
  and never uses a general research id. With them go the first-level cost of a general research
  (`_ability_data.py`, the `first_level` code), the naming rule and test that tie a general research to its levels,
  the deferred `api.next_level`, and `_general_ability` in the order book, whose last use they were.
- **`GENERAL_CANCEL`'s family collapses whole into `CANCEL`** (22 ids), the owner's choice on the measurement below:
  a unit is never offered two of its cancels at once, and the generic one does what each does. `cancelled_by` is
  `CANCEL` for a morph, an add-on or a structure going up, and `CANCEL_LAST` for a train or a research.
  `CANCEL_LAST` stays a second id: the generic cancel is answered `Error` by a structure that is training.
- **The three oddities are read as the id ordered.** The liberator's two `_EXACT` ids go. `Archon_Warp_Target`
  (1767) is read as `MORPH_ARCHON`: a walking templar then shows `MORPH_ARCHON` aimed at its partner. Issued again
  while they walk, the order has no target where the reported one has, so the repeat is not recognised and is sent.
  The owner accepted that rather than a special rule in the repeat check.
- **Translation happens on the wire.** When reading (`UnitOrder._from_proto`, the order book, the builder tracker,
  `unit.abilities`), a per-unit id becomes its family's id. When sending, the family's id goes out as it is, since
  the game takes it.
- **A field that varies by performer becomes a mapping from unit type, on every row**: `row.products[unit_type]`,
  with one entry for an ability one type performs. That is the owner's choice of the three shapes offered. The
  others were `row.product_for(unit_type)`, as `order_behavior_for` is, and `row.by_performer[unit_type].product`.
  Required tech already lives per unit type, in `TECH_TREE.ability_requirements[unit_type][ability]`.
- **`GENERAL_` goes from every name**: `LIFT`, `BURROW`, `STIM`, `GATHER`, `SMART`, `SALVAGE`, `SIEGE`,
  `MORPH_ARCHON`, `CANCEL`, and so on. `VIKING_LIFT` (`Morph_VikingFighterMode`) is not in the lift family and keeps
  its name, beside `LIFT`.

About 165 fewer ids: 127 per-unit ids, the 22 cancels, the 13 general researches, and the 3 oddities.

The families that collapse, with what their members differ in (from `api.data` and `TECH_TREE` on main,
2026-10-07):

| Family | Ids | Differs per performer |
|---|---|---|
| `GENERAL_ATTACK` | 4 | order behavior (bunker, battlecruiser); requirement (`GENERAL_SCAN_MOVE` needs tunneling claws when burrowed) |
| `GENERAL_BLINK` | 2 | requirement (blink or shadow stride research) |
| `GENERAL_BUILD_CREEP_TUMOR` | 2 | product (`CREEP_TUMOR` or `CREEP_TUMOR_QUEEN`) |
| `GENERAL_BUILD_REACTOR`, `GENERAL_BUILD_TECH_LAB` | 3 + 3 | product |
| `GENERAL_BURROW` | 12 | product (the burrowed type); requirement (none for lurker and widow mine) |
| `GENERAL_UNBURROW` | 12 | `allows_autocast`. Every burrowed zerg type is offered 10 of the 12, so they are interchangeable |
| `GENERAL_CANCEL_LAST` | 7 | nothing; no type is offered two |
| `GENERAL_CLOAK_ON` / `_OFF` | 2 + 2 | requirement (banshee or ghost cloak) |
| `GENERAL_GATHER`, `GENERAL_RETURN`, `GENERAL_REPAIR`, `GENERAL_SPRAY` | 4, 4, 2, 3 | nothing |
| `GENERAL_HALT` | 2 | nothing |
| `GENERAL_HOLD_FIRE_ON` / `_OFF` | 2 + 2 | nothing |
| `GENERAL_HOLD_POSITION`, `GENERAL_MOVE`, `GENERAL_PATROL` | 2 each | requirement (tunneling claws when burrowed) |
| `GENERAL_STOP` | 3 | order behavior |
| `GENERAL_LAND` | 5 | nothing |
| `GENERAL_LIFT` | 5 | product (the flying type) |
| `GENERAL_LOAD`, `GENERAL_LOAD_ALL` | 6, 1 | nothing |
| `GENERAL_UNLOAD`, `GENERAL_UNLOAD_AT` | 7, 7 | order behavior (unload) |
| `GENERAL_RALLY_UNITS`, `GENERAL_RALLY_WORKERS` | 2, 3 | nothing |
| `GENERAL_RECALL`, `GENERAL_STIM` | 2, 2 | nothing |
| `GENERAL_ROOT`, `GENERAL_UPROOT` | 2, 2 | product (uproot) |

Only `product`, `cancelled_by` and `allows_autocast` vary on `AbilityData`. `product` is read for the derived cost
and for whether an ability makes a structure, and both come out the same within each family.

### What it costs

- **The data.** `AbilityData` and `TechTree` are keyed by per-unit id: `ability_requirements`, `ability_products`,
  `creation_abilities`, `ability_cancels` and `ability_remaps`. `ability_products` stops being a function, since
  `LIFT` makes five flying types.
- **The generator and `data/tech_tree.json`.** The sweep records what the game offers, which is per-unit ids, and
  keeps doing so. The fold onto family ids happens where the file is loaded, so the file stays a record of the game.
- **The order book gets shorter**: `_general_ability` goes.
- **Docs.** The conventions in `docs/curating-ids.md` (performer first, `GENERAL`, `_EXACT`, the general research's
  name), `docs/migrating-from-python-sc2.md` (`remaps_to`, `exact_id`), and the examples in `docs/orders.md` and
  `docs/game-behavior.md`.
- **AvocaDOS.** `tests/test_nachos_id_parity.py` still holds, since every remaining id but the custom ones is a game
  id. The bot is ported once at M5, so this should land before then.

### Measurements it needs (`tools/sweep_orders.py`)

- **The liberator repeat.** Done in #103 (runs 37605755080 and 37606192575): a liberator is sieged by the next
  observation, reports its siege aimed at itself for the 64 steps its zone takes to form, and refuses the siege
  again `NotSupported`.
- **`GENERAL_ATTACK` against `GENERAL_SCAN_MOVE`.** Done (runs 37610767491 and 37611898076, docs/game-behavior.md).
  At a point, `GENERAL_ATTACK` runs `GENERAL_ATTACK_EXACT` for the four offered both, as for a marine. For the 24
  types offered the scan move and not the attack, the medivac among them (the owner's question), it runs as the scan
  move, at a point or at an enemy, and `GENERAL_ATTACK_EXACT` is refused; a burrowed infestor or roach does the same.
  So the scan move is the per-unit attack of the units that cannot fire, it reads as `ATTACK`, and a bot loses
  nothing it was seen to need.
- **What `GENERAL_CANCEL` does, and whether it collapses whole.** Done (runs 37610767491 and 37611317534): in each
  of 23 states a unit is offered a cancel of the family in, from a morphing command center to a nuke, it is offered
  exactly one, and `GENERAL_CANCEL` does what that one does; the game even reports the action as the unit's own
  cancel. The owner chose to collapse the family whole into `CANCEL` (2026-10-07).
- **`allows_autocast` on the unburrows.** Done (run 37610767491): an unburrow's autocast is the performer's. A burrowed
  roach comes up by autocast and a burrowed drone does not, whichever per-unit id is switched; switching
  `GENERAL_UNBURROW` does nothing. The plan was to make `allows_autocast` a mapping from performer; PR 2 left it a
  bool, "for some type that carries it out", with the roach and the drone named in its docstring. The game's rows
  say which unburrow id allows it, but nothing links a burrowed type to its own unburrow among the ten it is offered,
  so a mapping would need a rule written by hand. It matters once NachOS sends autocast switches (it reads them only,
  as `AutocastToggle`): a family id must then go out as a performer's own id, and that needs the same link. Raised
  with the owner in PR 2; not decided.

### Pull requests

1. **The three oddities.** #103: `AbilityId._REMAPPED_IDS`, read by `AbilityId.read` and `get`, with just
   the liberator and archon pairs. The next PR extends it.
2. **The families.** PR 2 (branch `claude/one-id-per-action-families`): the map extended to 146 ids, the per-unit
   ids, the cancels and the general research ids gone from `AbilityId`, the generator folding at the end, products
   by performer, order behaviors judged per type, and the docs. Where it departs from the plan: `allows_autocast`
   stays a bool (above), and a type that holds no order of its own keeps it for every ability that makes nothing, so
   a missile turret or a cannon given an attack or a smart now keeps its orders, where it took the behavior of the
   game id it shared with units. A gateway's warp gate morph needed a new override, `SELF_MORPHS`.
3. **One id sent as each unit type's own** (the owner, in the review of #104, 2026-10-07). PR 3 (branch
   `claude/one-id-per-type`): `AbilityData.sent_as`, from the `ABILITIES_SENT_AS_ANOTHER` override, replaces `CUSTOM_ABILITIES`; a
   custom row's target type and cast range are worked out from the abilities it is sent as, and the sieged forms are
   listed too, so a repeated siege is still refused by the game rather than by NachOS. A custom id whose
   `sent_as` names a game ability per unit type, a group order going out as one command per type and answered as
   #101 answers a group. With it:
   - `GENERAL_UNLOAD` and `GENERAL_UNLOAD_IN_PLACE` become one id, `GENERAL_UNLOAD`, "put everyone down here": sent as
     UnloadAll to a bunker, command center, planetary fortress or nydus, and as UnloadAllAt aimed at itself to a
     medivac, warp prism or transport overlord, which answer UnloadAll `Error` (`held-orders`). `GENERAL_UNLOAD_AT`
     stays, for a point.
   - `GENERAL_SIEGE` and `GENERAL_UNSIEGE` for the tank, the liberator, the observer and the overseer, the owner's
     choice of all four. The game has no shared id for either. The siege takes a point, which only the liberator's
     part uses, for its zone; the unsiege takes nothing. Each type's own game id reads as the custom one.
4. **The rename**: `GENERAL_` dropped everywhere, as a mechanical PR on its own so the earlier diffs stay readable.
   PR 4 (branch `claude/drop-general-prefix`): 39 ids renamed, `AbilityId` re-sorted by name (the two custom ids keep
   their order, so their values), the tech tree regenerated (reordered only) and the naming rule in the docs reworded.
   Earlier entries in this plan keep the names of their day.

## 3. The rest of M4

From AvocaDOS's `docs/plans/nachOS-plan.md`, section "M4 slices". Each slice is one nachOS PR; the owner chooses
when each starts.

5. **The derived reads**: workers, townhalls, mineral fields, geysers, this player's start location, counts in
   production, and `api.enemy.units` against `api.units.enemy`.
6. **Debug and chat**: typed debug commands, drawing, sending chat, `query_pathing` and leaving a game.
7. **Typed order methods on `OwnUnit`**, or the decision to defer them.
8. **The demo bot**, M4's exit gate: a small bot in this repo (build workers, expand, attack) that plays a full game
   against `Computer` using only NachOS, and doubles as the library's example for other developers.

## 4. After each nachOS pull request merges

Record it in AvocaDOS's `docs/plans/nachOS-plan.md` on `main`: a paragraph under "M4 status" saying what it
settled and what AvocaDOS writes differently at M5. That needs an AvocaDOS checkout; its tests (`uv run pytest`,
`tests/test_nachos_parity.py` in particular) install nachOS from source. Commit there, and push only when the owner
says so.

## 5. Later, at M5 (AvocaDOS runs on NachOS)

In AvocaDOS's plan, section "M5 — The swap". Already known:

- AvocaDOS's own `api.event` and `api.order` become NachOS's `api.events` and `api.orders`; its `OrderManager` keeps
  the *highest*-priority order where NachOS keeps the *last*, and `has_order(unit)` is `api.orders.issued_to(unit)`.
- `phase=` becomes `priority=`, with the handlers that forget the dead at `HIGHEST`.
- The bot's `MEDIVACINCREASESPEEDBOOST`, `DUTCHMARAUDERSLOW` and `IMMORTALOVERLOAD` take their curated names;
  AvocaDOS's `tests/test_nachos_id_parity.py` fails on those three until then, as expected.
