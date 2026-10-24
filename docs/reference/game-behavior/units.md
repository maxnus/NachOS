# Units

Part of [how the game behaves](README.md), which says how each entry was seen.

## What an observation lists

- The first observation lists every starting unit (tested).
- A radar blip, of type `NotAUnit`, and a structure placed but not started have no tag. A tagless placeholder stands
  where a structure was ordered from the moment of the order, before a worker reaches the spot (#25; tested).
- A structure or mineral field that leaves sight is replaced, in the same observation, by a remembered copy under a
  new tag at a bit-identical position; seen again, it is back under its first tag, and each return to the fog makes
  another tag. Mineral fields start the game as such copies (#25; tested).
- A copy can have a different type from the structure it copies: a depot that lowered as it left sight (#25).
- A barracks that lifts off, a spine crawler that uproots and a command center that becomes an orbital command out
  of sight keep their tags. Seen elsewhere, the structure is listed there while its copy stays listed at the old
  spot, until that spot is seen (#25; tested).
- A structure lifted off or uprooted, and a viking landed or flying, is never left in the fog as a remembered copy
  (stated, the owner, 2026-09-26).
- A refinery stands at its geyser's exact position (#25).
- A unit inside a transport or a gas building, or a drone that became a structure, leaves the observation; a
  passenger returns under its tag when unloaded (#25; tested).
- Deaths come in `raw_data.event.dead_units`, and only deaths this player sees. A unit reported dead is already gone
  from that observation's units, and is reported once. Most reported deaths name tags no observation ever listed:
  66 of 69 in one corpus game (#25, #28; corpus; tested).
- A structure killed or burned down out of sight is never reported dead, not even once its spot is seen; its copy
  disappears (#25; tested).
- An undetected cloaked enemy is listed, as `CLOAKED` and `INVISIBLE`. An undetected burrowed enemy zergling is not
  listed at all (#37; tested), but an enemy's burrowed widow mine can be (below).
- Force fields and reaper grenades are listed as neutral units, not effects; a parasitic bomb adds no unit. Lurker
  spines are one effect with several positions along a line (#12, #25).
- Map-owned units are owner 16 (stated). An enemy structure built out of sight is not listed (tested).

## What is reported of a unit

- The game zeroes what the player cannot know: a remembered copy's health, contents and buffs, an unseen mineral
  field's contents, a hidden unit's owner. An undetected enemy listed `INVISIBLE` reports its position and cloak, and no
  health, owner, build progress, buffs, facing or energy (#25; corpus).
- Orders, cargo, harvesters, rallies, weapon cooldown, the add-on and the engaged target are reported only for this
  player's own units (#25; corpus).
- `life` is health alone; shields are separate (stated). A marine has 45 health, 55 with Combat Shield, and the
  tables hold no health (#34; tested).
- `build_progress` counts up while a structure is built or a unit warps in (a zealot read 0.01, 0.32, 0.64, 0.95,
  1.00), and reads 1 throughout a morph: a larva's egg, a cocoon, and a structure becoming another (#25; tool
  `sweep_orders`, build 75689).
- Only the command center (12 of 16, then 9 once three SCVs went to gas) and the refinery (3 of 3) reported
  harvesters. `ideal_harvesters` is 2 per mineral field and 3 per geyser (#25).
- `is_powered` is true for a gateway in a pylon's field and false outside it, and false for a nexus, an assimilator,
  a barracks, a command center and a pylon, which always stands in its own field, alone or beside another (#25, #27,
  #39).
- `is_hallucination` is true for an enemy's hallucination only while a detector is near; this player's own are
  always flagged. `is_active` is true for any unit carrying out an order, the enemy's included. `engaged_target_tag`
  names the unit a marine attacks (#25).
- A unit lifted by a graviton beam wears `PHOENIX_GRAVITON_BEAM` and reports `is_flying` false (#26; tested).
- Visible enemy units report their armor and shield levels, and armed ones their attack level too; an undetected
  enemy listed `INVISIBLE` reports none (#12; corpus).
- Order and rally points have height 0. A rally onto a unit that is gone, such as a mined-out mineral field, holds
  tag 2³², which names no unit (#25; corpus). So does a rally onto a mineral field out of sight: a command center
  rallied onto one in the fog by its copy's tag, or onto one in sight that then went into the fog, held 2³² and the
  field's point (in game, build 75689).
- An SCV walking back to a mineral field out of sight, after carrying its minerals off, shows its gather aimed at a
  tag the observation lists nowhere: neither the field's own tag nor its copy's, in no unit, event or other field of
  the response, 2,891 times over 1,000 steps. Back in sight of the field, the order names the field's own tag again.
  Each SCV went back to the field it had mined, also with all 8 of the main's fields killed in the fog meanwhile,
  and went idle there. An SCV ordered to gather a field in the fog by its copy's tag shows that tag. Seen with the
  main's command center killed and another standing far off, on PylonAIE_v4 (in game, build 75689); it crashed 9 of
  84 of the demo bot's games against the harder computers before NachOS read such a target.
- Order progress runs from 0 to 1 for training and research and is 0 otherwise (#25; corpus). A larva's egg
  counts up like a train, and an SCV's and an egg's run evenly over the build time in the game's table. A
  morph's order reads 0 from start to end, and so does a structure's building an add-on: an orbital command, a
  planetary fortress, a lair, a hive, a greater spire, a tech lab, a reactor, every cocoon and the lurker egg.
  Nothing a unit reports says how far a morph has got. Each was done once the table's build time had passed since
  the order, to within the 16 steps between reads, except the lurker egg, which hatched 402 to 418 steps after the
  order against the table's 553 (tool `sweep_orders`, build 75689).
- Points come back in single precision (tested). Facing is in radians from the +x axis, and z is on the
  terrain-height scale (stated).
- Units on flat ground stand up to 0.03 below the map's height or up to 0.16 above it (corpus).
- A unit reports its owner's upgrade levels. `attack_upgrade_level` and `shield_upgrade_level` count levels, but
  `armor_upgrade_level` is the armor added: an ultralisk with Chitinous Plating and three levels reports 5, a
  structure with Neosteel Armor 2 (#30).

## Turning

- **A unit ordered to move turns toward where it goes from the step after the order, by at most its rate a step, so a
  turn takes as many steps as its angle over the rate, rounded up, give or take one.** The rates: 22.5° a step (360° a
  Normal second) for a cyclone and a swarm host; 45° (720°) for a thor in either mode, an ultralisk, a siege tank, a
  hellion, a hellbat, a widow mine, a locust, a landed viking, a tempest, a carrier, a battlecruiser, a brood lord, a
  warp prism, an observer and every flying structure; some 93° for a banshee, a mutalisk and a phoenix, which turned 96°
  in one step and 98° in two, and some 91° for a liberator, which turned 94° in one and 96° in two; and 62.5° (1000°)
  for every other type that moves. A marine turned 120° in two steps and 130° in three, a thor 130° in three and 140° in
  four. A turn of more than two steps takes the rate in all but the last two, which share the rest: a thor's 170° went
  45°, 45°, 40° and 40°. Of the 690 turns of 45° to 170° run in two runs, 630 came out the same and 53 a step apart: a
  battlecruiser and a carrier at times began a step late, and a few flyers and workers turned 2° or 3° more as they set
  off, a MULE for some steps more (tool `sweep_turning`, build 75689).
- **Most units set off while they turn, in the step after their turn begins, whatever is left of it; a thor in either
  mode, an ultralisk and a siege tank set off only once they face where they go.** Workers, a MULE, an overlord in
  either form, an overseer, a medivac, a raven, a void ray, an observer, a carrier, a battlecruiser, a brood lord, a
  mothership and every flying structure set off a step later than the rest, in the fourth step after the order (tool
  `sweep_turning`, build 75689).
- **The facing the game reports never changes for a colossus, a disruptor, an immortal, a mothership, a purification
  nova and an uprooted spine or spore crawler**, ordered to move at any angle, though each sets off as the rest do (tool
  `sweep_turning`, build 75689).

## Units coming, changing and going

- A larva stays one unit as it becomes an `EGG`. What hatches comes under new tags, two zerglings as two units, and
  the egg is reported dead (#37; tested). The death comes in the first observation without the egg's order, so a
  larva's order ends with its egg dying, and an egg killed before it hatches looks the same (tested).
- A morph keeps the tag: a tank sieging, a depot lowering, a zergling through `BANELING_COCOON` to a baneling, an
  overlord through `OVERLORD_COCOON` to an overseer. A lair or an orbital command changes type only once the morph
  finishes (#37; tested). A unit's type changes on burrowing too (#37).
- A drone that becomes a structure leaves the observation with no death reported, and the structure appears under a
  new tag. The game reports the drone dead when the structure finishes or is killed, in the same step in 3 games of
  6 and a step later in the other 3. On a cancel the structure is reported dead and the drone returns in that
  observation, under its own tag and with its health (#25, #37; tested).
- An SCV first walks to the site carrying the build order; once construction starts, its order targets the
  structure's snapped center ([where a site snaps](map.md)). Halted, it has no orders and progress stops;
  resumed with a smart order, its order targets the structure; killed, it is reported dead and progress freezes;
  once the structure is finished, it has no orders (#25).
- A probe warps its building in and walks away, and a drone becomes the building, so only an SCV has anything to
  halt (in game).
- A worker that built a gas structure goes inside it to harvest for under 120 steps (tool `sweep_tech_tree`).
- Without `fast_build` a supply depot is still going up 120 steps after the order, and done within 600 more
  (tested).
- An add-on is a unit of its own, first seen the step after the order at 2% built. It sits 2.5 right of its host and
  0.5 below; with no room the host lifts off to build it elsewhere (#37; tested). Ordered without a point, as a bot
  usually orders one, it is built where the host stands and charged at once, 50/50 for a reactor; but a host whose
  add-on spot a supply depot fills is answered `Success`, and nothing is charged or started, then or 24 steps later
  (tool `sweep_orders`).
- A lifted barracks needs a point to build an add-on: where it is to land. Given a point with room beside it, it
  shows `Land_Barracks` and then `Build_Reactor_Barracks`, both targeting the point, flies there, lands and builds
  the add-on, and is charged at once: the reactor's 50/50 was gone by the next observation, with 22 still to fly and
  430 steps before it landed. Given no point it is answered `Success`, and nothing is charged or started; given a
  point whose add-on spot a supply depot fills, `CantFindPlacementLocation`, and nothing is charged. A lifted factory
  and starport do the same, each charged the reactor's 50/50 by the next observation (tool `sweep_orders`).
- A flying command center or barracks given land and then, queued, a morph or a train answers the second
  `NotSupported` as it is given: the orbital morph, an SCV, a marine. The barracks lands and stands idle (tool
  `sweep_orders`, `queue-cases`).
- A warp-in is first seen unfinished and finishes some steps later (#37; tested). A gateway becomes a warp gate by
  itself once Warp Gate is researched, and nothing else makes one (#29; tested).
- The starting townhall is never seen unfinished. An auto-turret has the structure attribute but is first seen
  finished. A queen's creep tumor is first seen at 0%, finishes 240 steps later, then becomes
  `CREEP_TUMOR_BURROWED` (#37).
- A structure cancelled while going up is reported dead, and so is a MULE when it expires (#37; tested), and a mineral
  field killed by debug command (tested; local game, build 75689).
- A neural parasite keeps the tag and hands the unit to the caster's player within 24 steps, alliance and upgrades
  included (a marine read 55 health), with the `INFESTOR_NEURAL_PARASITE` buff; the unit returns when the infestor
  dies (#25, #34; tested).
- An order to one larva may be carried out by another larva of the same hatchery, and larva die with their
  hatchery. A morph ordered on several units morphs only one of them (tool `sweep_tech_tree`, tool `sweep_alerts`).
- A drone's morph given to a hatchery itself is answered `NotSupported`, and no larva takes it (tool `sweep_orders`,
  `larvae`; local game, Linux build 75689).
- Only one mothership can stand at a time, arming a nuke needs a factory, and a game starts with 50 minerals (tool
  `sweep_tech_tree`, tool `sweep_alerts`).
- The game itself disguises a changeling, collapses a tower, lifts a locust into the air and burrows a creep tumor
  (stated).

## Vision, fog, cloak and detection

- A change out of sight, such as a morph in the fog, shows only when the unit is next seen (stated).
- Vision of an enemy lingers some 60 steps after the unit that saw it dies (#37; tested).
- An enemy that cloaks where it stands stays listed. An undetected enemy observer is listed `CLOAKED` and
  `INVISIBLE`, in sight but not visible, with no buffs; beside an overseer or a raven it reads `CLOAKED_DETECTED`
  and `VISIBLE`, and a ghost detected that way shows `GHOST_CLOAK` (#37; tested).
- A cloaked unit of this player's (an observer, a dark templar, a cloaked ghost) reads `CLOAKED_ALLIED` and
  `VISIBLE` whether the enemy detects it or not: an enemy observer, missile turret and photon cannon each detected
  and shot one (the turret took an observer from 70 to 34), and nothing in its report changed (#37).
- A ghost cloaks and uncloaks one step after the order (#37; tested).
- Burrowing is no cloak: a burrowed zergling reads `NOT_CLOAKED`, and an undetected burrowed enemy zergling is not
  listed (#37).
- An enemy's burrowed widow mine was listed `INVISIBLE` with its cloak `UNKNOWN`, in 8 reports of one game,
  MagannathaAIE_v2-TvT; no other unit was listed so (corpus).
- A unit that burrows reads `is_burrowed` false for up to 3 observations after its type turned burrowed: a widow mine
  for 3, a lurker for 2 (corpus).
- A remembered copy reports its cloak as unknown (stated).
- The visibility grid reads 0 for never seen, 1 for seen before and 2 for in sight now; creep is one bit per tile.
  At the start this player's base has creep and vision, and the enemy's start is unexplored (tested).
