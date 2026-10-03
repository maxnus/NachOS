"""Play one game against the built-in AI and write how it went, as the match workflow runs a bot.

    uv run python tools/run_game.py --opponent-race Zerg --opponent-difficulty VeryEasy --time-limit 120 \\
        --result-file result.json

The arguments are the ones .github/workflows/match.yml passes, and the result file is the JSON it reads; see
docs/github-actions.md. The bot here does nothing: a bot repository copies this script and plays its own `Api`.
"""

import json
import random
import re
from argparse import ArgumentParser
from pathlib import Path

from sc2nachos import Api
from sc2nachos.launch import Installation, MapFile
from sc2nachos.match import AIBuild, Computer, Difficulty, Race, Result
from sc2nachos.run import ApiBot, run_local

_RESULTS = {Result.VICTORY: "Victory", Result.DEFEAT: "Defeat", Result.TIE: "Tie"}


def _member[E: (Race, Difficulty, AIBuild)](enum: type[E], name: str) -> E:
    """The member the game's own spelling names: `VeryEasy` is `VERY_EASY`, and `RandomBuild` is `RANDOM`."""
    key = re.sub(r"(?<!^)(?=[A-Z])", "_", name.removesuffix("Build") or name).upper()
    return enum[key]


def main() -> None:
    parser = ArgumentParser(description="Play one game against the built-in AI and write how it went.")
    parser.add_argument("--opponent-race", default="Random")
    parser.add_argument("--opponent-difficulty", default="VeryHard")
    parser.add_argument("--opponent-build", default="RandomBuild")
    parser.add_argument("--map", help="a map under the client's maps folder (default: one at random)")
    parser.add_argument("--time-limit", type=float, help="end the game as a tie after this many game seconds")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--replay-file", type=Path, help="accepted for the match workflow; no replay is saved yet")
    args = parser.parse_args()

    installation = Installation.find()
    name = args.map or random.choice(sorted({path.stem for path in installation.maps.rglob("*.SC2Map")}))
    map_file = MapFile.find(name, installation=installation)
    opponent = Computer(
        _member(Race, args.opponent_race),
        _member(Difficulty, args.opponent_difficulty),
        _member(AIBuild, args.opponent_build),
    )

    api = Api()
    result = run_local(map_file, ApiBot(api, Race.TERRAN, "NachOS"), opponent, time_limit=args.time_limit)

    if args.result_file is not None:
        args.result_file.write_text(
            json.dumps(
                {"result": _RESULTS.get(result, result.name), "map": map_file.name, "game_time": round(api.time)}
            )
        )


if __name__ == "__main__":
    main()
