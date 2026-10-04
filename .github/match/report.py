"""Write the match report from the games' result files: the run summary, and the state, tally and report outputs.

Run by .github/workflows/match.yml with the result files in results/, the replays' artifact ids in replays.txt, and
the run's settings in the environment.
"""

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

LETTERS = {"VICTORY": "W", "DEFEAT": "L", "TIE": "T"}
MARKER = "<!-- sc2-games:match -->"


@dataclass(frozen=True, slots=True)
class Game:
    """One game the run planned, and what its result file says, if it wrote one."""

    n: int
    race: str
    played: dict[str, object]

    @property
    def result(self) -> str | None:
        """VICTORY, DEFEAT or TIE: a game script may write them in any case."""
        result = self.played.get("result")
        return result.upper() if isinstance(result, str) else None

    @property
    def actual_race(self) -> str:
        """The race played against: the one a RANDOM opponent turned out to be, where the result file says."""
        actual = self.played.get("opponent_actual_race")
        if self.race == "RANDOM" and isinstance(actual, str) and actual.upper() != "RANDOM":
            return actual.upper()
        return self.race


def read_games(planned: list[dict[str, object]], results: Path) -> list[Game]:
    games = []
    for game in planned:
        path = results / f"result-{game['n']}.json"
        played = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        games.append(Game(int(str(game["n"])), str(game["race"]), played))
    return games


def tally(results: list[str | None]) -> str:
    counts = Counter(LETTERS.get(result) if result else None for result in results)
    crashed = f", {counts[None]} crashed" if counts[None] else ""
    return f"{counts['W']}W {counts['L']}L {counts['T']}T{crashed}"


def race_table(games: list[Game]) -> list[str]:
    """W-L-T per opponent race and in total, with a column for crashes when there were any. Empty for one race."""
    by_race: defaultdict[str, list[str | None]] = defaultdict(list)
    for game in games:
        by_race[game.actual_race].append(game.result)
    if len(by_race) < 2:
        return []
    crashes = any(game.result is None for game in games)

    def row(name: str, results: list[str | None]) -> str:
        counts = Counter(LETTERS.get(result) if result else None for result in results)
        cells = [name, *(str(counts[letter]) for letter in "WLT")] + ([str(counts[None])] if crashes else [])
        return "| " + " | ".join(cells) + " |"

    return [
        "| Opponent | W | L | T |" + (" Crashed |" if crashes else ""),
        "|---|---|---|---|" + ("---|" if crashes else ""),
        *(row(race.title(), results) for race, results in by_race.items()),
        row("**Total**", [game.result for game in games]),
        "",
    ]


def game_table(games: list[Game], replays: dict[str, str], run_url: str) -> list[str]:
    """One row per game, with its map, result, length and replay link."""
    rows = ["| # | Opponent | Map | Result | Length | Replay |", "|---|---|---|---|---|---|"]
    for game in games:
        opponent = game.race.title()
        if game.actual_race != game.race:
            opponent += f" → {game.actual_race.title()}"
        seconds = game.played.get("game_time")
        length = f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if isinstance(seconds, (int, float)) else ""
        artifact = replays.get(f"replay-{game.n}")
        replay = f"[replay]({run_url}/artifacts/{artifact})" if artifact else ""
        result = game.result.title() if game.result else "no result"
        rows.append(f"| {game.n} | {opponent} | {game.played.get('map', '')} | {result} | {length} | {replay} |")
    return [*rows, ""]


def main() -> None:
    env = os.environ
    run_url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    lines = Path("replays.txt").read_text(encoding="utf-8").splitlines()
    replays = dict(line.split() for line in lines if line)
    games = read_games(json.loads(env["GAMES"]), Path("results"))

    results = [game.result for game in games]
    description = tally(results)
    if env["GAME_JOBS"] == "cancelled":
        state, description = "error", f"Cancelled ({description})"
    else:
        state = "failure" if None in results else "success"

    settings = f"{env['DIFFICULTY']}, {env['BUILD']}"
    if env["TIME_LIMIT"]:
        settings += f", time limit {env['TIME_LIMIT']}s"
    heading = [f"### sc2 match: {description}", "", f"`{env['SHA'][:7]}` against {settings} · [run]({run_url})", ""]
    report = [MARKER, *heading, *race_table(games), *game_table(games, replays, run_url)]

    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write("\n".join(report[1:]))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"state={state}\ntally={description}\nreport<<REPORT_END\n" + "\n".join(report) + "\nREPORT_END\n")


if __name__ == "__main__":
    main()
