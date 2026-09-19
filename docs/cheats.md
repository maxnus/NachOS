# Debug cheats

What each of the game's debug cheats (`DebugGameState`) does to what the game reports, as measured in game. Read this
before a tool or an integration test turns one on: several change more than their name says. Each is a toggle, so
sending one twice turns it off again.

- **`god` multiplies every weapon's damage in `ResponseData` by 10**, besides making this player's units take no
  damage. It shows a few steps after it is turned on -- not within the first ten steps of a game, within four steps
  once the game has run for sixty -- and is gone within 16 steps of turning it off. Never read the tables under it:
  `tools/sweep_upgrades.py` leaves it off, and `tools/sweep_tech_tree.py`, which reads no weapon, turns it on so the
  computer's attacks kill nothing a requirement is read off.
- **`tech_tree` waives every requirement**, so what a unit is offered under it is not the command card a player has:
  a hydralisk den is offered `ResearchFrenzy` before a Hive stands. It also hands the player 42 upgrades within a few
  steps. It can only add ids, so a dead id staying unoffered under it still counts as dead. It changes production
  too: under it a barracks without an add-on trains two marines at once, so `tools/sweep_orders.py` reads training
  without it and makes what an order needs instead.
- **`upgrade` grants campaign upgrades** as well as the ladder's, which lets campaign ids into what a unit is offered.
- **`free` makes everything cost nothing, a spell's energy included; `all_resources` only hands out resources**, which
  run dry once add-ons have been rebuilt a few hundred times. Under `free` a high templar's feedback leaves it the
  energy it had.
- **`fast_build` makes building, training and research take seconds**, which is how the sweeps research everything a
  race has. It hands out no upgrade by itself, and neither does `free`.
- **`food` lifts the supply cap.**
- **`minerals` hands out 5000 minerals, and `gas` no vespene at all**: 216 steps after `gas` a player still had none.

What debug commands make is not always what a player would have:

- **A debug-created unit can be yours where the real one is nobody's.** A force field a sentry casts is a neutral
  unit, owner 16, offered nothing; created straight onto the map it is yours and offered `Shatter`, which no player
  can ever reach. Check `alliance` on the real thing before believing what a created one is offered.
- **A creep tumor is created only on creep, and a plain `CREEP_TUMOR` never.** A queen's `CREEP_TUMOR_QUEEN` turned up
  beside a hatchery and at no spot off creep, and not at every spot on it either.
- **A vital set to 0 is set to its most.** `DebugSetUnitValue` with 0 fills a unit's energy or shields rather than
  emptying them, so a unit is drained to 1.
