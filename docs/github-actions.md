# Running StarCraft II on GitHub Actions

NachOS ships two pieces a bot repository can use to play StarCraft II in its own GitHub Actions:

- **`match.yml`**, a reusable workflow that plays your bot against the built-in AI and reports how it went: a few
  lines of YAML in your repository, games on GitHub's runners or on your own machines.
- **`setup-sc2`**, the action under it, which provides the Linux StarCraft II 4.10 client (build 75689, the one the
  ladder plays) and your maps. Use it directly for anything else that needs the game, such as tests or sweeps.

## Playing matches

Three files in your bot repository.

### 1. A game script

For a NachOS bot, the script builds the bot and hands it to `run_from_command_line`, which does the rest:

```python
# scripts/run_game.py
from sc2nachos import ApiBot, Race, run_from_command_line

from my_bot.api import api

if __name__ == "__main__":
    run_from_command_line(ApiBot(api, Race.TERRAN, "MyBot"))
```

[`examples/run_game.py`](../examples/run_game.py) is the same, with a bot that does nothing.

What the workflow asks of the script, for a bot not built on NachOS or a script of your own: it runs
`uv run python <script>` with these arguments, any of which may be left out, the values being NachOS's member names
of `Race`, `Difficulty` and `AIBuild`.

| Argument | Values |
|---|---|
| `--opponent-race` | `RANDOM`, `TERRAN`, `ZERG`, `PROTOSS` |
| `--opponent-difficulty` | `VERY_EASY`, `EASY`, `MEDIUM`, `MEDIUM_HARD`, `HARD`, `HARDER`, `VERY_HARD`, `CHEAT_VISION`, `CHEAT_MONEY`, `CHEAT_INSANE` |
| `--opponent-build` | `RANDOM`, `RUSH`, `TIMING`, `POWER`, `MACRO`, `AIR` |
| `--map` | a map name; when absent, the script picks one |
| `--time-limit` | game seconds after which the game ends as a tie |
| `--result-file` | where to write the result |
| `--replay-file` | where to save the replay, if the script can |

The result file is JSON. `result` and `map` are required; the rest are optional:

```json
{"result": "VICTORY", "map": "PylonAIE_v4", "game_time": 754.384, "opponent_actual_race": "ZERG"}
```

`result` is `VICTORY`, `DEFEAT` or `TIE`, in any case, and `game_time` is in game seconds. `opponent_actual_race` is
what a `RANDOM` opponent turned out to be. A game that writes no result file counts as crashed.

### 2. `.github/sc2-games.yml`

```yaml
run: scripts/run_game.py      # the game script, relative to the repository root
install: uv sync              # optional, the default
maps: sc2maps/AIE             # optional, a folder of .SC2Map files in your repository
checkouts:                    # optional, repositories placed next to yours
  - repository: you/your-library
    path: your-library
```

`maps` is copied into the client's `maps/<folder name>`, `maps/AIE` here, where `MapFile.find` and python-sc2 find a
map by its name alone. NachOS's own [`sc2maps/AIE`](../sc2maps/AIE) holds the AIE ladder pool, if you want a
copy.

### 3. A workflow

```yaml
# .github/workflows/sc2.yml
name: sc2
on:
  workflow_dispatch:
  pull_request:

jobs:
  match:
    permissions:
      contents: read
      actions: read            # for the replay links in the report
    uses: maxnus/NachOS/.github/workflows/match.yml@main
    with:
      games: 2                 # per opponent race
      opponent-race: all
      opponent-difficulty: HARD
```

Each run plays the games, two per race here, all at once on GitHub-hosted runners. Its summary page shows the tally, a
table per race and one per game, each game linking its replay.

`@main` follows NachOS as it changes. To stay on a version you have checked, pin a commit instead, and pass the same
commit as `nachos-ref`, which the workflow takes its own scripts from: GitHub does not tell a called workflow the ref
it was called at.

```yaml
    uses: maxnus/NachOS/.github/workflows/match.yml@<sha>
    with:
      nachos-ref: <sha>
```

### Inputs

