# Curating ids

The curated enums in `sc2nachos/ids/` name only what is really in the game, so "does this still exist?" comes up
constantly. Answer it from these.

## Where to look

- **Liquipedia** is the source of record for units, abilities and upgrades, and it dates removals -- a page's
  `Removed Upgrades` section names the patch that took one out. The page URL returns HTTP 403 to automated
  fetches, so read the wikitext through the API instead:
  `https://liquipedia.net/starcraft2/api.php?action=parse&page=Raven_(Legacy_of_the_Void)&prop=wikitext&format=json`
- **What a live structure offers** is the only direct evidence that an upgrade is researchable:
  `query_available_abilities` on a debug-created structure. An armory offers vehicle weapons, ship weapons and
  vehicle and ship plating, and nothing else -- vehicle plating and ship plating are dead halves of a 2012 merge.
- **Ask without the `tech_tree` cheat on, and research the real prerequisites first.** The cheat waives every
  requirement, so what it offers is not the command card a player has. Under it a hydralisk den is offered
  `ResearchFrenzy`, which looks like a campaign leftover but is not: 5.0.14 reused the campaign's ids for
  Nanomuscular Swell and the Lunge it unlocks, and a den offers the research only once a Hive stands. Under
  `fast_build` a debug-created structure researches in seconds; that is how `tools/sweep_buffs.py` researches
  everything a race has. `docs/cheats.md` lists what else the cheats and debug-created units get wrong.
- **A price is not evidence.** Dead upgrades keep theirs: vehicle plating still answers ability 852 at 100/100
  after fourteen years. Only an entry with no ability, no cost and no research time (Enhanced Shockwaves) can be
  caught from `RequestData` alone.
- **`friendlyname` in `stableid.json`** gives an ability's display name -- `Research BattlecruiserWeaponRefit`
  named the upgrade that became `YAMATO_CANNON`.

## What has to be in

**The curated ids must cover everything the game's tables point at** -- each unit type's creation ability, each
upgrade's research ability, the generic id those remap to, and every unit type a `tech_alias`,
`tech_requirement` or `unit_alias` names -- **and both directions of anything that toggles.** A creation ability
names only the way in: no unit's creation ability unsieges a tank, so that ability has to come from asking a live
tank what it is offered. Asking gives only half of it, since a unit is offered only the half that matches its
state: an uncloaked ghost is offered `Behavior_CloakOn_Ghost` and a cloaked one `Behavior_CloakOff_Ghost`, never
both. `GameData` keeps only the rows the curated ids name, so an id left out empties a field. The exception is an
id nobody can order, because the game no longer honors it or does it by itself. Two tests in
`tests/test_gamedata.py` list the rows left without a maker, and fail when a new one turns up.

**A buff is curated when the game puts it on a unit.** `tools/sweep_buffs.py` finds those by making it happen: it
researches everything a race has, orders every ability each unit type is offered at a set of targets, and records
each buff that appears and which unit wears it. Buff names mislead more than any other kind: a marauder's
concussive shells put `Slow` on their target, never `DutchMarauderSlow`; an immortal's Barrier is `TakenDamage`,
never `ImmortalOverload`; and a ghost holding fire wears `GhostHoldFireB`, not `GhostHoldFire`. A buff the game
reports is curated even if its raw name suggests it is only a tint: `RavenShredderMissileTint` marks the target
of an anti-armor missile. The sweep never warps a unit in, so it missed `WARP_GATE_WARPING_IN`, which a unit
wears while warping in; the buff events' in-game test found it.

**A buff or an effect is named after the one unit type that causes it, then what a player sees**, as an
ability is named after its performer: `MARAUDER_CONCUSSIVE_SHELLS_SLOW`, `QUEEN_INJECTED`, `MARINE_STIMMED`,
`HIGH_TEMPLAR_STORM`. Where no single type is the source, as with a map's zones or the minerals every worker
carries, the name has no unit in it. Each race's worker carries gas under its own buff, so those are
`SCV_CARRYING_GAS` and its two siblings.

**A table can name an ability the game no longer honors; the upgrade table does.** It says the three terran
vehicle-and-ship plating levels are researched by `ArmoryResearchSwarm`, which an armory is never offered and
which does nothing when ordered; `ArmoryResearch` is what it offers and runs. Curate what the game offers, and
let the field that names the dead one stay empty.

**An `_EXACT` suffix marks the id a unit reports, as opposed to the one you order.** Order `LIBERATOR_SIEGE` and
the liberator's `orders` name `LIBERATOR_SIEGE_EXACT`; the exact id is never offered, and ordering it does
nothing. `remaps_to` links some such pairs and not others -- all four liberator rows leave it empty -- so the only
way to find one is to order the ability in game and read the performer's orders. A spell has no such twin:
ordered at a target out of reach, a storm, a neural parasite and each of the raven's and viper's spells show in
the caster's orders under the id ordered.

