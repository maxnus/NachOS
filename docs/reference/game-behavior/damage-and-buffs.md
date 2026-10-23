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

## Repair

- **An SCV repairs a unit to its whole health in the build steps of each of its stages that cost something**, give or
  take 10 steps, for 44 of the 50 types the tables call Terran and mechanical: a supply depot 481, a barracks 1045, a
  siege tank 726, a viking 668, a battlecruiser 1442, a thor 964, an orbital command 2168 (a command center's 1600 and
  its own 560), a planetary fortress 2410, and the banshee farthest off, 970 of 960. A form switched into for nothing is
  repaired as the form it switched from: a lowered depot, a sieged tank or liberator, a landed viking, a lifted
  barracks, factory, starport, command center or orbital, and a thor in high-impact mode. The tables give each switch
  build steps of its own, 21 to 69, which do not count (tool `sweep_repair`, build 75689).
- **Six types break that rule** (tool `sweep_repair`, build 75689):
  - a MULE takes 270 steps, as an SCV does, though the tables give a MULE none;
  - an auto-turret 800, against its 16;
  - a widow mine 320, burrowed or not, against its 480;
  - a reactor and a tech lab standing on their own, as a structure lifting off leaves them, 800 and 400, against the
    2 the tables give them. On a barracks, a factory or a starport, the tables give them 800 and 400, and a repair
    takes that.
- **A whole repair costs a quarter of what the type costs, whatever it morphed from included**, minerals and vespene
  alike, for all 50 types, the six above among them: from half health, 13 minerals for a supply depot, 19 and 16
  vespene for a siege tank, 50 and 38 for a battlecruiser, and 69 for an orbital command, of the 550 that it and its
  command center cost. The resources are taken one at a time as the health comes back, the first as the repair
  starts, so a part repair costs its share rounded up (tool `sweep_repair`, build 75689).
