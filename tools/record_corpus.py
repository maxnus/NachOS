"""Record the games the tests replay: a bare api against the computer, on maps from the current ladder pool.

The bare api gives no orders, so the only orders in the corpus are the game's own, nearly all of them workers mining,
and since nothing leaves its base the corpus shows the computer's army but none of its buildings.

Needs StarCraft II installed, with this repository's `sc2maps/AIE` the copies it finds of the pool's maps: it refuses
to record on any other copy, since a map file carries data of its own. Records every game, or only those named, into
`tests/corpus`, replacing any recording of the same name::

    uv run python tools/record_corpus.py
    uv run python tools/record_corpus.py PylonAIE_v4-TvZ
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from sc2nachos import Api, run_local
from sc2nachos.launch import MapFile
from sc2nachos.match import AIBuild, Computer, Difficulty, Race

CORPUS = Path(__file__).parents[1] / "tests" / "corpus"
REPO_MAPS = Path(__file__).parents[1] / "sc2maps" / "AIE"
STEPS_PER_TURN = 16

_LETTERS = {Race.TERRAN: "T", Race.ZERG: "Z", Race.PROTOSS: "P", Race.RANDOM: "R"}


@dataclass(frozen=True, slots=True)
class CorpusGame:
    """One corpus game: its map, race, opponent and seed."""

    map: str
    race: Race
    opponent: Computer
    seed: int

    @property
    def name(self) -> str:
        """The map and the matchup, as in `PylonAIE_v4-TvZ`."""
        return f"{self.map}-{_LETTERS[self.race]}v{_LETTERS[self.opponent.race]}"

    @property
    def path(self) -> Path:
        """The recording's path."""
        return CORPUS / f"{self.name}.sc2rec"


GAMES = (
    CorpusGame("PylonAIE_v4", Race.TERRAN, Computer(Race.ZERG, Difficulty.VERY_HARD, AIBuild.POWER), seed=1),
    CorpusGame("TorchesAIE_v4", Race.TERRAN, Computer(Race.PROTOSS, Difficulty.VERY_HARD, AIBuild.AIR), seed=2),
    CorpusGame("MagannathaAIE_v2", Race.TERRAN, Computer(Race.TERRAN, Difficulty.VERY_HARD, AIBuild.MACRO), seed=3),
    CorpusGame("LeyLinesAIE_v3", Race.ZERG, Computer(Race.PROTOSS, Difficulty.VERY_HARD, AIBuild.TIMING), seed=4),
    CorpusGame("PersephoneAIE_v4", Race.PROTOSS, Computer(Race.TERRAN, Difficulty.VERY_HARD, AIBuild.POWER), seed=5),
    CorpusGame("UltraloveAIE_v2", Race.ZERG, Computer(Race.TERRAN, Difficulty.VERY_HARD, AIBuild.RUSH), seed=6),
    CorpusGame("IncorporealAIE_v4", Race.PROTOSS, Computer(Race.ZERG, Difficulty.VERY_HARD, AIBuild.MACRO), seed=7),
)


def record(game: CorpusGame) -> None:
    """Play `game` with an api that does nothing, and record it."""
    map_file = MapFile.find(game.map)
    if map_file.path.resolve() != (REPO_MAPS / f"{game.map}.SC2Map").resolve():
        raise SystemExit(f"{game.map} is found at {map_file.path}, which is not this repository's copy in {REPO_MAPS}")
    api = Api()
    result = run_local(
        map_file,
        api,
        game.opponent,
        race=game.race,
        name="NachOS",
        steps_per_turn=STEPS_PER_TURN,
        random_seed=game.seed,
        record_to=game.path,
        window=(640, 480),
    )
    logger.info("{} ended in a {} after {:.0f} seconds", game.name, result, api.time.seconds)


def main(names: Sequence[str]) -> None:
    """Record the games named, or every game when none are."""
    unknown = set(names) - {game.name for game in GAMES}
    if unknown:
        raise SystemExit(f"No corpus game is called {', '.join(sorted(unknown))}")
    CORPUS.mkdir(exist_ok=True)
    for game in GAMES:
        if not names or game.name in names:
            record(game)


if __name__ == "__main__":
    main(sys.argv[1:])
