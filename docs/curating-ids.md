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
- **Ask without the `tech_tree` cheat on, and research what an answer needs for real.** The cheat waives every
  requirement, so what it offers is not the command card a player has. Under it a hydralisk den is offered
  `ResearchFrenzy`, which looks like a campaign leftover and is not: 5.0.14 reused the campaign's ids for
  Nanomuscular Swell and the Lunge it unlocks, and a den offers the research only once a Hive stands. Under
  `fast_build` a debug-created structure researches in seconds, which is how `tools/sweep_buffs.py` researches
  everything a race has. What else the cheats and debug-created units get wrong is in `docs/cheats.md`.
- **A price is not evidence.** Dead upgrades keep theirs: vehicle plating still answers ability 852 at 100/100
  after fourteen years. Only an entry with no ability, no cost and no research time at all (Enhanced Shockwaves)
  is caught from `RequestData` alone.
- **`friendlyname` in `stableid.json`** carries what an ability is called -- `Research BattlecruiserWeaponRefit`
  named the upgrade that became `YAMATO_CANNON`.

## What has to be in

**The curated ids must cover everything the game's tables point at** -- each unit type's creation ability, each
upgrade's research ability, the generic id those remap to, and every unit type a `tech_alias`,
`tech_requirement` or `unit_alias` names -- **and both directions of anything that toggles.** A creation ability
only ever names the way in: what turns a sieged tank back is nobody's creation ability, so it has to come from
asking a live one what it is offered. Asking answers only half of it, because a unit is offered the half that
matches the state it is in: an uncloaked ghost offers `Behavior_CloakOn_Ghost` and a cloaked one
`Behavior_CloakOff_Ghost`, never both. `GameData` keeps only rows the curated ids name, so an id left out
empties a field instead. The exception is an id that names nothing anyone can order, whether because the game
no longer honors it or because the game does it by itself. Two tests in `tests/test_gamedata.py` hold the rows
that lose their maker, and fail when a new one turns up.

**A buff is curated when a game puts it on a unit**, which `tools/sweep_buffs.py` finds by making it happen: it
researches everything a race has, orders every ability each unit type is offered at a set of targets, and records
each buff that turns up and which unit wears it. Buff names mislead more than any other: a marauder's concussive
shells put `Slow` on their target and never `DutchMarauderSlow`, an immortal's Barrier is `TakenDamage` and never
`ImmortalOverload`, and a ghost holding fire wears `GhostHoldFireB`, not `GhostHoldFire`. One a game reports is
curated even where its raw name says it is only a tint: `RavenShredderMissileTint` marks the unit an anti-armor
missile is flying at.

**A buff or an effect is named after the one unit type that brings it on, then what a player sees**, as an
ability is named after its performer: `MARAUDER_CONCUSSIVE_SHELLS_SLOW`, `QUEEN_INJECTED`, `MARINE_STIMMED`,
`HIGH_TEMPLAR_STORM`. Where no one type is its only source, as with a map's zones or the minerals every worker carries, the name has no unit in it.
Gas is carried under a different buff by each race's worker, so it is `SCV_CARRYING_GAS` and its two siblings.

**A table can name an ability the game no longer honors, and the upgrade table does it too.** It says the three
terran vehicle-and-ship plating upgrades are researched by the `ArmoryResearchSwarm` spelling, which an armory
is never offered and which does nothing when ordered; `ArmoryResearch` is the one it offers and runs. Curate
what the game offers, and let the field that names the dead one come back empty.

**A suffix of `_EXACT` marks the id a unit reports, beside the one you order.** Order `LIBERATOR_SIEGE` and the
liberator's `orders` name `LIBERATOR_SIEGE_EXACT`; the exact id is never offered, and ordering it does nothing.
`remaps_to` links some pairs of this shape and not others -- all four liberator rows leave it empty -- so the
only way to find one is to order the ability in game and read the performer's orders back.

**The upgrade table also keeps upgrades the game has taken out.** `MicrobialShroud` still carries its 150/150
price and its `EvolveAmorphousArmorcloud` research id, but 4.12.0 made the infestor's shroud free: an
infestation pit is never offered that research, with a pit, a lair or a hive, and ordering it puts no order on
the pit and grants nothing, while an infestor holding no upgrades at all is offered the cast and runs it. An
upgrade nobody can ever hold is not in the game, so neither it nor its research is curated. Rapid Reignition
System is another, which 5.0.12 replaced with Caduceus Reactor, and so is Ventral Sacs: a lair is offered only
Pneumatized Carapace and Burrow, and an overlord morphing into a transport grants no upgrade.