- **An air unit hovering over the middle of a command center was never repaired.** A viking and a battlecruiser halved
  by a debug command, and a medivac and a viking shot by this player's marines, were each answered `Success`, but the
  SCV held the order 32 to 48 steps and dropped it, and their health never rose. Hovering beside the SCV, a viking, a
  medivac and a battlecruiser were repaired (#NNN).
- A repair is refused `MustTargetMechanicalUnits` for a unit that is not mechanical, a marine, and
  `CantTargetUnderConstructionUnits` for a supply depot a quarter built (tool `sweep_repair`, build 75689).

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
  beam on 15, and 7 more against light, every 14 steps at 4, to the ground alone: it never fired at an overlord. Their
  shots show no random delay: the battlecruiser's came 4, 3, 4, 4 and 3 steps apart over and over, and the others' every
  8, 16 and 14 steps after the first. Each attack level adds 1 to the first three's damage, none to a bonus, and nothing
  to the oracle's. Armor comes off the first three's hits as off any weapon's, 3 more taking 3 off each, but nothing
  comes off the oracle's: it took 22 off an SCV and 15 off a roach with 3 more armor as without, and 22 off a zealot at
  shields level 3 with 4 armor, the 16 past its shields included. It wore `OracleWeapon` at every step its beam was on,
  and with its beam never on, or turned on and off again, wore none and took nothing off an SCV (tool `sweep_combat`,
  `no-weapon`, build 75689).
- A hit takes off its weapon's damage and the bonus against the target's attributes, the attacker's upgrades in both.
  From health it takes that less the armor, but at least 0.5; from shields, first, that less the shields level, and what
  is more than the shields goes on to health less the armor, at nothing at least. A marine's 6 took 5 off a roach, with
  armor 1, and 0.5 off an ultralisk with 7; and 6 off a zealot's shields, 3 at shields level 3. An upgraded zealot's
  blade of 11, on a zealot with 2 shields, shields level 3 and armor 4, took the 2 and 2 of its health: 11 less 3, less
  2, less 4. A marauder's 10 and 10 against armored took 19 off a roach and 20 off a stalker's shields, 17 at level 3
  (tool `sweep_combat`, build 75689).
- A hallucination's attacks do no damage (stated).
- A zealot's attack is 2 hits of 8, 4 steps apart, and a colossus's 2 beams land together, 15 each on a zergling (tool
  `sweep_combat`, build 75689).
- The tables' cooldowns leave out what speeds an attack up. Timed over six shots at a command center, Adrenal Glands
  brought a zergling's from 11.7 steps to 8.0, 1.4 times as fast, Resonating Glaives an adept's from 36.8 to 25.0, 1.45
  times, and stim a marine's from 13.7 to 9.3 and a marauder's from 25.2 to 16.4, 1.5 times; over six shots the delay
  below leaves each to within a few percent (tool `sweep_combat`, build 75689).
- A unit does not fire every cooldown. Each shot adds to the tables' cooldown a delay of -1 + 3r/4096 steps, r drawn
  evenly from 0 to 4095: from -1 step to 3/4096 short of 2, in 4096 even parts of -1/16 to 1/8 of a game second, the
  range the game's data editor gives weapons (stated). The cooldown then falls by 1 a step, never below 0, and the part
  of a step by which it passes 0 counts toward the next, so shots land on whole steps but come every cooldown and half
  a step on average. Worked back from the cooldown shown at each shot at a command center, all 45,000 delays the
  sweep recorded of 25 types, workers, melee and ranged units and fliers of the three races, lay on those values. A
  zergling's 40,000 drew each of the 4096 about 10 times, as evenly as chance gives: -1 itself 8 times, and 2 never,
  which a value drawn as often would miss once in 17,000 runs. Each type's 200 more spread alike, the types no
  different from one another, and a delay's correlation with the last was 0.002 over the zergling's. The mean gap was
  the cooldown and 0.35 to 0.66 steps, in five runs of eight types: a marine's, of cooldown 13.77, 12 to 16 steps,
  14.2 to 14.3 on average (tool `sweep_combat`, build 75689). A thor's and an ultralisk's shots do not fit (see [not
  understood yet](not-understood-yet.md)).
- A cooldown can read 0 for a few steps before the unit fires. Traced over ten shots, a marauder's and a zealot's rose
  in the step it ran out, a marine's after 1 step at 0, and a zergling's, a stalker's, a roach's, a hydralisk's and a
  siege tank's after 3, at every shot; the gaps between shots give the same counts, and for more types a reaper's and
  a mutalisk's 0, a ghost's and a colossus's 2, a worker's, a cyclone's, a banshee's, an adept's, an immortal's, an
  archon's, a tempest's and a queen's 3, a hellion's and a ravager's 4 and a dark templar's 6, at every shot of 200.
  Those steps count toward the next cooldown: the cooldown shown when it rises is that much lower, and the unit fires
  no less often. An instant attack's damage lands in the step the cooldown rises, a marine's, a zergling's, a zealot's
  first blade and a siege tank's, and a missile's later, from beside a command center a hydralisk's 3 steps later, a
  marauder's 4 and a stalker's and a roach's 5 (tool `sweep_combat`, build 75689).
- What speeds an attack up divides the tables' cooldown, and the delay is added to what it leaves, as it is. With
  Adrenal Glands, every one of a zergling's 3,000 cooldowns was 32585/4096 steps, its tables' 11.137 divided by 1.4 to
  within 3/4096, and a delay of -1 to 3/4096 short of 2, each end drawn; with Resonating Glaives, an adept's 3,000 were
  its 36 divided by 1.45 and a delay of -0.999 to 1.999. The cooldown still fell by 1 a step, in a probe's trace of
  ten shots of each. So the mean gap is the sped-up cooldown and half a step (tool `sweep_combat`, `delay-sped-up`,
  build 75689).
- A move ordered while an attacker waits to fire cancels the shot if it comes 2 steps or more before it, and not if it
  comes 1 step before or after the cooldown has risen. In 10 tries each at a command center, a marine, a zergling and
  a stalker ordered to move 2 or more steps before a shot due neither fired nor did damage within 16 steps, whether
  their cooldown read 0 then or not yet; ordered 1 step before it they fired at the step due, as did a marauder, and
  the damage landed. Each began to move 3 steps after the order. A missile already fired still landed, but some 4 steps
  later than when its attacker stood: a marauder's 8 steps after the rise in 18 tries of 20 and 4 or 6 in the others,
  against 4, and a stalker's 9, against 5 (tool `sweep_combat`, `move-before-shot`, build 75689).

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
