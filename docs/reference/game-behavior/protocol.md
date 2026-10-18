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
- The game answers a debug request with an empty `debug`. In a game of two clients on one machine, the client that
  joined the game made a marine, took the `minerals` cheat and drew, as the one that created it did, and was refused
  none of the three (tool `sweep_requests`, build 75689). A drawing lasts until the next debug request (#22).
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
- After `create_game` the status is `init_game`, the join returns player 1, and `game_info` gives only the joining
  player's own `race_actual` (#9; tested).
- A game with no opponent is won the moment it starts (#22).
- On every launch the game rewrites `stableid.json`, in `Documents/StarCraft II` on Windows, from the loaded map's
  mod dependencies, so its ids differ from map to map: BerlingradAIE yields 518 ability ids MagannathaAIE lacks
  (#5, #16).
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
