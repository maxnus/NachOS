# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` has every PR to #180. `uv run pytest` passes 2976 tests with #182; `uv run pytest -m integration`
  passed 41 against a real game with #177 (run 2026-10-25).
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
- **A unit type names what makes it, not what the tables alias it to** (#75; the owner, 2026-09-26 and 2026-10-19).
  `UnitTypeData.producers` holds every type that makes one, from the tech tree, with the archon's templar written by
  hand, since the sweep never saw a templar offered the merge. `base_type`, the game's `unit_alias`, is gone: the
  tables alias an adept's shade to the adept. `morphed_from` gives a form's source, and the sweeps and tests read
  `unit_alias` from the game's answer. The off half of a toggle has its on half's performers (review of #138).
- **The tech tree splits into what each data class reads** (the owner, review of #138, and 2026-10-20, one question
  at a time): `TechTree(base_build, unit_types, abilities, upgrades)`, of `TechTreeUnitTypes`, `TechTreeAbilities` and
  `TechTreeUpgrades`, each holding the mappings its data class reads under shorter names
  (`tech_tree.abilities.products`), and each data class's reader taking only its own. `tools/generate_tech_tree.py`
  works out the performers, the producers and the research abilities and writes them into the module, so that the
  records are plain, over the tree working them out at import.
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
    a new base `AnyUpgradeEvent` with `OwnUpgradeFinishedEvent`. Started counts a queued research; stopped is a cancel
    or the structure's death, never a finish (tool `sweep_orders`, `research-ends`).
  - `OwnChatEvent` and `OpponentChatEvent` (review of #131: "opponent" for the other player, as
    `opponent_start_locations` has it, "enemy" for units): the opponent is any other player but one the game's info
    lists as an observer, an ally included in a larger game, since the protocol names no teams.
  - `OwnUnitDiedEvent` and `EnemyUnitDiedEvent`, `OwnUnitTypeChangedEvent` and `EnemyUnitTypeChangedEvent`, the
    owner's addition, and `NeutralUnitDiedEvent` for the map's units, whose deaths matter to a bot: a mineral field
    gone, a rock broken.
  - Every base is an `Any…` event, which NachOS never hands out itself (review of #131): `AnyUnitEvent`,
    `AnyBuffEvent`, `AnyVitalEvent`, `AnyAreaEvent`, `AnyUpgradeEvent`, `AnyUnitTypeChangedEvent`, `AnyUnitDiedEvent`
    and `AnyChatEvent`. Chosen over handing out a base for the units and players neither side's kind takes, since a
    bot wants one side and can subscribe to both: an ally's unit and an observer's message get no event.
  - `api.researches` (reviews of #131): a mapping from each upgrade running or queued to its `Research` record, with
    `unit`, the table's time as `total_steps`, `steps_left`, and `progress` worked out from the two, chosen over a
    tuple of records and over a mapping to the structure alone. It replaces `api.research_progress`, which answered
    one upgrade. The steps are whole (the owner): the tables' research times are, and the game's progress rounds to
    the steps left exactly (tool `sweep_orders`, `research-ends`).
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
    reads progress `None`, since the game reports 0 from start to end. `api.research_progress(upgrade)`, from 0 to
    1 or `None`, gave way to `api.researches` in #131.
  - No `api.enemy.units`: the enemy's units stay `api.units.enemy`.
- **The expansions** (#122, the owner, 2026-10-08 and review of #122):
  - `api.expansions` is found once at the start, on the api and not the map, so that `GameMap` stays a pure reading of
    the protos. Each `Expansion` has its townhall's `center` and its `mineral_fields` and `geysers`, the same objects
    all game, and its `townhall`, anyone's (review of #125, over an `OwnedExpansion`).
  - Where a townhall may stand is measured (`tools/sweep_townhall_placement.py`), not taken from python-sc2's
    distances, and is built as a zone around each resource that no townhall's footprint may cover.
  - A group of 6 to 10 mineral fields is an expansion: fewer are a blocker, more a wall. A geyser more than 11 from its
    group's townhall gets an expansion of its own, sharing the fields, as on Torches' gold bases.
  - They come nearest this player's start first by pathing distance, from the distances of the public
    `Grid.cheapest_paths_from` ("pathing distance", never "walking distance"). Each says `is_start_location` and
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
- **A tile is a frozen dataclass, not a tuple** (the owner, 2026-10-19, after #2 removed the round trip that made a
  tuple's hashing matter). As a tuple, a tile passed pyright as a point, equalled and hashed like its corner point,
  indexed numpy as an array index and needed its tuple behavior patched out. `@dataclass(frozen=True, slots=True,
  order=True, init=False)` with its own `__init__` (the owner's idea): `operator.index` makes a numpy integer an
  int, and a float raises. No arithmetic methods: Python's own `TypeError` stands. The generated equality and hash
  are kept; a bot's own `set[Tile]` pays for them, a 10,000-tile flood fill taking 10.8 ms against 7.1 with tuple
  tiles, and keys on `(tile.x, tile.y)` where that matters. `TileSet` holds `(x, y)` pairs, so its own lookups stay
  a tuple's, but its constructor takes tiles: the pairs stay private to the library (review of #137).
  `Tile.shifted(dx, dy)` moves a tile by whole tiles, refusing a fraction, where `translated` moves its center by
  any offset. This supersedes the note of 2026-09-07 in AvocaDOS's plan, "A tile is a tuple for hashing".
- **No runtime check that a point is a point** (the owner, review of #137): `coordinates()` went, and a method that
  takes a point reads its coordinates. pyright catches a tile or a unit given as one, and anything else gets
  Python's own error. What must refuse a non-point to work at all, `in`, a grid's index and `Point`'s arithmetic,
  raises `not_a_point_error`.
- **The players** (the owner, 2026-10-20, one question at a time):
  - `api.player` is a `Player` record of this player, with `id`, `race`, `requested_race` and `name`, over a lone
    `api.race`.
  - `api.enemy` gains `id`, `requested_race`, `name` and `race`. Its `race` reads `None` until known, never `RANDOM`:
    a game of two gives it from the start (tool `sweep_requests`, `players`), and a Random computer's is its first
    unit's.
  - Reading `api.enemy` raises `NoEnemyError` in a game with no other player, such as a mini-game (the owner, review of
    #143, so that no bot has to check for one), over `Enemy | None` and over optional fields; the game itself holds
    no enemy there.
  - `EnemyRaceKnownEvent(race)` comes once a game for any race, on the first turn where it is known from the start.
  - A name is `str | None`, though no game seen names a player, over a fallback string and over no name at all.
  - A first unit held by a neural parasite reads as the enemy's race; this is documented, not handled.
- **Ladder running** (the owner, 2026-10-20, one question at a time):
  - one `run_from_command_line` plays a ladder's game when the ladder passes `--GamePort`, and a local one against the
    computer otherwise, over a second function and over leaving the ladder's command line to the bot;
  - the ladder's `--OpponentId` is `api.enemy.ladder_id: str | None`, over an `api.ladder` record and over leaving it
    to the bot;
  - an argument NachOS does not read is left for the bot and listed in the log, over refusing it and over handing out
    the parser;
  - a replay is saved however the game ends, after an error too, a failing save logged. The game gives none to a
    client that has left (tool `sweep_requests`, `replay`), so `Api.play` saves it before leaving, over the runners
    saving after it returns, which would lose a game the bot left, and over `play` no longer leaving.
  - The command line's names follow the ladder's (review of #146): `--RealTime` for both kinds of game; a local game's
    computer `--ComputerRace`, `--ComputerDifficulty` and a `--ComputerBuild` of our own, checked by hand in a local
    game alone, since what the ladder passes in them was not checked; and `--Map`, `--TimeLimit`, `--ResultFile` and
    `--ReplayFile`. Chosen over the match workflow's kebab-case names and over a `nachos` prefix: a bot's parser can
    read the same argument as NachOS's.
  - `save_replay_to`, over `replay_to` and `save_replay_as`; the save stays in `api.py`, over a method of `Client`.
  - A ladder game whose connection the ladder closes ends `Result.UNDECIDED` with a warning in `run_from_command_line`,
    as AvocaDOS's ladder `run.py` had it; `run_ladder` and a local game still raise `ConnectionClosedError`. A save
    after an interrupted request reads that request's answer and is logged as failed, which stays (my review of #146).
  - The runners take the api with `race` and `name` keywords, and `ApiBot` is gone (review of #146, #147):
    `run_local("PylonAIE_v4", api, Computer(Race.ZERG), race=Race.TERRAN)`. They stay functions, over methods of
    `Api`, which would have `api.py` import `run.py`, which imports it, or move launching and argparse into the class
    a bot's handlers talk to every turn.
  - `docs/ladder.md` shows a bot's own arguments read into a settings dataclass of its own, at module level beside its
    api, with `parse_known_args(namespace=settings)`, and its handlers plain functions that read it (the owner, review
    of #147). Chosen over an object built from the settings that subscribes its handlers, which makes each a method,
    over handlers defined in the main block, which does not scale to many settings, and over a function that builds
    the api, which keeps it from the modules that register handlers. Subclassing `Api` stays out.
- **The code style has a doc of its own**, `docs/maintaining/code-style.md` (the owner, review of #143): AGENTS.md's
  "Conventions" moved there but for "Where a finding goes", with the naming rules of the reviews, and AGENTS.md points
  to it, chosen over a line in AGENTS.md and over the review checklist. The docstring contract moved there too, beside
  the rule on which functions have one (the owner, review of #144). A classmethod that turns a value of another kind
  into an instance is named `from_` that value, and `of` names what selects part of a whole or reads something of a
  value.
- **The corpus is played on the repo's maps** (the owner, 2026-10-20): it was recorded again once the old one turned
  out to hold the tables of older map copies, and `tools/sweep_requests.py tables` finds no difference from a game
  today. The zone groups follow the tables: an acceleration zone and a flying inhibitor zone are no structure, the
  ground inhibitor zones are, chosen over keeping both groups structures by hand.
- **The unit type groups** (review of #125, the owner): a group for each structure and unit with a second form
  (`AnySupplyDepot`, `AnyRoach` burrowed or not, `AnyOrbitalCommand`, `AnyCommandCenter` the plain one only...);
  `AnySpire` and `AnyOverlord`, overseers included, as the tables count them, but no `AnyHatchery` or `AnyLair`; the
  map's `Destructible`, with the collapsible towers and their debris, though a tower falls and stays (over my proposal
  of the debris alone), without `ROCKS_2X2_NON_CONJOINED`; `InhibitorZone`, `AccelerationZone`, `AnyTerranTownhall`
  and `AnyZergTownhall`. Each group is in every group that holds all its types, so `AnyDrone` is a `Worker` and
  `CollapsibleTower` a `Structure`.
- **Paths on a grid** (the owner, 2026-10-21, one question at a time), on semita
  ([github.com/maxnus/semita](https://github.com/maxnus/semita)), the C package AvocaDOS's `external/pathfinding`
  became, a package of its own so that NachOS ships no compiled code (the owner):
  - `Grid.cheapest_path(start, end) -> TilePath | None` and `Grid.cheapest_paths_from(start) -> CheapestPaths`, with
    read-only `costs` and `distances` grids and `path_to(end)`, over a one-path method alone and over keeping
    `path_distance_from` beside them. A grid's values are its tiles' costs, a grid of bools as before.
  - Named as in semita, whose `find_path` became `cheapest_path` in 0.2.0 for the pair (the owner's idea), over
    `path_between` and `paths_from`, and over AvocaDOS's plan's `Grid.path_to(start, end)`.
  - `TilePath` stays, with no cost, over a `cost: float | None` and over retiring it for `semita.Path`, whose rows are
    the searched grid's indices, with no `Tile`s; a path's cost is `cheapest_paths_from(a).costs[b]`.
  - `api.map.pathing` is no grid to route on, and stays as the game sends it (the owner: it may block fields or a
    townhall that are gone), over opening this player's townhall in it. It misses every later structure and the
    enemy's fields, and reads differently at the two bases; it stays for the map's shape at the start.
  - The grid to route on is the game's, asked lazily (after #150's sweep): `api.pathing.grid` asks for
    `RequestGameInfo` the first time it is read in a turn and hands out that grid for the rest of the turn, so a turn
    that never reads it costs nothing. It is exact for what is in sight and needs no tables, and forgets structures
    out of sight, which the docs say; chosen over NachOS laying walking footprints on the terrain, and over both.
  - `api.pathing.cheapest_path(start, end, *, leave_structure=True)` and `cheapest_paths_from(start, *,
    leave_structure=True)` search the turn's grid, and a path from inside a structure leaves it, its footprint opened
    on a copy, as the game's query does (the owner's idea of a boolean keyword, named by the owner over
    `from_inside_structure` and `open_start_structure`). `Grid`'s methods stay plain: a grid knows no structures, and
    opening the blocked tiles joined to the start can open a cliff.
  - The structure under a point or a tile is the public `Units.structure_at(point)` (the owner, review of #151, over
    `api.placement.structure_at` and keeping it in `Pathing`): the structure on the ground whose footprint holds the
    point, one in sight before a snapshot over the same ground. Whether a unit stands on the ground is the public
    property `Unit.is_ground_structure`, the owner's name: a lifted structure does not, and a snapshot does, since the
    game does not report whether it flies. Every `api.pathing` method takes a tile where it takes a point.
- **Combat conveniences on a unit** (the owner, 2026-10-21, one question at a time, M5's gap 4):
  - `unit.weapon_against(target) -> Weapon | None`, the weapon that can hit `target` by whether it flies, air weapons
    hitting a colossus too; `unit.has_in_range(target, *, margin=0.0)`; `unit.damage_against(target)`, one attack's
    damage after the bonus, armor and shields, hits on shields first; `unit.dps_against(target)`; and `Weapon.dps`,
    damage x attacks per real second. Weapon ready stays `weapon_cooldown_steps == 0` (reversed in the review of
    AvocaDOS #52: `OwnUnit.is_weapon_ready`, below). Chosen over python-sc2's
    per-domain set and over leaving damage out; named over `reaches`/`_on` and over asking the target.
  - The attackers the tables give no weapon, the battlecruiser, oracle, void ray and sentry, get the weapons
    `tools/sweep_combat.py` measured, written into NachOS's generated data and marked as measured, over leaving them
    weaponless; the carrier stays weaponless, its interceptors carrying the weapon.
  - Attack speed counts upgrades and buffs (over upgrades alone and over neither): Adrenal Glands and Resonating
    Glaives shorten `base_cooldown_steps` for the side that has them, and `dps_against` counts the buffs a unit has,
    stim among them.
  - `Weapon.dps` counts the random delay on every shot at its mean, the tables' cooldown plus 0.4996 steps (the owner,
    2026-10-21, on `tools/sweep_combat.py`'s finding that the delay is -1 + 3r/4096 steps, r even over 0 to 4095). In
    the owner's review of #153 the cooldown split in two: `base_cooldown_steps` is the tables', sped up by upgrades, a
    field `mean_delay_steps` holds the delay's mean (0 for the weapons the tables leave out), and the property
    `average_cooldown_steps` adds them. A unit's reported cooldown already holds its shot's own delay and stays as
    reported.
  - `has_in_range` is one rule, range and both radii and `margin`, for every pair (the owner, 2026-10-21, over taking
    off the largest shortfall measured and over a table by structure): exact for a unit that can move firing at a
    unit, and its docstring says an attacker that cannot move falls some 0.1 short and a structure up to some 0.26.
  - The measured weapons and the attack speeds are hand-written in `gamedata/_missing_attacks.py` (the owner,
    2026-10-21, over a generator from the sweep's output; named by the owner in the review of #153), and
    `Weapon.ignores_armor` says the oracle's beam takes nothing off for armor or shield armor (the owner, over leaving
    the oracle weaponless and over an ordinary weapon).
  - From the review of #153 (the owner, 2026-10-21): an oracle has its beam only while it wears
    `ORACLE_PULSAR_BEAM`, which it does exactly while the beam is on (measured); a hallucination does no damage
    (stated by the owner, not measured); the damage rule is `Weapon.damage_against`, taking a target's attributes,
    armor and shields; an upgrade's attack speed is `WeaponUpgrade.attack_speed_multiplier`; and the corpus tables are
    one shared fixture, `first_corpus_tables` in `tests/conftest.py`.
- **A playback mode** (the owner, 2026-10-22, one question at a time), so that AvocaDOS can replay the corpus's Terran
  games in CI as a smoke test:
  - `PermissivePlaybackTransport(recording)`, a class of its own beside `PlaybackTransport`, which stays strict for
    `test_corpus.py` and as main had it (the owner, 2026-10-22, reversing a keyword `answer_unrecorded` on
    `PlaybackTransport`, which had made it much more complicated; a sibling over a subclass, which would break the
    strict class's promise to raise), and `run_recording` in `sc2nachos.run`, over the keyword alone.
  - `run_recording(recording, api, *, permissive=False, time_limit=None, enemy_ladder_id=None, save_replay_to=None)` is
    strict by default (the owner, 2026-10-22, over always permissive), through a `PlaybackTransport`, so that a bot
    replaying its own game gets the game's own answers; `permissive`, over `strict=True` and over the transport's
    class. Strict, it reads the setup from the recording (over keywords), as two properties of it (the owner, review
    of #154, over a classmethod on `PlaybackTransport` and over a function and a dataclass in `run.py`):
    `Recording.created_its_game`, whether it asked to create the game before joining, and `played_in_real_time`, as the
    create asked, or for a game only joined, if no step comes before the second observation. It leaves no game, so a
    handler's exception raises out as it is. `save_replay_to` came from my review of 36abcc2 (the owner, over
    documenting it only): without it, a game the bot left after saving its replay raised at the leave. The permissive
    mode's lasting use after M5 is a crash check over fixed recordings that any version of a bot plays through, in the
    plain test run; a real game in CI catches more.
  - What a bot asks of its own accord is made up, under the last recorded status: every action `Success`, so the order
    book records each order as sent, over every action refused; a debug request an empty answer; saving the replay
    refused, which `play` logs; leaving before the recording does answered, `launched`.
  - A pathing query is the straight line between each pair's ends, from a unit's tag at its position in the last
    recorded observation, a tag not in it reading no path; over a path on the start's grid, no path everywhere and
    refusing queries. A query for abilities or placements raises `ProtocolError`.
  - The recording's own answers to a bot's requests, actions, debug, queries and replay saves, are passed over, so that
    a recording of a bot that acted plays back too, over a recorded answer of the same kind first. A game info asked
    for when none is next is the last one recorded, the start's for the corpus.
  - From my review of #154 (the owner): every step is made up too, the recorded ones passed over, and so is a create
    the recording does not hold, so a permissive playback reads nothing of the file to guess (reversing steps played as
    recorded, over one scan for a create and real time). An observation asked once only leaving, quitting or nothing is
    left is the last again with the status `ended`, so a recording that stops before its game's end, as one made with a
    time limit does, ends `UNDECIDED` (over a `TIE` at its end and over raising, documented).
- **`--RecordFile PATH`** (the owner, 2026-10-22), so that a bot started through `run_from_command_line` records its
  ladder games for `run_recording` to reproduce a crash: read in both kinds of game and passed as `record_to`, over
  ladder games only, and named like `--ReplayFile`, over `--RecordingFile`. The ladder passes only its own arguments,
  so a script adds it to the `argv` it hands over (`docs/ladder.md`). `RecordingTransport` compresses at xz preset 3,
  over 1 and over xz's default of 6, which took several times as long an exchange for a file barely smaller (the
  measurements in #155).
- **Ramp wall-in sites** (the owner, 2026-10-22, one question at a time, and in two reviews of #156):
  - both walls, Terran and Protoss, over Terran only and over a search for any ramp;
  - `api.placement.terran_wall(ramp)` and `protoss_wall(ramp)`, asked of a main's ramp, its start found as the start
    location whose main ramp it is; a ramp out of no main raises (third review of #156: the wall is the ramp's, over any
    ramp facing the nearest start. The first review had made them classmethods taking a ramp and a start, which was one
    too many, and the second had them take the start). Each is `None` for a ramp not 2 wide with two top tiles and
    climbing along a diagonal (`Direction.is_diagonal`), and checks no site against the grids: whether a structure may
    stand at one is `is_free`'s to say;
  - built for the main's start, which picks the Protoss gap's corner and names near and far: `near_depot`, `far_depot`,
    `barracks_with_add_on`, `add_on`, `barracks_in_wall` and `middle_depot`; `pylon`, `near_building`, `far_building`
    and `gap`, all Points (over Rectangles, a `Square` and Tiles). A tie in the corners goes to the lower x;
  - `api.map.main_ramp` and `main_ramp_of(start)` for start locations only, found when the map is made (over finding
    other points on asking): of the ramps whose top is at the start's level, the one whose center is the shortest walk,
    over the pathing grid with a townhall's footprint at the start opened;
  - `api.map.all_start_locations`, one tuple with this player's first, over `start_locations`;
  - `Ramp.orientation`, the `Direction.closest` of eight compass directions to the climb, never `None`; `Ramp.width`,
    how many tiles the top spans across the climb; and the private `Ramp._point_at_offset_from_the_top(offset)`;
  - `tools/sweep_walls.py`, placement and a zergling on every main of the pool, over placement alone and one map first.
  - Found building it: on a main left of its ramp no barracks closes the wall alone with room for its add-on, so
    `barracks_with_add_on` closes it once the add-on stands, documented (the owner, over `None` there and over a field).
  - `Tile.containing` is `Tile.at` (review of #156).

- **What the owner's review of AvocaDOS #49 settled for NachOS** (2026-10-23, one question at a time):
  - `Units.non_structures`, the units that are not structures as `UnitType.Structure` has them, over `mobile`, which
    a larva or a sieged tank is not and a lifted barracks is, and over python-sc2's `units`;
  - `Race.worker_type`, `starting_townhall_type` and `supply_type`, each raising `ValueError` for `NONE` and
    `RANDOM`, over the same on `Player` and `Enemy`; `starting_townhall_type` over `townhall_type`, which claims the
    race's every townhall, and over `base_townhall_type` (review of #157);
  - no `army`: NachOS gives a property only for a category the game draws, and type groups for `of_type` and
    `excluding_type`; what counts as army is a bot's call;
  - `Unit.damage_taken`, the health and shields lost since the observation before, `None` unless the unit was
    visible in both and kept its type (review of #158, over 0), with the health a stim costs counted as the game
    reports it (over leaving it out, after a sweep); its report kept in a slot of its own beside `velocity`'s position
    and step, which keep other rules (review of #158, over one record for the three, which made intake some 8%
    slower and would have changed `velocity`);
  - repair measured by a sweep first, then `Unit.required_repair_cost` and `Unit.total_repair_cost`, from the health
    now and from none, each a `Cost` whose steps are one SCV's (review of #159, over `repair_cost` and `repair_steps`,
    over a class of their own for the two, and over the same per type in the tables); every Terran mechanical type
    swept, with the generated table keeping only the six not repaired in the steps their type costs (review of #159,
    over every type measured, and over a hand-written table of the exceptions), once the sweep's air units that were
    never repaired were found to be the sweep's own doing (over answering `None` for them) (#159);
  - `Cost.steps`, 0 where nothing is made; in a unit type's row the build steps of each stage that costs something,
    so that a switch that costs nothing adds none and every form of a unit reads alike (review of #159, over no time
    on `Cost`, and over counting a switch's own steps); `build_steps` stays the last stage alone, and
    `research_steps` goes for `cost.steps` (review of #159, over keeping both until the duration type);
  - every step count read from the tables whole: a build time rounded up, as a train was seen to take it, and a
    repair's share rounded up, with `Cost.steps` still a float, since a cost scales (review of #159, over int-typed
    steps, and over the tables' fractions);
  - a duration type for every duration, with steps and seconds, as its own pull request, planned first (review of
    #159, over seconds for repair alone, and over one type for repair first);
  - the warp gate's source, the gateway, from `SELF_MORPHS` in this pull request, so that it costs the gateway's steps
    (review of #159, over a task of its own, and over the tech tree's sweep turning a warp gate back first, which the
    next sweep of the tech tree does);
  - turning measured by a sweep of each type's turn rate, then `Unit.time_to_face(target)`, now, over after the swap;
    the sweep measuring whether a unit moves before it faces and whether it turns to fire too (over the turn rate
    alone); the values on the rows, `UnitTypeData.turn_rate` and `moves_while_turning` (over internal, as repair, over
    the rate alone, and over the name `sets_off_while_turning`), and `Weapon.turns_to_fire`, a bool, true unless the
    type was seen to fire without turning, a weapon never seen to fire taken to turn (over the planned
    `Weapon.firing_arc`, and over a value at the ground and one at the air, which agreed for every type); the rates
    generated in the game's unit, 1/4096 of a full turn a step, the fast flyers pinned by a turn in place (over degrees
    to the nearest half), each type mapped to its rate, a constant named for the degrees a Normal second it makes (over
    grouping the types by rate, and over plain whole numbers); `Unit.time_to_face(target, *, facing_tolerance)`, a
    `GameTime` since #165, the turn alone, since how long an order takes to land is the connection's (over
    `steps_to_face`, and over counting the order's step); settled at a stop point after the sweep and in the owner's
    review (#162);
  - one type for every moment and length of game time, `sc2nachos.GameTime` (the owner, 2026-10-24, one question at a
    time, over `Duration`, a length alone, and over `Time`), so `api.time` returns one too:
    - every moment one, under a `time` name: `event.time`, `action.time`, `order.issued_time`, `reservation.time`,
      `unit.last_seen`, and a series' `start_time` and `last_time`; `api.step` stays the one int counter (over
      dropping it, and over changing `api.time` alone);
    - a `TimeSeries` indexed by `GameTime` throughout: `TimeSeries(buffer, start_time, size)`, `append(time, value)`
      at a whole number of steps, `at(time)` (`at_time` folded in), `series[time]` and its slices, iteration by
      `(GameTime, value)` and `derivative_at(time)`, a time between two steps read at its `nearest_step`, a tie the
      later (the owner's review, reversing the index of steps with `start_time` and `last_time` beside it, chosen
      over keeping the series wholly by the step counter; the nearest step over whole steps alone, and the
      property's name over `rounded_steps` and `step`);
    - a frozen, slotted dataclass of one keyword-only field, `steps: float`, with `seconds` worked out and
      `from_seconds` (over a `float` subclass, and over holding seconds, and in the owner's review over `GameSteps`
      with its field positional), made once an observation for its events; `is_integer()` says whether it is a whole
      number of steps (the owner's review, over a private helper), `GameTime.ZERO` is the one zero (the owner's
      review, over a module constant each, and over a lowercase `zero`, every other constant being upper case), and
      `from_normal_seconds` and `normal_seconds` read the times the game gives at the Normal speed, 16 steps a
      second: a weapon's cooldown, the score's idle times (the owner's review);
    - `timedelta`'s arithmetic: `+`, `-` and `%` between two, `*` and `/` by a plain number, `/` and `//` between two
      to a plain number, unary `-` and `abs`, ordering between two alone, `TypeError` for a plain number, and zero
      false (over the least needed, and over a plain number read as steps);
    - every length one, its `_steps` dropped or made `_time`: `build_time`, `Cost.time`, `base_cooldown`,
      `mean_delay`, `average_cooldown`, `weapon_cooldown`, what `time_to_face` returns, `Research.total_time` and
      `time_left`, the score's `idle_production_time` and `idle_worker_time`, and `PlacementReservation.max_time`
      (over a unit's cooldown kept a float, some 150 ns a read cheaper); rates stay as they are;
    - the inputs too: `api.on(min_interval=, at_time=)` (`min_interval` kept over `interval` in the owner's review: a
      floor counted from the handler's last run, not a schedule), a drawing's `shown_for`, `debug.refresh_interval`,
      `reserve(max_time=)`, `time_limit`, and a series' `window`, `sigma` and `size`, a drawing's, the refresh's and a
      moving average's a whole number of steps or `ValueError`; `steps_per_turn` stays an int and `--TimeLimit`
      seconds (over plain step counts, and over taking either);
    - no `str` of its own but `to_string(decimals=3)`, minutes and seconds to 0 to 3 places, rounded down, which
      from 2 places tells every step from the next (the owner's, over `str` as the game's clock, and in the owner's
      review over the property `time_string` and over a format language); `steps_to_seconds` and
      `seconds_to_steps` go for it (this branch);
- **Replay playback** (the owner, 2026-10-24, one question at a time), so that an api can learn from a replay, as a bot
  of either player saw the game, in a branch of its own beside M5:
  - `run_replay(replay, api, *, player_id, steps_per_turn=1, time_limit=None, record_to=None, installation=None,
    window=(1024, 768))`, beside the other runners, through `Client.replay_info` and `Client.start_replay`. The newest
    build installed reads which build a replay needs, and a second client starts when that is another.
  - `player_id` is required, 1 or 2, and 0 or any id the replay does not list as a player raises `ValueError`: the
    game's player 0 lists every unit, but as nobody's (tool `sweep_replays`, `views`). Chosen over a default of 1, and,
    once the owner asked whether 0 is how the fog lifts, over three views (each player, and everything), since a view
    with the fog off still has a side.
  - No fog-off view yet (found building it, the owner over keeping `disable_fog` documented and over fixing the reading
    in the same PR): the game reports the units a player never saw in full but for their orders, displayed `Hidden` or
    as remembered, and NachOS reads them as units out of sight, a `Hidden` one `INVISIBLE` with `in_sight` True and its
    health raising `NotReportedError`. #163 then taught NachOS to read a fog-off report, keeping what the player saw
    apart from the rest.
  - Orders, chat, drawings and camera moves get no special code: the game refuses the turn's request, and `play`
    raises `ProtocolError` with its words (over raising at the call and over dropping them with a log). The observer's
    camera move, which the game takes, is left out.
  - A replay stopped before its end, by `api.leave_game()` or at `time_limit`, returns `UNDECIDED` (over `DEFEAT` and
    `TIE` as in a game, and over the result the replay's info gives).
  - `record_to`, and `run_recording` plays a recorded playback back exactly, which `Recording.start_replay_request`
    finds; `permissive` raises `ValueError` for one (over the next PR, and over leaving it to tables made for queries).
  - Found building it: a computer's `Player.race` is the race it asked for, since the game gives a computer none, as
    when a replay is played back as one; a replay is the game's file and a recording NachOS's, never named for each
    other (`docs/maintaining/code-style.md`).
  - From my review of #160 (the owner, over changing them): a Random computer's `Player.race` stays `RANDOM` in a
    replay played back as it, as documented, a niche case; a recording that asks something before starting its replay
    is not played back, as a recorded game with a request before its setup is not; and `run_recording` reads the start
    of a recording up to three times, a few kilobytes each.
- **Reading a playback with the fog off** (the owner, 2026-10-24, one question at a time):
  - Two collections, over one list marking the unseen with a new `Visibility.UNSEEN` and over the truth alone. The
    owner: "We don't really want to make things more complicated for writing a bot, just for the purpose of supporting
    the replay functionality." `api.units`, `Visibility` and every event stay what the player knew, as with the fog on.
  - The second holds every unit, the whole game, as objects of its own read in full from a second tracker, over only
    the rest: a unit the player lost sight of is stale in `api.known_units`, read as last seen, and in the whole game
    as it is.
  - `api.all_units`, over `api.units_with_fog_off` (my recommendation) and `api.whole_game.units`; where the fog is on,
    reading it raises a new `FogOnError`, as `api.enemy` raises `NoEnemyError`.
  - The same id in both, over a lookup by tag: the player's view takes each unit's id from the whole game's.
  - A unit undetected in the player's sight reads as last reported in full in `api.all_units`, as AGENTS.md's "never
    answer the zero" has it.
  - Found building it: the player's view rebuilds the fog-on observation, the structures and map's units in the fog as
    copies under tags of their own, remembered as they went until their spot is in sight without them, so that the
    tracker reads it unchanged. What tells an enemy undetected in sight from one unseen is that it reports no owner,
    not the tile under it, and an integration test plays one replay with the fog on and off a step at a turn and finds
    the player's views the same at every step. With more steps a turn the fog on can remember a structure afresh
    between two turns, which NachOS read as one found dead and another first seen, in a game too, until #164 took it
    for the one unit (below).
  - From my review of #163 (the owner, over changing them): no guard against `id_provider` giving an id a unit already
    holds, since no path to it was found; and each fog-off observation stays copied whole, some 0.3 ms of a 4.0 ms
    turn.
  - From the owner's review of #163: the tracker takes each new unit's id from one callable, `id_provider`, given the
    report the unit is first seen in, its alliance's next id unless another is given, over a branch on an optional
    `id_of_tag` (11 ns more a new unit). An enemy unit of `api.all_units` stays a `Unit`, with no sweep of the fields
    only `OwnUnit` reads: the fog off reports no enemy orders, and the rest was not compared.
- **A structure remembered afresh between two turns** (the owner, 2026-10-24, over leaving it found dead and first
  seen, documented): a fog copy that comes under a new tag at the bit-identical spot of the same alliance is the same
  unit, with no event. `tools/sweep_fog.py` measured it (`retag`): a structure seen and lost again between two
  observations comes so, and NachOS read it as one found dead and another first seen. The game shows one killed out
  of sight and built again on its spot, seen so, alike (`rebuilt`), so that one is taken for the old until its spot is
  in sight at a turn, when the old is found dead and the new first seen. A sight change is recorded only where the
  display changes, and a unit reported dead under either of its tags is matched to no copy.
- **Timed lives and buffs** (the owner, 2026-10-25, one question at a time), after `tools/sweep_timed_life.py` found
  the game reporting `buff_duration_remain` and `buff_duration_max` to a unit's owner alone, and nothing that times a
  force field or a reaper's grenade to anyone:
  - `OwnUnit.buff_duration_max` and `buff_duration_remaining`, the owner's names, on `OwnUnit` beside `orders` and
    `weapon_cooldown` (over `Unit` raising `NotReportedError` for every unit not this player's), each a
    `GameTime | None`, `None` where the game reports no maximum (over `GameTime.ZERO`, and over plain step counts).
    The remaining duration hangs on the maximum, so a timed life run out reads zero, not `None`;
  - `weapon_cooldown` stays a `GameTime`, zero without a weapon, with `weapons` saying whether a unit can attack at
    all (over `None` from the unit's weapons, which the game's single field cannot tell, would cost a read some
    130 ns, and leaves `not unit.weapon_cooldown` reading a weaponless unit as ready all the same);
  - force fields and reaper grenades stay units, over effects as python-sc2 makes them: the game times neither, to
    anyone, and their time left, worked out from a lifetime and the first listing, needs each followed from one
    observation to the next, as a unit is and an effect is not. The same would serve the enemy's MULEs and broodlings,
    whose time left the game reports to their owner alone. How NachOS gives that time waits until after the migration
    (section 1).
- **Writing a replay's whole game for salsa** (the owner, 2026-10-25, one question at a time), so that a model or an
  LLM learns from a game without playing it back:
  - salsa, `maxnus/salsa`, holds the `.salsa` format whole: its spec, the writer, the reader (`sc2salsa`) and the
    analysis, and its plan holds the format's decisions. sc2salsa depends on sc2nachos, a plain dependency, to launch
    the game and play a replay back. This reverses the first split, in which NachOS wrote the files behind a
    `sc2nachos[dataset]` extra: read straight from the protocol, the writer uses NachOS's launch and protocol layers
    alone (maxnus/salsa#3).
  - NachOS's part: a public way to start a replay on its own build with its map and read its observations, the first
    half of `run_replay`, which any bot maker wanting a replay's raw observations can use too. A bot's own game is saved
    with `save_replay_to`, and its file written from that replay.
  - salsa's writer plays the replay as each player with the fog on, every unit from its owner's view: compared side by
    side, the fog off adds only the map's units while no player sees them, and its sight is NachOS's reconstruction
    (maxnus/salsa#3).
  - A replay plays only on its own build: 4.10 (75689) is installed here, and AI Arena's games are on it (three of
    2026-10-07); a human ladder replay needs a client of its build, on Windows or macOS.
  - The measurements these rest on are in #168 and maxnus/salsa#3.
- **An action on a copy the fog-off view never lists** (the owner, 2026-10-25, over `None`): its target reads as not
  reported, as an order's does. A player's order on a structure or mineral field it remembers in the fog names the tag
  of the game's copy of it, which the game lists only with the fog on; NachOS's fog-off view makes copies of its own,
  under tags from 1 << 62. So an `OwnActionEvent` handler stops `run_replay(..., disable_fog=True)` with
  `UnknownTagError` (`read_action`), in all six views of three AI Arena games, 3 to 1,290 actions a view.
- **What AvocaDOS's units piece settled for NachOS** (the owner, 2026-10-25, one question at a time), after a survey of
  python-sc2's `Unit` and `Units` members that AvocaDOS reads:
  - `closest_to`, `closest_n_to`, `sorted_by_distance_to` and `closest_distance_to` take a unit as well as a point,
    measured from its position as `within` and `distance_to` measure, a unit in the collection at distance 0 from
    itself (over AvocaDOS passing `unit.position`);
  - `OwnUnit.is_doing(abilities)`, whether the current order carries out one of them, and `Unit.is_carrying_resource`,
    from the carrying buffs (over python-sc2's `is_gathering`, `is_returning`, `is_moving`, `is_attacking` and
    `is_repairing`, and over the bot's own functions for all; reversed in the review of AvocaDOS #52, below); whether an
    SCV is constructing stays the bot's;
  - in the owner's review of #173: `Unit.has_buff(buff_ids)`, one buff or any of several, and the filters
    `Units.doing(abilities)` and `Units.with_buff(buff_ids)`, the latter raising for a unit never shown visible as
    `complete` does; every race's gas buff folded into `BuffId.CARRYING_GAS`, read as one as `GATHER` is, rich minerals
    kept apart (over keeping the three, and over folding rich minerals too); and a worker carrying gas from a rich
    geyser, which the game gives no buff (in game), documented as carrying nothing (over an own unit's return order
    counting too, and over renaming to `has_carrying_buff`);
  - in the owner's second review: `CARRYING_GAS` a custom id, `auto()`, with all three game ids read as it, as `SIEGE`
    is (over the SCV's id standing for all three); `has_buff` and `is_carrying_resource` read through `buffs` (over
    checking the report's game ids, some 0.4 us a call faster for one buff and slower for several) (#173).
- **What the owner's review of AvocaDOS #52 moved into NachOS** (the owner, 2026-10-25, one question at a time): the
  owner's view that more of python-sc2's conveniences belong in NachOS, since they are generally useful and a bot
  moving over will expect them.
  - Stated by the owner: `Unit.is_own` and `is_enemy`; `is_not_stale`, the opposite of `is_stale`, the owner's name
    (over `is_listed`, `is_in_observation` and `is_present`); `is_repairable`; `is_melee`; `is_carrying_minerals`, with
    `is_carrying_gas`; `Units.not_complete`, `ground` and `air`.
  - `OwnUnit.is_moving`, `is_attacking`, `is_gathering`, `is_returning` and `is_repairing`, each its `is_doing`, and
    `is_constructing` moved from AvocaDOS: its `construction`, or a current order whose ability places a structure
    (reversing #173's choice of `is_doing` alone; over the five without `is_constructing`).
  - `OwnUnit.is_weapon_ready`: a weapon and a zero cooldown, true too while an attack is under way (reversing "weapon
    ready stays a zero cooldown"; over python-sc2's `weapon_ready`, and over no read). AvocaDOS's check of a cooldown
    of at most 4 steps becomes `weapon_nearly_ready`.
  - `Unit.ground_weapon` and `air_weapon`, with `ground_range`, `air_range`, `ground_dps`, `air_dps`, `ground_damage`
    and `air_damage`, each 0 without a weapon, a hallucination's counted, as python-sc2's are (widening the combat
    conveniences' choice of `weapon_against` over python-sc2's per-domain set; over weapons and ranges alone, and over
    weapons alone). The oracle's beam counts only while it is on, as for `weapon_against`.
  - `Units.having_in_range(target, *, margin)` and `Units.in_range_of(attacker, *, margin)`, filters over
    `has_in_range` (over `attackers_of`/`targets_of`, and over one of them on `Unit`).
  - `Unit.bonus_damage_against(target)`, a float: the bonuses for the target's attributes, summed, times the attacks
    (over a bool, and over leaving it to the bot).
  - `UnitOrder.known_target`, `None` where `target` raises; `target` keeps raising, since `None` also means an order
    aimed at nothing (over `target` answering `None`, and over leaving it to the bot).
  - `Units.without(units)`, by NachOS id, and no operators, as #35 chose (over `|` and `-`, and over `+` and `-`).
  - `Units.sorted(key, *, reverse)`, a collection, stable (over `sorted_by`, and over lists).
  - `Units.beyond(distance, *, of)`, the complement of `within`, and `Units.outside(areas)`, of `in_area`, which
    becomes `inside` (the owner, over `within` taking areas, and over python-sc2's `further_than`). `beyond` is
    `within`'s complement rather than a loop shared with it, which made `within` some 10% slower.
  - `OwnUnit.production_capacity`: 2 for a structure with a reactor, 1 for another the tables give as a maker of a
    unit, 0 otherwise, a warp gate reading 1 (over `has_reactor` and `has_tech_lab` alone, and over leaving it to the
    bot); and `OwnUnit.has_reactor` and `has_tech_lab` (the owner, after noting that a unit is no member of a type, over
    `includes` alone and over `in` taking a type id) (#175).
  - In the owner's review of #175: `Unit.active_weapons`, public, the weapons it can fire now, which leaves out an
    oracle's beam while it is off, read by `weapon_against`, the per-domain reads and `is_weapon_ready` (over a
    private method, and over `weapon_against` keeping its own check to spare some 20 ns, of which the owner said "I
    don't think we need to chase 20 ns"); `Weapon.total_damage`, damage times attacks;
    `Weapon.bonus_per_hit_against(attributes)`, which `Weapon.damage_against` and `Unit.bonus_damage_against` share;
    "first" dropped from the per-domain reads, no type having two weapons for one domain (corpus); `_OWN_ALLIANCE`
    beside `_ENEMY_ALLIANCE` (#175).
  - In the owner's review of AvocaDOS #52's switch to these reads: `Unit.first_seen`, the time of the first
    observation that showed the unit visible, `None` before one has, beside `last_seen` (the owner, over
    `Unit.last_listed`, the last observation that listed it, which would have replaced AvocaDOS's `_last_listed_time`;
    over both; and over neither). AvocaDOS keeps its `_last_listed_time` and `_listed_since` (#177).
- **What the owner's review of AvocaDOS #54 settled for NachOS** (the owner, 2026-10-26), the piece that holds units
  where AvocaDOS held tags:
  - `Unit.is_alive`, the opposite of `is_dead`, true for an enemy's unit that died out of sight, whose death the game
    never reports (the owner, over `is_not_dead`, spelled like `is_not_stale`, and over no new read) (#178);
  - `Unit.is_orderable`, the last observation listed the unit as this player's, and `Units.orderable`, typed as own
    units: false inside a gas building or a transport, under a neural parasite, which makes the unit an enemy `Unit`,
    and once dead (the owner, moving AvocaDOS's `is_listed_as_own` into NachOS, over `is_mine`, close to `is_own`,
    `is_at_hand` and `is_listed_own`; and over the property alone, and over a `Units.not_stale` filter alone) (#179);
  - `Units.alive`, `dead`, `stale` and `not_stale`, for a collection kept from an earlier observation (the owner), and
    `Units.visible`, one of python-sc2's two filters NachOS had no name for (the owner, over `selected` as well, and
    over neither) (#179);
  - and a fix, not the owner's call: `Units` covariant in its units, declared by a `TypeVar`, since the type
    parameter syntax inferred it invariant through the self types of `own` and `orderable`, so a `Units[OwnUnit]` had
    neither; found switching AvocaDOS #54 to `orderable`, and guarded by a test pyright reads (#180);
  - `Unit.is_listed`, with `Units.listed` and `not_listed`, for `is_stale`, `is_not_stale`, `Units.stale` and
    `not_stale`: a positive name, which needs no opposite (the owner, 2026-10-28, over `is_present`, which reads as
    alive, `is_in_observation` and keeping `is_stale`; the owner had asked about `is_observed`, `is_current`,
    `is_updated` and `is_latest`, each wrong for a unit listed but not seen, or vague) (#182). The internal flag
    follows, inverted: `_listed`, `_mark_unlisted` and the tracker's `_unlisted_units`, so the code has one word for
    it (the owner, 2026-10-28, over keeping `_stale` to hold the PR to the public names) (#182);
  - `Units.combined_with`, one unit or many added, matched by id, and `Units.without` taking a single unit too.
    `Units` stays immutable: #180's covariance needs it, every handler shares `api.units`, and its id index must not
    go out of date (the owner, 2026-10-28, over the operators `|` and `-`, and over a mutable `Units`). The name
    stays `combined_with`, the one-collection form of `Units.combined` (the owner, in the review of #182, over
    `with_`, since `with` is a keyword, and over `plus`) (#182).

## Next

- **What M5 needs of NachOS first** (AvocaDOS's plan, M5, the owner's decisions of 2026-10-20), each a pull request:
  players (#143), running on the ladder (#146: its command line, a bot's own arguments in `run_from_command_line`,
  replay saving, and the runners taking the api with `race` and `name`, #147), paths on a grid (#148, #151), combat
  conveniences on a unit (#152, #153), a playback mode that answers a bot's own actions (#154, with `--RecordFile` in
  #155), and ramp wall-in sites with their sweep (#156), the last of them.
- **What the owner's review of AvocaDOS #49 asks of NachOS** (2026-10-23, below), each a pull request before the swap's
  next piece: `Units.non_structures` and the race's unit types (#157); `Unit.damage_taken` (#158); repair, measured by a
  sweep, then `Unit.required_repair_cost` and `Unit.total_repair_cost`, with `Cost.steps` (#159); and turning,
  measured by a sweep of how each type turns, then `UnitTypeData.turn_rate` and `moves_while_turning`,
  `Weapon.turns_to_fire` and `Unit.time_to_face(target)` (#162).
- **What AvocaDOS's units piece needs of NachOS** (2026-10-25, above): the nearest-unit methods taking a unit,
  `OwnUnit.is_doing`, `Unit.has_buff`, `Unit.is_carrying_resource`, `Units.doing`, `Units.with_buff` and
  `BuffId.CARRYING_GAS` (#173), before the piece; and what the owner's review of AvocaDOS #52 moved into NachOS (#175),
  before that piece merges.
- M5, in AvocaDOS (section 3).
- Beside M5, **writing a replay's whole game for salsa** (2026-10-25, above), a pull request at a time, in the order
  salsa's plan holds:
  1. Two bugs AI Arena's replays hit (the owner, 2026-10-25): `run_replay` could not play back a replay whose map is
     not where the replay names it (#169, merged), and `OwnUnit.engaged_target` raised for a target the game never
     listed (#170, merged).
  2. Starting a replay on its own build with its map, public, for salsa's writer.
- Apart from salsa, and not waited on by it: an action's target on a copy the fog-off view never lists reads as not
  reported (2026-10-25, above).

## 1. Waiting until something needs them

- A launch passes no data version, only the base build: a replay whose data version is not its base build's default
  may be refused on Windows or macOS (unverified; the owner, review of #160, over passing it untested).

- A timed unit's time left where the game reports none: a force field's, a reaper's grenade's, and the enemy's MULEs'
  and broodlings', from a lifetime a sweep measures and the unit's first listing. The owner put it after the migration
  (2026-10-25). `tools/sweep_timed_life.py` measured those four; the other timed units, how to give the time, and what
  it reads for a unit first seen already standing, are to plan then.

- Keeping the selections a turn's units hand out, rather than building them at every read, about 15 ns a unit: the
  owner chose to decide it once the swap runs and AvocaDOS's exit gate measures what its reads cost (review of
  AvocaDOS #49). The owner's two ideas: one key for a set of selections in any order, so `own.structures` and
  `structures.own` are built once, and lazy views, so a chain filters once. A view must still be a `Sequence`.

- A type for start locations, perhaps a subclass of `Expansion` holding its main ramp (the owner, second review of
  #156), to plan once M5 shows how AvocaDOS reads its mains; `main_ramp_of` and the walls take a start location Point
  until then.
- A narrow, read-only context for a unit in place of its tracker, through which it reads the tables, the upgrades,
  units by tag and builders (an agent's review of the codebase, 2026-10-20; the owner chose to wait for M5 to show a
  need). The whole of `_tracking` shares the tracker this way, and tests build one in a line.
- Typed order methods on `OwnUnit`, M4's slice 7, deferred (above).
- Power in the placement grids, in a pull request of its own that measures what the game requires; `needs_power`
  stays a bool until then.
- An up-to-date pathing grid.
- The next trial of `tools/sweep_fog.py` (begun with `retag` and `rebuilt`, 2026-10-24) checks that a structure lifted
  off or uprooted, and a viking in either form, is never left as a remembered copy, which the tracker assumes (the
  owner, review of #71). It also measures whether a remembered copy reports its structure's build progress. If it
  does, a copy under a new tag whose build progress went down is another unit: a structure built again on its spot
  starts unfinished (the owner, review of #164).
- The next sweep of the tech tree gets a carrier offered Build Interceptors, which the sweep never saw (#75), so that
  the tech tree records what makes an interceptor and `COST_OVERRIDES` can lose its entry. It also turns a warp gate
  back into a gateway after Warp Gate is researched, reads what that gateway is offered and orders its morph, so that
  the tech tree records a gateway offered `GATEWAY_MORPH_WARP_GATE` and `SELF_MORPHS` can go (#159). Recorded as it
  stands, the turn back would make the warp gate the gateway's source too, a loop that would break the gateway's cost:
  plan with the owner which way a switch both ways gives the source.
- When the corpus is next recorded, `run_ladder` pings the client for its build and warns of one the tech tree was not
  swept on, as `run_local` warns from the installation without asking the game (the owner, #75).
- Autocast switches sent by NachOS, which it only reads now (`AutocastToggle`). `allows_autocast` is a bool, though
  an unburrow's autocast is the performer's: a burrowed roach comes up by autocast, a drone does not. A family id
  would then have to go out as a performer's own id, which needs a link the game's rows do not give (raised with the
  owner in #104, not decided).

## 2. After each nachOS pull request merges

Record it in AvocaDOS's `docs/plans/nachOS-plan.md` on `main`: a paragraph under M5's "Status" saying what it
settled and what AvocaDOS writes differently at M5. That needs an AvocaDOS checkout; its tests (`uv run pytest`,
`tests/test_nachos_parity.py` in particular) install nachOS from source. Commit there, and push only when the owner
says so.

## 3. M5 (AvocaDOS runs on NachOS)

In AvocaDOS's plan, section "M5 — The swap". Its preparation on python-sc2 is done (AvocaDOS #45 to #48: the bot's
state off the api, the event layer in NachOS's shape, the last order wins, and the whole turn timed), and the swap is
a pull request a piece into AvocaDOS's `main`, the first of them AvocaDOS #49 (the owner, 2026-10-24, over an
integration branch, `nachos-swap`, to be merged into `main` once the whole passed). Merged: `sc2nachos.Api()` (#49)
and ids and points (#51). Next is the units piece, once #173 is merged.
