# Terrain, placement and pathing

Part of [how the game behaves](README.md), which says how each entry was seen.

- One terrain-height byte is an eighth of a unit, and 127 is height 0: bytes 191, 207, 223 and 239 are heights 8,
  10, 12 and 14. The byte is the height at a tile's lower-left corner, matched to about ±0.014; on a ramp it can be
  0.41 off elsewhere in the tile, and beside a cliff it can be the other side's level (#17).
- Ramp corner heights fall on quarters (byte 193 is 8.25, 205 is 9.75). Flat ground stands near its level, though
  Pylon's main base is at 12.15 over byte 223 (#17; corpus).
- Next to a cliff each corner holds one level: a tile whose corners split 3 to 1 stands with the three (455 of 457),
  and one split 2 to 2 on the upper side (115 of 115). Corners across a ramp differ by at most 1.25, across a cliff
  by at least 2, and ground levels are 2 apart (#17).
- Torches, Ultralove and Incorporeal have ramps standing up to 0.76 above the heights the game sends. On four ramps
  of the pool, tiles in one row across differ by half a byte, and on every measured ramp the next row up is 0.1875
  higher (#17, #22).
- A unit's z is within 0.05 of its tile center's height 9 times in 10, and within 0.03 of the interpolated height
  (#17).
- Ramps are walkable and unbuildable, and climb 1 to 2. Bridges, trees and the ground under indestructible doodads
  are all level, walkable and unbuildable, and nothing in `ResponseGameInfo` tells them apart; PylonAIE_v4 has two
  bridges (#17, #22; corpus).
- Nothing pathable or placeable lies outside the playable area, which is 29% of Pylon and 53% of a map on average,
  and no unit can leave it. Grid images are 1 or 8 bits per pixel, one row per y, from the bottom up (#17; corpus).
- At step 0 the pathing grid blocks rocks and only this player's own starting townhall, mineral fields and geysers (#17;
  corpus). The placement grid reads the ground beneath every unit, and blocks none: no resource, townhall, rock or
  tower, in sight or not. On the 7 maps of the corpus each neutral unit but a resource was killed in turn, and of the
  tiles under each that went (a collapsible tower stays; see below), every one the start's grid leaves open took a
  structure once it was gone, and none the grid blocks did, but one under a collapsible tower beside a rock. Rocks on a
  ramp, as on LeyLinesAIE_v3 and PylonAIE_v4, stand on ground no structure takes (tool `sweep_placement`, build 75689;
  corpus).
- A structure covers a square twice its creation ability's `footprint_radius` across, laid around its center as the game
  snaps it: a sensor tower 1 tile, a depot or a pylon 2x2, a barracks 3x3, a townhall 5x5. A form a structure morphs
  into covers what it morphed from: a lowered depot 2x2, an orbital, a planetary fortress, a lair and a hive 5x5, a warp
  gate 3x3, a greater spire 2x2. An add-on covers 2x2 beside its host. A creep tumor, plain, a queen's or burrowed, and
  an oracle's stasis ward cover one tile. Nothing that flies covers any, nor an uprooted crawler, a KD8 charge or a
  Xel'Naga healing shrine (tool `sweep_placement`, build 75689).
- A mineral field covers 2x1 and a geyser 3x3; a Xel'Naga tower and unbuildable plates and bricks 2x2; an inhibitor
  zone one tile; a collapsible tower and its debris 5x5; and the unit that pushes units from under a falling tower a
  7x7 square less three tiles at each corner. Rocks and debris cover the size their name gives, but a 4x4 one loses a
  tile at each corner and a 6x6 one three, and a huge diagonal one covers a band five tiles wide across a 10x10
  square (tool `sweep_placement`, build 75689).
- The footprints were measured one unit at a time on open ground, by asking whether a sensor tower, refused on creep,
  or a queen's creep tumor, refused off it, could stand at each tile center around the unit: the tiles both are
  refused are its own. The placement query takes a sensor tower with no engineering bay. An acceleration zone and a
  flying inhibitor zone the game does not create at all (tool `sweep_placement`, build 75689).
- Whether a structure may stand on creep was asked of each structure type a unit places at a point, on open ground and
  on a hatchery's creep, with a pylon's power over both. A zerg structure but a hatchery and a nydus worm, and a creep
  tumor, is taken only on creep, and refused off it `CantBuildTooFarFromCreepSource`; a terran or protoss structure but
  a raven's auto turret and an oracle's stasis ward only off it, and refused on it `CantBuildTooCloseToCreepSource`; a
  hatchery, a nydus worm, an auto turret and a stasis ward either way. A zerg structure needs creep under every tile of
  its footprint: beside a hatchery, a spawning pool was refused while the creep had not yet reached one corner of its
  3x3 (tool `sweep_placement`, build 75689).
- An add-on is its host's own type only while attached: created alone by a debug command, a barracks', factory's or
  starport's tech lab or reactor comes out plain, and a barracks lifting off leaves its reactor plain. The sweep
  measures each beside a host told to build it (tool `sweep_placement`; local game, build 75689).
- A collapsible tower stands mostly on ground no structure takes: of the 825 tiles under the corpus's 33, the start's
  grid blocks 712. Destroyed, by attack as by a debug kill, it falls: its health drops to 0, and its debris, units of
  their own with 2000 health, lands about 5 tiles off where it falls. The game goes on listing the tower at 0 health,
  and it still covers its 5x5: the other 113 tiles stayed refused, as did all 25 under one felled on open ground (tool
  `sweep_placement`; thors attacking one on MagannathaAIE_v2, build 75689).
- Each mineral field and geyser keeps townhalls off a zone around it: its footprint grown by 3 tiles on every side,
  with each corner cut, by one tile at a mineral field and by three at a geyser (the corner tile and the tile beside
  it on each side). The game refuses a townhall whose footprint covers any tile of a zone. All 15 kinds of field and 6
  of geyser keep the same zones, and a command center, a nexus and a hatchery are refused alike (tool
  `sweep_townhall_placement`).
- NachOS's rule for where a townhall may stand was checked against the game on the 7 maps of the corpus. Around each
  expansion NachOS finds, the game was asked whether it would take a townhall at every tile center within 7 tiles:
  21,315 centers in all. NachOS gives the game's answer at every one, blocking each of the map's other units by its
  measured footprint. Blocking the square a unit's radius spans instead, it refused 26 the game takes, beside diagonal
  collapsible towers and some 6x6 rocks and debris, though none would have given an expansion a better spot (tool
  `sweep_townhall_placement`, build 75689).
- `api.placement.free` was checked against the game on the 7 maps of the corpus, with every unit but a structure
  killed: asked whether a one-tile structure could stand on each tile of the playable area, 127,020 tiles in all, the
  game gives the grid's answer at all but 14, on UltraloveAIE_v2, beside its two unbuildable plates, as "Not
  understood yet" says (tool `sweep_placement`, build 75689).
- `start_locations` leaves out this player's own, and names each other spawn at the center of its townhall's tile
  (#17; corpus).
- A structure's center snaps to the grid, to a tile corner for an even footprint and a tile center for an odd one,
  and a builder's order shows its site snapped: a depot ordered at (86.3, 159.7) at (86, 160), and a barracks ordered
  at (82.3, 158.7) at (82.5, 158.5) (tool `sweep_orders`). An SCV's order, once construction starts, targets a center
  at most 0.71 from the point ordered (#25). The game moved a forge placed at the edge of buildable ground from
  (93, 150) to (90.5, 147.5), and a pylon ordered onto unbuildable ground was never made (#25, #32).
- Power comes from a pylon or a warp prism (stated); a warp-in needs a pylon with open ground beside it (tool
  `sweep_alerts`).
- The pathing query (`RequestQueryPathing`) answers the distance a ground unit walks over the map as it stood at the
  last observation asked for: a change of structures shows only once an observation has been asked for since. A ring of
  eight supply depots made by debug command shut the ground inside off by the first observation after, and lowered
  opened it 20 steps after the order, observed every 2 steps; with no observation asked for, neither change showed in 48
  steps, and the next observation brought it at once, in two games each (tool `sweep_requests`, build 75689).
- It answers 0 when there is no path, for an end inside a structure, the computer's townhall included, for an unknown
  tag, and from a point to itself; points 0.1 apart read 0.0999. It paths out of the townhall its start lies in, 7.0 to
  a point 8 away, but never into a structure (tool `sweep_requests`, build 75689).
- From a unit's tag it answers for that unit: from atop a cliff on PylonAIE_v4, 63.7 for a marine, by the ramp, 3.3
  for a reaper, which jumps the cliff, 58.0 for a colossus, and the straight line for a medivac. From the marine's
  position the answer was 61.7 (tool `sweep_requests`, build 75689).
- Over 100 random pairs of tiles on PylonAIE_v4, its distance was 0.89 to 1.12 times that of `Grid.cheapest_paths_from`,
  0.95 at the median. The two pairs it answered 0 for, where the grid has a path, ended inside the computer's
  structures, which the start's grid leaves open (tool `sweep_requests`, build 75689).
- PylonAIE_v4 has two rich geysers, each the one geyser of a base, and neither in sight from a start (corpus).
- UltraloveAIE_v2 has neutral critters, `CleaningBot` and `LabBot`, which `show_map` brings into sight (tool
  `sweep_townhall_placement`): two of each, ground units of radius 0.375 and 10 health that wander. The placement query
  answers a structure over one as over clear ground, and as over an own marine, but a build over one starts only once
  it has walked off: a command center ordered with its SCV at the site's center started 1 step after the order on clear
  ground, 3 times in 3, and 24 to 32 steps after with a `LabBot` at the center, which the game pushed out of the 5x5
  (in game, build 75689).
