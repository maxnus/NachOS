# Migrating from python-sc2

For bots moving over from [python-sc2](https://github.com/BurnySc2/python-sc2), the `burnysc2` package imported as
`sc2`. NachOS is not a drop-in replacement. This page lists the places where the obvious translation of python-sc2
code goes wrong. It covers what NachOS has so far, and grows with it.

## Running a game

- **Nothing is subclassed, and nothing is `async`.** Construct an `Api`, at import time if you like, and hand it
  to a runner along with the race it plays: `run_local("PylonAIE_v4", ApiBot(api, Race.TERRAN), Computer(Race.ZERG))`.
- **A turn is one step unless you say otherwise.** Use `Api(steps_per_turn=4)` to match python-sc2, whose
  `client.game_step` defaults to 4. The value is fixed for the life of the api and cannot be changed mid-game.
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
  reading one off a unit raises `UncuratedIdError`, which is one. `sc2nachos.ids.raw` holds every id, under
  Blizzard's own names.
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
| `unit.add_on_tag`, `order.target` as a tag | `unit.add_on_id`, `order.target` as an id |
| `UnitOrder` | `Order` |
| `unit.distance_to(p)` | `unit.position.distance_to(p)` |
| `unit.name` | `unit.type_id.name` |
| `units.find_by_tag(t)`, `by_tag(t)` | `units.get(unit_id)`, `units.by_id(unit_id)` |
| `units.tags_in(ts)`, `tags_not_in(ts)` | `units.with_ids(ids)`, `units.without_ids(ids)` |
| `units.exclude_type(t)` | `units.excluding_type(t)` |
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
- **A remembered structure reads as it was last seen.** Its position and type are current; its health, contents
  and buffs are as of `unit.last_seen`. python-sc2 reads the zeros the game sends for them.
- **What the game never showed raises `NotReportedError`**, such as the health of a burrowed unit never detected
  or the contents of a mineral field no one has looked at. python-sc2 answers 0.
- **Orders, cargo, harvesters, rally points and weapon cooldown are on `OwnUnit` only**, the class of your own
  units, since the game reports them for nobody else's. python-sc2 has them on every unit, where an enemy is always
  `is_idle`. `units.own` is typed as your own units, and `units.idle` exists only on them. A unit taken over by a
  neural parasite changes class, and changes back.
- **Velocity is built in.** `unit.velocity` is in distance per second, measured between its last two observations.
- **Reaper grenades and force fields stay units.** python-sc2 moves them into `state.effects`; in NachOS they are
  in `api.units` as `REAPER_GRENADE` and `FORCE_FIELD`, so leave them out of an army count.
- **Blips and placeholders are not units.** Neither has a tag. python-sc2 has `bot.blips` and puts placeholders in
  `all_units`; NachOS has neither yet.
- **`life` is health and shield together.** The protocol calls health alone life; NachOS does not.
- **A collection never changes.** python-sc2's `Units` is a list you can append to; NachOS's is a fixed sequence,
  and every filter answers a new one.
- **Data of your own about a unit is keyed by the unit or its id.** A unit takes no attributes of yours, and its
  class is not yours to subclass. A `weakref.WeakKeyDictionary` keyed by unit lets go of an entry once the unit is
  dead and dropped.

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
| `unit_data._proto.movement_speed` | `unit_data.speed` |
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
- **A field naming something uncurated reads as `None`, and `tech_aliases` drops it.** Twenty-two unit types
  have no `creation_ability` and one has no `tech_aliases`, for two reasons, each checked in game rather
  than guessed. Six name an ability the game no longer honors: a lurker's is
  `LurkerAspectMPFromHydraliskBurrowed`, a baneling's is `MorphZerglingToBaneling`, a rich refinery's is a
  second `TerranBuild` row, an auto turret's is `RavenBuild_AutoTurret`, a locust's is `SpawnInfestedTerran`
  and a purification nova's is `PurificationNovaMorph` -- none is ever offered, none does anything when
  ordered, and `HYDRALISK_MORPH_LURKER`, `ZERGLING_MORPH_BANELING`, plain `SCV_BUILD_REFINERY`,
  `RAVEN_SPAWN_AUTO_TURRET`, `SWARM_HOST_SPAWN_LOCUST` and `DISRUPTOR_PURIFICATION_NOVA` are what work. The
  rest name one nothing can order at all, since the game disguises a changeling, collapses a tower, takes a
  locust into the air and digs a creep tumor in by itself, and a bare tech lab or reactor is a tech
  requirement no unit is built as.
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
  NachOS hands back the game's numbers as they stand; what morphs from what belongs to the relationship tables.
- **Reading the tables leaves the message they came from alone.** Building python-sc2's `GameData` writes
  `MORPH_LURKER` over that same lurker row in the `ResponseData` it was handed, so whatever reads that message
  afterwards sees the substitution rather than what the game said.
- **There is no buff table.** `BuffData` carries an id and a name and nothing else, and the name is the id's.
