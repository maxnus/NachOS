# NachOS

An event-driven StarCraft II bot API for Python, built directly on the
[StarCraft II client protocol](https://github.com/Blizzard/s2client-proto).

> **Status: pre-alpha.** Nothing here is usable yet. The library is being built out milestone by milestone
> alongside its first consumer, the Terran bot [AvocaDOS](https://aiarena.net/).

```bash
pip install sc2nachos
```

```python
import sc2nachos
```

The distribution and the import name are both `sc2nachos`. The project is called NachOS; the bare `nachos` name on
PyPI belongs to an unrelated project.

## Design

**You instantiate the API. You do not subclass it, and your bot does not live inside it.** NachOS exposes an
`Api` that owns the client, game state, game data, units, orders and the event bus. Construct one and reach it
however suits your bot — most bots will want a module-level singleton:

```python
# my_bot/api.py
from sc2nachos import Api

api = Api()
on = api.event.on
```

```python
# my_bot/economy.py — anywhere else in your bot
from sc2nachos.events import TurnEvent

from my_bot.api import api, on


@on(TurnEvent, every_steps=4)
def manage_workers(event):
    for worker in api.workers.idle:
        ...
```

[Events](docs/events.md) lists every event a game hands out, and how to select some of them.
[Orders](docs/orders.md) says how a bot orders its units, and what it learns of each order.

NachOS itself never creates or exposes a singleton, and holds no module-level mutable state. The singleton is your
choice, confined to one line of your own code, and one api plays any number of games in turn. Two bots playing
each other run a process each, as they would on a ladder.

Your own helpers live in your own modules, as ordinary functions and objects. There is no mixin to inherit and no
extension hook to register. If you find yourself wanting to subclass so you can hang a helper off `api`, that is a
gap in NachOS rather than a pattern to follow — please open an issue.

`Api()` is cheap and needs no live connection, so you can construct it at import time and register event
handlers as your modules load. Connecting happens later, in the runner:

```python
from sc2nachos import ApiBot, Computer, Difficulty, Race, run_local

run_local("PylonAIE_v4", ApiBot(api, Race.TERRAN), Computer(Race.ZERG, Difficulty.VERY_HARD))
```

## Coming from python-sc2

NachOS is not a drop-in replacement. [Migrating from python-sc2](docs/migrating-from-python-sc2.md) lists the
places where the obvious translation goes wrong.

## Requirements

Python 3.12+ and a StarCraft II installation.

## License

MIT. See [LICENSE](LICENSE), and [NOTICE](NOTICE) for attribution — NachOS is an independent reimplementation that
used [python-sc2](https://github.com/BurnySc2/python-sc2) (MIT, © 2017 Hannes Karppila) as a reference.
