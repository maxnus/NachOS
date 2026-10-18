# Tools

The sweeps measure what the game does and need StarCraft II installed; what they find goes on its topic's page under
[`docs/reference/game-behavior/`](../docs/reference/game-behavior/README.md). The generators turn what a sweep wrote,
or the vendored data, into source, and run without the game. Each tool's docstring says what it does and what it
takes. Before measuring anything in game, look here: a sweep may already do it.

| To | Run | Starts the game |
|---|---|---|
| Find what the game does with an order | `uv run python tools/sweep_orders.py`, which writes its trials as JSON | yes |
| Find which alerts a game raises, and when | `uv run python tools/sweep_alerts.py` | yes |
| Find what the game does with debug commands, chat, leaving and the pathing query | `uv run python tools/sweep_requests.py` | yes |
| Find the buffs a game puts on units | `uv run python tools/sweep_buffs.py` | yes |
| Sweep the tech tree again | `uv run python tools/sweep_tech_tree.py`, a game as each race, then `uv run python tools/generate_tech_tree.py` | yes |
| Find what each upgrade adds to each unit type | `uv run python tools/sweep_upgrades.py`, then `uv run python tools/generate_tech_tree.py` | yes |
| Measure every structure's footprint, and what lies under the map's rocks | `uv run python tools/sweep_placement.py footprints` or `ground-under-rocks`, then `uv run python tools/generate_footprints.py` | yes |
| Find where a townhall may stand around resources, and check the expansions by it | `uv run python tools/sweep_townhall_placement.py` | yes |
| Look at the ramps on a map | `uv run python tools/show_ramps.py` | yes |
| Record the corpus again | `uv run python tools/record_corpus.py` | yes |
| Regenerate the raw ids | `uv run python tools/generate_ids.py`, after refreshing `data/stableid.json` as [curating ids](../docs/maintaining/curating-ids.md) says | no |
| Regenerate `UnitType` | `uv run python tools/generate_unit_types.py`, after changing `UnitTypeId` | no |
| Regenerate `ActionResult` | `uv run python tools/generate_action_results.py`, after a protocol release adds results | no |
| List the multi-line docstrings, to check each after a round of doc edits | `uv run python tools/list_multiline_docstrings.py` | no |

`_sandbox.py` is what the in-game tools share: a game to play, free ground to put structures on, and the requests they
make of the game.
