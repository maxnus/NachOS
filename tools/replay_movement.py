"""Play replays back as each of their players and record how the enemy's units moved: every window of a unit of a type
a speed upgrade changes, as upgrade inference is to read it, and the step each of the player's own upgrades finished,
which is the truth for the other player's playback.

Needs StarCraft II installed, with the replays' build and maps.

- `play` takes the first `--games` replays of a folder of them in an order shuffled by `--seed`, so a larger sample
  holds a smaller one of the same seed, and plays each back as player 1 and as player 2, at every step with the fog
  on, `--clients` at once. Each playback writes `<out>/<replay>-<player id>.json.gz`: the races, the map, how it ended
  or failed, how long it took, the step each of the player's upgrades finished, and each window of 11 steps in which
  an enemy unit moved. A playback already written is not played again, so a run that stopped picks up where it left
  off; with `--retry-failed`, one that failed is.
- `summarize` reads them back, one at a time. It pairs each window with whether the enemy had the upgrade throughout
  it, from the other player's playback, joins windows of one unit into longer ones on one clock, and counts the fast
  and slow votes each window length and band would give against the truth, plainly and steadily, a steady vote asking
  every observation's move to fall in the band too; then, for the proposed window and band, the votes by upgrade, and
  the playbacks in which a wrong assumption was possible, an enemy lacking an upgrade while its units of a type it
  speeds up voted. Last, how long a playback took.

Run it as::

    uv run python tools/replay_movement.py play /mnt/storage/data/sc2ai/aiarena-2026-season-1 --games 100
    uv run python tools/replay_movement.py summarize replay-movement

The summary is written as JSON.
"""

import argparse
import gzip
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sc2nachos import Api, run_replay
from sc2nachos.events import TurnStartEvent
from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindow, MovementWindows

DEFAULT_OUT_DIRECTORY = Path("replay-movement")
DEFAULT_SUMMARY_PATH = Path("replay-movement.json")
_WINDOW_STEPS = 11
_PLAYER_IDS = (1, 2)
# What the summary counts votes over: windows of one, two and four of the recorded ones, and bands of these widths
# around 0 and 1, as shares of the gap between the two predictions.
_WINDOWS_JOINED = (1, 2, 4)
_BAND_WIDTHS = (0.05, 0.1, 0.2)
# The window and band the plan proposes, for the summary's counts by upgrade and by playback.
_PROPOSED_WINDOWS_JOINED = 2
_PROPOSED_BAND_WIDTH = 0.05
# A window does not vote where the prediction with the upgrade is less than this much further than the one without.
_LEAST_GAP = 0.1
_VOTE_RULES = {"plain": False, "steady": True}

# A playback, by its replay's name without the extension and the player it was played as.
type _PlaybackKey = tuple[str, int]


class _Recorder:
    """What one playback records: each window an enemy unit moved in, and when each of the player's upgrades
    finished."""

    def __init__(self, api: Api[Any]) -> None:
        self._api = api
        self.player_race = api.player.race.name
        enemy_race = api.enemy.race
        self.enemy_race = None if enemy_race is None else enemy_race.name
        self.map_name = api.map.name
        self.windows: list[MovementWindow] = []
        self.upgrade_steps: dict[str, int] = {}
        self.last_step = 0
        MovementWindows(api, window_steps=_WINDOW_STEPS, on_window=self._keep_if_moved)
        api.on(TurnStartEvent)(self._note_the_turn)

    def _keep_if_moved(self, window: MovementWindow) -> None:
        if window.distance > 0:
            self.windows.append(window)

    def _note_the_turn(self, *_: object) -> None:
        api = self._api
        self.last_step = api.step
        upgrades = api.upgrades
        if len(upgrades) != len(self.upgrade_steps):
            for upgrade in upgrades:
                self.upgrade_steps.setdefault(upgrade.name, api.step)

    def as_json(self) -> dict[str, Any]:
        windows = self.windows
        return {
            "player_race": self.player_race,
            "enemy_race": self.enemy_race,
            "map": self.map_name,
            "last_step": self.last_step,
            "upgrades": self.upgrade_steps,
            "window_steps": _WINDOW_STEPS,
            "windows": {
                "unit_id": [window.unit_id for window in windows],
                "type": [window.type_id.name for window in windows],
                "upgrade": [window.upgrade.name for window in windows],
                "start": [window.start_step for window in windows],
                "end": [window.end_step for window in windows],
                "distance": [round(window.distance, 5) for window in windows],
                "without": [round(window.without_upgrade, 5) for window in windows],
                "with": [round(window.with_upgrade, 5) for window in windows],
                "least_step_share": [round(window.least_step_share, 4) for window in windows],
                "greatest_step_share": [round(window.greatest_step_share, 4) for window in windows],
                "creep_steps": [window.steps_on_creep for window in windows],
                "hallucination": [window.is_hallucination for window in windows],
                "buffs": {
                    str(index): sorted(buff.name for buff in window.buffs_worn)
                    for index, window in enumerate(windows)
                    if window.buffs_worn
                },
            },
        }


