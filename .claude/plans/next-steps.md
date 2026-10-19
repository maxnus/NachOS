# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` has every PR to #130. `uv run pytest` passes 2220 tests on branch `claude/more-events`;
  `uv run pytest -m integration` passes 32 against a real game (run 2026-10-19, on that branch).
- **The queue per unit (#115 to #120) is reversed** (#126, the owner, 2026-10-16): NachOS holds no order.
- **M4 is done**: slice 4, orders (#42 to #46), one id per action (#103 to #107), slice 5, the derived reads
  (#121), the expansions (#122), the placement grids (#123), slice 6, debug and chat (#127), and slice 8, the demo
  bot (#125). Slice 7, typed order methods on `OwnUnit`, is deferred (below). Next is M5, in AvocaDOS.
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
- **An order has no state, and nothing follows it once sent** (#69, then #115, 2026-10-07). A command the game
  refuses is listed in `api.action_failures` on the next turn, once for each unit it named, under the id ordered,
  ahead of what the game gave up on. The `Order` stays as a handle: its `data`, `withdraw()`, and what `issued_to`
  and `pending` list. A bot reads its units, events and `api.action_failures`, as with python-sc2. Whether an order
  ran, finished or failed was read from later observations, and the code review of 2026-09-24 found each reading
  could be wrong. The owner: *"Why do we actually need to know if the order went through successfully. Could we
  leave it to the user to check?"*, and later *"why do we need order state at all? In python-sc2 I believe issuing
  an order doesn't return anything."*
- **Every call to `issue` is an order of its own; one a unit is already carrying out is not sent to it** (#68).
  With nothing followed across turns, a repeated order costs one small object a turn. The owner first had a repeat
  hand back the order already sent, then reversed that in the review of #77, since the one object could describe
  only its first turn: *"I'm confused why the repeat says sent. I thought repeats are never send?"* Returning `None`
  was weighed and dropped, since `issued_to` and "the last order wins" could not see the call.
- **An action several types perform does to each unit what its type's own id does** (#70), and a group of several
  types is judged unit by unit. `KEEPS_ORDERS` is inferred only for types that hold no order of their own. A type's
  own id that the tables offer to no type has no say in the action's behavior, so a command center's and a medivac's
  unload keep orders as a bunker's does (review of #77, reversing "a medivac falls back to `REPLACES`").
- **No `clear_queue`; `issue(..., force=True)` instead** (review of #77, 2026-10-06). The owner asked why it was
  needed at all, and had it removed on condition that an order can still be sent though a unit is carrying it out.
  Re-sending a unit's current order that way drops its queue as `clear_queue` did.
- **An ability whose behavior depends on its target is split into custom ids, one per behavior** (review of #77,
  2026-10-06), so that a behavior stays one per ability and type; the owner's idea. Done for the unload (#101,
  #105): `UNLOAD` puts everyone down where the transport is and `UNLOAD_AT` at a point, and an unload at a point
  aimed at the transport itself raises `TypeError`. A load, three spells and a fortress's attack were swept and need
  no split (docs/game-behavior.md).
- **One public id per action** (2026-10-07, #103 to #107), the owner's choices after asking *"why is there a
  `LIBERATOR_SIEGE` and a `LIBERATOR_SIEGE_EXACT` - could we do with one? [...] is it really necessary to have a
  separate `BARRACKS_LIFT` and `STARPORT_LIFT`?"* Typed methods such as `barracks.lift()` would not make this
  unnecessary, since ids also show in `unit.orders` and `unit.abilities`.
  - A family whose members do one thing for different performers is one id, which is issued, offered and read back;
    its per-unit ids stay only in `RawAbilityId`, and translation happens on the wire. No `GENERAL_` prefix.
  - The leveled researches keep one id per level, and have no general id: a bot researches by level or by
    `UpgradeId`.
  - The cancels are one `CANCEL` (the owner, on the measurement that a unit is never offered two at once, over an
    earlier custom `CANCEL_ADD_ON`), and `CANCEL_LAST` a second id, since a structure that is training answers the
    generic cancel `Error`.
  - An id the game reports that nothing links to the one ordered reads as the one ordered: a sieging liberator's,
    a merging templar's. A repeated `MORPH_ARCHON` is sent again rather than special-cased.
  - A field that varies by performer is a mapping from unit type on every row (`row.products[unit_type]`).
  - One id can go out as each unit type's own (`AbilityData.sent_as`, from `ABILITIES_SENT_AS_ANOTHER`): `UNLOAD`,
    and the custom `SIEGE` and `UNSIEGE`, which the game has no shared id for.
- **Reversed on 2026-10-16: NachOS holds no order** (branch `claude/remove-hold`). From 2026-09-21 it held a worker's
  build and a structure's train, research, add-on and morph until the unit could start it (#115 to #120), which made the
  order book much more complicated for what it bought. The owner: a build paid on the worker's arrival can be the bot's
  own doing, or part of a more general sequence of actions over several units and conditions; that belongs in the bot,
  or in a layer above the order book, and would reverse #46 if it budgeted in NachOS. Decided with the owner, one
  question at a time:
  - Every unqueued order competes, a structure's train included, so a unit takes the last one a turn gives it; a second
    train needs `queued=True`.
  - Only what every unit of a command carries out goes to several: `issue` raises `ValueError` for an ability not in
    `MULTI_UNIT_ABILITIES` given to several units (`AbilityData.multi_unit`, from the `group-abilities` sweeps; the
    owner, review of #126, over a list of what one unit carries out), so a build, a train, a spell but a toggle, a
    landing or an ability never seen given to a group goes to one unit, which the bot picks. The rest goes out as one
    command naming them all.
  - A larva's morph given to a hatchery goes out as given, and the game refuses it `NotSupported`.
  - `api.orders.pending_cost`, first named `unpaid` (renamed in the owner's review of #126), stays under `api.orders`,
    as the cost of the turn's orders as they will go out, which only the order book knows.
  - `api.placement.reserve(type_id, *, at, margin=0, max_steps=None)`, a `PlacementReservation`, keeps a site, and a
    margin around it as `is_free` takes one (the owner, review of #126), out of the placement grids for a build not
    ordered yet, against my recommendation of no new API. It lasts until withdrawn, until `max_steps` have passed, or
    until one of this player's structures stands on its site, and is named for placement so as not to read as money.
  - A liberator's siege is `LIBERATOR_SIEGE_AT`, the game's defender mode at a point, out of the custom `SIEGE`:
    abilities that behave differently are not one id. `UNSIEGE` stays one for all four. `RECALL` is refused for any
    group, since two motherships never stand.
- **Debug, chat, the camera, pathing distances and leaving** (slice 6, the owner, 2026-10-16 and the reviews of #127,
  one question at a time):
  - `api.debug` is one `DebugCommands` per game, on `_Game` like `Camera` (the owner, review of #127, over one for the
    api's life). `refresh_steps`, `default_color` and `default_text_size` are set in a game, a `GameStartEvent` handler
    setting them for every game; there are no default steps, so that `None` has one meaning (the owner's second round:
    defaults for size and color only).
  - Shapes are values with offsets (the owner's second round, over offsets on `draw_*` methods and a group handle):
    `DebugText`, `DebugBox`, `DebugSphere`, `DebugMark` and `DebugLine(to=)`, each with `offset=` and `color=`, and
    `DebugGroup(*shapes, offset=, color=)`, itself a shape. `api.debug.draw(at, *shapes, color=, steps=)` returns one
    `Drawing`; the `draw_*` methods went but for `draw_text_on_screen`. A figure made once is drawn at any number of
    units.
  - A drawing lasts `steps=`, 1 by default, chosen over one turn and over until cleared; `steps=None` lasts until
    `drawing.remove()`. `refresh_steps` sends it at most that often, and only when it changed. A drawing at a unit, or a
    line to one, follows it, and ends once the unit is dead (the second review). Everything goes out after the turn's
    `RequestAction`, in one `RequestDebug`, and a request the game refuses raises out of `play` (the code review, over
    catching it).
  - `Marker` holds `CROSS = "x"`, `PLUS = "+"`, `STAR = "*"`, and `TRIANGLE`, `SQUARE`, `PENTAGON` and `HEXAGON`, each
    with a level base, polygons being wanted only as marks (the owner's second round, over a `Polygon` of any sides).
    Colors are a `Color` enum, or any red, green and blue.
  - Cheats are on and off, tracked (`cheats`, `turn_on`, `turn_off`), over a raw toggle. The givers are `give_minerals`,
    `give_resources` and `give_upgrades`; `gas`, which gives nothing, is left out.
  - `api.chat(text, team_only=False)` is on the api itself, over `api.orders.chat`, and goes in the turn's
    `RequestAction`. A text of 1040 bytes or more raises `ValueError`, since it kills the game client.
  - `api.leave_game()` leaves at the end of the turn, always at once, over waiting for the turn's chat to arrive: a
    goodbye goes two steps before. `play` returns `Result.DEFEAT`, since the game tells a client that left nothing
    (review of #127, over `UNDECIDED`).
  - `sc2nachos.pathing.Pathing`, as `api.pathing.query_distance` and `query_distances`, named for asking the game
    (review of #127), is a package of its own, which an up-to-date pathing grid and NachOS's own pathfinding may join.
  - `sc2nachos.camera.Camera`, as `api.camera`, holds `center` and `move_to`, in place of `api.orders.camera`; and
    `Unit.is_selected` is read, both for a bot's debug tools.
- **More events before M5** (the owner, 2026-10-18, from the Discussions #62, #65 and #67, one question at a time):
  - `OwnUnitBecameIdleEvent`: a finished unit of this player's with no orders and none sent that may not show yet,
    having had orders or such an order, been unfinished, or not been listed in the observation before. Chosen over
    reported orders alone, which reports a unit just ordered on the ladder, and over a change from busy alone.
  - `OwnWarpInStartedEvent`, and `OwnUpgradeStartedEvent` and `OwnUpgradeStoppedEvent`, both `(upgrade, unit)`, under
    a new base `UpgradeEvent` with `OwnUpgradeFinishedEvent`. Started counts a queued research; stopped is a cancel or
    the structure's death, never a finish (tool `sweep_orders`, `research-ends`).
  - `OwnChatEvent` and `EnemyChatEvent`, subclasses of `ChatEvent`: enemy is any other player but an observer, an
    ally included in a larger game, since the protocol names no teams; an observer's message is a plain `ChatEvent`.
  - `OwnUnitDiedEvent` and `EnemyUnitDiedEvent`, `OwnUnitTypeChangedEvent` and `EnemyUnitTypeChangedEvent`, the
    owner's addition, as subclasses of `UnitDiedEvent` and `UnitTypeChangedEvent`, which a neutral unit still gets.
  - An event's side is the side the unit is on at the time, which its class follows (`Unit._update_alliance`), as the
    existing pairs had it; `unit.first_alliance` says whose it was.
  - A neural parasite's takeover is handled by nothing but `UnitAllianceChangedEvent` and the class switch; the other
    events take it by their own rules, as `docs/events.md` says (the owner, 2026-10-19). As a rule, a niche case of
    one spell gets its behavior documented, never event generation made more complicated for it.
  - Not done: own and enemy as a selector over one class (Discussion #66), which would lose the `OwnUnit` typing of
    the own events.
- **Subscribing is on the api** (#128, the owner, 2026-10-18): `api.on`, `api.subscribe`, `api.unsubscribe`,
  `api.emit` and `api.handler_timings`, over `api.events.on` and the rest, so that marking a method and subscribing its
  instance sit on one object. The bus is private (`_EventBus`), and `api.events` is gone. `api.on` returns an
  `EventSubscriber`, as `where` does, public and exported from `sc2nachos.events`: one class, the type a bot sees and
  the object it gets, over a public Protocol beside a private class, and named for what it does over `EventDecorator`.
- **The docs** (the owner, 2026-10-18, one question at a time): the guides for bot authors stay in `docs/`;
  `docs/reference/` holds `python-sc2.md`, every python-sc2 name and difference, and `game-behavior/`, the catalog,
  one page per topic, with `cheats.md` its cheats topic; `docs/maintaining/` holds `curating-ids.md` and
  `code-review.md`. `migrating-from-python-sc2.md` is a short page for people: the same bot in both, the main changes
  and the commonest pitfalls. A measurement lives only in the catalog; everything else links there.
- **Typed order methods are deferred** (slice 7, the owner, 2026-10-18): `api.orders.issue` stays the one way to
  order. Of the 312 abilities offered to some type, a type is offered at most 24, and pyright's completion would offer
  every unit all of them; AvocaDOS's build orders hold ids, so it would order through `issue` anyway.
- **`issue` gives every unit its order** (the owner, 2026-10-18): one method taking one unit or many, over a second
  `issue_all` for groups. An ability every unit of a command carries out goes out as one command naming them all;
  any other as a command to each, so three barracks given a marine train three, which raised before. A research, or a
  structure placed at a point or on a geyser, given to several raises `ValueError`, since only one of them can carry
  it out: chosen over sending them as given, which the game answers by researching once and by charging every builder
  until all but one give up, and over raising for the build alone. So does a landing, a rooting or a warp-in given to
  several at one point (review of #130), which the tables do not mark: `ABILITIES_PLACING_AT_THE_TARGET`.
- **The derived reads** (#121, the owner, 2026-10-07):
  - The unit sets are `UnitType` groups, read through `of_type`, with no second spelling on `Api`: `Worker` (no
    MULE), `Townhall` (lifted forms too), `AnyMineralField`, `AnyVespeneGeyser` and `GasBuilding`, the last three
    what the tables say holds minerals or vespene. `Units` reads them as `workers`, `townhalls`, `mineral_fields`,
    `geysers` and `gas_buildings`, beside `structures` (reversed for `Units` in review of #125).
  - `api.map.start_location`, beside `opponent_start_locations`, is read once off the first observation's own
    townhall.
  - `api.in_production(types)` gives what the game has started or charged for, one `InProduction` per unit with its
    progress, and not the turn's orders, which are in `issued_to`; a count is its `len`. A morph other than a larva's
    reads progress `None`, since the game reports 0 from start to end. `api.research_progress(upgrade)` is from 0 to
    1, or `None`.
  - No `api.enemy.units`: the enemy's units stay `api.units.enemy`.
- **The expansions** (#122, the owner, 2026-10-08 and review of #122):
  - `api.expansions` is found once at the start, on the api and not the map, so that `GameMap` stays a pure reading of
    the protos. Each `Expansion` has its townhall's `center` and its `mineral_fields` and `geysers`, the same objects
    all game, and its `townhall`, anyone's (review of #125, over an `OwnedExpansion`).
  - Where a townhall may stand is measured (`tools/sweep_townhall_placement.py`), not taken from python-sc2's
    distances, and is built as a zone around each resource that no townhall's footprint may cover.
  - A group of 6 to 10 mineral fields is an expansion: fewer are a blocker, more a wall. A geyser more than 11 from its
    group's townhall gets an expansion of its own, sharing the fields, as on Torches' gold bases.
  - They come nearest this player's start first by pathing distance, from the public `Grid.path_distance_from`, which
    gives distances, never a path ("pathing distance", never "walking distance"). Each says `is_start_location` and
    `pathing_distance_from_start`; ranking by the opponent's distance too is the bot's judgment.
  - They are found by the private classmethod `Expansion._find_all(game_map, neutral_units)`, so that `GameMap`
    knows nothing of units and `api.expansions` stays the one public spelling.
  - Blockers are every neutral unit but a resource, and there is no `NeutralUnit` class.
- **The placement grids** (#123, the owner, 2026-10-13 and its reviews):
  - `api.placement` is one `sc2nachos.placement.PlacementGrids` for the game, in a package of its own rather than in
    `state`, moved on to each observation, with three layers. `static` is the ground the map lets a structure stand on,
    less its geysers and its collapsible towers, which stay where they fall. `dynamic` is true where no structure, in
    sight or remembered, and no mineral field stands. `reserved` is this player's builds ordered and not standing yet,
    and what `api.placement.reserve` keeps: what is blocked and what is reserved answer two questions (the owner,
    against one grid holding both).
  - `free` combines the three; `free_for(unit_type)` keeps to creep or off it by `UnitTypeData.creep_requirement`, and
    a townhall off the ground around resources. For one structure, `is_free(t, at=p)` and `nearest_free(t, near=p,
    max_distance=d)`, with a `margin` of tiles kept free around the footprint (review of #125). `api.map.placement`
    stays the game's grid from the start.
  - Structures only: a unit that moves blocks nothing, nor one in the air.
  - Every structure's footprint is measured, by `tools/sweep_placement.py`, into `api.data.units[t].footprint`. A
    neutral type the table lacks falls back to the square its radius spans, and a force field covers nothing.
  - `Footprint` is a value type in `sc2nachos.geometry`, made by `square`, `rectangle` or `from_rows`; a grid says
    where one fits (`Grid.fits_footprint`, `where_footprint_fits`, `fill_footprint`). The generated table makes each
    shape once, as a constant, rather than an enum, whose members would not be footprints.
  - On UltraloveAIE_v2 each unbuildable plate keeps a structure off 7 tiles more than its 2x2, which `dynamic` leaves
    open, and nothing the API reports sets those plates apart: recorded under "Not understood yet" and left.
- **The demo bot** (#125, the owner, 2026-10-15), M4's exit gate and the library's example for other developers:
  - It beats `Computer` at `MEDIUM` in all 21 games of the pool's 7 maps against each of the 3 races.
  - It plays Terran, placing through `api.placement`, with no power, which `free_for` does not know yet.
  - It is one file, `examples/demo_bot.py`, run by `run_from_command_line`, so the match workflow can play it.
    `examples/run_game.py` stays the bot that does nothing.
  - What it needs that NachOS lacks is asked of the owner one gap at a time, each with a proposed API, and goes into
    NachOS as its own commit in the same PR before the bot uses it; a big one becomes a PR of its own first.
  - The critters are curated (`CRITTER_CLEANING_BOT`, `CRITTER_LAB_BOT`, in `UnitType.Critter`), since a build over
    one waits until it has walked off; they cover no ground.
  - An order aimed by a tag the game never reports reads as aimed at the unit the unit's last order of the same
    ability named, dead since or not, and its `target` raises `NotReportedError` if there was none; a rally aimed so
    reads its point. An SCV walking back to a field in the fog shows such a tag.
  - From the review of #125: `Unit.distance_to` is center to center; `Units.within(d, of=...)`; `in_area` takes
    several areas; the main nouns import from `sc2nachos` itself, the events staying in `sc2nachos.events`. Not done:
    a callable target per unit (reconsider with slice 7), `issue` with no units (it keeps raising), and `UnitOrder`'s
    `_target` field and its `_NotReported` marker stay.
- **The unit type groups** (review of #125, the owner): a group for each structure and unit with a second form
  (`AnySupplyDepot`, `AnyRoach` burrowed or not, `AnyOrbitalCommand`, `AnyCommandCenter` the plain one only...);
  `AnySpire` and `AnyOverlord`, overseers included, as the tables count them, but no `AnyHatchery` or `AnyLair`; the
  map's `Destructible`, with the collapsible towers and their debris, though a tower falls and stays (over my proposal
  of the debris alone), without `ROCKS_2X2_NON_CONJOINED`; `InhibitorZone`, `AccelerationZone`, `AnyTerranTownhall`
  and `AnyZergTownhall`. Each group is in every group that holds all its types, so `AnyDrone` is a `Worker` and
  `CollapsibleTower` a `Structure`.

## 1. Waiting until something needs them

- Typed order methods on `OwnUnit`, M4's slice 7, deferred (above).
- Power in the placement grids, in a pull request of its own that measures what the game requires; `needs_power`
  stays a bool until then.
- An up-to-date pathing grid.
- Autocast switches sent by NachOS, which it only reads now (`AutocastToggle`). `allows_autocast` is a bool, though
  an unburrow's autocast is the performer's: a burrowed roach comes up by autocast, a drone does not. A family id
  would then have to go out as a performer's own id, which needs a link the game's rows do not give (raised with the
  owner in #104, not decided).

## 2. After each nachOS pull request merges

Record it in AvocaDOS's `docs/plans/nachOS-plan.md` on `main`: a paragraph under "M4 status" saying what it
settled and what AvocaDOS writes differently at M5. That needs an AvocaDOS checkout; its tests (`uv run pytest`,
`tests/test_nachos_parity.py` in particular) install nachOS from source. Commit there, and push only when the owner
says so.

## 3. Next, M5 (AvocaDOS runs on NachOS)

In AvocaDOS's plan, section "M5 — The swap". Already known:

- AvocaDOS's own `api.event.on` becomes NachOS's `api.on`, and `api.event.listener`, which subscribes each instance
  of a class as it is made, an `api.subscribe(self)` in the class's `__init__`. Its `api.order` becomes `api.orders`;
  its `OrderManager` keeps the *highest*-priority order where NachOS keeps the *last*, and `has_order(unit)` is
  `api.orders.issued_to(unit)`. `OrderManager._order`, which splits a `Units` into one order per unit, gives a group
  one `issue`, but for a research or a build at a target, which go to one unit.
- `phase=` becomes `priority=`, with the handlers that forget the dead at `HIGHEST`.
- The bot's `MEDIVACINCREASESPEEDBOOST`, `DUTCHMARAUDERSLOW` and `IMMORTALOVERLOAD` take their curated names;
  AvocaDOS's `tests/test_nachos_id_parity.py` fails on those three until then, as expected.
