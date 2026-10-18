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
  `ResearchFrenzy`, which looks like a campaign leftover but is not: 5.0.14 reused the campaign's ids for Nanomuscular
  Swell and the Lunge it unlocks, and a den offers the research only once a Hive stands. Under `fast_build` a
  debug-created structure researches in seconds; that is how `tools/sweep_buffs.py` researches everything a race has.
  `docs/reference/game-behavior/cheats.md` lists what else the cheats and debug-created units get wrong.
- **A price is not evidence.** Dead upgrades keep theirs: vehicle plating still answers ability 852 at 100/100
  after fourteen years. Only an entry with no ability, no cost and no research time (Enhanced Shockwaves) can be
  caught from `RequestData` alone.
- **`friendlyname` in `stableid.json`** gives an ability's display name -- `Research BattlecruiserWeaponRefit`
  named the upgrade that became `YAMATO_CANNON`.

## What has to be in

**The curated ids must cover everything the game's tables point at** -- each unit type's creation ability, each
upgrade's research ability, the action each unit type's own id of one stands for, and every unit type a `tech_alias`,
`tech_requirement` or `unit_alias` names -- **and both directions of anything that toggles.** A creation ability
names only the way in: no unit's creation ability unsieges a tank, so that ability has to come from asking a live
tank what it is offered. Asking gives only half of it, since a unit is offered only the half that matches its
state: an uncloaked ghost is offered `Behavior_CloakOn_Ghost` and a cloaked one `Behavior_CloakOff_Ghost`, never
both. `GameData` keeps only the rows the curated ids name, so an id left out empties a field. The exception is an
id nobody can order, because the game no longer honors it or does it by itself. Two tests in
`tests/test_gamedata.py` list the rows left without a maker, and fail when a new one turns up.

