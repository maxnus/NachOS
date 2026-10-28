# Not understood yet

Part of [how the game behaves](README.md), which says how each entry was seen.

- **How a thor's and an ultralisk's shots are timed.** A thor firing at a command center showed its cooldown rise about
  three times for each 48 steps of its ground weapon's cooldown, 7 to 23 steps apart, some rises to 0 from below it. An
  ultralisk at one read 0 for 6 steps before most shots, as the rule for the other types has it, but 5, 7 or 8 before
  some, and 17 of its 200 shots came up to 1.8 steps later than a delay of 2 allows (tool `sweep_combat`,
  `delay-weapons`, build 75689; the rule in [damage and buffs](damage-and-buffs.md)).
- **Why a zergling with Adrenal Glands reads 0 for 2 steps before some shots.** Without the upgrade it read 0 for 3
  steps before every one of 40,500 shots. With it, 2 before 17% of 3,000 and 3 before the rest, and 2 the more often
  the later in its step the cooldown ran out: before 2% of shots when it ran out in the step's first tenth, 31% in
  its last. An adept with Resonating Glaives read 0 for 3 before every shot, as without (tool `sweep_combat`,
  `delay-sped-up`, build 75689).
- **Why a missile lands later when its attacker moves off.** A marauder's and a stalker's missiles fired just before
  a move order landed some 4 steps later than when the attacker stood (tool `sweep_combat`, `move-before-shot`, build
  75689; the entry in [damage and buffs](damage-and-buffs.md)).
- **Whether the pathing query leaves a pocket shut in by structures.** From inside a ring of eight supply depots it
  answered 22.5 to a point 11 away, the way in reading 0, in three games at one spot on PylonAIE_v4; at two other spots,
  with the point outside 6.5 away, it found no path either way (tool `sweep_requests`; `tests/test_pathing.py`'s setup,
  build 75689).
- **Why a replay saved during a game can read back shorter than the game.** Against the computer, each one saved
  read back the step of the observation before it, not the step it was saved at: 0 at step 16, 16 at 2240 and 2240 at
  6720. In a game of two and in a realtime game, where nothing had been observed, each read back the step it was saved
  at (tool `sweep_requests`, `replay`, build 75689). One saved at a 300 second time limit, just after the observation
  at step 6720, read back 6720 and played back to 6720 (tool `sweep_replays`, `ending`, build 75689).
- **Why an enemy SCV far from any tile in sight is `Visible`.** Played back as player 1 two seconds after its marine
  was killed in the enemy's main, both the fog-on and the fog-off view list an enemy SCV the marine had damaged as
  `Visible`, 128 from the nearest tile player 1 sees (tool `sweep_replays`, `unseen`, build 75689).
- **Why a replay of a game won through `DebugEndGame` stops early when played back.** Its info gives it 16 steps, and
  played back 8 steps a turn its last observation, which says it ended, with the victory and the defeat, reads step 8
  (tool `sweep_replays`, `ending`, build 75689).
- **A structure that dies just as vision of it lapses**, before the game swaps it for a copy in the fog, is neither
  reported dead nor listed as a copy, so NachOS keeps it, unlisted, and never learns it died (#37).
- **A transfuse's buff** was not seen in game: the probe's marine was killed first (#37).
- **Whether `ResponseGameInfo.player_info` lists a game's observers, and whether their chat reaches the players**: no
  game with an observer has been played. NachOS hands out no event for a message whose sender that list names as an
  `Observer`.
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
