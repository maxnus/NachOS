# Not understood yet

Part of [how the game behaves](README.md), which says how each entry was seen.

- **Whether the pathing query leaves a pocket shut in by structures.** From inside a ring of eight supply depots it
  answered 22.5 to a point 11 away, the way in reading 0, in three games at one spot on PylonAIE_v4; at two other spots,
  with the point outside 6.5 away, it found no path either way (tool `sweep_requests`; `tests/test_pathing.py`'s setup,
  build 75689).
- **A structure that dies just as vision of it lapses**, before the game swaps it for a copy in the fog, is neither
  reported dead nor listed as a copy, so NachOS keeps it, stale, and never learns it died (#37).
- **A transfuse's buff** was not seen in game: the probe's marine was killed first (#37).
- **Whether `ResponseGameInfo.player_info` lists a game's observers, and whether their chat reaches the players**: no
  game with an observer has been played. NachOS hands out a plain `ChatEvent` for a message whose sender that list
  names as an `Observer`.
- **What an ability a busy structure refuses would do to what it is making** is still open: a lift, a morph and an
  add-on are answered `NotSupported`, so what they would do to a queue was never seen. Every ability a producer does
  take leaves what it is making alone (tool `sweep_orders`).
- **Why UltraloveAIE_v2's two unbuildable plates keep a structure off more than their 2x2.** Each stands at a
  natural, and also keeps a structure off the 7 tiles beside its 2x2 that are not cliff: a 4x4 with its corners cut,
  as a 4x4 rock covers. Those tiles take a structure once the plate is killed. Every other plate covers its 2x2: those
  the other maps place, and on Ultralove one created by a debug command on the very spot of one killed, and 16 more at
  offsets from the tile grid and at facings from 15° to 351°, which the game snaps to tile corners. The API reports
  nothing that sets the two apart, so `api.placement.dynamic` closes only their 2x2 and leaves those 14 tiles open (tool
  `sweep_placement`; local game, build 75689).