**The upgrade table also keeps upgrades the game has removed.** `MicrobialShroud` still carries its 150/150
price and its `EvolveAmorphousArmorcloud` research id, but 4.12.0 made the infestor's shroud free: an
infestation pit is never offered that research, whether a pit, a lair or a hive stands, and ordering it puts no
order on the pit and grants nothing, while an infestor with no upgrades at all is offered the cast and runs it.
An upgrade nobody can ever hold is not in the game, so neither it nor its research is curated. Rapid Reignition
System is another, replaced by Caduceus Reactor in 5.0.12, and so is Ventral Sacs: a lair is offered only
Pneumatized Carapace and Burrow, and an overlord morphing into a transport grants no upgrade.

**A structure offered a research is proof the research exists**, whatever the tables say. Researching everything
a race's structures offer, as `tools/sweep_buffs.py` does, found five upgrades the curation had missed, each
added or brought back in a patch: Interference Matrix, Caduceus Reactor, Mag-Field Accelerator, Tectonic
Destabilizers and Nanomuscular Swell.

**A `friendly_name` of `<link_name>_<index>` means the game has no name for the row**, so test before curating
it. Index 30 of every build menu is the command card's cancel slot, and only the terran one is an ability: a
building SCV is offered `Halt_TerranBuild` (348, named "Halt TerranBuild"), and ordering it clears the SCV's
orders and leaves the structure at the progress it reached. The zerg, protoss, queen, raven and nydus rows in
that slot are named `ZergBuild_30` and the like, are offered to nobody in any state -- idle, building, or the
half-built structure a drone became -- and ordering one does nothing. Only terran has anything to halt: a probe
warps its building in and walks away, and a drone becomes the building.

**A raw name ending in a bare id, like `TerranBuild_Refinery_325`, marks a duplicate row**: two entries share a
`link_name` and `button_name`. The suffix does not say which of the pair is live -- `BunkerTransport` is, and so
is `CommandCenterTransport_414` over the unsuffixed 412. Pick by what the game offers, or by which of the two a
general id remaps to; never by the name.

**An ability is named after the unit that performs it, then what it does**, as `SUPPLY_DEPOT_LOWER` and
`MEDIVAC_BOOST` always were: `SCV_BUILD_BARRACKS`, `BARRACKS_TRAIN_MARINE`, `LARVA_MORPH_ZERGLING`,
`ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1`, `ZERGLING_BURROW`, `SIEGE_TANK_SIEGE`. The performer comes from
the ability's catalog group -- `TerranBuild` is an SCV's, `LarvaTrain` a larva's. The performer implies the
race, so the name drops it, where `UpgradeId` has to keep it.

**Build where the performer survives, morph where it is consumed.** An SCV and a probe walk away from what
they put up; a drone becomes it. So `SCV_BUILD_BARRACKS` but `DRONE_MORPH_HATCHERY`, and likewise
`HATCHERY_MORPH_LAIR`. The tables tell the two apart: a consumed performer's cost is folded into the product,
which is why an extractor is priced at 75 where a pylon is 100.

Otherwise the verb is the game's own `friendly_name`, unless a player would say it differently -- a larva is
consumed, so `LARVA_MORPH_ZERGLING` rather than the game's "Train Zergling". Where a player has no verb for
a form change, the game's mode name serves: `THOR_HIGH_IMPACT_MODE`, `WARP_PRISM_PHASING_MODE`. An ability
several units can perform has `GENERAL` in the performer's place: `GENERAL_BUILD_TECH_LAB`, `GENERAL_BURROW`,
`GENERAL_ATTACK`. Every member reads as who does it and what it does, except `NULL`, which is id zero and no
ability.

**A leveled upgrade's general id takes its levels' name without the level**:
`ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS` beside the three leveled ones, not the catalog's spelling, or
renaming the upgrade would leave its general ability behind. A test in `tests/test_gamedata.py` keeps the two in
step.

## Refreshing `data/tech_tree.json`

What each unit type is offered, what each ability requires and which abilities consume the unit ordered are swept
in game, since `RequestData` has none of it: it names no performer for any ability, gives the ghost, thor,
battlecruiser and mothership no requirement though they have one, and holds no upgrade requirements at all. After
a patch, or after curating ids, run the sweep and then the generator, which writes the build's tech tree to
`sc2nachos/gamedata/_techtree/_build_<build>.py` and reports how many offered abilities no curated id names yet:

    uv run python tools/sweep_tech_tree.py
    uv run python tools/generate_tech_tree.py

A new build's module goes beside the old one, and `gamedata/_techtree/__init__.py` imports the one NachOS plays by.
Moving to a new build means changing that import; the generator says so when it is missing.

**Creation abilities the game's own table does not name are in `sc2nachos/gamedata/_techtree/_overrides.py`**,
written by hand, each entry with its evidence beside it. Where the table names an ability that does nothing, as for
a baneling, or none, as for a rich assimilator, the entry becomes the type's `creation_ability`; where the table
names one that works, the entry is an extra maker, as a warp gate's warp-ins make what a gateway trains. Neither
tool writes that file, so a regeneration keeps every entry, and every run checks each one: the sweep orders it in
game, and the generator refuses to write the tech tree unless the sweep saw it make its unit type. A test in
`tests/test_gamedata.py` fails once the game's table names an entry's ability for its type, and the entry can go.
Add an entry only with an in-game trial behind it, never to paper over something the sweep cannot reach.

