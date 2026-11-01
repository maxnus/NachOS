# Testing a bot

A bot is tested without a game client in two ways: a test of code that reads units makes the units it needs
([making units for a unit test](#making-units-for-a-unit-test)), and the whole bot plays a recorded game.

A bot runs from start to end with no game client by playing a recorded game. The bot is the player recorded, and its
handlers run on every recorded observation, a turn at each, whether the game was played stepped, `steps_per_turn` steps
apart, or in real time. `run_recording` plays a recording in one of two ways:
- **exactly**, by default, for a bot playing its own game back, as it asked then, with the game's own answers;
- **permissively**, for any bot, or one whose code has changed since, which makes the recording a smoke test that runs
  anywhere, CI included.

## Recording a game

Both runners take `record_to`, a file to write the whole conversation with the game to:

```python
run_local("PylonAIE_v4", api, Computer(Race.ZERG), race=Race.TERRAN, record_to="tests/games/pylon.sc2rec")
```

`run_from_command_line` takes the file as `--RecordFile`, in a ladder game too
([playing on the ladder](ladder.md#replays-recordings-and-results)). The file is complete once the runner returns; a
run killed before then leaves only part of it.

Record games of the bot itself for its tests: a game in which it builds, fights and loses units gives its code those
turns to run on. The corpus in NachOS's repository, `tests/corpus`, is NachOS's own test data and does not ship with
the package: a bare api that never acts, whose recorded side only ever has its townhall and workers.

A recording plays back only on a NachOS that asks the same kinds of question, in the same order, as the one that
recorded it, permissively too: NachOS's own requests, such as for the tables and each observation, are answered from the
file in turn. One recorded before NachOS came to ask otherwise raises `ProtocolError`, and is recorded again.

## Playing a bot's own game back exactly

```python
from sc2nachos import run_recording

from my_bot import api

run_recording("games/ladder.sc2rec", api, enemy_ladder_id="5a1f")
```

The bot must ask what it asked when the game was recorded, in the same order, so it must read what it read then, its
own files included, and it gets the game's own answers: to its orders, its queries and everything else. That
reproduces a crash as it happened, its handler's exception raising out of `run_recording` as out of the game. A
request of another kind than the recording holds next raises `ProtocolError`; only the kind of each request is
checked, not its contents. The game is created and played in real time as recorded.

`run_recording(recording, api, *, permissive=False, time_limit=None, enemy_ladder_id=None, save_replay_to=None)`
returns how the recorded game ended for the player recorded, or `TIE` at `time_limit`. The three keywords must be what
the game was played with:
- `time_limit`, without which a game its time limit ended raises `ProtocolError` at its end;
- `enemy_ladder_id`, without which a bot that reads `api.enemy.ladder_id` reads `None`;
- `save_replay_to`, without which a game the bot left after saving its replay raises at the leave. Given, it holds the
  replay recorded.

A game cut short by its connection closing, as a ladder closes it when either bot crashes, plays back up to the close
and then raises `ProtocolError`: the recording holds no answer to the request the close cut off. A replay played back
by `run_replay` and recorded plays back exactly too, as the player it was played for
([playing back a replay](replays.md#playing-it-back-again-with-no-game)).

Underneath, `run_recording` plays the file through a `PlaybackTransport`.

## Playing any bot through a recording

```python
def test_the_bot_plays_a_whole_recorded_game() -> None:
    run_recording("tests/games/pylon.sc2rec", api, permissive=True)
```

With `permissive=True`, it returns `UNDECIDED` if the recording stops before the game's end, as one made with a time
limit does, and `DEFEAT` if the bot leaves first. The bot sees the game as recorded, whatever it does: its orders
change nothing that comes after them. A game an older version of the bot played still tests a newer one: where the newer
one decides otherwise, it sees what the older one did. What it asks of the game of its own accord is answered as if the
game had answered:
- **Orders, the camera and chat** are accepted: every order reads as sent, and `api.action_failures` stays empty.
- **Debug requests** are accepted, and draw nothing.
- **`api.pathing.query_distance` and `query_distances`** answer the straight line between each pair of ends, from a
  unit where the last observation had it.
- **`api.pathing.grid`** is the grid as last recorded: the one from the start, unless the recorded bot read the grid
  itself.
- **No replay is saved**: a recording holds none, and `Api.play` logs a save asked of it as failed.

A query for abilities or placements through `api.client.query` raises `ProtocolError`, as does anything else the
recording holds no answer to.

Underneath, `run_recording` plays the file through a `PermissivePlaybackTransport`.

## Making units for a unit test

`MadeUpUnits` makes units as NachOS makes them in a game, from the reports of one observation after another, so the
code under test reads the same `Unit` and `OwnUnit` objects it reads in a game:

```python
from sc2nachos.ids import UnitTypeId
from sc2nachos.testing import MadeUpUnits, UnitReport
from sc2nachos.units import Alliance


def test_the_marine_targets_the_nearer_zergling() -> None:
    marine, near_zergling, far_zergling = MadeUpUnits().observe(
        UnitReport(1, UnitTypeId.MARINE, at=(10, 10)),
        UnitReport(2, UnitTypeId.ZERGLING, at=(13, 10), alliance=Alliance.ENEMY),
        UnitReport(3, UnitTypeId.ZERGLING, at=(20, 10), alliance=Alliance.ENEMY),
    )
    assert pick_target(marine, [near_zergling, far_zergling]) is near_zergling
```

- **A `UnitReport`** is this player's finished unit in sight unless its fields say otherwise, and each field reads back
  as the unit's property of the same name. Health, shield and energy read zero unless given, and each maximum reads as
  its value. An order is the ability and its target, and its progress if any: `(AbilityId.MOVE, (20, 10))`, aimed at a
  point, at another unit's report, at a tag, or at `None`.
- **`observe`** returns the units of its reports in their order. A report under a tag seen before is of the same unit,
  read anew: `dataclasses.replace(report, at=(12, 10))` moves it, at the velocity the steps between give. A unit not
  reported again is no longer listed, `dead` reports units dead, and `units` lists every unit of the latest
  observation. Each observation is a step after the one before unless `step` says otherwise.
- **The tables** the units read their types from are those NachOS ships, of StarCraft II 4.10 for Linux, build 75689,
  read afresh in about 15 ms for each `MadeUpUnits`. A test may pass its own, or `shipped_tables()` read once for many.

The units belong to no game: there is no map, no event and no order sent. Code that reads the api is tested by playing
a recorded game, as below.
