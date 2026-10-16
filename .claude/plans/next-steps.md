# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` has every PR to #125. `uv run pytest` passes 2066 tests on branch `claude/remove-hold`; `uv run
  pytest -m integration` passes 26 against a real game (run 2026-10-16).
- **Step 1, a queue per unit, was done and is reversed** on branch `claude/remove-hold` (the owner, 2026-10-16): NachOS
  holds no order. Step 2, one id per action, is done (#103 to #107). Step 3, the rest of M4: slice 5, the derived
  reads (#121), the expansions (#122), the placement grids (#123) and slice 8, the demo bot (#125), are done; slices
  6 and 7 are left.
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
- **Reversed on 2026-10-16: NachOS holds no order** (step 1 below). It sends every order in the turn it was given,
  as it did until the queue per unit of 2026-09-21 (#115 to #120), which made the order book much more complicated
  for what it bought. The owner: a build paid on the worker's arrival can be the bot's own doing, or part of a more
  general sequence of actions over several units and conditions; that belongs in the bot, or in a layer above the
  order book, and would reverse #46 if it budgeted in NachOS.

## 1. A queue per unit, kept in NachOS: done, then reversed

From 2026-09-21 NachOS held a worker's build, a structure's train, research, add-on and morph, and a larva's morph
given to a hatchery, until the unit could start it, so nothing was paid early: #115 (an order without state), #116
(the queue), #117, #118 (the timing sweeps) and #120 (the larva hold). The design and its decisions are in those PRs
and in this file's history. What the game was seen to do while it was built stays in `docs/game-behavior.md`, under
"Abilities and orders" and "Units coming, changing and going".

**Reversed on branch `claude/remove-hold`**, decided with the owner one question at a time (2026-10-16):

1. **Every unqueued order competes again**, so a unit, a structure included, takes the last one a turn gives it, as
   before #116. A second train needs `queued=True`, as a reactor's does.
2. **An order given to several units goes out as one command naming them all**, and the game has one carry out a
   build, an add-on, a train, a research or a morph (in game). NachOS picks no unit.
3. **A larva's morph given to a hatchery goes out as given**, and the game refuses it `NotSupported`.
4. **`api.orders.unpaid` stays where it is**, as the cost of the turn's orders as they will go out, each once: only
   the order book knows what of the turn goes out.
5. **`api.placement.reserve(type_id, *, at, max_steps=None)`**, a `PlacementReservation`, keeps a site out of the
   placement grids for a build not ordered yet, against my recommendation of no new API. It lasts until withdrawn,
   until `max_steps` have passed, or until one of this player's structures stands on its site, and is named for
   placement so as not to read as money (the owner, refining it twice).
6. **The demo bot pays up front**: an SCV is given its build at once, and the game charges it then. It beat the
   computer at Medium in all 21 games of the pool again.
7. **An order only one unit of a command carries out names one unit** (the owner, after the code review of #126):
   `issue` raises `ValueError` for one given to several, a build, a train, a research, a morph that costs, and
   every spell but a toggle, as the new `group-casts` sweeps measured (`AbilityData.carried_out_by_one`,
   `CARRIED_OUT_BY_ONE_ABILITIES`). This keeps every order exact per unit, and a bot picks the caster or worker.

Kept from the queue's time: an order without state (#115), `issued_to(include_sent=True)`, the `TypeError` for a
flying structure's add-on given no point, and the timing sweeps in `tools/sweep_orders.py`.

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
   - **The unit sets are `UnitType` groups**, read through `of_type`, with no second spelling on `Api`: `Worker` (no
     MULE), `Townhall` (lifted forms too), `AnyMineralField`, `AnyVespeneGeyser` and `GasBuilding`. `Units` reads them
     as `workers`, `townhalls`, `mineral_fields`, `geysers` and `gas_buildings`, beside `structures` (reversed for
     `Units` in review of #125, the owner). The last three are what the tables say holds minerals or vespene; `MineralField` and
     `VespeneGeyser` already name the plain types, hence `Any`.
   - **`api.map.start_location`**, beside `opponent_start_locations`, read once off the first observation's own
     townhall.
   - **`api.in_production(types)`** gives what the game has started or charged for, one `InProduction` per unit,
     with the unit it is read from and its progress, and not the turn's orders, which are in `issued_to`; a count is
     its `len` (the owner, review of #121). A morph other than a larva's reads progress `None`, since the game reports
     0 from start to end. **`api.research_progress(upgrade)`** is from 0 to 1, or `None`.
   - **No `api.enemy.units`**: the enemy's units stay `api.units.enemy`, and the migration guide maps python-sc2's
     `enemy_units` and `enemy_structures` onto it.
6. **Debug and chat**: typed debug commands, drawing, sending chat, `query_pathing` and leaving a game.
7. **Typed order methods on `OwnUnit`**, or the decision to defer them.
8. **The demo bot**, M4's exit gate: a small bot in this repo (build workers, expand, attack) that plays a full game
   against `Computer` using only NachOS, and doubles as the library's example for other developers. Decided by the
   owner, 2026-10-15:
   - **It beats `Computer` at `MEDIUM` in all 21 games** of the pool's 7 maps against each of the 3 races.
   - **It plays Terran**: depots, barracks and command centers placed through `api.placement`, and no power, which
     `free_for` does not know yet.
   - **It is one file**, `examples/demo_bot.py`, run by `run_from_command_line`, so the match workflow can play it.
     `examples/run_game.py` stays the bot that does nothing.
   - **What it needs that NachOS lacks is asked of the owner one gap at a time**, each with a proposed API, and goes
     into NachOS as its own commit in the same PR before the bot uses it; a big one becomes a PR of its own first.
   - **The critters are curated** (`CRITTER_CLEANING_BOT`, `CRITTER_LAB_BOT`, in `UnitType.Critter`), since a build
     over one waits until it has walked off (the owner, who asked for that to be measured with a command center); they
     cover no ground. Seeing one raised `UncuratedIdError`.
   - **`api.orders.held`**, every order NachOS holds, beside `pending`, less what the turn's orders will drop, and
     **`api.orders.unpaid`**, the `Cost` of both, so a bot can budget for what the bank does not show yet (the owner:
     `held` first, `unpaid` in review of #125). `held` went with the hold, and `unpaid` is the turn's alone (step 1).
   - **An order aimed by a tag the game never reports reads as aimed at the unit the unit's last order of the same
     ability named**, dead since or not, and its `target` raises `NotReportedError` if there was none; a rally aimed
     so reads its point. An SCV walking back to a field in the fog shows such a tag, and reading `orders` crashed 9
     of 84 games against the harder computers (the owner, after asking whether NachOS filtered the tag out and
     whether the reading could be wrong).
   - **Decided in review of #125** (the owner): a group for each structure and unit with a second form, 26 of them
     (`AnySupplyDepot`, `AnyBarracks`, `AnySiegeTank`, `AnyViking`, `AnyRoach` burrowed or not...); `margin`, one
     count of tiles or four (left, right, bottom, top), on `Grid.fits_footprint` and `where_footprint_fits`, kept
     free around the tiles the footprint covers, so a cut corner stays cut, and passed on by `is_free` and
     `nearest_free`; `Expansion.townhall`, the townhall
     standing on it, anyone's, over an `OwnedExpansion`; `Unit.distance_to`, center to center; `Units.within(d,
     of=...)`; `in_area` taking several areas; the main nouns importable from `sc2nachos` itself, the events staying
     in `sc2nachos.events`. Not done: a callable target per unit (reconsider with slice 7), `issue` with no units
     (it keeps raising), and `UnitOrder`'s `_target` field and its `_NotReported` marker stay. Later in the same
     review: the groups `AnyChangeling`, `AnyCreepTumor` and `CollapsibleTowerDebris`, and each group in every race
     and structure group all its types are in, so `CollapsibleTower` is a `Structure` and `AnySupplyDepot` a
     `TerranStructure`. Then, asked for `AnyOrbitalCommand` and any more (2026-10-16): the two forms
     `AnyOrbitalCommand`, `AnyCommandCenter` (the plain one only), `AnyGateway` and `AnyLocust`; `AnySpire` and
     `AnyOverlord`, overseers included, as the tables count them, but no `AnyHatchery` or `AnyLair`; the map's
     `Destructible`, with the collapsible towers and their debris, `InhibitorZone` and `AccelerationZone`, without
     `ROCKS_2X2_NON_CONJOINED`; `AnyTerranTownhall` and `AnyZergTownhall`; and each group in every group that holds
     all its types, so `AnyDrone` is a `Worker`. A tower is `Destructible` though it falls and stays (the owner,
     over my proposal of the debris alone).

### Before 8: the expansions, done (#122)

The demo bot needs to know where to expand, so the expansions come first: branch `claude/expansions`, chosen before
slices 6 and 7 (the owner, 2026-10-08). Decided by the owner, 2026-10-08:

- **`api.expansions`**, found once at the start: each `Expansion` has its townhall's `center` and its
  `mineral_fields` and `geysers` as units, the same objects all game. It is on the api, not the map, so that
  `GameMap` stays a pure reading of the protos built before any unit: a map holding one game's units could not be
  shared, would make a circle if the tracker ever needs the map, and would leave tools without expansions.
- **Where a townhall may stand is measured**, by `tools/sweep_townhall_placement.py`, not taken from python-sc2's
  distances, which put 4 bases of the pool where the game takes no townhall. The rule is stated, and built, as a
  zone around each resource that no townhall's footprint may cover (the owner, review of #122).
- **A group of resources with 6 to 10 mineral fields is an expansion**: fewer fields are a blocker, more a wall.
  Tightened from "5 to 12 resources" in review of #122, as the corpus's bases have 6 to 10 fields (the owner).
- **A geyser more than 11 from its group's townhall gets an expansion of its own**, sharing the fields, as on
  Torches' gold bases. A base's own geysers stand up to 9.22 from its townhall, the far-side one 13.0.
- **They come nearest this player's start first by pathing distance**, by `Grid.path_distance_from` over the start's
  pathing grid. Reversed in review of #122: that method is public, beside `Grid.distance_from`, since nothing in it
  is ground-specific (the owner); it gives distances, never a path. "Pathing distance", never "walking distance"
  (the owner).
- **Each expansion says `is_start_location` and `pathing_distance_from_start`** (the owner, review of #122). The
  order stays by this player's pathing distance: ranking by own distance minus the opponent's is a judgment for the
  bot, and with more than two starts the opponent's is unknown. Its townhall's spot is `center` (the owner).
- **They are found by `Expansion._find_all(game_map, neutral_units)`**, a private classmethod beside the steps in
  _expansion.py, so `GameMap` knows nothing of units and `api.expansions` stays the one public spelling (the owner,
  review of #122). Where a townhall may stand is `_ExpansionLocations`, built once from the placement grid, the
  resources and the other neutral units (the owner).
- **Blockers stay every neutral unit but a resource** (the owner, review of #122): critters are never in a main base
  at the start, the only place one could be seen then.
- **No `NeutralUnit` class** (the owner, review of #122): neutral units have nothing the game reports for them alone
  but `mineral_contents`, which `Unit` has.

### Before 8: the placement grids

The demo bot will place depots and pylons, so it needs to know where a structure can stand now, not at the start:
branch `claude/placement-grids`. Decided by the owner, 2026-10-13, one question at a time:

- **`api.placement` is one object with three layers** (reworked in review of #123, the owner), a
  `sc2nachos.placement.PlacementGrids`, in a package of its own rather than in `state` (the owner, second review):
  one object for the game, moved on to each observation. `static` is what
  cannot change during a game: the ground the map lets a structure stand on, less its geysers and its collapsible
  towers, which stay where they fall (`UnitType.CollapsibleTower`). `dynamic` holds only what can go: true where no
  structure, in sight or remembered in the fog, and no mineral field stands. `reserved` is only the ground under this
  player's builds ordered and not standing yet, issued this turn or on their way, and, since step 1's reversal, what
  `api.placement.reserve` keeps: "what is blocked" and "what is reserved" answer two questions (the owner, against
  one grid holding both). `free` combines the three, and
  `free_for(unit_type)` keeps to creep or off it by `UnitTypeData.creep_requirement` (`CreepRequirement`), measured
  by the sweep for every structure type a unit places at a point, a raven's auto turret included (the owner: both),
  and a townhall off the ground around resources, which the expansions share (the owner, third review). For one
  structure, `is_free(t, at=p)` and `nearest_free(t, near=p, max_distance=d)` (the owner, third review).
  Power waits for a pull request of its own, which measures what the game requires; `needs_power` stays a bool (the
  owner, second review). `api.map.placement` stays the game's grid from the start.
- **Structures only**: a unit that moves blocks nothing, nor one in the air.
- **Every structure's footprint is measured**, the players' with their morphed forms and every curated neutral type,
  by `tools/sweep_placement.py`, into a table read as `api.data.units[t].footprint`. A neutral type the table lacks
  falls back to the square its radius spans; only the acceleration zones and the flying inhibitor zones are left for
  it, which the game would not create to be measured. A force field, a neutral unit of a protoss type, covers nothing
  (the owner, review of #123).
- **`Footprint` is a value type in `sc2nachos.geometry`**: a mask, made by `square` and `rectangle` (corners cut or
  not) or drawn `from_rows`, with its `bounding_box`, `snapped_center`, `center_in_tile` and `tiles`. A grid says where
  one fits: `Grid.fits_footprint`, `Grid.where_footprint_fits`, a binary erosion, and `Grid.fill_footprint` (the
  owner, reviews of #123). The generated table makes each shape once, as a constant, so types of one shape share one
  instance (the owner: constants rather than an enum, whose members would not be footprints). The expansions block their other neutral units by it, which leaves no disagreement with the
  game around them.
- **No up-to-date pathing grid yet**: it waits until something needs it.

Measured on the way: the start's placement grid reads the ground beneath every unit and blocks none, rocks included, so
`static` opens nothing; a collapsible tower, attacked or killed by a debug command, falls and stays. On UltraloveAIE_v2
each unbuildable plate keeps a structure off 7 tiles more than its 2x2, which `dynamic` leaves open: the one
disagreement with the game in 127,020 tiles over the corpus. Not the type, the spot or the facing; nothing the API
reports sets those two plates apart, so it is recorded under "Not understood yet" and left (the owner).

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
