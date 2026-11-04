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

As of 2026-11-04:

- **M4 is done** but for slice 7, typed order methods on `OwnUnit`, which is deferred (section 1).
- **M5's swap is done**: AvocaDOS runs on NachOS, on the ladder since its v1.4.0 (2026-11-01). Its exit gate, a day
  on the ladder and the comparison of `AverageStepTime` with the baseline (AvocaDOS's plan, M5), is open.
- **M6 is under way** (section 3): what is left is the owner's setup for releasing, the first release, and AvocaDOS
  pinned to it.
- The consuming bot is AvocaDOS ([github.com/maxnus/AvocaDOS](https://github.com/maxnus/AvocaDOS), branch
  `main`). Its `docs/plans/nachOS-plan.md` is the milestone plan (M1 to M6) and records every nachOS PR. It has no
  python-sc2 left (AvocaDOS #68), its game map searches run on `Grid` (AvocaDOS #70), and its expansions and start
  locations (AvocaDOS #84) and speed mining (AvocaDOS #76, #85) are NachOS's.

## Decided, so not to be proposed again

The issue, discussion or pull request named holds the reasons, and the alternatives weighed.

### Orders

- **NachOS checks nothing an order needs** (#46): minerals, vespene, supply, queue room and tech are the game's to
  judge and the bot's to budget, and `api.action_failures` lists what the game refused. No budget, no `can_afford`,
  no `api.orders.cancel`: a built version with all of that was dropped as too complicated for what it bought.
- **No order priority, no hold and no order state.** A unit takes the last order a turn gives it, a structure's train
  included, and a bot that ranks its orders puts the rank in the order's `data`. NachOS never holds an order until a
  unit can start it (#126, reversing #115 to #120): that belongs in the bot, or in a layer above the order book.
  Nothing follows an order once sent (#69, #115): the `Order` is a handle, and the bot reads its units, events and
  `api.action_failures`.
- **An unqueued order a unit is already carrying out is not sent to it** (#68), though a train or a research always
  is. `force=True` sends it anyway, dropping the unit's queue, so there is no `clear_queue` (#77). A production
  order gets no "replace": a cancel frees no queue slot and no minerals in the same step (#43).
- **One public id per action** (#103 to #107): a family that does one thing for different performers is one id, its
  per-unit ids only in `RawAbilityId`, with no `GENERAL_` prefix, and the leveled researches keep an id per level. An
  ability that behaves differently by its target is split into custom ids, one per behavior: `UNLOAD` and
  `UNLOAD_AT`, `LIBERATOR_SIEGE_AT` (#101, #126).
- **`api.orders.issue` is the one way to order**, one unit or many, with no `issue_all` (#130): what every unit of a
  command carries out goes out as one command naming them all, anything else as a command to each. A research, or a
  placement at one target, given to several raises `ValueError`, since only one of them can carry it out.

### Units and their types

- **python-sc2's conveniences that bots generally use belong in NachOS** (review of AvocaDOS #52, #175): a bot
  moving over will expect them. They are named and shaped as NachOS's own, as AGENTS.md has it.
- **A unit's type stays a type parameter**, `Unit[UnitType.Refinery]`, over a class per type, which would need an own
  and an enemy class of each of the 342 types and groups and made reads slower (#185). A read only some types have
  takes a self type, but the shield reads, `construction` and `rally_targets` stay on every unit: asking a Zerg
  unit's shield is no type error. No `Unit.is_type(group)`: a type guard cannot narrow the object a method is
  called on, so `UnitType.X.includes(unit)` stays the one spelling (#202).
- **`Units` is immutable, with no operators** (#35, #182): `combined_with` and `without`, by NachOS id. Every handler
  shares `api.units`, and its covariance and its id index need it unchanged.
- **NachOS names only the categories the game draws**: no `army` and no `mobile` (#157). What counts as army is the
  bot's call, through the type groups, `of_type` and `excluding_type`.
- **A unit type names what makes it, not what the tables alias it to** (#75): no `UnitTypeData.base_type`, the
  game's `unit_alias`, which aliases an adept's shade to the adept. `morphed_from` gives a form's source.
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
- **With the fog off, what the player knew stays as it was** (#163): `api.units`, `Visibility` and every event read
  as with the fog on, and the whole game is `api.all_units`, which raises `FogOnError` with the fog on, over marking
  the unseen in one list.

### The package and its data

- **Only `__init__.py` and `__about__.py` stand at the top of `src/sc2nachos`** (#205). Everything else is in a
  package, most named for the `Api` attribute whose type it holds, over nesting packages, and `_shared` imports
  nothing else of NachOS.
- **`api.data` is static**: the game's tables, read once. Anything that depends on the game's progress is read from
  the state, never written into `api.data`.
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

### Extensions

- **Extensions are the one place for opt-in features** (#201), named over plugins, which suggest changing existing
  methods. A bot hands `Api(extensions=Extensions)` its own class, which builds each extension, and an extension's
  handlers run before the bot's of the same priority.
- **Speed mining takes workers only through `mine`, `collect` and `release`** (#202, #208), and only at a base with a
  finished townhall of this player's at the expansion's center: no long-distance mining, and no taking over a bot's
  plain gathers by itself, which the bot's own gathers could undo. Gas gets no move until it is measured (Next).
- **Upgrade inference is an extension** (#206), and the core always reads the levels the enemy's units report.
- **Speed upgrades read off how fast enemy units move**, planned, not built (the owner, 2026-11-04, one question at a
  time). The rule the owner asked for is the simplest code that is right at least 99.9% of the time, cheap in the hot
  loop and with few code paths:
  - An option of `UpgradeInference`, on by default (the owner, over always on and over an extension of its own).
  - The rows give 12 upgrades a speed. Charge stays read off its buff, and the other ten are read off movement:
    Metabolic Boost, Glial Reconstitution, Centrifugal Hooks, Muscular Augments, Anabolic Synthesis, Pneumatized
    Carapace (overlord, transport overlord and overseer), Hyperflight Rotors, Gravitic Boosters, Gravitic Drive and Flux
    Vanes. Tunneling Claws, the twelfth, is open (below).
  - Each enemy unit of the types those ten speed up keeps a window, where and when it began. It begins again when the
    unit is not visible, is on another side than when first seen, has no `velocity` after a gap in the observations (a
    transport, a nydus, out of sight), or wears an acceleration zone's buff. A burrowed roach does not vote: its
    unupgraded speed is 0, so one standing still would vote against Glial Reconstitution. Nor was a unit known to be a
    hallucination to, as it might not move at its owner's speed; it does, so whether it votes is open (below).
  - A window of about a second compares the distance covered with two predictions, without the upgrade and with it,
    each summed step by step at the speed for the ground the unit stood on: Zerg ground types count creep too (the
    owner, over measuring off creep only), from a table measured in game, since the rows hold no creep speed. Where the
    distance falls between the two, as `u`, 0 without and 1 with: within `x` of 1 is a fast vote, within `x` of 0 a slow
    one, anything else none (the owner: within a small tolerance of the expected upgraded speed, over faster than
    allowed). With `x` below 0.5 the bands never overlap. A window does not vote where the two predictions are closer
    than the measured precision can tell apart, as for an ultralisk on creep, which Anabolic Synthesis speeds by only
    3% (tool `sweep_movement`).
  - The votes of a slice are one observation for each upgrade, k fast of the n units that voted, with a beta-binomial
    likelihood whose spread says how often a whole group comes out fast without the upgrade: 20 of 20 is strong, 1 of
    20 counts against it (the owner: the number of units, their share and the number of observations all count, over
    each unit alone). What a slice is, is open (below).
  - The score is the probability that the enemy has the upgrade, by Bayes' rule on each slice, in [0, 1] (the owner,
    over a clamped sum). No vote counts before the earliest the upgrade could finish: its research time, plus its
    researcher's build time where no player starts with a researcher, as every Zerg starts with a hatchery, which
    researches Pneumatized Carapace. After that it starts at a fixed `p0` and moves only with what is seen, `p0` being
    its floor too, so evidence against it from before the upgrade finished leaves no debt (the owner, over a prior
    rising with game time, a timing guess that is the bot's to make).
  - It is assumed at 0.999 and forgotten at a lower threshold of its own, so it does not flip back and forth (the
    owner, over assuming for good). It forgets only what speed assumed. The extension cannot see an upgrade the bot
    assumes after speed did, so speed can forget that one: a limit to document.
  - Without drift, the evidence is a likelihood ratio, and by Ville's inequality the chance that an enemy without an
    upgrade ever reaches 0.999 for it in a game is at most `p0`'s odds over 0.999's, about 1 in 100,000 from `p0` = 1%,
    if the likelihoods are right; each climb off the floor adds about as much. The bounds of a race's speed upgrades
    add up, Zerg's six among them, so for 0.1% a game `p0` stays under about 14%, and within that the replays choose
    it, trading safety against how much evidence an assumption needs.
  - Left out, as the checks above cover them: guards for a reaper's grenade, a force field, a falling tower, Graviton
    Beam, a hydralisk's lunge and the facing. One comes back only if the replays show the checks miss it.

### Kept as they are

Code the owner chose to keep as it is, which a review could otherwise propose to change.

- **A few nanoseconds are not worth a second code path or a duplicated check** (#175): "I don't think we need to
  chase 20 ns".
- **`UnitTypeData.build_time` is a stored field beside `_unrounded_build_time`** (#189), over a property derived from
  it, which built a `GameTime` at every read.
- **Speed mining's `_is_returning(order)` stays** (#208), over `OwnUnit.is_returning`, some 8 times slower.
- **The fog-off view has no guard against `id_provider` giving an id a unit already holds**, since no path to it was
  found, and copies each fog-off observation whole (#163).

## Next

- **Measuring speed-mined gas** (the owner, 2026-11-03), after #208: a script run through the `sc2` workflow, since
  no game runs in a cloud session, collecting plainly against with a move at the end of each trip, at one, two and
  three workers a geyser. The move goes into `collect` in a pull request of its own only where it collects more, and
  `_is_mining` and `_is_collecting` then become one check (the owner, review of #208). AvocaDOS waits on it no longer:
  its collectors are speed mining's since AvocaDOS #85.
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

     In the second, the run the pilot sizes: it fits the beta-binomial and `p0` on some of the games, and checks on
     the rest that a probability reads true (of moments at 0.99, about 99% had it), how often a game assumes an upgrade
     the enemy lacks, and how late each real one is found. No false assumption in n playbacks in which one was possible
     bounds the rate below about 3/n (95%). In 195 of the pilot's 400 the enemy lacked an upgrade while its units of a
     type it speeds up voted steadily within 0.05, so 3,000 take some 3,100 games: at the pilot's 13 minutes for 200
     playbacks, 16 at once, about seven hours.
  3. The option, with tests for both bands and a window between them, a creep edge, a gap in the observations, a side
     change, an acceleration zone, the earliest finish, 20 of 20 fast against 1 of 20, the floor, a mistake forgotten
     and an upgrade the bot assumed first, which is never forgotten. The extension's docstring, which says what it
     reads stays, and `docs/extensions.md` say what speed may forget.
  4. Its cost a turn over the corpus and AvocaDOS's recorded games.

  Open, to settle with the owner at the step that needs it (proposed, not decided):
  - The option's name; `from_movement` was proposed.
  - Whether a hallucination votes: it moves at its owner's speed, upgrades included (tool `sweep_movement`), so leaving
    it out only drops votes; proposed, it votes as the unit it copies.
  - What a slice is: proposed, fixed ticks of a second on one clock for every unit, a unit voting in a tick it was
    measurable through, so each unit keeps only its position at the last tick.
  - One pair of beta-binomial parameters for every upgrade, which `u` makes comparable, unless the replays show they
    differ; rare upgrades such as Gravitic Drive have too few votes to fit their own.
  - Tunneling Claws later, with Blink and Shadow Stride, since a burrowed roach has two upgraded speeds, Tunneling
    Claws alone or with Glial Reconstitution, which would need a second code path.
- M6, in the order of section 3.
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

M5's swap is done, so the items below that waited on it, a narrow context for a unit and typed order methods, can
be planned with the owner. Keeping the selections still waits on M5's exit gate.

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
  need). The whole of `_tracking` shares the tracker this way, and tests build one in a line.
- Typed order methods on `OwnUnit`, M4's slice 7, to plan with the owner once M5 is done (the owner, 2026-10-31).
  M4 deferred them (2026-10-18), since a type is offered at most 24 of the 312 abilities and pyright's completion
  would offer every unit all of them, and AvocaDOS's build orders hold ids. They would not make one id per action
  unnecessary, since ids also show in `unit.orders` and `unit.abilities`, and a callable target per unit is to
  reconsider with them (review of #125). Building them is easy, one `api.orders.issue` a method through the unit's
  tracker once it holds the order book, but their design needs the owner: a small generic set with self types, so a
  barracks has no `gather`; methods on `Units` for a group, which `issue` sends as one command; the "no second
  spelling" rule of `docs/maintaining/code-review.md`; and raising for a unit no order book sends, in a finished game
  or a replay. AvocaDOS keeps its own wrappers, so it will not use them soon.
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

## 2. After each nachOS pull request merges

Record it in AvocaDOS's `docs/plans/nachOS-plan.md`, in the next AvocaDOS pull request: a paragraph under M6's
"Status" saying what it settled and what AvocaDOS writes differently. AvocaDOS's tests (`uv run pytest`) install nachOS
from the checkout beside it, so run them on a nachOS pull request that renames or removes anything.

## 3. M6 (python-sc2 gone, NachOS released)

In AvocaDOS's plan, section "M6 — Delete burnysc2". Done: python-sc2 and `external/pathfinding` gone from AvocaDOS
(AvocaDOS #68, #70), its numeric helpers moved back into it (AvocaDOS #71) before nachOS deleted them (#194), its
tests playing its own games (AvocaDOS #72) and making units through `sc2nachos.testing` (#196, AvocaDOS #74), the
README and the API reference (#198), and the release workflow: publishing a GitHub release uploads it to PyPI, its
version read from its tag (#213, `docs/maintaining/releasing.md`). Next:
1. The owner's setup, once, that `releasing.md` lists: PyPI's trusted publisher, the `pypi` environment, and GitHub
   Pages with the `github-pages` environment deploying the tags `v*`.
2. The first release, 0.1.0, with the owner's permission; a pre-release, 0.1.0rc1, tries the workflow out first.
3. AvocaDOS pinned to it, in an AvocaDOS pull request: `sc2nachos==0.1.0` from PyPI, without the path to `../NachOS`;
   its ladder zip taking sc2nachos from the installed environment, as it takes `websocket`, not from NachOS's checkout;
   and its CI checking NachOS out no more, but for the maps its `.github/sc2-games.yml` reads from
   `../NachOS/sc2maps/AIE`, which need a home. Section 2's rule then becomes running AvocaDOS's tests when it moves to
   a newer release, not on every nachOS pull request.
