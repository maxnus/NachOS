"""Check the match workflow's inputs and the bot's config, and write the games to play as step outputs.

The opponent's race, difficulty and build are NachOS's member names of `Race`, `Difficulty` and `AIBuild`; this
script runs without NachOS installed, so it lists them.

Run by .github/workflows/match.yml with the inputs in the environment and the config, as JSON, at the path given.
"""

import json
import os
import re
import sys
from pathlib import Path

RACES = ["TERRAN", "ZERG", "PROTOSS"]
DIFFICULTIES = [
    "VERY_EASY",
    "EASY",
    "MEDIUM",
    "MEDIUM_HARD",
    "HARD",
    "HARDER",
    "VERY_HARD",
    "CHEAT_VISION",
    "CHEAT_MONEY",
    "CHEAT_INSANE",
]
BUILDS = ["RANDOM", "RUSH", "TIMING", "POWER", "MACRO", "AIR"]
CONFIG_KEYS = {"run", "install", "maps", "checkouts"}


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def games(count: str, races: str) -> list[dict[str, object]]:
    """The games, numbered from 1, cycling through the races so that a run cut short still has a spread of them."""
    chosen = RACES if races == "all" else races.split(",")
    if not all(race in [*RACES, "RANDOM"] for race in chosen) or len(set(chosen)) != len(chosen):
        fail("opponent-race must be all or a list of RANDOM, TERRAN, ZERG, PROTOSS")
    if not count.isdigit() or not 1 <= int(count) <= 10:
        fail(f"games must be 1 to 10 per race, got '{count}'")
    return [{"n": i * len(chosen) + j + 1, "race": race} for i in range(int(count)) for j, race in enumerate(chosen)]


def check_settings(difficulty: str, build: str, time_limit: str) -> None:
    if difficulty not in DIFFICULTIES:
        fail(f"opponent-difficulty must be one of {', '.join(DIFFICULTIES)}")
    if build not in BUILDS:
        fail(f"opponent-build must be one of {', '.join(BUILDS)}")
    try:
        valid = time_limit == "" or float(time_limit) >= 0
    except ValueError:
        valid = False
    if not valid:
        fail(f"time-limit must be empty or a number of game seconds, got '{time_limit}'")


def read_config(path: Path, name: str) -> dict[str, str]:
    """The config's settings as step outputs: the script, the install command, the maps and the checkouts."""
    config = json.loads(path.read_text(encoding="utf-8")) or {}
    if unknown := set(config) - CONFIG_KEYS:
        fail(f"unknown keys in {name}: {', '.join(sorted(unknown))}")
    if not config.get("run"):
        fail(f"{name} must name the game script in `run`")
    checkouts = config.get("checkouts") or []
    for checkout in checkouts:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", checkout.get("repository", "")):
            fail(f"bad checkout {checkout} in {name}")
        if not re.fullmatch(r"[\w.-]+", checkout.get("path", "")):
            fail(f"checkout path must be one folder: {checkout} in {name}")
    return {
        "run": config["run"],
        "install": config.get("install") or "uv sync",
        "maps": config.get("maps") or "",
        "checkouts": json.dumps(checkouts),
    }


def main() -> None:
    env = os.environ
    check_settings(env["DIFFICULTY"], env["BUILD"], env["TIME_LIMIT"])
    outputs = {"games": json.dumps(games(env["GAMES"], env["RACES"])), **read_config(Path(sys.argv[1]), env["CONFIG"])}
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.writelines(f"{key}={value}\n" for key, value in outputs.items())


if __name__ == "__main__":
    main()