| Input | Default | |
|---|---|---|
| `repo` | the calling repository | the bot to play, `owner/name` |
| `ref` | the commit the workflow runs on, or another `repo`'s default branch | branch, tag or commit to play |
| `config` | `.github/sc2-games.yml` | where the config file is |
| `games` | `1` | games per opponent race, 1 to 10 |
| `opponent-race` | `all` | `all`, or a comma-separated list of `RANDOM`, `TERRAN`, `ZERG`, `PROTOSS` |
| `opponent-difficulty` | `VERY_HARD` | as the game script takes it |
| `opponent-build` | `RANDOM` | as the game script takes it |
| `map` | empty | one map for every game; empty leaves it to the script |
| `time-limit` | empty | game seconds before a game ends as a tie, `0` at once; empty plays it out |
| `runner` | `"ubuntu-latest"` | where games run, as JSON for `runs-on`; see below |
| `max-parallel` | `15` | games at once |
| `nachos-ref` | `main` | the NachOS ref the workflow's scripts come from; the one in `uses:` |

The secret `token` is needed only when the bot repository or a `checkouts` repository is private and is not the
calling repository. It needs Contents read on each.

### Outputs

| Output | |
|---|---|
| `sha` | the commit played |
| `tally` | e.g. `5W 3L 1T`, with `, 1 crashed` when a game wrote no result |
| `state` | `success`, `failure` when a game wrote no result (losses and ties are not failures), `error` when the games were cancelled |
| `report` | the report in Markdown |

To post the report on the pull request, add a job after the match:

```yaml
  comment:
    needs: match
    if: github.event_name == 'pull_request' && needs.match.outputs.report != ''
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
          REPORT: ${{ needs.match.outputs.report }}
        run: gh pr comment ${{ github.event.pull_request.number }} --repo ${{ github.repository }} --body "$REPORT"
```

## Running on your own machines

`runner` takes any `runs-on` value, so games can run on a self-hosted runner you registered on the bot repository:

```yaml
    with:
      runner: '["self-hosted", "sc2"]'
      max-parallel: 1            # one game at a time, if the machine plays one at a time
```

On a self-hosted runner, `setup-sc2` uses the client already installed there and never downloads one. Install the
[Linux 4.10 client](https://github.com/Blizzard/s2client-proto#downloads) on the machine, and set `SC2PATH` to it
unless it is at `~/StarCraftII`. Your maps are still
copied in for each game.

Keep self-hosted runners off public repositories: a pull request from a fork could run its code on your machine.

A runner registered on a repository serves only the workflows that run as that repository; an organization can
share one among its repositories, a personal account cannot. To play several bots on one machine without a runner
per bot, keep one private repository that owns the runner and calls `match.yml` with `repo:` set to each bot, passing
a `token` that reads them. maxnus/sc2-games is set up this way.

## Anything else that needs the game

`setup-sc2` works in any job, for tests or tools that start the game themselves:

```yaml
jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maxnus/NachOS/.github/actions/setup-sc2@main
        with:
          maps: sc2maps/AIE        # optional, one folder per line
      - uses: astral-sh/setup-uv@v5
      - run: uv sync && uv run python tools/my_tool.py
```

It sets `SC2PATH`, which NachOS's `Installation.find` and python-sc2 both read. Its outputs are `path`, `build` (e.g.
`Base75689`) and `source` (`installed`, `cache` or `download`). NachOS's own [`sc2.yml`](../.github/workflows/sc2.yml)
runs its sweep tools this way.

## Limits

- **The client**: the first run downloads it from Blizzard and keeps it in the repository's Actions cache, which
  later runs restore it from. It takes 4.1 GB of the 10 GB cache a repository gets. A cache entry unused for 7 days is
  dropped, and the next run downloads it again. The match workflow downloads it once before its games start, not once
  per game.
- **Cache scope**: a cache saved on a pull request serves only that pull request. Every branch can use one saved on
  the default branch, so a run there fills it for all.
- **Games**: at most 10 per opponent race, each with an hour before it is cut off.
- **Licence**: the client's download is protected by a password that accepts Blizzard's AI and Machine Learning
  License, and setup-sc2 enters it for you; using the action is accepting the licence. Download the client from
  Blizzard as the action does, rather than publishing a copy of it.
