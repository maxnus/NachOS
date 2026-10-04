"""The game script for NachOS's match workflow, with a bot that does nothing.

    uv run python examples/run_game.py --opponent-race ZERG --opponent-difficulty VERY_EASY --time-limit 120 \\
        --result-file result.json

A bot repository's script is the same, with its own `Api` and race; see docs/github-actions.md.
"""

from sc2nachos import Api, ApiBot, Race, run_from_command_line

api = Api()

if __name__ == "__main__":
    run_from_command_line(ApiBot(api, Race.TERRAN, "NachOS"))
