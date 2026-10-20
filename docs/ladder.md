# Playing on the ladder

A ladder such as [AI Arena](https://aiarena.net) starts the game client itself, creates the match, and then runs your
bot's script with the client's address on the command line. One script serves the ladder and your own local games:

```python
# run.py
from sc2nachos import ApiBot, Race, run_from_command_line

from my_bot import api

if __name__ == "__main__":
    run_from_command_line(ApiBot(api, Race.TERRAN, "MyBot"), steps_per_turn=2)
```

Given the ladder's `--GamePort`, `run_from_command_line` joins the ladder's game. Without it, it plays a local game
against the built-in computer, with the arguments [the match workflow](github-actions.md#1-a-game-script) passes:
`--ComputerRace`, `--ComputerDifficulty` and `--ComputerBuild` as NachOS's member names, `--Map` and `--TimeLimit`.
The names follow the ladder's, and `--RealTime` plays either kind of game in real time. `steps_per_turn` is yours to
choose in the script: the command line never sets it.

## What the ladder passes

| Argument | What NachOS does with it |
|---|---|
| `--LadderServer` | the client's host, 127.0.0.1 if not given |
| `--GamePort` | the client's port; with it, the game is the ladder's |
| `--StartPort` | the ports the game's players connect on; a game against the built-in computer has none |
| `--OpponentId` | the ladder's id for your opponent, read as `api.enemy.ladder_id` |
| `--RealTime` | plays the game in real time, as the ladder created it |
| `--ComputerOpponent`, `--ComputerRace`, `--ComputerDifficulty` | nothing in a ladder game, which the ladder has created, whatever form their values take |

`api.enemy.ladder_id` is the one way to tell opponents apart: it stays the same in every game against the same bot,
while `api.enemy.name` reads `None` wherever the game gives no name, as in every game seen so far
([game behavior](reference/game-behavior/protocol.md#launching-maps-and-players)). Outside a ladder game, `ladder_id` is
`None`.

## Your own arguments

`run_from_command_line` reads only the arguments above and the match workflow's, and leaves any other for you,
listing it in the log. Read yours with a parser of your own, before you build the bot:

```python
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("--build")
own = parser.parse_known_args()[0]
run_from_command_line(ApiBot(make_api(build=own.build), Race.TERRAN, "MyBot"))
```

An argument of the other kind of game, such as `--Map` given to a ladder game, is logged as ignored.

## Replays and results

`--ReplayFile` saves the game's replay there, however the game ends: at its end, before leaving it when the bot calls
`api.leave_game()`, and after a handler raises. The ladder keeps its own replays, so this is for your own runs.
`--ResultFile` writes the result as JSON, as for [the match workflow](github-actions.md#1-a-game-script), with the
map's display name in a ladder game, where no map file is known.

A ladder game has no time limit. A bot ends one early only by leaving it, with `api.leave_game()`, which concedes it.
