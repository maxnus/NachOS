# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` has every PR to #121. `uv run pytest` passes 1716 tests on the expansions' branch; `uv run pytest -m
  integration` passes 27 against a real game (run 2026-10-08).
- **Step 1, a queue per unit, is done**: the money cases (#115, #116), the lifted command center (#117), the timing
  sweeps (#118), and the larva hold (#120). Warp-ins are not held, and spells stay with the game. Step 2, one id per
  action, is done (#103 to #107). Step 3, the rest of M4, is under way: slice 5, the derived reads, is done (#121),
  and the expansions the demo bot needs are on branch `claude/expansions`.
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
  judge and the bot's to budget. NachOS sends what it is given, and `api.action_failures` lists what the game
  refused. No budget, no refusal by NachOS itself, no `can_afford`, no `api.orders.cancel`. A built version
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
  units, events and `api.action_failures`, as with python-sc2. This reverses part of #44 and #46. Reversed again on
  2026-10-07: an order has no state at all (below).
- **Reversed on 2026-10-07: an order has no state** (step 1's PR A, #115). `order.state`, `order.action_result` and
  `OrderState` went. A command the game refuses is listed in `api.action_failures` on the next turn, once for each
  unit it named, under the id ordered, ahead of what the game gave up on. The `Order` stays as a handle: its `data`,
  `withdraw()`, and what `issued_to` and `pending` list. The owner, while the queue was being planned: *"why do we
  need order state at all? In python-sc2 I believe issuing an order doesn't return anything. What do we get by having
  this object with a state?"* The state's one job nothing else did was to carry the game's refusal; `OVERRIDDEN`,
  `WITHDRAWN` and `REDUNDANT` a bot rarely needs, and the `HELD` and `DROPPED` the queue would have needed,
  `issued_to` already says. It also spares a group order split by the queue a state per unit.
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

**Status: done with the larva hold.** The money cases are #115 and #116; the timing cases (2026-10-07, "The timing
cases" below) are #117, #118 and the larva hold. The decisions and the design follow the cases below.

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

### Decided for the queue (the owner, 2026-10-07)

Settled one question at a time, each with the previous agent's recommendation unless it says otherwise.

1. **An item waits on its unit alone.** It goes out once its unit can start it (the worker within reach of its site,
   the structure landed, a free slot), whatever the bank, the supply cap or the tech say. #46 stands: what the bank
   lacks, the game refuses, and the refusal shows in `api.action_failures`.
2. **NachOS holds only an item that costs and that its unit cannot start yet**: a worker's build, a train, a
   research, an add-on, a structure's morph. Everything else goes to the game as now, queued or not, so speed mining
   and micro are unchanged. A free item queued behind a held one is held too and goes out right behind it, queued, in
   the same request. A held build sends a move to its site ahead of it (its lead-in); a flying structure's add-on
   sends a `LAND` at its point, and goes out with no target once the structure has landed and is idle. A structure's
   game queue holds only what it runs at once, one item or two with a finished reactor, so the reactor is the same
   rule with two slots, not an exception.
3. **A held order is read through `issued_to(unit)`**: the held items in queue order, then the turn's pending ones.
   `pending` stays the turn's own. `withdraw()` takes a held order back. No reorder. Withdrawing a held build leaves
   its lead-in.
4. **`queued` follows the ability's `OrderBehavior`** (first decided as "every unqueued order replaces the held
   items", then reconsidered with the owner, since the hold removes both reasons a structure made the last thing a
   turn told it: the money, and the reactor's `queued=True`):
   - `REPLACES` (move, attack, build): unqueued replaces the held items and the game's queue; queued goes behind.
   - `QUEUES` (train, research): always goes behind the held items, and never overrides another, in its turn or
     across turns. Two trains given in one turn are two items. This reverses the in-turn override for structures. A
     handler that trains every turn piles up held trains, uncharged, and checks `issued_to` itself.
   - `NEEDS_IDLE` (add-on, morph): unqueued replaces the held items and waits until the structure is idle; queued
     goes behind.
   - `KEEPS_ORDERS`: touches nothing.
5. **A refused release drops everything held behind it** (the owner, against the recommendation to let each item
   stand alone). A train the game answers `SUCCESS` and silently never makes is no refusal, so a broke structure
   still loses its held trains one by one.
6. **NachOS lets go of a unit's whole held queue** when an item ahead is refused, the unit dies (reported or found
   dead), the unit changes hands, or a lead-in fails: the worker idle out of reach after its move, the structure idle
   and still flying after its land. The order last sent stands for what the unit is doing for `_SHOWN_WITHIN`
   observations. The items just leave `issued_to`; nothing else reports them.
7. **A held kind given to several units is held for one unit, which NachOS picks at `issue`** (the owner's
   direction, after an item shared by several units' queues was ruled out as too complicated). `order.units` is the
   unit picked. The pick counts the turn's earlier orders, uses the last observation, is never revised, and breaks
   ties by lowest id. It ranks by the steps until the unit could start the item: a structure 0 with a free slot,
   else the steps until a slot frees, from what it runs (`1 - progress` of the product's time; a morph or add-on
   reports 0 progress) and the full time of what NachOS holds for it, over its slots; a worker by straight-line travel
   steps at its speed, a busy one (holding items, or its first order a build) only if all are, then the nearest,
   behind its items. A picked order never drops held items. Larva trains and unit morphs are not held kinds, so they
   go to every unit as today.
8. **A held build goes out within 2.5 of its site** (the owner: 4 is too far; AvocaDOS uses 2.5), configurable as
   `Api(build_reach=2.5)`, fixed per api. For a site that is a unit, a geyser, from its edge.
9. **Nothing in NachOS's queue is redundant.** The owner: *"the redundant machinery is about game orders. It's
   explicitly to reduce unnecessary traffic and APM."* An override applies before the redundancy check, which then
   decides only what goes to the game. A build repeated every turn replaces the held one and restarts its lead-in.
10. **An add-on or morph given to a busy grounded structure is held too**, by the same "until idle" rule: an orbital
    as soon as the SCV being trained is done. +2 after +1 is a research behind a research.
11. **A queued order to several units, some holding items, is split**: it goes out at once to the units holding
    nothing, in one command, and to each other unit when its queue reaches it. Possible because an order has no state.

Known consequences, for the docs: held trains have no cap (the game capped a queue at 5, 8 with a reactor); a slot
that frees within `_SHOWN_WITHIN` observations of a release waits up to a turn; an add-on given to several barracks no
longer goes, by the game's pick, to one with room beside it.

### PR B, the design

- `orders/_held_queue.py`, `HeldQueue`: one unit's held items, head first, each an `(order, unit)` pair so a split
  group order sits in several queues; its lead-in record (the item it serves, the observation it was sent); what it
  released within `_SHOWN_WITHIN` observations. `_SHOWN_WITHIN` moves here.
- `orders/_starting.py`, plain functions: `site_of`, `within_reach`, `slots` (2 for a finished add-on whose
  `tech_aliases` hold `REACTOR`, 0 while flying, else 1), `travel_steps`, `product_steps`,
  `steps_until_a_slot_frees`.
- `OrderBook`: `_held: dict[int, HeldQueue]` and `_build_reach`. `issue` picks (7) and raises `TypeError` for an
  ability that `needs_placement` given no target while a unit is flying. `_competes_for` is
  `not queued and behavior in (REPLACES, NEEDS_IDLE)`. `_send`: the turn's orders through the competition; the
  overrides (4); each order joins a queue (when its unit holds items and it is queued or `QUEUES`, or when it is a held
  kind) or goes out as given; then each queue releases what can start (after a lead-in unqueued, after a `LAND` with no
  target), drops itself if its lead-in failed, or sends the head's lead-in unless `_is_doing` says the unit is on it;
  one request, the turn's orders first; a refusal drops the rest of its queue. `_observe` drops the queues of the
  dead and of units that changed hands (`units_alliance_changed`, passed by `_game.py`); a change of type drops
  nothing, so a barracks that lands keeps its add-on. A stale unit is neither started nor given up on.
- When an item can start: `QUEUES` with a free slot, counting reported and in-flight production, a `NEEDS_IDLE` order
  filling every slot (the add-on order a barracks still shows a step after); `NEEDS_IDLE` when idle and not flying; a
  build within reach, and a queued one only after its own turn with the unit idle or on its lead-in; anything else at
  once.
- Tests: `_HOLDING_TABLES` with costs, times and speeds beside the free `_TABLES`; an integration test where a held
  depot takes no minerals until its SCV is within 2.5, and a refinery to check a worker's reach of a geyser.

### Measurements it needs (`tools/sweep_orders.py`, findings into `docs/game-behavior.md`)

- **"Close enough" for a build**: measured, and anything up to the site itself will do. A worker moved to its site
  and sent the build within 8 down to 0.5 of it put the structure up 4 to 7 steps sooner than one sent the build from
  30 away; none stopped and waited (docs/game-behavior.md).

Measured since (docs/game-behavior.md): a drone and a probe are charged at the order as an SCV is; a build whose
site is taken before the worker arrives fails with `CouldntReachTarget` and is refunded then; a lifted factory and
starport take an add-on as a barracks does; and a morph or a train queued behind land is refused `NotSupported` as
given, so the game holds none of those sequences and a queue in NachOS would have to. And PR B's integration test:
an SCV sent toward a geyser's center comes within 1.2 of its edge while walking and takes the refinery there.

Still to measure: what a flying command center's load-all does to its move. It reads `REPLACES`; the landed command center's and the
planetary fortress's, which keep their training, were measured (docs/game-behavior.md).

### The timing cases (the owner, 2026-10-07)

Orders the game refuses now and would take later, settled one question at a time after #116:

1. **A bug in #116, fixed first on its own.** A lifted command center given `LAND` and a queued orbital in one turn
   had the orbital released in the same request, refused `NotSupported` (`queue-cases`), since "on the ground" was
   checked only for an add-on with a point. An add-on or a morph now waits until the structure is on the ground,
   whatever its target; a lifted command center given a morph holds it until the bot lands it.
2. **Tech stays out**, as Q1 decided: an order never waits on a requirement. +2 behind +1 on one bay is held by the
   bay's own state. To measure: how soon the game takes +2 after +1 finishes; the queue changes only if the bay's
   first idle observation is too early.
3. **Warp-ins: measure first, decide after**: the game's answer to a warp-in during the cooldown and whether anything
   is charged, the cooldown per unit type, and whether `is_active`, a buff or the available-abilities query shows a
   gate ready. A warp-in goes out as given until then.
4. **A larva morph given to a hatchery is held there until one of its larvae is free**: `issue(hatchery,
   LARVA_MORPH_DRONE)` goes to a larva of the hatchery showing no order and given none this turn. To measure first:
   how far a hatchery's larvae stand from it, and what the game answers to a larva morph given to the hatchery.
5. **Spells stay with the game**: the game walks a caster into reach itself, no money is at stake, and a hold would
   cost micro up to a turn.
6. **Warp-ins are not held** (the owner, after #118, against the recommendation to time each gate's wait): a warp-in
   goes out as given; during the gate's wait the game refuses it `NotEnoughCharges` at no cost, and the bot retries.
7. **A larva is its hatchery's whose larva spot is nearest** (the owner's idea, after #118): larvae gather south of a
   hatchery, so its center moved 2.85 south, as measured, tells two close hatcheries' larvae apart where a plain
   nearest center would not. A held morph goes to a free larva, one per larva a turn; two given in a turn are two;
   given to several hatcheries it goes to the one with the most free larvae, less what it holds.

### Pull requests

1. **PR A, an order without state**: #115, branch `claude/order-without-state`.
2. **PR B, the queue**: #116, branch `claude/per-unit-queue`. The money cases (a worker's build, alone or behind
   moves; a flying structure's add-on; a train or research behind production), the pick, and an add-on or morph on a
   busy structure. Where it settles what the design left open: a refused lead-in is listed in `api.action_failures`
   under `MOVE` or `LAND`, the ability that went out; an order whose picked unit is busy reads `queued`; each order
   released goes out as a command of its own, so a split group order's later parts keep no spacing; and a train or a
   research is held only for a structure offered `CANCEL_LAST`, or its lifted form, so a warp-in is not held (review
   of #116).
3. **The #116 fix** (timing case 1): #117, branch `claude/held-morph-on-the-ground`.
4. **The timing sweeps**: `research-after`, `warp-gate-cooldown` and `larvae` in `tools/sweep_orders.py`, findings in
   `docs/game-behavior.md`. Branch `claude/timing-sweeps`. What they found:
   - **+2 needs nothing**: a bay takes level 2 the step it is done with level 1, as the queue already gives it.
   - **A warp gate's wait shows only to the abilities query counting costs**: 448 steps after a zealot or an adept,
     512 after a stalker or a sentry, 720 after a templar; a warp-in given meanwhile is answered `NotEnoughCharges` and
     charged nothing, and the gate reads no different.
   - **A larva stands within 3.6 of its own hatchery's center**, an inject's too, and a morph given to a hatchery is
     refused `NotSupported`.
5. **The larva hold**: #120, branch `claude/larva-hold`. The `larvae` sweep measures where larvae stand from each of
   four hatcheries in two games; a larva's morph given to a hatchery, a lair or a hive is held as a train is and goes
   to a free larva by its larva spot.

## 2. One id per action: the families and the `_EXACT` ids

Settled with the owner on 2026-10-07 (see "Decided" above); done in #103, #104, #105 (named in #106) and #107,
listed under "Pull requests" below. The owner's words: *"why is there a
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
2. **The families.** #104 (branch `claude/one-id-per-action-families`): the map extended to 146 ids, the per-unit
   ids, the cancels and the general research ids gone from `AbilityId`, the generator folding at the end, products
   by performer, order behaviors judged per type, and the docs. Where it departs from the plan: `allows_autocast`
   stays a bool (above), and a type that holds no order of its own keeps it for every ability that makes nothing, so
   a missile turret or a cannon given an attack or a smart now keeps its orders, where it took the behavior of the
   game id it shared with units. A gateway's warp gate morph needed a new override, `SELF_MORPHS`.
3. **One id sent as each unit type's own** (the owner, in the review of #104, 2026-10-07). PR 3 (branch
   `claude/one-id-per-type`, #105): `AbilityData.sent_as`, from the `ABILITIES_SENT_AS_ANOTHER` override (so named
   in #106), replaces `CUSTOM_ABILITIES`; a custom row's target type and cast range are worked out from the abilities
   it is sent as, and the sieged forms are listed too, so a repeated siege is still refused by the game rather than
   by NachOS. A custom id whose `sent_as` names a game ability per unit type, a group order going out as one
   command per type and answered as #101 answers a group. With it:
   - `GENERAL_UNLOAD` and `GENERAL_UNLOAD_IN_PLACE` become one id, `GENERAL_UNLOAD`, "put everyone down here": sent as
     UnloadAll to a bunker, command center, planetary fortress or nydus, and as UnloadAllAt aimed at itself to a
     medivac, warp prism or transport overlord, which answer UnloadAll `Error` (`held-orders`). `GENERAL_UNLOAD_AT`
     stays, for a point.
   - `GENERAL_SIEGE` and `GENERAL_UNSIEGE` for the tank, the liberator, the observer and the overseer, the owner's
     choice of all four. The game has no shared id for either. The siege takes a point, which only the liberator's
     part uses, for its zone; the unsiege takes nothing. Each type's own game id reads as the custom one.
4. **The rename**: `GENERAL_` dropped everywhere, as a mechanical PR on its own so the earlier diffs stay readable.
   #107 (branch `claude/drop-general-prefix`): 39 ids renamed, `AbilityId` re-sorted by name (the two custom ids keep
   their order, so their values), the tech tree regenerated (reordered only) and the naming rule in the docs reworded.
   Earlier entries in this plan keep the names of their day.

## 3. The rest of M4

From AvocaDOS's `docs/plans/nachOS-plan.md`, section "M4 slices". Each slice is one nachOS PR; the owner chooses
when each starts.

5. **The derived reads**, done (#121). Decided by the owner, 2026-10-07:
   - **The unit sets are `UnitType` groups only**, read through `of_type`, with no second spelling on `Units` or
     `Api`: `Worker` (no MULE), `Townhall` (lifted forms too), `AnyMineralField`, `AnyVespeneGeyser` and
     `GasBuilding`. The last three are what the tables say holds minerals or vespene; `MineralField` and
     `VespeneGeyser` already name the plain types, hence `Any`.
   - **`api.map.start_location`**, beside `opponent_start_locations`, read once off the first observation's own
     townhall.
   - **`api.in_production(types)`** gives what the game has started or charged for, one `InProduction` per unit,
     with the unit it is read from and its progress, and not what NachOS holds, which is in `issued_to`; a count is
     its `len` (the owner, review of #121). A morph other than a larva's reads progress `None`, since the game reports
     0 from start to end. **`api.research_progress(upgrade)`** is from 0 to 1, or `None`.
   - **No `api.enemy.units`**: the enemy's units stay `api.units.enemy`, and the migration guide maps python-sc2's
     `enemy_units` and `enemy_structures` onto it.
   - **The expansions**, which the demo bot needs to expand: branch `claude/expansions`, chosen before slices 6 and
     7 (the owner, 2026-10-08). Decided by the owner, 2026-10-08:
     - **`api.expansions`**, found once at the start: each `Expansion` has its townhall's `location` and its
       `mineral_fields` and `geysers` as units, the same objects all game. It is on the api, not the map, so that
       `GameMap` stays a pure reading of the protos built before any unit: a map holding one game's units could not
       be shared, would make a circle if the tracker ever needs the map, and would leave tools without expansions.
     - **Where a townhall may stand is measured**, by `tools/sweep_townhall_placement.py`, not taken from
       python-sc2's distances, which put 4 bases of the pool where the game takes no townhall.
     - **A group of 5 to 12 resources is an expansion**: fewer is a blocker, more a wall.
     - **A geyser more than 10 from its group's townhall gets an expansion of its own**, sharing the fields, as on
       Torches' gold bases.
     - **They come nearest this player's start first by walking**, by a private Dijkstra over the start's pathing
       grid; no public pathfinding comes with it.
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