**A structure that is offered a research is the proof the research exists**, whichever way the tables point.
Researching everything a race's structures offer, as `tools/sweep_buffs.py` does, found five upgrades the curation
had missed, each added or brought back in a patch: Interference Matrix, Caduceus Reactor, Mag-Field Accelerator,
Tectonic Destabilizers and Nanomuscular Swell.

**A `friendly_name` of `<link_name>_<index>` means the game has no name for the row**, and is a reason to test
before curating. Index 30 of every build menu is the command card's cancel slot, and only the terran one is an
ability: a building SCV is offered `Halt_TerranBuild` (348, named "Halt TerranBuild"), and ordering it clears the
SCV's orders while the structure stands at the progress it reached. The zerg, protoss, queen, raven and nydus
rows in that slot are named `ZergBuild_30` and the like, are offered to nobody in any state -- idle, building, or
the half-built structure a drone became -- and ordering one does nothing. Terran is the only race with anything
to halt, since a probe warps its building in and walks away and a drone becomes the building.

**A raw name ending in a bare id marks a duplicate row**, as `TerranBuild_Refinery_325` does: two entries share
a `link_name` and `button_name`. It does not say which of the pair is the live one -- `BunkerTransport` is, and
so is `CommandCenterTransport_414` over the unsuffixed 412. Pick by what the game offers, or by which of the
two a general id remaps to; never by the name.

**An ability is named after the unit that performs it, then what it does**, as `SUPPLY_DEPOT_LOWER` and
`MEDIVAC_BOOST` always were: `SCV_BUILD_BARRACKS`, `BARRACKS_TRAIN_MARINE`, `LARVA_MORPH_ZERGLING`,
`ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1`, `ZERGLING_BURROW`, `SIEGE_TANK_SIEGE`. The performer comes from
the catalog group the ability sits in -- `TerranBuild` is an SCV's, `LarvaTrain` a larva's -- and carries the
race, so the name drops it where `UpgradeId` has to keep it.

**Build where the performer survives, morph where it is consumed.** An SCV and a probe walk away from what
they put up; a drone becomes it. So `SCV_BUILD_BARRACKS` but `DRONE_MORPH_HATCHERY`, and `HATCHERY_MORPH_LAIR`
for the same reason. The tables say which: a consumed performer's cost is folded into the product, which is
why the game prices an extractor at 75 where a pylon is 100.

Otherwise the verb is the game's own `friendly_name` unless a player would say otherwise -- a larva is
consumed, so `LARVA_MORPH_ZERGLING` rather than the game's "Train Zergling". Where a player has no verb for
a form change, the game's mode name serves: `THOR_HIGH_IMPACT_MODE`, `WARP_PRISM_PHASING_MODE`. An ability
several units can perform is named `GENERAL` in the performer's place: `GENERAL_BUILD_TECH_LAB`,
`GENERAL_BURROW`, `GENERAL_ATTACK`. Every member reads as who does it and what it does, bar `NULL`, which is
id zero and no ability at all.

**A leveled upgrade's general id takes its levels' name without the level**, so
`ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS` sits beside the three leveled ones -- not the catalog's
spelling, or renaming an upgrade leaves its general ability behind. A test in `tests/test_gamedata.py` holds
the two together.

## Refreshing `data/tech_tree.json`

What each unit type is offered, what it needs for each ability and which abilities use up the unit ordered are swept
in game, since `RequestData` has none of it: it names no unit that performs an ability, leaves a ghost, a thor, a
battlecruiser and a mothership without the requirement they have, and holds no upgrade's requirements at all. After a
patch, or after curating ids, run the sweep and then the generator, which writes the build's tech tree to
`sc2nachos/gamedata/_techtree/_build_<build>.py` and reports how many offered abilities no curated id names yet:

    uv run python tools/sweep_tech_tree.py
    uv run python tools/generate_tech_tree.py

A new build's module goes beside the old one, and `gamedata/_techtree/__init__.py` names the one NachOS plays by, so
moving to a new build is a change of that import, which the generator points out when it is missing.

**Where the game's own table is wrong, `sc2nachos/gamedata/_techtree/_overrides.py` says what is right**, by hand,
with the evidence for each entry beside it. It lists the ability that makes a unit type where the table names one that
does nothing, as with a baneling, or none, as with a rich assimilator, and `GameData` puts it in `creation_ability`.
Neither tool writes that file, so a regeneration keeps every entry, and each one is checked on every run: the sweep
orders each override in game, and the generator refuses to write the tech tree unless the sweep saw each make its unit
type. A test in `tests/test_gamedata.py` fails once the game's table names a working ability for a type there, and its entry
can go. Add one only with a trial in game behind it, never to paper over something the sweep cannot reach.

