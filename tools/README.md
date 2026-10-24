# Tools

The sweeps measure what the game does, need StarCraft II installed and take minutes; what they find goes on its topic's
page under [`docs/reference/game-behavior/`](../docs/reference/game-behavior/README.md). The generators turn what a
sweep wrote, or the vendored data, into source, and run without the game. Each tool's docstring says what it does and
what it takes. Before measuring anything in game, look here: a sweep may already do it.

| To | Run | Starts the game |
|---|---|---|
| Find what the game does with an order, and how long a gas building takes to build | `uv run python tools/sweep_orders.py`, which writes its trials as JSON | yes |
| Find which alerts a game raises, and when | `uv run python tools/sweep_alerts.py` | yes |
| Find what the game does with debug commands, chat, leaving, the pathing query and saving a replay, how its tables differ from the corpus's, and what it says of each player | `uv run python tools/sweep_requests.py` | yes |
| Find what the game does when it plays a replay back: a player's view against the game as it was played, the fog off and player 0, what it answers in a replay, how a replay ends, and how fast one plays back and records | `uv run python tools/sweep_replays.py`, or with `views`, `requests`, `ending` or `speed` | yes |
| Find the buffs a game puts on units | `uv run python tools/sweep_buffs.py` | yes |
| Sweep the tech tree again | `uv run python tools/sweep_tech_tree.py`, a game as each race, then `uv run python tools/generate_tech_tree.py` | yes |
| Find what each upgrade adds to each unit type | `uv run python tools/sweep_upgrades.py`, then `uv run python tools/generate_tech_tree.py` | yes |
| Measure every structure's footprint, and what lies under the map's rocks | `uv run python tools/sweep_placement.py footprints` or `ground-under-rocks`, then `uv run python tools/generate_footprints.py` | yes |
| Find how far an attack reaches, what one hit takes off, what the units the tables give no weapon do, what speeds an attack up, how far apart shots come and what random delay each draws, what the cooldown reads between them, what an attack speed-up does to the delay, and whether a move cancels a shot | `uv run python tools/sweep_combat.py range`, `damage`, `no-weapon`, `attack-speed`, `shot-timing`, `delay-bounds`, `delay-weapons`, `delay-sped-up`, `cooldown-trace` or `move-before-shot` | yes |
| Find what the game's pathing grid shows as a game goes on, what each structure blocks for walking, and what the pathing query does from inside one | `uv run python tools/sweep_pathing.py timeline`, `footprints` or `inside` | yes |
| Find where a townhall may stand around resources, and check the expansions by it | `uv run python tools/sweep_townhall_placement.py` | yes |
| Look at the ramps on a map | `uv run python tools/show_ramps.py` | yes |
| Check that the walls `api.placement` gives close each main's ramp of the pool | `uv run python tools/sweep_walls.py`, or with the maps to check | yes |
| Record the corpus again | `uv run python tools/record_corpus.py` | yes |
| Regenerate the raw ids | `uv run python tools/generate_ids.py`, after refreshing `data/stableid.json` as [curating ids](../docs/maintaining/curating-ids.md) says | no |
| Regenerate `UnitType` | `uv run python tools/generate_unit_types.py`, after changing `UnitTypeId` | no |
| Regenerate `ActionResult` | `uv run python tools/generate_action_results.py`, after a protocol release adds results | no |
| List the multi-line docstrings, to check each after a round of doc edits | `uv run python tools/list_multiline_docstrings.py` | no |

`_sandbox.py` is what the in-game tools share: a game to play, free ground to put structures on, and the requests they
make of the game.
