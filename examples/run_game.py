"""The game script for NachOS's match workflow, with a bot that does nothing.

    uv run python examples/run_game.py --ComputerRace ZERG --ComputerDifficulty VERY_EASY --TimeLimit 120 \\
        --ResultFile result.json

A bot repository's script is the same, with its own `Api` and race; see docs/github-actions.md.
"""

from sc2nachos import Api, Race, run_from_command_line

api = Api()

if __name__ == "__main__":
    run_from_command_line(api, race=Race.TERRAN, name="NachOS")
