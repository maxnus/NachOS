# Testing a bot on a recorded game

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
turns to run on. NachOS's own corpus, in its repository's `tests/corpus`, is its own test data and is not installed with
it: a bare api that never acts, whose recorded side only ever has its townhall and workers.

A recording plays back only on a NachOS that asks the game what it asked when the game was recorded, permissively too:
NachOS's own requests, for the game info, the tables and each observation, are answered from the file in order. One
recorded before NachOS came to ask otherwise raises `ProtocolError`, and is recorded again.

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
