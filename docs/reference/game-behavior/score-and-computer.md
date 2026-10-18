# The score, supply and the computer

Part of [how the game behaves](README.md), which says how each entry was seen.

## Score and supply

- The game rounds supply used and army supply down: one zergling left `food_used` at 14, a second made it 15, and a
  third left it at 15 (#28; tested). `food_used` counts units in production, `food_workers` does not; supply left
  goes negative once supply providers are lost (stated).
- `larva_count` reads 0 with three larva at the hatchery; `warp_gate_count` counts warp gates (#28).
- The score's idle production and idle worker times are in Normal seconds, summed over the idle units (two idle
  drones added 20 over 160 steps), and all 10,232 values in the corpus are whole steps (#28; corpus).
- The score is the value of every unit and structure, finished or not, plus the resources banked; `total_value`
  counts only what is finished, `collection_rate` is per minute, and `spent` counts an order when it is queued and
  refunds it when cancelled. `used_*` leaves out what was destroyed, and `total_used_*` counts it (stated).
- Recent APM reads 0 through the raw interface (#28).

## The computer opponent

- The very easy computer keeps the game going, leaves the player alone and does not come to the map's middle early,
  but it does attack at some point (tool `sweep_buffs`, tool `sweep_tech_tree`).
- It concedes a game it sees as lost, with nothing left to fight with or its base cleared, and the game ends (tool
  `sweep_alerts`).
- A bare api against the very hard zerg lasts over 60 s and ends in victory or defeat (tested). Against a bot that
  never leaves its base, the computer's army shows up but none of its buildings, and it researches, as its units'
  levels show (corpus).
