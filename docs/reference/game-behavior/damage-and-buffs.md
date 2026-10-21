# Damage, energy, spells and buffs

Part of [how the game behaves](README.md), which says how each entry was seen.

## Damage, energy and spells

- Shield armor is the shields level: a marine's 6 damage took 5, 4 and 3 off a zealot's shields at levels 1, 2 and
  3. No table holds shield armor (#31).
- Every unit below its maximum energy regenerates some every step (#37).
- A feedback cost the high templar 50 energy and drained all 100 of a raven's, dealing 50 damage, and left no buff
  and no effect. An EMP cost the ghost 75 and drained 100 of a raven's 150 (#37; tested).
- A storm is the effect `HIGH_TEMPLAR_STORM`, carrying its caster's alliance, and this player's own storm puts the
  storm buff on its own units too (#34; tested).
- A liberation zone is drawn for 2 s before it fires, and a time warp slows nothing for its first 1.79 s; each phase
  has its own pending effect id (#5).
- Since 4.12.0 Microbial Shroud needs no research: an infestor with no upgrades is offered it and casts it (#23).
- A unit under attack that cannot fight back drifts away unless it holds position (tool `sweep_alerts`).

## Reach, damage and attack speed

- A unit fires at another while their centers are no farther apart than its weapon's range and both radii, across or
  diagonally alike, to within 0.01: a marine at an SCV out to 5.75, its 5 and two radii of 0.375, at an overlord and at
  a colossus 6.375, a viking at an overlord 10.75 and an unsieged tank at an SCV 8.25. Range is measured horizontally
  (stated) (tool `sweep_combat`, build 75689).
- Air weapons hit a colossus, which does not read flying: a viking, with no ground weapon, fired at one out to 10.75, as
  at an overlord (tool `sweep_combat`, build 75689).
- An attacker that cannot move reaches some 0.1 less: a missile turret fired at an overlord out to 9.00 to 9.06 of its
  9.125, a planetary fortress at an SCV to 9.01 of 9.125, a spine crawler to 8.41 of 8.5 and a sieged tank to 14.12 of
  14.25. A structure is reached as though its radius were smaller, and more so across than diagonally: a supply depot's
  1.25 as 0.99 across and 1.05 diagonally, a barracks's 1.8125 as 1.57 and 1.76, a command center's 2.75 as 2.66 across.
  Runs agreed to within 0.04, though the game moves a unit made beside a structure where something stands in the way,
  which left some trials without an answer (tool `sweep_combat`, build 75689).
- The tables give the battlecruiser, the oracle, the void ray and the sentry no weapon, though they attack: a
  battlecruiser 8 to the ground and 5 to the air every 3 to 4 steps, 3.6 on average, at range 6; a void ray 6, and 4
  more against armored, every 8 steps at 6, to both; a sentry 6 every 16 steps at 5, to both; and an oracle with its
  beam on, against the ground, 22 to an SCV and 15 to a roach every 14 steps at 4. Each attack level adds 1 to the first
  three and nothing to the oracle's, and armor comes off as for any weapon (tool `sweep_combat`, build 75689).
- A hit takes off its weapon's damage and the bonus against the target's attributes, the attacker's upgrades in both.
  From health it takes that less the armor, but at least 0.5; from shields, first, that less the shields level, and what
  is more than the shields goes on to health less the armor, at nothing at least. A marine's 6 took 5 off a roach, with
  armor 1, and 0.5 off an ultralisk with 7; and 6 off a zealot's shields, 3 at shields level 3. An upgraded zealot's
  blade of 11, on a zealot with 2 shields, shields level 3 and armor 4, took the 2 and 2 of its health: 11 less 3, less
  2, less 4. A marauder's 10 and 10 against armored took 19 off a roach and 20 off a stalker's shields, 17 at level 3
  (tool `sweep_combat`, build 75689).
- A zealot's attack is 2 hits of 8, 4 steps apart, and a colossus's 2 beams land together, 15 each on a zergling (tool
  `sweep_combat`, build 75689).
- The tables' cooldowns leave out what speeds an attack up. Timed over six shots at a command center, Adrenal Glands
  brought a zergling's from 11.7 steps to 8.0, 1.4 times as fast, Resonating Glaives an adept's from 36.8 to 25.0, 1.45
  times, and stim a marine's from 13.7 to 9.3 and a marauder's from 25.2 to 16.4, 1.5 times; over six shots the delay
  below leaves each to within a few percent (tool `sweep_combat`, build 75689).
- A unit does not fire every cooldown. Each shot adds a delay drawn evenly from -1 to 2 steps, -1/16 to 1/8 of a game
  second as the game's data editor gives weapons (stated), to the tables' cooldown. The cooldown then falls by 1 a
  step, never below 0, and the part of a step by which it passes 0 counts toward the next, so shots land on whole steps
  but come every cooldown and half a step on average. Worked back from the cooldown shown at each of 200 shots of each
  of eight types at a command center, the delays lay between -0.9993 and 1.9971, spread evenly, each unrelated to the
  last; the mean gap was the cooldown and 0.40 to 0.60 steps, in two runs: a marine's, of cooldown 13.77, 13 to 16
  steps and once 12, 14.33 on average; a zergling's, of 11.14, 10 to 13, 11.67 (tool `sweep_combat`, build 75689).
- A cooldown can read 0 for a few steps before the unit fires. Traced over ten shots, a marauder's and a zealot's rose
  in the step it ran out, a marine's after 1 step at 0, and a zergling's, a stalker's, a roach's, a hydralisk's and a
  siege tank's after 3, at every shot. Those steps count toward the next cooldown: the cooldown shown when it rises is
  that much lower, and the unit fires no less often. An instant attack's damage lands in the step the cooldown rises,
  a marine's, a zergling's, a zealot's first blade and a siege tank's, and a missile's later, from beside a command
  center a hydralisk's 3 steps later, a marauder's 4 and a stalker's and a roach's 5 (tool `sweep_combat`, build 75689).

## Buffs

- A melee game puts 45 buffs on units, 5 of them for carrying resources: minerals under one buff for every race and
  another for rich minerals, gas under a buff per race's worker. Map zones put buffs on units too (tool
  `sweep_buffs`, #24).
- Workers gain and lose a buff on every mineral trip, picking up and delivering: all but 13 of the corpus's 16,760
  buff changes are those (corpus, #37).
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
  behind the first, and nothing shows it (#24). The hatchery's `buff_duration_remain` counts it down a step at a
  time, against a `buff_duration_max` of 640 (tool `sweep_orders`, build 75689).
- A fungal growth showed the step after the cast and was gone 64 steps later (#37; tested).
- A unit warping in wears raw buff 8, `PowerUserWarpable`, from its first step until the step after it finishes; a
  structure a probe warps in wears none (#37).
- An oracle is never offered Stasis Ward, a nexus never Battery Overcharge, and a shield battery puts no buff on what
  it recharges (tool `sweep_buffs`, #24).
- `BuffData` has only an id and a name, and the name is the id's own (#23).