What the game's `RequestQueryAvailableAbilities` answers, which the sweep reads everything off:

- **It leaves out what a unit lacks the tech for**, and an add-on counts only on the structure it is attached to: a
  bare barracks is not offered a marauder while another stands with a tech lab. It ignores energy and cooldowns.
- **A cancel and a halt are offered only in the state they undo**: a barracks is offered its cancel while it trains,
  an SCV a halt while it builds, and a structure going up both. So the sweep sets every structure making something
  and a worker building before it reads them.
- **An unpowered structure is offered nothing it needs power for**, which is `needs_power` and no requirement on a
  pylon. A probe is offered a gateway with a nexus standing and no pylon at all.
- **A requirement leaves the answer within 4 steps of its structure leaving the observation**, and a lifted barracks
  still counts as a barracks, as its `tech_aliases` say. The sweep kills one structure type at a time, with the
  types whose `tech_aliases` name it, so a requirement either of two unrelated structures would meet is found as
  neither. None is known.
- **Once Burrow is researched, every zerg unit that burrows is offered every zerg unit's burrow**, and ordered any of
  them burrows as itself. The sweep orders each ability that makes a unit type on every type offered it, and a type
  that turns into something other than the product is not counted as offered it.
- **The other half of a toggle is offered once the unit has switched**, up to 22 steps after the order, and unloading
  once a transport carries something.
- **A gateway turns into a warp gate on its own once Warp Gate is researched**, so what a gateway trains is tried
  before any research; nothing is found to make a warp gate out of a gateway.

Running it, three things stood in the way, each of which read as a requirement until it was dealt with: the
computer, which attacks at some point (`god`); workers carrying minerals, which are offered a return a fresh one is
not (the starting workers go); and a pylon the game did not make, which left a twilight council unpowered and its
research unread (a pylon goes beside every structure still unpowered). A placeholder, with no tag, stands where a
structure was ordered from the moment it is ordered, so it is no sign that the structure has been started.

## Refreshing `data/upgrades.json`

What each upgrade adds to each unit type is swept in game too, since `UpgradeData` holds only an upgrade's cost and
time. The game folds the upgrades the asking player holds into the unit types' rows of `RequestData`, so
`tools/sweep_upgrades.py` researches one upgrade at a time, asks for the rows again and writes down what changed. The
generator reads this file beside `data/tech_tree.json` and refuses the two from different builds, so after a patch run
both sweeps before it:

    uv run python tools/sweep_upgrades.py
    uv run python tools/generate_tech_tree.py

What the rows take in:

- **Weapon damage, damage bonuses and range, armor and speed.** A bonus can be new: Infernal Pre-Igniter gives a
  hellbat one against light. Every level of a leveled upgrade adds the same, to every unit type.
- **Nothing else an upgrade does.** Adrenal Glands and Resonating Glaives change no weapon's cooldown, and shield
  levels, Combat Shield's health, Stim, Concussive Shells and the spells researched leave the rows as they were.
  Anabolic Synthesis is in them, though in play it counts only off creep.
- **A unit reports how many attack levels it has, but in `armor_upgrade_level` the armor its upgrades add**: an
  ultralisk with Chitinous Plating and three levels reports 5. The sweep writes down whose reported levels each upgrade
  raises, which is `UpgradeData.type`; an upgrade is a level where its curated name ends in the number, so
  Chitinous Plating raises the armor report and is no level. A leveled upgrade's name has to keep its number for this.
- **Shield armor is the shields levels, and no row holds it.** Each level takes one more off every hit a protoss
  unit's shields receive. Nothing in `RequestData` says so, so `Unit.shield_armor` counts the levels instead.

Every run checks what it found in two ways, and the generator refuses to write while either turns something up: once
everything is researched, each row has to be the first one with every change found added up, and after each upgrade,
every unit's type armor with the armor it reports from upgrades has to be its row's armor.

The `god` cheat multiplies every weapon's damage in the rows (`docs/cheats.md`), so this sweep never turns it on, and
reads the rows it adds everything up against before any cheat.

## Refreshing `data/stableid.json`

**The file depends on the map.** SC2 rewrites it on every launch from the loaded map's mod dependencies:
BerlingradAIE (2022) yields 518 ability ids that MagannathaAIE does not, and shifts a couple of dozen shared names
by +312 or +316. So after a patch, launch a current ladder map, copy the file the game wrote
(`~/Documents/StarCraft II/stableid.json` on Windows) over `data/stableid.json`, and run
`uv run python tools/generate_ids.py`.
