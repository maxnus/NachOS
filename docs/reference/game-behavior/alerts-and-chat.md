# Alerts, the camera and chat

Part of [how the game behaves](README.md), which says how each entry was seen.

## Alerts and the camera

- An alert names no unit and no position. It comes once per event, in the observation where that event is first
  seen, and several can share an observation (tool `sweep_alerts`, #37; tested).
- `TRAIN_WORKER_COMPLETE` once per worker; `TRAIN_UNIT_COMPLETE` once per non-worker unit, twice for a pair of
  zerglings, and never for a mothership, a warp-in or a morph; `MOTHERSHIP_COMPLETE` instead for a mothership;
  `WARP_IN_COMPLETE` once per warp-in (tool `sweep_alerts`, #37; tested).
- `BUILDING_COMPLETE` once per structure, including one a drone became and a nydus worm, but not an add-on;
  `ADD_ON_COMPLETE` once per add-on (tool `sweep_alerts`, #37; tested).
- `MORPH_COMPLETE` for a lair, a baneling, an overseer or a ravager, none for a hellbat; `TRANSFORMATION_COMPLETE`
  for a gateway becoming a warp gate and back, the automatic conversion included; `MERGE_COMPLETE` some 100 steps
  after the archon is first seen (tool `sweep_alerts`, #37; tested).
- `RESEARCH_COMPLETE` for research that is no level (stimpack, combat shield, zergling speed, warp gate, charge);
  `UPGRADE_COMPLETE` for a weapons or armor level, and for a command center becoming an orbital command or a
  planetary fortress (tool `sweep_alerts`, #37; tested).
- `LARVA_HATCHED` once per inject, a few steps before the larva are seen, and never for larva a hatchery makes itself
  (tool `sweep_alerts`, #37).
- `MINERALS_EXHAUSTED` once per field this player mined, in the observation it disappears from (8 of 8, at the same
  steps); `VESPENE_EXHAUSTED` once per geyser (2 of 2); `MULE_EXPIRED` in the observation that reports the MULE
  dead; `NUKE_COMPLETE` for a nuke armed at a ghost academy (tool `sweep_alerts`, #37; tested).
- `NUCLEAR_LAUNCH_DETECTED` once per enemy nuke, whether aimed at this player's base or somewhere it sees nothing, in
  the first observation after the launch; `NYDUS_WORM_DETECTED` once per enemy worm, seen or not. This player's own
  nuke raises nothing, and its own worm only `BUILDING_COMPLETE` (tool `sweep_alerts`, #37).
- `UNIT_UNDER_ATTACK` and `BUILDING_UNDER_ATTACK` come only for units the camera does not show (5 of 5 off screen,
  0 of 5 on), once per unit, and not again for that unit until it has gone some 6000 to 6500 steps without being
  attacked: none after 6069, one after 6524, and one in 24000 steps for a unit attacked every 3000 (tool
  `sweep_alerts`, #37).
- `AlertError` and `TrainError` were never raised, not even by failed orders (tool `sweep_alerts`, #37).
- The camera starts within 2 of the main townhall, with the workers, mineral fields and geysers on screen, and the
  game moves it once, at step 2, by under 2. So until something moves it, an attack on the main raises no alert, and
  the corpus has none (#37; corpus).

## Chat

- This player's own chat message comes back once, in the next observation, under its own player id; every player's chat
  is delivered (#28; tested). So does a message to the team alone, in a game against the computer (tool
  `sweep_requests`, build 75689).
- The game answers each chat action `Success`, one result each, after those of the unit commands and the camera
  move of the same request (tool `sweep_requests`, build 75689).
- A chat message of 1040 bytes or more, counted in UTF-8, kills the game client within two steps: the connection
  drops. One of 1039 bytes comes back whole, of plain letters, of `é` or of `日` (tool `sweep_requests`, build 75689).
- Chat that comes too fast is dropped without a word once some 500 bytes have gone out in a row. Sent a step apart, ten
  messages of 20 or 50 bytes all came back, of 100 bytes the first five and of 255 bytes the first two. The game counts
  by the clock, not in steps: after 5 seconds every message came back again, while 672 steps stepped in a moment brought
  no message back. Twelve short messages in one request all came back (tool `sweep_requests`, build 75689).
- In a game of two, chat reaches the other player one or two steps after it is sent, each in 6 games of 12. Sent in the
  step a player leaves, it never arrives, and an observation made once the game has ended carries no chat, so the other
  player sees a message only if it observes after it arrives and before the end (tool `sweep_requests`, build 75689).
