"""Write the match report from the games' result files: the run summary, and the state, tally and report outputs.

Run by .github/workflows/match.yml with the result files in results/, the replays' artifact ids in replays.txt, and
the run's settings in the environment.
"""

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

LETTERS = {"Victory": "W", "Defeat": "L", "Tie": "T"}
MARKER = "<!-- sc2-games:match -->"


def tally(results: list[str | None]) -> str:
    counts = Counter(LETTERS.get(result) if result else None for result in results)
    crashed = f", {counts[None]} crashed" if counts[None] else ""
    return f"{counts['W']}W {counts['L']}L {counts['T']}T{crashed}"


def length(seconds: object) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if isinstance(seconds, (int, float)) else ""


def race_row(name: str, results: list[str | None], *, crashes: bool) -> str:
    counts = Counter(LETTERS.get(result) if result else None for result in results)
    cells = [name, *(str(counts[letter]) for letter in "WLT")] + ([str(counts[None])] if crashes else [])
    return "| " + " | ".join(cells) + " |"


def main() -> None:
    env = os.environ
    run_url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    lines = Path("replays.txt").read_text(encoding="utf-8").splitlines()
    replays = dict(line.split() for line in lines if line)

    rows: list[str] = []
    results: list[str | None] = []
    by_race: defaultdict[str, list[str | None]] = defaultdict(list)
    for game in json.loads(env["GAMES"]):
        n, race = game["n"], game["race"]
        path = Path("results") / f"result-{n}.json"
        played = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        result = played.get("result")
        results.append(result)
        actual = played.get("opponent_actual_race")
        opponent = f"{race} → {actual}" if race == "Random" and actual and actual != "Random" else race
        by_race[actual if race == "Random" and actual else race].append(result)
        artifact = replays.get(f"replay-{n}")
        replay = f"[replay]({run_url}/artifacts/{artifact})" if artifact else ""
        game_map, length_cell = played.get("map", ""), length(played.get("game_time"))
        rows.append(f"| {n} | {opponent} | {game_map} | {result or 'no result'} | {length_cell} | {replay} |")

    description = tally(results)
    if env["GAME_JOBS"] == "cancelled":
        state, description = "error", f"Cancelled ({description})"
    else:
        state = "failure" if None in results else "success"

    settings = f"{env['DIFFICULTY']}, {env['BUILD']}"
    if env["TIME_LIMIT"]:
        settings += f", time limit {env['TIME_LIMIT']}s"
    report = [
        MARKER,
        f"### sc2 match: {description}",
        "",
        f"`{env['SHA'][:7]}` against {settings} · [run]({run_url})",
        "",
    ]
    if len(by_race) > 1:
        crashes = None in results
        report += [
            "| Opponent | W | L | T |" + (" Crashed |" if crashes else ""),
            "|---|---|---|---|" + ("---|" if crashes else ""),
            *(race_row(race, race_results, crashes=crashes) for race, race_results in by_race.items()),
            race_row("**Total**", results, crashes=crashes),
            "",
        ]
    report += ["| # | Opponent | Map | Result | Length | Replay |", "|---|---|---|---|---|---|", *rows, ""]

    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write("\n".join(report[1:]))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"state={state}\ntally={description}\nreport<<REPORT_END\n" + "\n".join(report) + "\nREPORT_END\n")


if __name__ == "__main__":
    main()
