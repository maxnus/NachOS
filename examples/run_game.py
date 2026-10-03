"""Play one game against the built-in AI and write how it went, as the match workflow runs a bot.

    uv run python examples/run_game.py --opponent-race Zerg --opponent-difficulty VeryEasy --time-limit 120 \\
        --result-file result.json

The arguments are the ones .github/workflows/match.yml passes, in the game's own spelling, which python-sc2 shares;
the result file is the JSON the workflow reads. See docs/github-actions.md. The bot here does nothing: a bot
repository copies this script and plays its own `Api`.
"""

import json
import random
from argparse import ArgumentParser
from pathlib import Path

from s2clientprotocol import common_pb2, sc2api_pb2

from sc2nachos import Api
from sc2nachos.launch import Installation, MapFile
from sc2nachos.match import AIBuild, Computer, Difficulty, Race
from sc2nachos.run import ApiBot, run_local


def main() -> None:
    parser = ArgumentParser(description="Play one game against the built-in AI and write how it went.")
    races = [common_pb2.Race.Name(common_pb2.Race.ValueType(race)) for race in Race if race is not Race.NONE]
    parser.add_argument("--opponent-race", choices=races, default="Random")
    parser.add_argument("--opponent-difficulty", choices=sc2api_pb2.Difficulty.keys(), default="VeryHard")
    parser.add_argument("--opponent-build", choices=sc2api_pb2.AIBuild.keys(), default="RandomBuild")
    parser.add_argument("--map", help="a map under the client's maps folder (default: one at random)")
    parser.add_argument("--time-limit", type=float, help="end the game as a tie after this many game seconds")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--replay-file", type=Path, help="accepted for the match workflow; no replay is saved yet")
    args = parser.parse_args()

    installation = Installation.find()
    name = args.map or random.choice(sorted({path.stem for path in installation.maps.rglob("*.SC2Map")}))
    map_file = MapFile.find(name, installation=installation)
    opponent = Computer(
        Race(common_pb2.Race.Value(args.opponent_race)),
        Difficulty(sc2api_pb2.Difficulty.Value(args.opponent_difficulty)),
        AIBuild(sc2api_pb2.AIBuild.Value(args.opponent_build)),
    )

    api = Api()
    result = run_local(map_file, ApiBot(api, Race.TERRAN, "NachOS"), opponent, time_limit=args.time_limit)

    if args.result_file is not None:
        outcome = {
            "result": sc2api_pb2.Result.Name(sc2api_pb2.Result.ValueType(result)),
            "map": map_file.name,
            "game_time": round(api.time, 3),
        }
        args.result_file.write_text(json.dumps(outcome))


if __name__ == "__main__":
    main()
