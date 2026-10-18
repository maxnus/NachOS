# Cheats and debug commands

Part of [how the game behaves](README.md), which says how each entry was seen. Read this before a tool or an integration
test turns a cheat on: several change more than their name says.

## Debug requests

- The game answers a debug request with an empty `debug`. In a game of two clients on one machine, the client that
  joined the game made a marine, took the `minerals` cheat and drew, as the one that created it did, and was refused
  none of the three (tool `sweep_requests`, build 75689). A drawing lasts until the next debug request (#22).

## Cheats

- **Every cheat (`DebugGameState`) is a toggle for the whole game**: sent twice, it is off, and in a game of two only
  one player sends it (#28, #37). `minerals`, `all_resources` and `upgrade` hand something out each time they are sent
  instead, and `gas` gives nothing (tool `sweep_requests`, build 75689).
- **The cheats take a few steps to take effect**; the in-game tests wait 8 (tested).
- **`god` multiplies every weapon's damage in `ResponseData` by 10**, as well as making this player's units take no
  damage. The change to the tables is absent in a game's first ten steps, shows within 4 steps once 60 have run, and is
  gone within 16 of turning it off (#30). Never read the tables under it: `tools/sweep_upgrades.py` leaves it off, and
  `tools/sweep_tech_tree.py`, which reads no weapon, turns it on so the computer's attacks kill nothing it reads a
  requirement from.
- **`tech_tree` waives every requirement**, so what a unit is offered under it is not a player's command card: a
  hydralisk den is offered `ResearchFrenzy` before a Hive stands. It grants 42 upgrades within a few steps, campaign
  ones included. It only ever adds ids, so an id still unoffered under it is still dead: a hydralisk is offered every
  race's `BurrowDown` (#23, #28). Under it a barracks without an add-on trains two marines at once, so
  `tools/sweep_orders.py` reads training without it and builds what an order needs instead (tool `sweep_orders`).
- **`upgrade` grants upgrades of every race**, more each time it is sent: 55 the first time, researches and the first
  level of each weapon, armor and shield, and the 15 second levels the next time. It grants campaign upgrades as well as
  the ladder's, so campaign ids show up in what a unit is offered (#23, #28; tool `sweep_requests`, build 75689).
- **`free` makes everything cost nothing, a spell's energy included**: under it a high templar's feedback leaves it the
  energy it had. **`all_resources` only hands out resources**, 5000 minerals and 5000 vespene each time it is sent, with
  nothing topped up as they are spent, so they run dry once add-ons have been rebuilt a few hundred times (#28, #33,
  #37; tool `sweep_requests`, build 75689).
- **`minerals` hands out 5000 minerals each time it is sent, and `gas` no vespene at all**: 216 steps after `gas` a
  player still had none, and none after it was sent twice (tool `sweep_requests`, build 75689).
- **`fast_build` makes building, training and research take seconds**, research on debug-made structures included, which
  is how the sweeps research everything a race has. Neither it nor `free` grants an upgrade (#28, #33, #37).
- **`food` lifts the supply cap** (in game).

## What debug commands create

- **A debug-made unit can appear a few steps late, and the game does not always make what is asked**: not every pylon,
  and no structure on ground it cannot stand on; a photon cannon ordered onto this player's creep was never made (tool
  `sweep_alerts`, #37).
- **A unit made for player 0 is reported as owner 16** (tool `_sandbox`).
- **A debug-made unit can be yours where the real one is nobody's.** A force field a sentry casts is neutral, owner 16,
  and offered nothing, and Shatter ordered on it is refused; made straight onto the map, one is yours and offered
  `Shatter`, which no player can ever reach. Check `alliance` on the real unit before trusting what a made one is
  offered (#23, #33).
- **A creep tumor, plain or a queen's, is made only on creep, and not at every spot on it.** A queen's
  `CREEP_TUMOR_QUEEN` appeared beside a hatchery, at no spot off creep, and not at every spot on it either. A plain
  `CREEP_TUMOR` appeared on a hatchery's creep once `tools/sweep_placement.py` tried a few spots, though #28 saw none
  (#28; tool `sweep_placement`, build 75689).
- **A larva is made only on creep.** Off creep none appeared, and none was reported dead; two made on creep beside a
  hatchery lived on for 2400 steps (in game, build 75689).
- **A debug-made photon cannon did not detect in its first 4 steps** (#37), **and a debug-made forge researches nothing
  unless a pylon powers it** (#31).
- **Setting a unit's shields reads as shields lost** (tested). Set to 1, they regenerate straight away, 2 every 16 steps
  (#41; tested).
- **A vital set to 0 goes to its maximum instead**: `DebugSetUnitValue` with 0 leaves shields full and energy at 200, so
  drain a unit to 1 instead (#41, tool `sweep_orders`).
