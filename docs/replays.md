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

`run_replay(replay, api, *, player_id, disable_fog=False, steps_per_turn=1, time_limit=None, record_to=None,
installation=None, window=(1024, 768))` returns how the replay ended for that player.

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

## With the fog off

`disable_fog=True` plays the replay back as the same player, and shows the api the same: `api.units`, every read and
every event are what that player knew, as with the fog on. Besides, `api.all_units` holds every unit of the game as it
is, those the player did not see included:

```python
from sc2nachos import Api, UnitType, run_replay
from sc2nachos.events import TurnEvent

api = Api()


@api.on(TurnEvent, every_steps=224)
def compare(_: TurnEvent) -> None:
    known = len(api.units.enemy.of_type(UnitType.AnyZergling))
    there = len(api.all_units.enemy.of_type(UnitType.AnyZergling))
    print(f"{api.time:.0f} s: {known} zerglings known of, {there} there")


run_replay("games/ladder.SC2Replay", api, player_id=1, disable_fog=True)
```

- A unit in both is under the same `id`, so `api.all_units.get(unit.id)` is a unit the player lost sight of as it is
  now, where `api.known_units` holds it as last seen. Ids in `api.units` skip the units the player never saw.
- Every unit of `api.all_units` is read in full and reads `VISIBLE`, but one undetected in the player's sight: the game
  reports no health and the like for it, with the fog off too, so it reads as last reported in full. A burrowed
  zergling undetected in the player's sight is in neither: the game lists it in neither view.
- No event comes of `api.all_units`: the events are the player's, as with the fog on.
- In a game, or a replay played back with the fog on, reading `api.all_units` raises `FogOnError`.

With the fog off, the game lists every unit, those out of the player's sight under their own tags and in full, and
NachOS makes the player's view from it: what the player did not see left out, and the structures and map's units it
remembers in the fog listed as copies, as the game lists them with the fog on
([game behavior](reference/game-behavior/protocol.md#playing-a-replay-back)). Played back a step at a turn, the
player's view reads as a playback with the fog on does, at every step. A structure that died or moved off out of sight
is forgotten once the tile under it reads in sight, which may come at another step than with the fog on. With more steps a turn, a geyser remembered in
the fog was seen to come under a new tag between two turns with the fog on, which NachOS reads as one found dead and
another first seen, where with the fog off it stays the one unit
([not understood yet](reference/game-behavior/not-understood-yet.md)).

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

It plays back exactly, as [testing](testing.md#playing-a-bots-own-game-back-exactly) describes, with the fog as it
was: the api must ask what the one recorded asked. `permissive=True` raises `ValueError` for a replay played back.
