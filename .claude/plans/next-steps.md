# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps. A
decision gets a line or two: what it rules out, and why where that is not plain. What was built is in the code and
the docs, and who chose what over which alternative is in its issue or pull request. The decisions in full, as they
stood before they were cut to that, are `git show 582149c:.claude/plans/next-steps.md`, and they still stand.

## Where things stand

As of 2026-11-08:

- **M4 is done**: its slice 7 became `OwnUnit.issue` (#240), and shortcuts for common orders wait (section 1).
- **M5's swap is done**: AvocaDOS runs on NachOS, on the ladder since its v1.4.0 (2026-11-01). Its exit gate, a day
  on the ladder and the comparison of `AverageStepTime` with the baseline (AvocaDOS's plan, the first of "Open"), is
  open.
- **M6 is done**: AvocaDOS pins NachOS's releases from PyPI, 0.1.0 first (AvocaDOS #87, 2026-11-04), then 0.2.0,
  which speed collects (AvocaDOS #90). A release is made as `docs/maintaining/releasing.md` says.
- **0.3.0 is out** (2026-11-05, from 4c82513), with #226 to #235. Its notes say what a bot has to change: the `Vision`
  renames (#227), an extension's handlers at `EventPriority.BEFORE` or `AFTER` alone (#232), `Units` where there were
  tuples (#229, #230), speed mining letting go of a worker the bot orders elsewhere (#231), and a handler of
  `AnyUpgradeEvent` handed upgrade inference's `EnemyUpgradeAssumedEvent` (#233). AvocaDOS #91, open, takes it: it keeps
  a number of collectors, speed mining picking their refineries and `release_collectors` those to let go, reads when
  each tile was last in sight off `api.vision`, and starts its turn clock at `BEFORE` and ends it at `AFTER`, after
  speed mining, so that its step time stays the whole turn.
- **0.4.0 is out** (2026-11-05, from 9f56f9a), with #236 and #237: orders given apart go out as one command where one
  order could have named their units. Its notes say a bot changes nothing in its code, only what `api.action_failures`
  lists. AvocaDOS took it in AvocaDOS #95.
- **Since 0.4.0**, on main: #240 to #248. The next release, 0.5.0 since #241, #242 and #247 remove and
  rename what a bot uses, says in its notes what a bot has to change: `issue` takes no `data` (#241), a bot keeping its
  own data per unit in a `UnitDict`; the order a bot gave is an `IssuedOrder`, in `sc2nachos.orders`, where it was an
  `Order`, and what a unit reports doing an `Order`, in `sc2nachos.units`, where it was a `UnitOrder` (#242); and
  `InProduction` and `Research` are a `Progress`, whose `product` and `fraction` were their `type_id` or `upgrade` and
  their `progress` (#247). It adds `unit.issue` (#240), `Unit.morph_progress` (#243), `train`, `build` and `research`
  (#245), and `UnitTypeData.base_form` (#248). AvocaDOS #97, a draft, takes it: its production trains a unit, builds an
  add-on and morphs an orbital command, and it bumps its pin once 0.5.0 is out.
- The consuming bot is AvocaDOS ([github.com/maxnus/AvocaDOS](https://github.com/maxnus/AvocaDOS), branch
  `main`). Its `docs/plans/nachOS-plan.md` holds what is left of the migration on its side, and
  `docs/plans/archive/nachOS-plan.md` the milestone plan (M0 to M6) with every nachOS PR it took. It has no
  python-sc2 left (AvocaDOS #68), its game map searches run on `Grid` (AvocaDOS #70), and its expansions and start
  locations (AvocaDOS #84) and speed mining (AvocaDOS #76, #85) are NachOS's.

## Decided, so not to be proposed again

The issue, discussion or pull request named holds the reasons, and the alternatives weighed.

### Orders

- **NachOS checks nothing an order needs** (#46): minerals, vespene, supply, queue room and tech are the game's to
  judge and the bot's to budget, and `api.action_failures` lists what the game refused. No budget, no `can_afford`,
  no `api.orders.cancel`: a built version with all of that was dropped as too complicated for what it bought.
- **No order priority, no hold and no order state.** A unit takes the last order a turn gives it, a structure's train
  included, and a bot that ranks its orders keeps the rank itself, in a `UnitDict` it empties at each turn's start.
  NachOS never holds an order until a unit can start it (#126, reversing #115 to #120): that belongs in the bot, or in a
  layer above the order book. Nothing follows an order once sent (#69, #115): the `IssuedOrder` is a handle, and the bot
  reads its units, events and `api.action_failures`.
- **An unqueued order a unit is already carrying out is not sent to it** (#68), though a train or a research always
  is. `force=True` sends it anyway, dropping the unit's queue, so there is no `clear_queue` (#77). A production
  order gets no "replace": a cancel frees no queue slot and no minerals in the same step (#43).
- **One public id per action** (#103 to #107): a family that does one thing for different performers is one id, its
  per-unit ids only in `RawAbilityId`, with no `GENERAL_` prefix, and the leveled researches keep an id per level. An
  ability that behaves differently by its target is split into custom ids, one per behavior: `UNLOAD` and
  `UNLOAD_AT`, `LIBERATOR_SIEGE_AT` (#101, #126).
- **`api.orders.issue` orders one unit or many**, with no `issue_all` (#130): what every unit of a command carries
  out goes out as one command naming them all, anything else as a command to each. A research, or a placement at one
  target, given to several raises `ValueError`, since only one of them can carry it out.
- **`unit.issue(ability, ...)` is `api.orders.issue(unit, ability, ...)`** (#240), through the order book its tracker
  holds: a second spelling for one unit, which the owner chose over the review checklist's rule (2026-11-08). The docs
  and the demo bot order one unit through it and several through `api.orders.issue`. Every tracker holds a book:
  `MadeUpUnits` has its own, `orders`, which sends nothing and so holds every order its units were given (the owner,
  review of #240, over raising for its units, and over emptying it at each observation, code the order book would
  carry for a test helper alone).
- **An order carries nothing of the bot's** (the owner, review of #240; #241): no `data`, so `IssuedOrder` takes no
  type parameter. Nothing read it, not the demo bot, the extensions (speed mining chose not to, review of #231) or
  AvocaDOS, and a bot keeps its own data per unit in a `UnitDict` (#200).
- **The order a bot gave is an `IssuedOrder`, and what a unit reports doing an `Order`** (the owner, 2026-11-08,
  #242), over `Order` and `UnitOrder`, which said nothing of how they differ; over `PendingOrder`, since a sent order
  stays the same handle and `api.orders.pending` holds the unsent alone; and over `ReportedOrder`, the name
  `sc2nachos.testing` gives the tuple a test makes a unit's order from. The reported one stays in `sc2nachos.units`, so
  a bot's old `from sc2nachos.orders import Order` fails at import, or in the type checker where it is imported under
  `TYPE_CHECKING`, rather than meaning the other; `test_package.py` checks that `sc2nachos.orders` names no `Order`.
- **`train`, `build` and `research` order by what is made or researched** (the owner, 2026-11-08, #245), on
  `api.orders`, which translates, and on each unit, which forwards. Each (maker type, product) pair is made with one
  ability (a test checks it on the game's data), read off each ability's `products`, so a warp gate's zealot is its
  warp-in where a gateway's is a train, and a rich gas building, which the products leave out, is read off its own
  creation ability. `train` orders what a structure or a larva makes that is not a structure (53 abilities), and `build`
  a structure a worker makes and an add-on (57 maker and product pairs), over one method for everything a unit makes,
  and over `train` alone; morphs, an orbital, a lair or a baneling, and changes of mode, a lift or a siege, stay
  `issue`. A research given to a type not among its `researchers` raises `ValueError` naming them, a barracks's tech lab
  for Stimpack, over passing it to the add-on. Units whose types make one type with different abilities, a gateway and a
  warp gate, raise and are ordered apart, so each method returns one `IssuedOrder`. Self types keep `train` to a
  structure or a larva, `build` to a worker or an add-on's host and `research` to a structure. The demo bot keeps
  `issue`, since its budget and its count of builds go by ability.
- **Orders given apart go out as one command where one order could have named their units** (#237): of an ability
  every unit of a command carries out, sent as one game ability, at one target as the game keeps it and queued alike,
  joined across the whole turn unless that would send a unit's commands out of the order it was given them. An
  archon's merge and a load-all never join (`ABILITIES_JOINING_OTHER_ORDERS`), nor does a command aimed at one of its
  own units. The owner chose this over grouping the orders in AvocaDOS's squad micro (2026-11-05), whose order to each
  unit had sent a ladder game to 22,717 APM.

### Units and their types

- **python-sc2's conveniences that bots generally use belong in NachOS** (review of AvocaDOS #52, #175): a bot
  moving over will expect them. They are named and shaped as NachOS's own, as AGENTS.md has it.
- **A unit's type stays a type parameter**, `Unit[UnitType.Refinery]`, over a class per type, which would need an own
  and an enemy class of each of the 342 types and groups and made reads slower (#185). A read only some types have
  takes a self type, but the shield reads, `construction` and `rally_targets` stay on every unit: asking a Zerg
  unit's shield is no type error. No `Unit.is_type(group)`: a type guard cannot narrow the object a method is
  called on, so `UnitType.X.includes(unit)` stays the one spelling (#202).
- **`Units` is immutable, with no operators** (#35, #182): `combined_with` and `without`, by NachOS id. Every handler
  shares `api.units`, and its covariance and its id index need it unchanged. It equals, and hashes as, another of the
  same units in the same order, never a tuple or a list (the owner, 2026-11-04, over unordered), so `Order.units` and
  the actions' `units` hold one.
- **NachOS names only the categories the game draws**: no `army` and no `mobile` (#157). What counts as army is the
  bot's call, through the type groups, `of_type` and `excluding_type`.
- **A unit type names what makes it, not what the tables alias it to** (#75): no `UnitTypeData.base_type`, the
  game's `unit_alias`, which aliases an adept's shade to the adept. `morphed_from` gives a form's source, and
  `base_form` the type a form counts as.
- **`UnitTypeData.base_form` is the type of which a type is a form** (the owner, 2026-11-09, #248), over `counts_as`,
  `unit_alias` and a set of each type's forms, read through `unit.type_data` alone. Back along `morphed_from` through
  each step that costs nothing, a rich gas building its plain one, and by hand where the tech tree links nothing
  (`UNRECORDED_BASE_FORMS`): a hellbat is a hellion (the owner: one unit to a planner), a flying locust, a creep tumor
  and a changeling their plain one, so every `UnitType` group of one unit's forms shares one.
- **`techs_granted` is worked out from `morphed_from`** (the owner, 2026-11-09, #248): the tech named after a type and
  after each type back along it, over reading the game's `tech_alias`, which it matches for every curated type.
- **The tech tree holds one record per id** (#188), generated by `tools/generate_tech_tree.py` rather than worked out
  at import. An id it says nothing of reads an empty record, never `None` or a raise.
- **A buff every unit of one type wears for as long as it is listed is skipped, not curated** (#215):
  `BuffId._SKIPPED_IDS`, never a member the ids remap to.
- **A bot's data per unit is a `UnitDict`** (#200), over data on the unit, which every extension would share untyped,
  and over a data type parameter on `Unit`, which would spread through every unit event and `Api`.
- **Own and enemy stay separate classes and events**, over a selector over one class (Discussion #66), which would
  lose `OwnUnit`'s typing. Every base event is an `Any…` event NachOS never hands out itself, and "opponent" names the
  other player, "enemy" its units (#131).
- **A niche case of one spell gets its behavior documented**, never event generation made more complicated for it,
  as for a neural parasite's takeover.
- **A morph the game reports no progress for is timed from observations** (#109, the owner, 2026-11-08), over the
  step the game reports carrying out its order: from the step of the observation before the first that shows it, so
  that one step a turn is always exact. This player's structures by their order, a cocoon of either side by its type;
  an enemy's structure shows nothing, and raises. `Unit.morph_progress` gives a `Progress[T]`, one generic class for
  how far making something has got (the owner), which `InProduction` and `Research` subclass, over an optional
  `producer` on it, with their times required, over `None` for a warp-in: a warp-in is timed by how fast it rises from
  the observation before the one that first lists it, 1/256 a step for 256 steps and 1/80 for 81. A morph or a warp-in
  not seen to begin, which only a scripted first observation has, raises, with no code of its own (the owner, over
  taking it as just begun).
- **With the fog off, what the player knew stays as it was** (#163): `api.units`, `Visibility` and every event read
  as with the fog on, and the whole game is `api.all_units`, which raises `FogOnError` with the fog on, over marking
  the unseen in one list.

### The package and its data

- **Only `__init__.py` and `__about__.py` stand at the top of `src/sc2nachos`** (#205). Everything else is in a
  package, most named for the `Api` attribute whose type it holds, over nesting packages, and `_shared` imports
  nothing else of NachOS.
- **`api.data` is static**: the game's tables, read once. Anything that depends on the game's progress is read from
  the state, never written into `api.data`.
- **A build time the tables get wrong is corrected in `api.data`** (#109, the owner): the lurker's 403 steps and the
  archon's 192, measured, beside the cost overrides, over leaving their progress unknown.
- **A tile is a frozen dataclass, not a tuple** (#137): as a tuple it passed pyright as a point and indexed numpy as
  an array index. Nothing checks at run time that a point is a point: pyright catches a tile or a unit given as one,
  and only what cannot work otherwise, `in`, a grid's index and `Point`'s arithmetic, raises `not_a_point_error`.
- **Not in NachOS**: an upgrade's progress as one number, which `api.researches` holds (#131); the repair rates, each
  one division of `total_repair_cost`; a forecast of when tech is there; a getter beside `api.data.units[t]`;
  `api.enemy.units`, which stays `api.units.enemy` (#121); and the numeric helpers `clip`, `lerp`, `remap`, `damp`,
  `s_curve` and `sticky_round`, which went back to AvocaDOS (#194).
- **The demo bot, `examples/demo_bot.py`, is the library's example** (#125). It beat `Computer` at `MEDIUM` in all
  21 games of the pool's 7 maps against each of the 3 races before #202 gave it speed mining, and has not played them
  since (Next). What it needs that NachOS lacks goes into NachOS first, planned with the owner one gap at a time.

### The map

- **`api.expansions` is on the api, not on `GameMap`** (#122), which stays a pure reading of the protos and knows
  nothing of units. Where a townhall may stand is measured, never taken from python-sc2's distances, and the
  blockers are every neutral unit but a resource, with no `NeutralUnit` class.
- **`api.map.pathing` stays as the game sent it at the start** (#151): the grid to route on is `api.pathing.grid`, the
  game's own, asked for lazily, over NachOS laying footprints on the terrain. `Grid`'s methods know no structures, and
  paths on a grid come from semita, a package of its own, so that NachOS ships no compiled code (#148).
- **The placement grids keep what stands apart from what is reserved** (#123): `dynamic` and `reserved`, structures
  only.
- **NachOS does not scout** (#209): `api.enemy.start_location` raises `NotReportedError` on a map with more than two
  starts until the bot calls `Enemy.assume_start_location`, and the order to take expansions in stays the bot's too.
  A `StartLocation`'s reads raise where there is none, over `None`.
- **What this player sees is one `Vision`, at `api.vision`** (#227): `in_sight`, `explored` and `last_in_sight`,
  -1 for a tile never in sight, and one read, `time_unseen`, a length of time over a tile or an area,
  `GameTime.INFINITE` for one never in sight, over `None`. Not an extension, no aliases for the old grids, and
  `api.creep` stays out.

### Running, recordings and replays

- **One `run_from_command_line`** plays a ladder game when given `--GamePort` and a local one otherwise. Its own
  arguments, such as `--RealTime` and a local game's `--ComputerRace`, are named in the ladder's style, over
  kebab-case and a `nachos` prefix, and one it does not read is left for the bot (#146). The runners are functions
  taking a built api, never methods of `Api` (#147).
- **NachOS declares numpy 2.0 or later** (#193), since the aiarena ladder's bot image carries numpy 2.0.2 on Python
  3.12, which its zip does not upgrade through pip.
- **`PlaybackTransport` stays strict** (#154): `PermissivePlaybackTransport` is a sibling, never a keyword on it or a
  subclass, which would break the strict class's promise to raise.
- **A replay played back names its player**, `player_id` 1 or 2, with no default (#160). An order, a chat, a drawing
  or a camera move in one gets no special code: the game refuses it, and `play` raises.
- **The corpus is NachOS's test data, never installed with the package**: a bot records its own games for its tests,
  and makes units through `sc2nachos.testing`, importing nothing of s2clientprotocol (#196).
- **The API reference is pdoc's**, from the docstrings, over MkDocs, which links no name in plain backticks, and is
  published at each release, not at each merge (#198).
- **The tests run on several workers when asked** (#235): `-n 8` for the in-game tests and `-n auto` for the unit
  suite, as AGENTS.md says, and `-n 4` in CI and the release (the owner, over making it the default and over keeping
  CI serial). Each worker's in-game clients take ports of a block of its own (the owner, review of #235, over leaving
  `free_port`'s window and over a fix in the library).

### Extensions

- **Extensions are the one place for opt-in features** (#201), named over plugins, which suggest changing existing
  methods. A bot hands `Api(extensions=Extensions)` its own class, which builds each extension.
- **An extension's handlers run before every handler of the bot's or after every one** (the owner, 2026-11-04, #232),
  at `EventPriority.BEFORE` or `AFTER`, which only a handler subscribed while NachOS builds the extensions takes and
  which it must take; over reusing `HIGHEST` and `LOWEST`, where an extension's `TurnEvent` handler would still run
  after a bot's handler of the turn's other events. Upgrade inference runs before the bot, and speed mining after it
  (the owner, over before it), as #231 keeps it: it sees every order the bot gives in the turn before ordering any.
- **Speed mining takes workers only through `mine`, `collect` and `release`** (#202, #208), and only at a base with a
  finished townhall of this player's at the expansion's center: no long-distance mining, and no taking over a bot's
  plain gathers by itself, which the bot's own gathers could undo.
- **Speed collecting** (#222) gives a collector both moves on the line between the centers, its trip read off its
  order, since rich gas shows no cargo. One coming out stays on its plain return until past every collector of its gas
  building going in on its move, and is taken off a move it was sent lately (the owner, review of #222): lanes,
  waypoints, the one going in yielding, and moves by the count of collectors were each measured to collect less. The
  rule holds for every race, measured with SCVs only.
- **Speed mining picks every gas building, and releases the workers costing least** (the owner, 2026-11-04, #229), so
  that a bot holds only how many mine and how many collect. `collect` fills gas buildings to two, at the expansion
  nearest the worker with one under two, nearest its center first, before a third at any, which goes to the expansion
  nearest the worker with one under three, farthest from its center (the owner, over the farthest from its townhall at
  any base). `release_collectors(count)` and `release_miners(count)` release in the reverse of the picks, one the bot
  has ordered first (the owner, over a count to keep and over a read the bot passes to `release`).
- **Speed mining lets a worker go once the bot orders it elsewhere** (#231), in that turn, reversing #201 (the owner,
  review of #229, over keeping such a worker and ranking it first in the releases), so that a number kept is true. Run
  after the bot, it reads only the orders issued to a worker in the turn, telling its hand-overs' gathers and returns
  by their kind (the owner, review of #231, over marking its orders through `Order.data`); a refused order lets the
  worker go too, as documented (the owner, over reading the refusals).
- **Speed mining is a package**, a class to a file, as upgrade inference is (#225).
- **Upgrade inference is an extension** (#206), and the core always reads the levels the enemy's units report.
- **Upgrade inference reads each turn's observation on `TurnStartEvent`, and not the one the game ended on** (the
  owner, review of #234, over reading that one on `GameEndEvent` too, as #206 did): `api.enemy.upgrades` at the end
  holds what the turns showed, and the levels the core read off the last observation.
- **Upgrade inference hands out what it adds to `api.enemy.upgrades`** (the owner, 2026-11-04): an
  `EnemyUpgradeAssumedEvent` for each upgrade, an `AnyUpgradeEvent`, at once as it reads, and none for what the bot
  assumes or for the levels the core reads.
- **Speed upgrades read off how fast enemy units move**, built in #226 (the owner, 2026-11-04, one question at a
  time). The rule the owner asked for is the simplest code that is right at least 99.9% of the time, cheap in the hot
  loop and with few code paths:
  - An option of `UpgradeInference`, on by default (the owner, over always on and over an extension of its own),
    `include_speed_upgrades` (the owner, planning step 3, 2026-11-04, over `speed_from_movement` and `from_movement`).
  - The rows give 12 upgrades a speed. Charge stays read off its buff, and the other ten are read off movement:
    Metabolic Boost, Glial Reconstitution, Centrifugal Hooks, Muscular Augments, Anabolic Synthesis, Pneumatized
    Carapace (overlord, transport overlord and overseer), Hyperflight Rotors, Gravitic Boosters, Gravitic Drive and Flux
    Vanes. Tunneling Claws, the twelfth, is open (below).
  - Each enemy unit of the types those ten speed up keeps a window, where and when it began. It begins again when the
    unit is not visible, is on another side than when first seen, has no `velocity` after a gap in the observations (a
    transport, a nydus, out of sight), or wears an acceleration zone's buff. A burrowed roach does not vote: its
    unupgraded speed is 0, so one standing still would vote against Glial Reconstitution. A hallucination votes as the
    unit it copies, since it moves at its owner's speed, upgrades included (the owner, planning step 3, over leaving it
    out, which would only drop votes).
  - A window of about a second compares the distance covered with two predictions, without the upgrade and with it,
    each summed step by step at the speed for the ground the unit stood on: Zerg ground types count creep too (the
    owner, over measuring off creep only), from a table measured in game, since the rows hold no creep speed. Where the
    distance falls between the two, as `u`, 0 without and 1 with: within `x` of 1 is a fast vote, within `x` of 0 a slow
    one, anything else none (the owner: within a small tolerance of the expected upgraded speed, over faster than
    allowed). With `x` below 0.5 the bands never overlap. A window does not vote where the two predictions are closer
    than the measured precision can tell apart, as for an ultralisk on creep, which Anabolic Synthesis speeds by only
    3% (tool `sweep_movement`).
  - A slice is the votes of one upgrade's units in one window's steps, on the one clock, and the first with a fast
    vote assumes the upgrade for good, from the game's first step (the owner, 2026-11-04, after #233). It reverses the
    score #226 built, which `git show 5ab07a2:.claude/plans/next-steps.md` holds in full: the probability that the enemy
    has the upgrade, by Bayes' rule on each slice from a floor of 1%, assumed at 99% and forgotten below 50%, the three
    arguments of `UpgradeInference`, and no vote before the earliest the upgrade could finish. By the rule of three over
    every slice the replays gave (#224), none of the 69,452 from an enemy that lacked an upgrade had a fast vote, after
    the earliest it could finish, and none of the 33,558 from one that had it only slow ones, so forgetting never
    fired. Before the earliest finish is unmeasured, where the owner judged a wrong assumption very unlikely; tool
    `replay_movement check` counts it. In #224, no wrong assumption in 433 playbacks, below 0.7% a playback. One fast
    vote counts beside any number of slow ones: the units of a slice move as one, and one with the upgrade can keep pace
    with slower units, as the one wrong vote of #224 did beside two fast ones, while one without it never moved
    steadily at the faster speed. A bot that wants one gone calls `Enemy.forget_upgrades`.
  - The window's length is an argument of `UpgradeInference`, `speed_window`, a `GameTime` of 22 steps by default
    (the owner, 2026-11-04, over `speed_window_steps`), and so is the band, `speed_tolerance`, 0.05 of the gap by
    default (the owner, review of #234), above 0 and below 0.5 so that a unit counts only nearer its speed with the
    upgrade: the replays checked these defaults alone.
  - Left out, as the checks above cover them: guards for a reaper's grenade, a force field, a falling tower, Graviton
    Beam, a hydralisk's lunge and the facing. One comes back only if the replays show the checks miss it.

### Kept as they are

Code the owner chose to keep as it is, which a review could otherwise propose to change.

- **A few nanoseconds are not worth a second code path or a duplicated check** (#175): "I don't think we need to
  chase 20 ns".
- **`UnitTypeData.build_time` is a stored field beside `_unrounded_build_time`** (#189), over a property derived from
  it, which built a `GameTime` at every read.
- **Speed mining's `_is_returning(order)` stays** (#208), over `OwnUnit.is_returning`, some 8 times slower.
- **Speed upgrades' cost a turn stays as measured** (the owner, step 4 of #226), 6.9 µs a turn over AvocaDOS's recorded
  games, over reading creep a tile at a time without the map, some 11 µs on a Zerg turn, and over walking `api.units`
  without building `api.units.enemy`, 1 or 2 µs: about a tenth of the rest of upgrade inference.
- **The fog-off view has no guard against `id_provider` giving an id a unit already holds**, since no path to it was
  found, and copies each fog-off observation whole (#163).
- **`playback` names a `ReplayPlayback`, and `game` a `GameProcess`** (the owner, #176, 2026-11-04), over
  `replay_playback` and `game_process`, which the rule that a name follows its class would give.

## Next

- The build planner (#239) drops `_note_morph_starts` and `morph_starts` once its branch takes main, which times a
  command center's morph, and reads `InProduction.product` and `fraction` where it read `type_id` and `progress`. It
  drops its own `usual_form` too, for `UnitTypeData.base_form`, which makes a hellbat a hellion where it kept them
  apart.
- Two miners of a field may meet head on, both on their moves, as two collectors of a refinery did before #222
  (unmeasured): a sweep of one and two SCVs a field, on plain gathers, speed mined and with the yield, to plan with
  the owner. The yield goes into `mine` only if it is measured to mine more.
- #202's checks in game, not run before it merged: the demo bot's 21 games. Its integration test passed in #203, and
  an orbital's morph, which let its base's workers go, is fixed there.
- **Speed upgrades read off how fast enemy units move** (above), a step at a time, each with the owner:
  1. Done in #212: `tools/sweep_movement.py` measured each type's speed with and without its upgrade,
     the Zerg ground types on and off creep, and a sentry's hallucinations, in `docs/reference/game-behavior/units.md`
     and `upgrades-and-tables.md`. A ground unit runs at its row's speed from the first step, every window to 0.0001
     a second; a flying unit within 0.15% once up to speed, after 10 to 32 steps; along a diagonal up to 0.6% slower.
     Creep makes a Zerg ground unit 1.3 times as fast, but Muscular Augments and Anabolic Synthesis add less on it, so
     the creep table holds both speeds on creep for those two. A hallucination moves at its owner's upgraded speed, so
     the reason it was not to vote is gone. Proposed from it, to settle with the owner at step 3: `x` 0.1 of the gap,
     at least 1.8% of the speed for the closest, 18%, against the 0.6% a diagonal costs; a window of 22 steps, which a
     flying unit's speeding up spoils for one or two windows; and no vote where the gap is under 10% of the speed, as
     for an ultralisk on creep, 3%.
  2. A replay tool, in two pull requests. In the first, #214, `MovementWindows`, private in
     `sc2nachos/extensions/upgrade_inference/_movement_windows.py` until step 3, upgrade inference having become a
     package (the owner, review of #214), measures each visible enemy unit of the 12 types over windows of 11 steps on
     one clock: the distance moved, the two predictions, and the least and the greatest share of the gap a single
     observation's move came to. `tools/replay_movement.py` plays a random sample of every bot's
     games of AI Arena's 2026 Season 1 back as each player (the owner, 2026-11-04, over AvocaDOS's games alone, for a
     wider view), each playback's own upgrades being the truth for the other's, and keeps the windows raw, so the band,
     the window and the least gap come from the data. Its pilot, on two samples of 100 games played as each player,
     the first 100 of the shuffled order of seed 1 and the 100 that `random.sample` picked with seed 1 before, proposes,
     to settle with the owner at step 3:
     - Steady votes: a window votes only if every single observation's move falls in the band too. Over 22 steps
       within 0.1, no window voted fast for an enemy that lacked the upgrade, of 189,498 such windows, but 12,783 of
       the 91,784 slow votes came from an enemy that had it, since an upgraded unit that stops for part of a window
       covers what one without it does. Steady, 5 votes were wrong, all slow: four zerglings with Metabolic Boost under
       a mothership's Time Warp, which slows to 0.6, and an upgraded overlord kept at a slower pace.
     - Within 0.05 of the gap rather than 0.1: steady, none of 63,941 votes was wrong, 90% of the steady votes within
       0.1.
     - 22 steps, as proposed at step 1: over 11 steps, 3 steady slow votes within 0.05 were wrong; over 44, none.
     - No vote while a unit wears a buff that changes its speed, Time Warp among them, as for an acceleration zone: a
       slow can bring an upgraded unit into the slow band, as Time Warp brings an overseer with Pneumatized Carapace to
       0.1 of the gap above its speed without.
     - The Parasitic Bomb's dummy unit, an id the curated unit types left out, stopped 4 playbacks of the second
       sample (#215).
     - The buff a unit a stasis ward traps wears, an id the curated buffs left out, stopped the playback of 4719987 as
       player 2 (#223), and is `STASIS_WARD_TRAPPED` now. The unit stands still for 480 steps, so the buff is one that
       changes its speed (above).

     In the second, `check` fits the beta-binomial on the pilot's 200 games and runs the score over others, as
     upgrade inference is to, timing each upgrade from the first sight of one of its units moving after it finished
     (the owner, over timing from the finish, which measures the enemy's play more than the evidence) to its
     assumption. The owner capped the run at an hour, over the seven hours that 3,000 playbacks in which a wrong
     assumption is possible would take: games 101 to 550 of the shuffled order of seed 1, 900 playbacks in 48 minutes,
     24 at once. Two games failed, one on the stasis ward's buff (#223) and one whose client dropped the connection,
     leaving 896 playbacks. By the rule proposed above, a slice being one window's steps:
     - Votes: 1 of 153,060 was wrong, a slow vote from a zergling with Metabolic Boost on creep at a roach's speed
       with Glial Reconstitution there, in a slice with two fast ones. None of the pilot's 63,477 was. Over 11 steps,
       6 steady fast votes came from an enemy that lacked the upgrade, and 12 slow ones from one that had it; over 44,
       none (tool `replay_movement summarize`, which counts votes under speed-changing buffs and before the earliest
       finish too).
     - The fit: with no wrong vote to learn the spread from, it takes the most clustered tried, 0.99, so a slice counts
       about as one vote, one fast slice taking 1% to 99.8%, and a slice of fast and slow votes counts for little
       either way, as the one wrong vote's did. Each mean then comes to about half a vote over the slices fitted on,
       so how strongly a fast slice counts, about 41,000 to 1, follows from the 20,351 slices of enemies that lacked an
       upgrade, not from an error seen: constants for step 3 fitted so would grow with the games fitted on.
     - No wrong assumption in the 433 playbacks in which one was possible, below 0.7% a playback (95%), and no wrong
       forgetting in the 203 in which a slice followed an assumption, at each setting. Every one of 24,046 readings
       at 0.99 or more was of an enemy that had it.
     - From 1%, assuming at 99%, as from 5% or 14% at 99.9%: their odds between floor and threshold differ, about
       9,800, 19,000 and 6,100 to 1, but one fast slice clears each. 358 of 417 finished upgrades were assumed, a
       median of 3.5 seconds from first sight of one of its units in a window it moved in, 90% within 54. Of the
       other 59, 29 had no unit seen moving after it finished and 30 none moving steadily at full speed. From 1% at
       99.9%, whose 99,000 to 1 takes two slices, 338, a median of 9.0 seconds, 90% within 83.

     Settled when planning step 3 (above): a floor of 1%, assuming at 99% and forgetting below 0.5. Reaching 0.1% a
     playback would take some 2,700 games more, nearly five hours, 24 at once.
  3. Done in #226: the option, `UpgradeInference(api, include_speed_upgrades=True)`, by the rule above and the
     pilot's proposals: windows of 22 steps, steady votes within 0.05 of the gap, none where the gap is under 10% of
     the speed, none under a buff that changes speed (only Time Warp's slow measured), and none before the earliest
     finish, from `api.data`. `MovementWindows` hands over the windows ending at an observation together, once a
     window's steps, each saying whether it covered its whole block; a private `_speed_upgrades.py` turns them into
     votes and the score, and assumes and forgets through `api.enemy`. The corpus now shows Metabolic Boost in its two
     games where the computer's zerglings attacked, within 0.4% of their boosted speed. It observes every 16 steps, so a
     window holds one or two moves, and the docs say a bot observing fewer than every step tells a steady unit less
     well from one that stopped; how often the replays' rule then errs is not measured. `check` now imports the rule
     and scores each slice as upgrade inference does: over #224's 896 playbacks it gives what the fitted model gave at
     1% and 99%, no wrong assumption in 433, no wrong forgetting in 203, and 358 of 417 upgrades assumed, a median of
     3.5 seconds from first sight.
  4. Measured in #226, `tools/time_speed_upgrades.py`, the recordings played back with no bot: over AvocaDOS's seven
     recorded games, 96,552 turns at 250 to 460 µs a turn, the speed code takes 6.9 µs a turn, 4.7 to 12.3 by game,
     99% of turns within 8.5 to 46.6 µs and the slowest 98.5 µs, measuring 11 units; it measured 0.7 units a turn,
     18 at most. Of that, about 4 µs a turn is building `api.units.enemy`, a filter over every unit, even with none in
     sight, and 11 µs decoding the creep map, in an observation in which a Zerg ground unit's window goes on, which
     anything else reading creep then shares. The corpus, 0.1 units a turn: 4.4 µs. The rest of upgrade inference
     takes 80 to 100 µs a turn in three of AvocaDOS's games, decoding the buffs of every visible unit each turn.

  Open, to settle with the owner at the step that needs it (proposed, not decided):
  - Tunneling Claws later, with Blink and Shadow Stride, since a burrowed roach has two upgraded speeds, Tunneling
    Claws alone or with Glial Reconstitution, which would need a second code path.
- Beside the migration, **writing a replay's whole game for salsa**, a pull request at a time, in the order salsa's
  plan holds. salsa, `maxnus/salsa`, holds the `.salsa` format whole and depends on sc2nachos, its writer reading the
  protocol through NachOS's launch and protocol layers alone (maxnus/salsa#3). Next: starting a replay on its own
  build with its map, public, for salsa's writer: `open_replay` and `open_replay_recording` (#176, open since
  2026-10-25).
- Apart from salsa, and not waited on by it: an action's target on a copy the fog-off view never lists reads as not
  reported, as an order's does (the owner, 2026-10-25, over `None`). A player's order on a structure or mineral field
  it remembers in the fog names the tag of the game's copy, which the game lists only with the fog on, while NachOS's
  fog-off view makes copies of its own. Probably settled by #192, since `State.actions` reads tags through
  `get_by_tag`, a tag never reported reading as not reported: confirm with one `run_replay(..., disable_fog=True)` and
  an `OwnActionEvent` handler, which raised `UnknownTagError` before, then drop this item.
- **Ids AI Arena's replays report that the curated ids leave out**, found playing back matches 4805282 and 4846375
  raw while curating the parasitic bomb's dummy, and 40 more of the season with the fog off, 4 steps a turn
  (2026-11-04). None stops a playback, since each is read only when a bot reads it, but each raises then: buff 59,
  `ViperConsumeStructure`, worn by the hive a viper consumed, from step 14,132 and 15,245; ability 1662,
  `BurrowCreepTumorDown_BurrowDown`, a creep tumor's own order as it burrows by itself, which `OwnUnit.orders` raises on
  for a Zerg bot's tumors (5 of the 42 games); and buffs 39 to 42 (17 of the 42), each worn by every collapsible
  tower of one kind, in the fog, in the first observation of a playback with the fog off, and never again, nor ever
  in either player's own view: `CollapsibleRockTowerConjoinedSearch` by `CollapsibleRockTower`,
  `CollapsibleRockTowerRampDiagonalConjoinedSearch` by `CollapsibleRockTowerDiagonal`,
  `CollapsibleTerranTowerConjoinedSearch` by `CollapsibleTerranTower` and
  `CollapsibleTerranTowerRampDiagonalConjoinedSearch` by `CollapsibleTerranTowerDiagonal` (4686890 and 4750796, on
  MagannathaAIE, every 2 steps). `Api.all_units` shows that first report visible, so its towers' `buffs` raise in the
  first turn. The 40 reported no unit type the curated ids leave out.

## 1. Waiting until something needs them

M5's swap is done, so the items below that waited on it, a narrow context for a unit and shortcuts for common orders,
can be planned with the owner. Keeping the selections still waits on M5's exit gate.

- A launch passes no data version, only the base build: a replay whose data version is not its base build's default
  may be refused on Windows or macOS (unverified; the owner, review of #160, over passing it untested).

- A timed unit's time left where the game reports none: a force field's, a reaper's grenade's, and the enemy's MULEs'
  and broodlings', from a lifetime a sweep measures and the unit's first listing. A force field and a grenade stay
  units, over effects as python-sc2 makes them, since the game times neither and only a unit is followed from one
  observation to the next. The owner put it after the migration (2026-10-25). `tools/sweep_timed_life.py` measured
  those four; the other timed units, how to give the time, and what it reads for a unit first seen already standing,
  are to plan then.

- Keeping the selections a turn's units hand out, rather than building them at every read, about 15 ns a unit: the
  owner chose to decide it once the swap runs and AvocaDOS's exit gate measures what its reads cost (review of
  AvocaDOS #49). The owner's two ideas: one key for a set of selections in any order, so `own.structures` and
  `structures.own` are built once, and lazy views, so a chain filters once. A view must still be a `Sequence`.

- A narrow, read-only context for a unit in place of its tracker, through which it reads the tables, the upgrades,
  units by tag and builders (an agent's review of the codebase, 2026-10-20; the owner chose to wait for M5 to show a
  need). The whole of `_tracking` shares the tracker this way, and tests build one in a line. Since #240 the tracker
  also holds the order book `OwnUnit.issue` orders into, which such a context would carry too.
- Shortcuts for common orders, `issue_move`, `issue_attack` and `issue_gather`, with self types on new groups,
  `CanMove` and `CanAttack`, generated from the abilities the tech tree records each type offered; and methods on
  `Units` for a group. Held until a bot shows a need (the owner, 2026-11-08, building `OwnUnit.issue` alone in #240): a
  shortcut saves an import and a few characters, its self type checks only a unit whose type was narrowed, and the
  game already reports the mistake it catches in `api.action_failures`. Found while planning: `MOVE`, `PATROL` and
  `HOLD_POSITION` are offered to the same 81 types and `ATTACK` to 83, an SCV, a medivac and an overlord among them,
  so `CanAttack` is no army; a group of exactly those types leaves out `Worker` over the burrowed drone, so a burrowed
  form was to count as its unit. Since #237, a loop of `unit.issue` goes out as one command, so methods on `Units`
  save no APM. They would not make one id per action unnecessary, since ids also show in `unit.orders` and
  `unit.abilities`. A callable target per unit is to reconsider with them (review of #125). `train`, `build` and
  `research`, which translate an id, are in (#245).
- An enemy structure's add-on, found by position: the game never names it to the enemy, but lists the add-on itself
  at its host's position plus (2.5, -0.5) (`docs/reference/game-behavior/units.md`). `add_on`, `has_reactor` and
  `has_tech_lab` would then move to `Unit` (the owner, review of #185, over doing it there).
- Self types for energy, cargo and detection, as #185 gave the reads whose types the tables or a definition settle
  (the owner, 2026-10-29, over doing them in #185, and over never). No group holds their types, and the corpus shows
  too few: they need new groups, such as `WithEnergy`, `Transport` and `Detector`, from a sweep that creates every
  type and records its energy, cargo and detection.
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

## 2. Around a release

Before it, run AvocaDOS's tests against the commit to release, `uv run --extra dev --with-editable <this checkout>
python -m pytest` in AvocaDOS, so that its notes say what a bot has to change. After it, AvocaDOS takes it in a pull
request of its own, which bumps its pin and `.github/sc2-games.yml`'s NachOS ref, as its `AGENTS.md` says, and runs
its tests on it; a nachOS pull request is recorded there no more.