**A buff is curated when the game puts it on a unit.** `tools/sweep_buffs.py` finds those by making it happen: it
researches everything a race has, orders every ability each unit type is offered at a set of targets, and records each
buff that appears and which unit wears it. Buff names mislead more than any other kind
([game behavior](../reference/game-behavior/damage-and-buffs.md#buffs)). A buff the game reports is curated even if its
raw name suggests it is only a tint: `RavenShredderMissileTint` marks the target of an anti-armor missile. The sweep
never warps a unit in, so it missed `WARP_GATE_WARPING_IN`, which a unit wears while warping in; the buff events'
in-game test found it.

**A buff or an effect is named after the one unit type that causes it, then what a player sees**, as an
ability is named after its performer: `MARAUDER_CONCUSSIVE_SHELLS_SLOW`, `QUEEN_INJECTED`, `MARINE_STIMMED`,
`HIGH_TEMPLAR_STORM`. Where no single type is the source, as with a map's zones or the minerals every worker
carries, the name has no unit in it. Each race's worker carries gas under its own buff, so those are
`SCV_CARRYING_GAS` and its two siblings.

**A table can name an ability the game no longer honors**, as the upgrade table does for the vehicle-and-ship plating
levels ([game behavior](../reference/game-behavior/upgrades-and-tables.md)). Curate what the game offers, and let the
field that names the dead one stay empty.

**An action several unit types perform has one id, and each type's own is not curated.** The game offers a barracks
`Lift_Barracks` and a starport `Lift_Starport`, each reports its own while it runs it, and either takes `Lift` as an
order and runs its own (in game). NachOS curates `Lift` as `LIFT` and reads each type's own as it, from
`AbilityId._REMAPPED_IDS`. Where the game's table gives a `remaps_to`, it says which; collapse a family only where its
members do one thing for different performers. A leveled research keeps one id per level, since each makes its own
upgrade, and its general id is not curated: nothing is offered it. The generic cancel's members are one action too:
a unit is offered one at a time, and `Cancel` does it (tool `sweep_orders`, `cancels-whole`).

**A unit can report another id than the one it was ordered, which nothing links to it.** That id is not curated but read
as the one ordered, from the same table, as a sieging liberator's is
([game behavior](../reference/game-behavior/abilities-and-orders.md#ids-what-a-unit-is-offered-ordered-and-runs)). The
only way to find such a pair is to order the ability in game and read the performer's orders; a spell has none.

**The upgrade table also keeps upgrades the game has removed**, Microbial Shroud, Rapid Reignition System and Ventral
Sacs among them ([game behavior](../reference/game-behavior/upgrades-and-tables.md)). An upgrade nobody can ever hold is
not in the game, so neither it nor its research is curated.

**A structure offered a research is proof the research exists**, whatever the tables say. Researching everything
a race's structures offer, as `tools/sweep_buffs.py` does, found five upgrades the curation had missed, each
added or brought back in a patch: Interference Matrix, Caduceus Reactor, Mag-Field Accelerator, Tectonic
Destabilizers and Nanomuscular Swell.

**A `friendly_name` of `<link_name>_<index>` means the game has no name for the row**, so test before curating it.
Index 30 of every build menu is the command card's cancel slot, and only the terran one, `Halt_TerranBuild`, is an
ability ([game behavior](../reference/game-behavior/abilities-and-orders.md#what-a-unit-is-offered)).

**A raw name ending in a bare id, like `TerranBuild_Refinery_325`, marks a duplicate row**: two entries share a
`link_name` and `button_name`. The suffix does not say which of the pair is live -- `BunkerTransport` is, and so
is `CommandCenterTransport_414` over the unsuffixed 412. Pick by what the game offers, or by which of the two the
table remaps to the action's id; never by the name.

**An ability is named after the unit that performs it, then what it does**, as `SUPPLY_DEPOT_LOWER` and
`MEDIVAC_BOOST` always were: `SCV_BUILD_BARRACKS`, `BARRACKS_TRAIN_MARINE`, `LARVA_MORPH_ZERGLING`,
`ENGINEERING_BAY_RESEARCH_INFANTRY_ARMOR_1`, `GHOST_SNIPE`, `SUPPLY_DEPOT_LOWER`. The performer comes from
the ability's catalog group -- `TerranBuild` is an SCV's, `LarvaTrain` a larva's. The performer implies the
race, so the name drops it, where `UpgradeId` has to keep it.

**Build where the performer survives, morph where it is consumed.** An SCV and a probe walk away from what
they put up; a drone becomes it. So `SCV_BUILD_BARRACKS` but `DRONE_MORPH_HATCHERY`, and likewise
`HATCHERY_MORPH_LAIR`. The tables tell the two apart: a consumed performer's cost is folded into the product,
which is why an extractor is priced at 75 where a pylon is 100.

Otherwise the verb is the game's own `friendly_name`, unless a player would say it differently -- a larva is
consumed, so `LARVA_MORPH_ZERGLING` rather than the game's "Train Zergling". Where a player has no verb for
a form change, the game's mode name serves: `THOR_HIGH_IMPACT_MODE`, `WARP_PRISM_PHASING_MODE`. An ability
several units can perform is named by what it does alone: `BUILD_TECH_LAB`, `BURROW`, `ATTACK`. Every member reads
as what it does, and who does it where one unit type does, except `NULL`, which is id zero and no ability.

## Refreshing `data/tech_tree.json`

What each unit type is offered, what each ability requires and which abilities consume the unit ordered are swept in
game, since `RequestData` has none of it ([game behavior](../reference/game-behavior/upgrades-and-tables.md)). After a
patch, or after curating ids, run the sweep and then the generator, which writes the build's tech tree to
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

**What every unit of a command carries out is `MULTI_UNIT_ABILITIES` in the same file**, which `AbilityData.multi_unit`
reads; any other ability goes to one unit at a time. After a patch, `uv run python tools/sweep_orders.py
group-abilities-terran group-abilities-protoss group-abilities-zerg` gives each ability that costs nothing to two units
at once: one it classes `both` belongs there, one it classes `one` does not, and one it classes `no trace` or
differently from run to run needs a closer look. A new ability is refused for a group until it is listed.

The sweep reads everything off `RequestQueryAvailableAbilities`, whose answers are in
[game behavior](../reference/game-behavior/abilities-and-orders.md#what-a-unit-is-offered). What it does about them:

- **A cancel and a halt are offered only in the state they undo**, so the sweep sets every structure making something
  and a worker building each structure it can, reads a unit mid-morph, arms every nuke, and orders every ability aimed
  at a unit or a point on a fresh unit, reading what it is offered 6 steps later.
- **An unpowered structure is offered nothing that needs power**, which becomes `needs_power`, not a requirement on a
  pylon.
- **A requirement drops out of the answer within 4 steps of its structure leaving the observation**, and a lifted
  barracks still counts as a barracks, so the sweep kills one structure type at a time, together with the types whose
  `tech_aliases` name it. A requirement that either of two unrelated structures would meet is found as neither; none
  is known.
- **Every burrowing zerg unit is offered every zerg unit's burrow once Burrow is researched**, and burrows as itself,
  so the sweep orders each ability that makes a unit type on every type offered it, and a type that turns into
  something other than the product does not count as offered it.
- **A gateway turns into a warp gate by itself once Warp Gate is researched**, so the sweep tries what a gateway
  trains before any research; no ability was found that makes a warp gate out of a gateway.
- **A ghost is offered its calldown only while a nuke is armed**, which is neither a structure nor an upgrade, so the
  tables say the calldown needs nothing.

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

What the rows include and leave out is in [game behavior](../reference/game-behavior/upgrades-and-tables.md). Two things
follow for the sweep and the code:

- **A unit reports how many attack levels it has, but `armor_upgrade_level` is the armor its upgrades add**, so the
  sweep records which reported level each upgrade raises, which becomes `UpgradeData.upgrade_type`. An upgrade is a
  level when its curated name ends in the number, so Chitinous Plating raises the armor report but is no level. A
  leveled upgrade's name has to keep its number for this.
- **No row holds shield armor**, which is the shields level
  ([game behavior](../reference/game-behavior/damage-and-buffs.md#damage-energy-and-spells)), so `Unit.shield_armor`
  counts the levels.

Every run checks its findings two ways, and the generator refuses to write while either fails: once everything is
researched, each row has to equal the first row plus every change found; and after each upgrade, every unit's type
armor plus the armor it reports from upgrades has to equal its row's armor.

The `god` cheat multiplies every weapon's damage in the rows (`docs/reference/game-behavior/cheats.md`), so this sweep
never turns it on, and reads the baseline rows before any cheat.

## Refreshing `data/footprints.json`

The tiles a unit covers where it stands are measured in game, since `RequestData` gives a structure only its
building ability's `footprint_radius` and the map's own units nothing. `tools/sweep_placement.py footprints` makes
every curated structure type and neutral type in turn and asks which tiles around it would take a one-tile structure;
the generator writes them to `sc2nachos/gamedata/_footprints.py`, which `UnitTypeData.footprint` reads. After a patch,
or after curating a new neutral type, run both:

    uv run python tools/sweep_placement.py footprints
    uv run python tools/generate_footprints.py

The generator refuses a footprint that reaches the edge of the tiles the sweep asked about, or whose tiles do not
center on where the unit stood as a footprint centers.

## Refreshing `data/stableid.json`

**The file depends on the map**: the game rewrites it on every launch from the loaded map's mod dependencies
([game behavior](../reference/game-behavior/protocol.md#launching-maps-and-players)). So after a patch, launch a current
ladder map, copy the file the game wrote (`~/Documents/StarCraft II/stableid.json` on Windows) over
`data/stableid.json`, and run `uv run python tools/generate_ids.py`.