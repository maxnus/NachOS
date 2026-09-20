# How the game behaves

What StarCraft II does, as NachOS has seen it through its API. Seen with StarCraft II 5.0.15, base build 95841,
unless an entry says otherwise. A fact that shapes one piece of code is also in that code's docstring; this page is
where to look one up, and where a new finding goes. [cheats.md](cheats.md) says more about cheats, and
[curating-ids.md](curating-ids.md) how the game's ids were sorted into the curated ones.

Each entry ends with how it was seen:

- **#n**: made to happen in a running game, in the probes or tools of pull request #n, whose description says how;
- **in game**: seen in a running game, with no record of which pull request;
- **tested**: an in-game test (`uv run pytest -m integration`) checks it still;
- **tool** *name*: `tools/`*name*`.py` brings it about in a game and records it;
- **corpus**: read off the recorded games in `tests/corpus`;
- **stated**: written down in the code or docs with no record of how it was seen, so worth checking.

## The connection and the protocol

- The game serves one websocket connection at a time and drops a second one during its handshake. Once the first
  closes, a new connection finds the game it created, and after the game it pings as `launched` (#14; tested).
- Nothing matches an answer to its request: the game answers the last request asked, so only one can be open at a
  time (#6).
- Every response carries a status. An unset proto2 enum field reads as its first declared value, not zero: an unset
  status reads `launched`, an unset join error `MissingParticipation` and an unset create error `MissingMap`, all
  truthy, so they are read only behind `HasField` (#6, #9).
- The protocol is binary; a text frame is the game or a proxy reporting an error (stated).
- Before a game, or after leaving one, a request that needs one fails with "A game has not been started yet"; after
  a game has ended, with "Game has already ended" or "Not supported if game has already ended" (#14).
- Leaving a running or ended game concedes it, the opponent being told it won, and returns the client to
  `launched`; leaving when no game was started is refused. An ended game accepts a new `create_game` without being
  left first (#14).
- The game says it has ended one observation before it says who won, and only an observation carries the results
  (#16).
- A game that dies resets the socket, which shows up as a bare connection error (#16).
- `ResponseGameInfo` is about 77 KB, and byte-identical at loops 0, 256, 1024 and 3008. An observation is about
  80 KB and barely changes from one step to the next (#10, #11).
- A debug request gets no answer. Debug commands work only in a game the client created, and a drawing lasts until
  the next debug request (#22).
- Each connection needs two ports, and every participant must send the same port sets in the same order, or the
  join fails. From a ladder start port N, N is unused, the server gets N+2 and N+3 and the client N+4 and N+5. A
  python-sc2 bot given the same start port joined NachOS's game and played in lockstep (#14).
- In a game of two clients, steps pass only once both have asked, and each join waits for the other (tool
  `_sandbox`, #37).
- A realtime game advances by itself; only a stepped game needs step requests (stated).
- An unset `random_seed` lets the game pick one, and 0 is a real seed (stated).

## Launching, maps and players

- The client was listening 4.9 s after launch. The raw interface starts at base build 55958 (#7).
- It serves the API at `ws://host:port/sc2api`, and takes `-listen`, `-port`, `-dataDir`, `-tempDir`,
  `-displayMode`, `-windowwidth`, `-windowheight`, `-windowx` and `-windowy` (#7).
- The executable is `Versions/Base<build>/SC2_x64.exe` on Windows, started from `Support64`, `SC2_x64` on Linux, and
  `SC2.app/Contents/MacOS/SC2` on macOS. The launcher writes a non-default install into `ExecuteInfo.txt`, under
  `Documents/StarCraft II` on Windows and `Library/Application Support/Blizzard/StarCraft II` on macOS, as
  ` = <path>Versions` ending in the separator of the platform that wrote it (#7).
- Installers name the map folder `Maps` or `maps`. A map pack installs beside the maps it replaces, and a revised
  map is a new file next to the old one (`PylonAIE_v4` beside `PylonAIE`), so one name can appear more than once
  (#9, #21). Replays are written to `<install>/Replays` (stated).
- A client left running keeps holding the graphics card (stated).
- Every map in use seats two. The game drops extra players without a word, and `PlayerSetup` cannot set teams
  (#11). `create_game` ignores a participant's race and name: the join sets them. `PlayerSetup.race` counts only for
  a computer (#9).
- After `create_game` the status is `init_game`, the join returns player 1, and `game_info` gives only the joining
  player's own `race_actual` (#9; tested).
- A game with nobody to beat is won the moment it starts (#22).
- On every launch the game rewrites `stableid.json`, in `Documents/StarCraft II` on Windows, from the loaded map's
  mod dependencies, so its ids differ from map to map: BerlingradAIE yields 518 ability ids MagannathaAIE lacks
  (#5, #16).
- A bot ends a game early only by leaving it, which concedes it. The ladder starts the client, creates the game and
  passes host, port and start port on the command line; a game against the built-in computer has no start port
  (#14).

## Time and steps

- Normal speed is 16 steps a second, and Faster, the ladder's, 22.4: 224 steps are 10 seconds (#25).
- The tables give weapon speed as the cooldown in Normal seconds (a marine that had just fired read 13.56 steps
  against the table's 13.77), movement speed per Normal second (a zergling 2.95, which is 4.13 per Faster second),
  and build and research times in steps (stimpack 2240) (#25, #30).
- A unit's `weapon_cooldown` and a buff's `buff_duration_remain` and `buff_duration_max` count steps (#24, #25).
- Chat, commands and deaths from 45 unobserved realtime steps all came in the next observation, and never again:
  each is reported once, in the observation after it, however many steps that spans (#28).
- On the ladder or in realtime, what an order does can show an observation later than it would stepped, and
  realtime skips steps at will (#37, stated).

## Cheats and debug commands

[cheats.md](cheats.md) holds the rest.

- Every debug cheat is a toggle for the whole game: sent twice, it is off, and in a game of two only one player
  sends it (#28, #37).
- The cheats take a few steps to hold; the in-game tests wait 8 (tested).
- `free` makes everything cost nothing, a spell's energy included. `all_resources` only hands out resources, which
  run dry after a few hundred add-on rebuilds. `fast_build` makes building, training and research take seconds,
  research on debug-made structures included. Neither `free` nor `fast_build` grants an upgrade (#28, #33, #37).
- `tech_tree` waives every requirement and grants 42 upgrades within a few steps, campaign ones included; the
  `upgrade` cheat grants campaign upgrades too. Both only ever add ids: a hydralisk is offered every race's
  `BurrowDown` (#23, #28). Under `tech_tree` a barracks without an add-on trains two marines at once (tool
  `sweep_orders`).
- `god` stops this player's units taking damage and multiplies every weapon's damage in `ResponseData` by 10. Its
  effect on the tables is absent for a game's first ten steps, shows within 4 once 60 have run, and is gone within
  16 of turning it off (#30).
- `food` lifts the supply cap (in game).
- A debug-made unit can appear a few steps late, and the game does not always make what is asked, such as every
  pylon, or a structure on ground where it cannot stand: a photon cannon ordered onto this player's creep was never
  made (tool `sweep_alerts`, #37).
- A unit made for player 0 is reported as owner 16 (tool `_sandbox`).
- Debug commands make a larva but no plain creep tumor, and a queen's tumor only on creep (#28).
- A debug-made force field belongs to its player and is offered Shatter; one a sentry casts is neutral, owner 16,
  and offered nothing, and Shatter ordered on it is refused (#23, #33).
- A debug-made photon cannon did not detect in its first 4 steps (#37). A debug-made forge researches nothing unless
  a pylon powers it (#31).
- Setting a unit's shields by debug command reads as shields lost (tested). Set to 1, they regenerate straight away,
  2 every 16 steps (#41; tested).
- A vital set to 0 by debug command is set to its most: shields are left full, and energy reads 200 (#41, tool
  `sweep_orders`).

## What an observation lists

- The first observation lists every starting unit (tested).
- A radar blip, of type `NotAUnit`, and a structure placed but not started have no tag. A tagless placeholder stands
  where a structure was ordered from the moment of the order, before a worker reaches the spot (#25; tested).
- A structure or mineral field that leaves sight is replaced, in the same observation, by a remembered copy under a
  new tag at a bit-identical position; seen again, it is back under its first tag, and each fogging makes another
  tag. Mineral fields start the game as such copies (#25; tested).
- A copy can have another type than the structure had: a depot lowered as it left sight (#25).
- A barracks that lifts off, a spine crawler that uproots and a command center that becomes an orbital command out
  of sight keep their tags. Seen elsewhere, the structure is listed there while its copy stays listed at the old
  spot, until that spot is seen (#25; tested).
- A refinery stands at its geyser's exact position (#25).
- A unit inside a transport or a gas building, or a drone that became a structure, leaves the observation; a
  passenger returns under its tag when unloaded (#25; tested).
- Deaths come in `raw_data.event.dead_units`, and only deaths this player sees. A unit reported dead is already gone
  from that observation's units, and is reported once. Most reported deaths name tags no observation ever listed:
  77 of 81 in one corpus game (#25, #28; corpus; tested).
- A structure killed or burned down out of sight is never reported dead, not even once its spot is seen; its copy
  simply goes (#25; tested).
- An undetected cloaked enemy is listed, as `CLOAKED` and not in vision. An undetected burrowed enemy is not listed
  at all (#37; tested).
- Force fields and reaper grenades are listed as neutral units, not effects; a parasitic bomb adds no unit. Lurker
  spines are one effect with several positions along a line (#12, #25).
- Map-owned units are owner 16 (stated). An enemy structure made out of sight is not listed (tested).

## What is reported of a unit

- The game zeroes what the player cannot know: a remembered copy's health, contents and buffs, an unseen mineral
  field's contents, a hidden unit's owner. An undetected cloaked enemy reports its position and cloak, and no
  health, owner, build progress, buffs, facing or energy (#25; corpus).
- Orders, cargo, harvesters, rallies, weapon cooldown, the add-on and the engaged target are reported only for this
  player's own units (#25; corpus).
- `life` means health alone; shields are apart (stated). A marine has 45 health, 55 with Combat Shield, and the
  tables hold no health (#34; tested).
- `build_progress` counts up while a structure is built or a unit warps in (a zealot read 0.01, 0.32, 0.64, 0.95,
  1.00); an egg or cocoon reads 1 throughout, its progress being in its order (#25).
- Only the command center (12 of 16, then 9 once three SCVs went to gas) and the refinery (3 of 3) reported
  harvesters. `ideal_harvesters` is 2 a mineral field and 3 a geyser (#25).
- `is_powered` is true for a gateway in a pylon's field and false outside it, and false for a nexus, an assimilator,
  a barracks, a command center and a pylon, which always stands in its own field, alone or beside another (#25, #27,
  #39).
- `is_hallucination` is true for an enemy's hallucination only while a detector is near; this player's own are
  always flagged. `is_active` is true for any unit carrying out an order, the enemy's included. `engaged_target_tag`
  names the unit a marine attacks (#25).
- A unit lifted by a graviton beam wears `PHOENIX_GRAVITON_BEAM` and reports `is_flying` false (#26; tested).
- Visible enemy units report their armor and shield levels, and armed ones their attack level too; an undetected
  cloaked observer reports none (#12; corpus).
- Order and rally points have height 0. Order progress runs from 0 to 1 for training and research and is 0
  otherwise. A rally onto a unit that is gone, a mined-out mineral field among them, holds tag 2³², which names no
  unit (#25; corpus).
- Points come back in single precision (tested). Facing is in radians from the +x axis, and z is on the
  terrain-height scale (stated).
- Units on flat ground stand up to 0.03 below the map's height or up to 0.16 above it (corpus).
- A unit reports its owner's upgrade levels. `attack_upgrade_level` and `shield_upgrade_level` count levels, but
  `armor_upgrade_level` is the armor added: an ultralisk with Chitinous Plating and three levels reports 5, a
  structure with Neosteel Armor 2 (#30).

## Units coming, changing and going

- A larva stays one unit as it becomes an `EGG`. What hatches comes under new tags, two zerglings as two units, and
  the egg is reported dead (#37; tested).
- A morph keeps the tag: a tank sieging, a depot lowering, a zergling through `BANELING_COCOON` to a baneling, an
  overlord through `OVERLORD_COCOON` to an overseer. A lair or an orbital command changes type only once the morph
  finishes (#37; tested). A unit's type changes on burrowing too (#37).
- A drone that becomes a structure leaves the observation with no death reported, and the structure appears under a
  new tag. The game reports the drone dead as the structure finishes or is killed, in the same step in 3 games of 6
  and a step later in the other 3. On a cancel the structure is reported dead and the drone returns under its own
  tag, with its health, in that observation (#25, #37; tested).
- An SCV walks to the site with the build order first; once construction starts, its order is aimed at the
  structure's snapped center, at most 0.71 from the ordered point. Halted, it has no orders and progress stops;
  resumed with a smart order, it is aimed at the structure; killed, it is reported dead and progress freezes;
  finished, it has no orders (#25).
- A probe warps its building in and walks away, and a drone becomes the building, so only an SCV has anything to
  halt (in game).
- A worker that built a gas structure goes inside it to harvest, briefly, under 120 steps (tool `sweep_tech_tree`).
- Without `fast_build` a supply depot is still going up 120 steps after the order, and done within 600 more
  (tested).
- An add-on is a unit of its own, seen the step after the order at 2% built. It sits 2.5 right of its host and 0.5
  below; with no room the host lifts off to build it elsewhere (#37; tested).
- A warp-in is first seen unfinished and finishes some steps later (#37; tested). A gateway becomes a warp gate by
  itself once Warp Gate is researched, and nothing else makes one (#29; tested).
- The starting townhall is never seen unfinished. An auto-turret has the structure attribute but is first seen
  finished. A queen's creep tumor is first seen at 0%, finishes 240 steps later, then becomes
  `CREEP_TUMOR_BURROWED` (#37).
- A structure cancelled while built is reported dead, and so is a MULE as it expires (#37; tested).
- A neural parasite keeps the tag and hands the unit to the caster's player, alliance and upgrades included (a
  marine read 55 health), within 24 steps, with the `INFESTOR_NEURAL_PARASITE` buff; it returns when the infestor
  dies (#25, #34; tested).
- An order to one larva may be carried out by another larva of the same hatchery, and larva die with their
  hatchery. A morph ordered on several units morphs only one of them (tool `sweep_tech_tree`, tool `sweep_alerts`).
- Only one mothership can stand at a time, arming a nuke needs a factory, and a game starts with 50 minerals (tool
  `sweep_tech_tree`, tool `sweep_alerts`).
- The game disguises a changeling, collapses a tower, lifts a locust into the air and digs a creep tumor in by
  itself (stated).

## Vision, fog, cloak and detection

- A change out of sight, such as a morph in the fog, shows only when the unit is next seen (stated).
- Vision lingers some 60 steps after the unit that saw an enemy dies (#37; tested).
- An enemy that cloaks where it stands stays listed. An undetected enemy observer is listed `CLOAKED` and
  `INVISIBLE`, in sight but not in vision, with no buffs; beside an overseer or a raven it reads `CLOAKED_DETECTED`
  and `IN_VISION`, and a ghost detected so shows `GHOST_CLOAK` (#37; tested).
- A cloaked unit of this player's (an observer, a dark templar, a cloaked ghost) reads `CLOAKED_ALLIED` and
  `IN_VISION` whether the enemy detects it or not: an enemy observer, missile turret and photon cannon each detected
  and shot one (the turret took an observer from 70 to 34), and nothing of its report changed (#37).
- A ghost cloaks one step after the order, and uncloaks one step after the order (#37; tested).
- Burrowing is no cloak: a burrowed zergling reads `NOT_CLOAKED`, and an undetected burrowed enemy is not listed
  (#37).
- A remembered copy reports its cloak as unknown (stated).
- The visibility grid reads 0 for never seen, 1 for seen before and 2 for in sight now; creep is one bit a tile. At
  the start this player's base has creep and vision, and the enemy's start is unexplored (tested).

## Damage, energy and spells

- Shield armor is the shields level: a marine's 6 damage took 5, 4 and 3 off a zealot's shields at levels 1, 2 and
  3. No table holds shield armor (#31).
- A colossus lands 2 hits an attack (stated). Range is measured horizontally (stated).
- Every unit below its most energy regenerates some every step (#37).
- A feedback cost the high templar 50 energy and drained all 100 of a raven's, dealing 50 damage, and left no buff
  and no effect. An EMP cost the ghost 75 and drained 100 of a raven's 150 (#37; tested).
- A storm is the effect `HIGH_TEMPLAR_STORM`, carrying its caster's alliance, and this player's own storm puts the
  storm buff on its own units too (#34; tested).
- A liberation zone is drawn for 2 s before it fires, and a time warp slows nothing for its first 1.79 s; each phase
  has its own pending effect id (#5).
- Since 4.12.0 Microbial Shroud needs no research: an infestor with no upgrades is offered it and casts it (#23).
- A unit attacked that cannot fight back drifts away unless it holds position (tool `sweep_alerts`).

## Buffs

- A melee game puts 45 buffs on units, 5 of them for carrying resources: minerals under one buff for every race and
  a second for rich minerals, gas under a buff of each race's worker. Map zones put buffs on units too (tool
  `sweep_buffs`, #24).
- Workers pick up and deliver minerals every trip, a buff gained and lost each time: all but 14 of the corpus's
  16,882 buff changes (corpus, #37).
- Buff names mislead: concussive shells put `Slow` on what they hit, never `DutchMarauderSlow`; an immortal's
  Barrier is `TakenDamage`, never `ImmortalOverload`, starting on the first hit, lasting 48 steps, with the shields
  held at 100; a ghost holding fire wears `GhostHoldFireB`, not `GhostHoldFire` (tool `sweep_buffs`, #24).
- Stim, Charge, Lunge (`HydraliskFrenzy`) and a banshee's or ghost's cloak buff the unit using them; Concussive
  Shells and Interference Matrix buff the target; a cloaked ghost always wears `GHOST_CLOAK` (#34; tested).
- LockOn and Transfusion are worn by the target, the snipe buff by the ghost while it channels, `CloakField` by the
  mothership and `CloakFieldEffect` by each unit it cloaks, and `Charging` by the zealot (#24).
- `RavenShredderMissileTint` is on an anti-armor missile's target from launch to impact, steps 18 to 52, and the
  armor reduction goes on every unit hit (#24).
- `QueenSpawnLarvaTimer` runs 640 steps, and the larva hatch 30 to 38 steps after it ends; a second inject queues
  behind the first, with nothing showing it (#24).
- A fungal growth showed the step after the cast and was gone 64 steps later (#37; tested).
- A unit warping in wears raw buff 8, `PowerUserWarpable`, from its first step until the step after it finishes; a
  structure a probe warps in wears none (#37).
- An oracle is never offered Stasis Ward, a nexus never Battery Overcharge, and a shield battery puts no buff on what
  it recharges (tool `sweep_buffs`, #24).
- `BuffData` has only an id and a name, and the name is the id's (#23).

## Upgrades and the game's tables

- `ResponseData` asked after an upgrade folds the asking player's upgrades, and only theirs, into the rows: 131 of
  2005 types changed after one upgrade. Asked at the start, it holds the base values both players share (#23;
  tested).
- The rows take in damage, bonuses (a new one too: Infernal Pre-Igniter gives the hellbat a bonus against light),
  range, armor and speed, each by adding. Every level adds the same to a type, but different amounts to different
  types: a marauder gets +1 and +1 against armored (tool `sweep_upgrades`, #30).
- The rows leave out attack speed (Adrenal Glands, Resonating Glaives), shield armor and what abilities and buffs
  do, and do not change for shields levels, Combat Shield, Stim or Concussive Shells. They count Anabolic Synthesis
  whether the unit is on creep or not (tool `sweep_upgrades`, #30).
- The void ray, carrier, sentry, battlecruiser and oracle have no weapon in the rows, yet the first three take
  attack levels (tool `sweep_upgrades`, #30).
- A type's armor plus the armor its units report from upgrades is the upgraded row's armor. Armor levels add 1
  each, Neosteel Armor 2 to terran structures, Chitinous Plating 2, which is no level (#30; corpus).
- Each cyclone attack level adds 1; Flux Vanes makes a void ray 1.21 times as fast, Gravitic Boosters an observer
  1.5, Muscular Augments a hydralisk 1.31 and Anabolic Synthesis an ultralisk 1.18; Pneumatized Carapace speeds up
  a transport overlord too, and Adaptive Talons no lurker (tool `sweep_upgrades`, #30).
- The game reports no enemy upgrade beyond the attack, armor and shield levels on its units in sight, and a level
  belongs to the player's whole upgrade line; an ultralisk's armor is ambiguous, and Metabolic Boost is never
  reported. What is seen implies others: warp gates, burrow, stim, charge, lunge, cloak, a 55-health marine, a
  concussive slow, an interference matrix, a storm (#31, #34).
- Upgrades are never lost (stated).
- `UpgradeData` holds only cost and time. Dead upgrades keep their prices (vehicle plating, ability 852, 100/100),
  and Enhanced Shockwaves has no ability, cost or time (#23, #30).
- Researchable in 5.0.15: Interference Matrix, Caduceus Reactor, Mag-Field Accelerator, Tectonic Destabilizers and
  Nanomuscular Swell, which needs a Hive and since 5.0.14 reuses the `HydraliskFrenzy` ids. No longer researchable:
  Rapid Reignition System, which Caduceus Reactor replaced in 5.0.12, Ventral Sacs, Rapid Deployment, and Microbial
  Shroud, which keeps its 150/150 price and research id but is never offered. A lair offers only Pneumatized
  Carapace and Burrow (tool `sweep_buffs`, #23, #24).
- An armory offers exactly three researches, 855, 861 and 864: vehicle weapons, ship weapons and vehicle-and-ship
  plating. The upgrade table names `ArmoryResearchSwarm` for the plating levels, which an armory is never offered
  and which does nothing when ordered (#5, #23).
- `RequestData` names no unit that performs an ability, gives the ghost, thor, battlecruiser and mothership no
  requirement, has room for only one requirement, and holds no upgrade requirement (tool `sweep_tech_tree`, #29).
  Requirements belong to a type and an ability together: a burrowed roach moves only once Tunneling Claws is
  researched (#29).
- The catalog holds 2005 unit types, 940 of them unnamed skipped ids, 4134 abilities, 306 upgrades, 303 buffs and
  13 effects; 894 ability names repeat, and 23 buff ids share their number with a unit type (#23).
- The `available` flag filters nothing: `MorphZerglingToBaneling` is marked unavailable though zerglings morph, and
  the viking's alias 1940, an empty row no unit ever is, is marked available (#23).
- A morph's cost includes what it came from (an orbital command 550 minerals, the command center's 400 included),
  though the game charges only the difference, and its build time only the morph (25 s). A consumed performer's
  cost is folded in: an extractor costs 75. Costs are whole numbers, and zerglings come in pairs (#23; corpus).
- What the game charges as an ability is ordered is its product's row less that of what the product is made out of,
  which is right for every creation ability but six. A hatchery is charged 300 where that leaves 275, its row
  reading 325 rather than the drone's 50 and a hatchery's 300; a warp gate nothing, though a gateway's 150 stands
  in its row and nothing is recorded as made out of; an overlord transport 25/25, where its row reads the same 100
  an overlord's does; a pair of zerglings 50 minerals and a whole supply, where the row prices one; an auto turret
  nothing but the raven's energy, where its row keeps a price the game no longer charges; and a purification nova
  no supply, though its row carries the disruptor's 3. A reactor and a tech lab need no correction of their own any
  more: their rows read 50/50 and 50/25 (corpus).
- `unit_alias` names the type something morphed from; `is_building` means "needs placing"; an add-on's
  `footprint_radius` is 3.5, the reach past its host's far side; a bare tech lab or reactor is a requirement no unit
  is built as (#23).
- The tables count `AUTO_TURRET` and `REAPER_GRENADE` as terran structures and `STASIS_WARD` as protoss; map units
  have no race (#27).
- Ability names: `link_name` is the command card group (15 protoss builds share one), `button_name` is blank for ten
  and `BurrowDown` for twelve, and `friendly_name` is a sentence such as "Attack Attack". A friendly name of
  `<link_name>_<index>` means a row with no name, and index 30 of every build menu is the cancel slot (#23).

## Abilities and orders

- A unit reports the exact ability it runs. A general id is never offered but is accepted as an order, and the game
  runs the exact one: `GENERAL_MOVE` shows as `GENERAL_MOVE_EXACT`, Attack 3674 runs as 23, `GENERAL_BURROW` burrows
  any unit, and a general research id researches the next level (#23, #28; tested).
- A liberator ordered to siege (2558) reports 2554 running, and unsieging works the same way, though `remaps_to`
  links neither pair (#23).
- The archon: `Morph_Archon` (1766) given to two templar together, high or dark, even 6 apart, walks them to each
  other and merges them, each reporting `Archon_Warp_Target` (1767) aimed at the other; given to one alone it is
  refused. 1767 given to both at one of them merges them too, and given to one does nothing, though it answers
  `Success` (#37; tested).
- A spell ordered at a target out of reach (storm, neural parasite, the raven's and viper's) shows in the caster's
  orders under the id ordered. Hallucinations, Guardian Shield, Chrono Boost, Supply Drop, warp-ins, bunker orders
  and cancels finish at once and show nothing (#33).
- Creation abilities the tables name that are never offered and do nothing: the lurker's 2104 (2332 works), the rich
  refinery's 325 (a plain refinery on a rich geyser works), the baneling's 80 (4121), the auto-turret's 349 (1764,
  for 50 energy), the locust's 2018 (2704) and purification nova's 2546 (2346). Of the refinery rows 320 and 325,
  only 320 works. The rich assimilator's and rich extractor's rows name no creation ability; the plain one builds
  on a rich geyser (#23, #29).
- `RequestQueryAvailableAbilities` leaves out what a unit lacks the tech for, counts an add-on only for the structure
  it is attached to, ignores energy and cooldowns, and offers an unpowered structure nothing that needs power: a
  gateway trains nothing, though a probe is offered a gateway and a forge once a nexus stands, with no pylon (#29).
- A requirement drops out of the answer within 4 steps of its structure leaving the observation; a lifted barracks
  still counts as a barracks, a lowered depot as a depot and a hive as a lair. A ghost is offered 4 steps after its
  academy appears (a tech lab alone is not enough), and weapons level 2 after level 1 (#29; tested).
- A unit is offered only the half of a toggle that fits its state, and the other half up to 22 steps after it
  switches. Cancels and halts are offered only in the state they undo (training, building, a structure going up, a
  cocoon or lurker egg, a channeling ghost, infestor or phoenix, an adept whose shade is out, a nuke arming); a
  cyclone locked on gets none. Unload is offered only while a transport carries something, and a ghost its nuke only
  while one is armed (#29, #33).
- Once Burrow is researched, every burrowing zerg unit is offered every zerg burrow ability, the infested terran's
  included, and burrows as itself: a zergling given the infested terran's reports `ZERGLING_BURROW` (#29, #33).
- A roach is offered the ravager morph once a roach warren stands; a warp gate warps in all six gateway units;
  workers carrying minerals are offered a return fresh ones are not; brood lord and overlord cocoons can move,
  patrol and hold; a battlecruiser has its own attack, move, patrol, hold and stop (#29, #33).
- A building SCV is offered `Halt_TerranBuild` (348), which clears its orders and leaves the structure at its
  progress; the other races' slot-30 rows are offered to nobody and do nothing (#23).
- An order refused as given, with no supply: drones get `NotEnoughFood`. Five SCVs queued with 2 supply left train
  2, and the observation with the second carries a `NotEnoughFood` action error, as it does when the depot they need
  is killed. A depot ordered onto minerals gets `CantFindPlacementLocation`, and one whose ground a sieged tank then
  took a `CantBuildLocationInvalid` action error (tool `sweep_alerts`, #37).
- A second SCV or drone ordered with no minerals left, queued or not, is answered `Success`, never made, raises no
  error, and is left out of the actions the next observation reports (tool `sweep_alerts`, #37; tool
  `sweep_orders`).
- Supply is taken as a unit starts, not as it is queued, and a full cap stops the queue rather than refusing it. A
  barracks with two supply left given five marines takes all five: `food_used` rises by one as the first starts, and
  the four behind it wait at no progress, taking nothing. One given three with the cap full takes all three, answers
  each `Success`, and none of them starts: no progress for 380 steps, no action error, and nothing dropped. Two SCVs
  trained take one supply between them, and cancelling the one that had not started gives none back (tool
  `sweep_orders`).
- A single passenger cannot be unloaded through `RequestAction`: it takes the UI action `ActionCargoPanelUnload`
  after a raw command with ability 0 selects the transport, with `raw_affects_selection` and a feature layer on. The
  medivac's unload abilities unload every passenger at once (stated).
- The item in the middle of a queue can be cancelled, but only the same way: the UI action
  `ActionProductionPanelRemoveFromQueue`, with the structure in the selection. A barracks holding a marine, a
  reaper, a marine, a reaper and a marine, selected by a rally, and asked for index 2: answered `Success`, the third
  goes, and the next observation reports it as a raw `CancelSlot_Queue5` command on the barracks. The same action in
  a game joined with the raw interface and `raw_affects_selection` but no feature layer is answered `Error`, and the
  slot ids sent raw to the selected barracks are answered as they are to an unselected one: `Cancel_Slot` `Error`,
  `CancelSlot_Queue5` and `CancelSlot_QueueCancelToSelection` `NotSupported`. So a selection is not what they
  wanted, and NachOS, which asks for the raw interface alone, can cancel only a structure's last item (tool
  `sweep_orders`).
- Ability 0 is no ability, and Smart is the right-click order. `Cancel_Queue5` and
  `Cancel_QueueCancelToSelection` both remap to `Cancel_Last` (stated).
- A player that gives no orders still has its workers mine: the game gives those orders itself (corpus).
- Every action of a `RequestAction` gets a verdict, in the order sent, 500 in one request included, and the game
  carries them out in that order: of two unqueued moves to one unit the second stands, an unqueued move after a
  queued one clears it, and a queued one after an unqueued one goes behind it. A stim and a move to one marine are
  both carried out, either way round: it moves, stimmed (tool `sweep_orders`).
- Training and research go behind what a structure is making, queued or not: an unqueued train given to a command
  center, hatchery or nexus that is training, or an unqueued research to an engineering bay, forge or evolution
  chamber that is researching, waits its turn as a queued one does. A structure holds 5; a sixth is answered
  `QueueIsFull`. A reactor holds 8 and makes two at once, on a barracks, a factory and a starport alike, and a
  structure with a tech lab or no add-on holds 5 and makes one: given 10 to make in one step, each kept its 8 or its
  5, and exactly 2 or 1 of them carried progress (tool `sweep_orders`).
- A morph or an add-on given to a structure that is making something is refused, `NotSupported`, queued or not: an
  orbital command or a planetary fortress to a command center training an SCV, a lair to a hatchery training a queen,
  a warp gate to a gateway training a zealot, a tech lab to a barracks training a marine. An idle command center
  morphs and an idle barracks builds its tech lab. A gateway that turns into a warp gate by itself once the research
  is done waits for its zealot (tool `sweep_orders`).
- A barracks is busy for one step more than its add-on: in the observation the tech lab or reactor first reads
  finished, the barracks still shows the order that built it, is offered no train, and refuses a marine
  `NotSupported`. A step later it takes it (tool `sweep_orders`).
- Each action is judged against the game as the last step left it, not as the actions before it in the same step
  leave it. When the game steps it carries them out in order, and drops what no longer fits without an error and
  leaves it out of the reported actions. Ten marines sent to a barracks one request at a time in one step are all
  answered `Success`: five are queued, or eight with a reactor, and the rest are dropped. Of seven SCVs in one request
  to a command center, five are queued (tool `sweep_orders`).
- A structure's production can be cancelled through `RequestAction` only from its last item. `Cancel_Last` and a
  structure's own cancel remove the last item, and a busy structure is offered no other: `Cancel_Queue5` for a
  barracks or a bay, `Cancel_QueueCancelToSelection` for a command center. `Cancel_Slot` and the generic `Cancel` are
  answered `Error`, and another structure's cancel and `CancelSlot_Queue5` `NotSupported`. The report names the
  structure's own cancel, whichever was sent. A cancel to an idle structure is answered `Error`, and three cancels to
  a barracks training two marines are all answered `Success`, and two are carried out (tool `sweep_orders`).
- Cancels make no room for what follows them in the same step. A cancel and then a marine to a barracks holding 5:
  the cancel is carried out and the marine refused `QueueIsFull`, and a step later a marine is taken. To one holding
  4, a cancel and then a reaper are both carried out, and the reaper goes last. Cancels and then an orbital command to
  a command center training SCVs, or a cancel and then a tech lab to a barracks training a marine, empty it and
  refuse the morph or add-on `NotSupported`, and a step later it is taken (tool `sweep_orders`).
- What a cancel gives back is the game's own rule, and does not turn on how far it got: all of a train or a
  research, three quarters of a morph, an add-on or a structure going up, rounded up. Seen at real prices, as the
  jump in the purse over the observation the cancel landed in: an SCV 50 of 50 and a research 100/100 of 100/100;
  an orbital command 113 of 150, a planetary fortress 113/113 of 150/150, a tech lab 38/19 of 50/25, and a supply
  depot 75 of 100. An orbital cancelled 400 steps into its morph gives back the same 113 as one cancelled 40 steps
  in, and a depot cancelled half way up the same 75 as one barely started (tool `sweep_orders`).
- A structure part way through something is offered one cancel, and it is its own. A command center morphing is
  offered `Cancel_MorphOrbital` or `Cancel_MorphPlanetaryFortress`, a barracks building an add-on
  `Cancel_BarracksAddOn`, an engineering bay researching `Cancel_Queue5`, a command center training
  `Cancel_QueueCancelToSelection`, and a structure going up `Cancel_BuildInProgress`. `Cancel_Last` is answered
  `Error` by a morph and by an add-on, so the cancel to send is the one the game offers, not the generic one (tool
  `sweep_orders`).
- Which cancel a structure is offered turns on what it is making, and every producer of all three races has one. A
  command center morphing is offered `Cancel_MorphOrbital` or `Cancel_MorphPlanetaryFortress`, by which morph it is
  running; a barracks, a factory and a starport building an add-on their own `Cancel_BarracksAddOn`,
  `Cancel_FactoryAddOn` and `Cancel_StarportAddOn`, either add-on taking the same one; a hatchery morphing
  `Cancel_MorphLair`, a lair `Cancel_MorphHive` and a spire `Cancel_MorphGreaterSpire`; and everything training or
  researching the queue cancel of its own kind -- `Cancel_Queue5` for a barracks, an engineering bay, a gateway, a
  forge, an evolution chamber and the rest, `Cancel_QueueCancelToSelection` for a command center, an orbital
  command, a hatchery, a lair and a hive, `Cancel_QueuePasive` for a nexus and
  `Cancel_QueuePassiveCancelToSelection` for a planetary fortress. It is offered only while the work is going on,
  which is why an idle structure is offered none, and under `fast_build` an add-on is up again within two steps, so
  a structure set to build one has to be read at every step (tool `sweep_tech_tree`).
- An SCV cancelled refunds its 50 minerals by the next observation, whether it was half made or only queued, and the
  refund pays for nothing sent in the same step, to the same structure or another. With 5 minerals, a cancel and then
  an SCV to a command center: the SCV is refused `NotEnoughMinerals`, and a step later the 55 the cancel leaves pay
  for it. With none, 5 cancels to a command center and an orbital command to another, idle one: the orbital is
  refused `NotEnoughMinerals`, and a step later the refunded 250 pay for it (tool `sweep_orders`).
- Every ability a structure that makes something is offered that makes nothing leaves what it is making alone, at
  the progress it stood at. Each was given to a structure part way through a train or a research, and the structure
  went on with it: the rally of a barracks, factory, starport, robotics facility, stargate, nexus, command center,
  orbital command, planetary fortress, hatchery, lair and hive, workers and units alike; a command center's and a
  planetary's load; an orbital's MULE, scan and supply drop; a nexus's recall and energy recharge; a ghost academy's
  nuke; and a planetary fortress's own stop and attack, which a barracks is answered `Error` for (tool
  `sweep_orders`).
- A barracks training marines goes on training when given a rally or a cancel, which drops the last marine; a lift is
  refused `NotSupported`, and a stop, a move or hold position `Error`. Chrono Boost and an inject leave a structure's
  production as it was, and a carrier goes on building interceptors through a stop (tool `sweep_orders`).
- A research another structure of this player's is doing already is answered `Success`, and nothing is researched or
  reported. A level queued behind the one before it, still being researched, is refused `NotSupported` (tool
  `sweep_orders`).
- A research structure researching is offered no research at all, its own included, though it takes every other line
  queued: an engineering bay took infantry weapons 1, infantry armor 1, building armor and hi-sec auto tracking in
  one request, a forge took ground weapons 1, ground armor 1 and shields 1, and an evolution chamber melee 1,
  missile 1 and ground armor 1, each refusing the second level of a line it was already on `NotSupported` (tool
  `sweep_orders`).
- A few abilities act at once and leave a unit's orders as they were, a move carrying on: stim, the banshee's and
  ghost's cloak, the ghost's hold fire, the medivac's boost, Guardian Shield, the mothership's cloak field, the
  oracle's pulsar beam, the void ray's prismatic alignment, the overlord's creep, the adept's shade and the
  hydralisk's lunge. Every other ability a unit is offered replaces its orders when given unqueued: stop and hold
  position, morphs, burrows, sieges and landing, blink, charge, hallucinations, force fields, and every spell aimed
  at a unit or a point, even one within reach. Queued, each goes behind the move, but a cyclone's lock-on, which is
  refused `NotSupported` (tool `sweep_orders`).
- The half of a toggle that turns one off keeps a unit's orders too, as the half that turns it on does. A unit is
  offered the off half only once the on half has taken, and each was then given to a moving unit: the banshee's and
  ghost's cloak, the ghost's hold fire, the oracle's pulsar beam, the overlord's creep and the baneling's attack on
  structures. Every one is answered `Success`, leaves the move first in the unit's orders, and the unit goes on
  closing on its point. A lurker's hold fire is the one that could not be given, since it is offered only burrowed,
  and the game offers a burrowed lurker no move (tool `sweep_orders`).
- One command to several units sends each moving unit to a point of its own around the one ordered, so they keep
  their spacing, but everything else is carried out by one of them only, and charged once: a storm by a templar with
  the energy for it, a pylon by one of two probes, one marine from three barracks given one train (the purse fell
  50 and one of the three carried the order), one research from two engineering bays given one (100/100).
  What cannot take the order is left out, and the verdict is `Success`: a
  supply depot among marines given a move, a dead unit's tag among live ones. The same tag twice counts once (tool
  `sweep_orders`).
- A larva given two drones in one request makes two: the game hands each order to a larva of its choosing, and the
  action it reports names the larva it used, which need not be the one ordered (tool `sweep_orders`).
- A larva keeps no queue. Given a drone, an overlord and a drone in one request, all three answered `Success`, it
  becomes an egg carrying the last of them alone, and the hatchery's own orders stay empty. Every larva of a
  hatchery given a drone becomes a drone egg, and a second order to one already spoken for changes nothing (tool
  `sweep_orders`).
- A warp gate keeps no queue either: given two zealot warp-ins in one request, both answered `Success`, it shows no
  orders at all, then or 16 steps later (tool `sweep_orders`).
- An order to a dead unit's tag or to one never used is answered `Error`, and to an enemy unit
  `YouCantControlThatUnit`. A target of the wrong kind is answered `Error`: a point for a stop or a stim, none for a
  move, a unit for a supply depot (tool `sweep_orders`).
- A builder killed before the game gives up on its build is reported dying and nothing else. An SCV whose site a
  marine of this player's held position on was given up on 108 steps after the order, with
  `CantBuildLocationInvalid`; the same trial with the builder killed at 106 got no error at all, then or twelve
  steps later. So an order whose unit is gone and one the game gave up on never arrive together: the error comes
  while the unit is alive, and once it is gone nothing comes (tool `sweep_orders`).
- A structure killed while it is making something is reported dying and nothing more. A barracks training three
  marines, killed: the next observation still lists it with all three orders, the one after does not list it at all,
  and neither carries an action for it nor an action error. An SCV killed on its way to build is the same. So what a
  unit was making is told from the unit being gone, never from a report (tool `sweep_orders`).
- An enemy structure out of sight is attacked by the tag of the snapshot the observation lists for it. The tag it was
  seen under is refused, `NotSupported` (tool `sweep_orders`).
- A point crosses the protocol as a 32-bit float, so a coordinate the game reports is one exactly, widened; a
  point sent is cut to one on the way out, before the game rounds it down to its own lattice. The two are not
  the same cut: x = 157.123288015625 goes out as 157.123291015625, a whole lattice step above where cutting it
  down alone would land (tool `sweep_orders`; stated).
- The game keeps a point to 1/4096, cut down: a move to x = 157.123456 is carried out and reported as 157.123291, and
  one to 157.124456 as 157.124268. A builder's order shows its structure's site snapped as the structure will stand,
  on a tile corner for an even footprint and a tile center for an odd one: a depot asked at (86.3, 159.7) at
  (86, 160), a barracks asked at (82.3, 158.7) at (82.5, 158.5). The reported action keeps the point as sent (tool
  `sweep_orders`).
- An unqueued order the same as a unit's first, by the ability it runs and the target to the 1/4096, is ignored: it
  is answered `Success`, left out of the reported actions, and costs nothing. Four marines attacking pylons, each
  standing at each of four sites in turn for 896 steps, dealt 1382 to 1402 in all whether their attack was sent once
  or re-sent every step, every 4th or every 16th, each round within two shots of the others; a move re-sent covered the
  same ground, and a gather re-sent while the worker gathered mined as much. It still drops the orders queued behind
  the first. An order that differs is carried out: a gather sent to a worker returning its cargo sends it back to the
  field with it, so one re-sent every 16 steps mined 5 minerals in 1344 steps against 65 (tool `sweep_orders`).
- A builder whose site a unit of this player's holds position on gets a `CantBuildLocationInvalid` action error and
  drops its order. A build order takes its structure's cost as it is given, not once the builder gets there: a depot
  ordered 35 away took 100 of 120 minerals by the next observation, an SCV ordered with the 20 left was answered
  `Success` and never made, and the depot went up once the builder arrived. So a builder never finds its minerals
  spent. A build queued behind a move takes its cost as it is given too: with 100 minerals, an SCV ordered to move
  and then a depot queued had 0 left while still on its way to the first point. With 65, the queued depot is refused
  `NotEnoughMinerals`, the move is carried out, and minerals mined meanwhile bring the depot back no more. A storm
  queued behind a move, its energy gone meanwhile, is dropped with no error. An action error names
  the unit and the ability, and comes in the observation the game gave up in (tool `sweep_orders`).

## Alerts and the camera

- An alert names no unit and no position. It comes once for each thing, in the observation that thing is first seen
  in, several in one observation included (tool `sweep_alerts`, #37; tested).
- `TRAIN_WORKER_COMPLETE` once a worker; `TRAIN_UNIT_COMPLETE` once a unit that is no worker, twice for a pair of
  zerglings, none for a mothership, a warp-in or a morph; `MOTHERSHIP_COMPLETE` instead for a mothership;
  `WARP_IN_COMPLETE` once a warp-in (tool `sweep_alerts`, #37; tested).
- `BUILDING_COMPLETE` once a structure, one a drone became and a nydus worm included, but no add-on;
  `ADD_ON_COMPLETE` once an add-on (tool `sweep_alerts`, #37; tested).
- `MORPH_COMPLETE` for a lair, a baneling, an overseer or a ravager, none for a hellbat; `TRANSFORMATION_COMPLETE`
  for a gateway becoming a warp gate and back, the automatic conversion included; `MERGE_COMPLETE` some 100 steps
  after the archon is first seen (tool `sweep_alerts`, #37; tested).
- `RESEARCH_COMPLETE` for research that is no level (stimpack, combat shield, zergling speed, warp gate, charge);
  `UPGRADE_COMPLETE` for a weapons or armor level, and for a command center becoming an orbital command or a
  planetary fortress (tool `sweep_alerts`, #37; tested).
- `LARVA_HATCHED` once an inject, a few steps before the larva are seen, and never for larva a hatchery makes itself
  (tool `sweep_alerts`, #37).
- `MINERALS_EXHAUSTED` once a field this player mined, in the observation it is gone from (8 of 8, at the same
  steps); `VESPENE_EXHAUSTED` once a geyser (2 of 2); `MULE_EXPIRED` in the observation that reports the MULE dead;
  `NUKE_COMPLETE` for a nuke armed at a ghost academy (tool `sweep_alerts`, #37; tested).
- `NUCLEAR_LAUNCH_DETECTED` once an enemy nuke, aimed at this player's home or where it sees nothing, in the first
  observation after the launch; `NYDUS_WORM_DETECTED` once an enemy worm, seen or not. This player's own nuke raises
  nothing, and its own worm only `BUILDING_COMPLETE` (tool `sweep_alerts`, #37).
- `UNIT_UNDER_ATTACK` and `BUILDING_UNDER_ATTACK` come only for what the camera does not show (5 of 5 off screen,
  0 of 5 on), each unit for itself, and not again for a unit until it has gone some 6000 to 6500 steps without being
  attacked: none after 6069, one after 6524, and one in 24000 steps for a unit attacked every 3000 (tool
  `sweep_alerts`, #37).
- `AlertError` and `TrainError` were never raised, not even by failed orders (tool `sweep_alerts`, #37).
- The camera starts within 2 of the main townhall, with the workers, mineral fields and geysers on screen, and the
  game moves it once, at step 2, by under 2. So until something moves it, an attack on the main raises no alert, and
  the corpus has none (#37; corpus).

## Chat and actions

- This player's own chat message comes back once, in the next observation, under its own player id; every player's
  chat is delivered (#28; tested).
- An action names the ability as it runs: a move as `GENERAL_MOVE_EXACT` (16), a research ordered by its general id
  as its level (1186) (#28; tested).
- The next observation reports every order the game carried out, stepped or realtime, with the step it was carried
  out at: one action per command, naming the units that took it, the ability as it runs, the target as sent and
  whether it was queued. A camera move and an autocast toggle are reported too. An order refused, one answered
  `Success` and not carried out, and one ignored as the same as a unit's first are not (tool `sweep_orders`).
- In a local realtime game, an order shows in the unit's orders and among the reported actions in the same
  observation, the next one (tool `sweep_orders`).

## Terrain, placement and pathing

- One terrain-height byte is an eighth of a unit, and 127 is height 0: bytes 191, 207, 223 and 239 are heights 8,
  10, 12 and 14. The byte is the height at a tile's lower-left corner, matched to about ±0.014; on a ramp it can be
  0.41 off elsewhere in the tile, and beside a cliff the other side's level (#17).
- Ramp corner heights fall on quarters (byte 193 is 8.25, 205 is 9.75). Flat ground stands near its level, but
  Pylon's main base at 12.15 over byte 223 (#17; corpus).
- Next to a cliff a corner holds one level: a tile whose corners split 3 to 1 stands with the three (455 of 457), and
  one split 2 to 2 on the upper side (115 of 115). Corners across a ramp differ by at most 1.25, across a cliff by at
  least 2, and ground levels are 2 apart (#17).
- Torches, Ultralove and Incorporeal raise their ramps up to 0.76 above the heights the game sends. On four ramps of
  the pool, tiles in one row across differ by half a byte, and the next row up any measured ramp is 0.1875 higher
  (#17, #22).
- A unit's z is within 0.05 of its tile center's height 9 times in 10, and within 0.03 interpolated (#17).
- Ramps are walkable and unbuildable, and climb 1 to 2. Bridges, trees and the ground under indestructible doodads
  are all level, walkable and unbuildable, and nothing in `ResponseGameInfo` tells them apart; PylonAIE_v4 has two
  bridges (#17, #22; corpus).
- Nothing pathable or placeable lies outside the playable area, which is 29% of Pylon and 53% of a map on average,
  and no unit can leave it. Grid images are 1 or 8 bits a pixel, each row one y, from the bottom up (#17; corpus).
- At step 0 the pathing grid blocks rocks and only this player's own starting townhall, mineral fields and geysers;
  the placement grid blocks rocks but no resource or townhall (#17; corpus).
- `start_locations` leaves out this player's own, and names each other spawn at the center of its townhall's tile
  (#17; corpus).
- A structure's center snaps to the grid, on a tile corner or a tile center as its footprint is even or odd. A forge
  placed at the edge of buildable ground was moved by the game, from (93, 150) to (90.5, 147.5), and a pylon ordered
  onto unbuildable ground was never made (#25, #32).
- Power comes from a pylon or a warp prism (stated); a warp-in needs a pylon with open ground beside it (tool
  `sweep_alerts`).
- PylonAIE_v4 has no rich geyser (tool `sweep_tech_tree`).

## Score and supply

- The game rounds supply used and army supply down: one zergling left `food_used` at 14, a second made it 15, and a
  third left it at 15 (#28; tested). `food_used` counts units in production, and `food_workers` leaves them out;
  supply left goes negative once supply providers are lost (stated).
- `larva_count` reads 0 with three larva at the hatchery; `warp_gate_count` counts warp gates (#28).
- The score's idle production and idle worker times are in Normal seconds, summed over the idle units (two idle
  drones added 20 over 160 steps), and all 10,232 in the corpus come to whole steps (#28; corpus).
- The score is the value of every unit and structure, finished or not, plus the resources banked; `total_value`
  counts only what is finished, `collection_rate` is per minute, and `spent` counts an order as queued and refunds it
  when cancelled. `used_*` leaves out what was destroyed, and `total_used_*` counts it (stated).
- Recent APM reads 0 through the raw interface (#28).

## The computer opponent

- The very easy computer keeps the game open, leaves the player alone and does not come to the map's middle early,
  but it does attack at some point (tool `sweep_buffs`, tool `sweep_tech_tree`).
- It concedes a game it sees as lost, with nothing left to fight with or its base cleared, which ends the game (tool
  `sweep_alerts`).
- A bare api against the very hard zerg lasts over 60 s and ends in victory or defeat (tested). Against a bot that
  never leaves its base, the computer's army shows up but none of its buildings, and it researches, which its units'
  levels show (corpus).

## Not understood yet

- **A structure that dies just as vision of it lapses**, before the game swaps it for a copy in the fog, is neither
  reported dead nor listed as a copy, so NachOS keeps it stale and never finds it dead (#37).
- **A transfuse's buff** was not seen in game: the probe's marine was killed first (#37).
- **Whether a worker died or became what it was building.** A drone killed while its hatchery is half up is gone
  from the observation exactly as one that became the hatchery is, and neither is reported, so an order to build
  cannot tell the two apart (tool `sweep_orders`).
- **What an ability a structure cannot use while it is busy does to what it is making** is still open for the ones
  the game refuses rather than takes: a lift is answered `NotSupported` and a morph and an add-on likewise, so what
  they would do to a queue was never seen. Everything a producer is offered that it does take leaves what it is
  making alone (tool `sweep_orders`).
