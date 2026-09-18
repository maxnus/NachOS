# Migrating from python-sc2

For bots moving over from [python-sc2](https://github.com/BurnySc2/python-sc2), the `burnysc2` package imported as
`sc2`. NachOS is not a drop-in replacement. This page lists the places where the obvious translation of python-sc2
code goes wrong. It covers what NachOS has so far, and grows with it.

## Running a game

- **Nothing is subclassed, and nothing is `async`.** Construct an `Api`, at import time if you like, and hand it
  to a runner along with the race it plays: `run_local("PylonAIE_v4", ApiBot(api, Race.TERRAN), Computer(Race.ZERG))`.
- **A turn is one step unless you say otherwise.** Use `run_local(..., steps_per_turn=4)` to match python-sc2,
  whose `client.game_step` defaults to 4. It is set for a game and cannot be changed during one.
- **`Computer()` defaults to very hard, with a random race and build.** python-sc2's `Computer` requires a race
  and defaults to easy.
- **A game holds one bot and at most one computer.** Every current map has two slots, and the game drops extra
  players without saying so. Two bots cannot share a process.
- **`run_local`'s `time_limit` calls the game a tie at the first turn at or past the limit.** python-sc2's
  `game_time_limit` waits for the first turn strictly past it. `run_ladder` takes no limit, because a bot ends a
  game early only by leaving it, and leaving concedes it.
- **A map name can resolve to a different file.** NachOS searches every folder under `Maps`. When several copies
  match, it takes the shallowest, and the first alphabetically among copies at the same depth. python-sc2 searches
  two levels deep and takes whichever match the filesystem lists first.
- **`run_ladder` takes the address and ports as arguments.** Reading `--LadderServer`, `--GamePort` and
  `--StartPort` from the ladder's command line is up to you.
- **One api plays any number of games.** Each game starts from nothing. Before the first game, everything that
  belongs to a game, such as `api.step` or `api.map`, raises `NotPlayingError`.
- **New: recordings.** Both runners accept `record_to=path`, which writes the whole conversation with the game to
  `path`. A `Client` over `ReplayTransport(Recording(path))` then plays it back with no game running. A run that
  is killed before it finishes leaves only part of the file.

## Events

- **A bot's code runs in handlers, not in overridden methods.** `on_start`, `on_step` and `on_end` are handlers of
  `GameStartEvent`, `TurnEvent` and `GameEndEvent`, subscribed with `@api.event.on(TurnEvent)`. A module-level
  function is subscribed as it is defined. A method is marked, and subscribed for an instance passed to
  `api.event.subscribe(instance)`, usually by its own `__init__`.
- **`on_step` is best kept as one `TurnEvent` handler** that calls the bot's parts in the order it wants. Handlers
  run in the order of their priorities, highest first, then the order they subscribed, which is hard to follow
  across many modules.
- **There is no `iteration`.** `event.step` is the game loop, and `every_steps` and `at_step` count steps, so a
  handler runs as often in game time at any `steps_per_turn`.
- **The last observation gets no turn, as in python-sc2, but it is taken in.** A `GameEndEvent` handler reads the
  game as it ended, where python-sc2's `on_end` sees the observation before it.
- **A handler cannot be `async`**, and one that is is refused when it subscribes.
- **Subscriptions outlive a game.** A function stays subscribed for the life of the api, and an instance until it is
  passed to `api.event.unsubscribe`, since both are held strongly. A bot that makes its objects afresh for each game
  unsubscribes the old ones, or they go on handling events alongside the new.
- **The `on_unit_*` hooks are events too**, handed out each turn between `TurnStartEvent` and `TurnEvent` in the
  order `sc2nachos.events` gives, and made only for a type something subscribes to:

  | python-sc2 | NachOS |
  |---|---|
  | `on_unit_created(unit)` | `OwnUnitCreatedEvent`, structures included |
  | `on_building_construction_started`, `_complete` | `OwnConstructionStartedEvent`, `OwnConstructionFinishedEvent` |
  | `on_unit_destroyed(tag)` | `UnitDiedEvent(unit)`, and `UnitFoundDeadEvent` for a structure found gone from its spot |
  | `on_unit_type_changed` | `UnitTypeChangedEvent`, for every unit |
  | `on_upgrade_complete` | `OwnUpgradeFinishedEvent` |
  | `on_unit_took_damage` | `UnitDamagedEvent`, for every unit in vision |
  | `on_enemy_unit_entered_vision`, `_left_vision(tag)` | `EnemyUnitEnteredSightEvent`, `EnemyUnitLeftSightEvent(unit)` |
  | nothing | `EnemyUnitFirstSeenEvent`, `UnitAllianceChangedEvent`, `OwnWarpInFinishedEvent` |
  | `state.chat`, `state.actions`, `bot.alert(Alert.X)` | `ChatEvent`, `OwnActionEvent`, and an event per alert, such as `NuclearLaunchDetectedAlertEvent` |

- **A starting townhall is never reported finished**, since it was never seen unfinished. The starting units are
  reported created on the first turn.
- **An event's step is when NachOS learned of it.** A morph in the fog is reported when the unit is next seen, and a
  structure that died there when its spot is.
- **A unit that dies does not also leave sight, and one that cloaks where it stands stays in sight.**
- **Damage is what a unit lost since the observation before**, less what it regained in between, and a unit that
  changed type took none.
- **An under-attack alert is raised only for what the camera does not show**, and not again for the same unit until
  it has gone some 6000 steps without being attacked, the same as in python-sc2. `UnitDamagedEvent` reports every
  turn a unit in vision loses health. `AlertError` and `TrainError` have no events, since nothing the sweep in `tools/sweep_alerts.py` did raised
  either.

## Errors

- **Everything NachOS raises for a failure of its own is a `NachOSError`.** python-sc2's `ProtocolError` whose
  `is_game_over_error` is true is `GameEndedError` here, and its `ConnectionAlreadyClosedError` is
  `ConnectionClosedError`. Where a built-in fits, the error is one too: `ConnectionClosedError` is a
  `ConnectionError`, and `ConnectionTimeoutError` a `TimeoutError`.

## Time

- **One game loop is one step.** `api.step` is python-sc2's `state.game_loop`, and `api.time` is its `time`.
- **`api.time` differs from python-sc2's `time` in the last bit on about a quarter of steps.** Neither 22.4 nor
  1 / 22.4 is exact in binary. python-sc2 divides by 22.4 and NachOS multiplies by 1 / 22.4, and the two results
  round differently. NachOS lands exactly on every whole second. python-sc2 overshoots the whole seconds in the
  top quarter of each power of two, so 15 seconds comes out as `15.000000000000002` and 60 as
  `60.00000000000001`. A test that compares the two needs a tolerance.

## Ids

- **The numbers are the same, but the names often are not.** The curated enums use the names players use:
  `PUNISHERGRENADES` is `CONCUSSIVE_SHELLS`, `TERRANBUILD_BARRACKS` is `BUILD_BARRACKS`, and `ADEPTPHASESHIFT` is
  `ADEPT_SHADE`. `UnitTypeId(old.value)` translates one to the other.
- **Two buffs are not the ids python-sc2's names suggest.** Concussive shells put `SLOW` on their target, never
  `DUTCHMARAUDERSLOW`, and an immortal's Barrier is `TAKENDAMAGE`, never `IMMORTALOVERLOAD`. NachOS calls them
  `BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW` and `BuffId.IMMORTAL_BARRIER`, and leaves the other two out. A buff is
  named after the unit that brings it on, so `STIMPACK` is `MARINE_STIMMED`.
- **The curated enums hold only what a melee game needs.** Converting an id they leave out raises `ValueError`, and
  `UncuratedIdError` is the one NachOS raises, naming the id and what the raw catalog calls it.
  `sc2nachos.ids.raw` holds every id, under Blizzard's own names.
- **An id the enums leave out stops the game as the observation comes in**, for a unit's type and for an upgrade this
  player holds, since both belong among the curated ids and a game that reports one is a gap to fill. A buff, an
  effect and an ability read off a unit raise when they are read. python-sc2 has no curation to be missing from.
- **An ability is named after the unit that performs it, then what it does**: `BARRACKS_TRAIN_MARINE`,
  `SCV_BUILD_BARRACKS`, `LARVA_TRAIN_ZERGLING`, `HATCHERY_MORPH_LAIR`, `ZERGLING_BURROW`,
  `ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1`. The performer carries the race, so the name drops it where
  `UpgradeId` has to keep it. python-sc2 keeps Blizzard's catalog spelling instead:
  `BARRACKSTRAIN_MARINE`, `TERRANBUILD_BARRACKS`, `RESEARCH_TERRANINFANTRYARMORLEVEL1`. An ability several
  units perform is named `GENERAL` in the performer's place -- `GENERAL_BURROW`, `GENERAL_LIFT`,
  `GENERAL_ATTACK`.
- **Ids are ints.** They are `IntEnum`s, so `UnitTypeId.MARINE == 48` is true. python-sc2's ids are plain
  `Enum`s, for which it is false.
- **The match enums are upper case**: `Race.TERRAN`, `Difficulty.VERY_HARD`, `Result.VICTORY`, and
  `AIBuild.RANDOM` for python-sc2's `AIBuild.RandomBuild`. They are `IntEnum`s too.

## Points, areas and grids

| python-sc2 | NachOS |
|---|---|
| `Point2`, `Point3` | `Point`, `Point3D` |
| `Rect` | `Rectangle`, which is neither a point nor a tuple |
| `PixelMap` | `Grid` |
| `p.to2`, `p.to3` | `p.ground`, `p.with_height(z)` |
| `p.offset(q)` | `p + q` |
| `p.rotate(angle)` | `p.rotated(angle)`, optionally `around=` another point |
| `p.rounded`, which floors | `Tile.containing(p)`, the tile the point is on |
| `p.snap()` | `Tile.containing(p).center` |

- **Equality is exact.** python-sc2 treats points as equal if every coordinate is within 1e-8, and counts a
  missing coordinate as zero, so `Point3((1, 2, 0)) == Point2((1, 2))`. NachOS points compare as the tuples they
  are.
- **The origin is truthy.** python-sc2 makes `Point2((0, 0))` falsy, so there `if point:` means "not the origin".
- **`direction_vector` returns a unit vector.** python-sc2's returns the sign of each axis, such as `(1, -1)`.
- **2D and 3D points do not mix.** python-sc2 silently pads with zeros or drops the height: `Point2 + Point3`
  loses the height, and `Point3.towards(Point2)` pulls the height toward zero. NachOS raises instead. Convert with
  `.ground` or `.with_height(z)`.
- **Methods that take a point take only a point.** python-sc2's also accept anything with a `.position`. In
  NachOS, pass the position itself.
- **Grids are indexed `[x, y]`.** python-sc2's `PixelMap.data_numpy` is indexed `[y, x]`. `grid[point]` reads the
  tile that any point falls in, where a `PixelMap` needs whole-number coordinates. Reading past the edge raises
  `IndexError`, or returns the grid's `outside` value if it has one, instead of failing an assert.

## Units

| python-sc2 | NachOS |
|---|---|
| `bot.all_units` | `api.units`, and `api.known_units` with the units out of sight |
| `unit.tag`, to tell units apart | `unit.id` |
| `unit.is_mine`, `is_enemy` | `unit.alliance is Alliance.OWN`, `Alliance.ENEMY`; or `isinstance(unit, OwnUnit)` |
| `unit.is_snapshot`, `is_visible` | `unit.visibility is Visibility.IN_FOG`, `Visibility.IN_VISION` |
| `unit.is_ready`, `weapon_cooldown` | `unit.is_complete`, `weapon_cooldown_steps` |
| `unit.health_percentage`, `shield_percentage`, `energy_percentage` | `unit.health_fraction`, `shield_fraction`, `energy_fraction` |
| `unit.shield_health`, `shield_health_max` | `unit.life`, `life_max` |
| `unit.cargo_left` | `unit.cargo_max - unit.cargo_used` |
| `unit.add_on_tag`, `engaged_target_tag`, `order.target` as a tag | `unit.add_on`, `engaged_target`, `order.target` as the unit |
| `unit.is_constructing_scv` | `unit.construction is not None` once the structure is placed, and `structure.builder` |
| `UnitOrder` | `Order` |
| `unit.distance_to(p)` | `unit.position.distance_to(p)` |
| `unit.name` | `unit.type_id.name` |
| `unit.real_speed`, `calculate_speed(upgrades)` | `unit.speed`, with upgrades but not yet creep or buffs; see below |
| `unit.ground_range`, `air_range`, `ground_dps`, which count no upgrade | `unit.weapons`, which count them |
| `unit.armor + unit.armor_upgrade_level` | `unit.armor` |
| `units.find_by_tag(t)`, `by_tag(t)` | `units.get(unit_id)`, `units.by_id(unit_id)` |
| `units.tags_in(ts)`, `tags_not_in(ts)` | `units.with_ids(ids)`, `units.without_ids(ids)` |
| `units.of_type(UnitTypeId.MARINE)` | `units.of_type(UnitType.Marine)`, typed as marines; a `UnitTypeId` is still taken, untyped |
| `units.exclude_type(t)` | `units.excluding_type(t)` |
| `unit.type_id == UnitTypeId.MARINE`, to then use it as a marine | `UnitType.Marine.includes(unit)`, which narrows it |
| `units.owned`, `structure`, `ready` | `units.own`, `units.structures`, `units.complete` |
| `units.closer_than(d, p)` | `units.in_area(Circle(p, d))`, which counts a unit at exactly `d` |
| `units.closest_n_units(p, n)` | `units.closest(n, p)` |
| `units.amount`, `exists`, `empty`, `first` | `len(units)`, `bool(units)`, `not units`, `units[0]` |
| `units.random` | `random.choice(units)` |

- **A unit is one object under one id for the whole game.** The game's tags do not identify a unit: a structure
  going out of sight is replaced by a remembered copy under a new tag each time, and a mineral field starts the game
  as one. NachOS follows a structure through those swaps, so it keeps its object and id, and `unit.tag` is only the
  game's current handle. An id's first digit is the unit's alliance when first seen, 1 for yours and 4 for the
  enemy's; the rest counts that alliance's units. python-sc2 builds new objects every step, keyed by tag.
- **A unit out of the observation is stale, not gone.** It keeps what it last read, `is_stale` is true, and it is
  in `api.known_units` but not `api.units`. If it comes back, as a unit leaving a transport or an enemy walking back
  into sight does, it is the same object. Only death ends it: `is_dead` becomes true.
- **The game reports only the deaths you can see.** A structure that burns down or is killed out of sight stays
  remembered until its spot is in sight again; NachOS then finds it missing and marks it dead. A structure that can
  lift off or uproot may have moved instead, so it stays stale until it turns up. python-sc2 drops the remembered
  copy and says nothing.
- **A drone that becomes a structure is stale until the structure finishes, then dead.** The game gives the
  structure a new tag, and reports the drone dead once the structure finishes or is killed. If the structure is
  cancelled, the drone comes back as the same object. `builder` links the two for your own drones only, since only
  your own units' orders are reported.
- **A unit names the units it points at by object.** An order's target, a rally target, a passenger, an add-on and
  an engaged target are units, stale or dead ones included, where python-sc2 gives tags to look up. An order aimed
  at nothing has `None` for its target, where python-sc2's is `0`.
- **`builder` and `construction` link a structure and what builds it.** `structure.builder` is the SCV building it
  or the drone that became it, and `scv.construction` the structure. Both read `None` once it is finished, while
  it is halted, and for a Protoss structure. python-sc2 has `is_constructing_scv` and no link, and it is true from
  the order on, while the SCV walks to the site; `construction` is `None` until the structure is placed, so an SCV
  on its way is one whose first order is the build ability.
- **A remembered structure reads as it was last seen.** Its position and type are current; its health, contents
  and buffs are as of `unit.last_seen`. python-sc2 reads the zeros the game sends for them.
- **What the game never showed raises `NotReportedError`**, such as the health of a burrowed unit never detected
  or the contents of a mineral field no one has looked at. python-sc2 answers 0.
- **Orders, cargo, harvesters, rally points and weapon cooldown are on `OwnUnit` only**, the class of your own
  units, since the game reports them for nobody else's. python-sc2 has them on every unit, where an enemy is always
  `is_idle`. `units.own` is typed as your own units, and `units.idle` exists only on them. A unit taken over by a
  neural parasite changes class, and changes back.
- **A unit's type is a type parameter too.** `UnitType` has a class for every type, `UnitType.Marine`, whose `id`
  is its `UnitTypeId`, and for the groups the game's tables put them in: `UnitType.Terran`, `UnitType.Structure`,
  `UnitType.ProtossStructure`. `Unit[UnitType.Marine]` is a marine, a read only some types have is an error on the
  others, as `is_powered` is on anything but a Protoss structure. A unit typed `Unit[Any]` reads as any type, while
  on a `Unit[UnitType.AnyType]` such a read is an error until `UnitType.ProtossStructure.includes(unit)` narrows
  it. The type checker cannot follow a morph: a `Unit[UnitType.SiegeTank]` that sieges is still typed a siege tank.
- **Velocity is built in.** `unit.velocity` is in distance per second, measured between its last two observations.
- **A unit's weapons, speed and armor count its owner's upgrades.** Yours come from the observation, and the enemy's
  from `api.enemy.upgrades`. python-sc2's `real_speed` counts only your own upgrades and takes
  `calculate_speed(upgrades)` for anyone else's, and its `ground_range`, `ground_dps` and `armor` count none. For some
  other set, read `unit.type_data.with_upgrades(set)`.
- **What the enemy's units show is read off them, once, for the whole player.** The game reports nothing of the
  enemy's upgrades beyond the attack, armor and shield levels on each unit in sight, and those belong to the player
  and the line they are of: a marine at attack level 2 puts the first two Terran Infantry Weapons into
  `api.enemy.upgrades`, where every marauder of the enemy's counts them, in the fog as much as in sight. An
  ultralisk's armor is left out of this, since 2 could be two levels or Chitinous Plating. Anything the game never
  reports, such as Metabolic Boost, a bot adds with `api.enemy.assume_upgrades`, and can take back with
  `forget_upgrades`, and with `Api(infer_enemy_upgrades=UpgradeInference.NONE)` NachOS adds nothing itself. With
  `UpgradeInference.INTERMEDIATE` it also reads what only an upgrade brings about: an enemy warp gate or burrowed
  unit, a stimmed, charging, lunging or cloaked enemy, a marine at 55 health, a unit of yours slowed by concussive
  shells or matrixed, and an enemy storm.
  python-sc2 leaves all of this to the bot, per call.
- **`unit.armor` is what a unit in sight reports, so it is exact**, and what the enemy is known to have for a unit out
  of sight.
- **`unit.shield_armor` is the shields levels its owner has**, which is what python-sc2 writes as
  `enemy_shield_armor = target.shield_upgrade_level` inside `calculate_damage_vs_target`. The game's tables carry no
  shield armor at all: this was measured in game, where a marine's 6 damage took 5 off a zealot's shields at level 1
  and 3 at level 3.
- **`unit.speed` counts no creep and no buff yet**, where python-sc2's `real_speed` counts both.
- **Speeds are per second of the game's Faster speed, as `velocity` is.** The game's tables give them per second of
  its Normal speed, 16 steps, which python-sc2 hands on, so its code multiplies by 1.4 to get a distance a unit covers
  in a real second. NachOS has done it already: a zergling's `speed` is 4.13, not 2.95.
- **`armor_upgrade_level` is armor, not a count of levels.** An ultralisk with Chitinous Plating and three levels
  reports 5, and a structure with Neosteel Armor 2. `attack_upgrade_level` is a count.
- **Reaper grenades and force fields stay units.** python-sc2 moves them into `state.effects`; in NachOS they are
  in `api.units` as `REAPER_GRENADE` and `FORCE_FIELD`, so leave them out of an army count.
- **Blips and placeholders are not units.** Neither has a tag. python-sc2 has `bot.blips` and puts placeholders in
  `all_units`; NachOS has neither yet.
- **`life` is health and shield together.** The protocol calls health alone life; NachOS does not.
- **A collection never changes.** python-sc2's `Units` is a list you can append to; NachOS's is a fixed sequence,
  and every filter answers a new one.
- **Collections are put together with `Units.combined(a, b, ...)`**, which keeps each unit where it first appears
  and knows a unit by its NachOS id. python-sc2 spells it `a | b` and `a + b`.
- **Data of your own about a unit is keyed by the unit or its id.** A unit takes no attributes of yours, and its
  class is not yours to subclass. A `weakref.WeakKeyDictionary` keyed by unit lets go of an entry once the unit is
  dead and dropped.

## The state

| python-sc2 | NachOS |
|---|---|
| `state.score.killed_minerals_army` | `api.score.killed_minerals.army`, and `.total` across the categories |
| `state.score.collected_minerals`, `collection_rate_*`, `spent_*` | `api.score.collected.minerals`, `collection_rate`, `spent`, each a `Resources` |
| `state.score.total_value_units`, `killed_value_structures` | `api.score.total_value.units`, `killed_value.structures` |
| `state.score.idle_worker_time`, `idle_production_time` | `api.score.idle_worker_steps`, `idle_production_steps` |
| `bot.minerals`, `vespene` | `api.resources.minerals`, `vespene`, and `api.resources.covers(cost)` against a cost |
| `bot.supply_used`, `supply_cap`, `supply_left`, `supply_army`, `supply_workers` | `api.supply.used`, `cap`, `left`, `army`, `workers` |
| `bot.idle_worker_count`, `army_count`, `warp_gate_count` | `api.ui_unit_counts.idle_workers`, `army`, `warp_gates` |
| `state.upgrades` | `api.upgrades` |
| `state.visibility[p] == 2`, `> 0` | `api.vision[p]`, `api.explored[p]` |
| `state.creep` | `api.creep` |
| `state.effects` | `api.effects` |
| `state.common.larva_count` | `len(api.units.own.of_type(UnitType.Larva))` |
| `state.dead_units`, `chat`, `actions`, `alerts` | events: see Events |
| `state.action_errors` | nothing yet |

- **Each read answers from the last observation, and nothing is read until asked for.** There is no `state`
  object to hold on to; `api.score` read next turn is next turn's score.
- **Half a supply is kept.** The game rounds the supply in use, and the army's, down: one zergling leaves it where
  it was. python-sc2 counts zerglings and banelings to round it up instead; NachOS adds the half back, so
  `api.supply.used` reads `14.5`.
- **The larva count is always zero.** The game reports `larva_count` as 0 with larva at the hatchery, so NachOS
  leaves it out. Count the larva units.
- **The idle times are whole steps.** The game counts them in seconds of its Normal speed, 16 steps each, which
  python-sc2 hands on as they are. Each is summed over the idle units: two idle workers add two steps a step.
- **The recent APM is left out of the score**, since it reads zero in a game played through the raw interface.
- **`api.effects` holds only what the game reports as an effect.** python-sc2 adds force fields and reaper
  grenades to `state.effects`, which are units in NachOS.
- **The grids are the map's shape.** `vision`, `explored` and `creep` cover the playable area and read `False`
  past it, as `api.map.pathing` does, so they combine with it directly.

## The map

| python-sc2 | NachOS |
|---|---|
| `game_info.map_name` | `api.map.name` |
| `game_info.pathing_grid`, `in_pathing_grid(p)` | `api.map.pathing`, `api.map.pathing[p]` |
| `game_info.placement_grid`, `in_placement_grid(p)` | `api.map.placement`, `api.map.placement[p]` |
| `game_info.terrain_height`, `get_terrain_z_height(p)` | `api.map.height`, `api.map.height_at(p)` |
| `game_info.map_center` | `api.map.playable_area.center` |
| `enemy_start_locations` | `api.map.opponent_start_locations` |
| `game_info.map_ramps` | `api.map.ramps` |
| `game_info.vision_blockers` | nothing; see below |
| `ramp.points`, `ramp.upper`, `ramp.lower` | `ramp.tiles`, `ramp.top`, `ramp.bottom` |
| `ramp.top_center`, `ramp.bottom_center`, `ramp.center` | `ramp.top.center`, `ramp.bottom.center`, `ramp.tiles.center` |

- **The grids cover the playable area and no more.** A grid's `values[0, 0]` is the playable area's lower left
  corner, not the map's. Past the playable area, pathing and placement read `False` and height raises.
- **The grids refuse writes.** python-sc2 rebuilds the pathing grid every step, so writing into it lasted one
  step. A grid the map hands out is `readonly`, so a write raises `TypeError`; `copy()` gives one you can change.
- **Height is the ground's height, not a byte, and python-sc2 decodes the byte a little low.** Its
  `terrain_height` holds the byte the game sends. Its `get_terrain_z_height` computes `-16 + 32 * byte / 255`,
  which reads up to 0.03 below where units stand. A byte is an eighth of a unit of height, with 127 at zero.
- **A tile's height is its center's.** The game sends the height at each tile's lower left corner, and python-sc2
  reads that as the tile's. On a ramp, that is up to 0.41 off the ground elsewhere in the tile, and beside a cliff
  it can be the level on the other side. `api.map.height` averages the tile's corners on its own side of any
  cliff, and `api.map.height_at(p)` interpolates between them.
- **A ramp's ends are the tiles within a byte of its highest and lowest, and come out the same size whichever
  way it faces.** python-sc2's `upper` and `lower` are the tiles sharing the highest and lowest terrain byte,
  which it reads at each tile's lower left corner, so a ramp and its mirror image give ends of different sizes --
  13 and 6 tiles for two halves of the same map. NachOS reads the height at the tile's center, which is
  symmetric, and allows a byte because a row straight across a ramp is not quite level where the corners under it
  differ.
- **A patch of ground is a ramp whole, and no patch is dropped for being small.** python-sc2 asks of each tile
  alone whether the nine terrain bytes around it are equal, calls a tile a ramp point if they are not, and then
  throws away any group of fewer than 8 of them. NachOS groups the ground a unit can walk over but cannot build
  on and reads the whole patch: one whose heights span half a level or more climbs from one level to the next,
  and is a ramp. On the 2026 ladder pool the two find the same ramps, tile for tile.
- **There is no `vision_blockers`, because the map's grids cannot say.** A bridge, a stand of trees and the
  ground under an indestructible doodad are all level, walkable and unbuildable, and nothing in
  `ResponseGameInfo` separates them. python-sc2's `vision_blockers` is that whole mixture: on PylonAIE_v4 it
  calls 33 tiles of each of the map's two bridges a vision blocker. A bot that needs the real ones can find them
  in a game, from what its units can and cannot see.
- **No wall-in placements.** python-sc2's `Ramp` also answers where to put supply depots and a barracks to wall
  off a ramp, and raises on any ramp whose shape it does not expect. NachOS has no equivalent yet.

## The tables

| python-sc2 | NachOS |
|---|---|
| `game_data` | `api.data` |
| `game_data.units[unit_type.value]` | `api.data.units[unit_type]` |
| `game_data.abilities`, `game_data.upgrades` | `api.data.abilities`, `api.data.upgrades`, `api.data.effects` |
| `unit_data.cost` | `unit_data.cost`, a `Resources` without the time |
| `unit_data.cost.time` | `unit_data.build_steps` |
| `unit_data._proto.food_required`, `food_provided` | `unit_data.supply_cost`, `unit_data.supply_provided` |
| `unit_data._proto.movement_speed`, and python-sc2's `1.4 *` before it | `unit_data.speed`, per second of Faster speed |
| `unit_data._proto.weapons` | `unit_data.weapons` |
| `unit_data.unit_alias` | `unit_data.base_type` |
| `unit_data.tech_alias` | `unit_data.tech_aliases`, empty rather than `None` |
| `unit_data.creation_ability.exact_id` | `unit_data.creation_ability` |
| `upgrade_data.research_ability.exact_id` | `upgrade_data.research_ability` |
| `upgrade_data.cost.time` | `upgrade_data.research_steps` |
| `weapon.speed`, in Normal-speed seconds | `weapon.cooldown_steps` |
| `weapon.damage_bonus` | `weapon.damage_bonuses`, by the attribute each is earned by |
| `ability_data.link_name`, `button_name`, `friendly_name` | nothing; see below |
| `ability_data.is_building` | `ability_data.needs_placement` |
| `game_data.calculate_ability_cost(a)` | nothing; see below |
| `unit_data._proto.tech_requirement`, `require_attached` | `data.units[performer].ability_requirements[ability]`; see below |
| `UNIT_TRAINED_FROM[t]` | `data.abilities[data.units[t].creation_ability].performers` |
| `UPGRADE_RESEARCHED_FROM[u]` | `data.abilities[data.upgrades[u].research_ability].performers` |
| `TRAIN_INFO[maker][t]`, `RESEARCH_INFO[maker][u]` | `data.units[maker].ability_requirements[ability]` |
| `TERRAN_TECH_REQUIREMENT` and its siblings | the `structures` of the ability's requirements |
| `EQUIVALENTS_FOR_TECH_PROGRESS` | `unit_data.tech_aliases` of the type that stands |
| `UNIT_ABILITIES[t]` | `data.units[t].abilities` |
| `GENERIC_REDIRECT_ABILITIES` | `ability_data.remaps_to` |
| a requirement's `requires_power` | `unit_data.needs_power` |
| nothing | `ability_data.product`, `unit_data.morphed_from` |
| `DAMAGE_BONUS_PER_UPGRADE`, `SPEED_UPGRADE_DICT`, `SPEED_INCREASE_DICT` | `unit_data.upgrades`, `unit_data.with_upgrades(upgrades)` |
| nothing | `upgrade_data.type` and `upgrade_data.level`: the upgrade level units report an upgrade adds to, and which level of its line it is |

- **A table is keyed by the id itself**, where python-sc2 keys by the number inside it and every lookup reads
  `units[UnitTypeId.MARINE.value]`.
- **No row carries a name**, because the curated id is a better one than the game gives. A unit type, upgrade or
  effect names itself exactly as the catalog does, so `RawUnitTypeId(int(row.id)).name` is the game's spelling.
  An ability has three names and none of them is it: `link_name` is the command card group, which 15 protoss
  build abilities share; `button_name` is blank for ten of them and `BurrowDown` for twelve; `friendly_name` is
  a sentence, "Attack Attack" for the exact attack. python-sc2 carries all three.
- **`remaps_to` runs from the exact ability to the general one.** A unit always reports the exact id it is
  running, and the general one is a spelling you may order instead: order `GENERAL_MOVE` and the unit reports
  `GENERAL_MOVE_EXACT`. A general id is never offered by `RequestQuery`, but it is accepted as an order and
  the game picks which exact one it meant, so `ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS` researches whichever level
  comes next and `GENERAL_BURROW` burrows whatever the unit is. python-sc2 folds this into `AbilityData.id`,
  which answers the remapped id while `exact_id` answers the row's own, and ships the same relation by hand as
  `generic_redirect_abilities`. It does not catch every pair of that shape: a liberator reports
  `LIBERATOR_SIEGE_EXACT` for the `LIBERATOR_SIEGE` it was ordered, and all four liberator rows leave
  `remaps_to` empty. python-sc2 has the same blind spot, since it reads the same field.
- **A table holds only the rows a bot can name.** The game describes its whole catalog -- 2005 unit types, 940
  of them ids it skips and with no name at all, and 4134 abilities -- and a table keeps the ones the curated ids
  name and drops the rest, so nothing it hands back is a number without a word for it. python-sc2 filters
  instead on the `available` flag, which is no filter: the game marks `MorphZerglingToBaneling` unavailable, and
  a zergling morphs anyway.
- **A field naming something uncurated reads as `None`, and `tech_aliases` drops it, except where an override
  names what works.** Six unit types' rows name a creation ability the game no longer honors: a lurker's is
  `LurkerAspectMPFromHydraliskBurrowed`, a baneling's is `MorphZerglingToBaneling`, a rich refinery's is a second
  `TerranBuild` row, an auto turret's is `RavenBuild_AutoTurret`, a locust's is `SpawnInfestedTerran` and a
  purification nova's is `PurificationNovaMorph` -- none is ever offered and none does anything when ordered. A rich
  assimilator's and a rich extractor's rows name none at all. For these eight, `creation_ability` is the ability
  that works, `ZERGLING_MORPH_BANELING`, `HYDRALISK_MORPH_LURKER`, the plain gas builds and so on, which
  `gamedata/_techtree/_overrides.py` lists with its reasons and the tech tree sweep checks in game each time it runs
  (`docs/curating-ids.md`). python-sc2 papers over only the lurker, by writing `MORPH_LURKER` into the message it
  was handed. The rest name one nothing can order at all, and read `None`: the game disguises a changeling,
  collapses a tower, takes a locust into the air and digs a creep tumor in by itself, and a bare tech lab or
  reactor is a tech requirement no unit is built as.
  The viking is the `tech_aliases` one: its alias is a row with no cost, speed, sight or weapon that nothing
  requires and no unit is ever one of. python-sc2 keeps every one of these, because it filters unit types on
  `available` and the game sets that flag on them.
- **There is no ability for unloading one passenger.** The catalog's `UnloadUnit_*` rows cannot be ordered
  through `RequestAction` at all: the game takes it as a UI action, `ActionCargoPanelUnload` against the
  passenger's index, after a raw command with `ability_id=0` has selected the transport, and it needs both
  `raw_affects_selection` and a feature layer turned on. NachOS asks for neither and has no UI path, so it
  cannot do this yet; `MEDIVAC_UNLOAD` and `MEDIVAC_UNLOAD_AT` put everyone down at once.
- **A row's `id` is its own.** python-sc2's `AbilityData.id` answers the generic id the ability remaps to, and
  `exact_id` the row's own. NachOS keeps `id` the row's own and puts `remaps_to` beside it.
- **A cost is minerals and vespene, and times are seconds beside it.** python-sc2's `Cost` carries a `time`
  in steps, which its `__add__` adds and its `__eq__` ignores; build times overlap, so adding them is wrong
  nearly everywhere. NachOS has `Resources`, which adds, subtracts, scales, divides and answers `covers`, and
  `build_steps` and `research_steps` are their own fields. Its amounts are fractional, since half a
  cost and an average cost are ordinary things to want; what the game gave stays whole until something divides
  it.
- **The cost of a morph is everything spent to reach it, and its build time is only the last step.** An orbital
  command is 550 minerals, the command center's 400 included, and 25 seconds, the morph alone. python-sc2
  subtracts the predecessor in `morph_cost` and `calculate_ability_cost`, reading a hand-written
  `UNIT_TRAINED_FROM` and hard-coding that zerglings come in pairs and that a baneling really costs 25/25.
  NachOS hands back the game's numbers as they stand, and `morphed_from` says what to subtract.
- **What relates the tables to each other was swept in game, not read from the game's files.** `RequestData`
  names no unit that performs an ability, gives a ghost, a thor, a battlecruiser and a mothership no requirement,
  has room for only one, and holds no upgrade's requirements. python-sc2's dicts come from sc2-techtree, which
  read an older patch's data files; NachOS's come from `tools/sweep_tech_tree.py`, which asks the game what each
  unit type is offered and sees what goes when a structure dies or an upgrade finishes (`docs/curating-ids.md`).
  Where the two disagree, the game says a probe is offered a gateway and a forge once a nexus stands, whatever the
  pylons, and a roach a ravager once a roach warren stands, where python-sc2 says a pylon and a hatchery.
- **Requirements belong to a unit type and an ability together**, since one ability can need different things of
  different types: a burrowed roach moves only once Tunneling Claws is researched, and nothing else needs anything
  to move. `units[t].ability_requirements` holds an entry for every ability `units[t]` is offered, needing nothing where
  it needs nothing. A structure required counts as standing where a type whose `tech_aliases` name it stands, and an
  add-on among them has to be the unit's own, which is what python-sc2's `requires_techlab` says.
- **Power is the structure's, not the ability's.** An unpowered gateway is offered nothing it trains, which
  python-sc2 writes as `requires_power` on each ability; NachOS has `needs_power` on the unit type.
- **A unit type is offered what the game offers it, less what does not work.** Once Burrow is researched, every
  zerg unit that burrows is offered every zerg unit's burrow, and ordered any of them burrows as itself, so a
  zergling's `abilities` hold only `ZERGLING_BURROW`. A general ability is never offered, so its `performers` are
  those of the abilities that remap to it, and an id a unit only reports, such as `LIBERATOR_SIEGE_EXACT`, has none.
  A cancel, a halt or an unload counts among a type's `abilities` though it is offered only while there is something
  to cancel, halt or unload. What a gateway warps in is not curated yet, so a warp gate trains nothing in the tables.
- **`morphed_from` names the unit type used up making another**, where the unit ordered becomes the product or is
  gone: a larva for a zergling, a drone for a spawning pool, a command center for an orbital command, a siege tank
  for a sieged one. An SCV, a probe and a barracks make theirs beside themselves. Take the price of a morph as its
  `cost` less that of what it came from; python-sc2's `calculate_ability_cost` does it with a hand-written table.
- **What an upgrade changes was swept in game too, and is added, not multiplied.** python-sc2 writes
  `DAMAGE_BONUS_PER_UPGRADE` and its speed dicts by hand, the speeds as factors. `unit_data.upgrades` holds what
  `tools/sweep_upgrades.py` read off the game's rows after each upgrade, and every upgrade raising a level the type's
  units report besides, with nothing added to the row: a shields level for every protoss type, and the attack levels
  for a void ray, a carrier and a sentry, which the rows give no weapon. Those rows take in weapon damage, bonuses and
  range, armor and speed, and nothing else: no attack speed, since Adrenal Glands and Resonating Glaives change nothing
  in them, and Anabolic Synthesis counts there whether or not the ultralisk is on creep. Where the two disagree, the
  game says a cyclone's attack level adds 1 where python-sc2 says 2; Flux Vanes makes a void ray 1.21 times as fast,
  Gravitic Boosters an observer 1.5, Muscular Augments a hydralisk 1.31 and Anabolic Synthesis an ultralisk 1.18, where
  python-sc2 says 1.33, 2, 1.25 and 1.2; Pneumatized Carapace speeds up an overlord transport too; Adaptive Talons
  changes no lurker's speed; and python-sc2's Rapid Deployment is an upgrade the game no longer has.
- **A row is as it stands before any upgrade, unless `with_upgrades` made it.** Asked again later in a game, the game
  folds in the asking player's upgrades, and only that player's, though a unit type has one row; NachOS asks once.
- **Reading the tables leaves the message they came from alone.** Building python-sc2's `GameData` writes
  `MORPH_LURKER` over that same lurker row in the `ResponseData` it was handed, so whatever reads that message
  afterwards sees the substitution rather than what the game said.
- **There is no buff table.** `BuffData` carries an id and a name and nothing else, and the name is the id's.
