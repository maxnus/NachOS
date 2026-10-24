# Playing back a replay

A replay, the `.SC2Replay` file the game saves, plays back through the same `Api` a game plays through. Its handlers
run on every observation, a turn every `steps_per_turn` steps, and it sees the game as one of the replay's players saw
it when the game was played. `run_replay` starts a game client to play it back, and stops the client at the end.

```python
from sc2nachos import Api, run_replay
from sc2nachos.events import EnemyUnitFirstSeenEvent

api = Api()


@api.on(EnemyUnitFirstSeenEvent)
def note(event: EnemyUnitFirstSeenEvent) -> None:
    print(f"{api.time:.0f} s: {event.unit.type_id.name}")


run_replay("games/ladder.SC2Replay", api, player_id=1)
```

`run_replay(replay, api, *, player_id, steps_per_turn=1, time_limit=None, record_to=None, installation=None,
window=(1024, 768))` returns how the replay ended for that player.

## Whose view

`player_id` names the player, 1 or 2, and has no default. The api plays the replay as that player:
- `api.player` is that player and `api.enemy` the other, and a computer's race is the one it asked for, as the game
  gives a computer none;
- `api.units.own` are its units, and `api.resources`, `api.supply`, `api.score` and `api.vision` its own;
- each order it gave comes as an `OwnActionEvent`, a computer's included;
- the units are those it saw, as it saw them, in every field the game reports: the same as a bot of that player saw
  them in the game, with the same tags, but for mineral fields and vespene geysers remembered in the fog, which come
  under others ([game behavior](reference/game-behavior/protocol.md#playing-a-replay-back)).

To follow both sides, play the replay once as each. Any other `player_id` raises `ValueError`, 0 included: the game's
player 0 is no player's view ([game behavior](reference/game-behavior/protocol.md#playing-a-replay-back)).

## What the api can do in a replay

It reads everything it reads in a game, and the game answers its queries, `api.pathing.query_distance` among them. It
cannot act: the game takes no actions or debug commands in a replay. A turn whose handlers order, chat, move the
camera, draw or cheat sends its request once they have run, and `run_replay` raises `ProtocolError` with the game's
words, "Can't issue actions in a replay" or "Not allowed during replays". A turn that asks for nothing sends nothing,
so an api that only reads plays any replay through.

## How it ends

A replay played to its end returns what its last observation gives the player: `VICTORY` or `DEFEAT`, or `UNDECIDED`
for a replay saved before its game ended, as `save_replay_to` saves one at a time limit. `api.leave_game()` stops it,
and so does reaching `time_limit`, in game seconds; either returns `UNDECIDED`, since nothing was conceded or drawn.

## The replay's build

A replay plays back on the build it was played on, which must be installed, or `run_replay` raises `GameVersionError`
naming it and the builds there are. The newest build installed reads which one the replay needs, and a second client
starts when that is not the one. A replay of a game played on an installation plays back on it.

## Playing it back again, with no game

`record_to` records the whole conversation with the game, as for `run_local`, and `run_recording` plays the recording
back with no game client, as the player it was played for:

```python
from sc2nachos import run_recording, run_replay

run_replay("games/ladder.SC2Replay", api, player_id=1, record_to="games/ladder-1.sc2rec")
run_recording("games/ladder-1.sc2rec", api)
```

It plays back exactly, as [testing](testing.md#playing-a-bots-own-game-back-exactly) describes: the api must ask what
the one recorded asked. `permissive=True` raises `ValueError` for a replay played back.