The sweep reads everything off `RequestQueryAvailableAbilities`, which answers as follows:

- **It leaves out what a unit lacks the tech for**, and an add-on counts only for the structure it is attached to:
  a bare barracks is not offered a marauder while another barracks stands with a tech lab. It ignores energy and
  cooldowns.
- **A cancel and a halt are offered only in the state they undo**: a barracks is offered its cancel while it trains,
  an SCV a halt while it builds, and a structure going up both; a cocoon or a lurker egg its cancel while it
  morphs, a phoenix lifting, an infestor controlling and a ghost sniping theirs, likewise an adept whose shade is
  out and a ghost academy arming a nuke. So the sweep sets every structure making something and a worker building,
  reads a unit mid-morph, arms every nuke, and orders every ability aimed at a unit or a point on a fresh unit,
  reading what it is offered 6 steps later.
- **An unpowered structure is offered nothing that needs power.** That is `needs_power`, not a requirement on a
  pylon: a probe is offered a gateway with a nexus standing and no pylon at all.
- **A requirement drops out of the answer within 4 steps of its structure leaving the observation**, and a lifted
  barracks still counts as a barracks, as its `tech_aliases` say. The sweep kills one structure type at a time,
  together with the types whose `tech_aliases` name it, so a requirement that either of two unrelated structures
  would meet is found as neither. None is known.
- **Once Burrow is researched, every burrowing zerg unit is offered every zerg unit's burrow**, and given any of
  them burrows as itself. So the sweep orders each ability that makes a unit type on every type offered it, and a
  type that turns into something other than the product does not count as offered it.
- **The other half of a toggle is offered once the unit has switched**, up to 22 steps after the order, and unload
  once a transport carries something.
- **A gateway turns into a warp gate by itself once Warp Gate is researched**, so the sweep tries what a gateway
  trains before any research; no ability was found that makes a warp gate out of a gateway.
- **A ghost is offered its calldown only while a nuke is armed.** An armed nuke is neither a structure nor an
  upgrade, so the tables say the calldown needs nothing.

Three things got in the way of running it, each reading as a requirement until dealt with: the computer, which
attacks at some point (`god`); workers carrying minerals, which are offered a return that fresh ones are not (the
starting workers are removed); and a pylon the game failed to make, which left a twilight council unpowered and its
research unread (a pylon goes beside every structure still unpowered). A tagless placeholder stands where a
structure was ordered from the moment of the order, so it is no sign that the structure has been started.

## Refreshing `data/upgrades.json`

What each upgrade adds to each unit type is swept in game too, since `UpgradeData` holds only an upgrade's cost and
time. The game folds the asking player's upgrades into the unit type rows of `RequestData`, so
`tools/sweep_upgrades.py` researches one upgrade at a time, asks for the rows again and records what changed. The
generator reads this file beside `data/tech_tree.json` and refuses the two when they come from different builds, so
after a patch run both sweeps before it:

    uv run python tools/sweep_upgrades.py
    uv run python tools/generate_tech_tree.py

What the rows include:

- **Weapon damage, damage bonuses and range, armor and speed.** A bonus can be new: Infernal Pre-Igniter gives a
  hellbat one against light. Each level of a leveled upgrade adds the same as the last, for every unit type.
- **Nothing else an upgrade does.** Adrenal Glands and Resonating Glaives change no weapon's cooldown in the rows,
  and shield levels, Combat Shield's health, Stim, Concussive Shells and researched spells leave them as they were.
  Anabolic Synthesis is in them, though in play it counts only off creep.
- **A unit reports how many attack levels it has, but `armor_upgrade_level` is the armor its upgrades add**: an
  ultralisk with Chitinous Plating and three levels reports 5. The sweep records which reported level each upgrade
  raises, which becomes `UpgradeData.upgrade_type`. An upgrade is a level when its curated name ends in the
  number, so Chitinous Plating raises the armor report but is no level. A leveled upgrade's name has to keep its
  number for this.
- **Shield armor is the shields level, and no row holds it.** Each level takes one more off every hit a protoss
  unit's shields take. Nothing in `RequestData` says so, so `Unit.shield_armor` counts the levels instead.

Every run checks its findings two ways, and the generator refuses to write while either fails: once everything is
researched, each row has to equal the first row plus every change found; and after each upgrade, every unit's type
armor plus the armor it reports from upgrades has to equal its row's armor.

The `god` cheat multiplies every weapon's damage in the rows (`docs/cheats.md`), so this sweep never turns it on,
and reads the baseline rows before any cheat.

## Refreshing `data/stableid.json`

**The file depends on the map.** The game rewrites it on every launch from the loaded map's mod dependencies:
BerlingradAIE (2022) yields 518 ability ids that MagannathaAIE does not, and shifts a couple of dozen shared names
by +312 or +316. So after a patch, launch a current ladder map, copy the file the game wrote
(`~/Documents/StarCraft II/stableid.json` on Windows) over `data/stableid.json`, and run
`uv run python tools/generate_ids.py`.
