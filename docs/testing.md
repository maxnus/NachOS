# Testing a bot on a recorded game

A bot runs from start to end with no game client by playing a recorded game. The bot is the player recorded, and its
handlers run on every recorded observation, a turn at each, whether the game was played stepped or in real time: the
corpus's are 16 steps apart. `run_recording` plays a recording in one of two ways:
- **exactly**, by default, for a bot replaying its own game, as it asked then, with the game's own answers;
- **permissively**, for any bot, or one whose code has changed since, which makes the recording a smoke test that runs
  anywhere, CI included.

## Recording a game

Both runners take `record_to`, a file to write the whole conversation with the game to:

```python
run_local("PylonAIE_v4", api, Computer(Race.ZERG), race=Race.TERRAN, record_to="tests/games/pylon.sc2rec")
```

The file is complete once the runner returns; a run killed before then leaves only part of it. NachOS's own repository
holds seven such games in `tests/corpus`, a bot that does nothing losing to the computer on each map of the ladder
pool, three of them as Terran; `tools/record_corpus.py` says what each one is.

## Replaying a bot's own game exactly

```python
from sc2nachos import run_recording

from my_bot import api

run_recording("games/ladder.sc2rec", api, enemy_ladder_id="5a1f")
```

The bot must ask what it asked when the game was recorded, in the same order, and gets the game's own answers: to its
orders, its queries and everything else. That reproduces a crash as it happened, its handler's exception raising out
of `run_recording` as out of the game. A request of another kind than the recording holds next raises `ProtocolError`;
only the kind of each request is checked, not its contents. The game is created and played in real time as recorded.

`run_recording(recording, api, *, permissive=False, time_limit=None, enemy_ladder_id=None)` returns how the recorded
game ended for the player recorded, or `TIE` at `time_limit`. Both keywords must be what the game was played with: a
game its time limit ended raises `ProtocolError` at its end without it, and a bot that reads `api.enemy.ladder_id`
reads `None` without the ladder's.

Underneath, `run_recording` plays the file through a `PlaybackTransport`.

## Playing any bot through a recording

```python
def test_the_bot_plays_a_whole_recorded_game() -> None:
    run_recording("tests/corpus/PylonAIE_v4-TvZ.sc2rec", api, permissive=True)
```

With `permissive=True`, it returns `UNDECIDED` if the recording stops before the game's end, as one made with a time
limit does, and `DEFEAT` if the bot leaves first. The bot sees the game as recorded, whatever it does: its orders
change nothing that comes after them. What it asks of the game of its own accord is answered as if the game had
answered:
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
