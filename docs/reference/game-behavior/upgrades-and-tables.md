# Upgrades and the game's tables

Part of [how the game behaves](README.md), which says how each entry was seen.

- `ResponseData` asked after an upgrade folds the asking player's upgrades, and only theirs, into the rows: 131 of
  2005 types changed after one upgrade. Asked at the start, it holds the base values both players share (#23;
  tested).
- The rows include damage, bonuses (new ones too: Infernal Pre-Igniter gives the hellbat a bonus against light),
  range, armor and speed, each added on. Every level adds the same amount to a type, but different types get
  different amounts: a marauder gets +1 and +1 against armored (tool `sweep_upgrades`, #30).
- The rows leave out attack speed (Adrenal Glands, Resonating Glaives), shield armor and what abilities and buffs do,
  and do not change for shields levels, Combat Shield, Stim or Concussive Shells. They count Anabolic Synthesis whether
  the unit is on creep or not, though in play it speeds an ultralisk only off creep (stated) (tool `sweep_upgrades`,
  #30).
- The void ray, carrier, sentry, battlecruiser and oracle have no weapon in the rows, yet the first three take
  attack levels (tool `sweep_upgrades`, #30).
- A type's armor plus the armor its units report from upgrades is the upgraded row's armor. Armor levels add 1
  each, Neosteel Armor 2 to terran structures, Chitinous Plating 2, which is no level (#30; corpus).
- Each cyclone attack level adds 1; Flux Vanes makes a void ray 1.21 times as fast, Gravitic Boosters an observer
  1.5, Muscular Augments a hydralisk 1.31 and Anabolic Synthesis an ultralisk 1.18; Pneumatized Carapace speeds up
  a transport overlord too, and Adaptive Talons no lurker (tool `sweep_upgrades`, #30).
- The game reports no enemy upgrade beyond the attack, armor and shield levels on its visible units, and a level
  belongs to the player's whole upgrade line; an ultralisk's armor is ambiguous, and Metabolic Boost is never
  reported. Other upgrades can be inferred from what is seen: warp gates, burrow, stim, charge, lunge, cloak, a
  55-health marine, a concussive slow, an interference matrix, a storm (#31, #34).
- Upgrades are never lost (stated).
- `UpgradeData` holds only cost and time. Dead upgrades keep their prices (vehicle plating, ability 852, 100/100),
  and Enhanced Shockwaves has no ability, cost or time (#23, #30).
- Researchable in 5.0.15: Interference Matrix, Caduceus Reactor, Mag-Field Accelerator, Tectonic Destabilizers and
  Nanomuscular Swell, which needs a Hive and since 5.0.14 reuses the `HydraliskFrenzy` ids. No longer researchable:
  Rapid Reignition System, which Caduceus Reactor replaced in 5.0.12, Ventral Sacs, Rapid Deployment, and Microbial
  Shroud, which keeps its 150/150 price and research id but is never offered: an infestation pit is never offered
  it, whether a lair or a hive stands, and ordered it puts no order on the pit and grants nothing. A lair offers
  only Pneumatized Carapace and Burrow, and an overlord morphing into a transport grants no upgrade (tool
  `sweep_buffs`, #23, #24).
- An armory offers exactly three researches, 855, 861 and 864: vehicle weapons, ship weapons and vehicle-and-ship
  plating. The upgrade table names `ArmoryResearchSwarm` for the plating levels, which an armory is never offered
  and which does nothing when ordered (#5, #23).
- `RequestData` names no performer for any ability, gives the ghost, thor, battlecruiser and mothership no
  requirement, has room for only one requirement, and holds no upgrade requirement (tool `sweep_tech_tree`, #29).
  Requirements belong to a type and an ability together: a burrowed roach moves only once Tunneling Claws is
  researched (#29).
- The catalog holds 2005 unit types, 940 of them unnamed skipped ids, 4134 abilities, 306 upgrades, 303 buffs and
  13 effects; 894 ability names repeat, and 23 buff ids share their number with a unit type (#23).
- The `available` flag filters nothing: `MorphZerglingToBaneling` is marked unavailable though zerglings morph, and
  the viking's alias 1940, an empty row no unit ever has as its type, is marked available (#23).
- A morph's row prices it with what it came from (an orbital command 550 minerals, the command center's 400
  included), though the game charges only the difference; its build time covers only the morph (25 s). A consumed
  performer's cost is folded in: an extractor costs 75. Costs are whole numbers, and zerglings come in pairs (#23;
  corpus).
- What the game charges when an ability is ordered is the product's row less the row of what the product is made
  from, and that holds for every creation ability but six. A hatchery is charged 300 where that gives 275, its row
  reading 325 rather than the drone's 50 plus a hatchery's 300; a warp gate nothing, though its row holds a
  gateway's 150 and records nothing it is made from; an overlord transport 25/25, though its row reads the same 100
  as an overlord's; a pair of zerglings 50 minerals and a whole supply, where the row prices one; an auto turret
  nothing but the raven's energy, its row keeping a price the game no longer charges; and a purification nova no
  supply, though its row carries the disruptor's 3. A nuke, 100/100, has no row to derive from at all, since the
  curated ids name no unit type for it. An interceptor's row reads 15, but the table names no ability that makes one,
  and the sweep never saw a carrier offered Build Interceptors (tool `sweep_tech_tree`). A reactor and a tech lab no
  longer need a correction of their own: their rows read 50/50 and 50/25 (corpus).
- Every gas building takes 480 steps from the step it is first read, at build progress 0, to the step it is first read
  done, rich or plain: a refinery, an assimilator and an extractor, each built by its race's worker. The rows give the
  rich assimilator and the rich extractor no build time, and the rich refinery none in the tables a game answers on
  the repo's maps (tool `sweep_orders`, `gas-buildings`, build 75689).
- The tables a game answers on the repo's AIE maps differ from the corpus's in 38 unit rows, in a game against the
  computer and in a game of two alike, and to either player. The corpus was recorded on 2026-09-12, before those map
  files were added; a game recorded as its first one, on the same map, seed and opponent, answers the new tables.
  - The rich refinery's row names no ability and no build time, and the refinery as its alias, as on the ladder
    (AvocaDOS #44), where the corpus's names `TerranBuild_Refinery_325` and 480.
  - The rich assimilator's and the rich extractor's name their plain buildings as aliases.
  - 35 rows the corpus has available are not, and their names are blank: the Mengsk dummies, the sprays, and the
    acceleration and flying inhibitor zones, which also lose the structure attribute and their sight range of 22.
  - Ability 325 loses its placement fields, one buff row its name, and nearly 300 ability rows differ only in which
    empty fields they set. The upgrades and the effects are the same.

  Why is [not understood yet](not-understood-yet.md) (in game, build 75689).
- `unit_alias` names the type a form is of, a siege tank for a sieged tank, but also links types that are not forms
  of each other: an adept's shade names the adept, the queen's creep tumor the creep tumor, four geyser variants the
  plain geyser, the icy and `_EX1` rocks a plain one, and each changeling the plain changeling (corpus).
- `is_building` means "needs placing"; an add-on's `footprint_radius` is 3.5, the reach past its host's far side; a
  bare tech lab or reactor is a requirement, and no unit is ever built as one (#23).
- The tables count `AUTO_TURRET` and `REAPER_GRENADE` as terran structures and `STASIS_WARD` as protoss; map units
  have no race (#27).
- Ability names: `link_name` is the command card group (15 protoss builds share one), `button_name` is blank for ten
  and `BurrowDown` for twelve, and `friendly_name` is a sentence such as "Attack Attack". A friendly name of
  `<link_name>_<index>` means a row with no name, and index 30 of every build menu is the cancel slot (#23).
