# Testing a bot on a recorded game

A bot runs from start to end with no game client by playing a recorded game. The bot is the player recorded, and its
handlers run on every recorded observation, which makes the recording a smoke test that runs anywhere, CI included.

## Recording a game

Both runners take `record_to`, a file to write the whole conversation with the game to:

```python
run_local("PylonAIE_v4", api, Computer(Race.ZERG), race=Race.TERRAN, record_to="tests/games/pylon.sc2rec")
```

The file is complete once the runner returns; a run killed before then leaves only part of it. NachOS's own repository
holds seven such games in `tests/corpus`, a bot that does nothing losing to the computer on each map of the ladder
pool, three of them as Terran; `tools/record_corpus.py` says what each one is.

## Playing it back

```python
from sc2nachos import run_recording

from my_bot import api


def test_the_bot_plays_a_whole_recorded_game() -> None:
    run_recording("tests/games/pylon.sc2rec", api)
```

`run_recording(recording, api, *, time_limit=None)` returns how the recorded game ended for the player recorded:
`UNDECIDED` if the recording stops before the game's end, as one made with a time limit does, `DEFEAT` if the bot
leaves first, and `TIE` at `time_limit`. A handler that raises raises out of it, as out of any game. The bot has a turn
at each recorded observation, whether the game was played stepped or in real time: the corpus's are 16 steps apart.

## What the bot sees

The game as recorded, whatever the bot does: its orders change nothing that comes after them. What it asks of the game
of its own accord is answered as if the game had answered:
- **Orders, the camera and chat** are accepted: every order reads as sent, and `api.action_failures` stays empty.
- **Debug requests** are accepted, and draw nothing.
- **`api.pathing.query_distance` and `query_distances`** answer the straight line between each pair of ends, from a
  unit where the last observation had it.
- **`api.pathing.grid`** is the grid as last recorded: the one from the start, unless the recorded bot read the grid
  itself.
- **No replay is saved**: a recording holds none, and `Api.play` logs a save asked of it as failed.

A query for abilities or placements through `api.client.query` raises `ProtocolError`, as does anything else the
recording holds no answer to.

Underneath, `run_recording` plays the file through `PlaybackTransport(recording, answer_unrecorded=True)`. Without
`answer_unrecorded`, a `PlaybackTransport` answers only what the recording was asked, in order, and raises
`ProtocolError` at the first request of another kind: a strict check that the code asks the game exactly what it asked
when the recording was made.
