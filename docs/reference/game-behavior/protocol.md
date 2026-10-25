# The connection, launching and time

Part of [how the game behaves](README.md), which says how each entry was seen.

## The connection and the protocol

- The game serves one websocket connection at a time and drops a second during its handshake. Once the first
  closes, a new connection finds the game the first created, and after that game a ping reports `launched` (#14;
  tested).
- Responses carry nothing that matches them to a request: the game answers the last request sent, so only one can
  be open at a time (#6).
- Every response carries a status. An unset proto2 enum field reads as its first declared value, not zero: an unset
  status reads `launched`, an unset join error `MissingParticipation` and an unset create error `MissingMap`, all
  truthy, so each is read only behind `HasField` (#6, #9).
- The protocol is binary; a text frame is the game or a proxy reporting an error (stated).
- Before a game, or after leaving one, a request that needs one fails with "A game has not been started yet"; after
  a game has ended, with "Game has already ended" or "Not supported if game has already ended" (#14).
- Leaving a running or ended game concedes it (the opponent is told it won) and returns the client to `launched`;
  leaving when no game was started is refused. An ended game accepts a new `create_game` without being left first
  (#14).
- The game says it has ended one observation before it says who won, and only an observation carries the results
  (#16).
- A game that dies resets the socket, which shows up as a bare connection error (#16).
- `ResponseGameInfo` is about 77 KB, and byte-identical at loops 0, 256, 1024 and 3008. An observation is about
  80 KB and barely changes from one step to the next (#10, #11).
- Each connection needs two ports, and every participant must send the same port sets in the same order, or the
  join fails. From a ladder start port N, N is unused, the server gets N+2 and N+3 and the client N+4 and N+5. A
  python-sc2 bot given the same start port joined NachOS's game and played in lockstep (#14).
- In a game of two clients, a step passes only once both have requested it, and each join waits for the other (tool
  `_sandbox`, #37).
- A realtime game advances by itself; only a stepped game needs step requests (stated).
- An unset `random_seed` lets the game pick one, and 0 is a real seed (stated).

## Launching, maps and players

- The client was listening 4.9 s after launch. The raw interface starts at base build 55958 (#7).
- It serves the API at `ws://host:port/sc2api`, and takes `-listen`, `-port`, `-dataDir`, `-tempDir`,
  `-displayMode`, `-windowwidth`, `-windowheight`, `-windowx` and `-windowy` (#7).
- The executable is `Versions/Base<build>/SC2_x64.exe` on Windows, started from `Support64`, `SC2_x64` on Linux, and
  `SC2.app/Contents/MacOS/SC2` on macOS. The launcher records a non-default install location in `ExecuteInfo.txt`,
  under `Documents/StarCraft II` on Windows and `Library/Application Support/Blizzard/StarCraft II` on macOS, as
  ` = <path>Versions`, ending in the separator of the platform that wrote it (#7).
- Installers name the map folder `Maps` or `maps`. A map pack installs beside the maps it replaces, and a revised
  map is a new file next to the old one (`PylonAIE_v4` beside `PylonAIE`), so one name can appear more than once
  (#9, #21). Replays are written to `<install>/Replays` (stated).
- A client left running keeps hold of the graphics card (stated).
- Every map in use seats two. The game silently drops extra players, and `PlayerSetup` cannot set teams (#11).
  `create_game` ignores a participant's race and name: the join sets them. `PlayerSetup.race` counts only for a
  computer (#9).
- After `create_game` the status is `init_game`, the join returns player 1, and against the computer `game_info`
  gives only the joining player's own `race_actual` (#9; tested). In a game of two it gives both players' to either
  player, a Random one's included, from the start. No player has a `player_name` in either, though each joined with
  one (tool `sweep_requests`, `players`, build 75689).
- A game with no opponent is won the moment it starts (#22).
- On every launch the game rewrites `stableid.json`, in `Documents/StarCraft II` on Windows, from the loaded map's
  mod dependencies, so its ids differ from map to map: BerlingradAIE (2022) yields 518 ability ids MagannathaAIE
  lacks, and shifts a couple of dozen shared names by +312 or +316 (#5, #16).
- A bot ends a game early only by leaving it, which concedes it. The ladder starts the client, creates the game and
  passes host, port and start port on the command line; a game against the built-in computer has no start port
  (#14).
- A game against the built-in computer can be left too, though the protocol calls leaving multiplayer only. The client
  is back at `launched` and answers every request that needs a game "Not in a game", so no result is reported. In a game
  of two, the other player's game has ended by its next step, a victory for it; when both players leave in the same
  step, each is back at `launched`, and neither is told anything (tool `sweep_requests`, build 75689).
- `DebugEndGame` ends a game at the next step as any ending does: the observation reads `ended` with the results, a
  defeat for a surrender and a victory for a declared victory, the computer taking the other (tool `sweep_requests`,
  build 75689).
- A game of two can end in a tie for both players while nothing in it is made or killed. Two games of the combat sweep
  did, both at some 29,600 steps by their step rates: one some 29,500 steps into its one trial, the other some 6,400
  into a trial begun after trials of 3,600 to 4,900 steps, each of which made and killed its units by debug command.
  Games whose trials did so every 6,500 steps or less ran on, to 33,000 steps and to 466,000. This fits the stalemate
  rule, a tie once no player has mined, built or destroyed anything for some minutes (stated), with this player's
  workers having mined out its main, which was not checked (tool `sweep_combat`, build 75689).
- A replay can be saved while a game runs and after it has ended: on the client that created the game and on the one
  that joined it, and in a realtime game. For a game with nothing built it is 8 to 9 KB, an MPQ archive (`MPQ\x1b`),
  and takes 1.5 to 3 ms. The game reads each one back, with its map, build and players; saved after the end, with the
  results and the whole game's length. Once the client has left the game, ended or not, the game answers "Not in a
  game" and gives none (tool `sweep_requests`, `replay`, build 75689).

## Playing a replay back

What follows was seen on one game unless an entry says otherwise: the demo bot against the computer at Medium on
`PylonAIE_v4`, 300 game seconds, a turn every 8 steps, recorded and its replay saved at its time limit (tool
`sweep_replays`, build 75689).

- The game plays a replay back from its bytes, and from its path though it lies outside the installation's `Replays`.
  Starting one takes some 3 seconds. Leaving one returns the client to `launched`, and the next can start on the same
  client (`requests`).
- A replay's info gives its map by the path it was played from, `local_map_path`. Three AI Arena replays of
  2026-10-07, all build 75689, gave a bare file name, `PylonAIE_v4.SC2Map` and the like, and the game refused to play
  one back, "LaunchError: Unable to open map", with the map in a folder of the installation's maps (`maps/AIE`) and
  not at the top of `Maps`. Sent the map's bytes as `map_data`, it played each back as player 1 with the fog on and
  off, and as player 2 with it off. A replay saved here names the map's absolute path, and once that file is gone the
  game refuses it alike (seen 2026-10-25, with probes; tested).
- Played back as one of its players, a replay shows what that player saw when the game was played. At each of the 841
  steps the bot observed, every unit in both had the same type, owner, display, position, health, shields, energy and
  number of orders, and the actions reported were the same 465. Only mineral fields and vespene geysers remembered in
  the fog came under other tags, 631 times, each matched by type, alliance, display and position (`views`).
- An observation's actions are the player's own since the observation before, a computer's too: played back as the
  computer, its moves and trains (`views`).
- As player 0, the game lists every unit, as nobody's: the map's units read as its own and both players' as neutral,
  and its minerals, supply, score, tiles in sight and actions are all 0, with the fog on or off. The game's info lists
  no player 0 (`views`).
- With the fog off, a player is shown what it was with the fog on, the same units, tiles in sight and score, and every
  enemy unit it did not see besides: a structure displayed as remembered, `Snapshot`, and any other unit `Hidden`. The
  game reports those as their owner saw them, in type, health, shields, energy, build progress, flight, burrowing,
  weapon cooldown and facing, but not their orders. A structure a player has ordered and not yet placed, a
  `Placeholder`, is in its own view alone (`views`).
- With the fog off, in a game of player 1's against the computer whose units were made by debug commands (`unseen`):
  - an enemy unit player 1 does not see is listed `Hidden` when it stands out of player 1's sight, and the fog-on view
    lists none of them; one in its sight but undetected, an observer, is `Hidden` in both views under one tag;
  - a unit undetected in sight reports its health as 0 in both views, as one undetected does with the fog on, and the
    same observer out of sight reports its full 40;
  - a structure player 1 saw and lost sight of keeps its tag and its health, 1485 for a command center, displayed
    `Snapshot`, where the fog-on view remembers a copy under another tag with health 0;
  - no enemy unit reports an order, in either view, and the tiles in sight are the same in both;
  - the undetected observer and burrowed widow mine report no owner and no maximum health, and every enemy unit the
    player does not see reports both; a zergling burrowed undetected in sight is listed in neither view, and while it
    burrows, `Visible` in both;
  - the enemy's townhall and a marine killed out of the player's sight are reported dead in neither view, and the
    fog-on view goes on listing the townhall's copy, where the fog-off view lists the townhall no more.
- With the fog off, in four games of 100 seconds against the computer in which player 1's SCV scouted the enemy's main
  and came back, 1124 observations 8 steps apart (`scouting`):
  - every enemy unit listed `Hidden` reported its owner and maximum health, 3 of them on tiles the visibility image
    gave as in sight, and the fog-on view listed none of them;
  - the map's units in the fog are listed under their own tags, with their contents but for mined-out fields, 4 of
    them on tiles given as in sight, where the fog-on view lists copies under other tags;
  - the creep, the visibility, the score, the player's numbers, the alerts, the actions, the effects and the deaths
    were the same in both views at every observation.
- In a replay the game refuses every action, an empty one, chat and a camera move included, with "Can't issue actions
  in a replay"; every debug command, with "Not allowed during replays"; and a save, with "Can't save a replay of a
  replay". It answers an observer's camera move, the queries for abilities and pathing, the tables and the game
  info, which gives no player a name and the computer no `race_actual` (`requests`).
- At its end, a replay's last observation says `ended` and gives each player's result; a step after it is answered
  "Game has already ended". A game won through `DebugEndGame` ends in the victory and the defeat its info gives. The
  shared game, saved at its time limit before it ended, ends `Undecided` for both players at step 6720, its length,
  though its info gives both a tie (`ending`).
- Played back 8 steps a turn, observing each, the 300 seconds took some 3.6 seconds, 1,900 steps a second, with the
  fog on or off; observing every step, some 7 seconds, 900 to 950 steps a second. An observation is about 84 KB, and 96 KB
  with the fog off (`speed`).

## Time and steps

- Normal speed is 16 steps a second, and Faster, the ladder's, 22.4: 224 steps are 10 seconds (#25).
- The tables give weapon speed as the cooldown in Normal seconds (a marine that had just fired read 13.56 steps
  against the table's 13.77), movement speed per Normal second (a zergling 2.95, which is 4.13 per Faster second),
  and build and research times in steps (stimpack 2240) (#25, #30).
- A unit's `weapon_cooldown` and a buff's `buff_duration_remain` and `buff_duration_max` count steps (#24, #25).
- Chat, commands and deaths from 45 unobserved realtime steps all arrived in the next observation, and never again:
  each is reported once, in the first observation after it, however many steps that spans (#28).
- On the ladder or in realtime, an order's effect can show up an observation later than in a stepped game, and
  realtime skips steps at will (#37, stated).
