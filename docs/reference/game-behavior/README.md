# How the game behaves

What StarCraft II does, as NachOS has seen it through the API. Observed with StarCraft II 5.0.15, base build 95841,
unless an entry says otherwise. A fact that shapes one piece of code is also in that code's docstring; these pages are
where to look one up, and where a new finding goes, in its topic's page.
[curating-ids.md](../../maintaining/curating-ids.md) says how the game's ids were sorted into the curated ones.

- [The connection, launching and time](protocol.md)
- [Cheats and debug commands](cheats.md)
- [Units](units.md): what an observation lists and what it reports of a unit, turning, units coming, changing and
  going, and vision
- [Damage, energy, spells and buffs](damage-and-buffs.md)
- [Upgrades and the game's tables](upgrades-and-tables.md)
- [Abilities and orders](abilities-and-orders.md), and the actions the game reports
- [Alerts, the camera and chat](alerts-and-chat.md)
- [Terrain, placement and pathing](map.md)
- [The score, supply and the computer](score-and-computer.md)
- [Not understood yet](not-understood-yet.md): readings that contradict each other or could not be observed

## How an entry was seen

Each entry ends with how it was seen:

- **#n**: reproduced in a running game by the probes or tools of pull request #n, whose description says how;
- **in game**: seen in a running game, with no record of which pull request;
- **tested**: an in-game test (`uv run pytest -m integration`) still checks it;
- **tool** *name*: `tools/`*name*`.py` reproduces it in a game and records it;
- **corpus**: read off the recorded games in `tests/corpus`;
- **stated**: written down in the code or docs with no record of how it was seen, so worth checking.
