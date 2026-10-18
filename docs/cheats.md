# Debug cheats

What each debug cheat (`DebugGameState`) does to what the game reports, as measured in game. Read this before a tool
or an integration test turns one on: several change more than their name says. Each is a toggle: sending it twice
turns it off again, but for `minerals`, `all_resources` and `upgrade`, which hand something out each time they are
sent, and `gas`, which gives nothing.

- **`god` multiplies every weapon's damage in `ResponseData` by 10**, as well as making this player's units take no
  damage. The change shows a few steps after it is turned on -- not within the first ten steps of a game, within
  four steps once the game has run for sixty -- and is gone within 16 steps of turning it off. Never read the tables
  under it: `tools/sweep_upgrades.py` leaves it off, and `tools/sweep_tech_tree.py`, which reads no weapon, turns it
  on so the computer's attacks kill nothing it reads a requirement from.
- **`tech_tree` waives every requirement**, so what a unit is offered under it is not a player's command card: a
  hydralisk den is offered `ResearchFrenzy` before a Hive stands. It also hands the player 42 upgrades within a few
  steps. It can only add ids, so an id still unoffered under it is still dead. It changes production too: under it a
  barracks without an add-on trains two marines at once, so `tools/sweep_orders.py` reads training without it and
  builds what an order needs instead.
- **`upgrade` grants upgrades of every race**, more each time it is sent: 55 the first time, researches and the first
  level of each weapon, armor and shield, and the 15 second levels the next time (tool `sweep_requests`, build 75689).
  It grants campaign upgrades as well as the ladder's, so campaign ids show up in what a unit is offered.
- **`free` makes everything cost nothing, a spell's energy included; `all_resources` only hands out resources**, which
  run dry once add-ons have been rebuilt a few hundred times: 5000 minerals and 5000 vespene each time it is sent, with
  nothing topped up as they are spent (tool `sweep_requests`, build 75689). Under `free` a high templar's feedback
  leaves it the energy it had.
- **`fast_build` makes building, training and research take seconds**, which is how the sweeps research everything a
  race has. It hands out no upgrade by itself, and neither does `free`.
- **`food` lifts the supply cap.**
- **`minerals` hands out 5000 minerals each time it is sent, and `gas` no vespene at all**: 216 steps after `gas` a
  player still had none, and none after it was sent twice (tool `sweep_requests`, build 75689).

What debug commands create is not always what a player would have:

- **A debug-created unit can be yours where the real one is nobody's.** A force field a sentry casts is a neutral
  unit, owner 16, offered nothing; created straight onto the map it is yours and offered `Shatter`, which no player
  can ever reach. Check `alliance` on the real unit before trusting what a created one is offered.
- **A creep tumor is created only on creep, and not at every spot on it.** Asked of a debug command, a queen's
  `CREEP_TUMOR_QUEEN` appeared beside a hatchery, at no spot off creep, and not at every spot on it either. A plain
  `CREEP_TUMOR` appeared on a hatchery's creep once `tools/sweep_placement.py` tried a few spots (build 75689), though
  #28 saw none.
- **A larva is created only on creep.** Off creep a debug command made none: none appeared, and none was reported
  dead. Two made on creep beside a hatchery lived on for 2400 steps (build 75689).
- **A vital set to 0 is set to its maximum.** `DebugSetUnitValue` with 0 fills a unit's energy or shields instead of
  emptying them, so drain a unit to 1 instead.
