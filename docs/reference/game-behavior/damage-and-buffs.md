# Damage, energy, spells and buffs

Part of [how the game behaves](README.md), which says how each entry was seen.

## Damage, energy and spells

- Shield armor is the shields level: a marine's 6 damage took 5, 4 and 3 off a zealot's shields at levels 1, 2 and
  3. No table holds shield armor (#31).
- A colossus lands 2 hits per attack (stated). Range is measured horizontally (stated).
- Every unit below its maximum energy regenerates some every step (#37).
- A feedback cost the high templar 50 energy and drained all 100 of a raven's, dealing 50 damage, and left no buff
  and no effect. An EMP cost the ghost 75 and drained 100 of a raven's 150 (#37; tested).
- A storm is the effect `HIGH_TEMPLAR_STORM`, carrying its caster's alliance, and this player's own storm puts the
  storm buff on its own units too (#34; tested).
- A liberation zone is drawn for 2 s before it fires, and a time warp slows nothing for its first 1.79 s; each phase
  has its own pending effect id (#5).
- Since 4.12.0 Microbial Shroud needs no research: an infestor with no upgrades is offered it and casts it (#23).
- A unit under attack that cannot fight back drifts away unless it holds position (tool `sweep_alerts`).

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