def _playback_path(out_directory: Path, replay: Path, player_id: int) -> Path:
    return out_directory / f"{replay.stem}-{player_id}.json.gz"


def _key_of_playback(path: Path) -> _PlaybackKey:
    """The replay and the player of the playback recorded at `path`, as `_playback_path` names it."""
    replay, player_part = path.name.rsplit("-", 1)
    return replay, int(player_part.split(".")[0])


def play_back(replay: Path, player_id: int, out_file: Path) -> str:
    """Play `replay` back as `player_id`, write what it recorded to `out_file`, and say how it went."""
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    recorders: list[_Recorder] = []

    def build_the_recorder(api: Api[Any]) -> _Recorder:
        recorder = _Recorder(api)
        recorders.append(recorder)
        return recorder

    api: Api[Any] = Api(extensions=build_the_recorder)
    started = time.perf_counter()
    failure = None
    result = None
    try:
        result = run_replay(replay, api, player_id=player_id).name
    except Exception as error:
        # Recorded rather than raised, so that one replay the game cannot play stops none of the others.
        failure = repr(error)
    played = {
        "replay": replay.name,
        "player_id": player_id,
        "result": result,
        "failure": failure,
        "seconds": round(time.perf_counter() - started, 2),
        **(recorders[0].as_json() if recorders else {}),
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    unfinished = out_file.with_suffix(".tmp")
    with gzip.open(unfinished, "wt", encoding="utf-8") as file:
        json.dump(played, file)
    unfinished.replace(out_file)
    return f"{replay.name} as player {player_id}: {failure or result}, {played['seconds']} s"


def _read_playback(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        return json.load(file)


def _is_to_play(out_file: Path, *, retry_failed: bool) -> bool:
    """Whether the playback to be recorded at `out_file` is to be played: not yet recorded, or recorded failing and
    `retry_failed`."""
    if not out_file.exists():
        return True
    return retry_failed and _read_playback(out_file)["failure"] is not None


def play_sample(
    replay_directory: Path, *, games: int, seed: int, clients: int, out_directory: Path, retry_failed: bool
) -> None:
    """Play the first `games` replays of `replay_directory`, in an order shuffled by `seed`, back as each player,
    `clients` at once, and write each playback's record to `out_directory`."""
    replays = sorted(replay_directory.glob("*.SC2Replay"))
    random.Random(seed).shuffle(replays)
    playbacks = [
        (replay, player_id, _playback_path(out_directory, replay, player_id))
        for replay in replays[:games]
        for player_id in _PLAYER_IDS
    ]
    left = [playback for playback in playbacks if _is_to_play(playback[2], retry_failed=retry_failed)]
    logger.info("{} of {} playbacks to play, {} at once", len(left), len(playbacks), clients)
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=clients) as pool:
        running = {pool.submit(play_back, *playback): playback for playback in left}
        for done, finished in enumerate(as_completed(running), start=1):
            replay, player_id, _ = running[finished]
            try:
                outcome = finished.result()
            except Exception as error:
                # A worker that died writes nothing, so the playback is played again when the run is.
                outcome = f"{replay.name} as player {player_id}: its worker failed, {error!r}"
            logger.info("{}/{} after {:.0f} s: {}", done, len(left), time.perf_counter() - started, outcome)


@dataclass(frozen=True, slots=True)
class _Window:
    """One or more recorded windows of one unit joined, with whether the enemy had its upgrade throughout."""

    upgrade: str
    distance: float
    without_upgrade: float
    with_upgrade: float
    least_step_share: float
    greatest_step_share: float
    steps_on_creep: int
    steps: int
    is_hallucination: bool
    buffs: frozenset[str]
    enemy_had_it: bool | None

    @property
    def gap(self) -> float:
        """How much further the prediction with the upgrade is than the one without, as a share of the one without."""
        return (self.with_upgrade - self.without_upgrade) / self.without_upgrade

    @property
    def upgraded_share(self) -> float:
        return (self.distance - self.without_upgrade) / (self.with_upgrade - self.without_upgrade)

    def vote(self, band_width: float, *, steady: bool) -> str | None:
        """The window's vote: `"fast"` within `band_width` of 1, `"slow"` within it of 0, and `None` otherwise or where
        the gap is too small. A `steady` vote needs every single observation's move within the band too."""
        if self.gap < _LEAST_GAP:
            return None
        for vote, share in (("fast", 1.0), ("slow", 0.0)):
            if abs(self.upgraded_share - share) > band_width:
                continue
            if steady and (self.least_step_share < share - band_width or self.greatest_step_share > share + band_width):
                return None
            return vote
        return None


def _enemy_had_it(enemy: dict[str, Any] | None, upgrade: str, start: int, end: int) -> bool | None:
    """Whether the enemy, as its own playback `enemy` recorded it, had `upgrade` from `start` to `end`: `None` where
    it finished between them, or its playback is missing or stopped before `end`."""
    if enemy is None or (enemy["failure"] is not None and enemy.get("last_step", 0) < end):
        return None
    finished = enemy["upgrades"].get(upgrade)
    if finished is not None and finished <= start:
        return True
    if finished is None or finished > end:
        return False
    return None


def _joined_windows(
    playback: dict[str, Any], enemy: dict[str, Any] | None, windows_joined: int
) -> Iterator[tuple[int, _Window]]:
    """Each run of `windows_joined` full recorded windows of one unit in `playback`, one after the other, the first
    beginning on a step the joined window's length divides, with its unit's id."""
    columns = playback["windows"]
    window_steps = playback["window_steps"]
    joined_steps = window_steps * windows_joined
    buffs = {int(index): frozenset(names) for index, names in columns["buffs"].items()}
    by_unit: defaultdict[int, list[int]] = defaultdict(list)
    for index, unit_id in enumerate(columns["unit_id"]):
        if columns["end"][index] - columns["start"][index] == window_steps:
            by_unit[unit_id].append(index)
    for unit_id, indices in by_unit.items():
        by_start = {columns["start"][index]: index for index in indices}
        for index in indices:
            start = columns["start"][index]
            if start % joined_steps != 0:
                continue
            members: list[int] = []
            for offset in range(windows_joined):
                if (member := by_start.get(start + window_steps * offset)) is None:
                    break
                members.append(member)
            else:
                yield (
                    unit_id,
                    _Window(
                        upgrade=columns["upgrade"][index],
                        distance=sum(columns["distance"][member] for member in members),
                        without_upgrade=sum(columns["without"][member] for member in members),
                        with_upgrade=sum(columns["with"][member] for member in members),
                        least_step_share=min(columns["least_step_share"][member] for member in members),
                        greatest_step_share=max(columns["greatest_step_share"][member] for member in members),
                        steps_on_creep=sum(columns["creep_steps"][member] for member in members),
                        steps=joined_steps,
                        is_hallucination=any(columns["hallucination"][member] for member in members),
                        buffs=frozenset().union(*(buffs.get(member, frozenset()) for member in members)),
                        enemy_had_it=_enemy_had_it(enemy, columns["upgrade"][index], start, end=start + joined_steps),
                    ),
                )


def _count_votes(counts: Counter[str], window: _Window, band_width: float) -> None:
    """Add `window`, and its plain and steady votes, to `counts`, by whether the enemy had the upgrade; nothing for a
    window whose truth is not known."""
    if window.enemy_had_it is None:
        return
    had = "had" if window.enemy_had_it else "lacked"
    counts[f"windows, enemy {had} it"] += 1
    for rule, steady in _VOTE_RULES.items():
        if (vote := window.vote(band_width, steady=steady)) is not None:
            counts[f"{rule} {vote}, enemy {had} it"] += 1


def _median_and_tail(values: Sequence[float]) -> dict[str, float]:
    if len(values) < 2:
        return {"count": len(values)}
    deciles = statistics.quantiles(values, n=10)
    return {
        "count": len(values),
        "median": round(statistics.median(values), 2),
        "90%": round(deciles[8], 2),
        "max": round(max(values), 2),
    }


@dataclass(slots=True)
class _WrongAssumptionsPossible:
    """For one vote rule, the votes cast in each playback for each upgrade the enemy lacked, and the units casting a
    fast one."""

    votes: defaultdict[tuple[_PlaybackKey, str], Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    fast_units: defaultdict[tuple[_PlaybackKey, str], set[int]] = field(default_factory=lambda: defaultdict(set))

    def count_the_vote(self, playback: _PlaybackKey, unit_id: int, window: _Window, *, steady: bool) -> None:
        if window.enemy_had_it is not False or (vote := window.vote(_PROPOSED_BAND_WIDTH, steady=steady)) is None:
            return
        self.votes[(playback, window.upgrade)][vote] += 1
        if vote == "fast":
            self.fast_units[(playback, window.upgrade)].add(unit_id)

    def as_json(self) -> dict[str, Any]:
        with_fast_votes = {
            f"{replay} as {player_id}, {upgrade}": {
                **counts,
                "units voting fast": len(self.fast_units[(replay, player_id), upgrade]),
            }
            for ((replay, player_id), upgrade), counts in sorted(self.votes.items())
            if counts["fast"]
        }
        return {
            "playbacks and upgrades": len(self.votes),
            "playbacks": len({playback for playback, _ in self.votes}),
            "by upgrade": dict(Counter(upgrade for _, upgrade in self.votes).most_common()),
            "those with a fast vote": with_fast_votes,
        }


def summarize_playbacks(out_directory: Path) -> dict[str, Any]:
    """What the playbacks in `out_directory` show, as the module docstring says, holding one playback's windows at a
    time."""
    paths = sorted(out_directory.glob("*.json.gz"))
    # Everything but the windows, which the truth for the other player's playback needs.
    headers: dict[_PlaybackKey, dict[str, Any]] = {}
    for path in paths:
        header = _read_playback(path)
        header.pop("windows", None)
        headers[_key_of_playback(path)] = header
    summary: dict[str, Any] = {
        "playbacks": len(headers),
        "failed": dict(
            Counter(header["failure"].split("(")[0] for header in headers.values() if header["failure"] is not None)
        ),
        "seconds a playback": _median_and_tail([header["seconds"] for header in headers.values()]),
        "steps a second": _median_and_tail(
            [
                header["last_step"] / header["seconds"]
                for header in headers.values()
                if header["failure"] is None and header.get("last_step")
            ]
        ),
        "games by the races, player against enemy": dict(
            Counter(
                f"{header.get('player_race')} v {header.get('enemy_race')}" for header in headers.values()
            ).most_common()
        ),
    }
    recorded_windows = 0
    votes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    votes_by_upgrade: defaultdict[str, Counter[str]] = defaultdict(Counter)
    possible = {rule: _WrongAssumptionsPossible() for rule in _VOTE_RULES}
    hallucinations_voting: Counter[str] = Counter()
    partly_on_creep_voting: Counter[str] = Counter()
    buffs_voting: Counter[str] = Counter()
    for path in paths:
        playback = _read_playback(path)
        if "windows" not in playback:
            continue
        recorded_windows += len(playback["windows"]["start"])
        replay, player_id = key = _key_of_playback(path)
        enemy = headers.get((replay, 3 - player_id))
        for windows_joined in _WINDOWS_JOINED:
            proposed = windows_joined == _PROPOSED_WINDOWS_JOINED
            for unit_id, window in _joined_windows(playback, enemy, windows_joined):
                for band_width in _BAND_WIDTHS:
                    _count_votes(
                        votes[f"{windows_joined * _WINDOW_STEPS} steps, within {band_width}"], window, band_width
                    )
                if not proposed:
                    continue
                _count_votes(votes_by_upgrade[window.upgrade], window, _PROPOSED_BAND_WIDTH)
                for rule, steady in _VOTE_RULES.items():
                    possible[rule].count_the_vote(key, unit_id, window, steady=steady)
                    if (vote := window.vote(_PROPOSED_BAND_WIDTH, steady=steady)) is None:
                        continue
                    hallucinations_voting[rule] += window.is_hallucination
                    partly_on_creep_voting[rule] += 0 < window.steps_on_creep < window.steps
                    for buff in window.buffs:
                        buffs_voting[f"{rule} {vote}, had {window.enemy_had_it}: {buff}"] += 1
    proposed_name = f"{_PROPOSED_WINDOWS_JOINED * _WINDOW_STEPS} steps within {_PROPOSED_BAND_WIDTH}"
    summary["recorded windows"] = recorded_windows
    summary["votes of every upgrade, by window steps and band width"] = {
        name: dict(sorted(counts.items())) for name, counts in votes.items()
    }
    summary[f"votes by upgrade, {proposed_name}"] = {
        upgrade: dict(sorted(counts.items())) for upgrade, counts in sorted(votes_by_upgrade.items())
    }
    summary[f"playbacks in which a wrong assumption was possible, {proposed_name}, by vote rule"] = {
        rule: wrong_assumptions.as_json() for rule, wrong_assumptions in possible.items()
    }
    summary["voting windows of hallucinations, by vote rule"] = dict(hallucinations_voting)
    summary["voting windows partly on creep, by vote rule"] = dict(partly_on_creep_voting)
    summary["buffs worn in voting windows, by vote and truth"] = dict(buffs_voting.most_common())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    playing = commands.add_parser("play")
    playing.add_argument("replays", type=Path, help="a folder of replays")
    playing.add_argument("--games", type=int, default=100)
    playing.add_argument("--seed", type=int, default=1)
    playing.add_argument("--clients", type=int, default=16)
    playing.add_argument("--out", type=Path, default=DEFAULT_OUT_DIRECTORY)
    playing.add_argument("--retry-failed", action="store_true", help="play a playback recorded failing again")
    summarizing = commands.add_parser("summarize")
    summarizing.add_argument("playbacks", type=Path, nargs="?", default=DEFAULT_OUT_DIRECTORY)
    summarizing.add_argument("--out", type=Path, default=DEFAULT_SUMMARY_PATH)
    arguments = parser.parse_args()
    if arguments.command == "play":
        play_sample(
            arguments.replays,
            games=arguments.games,
            seed=arguments.seed,
            clients=arguments.clients,
            out_directory=arguments.out,
            retry_failed=arguments.retry_failed,
        )
    else:
        summary = summarize_playbacks(arguments.playbacks)
        arguments.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote the summary to {}", arguments.out)


if __name__ == "__main__":
    main()
