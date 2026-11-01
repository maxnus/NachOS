# NachOS

An event-driven StarCraft II bot API for Python, built directly on the
[StarCraft II client protocol](https://github.com/Blizzard/s2client-proto).

> **Status: alpha.** The Terran bot [AvocaDOS](https://github.com/maxnus/AvocaDOS) has played on the
> [AI Arena](https://aiarena.net/) ladder on NachOS since its version 1.4.0. Until NachOS 1.0, a release may still
> change the API.

```bash
pip install sc2nachos
```

```python
import sc2nachos
```

NachOS needs Python 3.12 or later. The distribution and the import name are both `sc2nachos`. The project is called
NachOS; the bare `nachos` name on PyPI belongs to an unrelated project.

## Getting started

1. **Install StarCraft II.** On Linux, download Blizzard's headless client from
   [s2client-proto](https://github.com/Blizzard/s2client-proto#downloads): NachOS is tested against its 4.10 build,
   75689, the one the AI Arena ladder plays, and `run_local` logs a warning for any other. On Windows and macOS,
   Battle.net installs the current build, free to play, which NachOS is not tested on. NachOS finds the game through
   `SC2PATH`, then where the launcher recorded it, then where the platform installs it by default:
   `C:\Program Files (x86)\StarCraft II`, `/Applications/StarCraft II` or `~/StarCraftII`.
2. **Add the maps.** NachOS finds a map by its name anywhere under the game's `Maps` folder, so a folder of maps can
   go in as it is. [`sc2maps/AIE`](https://github.com/maxnus/NachOS/tree/main/sc2maps/AIE) holds the AI Arena
   ladder's maps that NachOS is tested on.
3. **Play a game.** [`examples/demo_bot.py`](https://github.com/maxnus/NachOS/blob/main/examples/demo_bot.py) is a
   whole bot in one file: a Terran that trains SCVs, expands and attacks with marines, and beats the built-in computer
   at medium difficulty. Save it and run:

   ```bash
   python demo_bot.py --Map PylonAIE_v4 --ComputerRace ZERG --ComputerDifficulty MEDIUM
   ```

## What NachOS has

- **Units**: `api.units`, this player's, the enemy's and the map's, remembered structures included, selected by
  [type group](https://maxnus.github.io/NachOS/sc2nachos/units.html#UnitType), alliance and distance; and a bot's own
  data for each unit in a [`UnitDict`](https://maxnus.github.io/NachOS/sc2nachos/units.html#UnitDict), which forgets a
  unit once it dies.
- **Events** for what happens in a game, from a unit's death to an upgrade finishing, each subscribed to with
  `api.on` ([guide](https://github.com/maxnus/NachOS/blob/main/docs/events.md)).
- **Orders** through `api.orders.issue`, with what the game refused listed in `api.action_failures` on the next turn
  ([guide](https://github.com/maxnus/NachOS/blob/main/docs/orders.md)).
- **The game's data**: `api.data`, every unit type, ability, upgrade and weapon from the game's tables, with the tech
  tree, the structures' footprints and what the tables leave out, measured in the game
  ([reference](https://maxnus.github.io/NachOS/sc2nachos/gamedata.html)).
- **The map**: `api.map`'s heights, ramps and start locations, and `api.expansions`
  ([reference](https://maxnus.github.io/NachOS/sc2nachos/gamemap.html)).
- **Placement**: `api.placement`, where a structure may stand now, sites kept for builds to come, and the walls at a
  main's ramp ([reference](https://maxnus.github.io/NachOS/sc2nachos/placement.html)).
- **Pathing**: `api.pathing`, the cheapest paths on the game's current pathing grid, and the game's own distances
  ([reference](https://maxnus.github.io/NachOS/sc2nachos/pathing.html)).
- **Combat reads** on a unit: the weapon that can hit a target, whether it is in range, one attack's damage and the
  damage per second.
- **Debugging**: drawing on the game's screen, cheats and creating units
  ([guide](https://github.com/maxnus/NachOS/blob/main/docs/debugging.md)).
- **Running** a game against the built-in computer, on the ladder, and on GitHub Actions
  ([ladder](https://github.com/maxnus/NachOS/blob/main/docs/ladder.md),
  [GitHub Actions](https://github.com/maxnus/NachOS/blob/main/docs/github-actions.md)).
- **Recordings and replays**: a game recorded and played back through a bot with no game client, and a replay played
  back as one of its players saw it ([replays](https://github.com/maxnus/NachOS/blob/main/docs/replays.md)).
- **Testing**: `sc2nachos.testing` makes units for a bot's unit tests
  ([guide](https://github.com/maxnus/NachOS/blob/main/docs/testing.md)).
- **Extensions**: features a bot opts into, NachOS's or anyone's, which the api builds once the game starts and holds
  as `api.extensions`. NachOS's first is speed mining
  ([guide](https://github.com/maxnus/NachOS/blob/main/docs/extensions.md)).

## Design

**You instantiate the API. You do not subclass it, and your bot does not live inside it.** NachOS exposes an
`Api` that owns the client, game state, game data, units, orders and event handlers. Construct one and reach it
however suits your bot — most bots will want a module-level singleton:

```python
# my_bot/api.py
from sc2nachos import Api

api = Api()
```

```python
# my_bot/economy.py — anywhere else in your bot
from sc2nachos import GameTime
from sc2nachos.events import TurnEvent

from my_bot.api import api


@api.on(TurnEvent, min_interval=GameTime(steps=4))
def manage_workers(event):
    for worker in api.units.own.workers.idle:
        ...
```

NachOS itself never creates or exposes a singleton and holds no module-level mutable state. The singleton is your
choice, confined to one line of your own code. An api plays one game, so a bot plays a game per process, as on a
ladder, and two bots playing each other run in a process each. A program that plays several games in one process,
such as a script reading replays, makes an api for each.

Your own helpers live in your own modules, as ordinary functions and objects, and there is no mixin to inherit. A
feature a bot opts into, NachOS's or anyone's, is an extension: `Api(extensions=BotExtensions)` builds the bot's own
object holding its extensions once the game starts, and `api.extensions` holds it
([extensions](https://github.com/maxnus/NachOS/blob/main/docs/extensions.md)). If you want to subclass so you can
hang a helper off `api`, that is a gap in NachOS, not a pattern to follow — please open an issue.

`Api()` is cheap and needs no live connection, so you can construct it at import time and register handlers as
your modules load. Connecting happens later, in the runner:

```python
from sc2nachos import Computer, Difficulty, Race, run_local

run_local("PylonAIE_v4", api, Computer(Race.ZERG, Difficulty.VERY_HARD), race=Race.TERRAN)
```

## Documentation

- [API reference](https://maxnus.github.io/NachOS/): every public module, class and function, from its docstrings,
  for the latest release.
- [Events](https://github.com/maxnus/NachOS/blob/main/docs/events.md): every event a game hands out, and how to
  subscribe to only some of them.
- [Orders](https://github.com/maxnus/NachOS/blob/main/docs/orders.md): how a bot orders its units, and what it learns
  about each order.
- [Debugging](https://github.com/maxnus/NachOS/blob/main/docs/debugging.md): drawing on the game's screen, cheats, and
  creating units.
- [Extensions](https://github.com/maxnus/NachOS/blob/main/docs/extensions.md): features a bot opts into, how to use
  one and how to write one.
- [Running StarCraft II on GitHub Actions](https://github.com/maxnus/NachOS/blob/main/docs/github-actions.md): playing
  matches in a bot repository's workflows.
- [Playing on the ladder](https://github.com/maxnus/NachOS/blob/main/docs/ladder.md): one script for the ladder and
  for local games, and what the ladder passes.
- [Testing a bot](https://github.com/maxnus/NachOS/blob/main/docs/testing.md): making units for a unit test, and
  playing a bot through a recorded game, with no game client.
- [Playing back a replay](https://github.com/maxnus/NachOS/blob/main/docs/replays.md): a replay played through an api
  as one of its players saw the game, with every unit besides if the fog is off, and recorded to play again with no
  game client.
- [Migrating from python-sc2](https://github.com/maxnus/NachOS/blob/main/docs/migrating-from-python-sc2.md): the same
  bot in both, and where a translation goes wrong;
  [python-sc2 and NachOS](https://github.com/maxnus/NachOS/blob/main/docs/reference/python-sc2.md) has every name and
  every difference.
- [How the game behaves](https://github.com/maxnus/NachOS/blob/main/docs/reference/game-behavior/README.md): what
  StarCraft II was seen to do through the API, by topic, each finding with how it was seen.

## License

MIT. See [LICENSE](https://github.com/maxnus/NachOS/blob/main/LICENSE), and
[NOTICE](https://github.com/maxnus/NachOS/blob/main/NOTICE) for attribution — NachOS is an independent
reimplementation that used [python-sc2](https://github.com/BurnySc2/python-sc2) (MIT, © 2017 Hannes Karppila) as a
reference.
