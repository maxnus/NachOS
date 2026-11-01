# Tools

The sweeps measure what the game does, need StarCraft II installed and take minutes; what they find goes on its topic's
page under [`docs/reference/game-behavior/`](../docs/reference/game-behavior/README.md). The generators turn what a
sweep wrote, or the vendored data, into source, and run without the game. Each tool's docstring says what it does and
what it takes. Before measuring anything in game, look here: a sweep may already do it.

| To | Run | Starts the game |
|---|---|---|
| Find what the game does with an order, how long a gas building takes to build, and how long what a unit makes has left against the progress it reports | `uv run python tools/sweep_orders.py`, which writes its trials as JSON, or with `production-times` for the last | yes |
| Find which alerts a game raises, and when | `uv run python tools/sweep_alerts.py` | yes |
| Find what the game does with debug commands, chat, leaving, the pathing query and saving a replay, how its tables differ from the corpus's, and what it says of each player | `uv run python tools/sweep_requests.py` | yes |
| Find what the game does when it plays a replay back: a player's view against the game as it was played, the fog off and player 0, what the fog off shows of units unseen, what it answers in a replay, how a replay ends, and how fast one plays back and records | `uv run python tools/sweep_replays.py`, or with `views`, `requests`, `ending`, `unseen`, `scouting` or `speed` | yes |
| Find the buffs a game puts on units | `uv run python tools/sweep_buffs.py` | yes |
| Find what the game lists of a structure in the fog seen again between two observations, and of one killed out of sight and made again at its point | `uv run python tools/sweep_fog.py`, or with `retag` or `rebuilt` | yes |
| Find what the game reports of how long a force field, a reaper's grenade, a MULE and broodlings have left, to the player who made them and to its enemy | `uv run python tools/sweep_timed_life.py`, or with `force-field`, `grenade`, `mule` or `broodlings` | yes |
| Sweep the tech tree again | `uv run python tools/sweep_tech_tree.py`, a game as each race, then `uv run python tools/generate_tech_tree.py` | yes |
| Find what each upgrade adds to each unit type | `uv run python tools/sweep_upgrades.py`, then `uv run python tools/generate_tech_tree.py` | yes |
| Measure every structure's footprint, and what lies under the map's rocks | `uv run python tools/sweep_placement.py footprints` or `ground-under-rocks`, then `uv run python tools/generate_footprints.py` | yes |
| Find how far an attack reaches, what one hit takes off, what the units the tables give no weapon do, what speeds an attack up, how far apart shots come and what random delay each draws, what the cooldown reads between them, what an attack speed-up does to the delay, and whether a move cancels a shot | `uv run python tools/sweep_combat.py range`, `damage`, `no-weapon`, `attack-speed`, `shot-timing`, `delay-bounds`, `delay-weapons`, `delay-sped-up`, `cooldown-trace` or `move-before-shot` | yes |
| Find what the game's pathing grid shows as a game goes on, what each structure blocks for walking, and what the pathing query does from inside one | `uv run python tools/sweep_pathing.py timeline`, `footprints` or `inside` | yes |
| Measure how long one SCV takes to repair each Terran mechanical type, and what it costs | `uv run python tools/sweep_repair.py`, then `uv run python tools/generate_repair_steps.py` | yes |
| Measure how each unit type turns, and whether it turns to fire | `uv run python tools/sweep_turning.py`, then `uv run python tools/generate_turning.py` | yes |
| Find where a townhall may stand around resources, and check the expansions by it | `uv run python tools/sweep_townhall_placement.py` | yes |
| Look at the ramps on a map | `uv run python tools/show_ramps.py` | yes |
| Check that the walls `api.placement` gives close each main's ramp of the pool | `uv run python tools/sweep_walls.py`, or with the maps to check | yes |
| Record the corpus again, and the game's data `sc2nachos.testing` ships from it | `uv run python tools/record_corpus.py`, then `uv run python tools/write_shipped_game_data.py` | yes |
| Regenerate the raw ids | `uv run python tools/generate_ids.py`, after refreshing `data/stableid.json` as [curating ids](../docs/maintaining/curating-ids.md) says | no |
| Regenerate `UnitType` | `uv run python tools/generate_unit_types.py`, after changing `UnitTypeId` | no |
| Regenerate `ActionResult` | `uv run python tools/generate_action_results.py`, after a protocol release adds results | no |
| List the multi-line docstrings, to check each after a round of doc edits | `uv run python tools/list_multiline_docstrings.py` | no |

`_sandbox.py` is what the in-game tools share: a game to play, free ground to put structures on, and the requests they
make of the game.
