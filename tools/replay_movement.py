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
- `check` scores the playbacks as upgrade inference is to, by the rule the pilot proposes: windows of 22 steps,
  steady votes within 0.05 of the gap, and none from a unit wearing a buff that changes its speed, or before the
  upgrade could have finished. The votes of each upgrade in one window's steps are a slice, k fast of n, whose
  likelihood is a beta-binomial for an enemy that has the upgrade and another for one that lacks it, fitted on the
  playbacks of `--fit`. On the rest it runs the score from each floor `p0` and threshold of assuming of
  `_SCORE_SETTINGS`, forgetting below 0.5, and counts the playbacks that assume an upgrade the enemy lacks and those
  that forget one it has; how long each one it has takes to be assumed, after it finished and after one of its units
  was first seen moving, a unit that does not move in a window not being recorded; and how often a score reads true.

Run it as::

    uv run python tools/replay_movement.py play /mnt/storage/data/sc2ai/aiarena-2026-season-1 --games 100
    uv run python tools/replay_movement.py summarize replay-movement
    uv run python tools/replay_movement.py check replay-movement --fit pilot-movement

The summary and the check are written as JSON.
"""

import argparse
import gzip
import json
import math
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
from sc2nachos.extensions.upgrade_inference._movement_windows import (
    MovementWindow,
    MovementWindows,
    speed_upgrade_by_type,
)
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.ids import UnitTypeId
from sc2nachos.testing import load_game_data

DEFAULT_OUT_DIRECTORY = Path("replay-movement")
DEFAULT_SUMMARY_PATH = Path("replay-movement.json")
DEFAULT_CHECK_PATH = Path("replay-movement-check.json")
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
# The buffs that change a unit's speed, under which `check` counts no vote: Time Warp's slow is measured
# (`docs/reference/game-behavior/damage-and-buffs.md`); the others are taken from what their names say, unmeasured.
_SPEED_CHANGING_BUFFS = frozenset(
    {
        "ACCELERATION_ZONE",
        "ACCELERATION_ZONE_FLYING",
        "HYDRALISK_LUNGE",
        "INFESTOR_FUNGAL_GROWTH",
        "INHIBITOR_ZONE",
        "INHIBITOR_ZONE_FLYING",
        "MARAUDER_CONCUSSIVE_SHELLS_SLOW",
        "MOTHERSHIP_TIME_WARP",
        "PHOENIX_GRAVITON_BEAM",
        "VOID_RAY_PRISMATIC_ALIGNMENT",
    }
)
# The structures a player starts with, which the earliest finish of an upgrade they research waits for no building of.
_STARTING_STRUCTURES = frozenset({UnitTypeId.COMMAND_CENTER, UnitTypeId.HATCHERY, UnitTypeId.NEXUS})
# The floors `check` runs the score from, each with the score at which it assumes an upgrade, and where it forgets one.
_SCORE_SETTINGS = ((0.01, 0.99), (0.01, 0.999), (0.05, 0.999), (0.14, 0.999))
_FORGOTTEN_BELOW = 0.5
# The beta-binomial spreads `check` fits over, as the correlation of two votes of one slice, and the number of means,
# spaced evenly in their logarithm from `_LEAST_MEAN` to 0.5.
_SPREADS_FITTED_OVER = (0.0, 1e-4, 1e-3, 0.01, 0.03, 0.1, 0.3, 0.6, 0.9, 0.99)
_MEANS_FITTED_OVER = 400
_LEAST_MEAN = 1e-9
# Where a score reads, for how often one reads true: each bin from its lower bound up to the next one's.
_SCORE_BINS = (0.0, 0.02, 0.1, 0.5, 0.9, 0.99, 0.999)

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
    start_step: int
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
                        start_step=start,
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


def _headers_of(paths: Sequence[Path]) -> dict[_PlaybackKey, dict[str, Any]]:
    """Everything but the windows of each playback at `paths`, which the truth for the other player's playback needs."""
    headers: dict[_PlaybackKey, dict[str, Any]] = {}
    for path in paths:
        header = _read_playback(path)
        header.pop("windows", None)
        headers[_key_of_playback(path)] = header
    return headers


def summarize_playbacks(out_directory: Path) -> dict[str, Any]:
    """What the playbacks in `out_directory` show, as the module docstring says, holding one playback's windows at a
    time."""
    paths = sorted(out_directory.glob("*.json.gz"))
    headers = _headers_of(paths)
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


@dataclass(frozen=True, slots=True)
class _VoteSlice:
    """The votes of the units one upgrade speeds up, in one window's steps of a playback, by the rule `check` scores."""

    upgrade: str
    start_step: int
    end_step: int
    fast_votes: int
    slow_votes: int
    enemy_had_it: bool | None

    @property
    def vote_count(self) -> int:
        return self.fast_votes + self.slow_votes


def _earliest_finish_steps() -> dict[str, int]:
    """The earliest step each speed upgrade could finish: its research time, after the quickest of its researchers to
    build, but for a researcher a player starts with."""
    game_data = load_game_data()
    earliest_steps: dict[str, int] = {}
    for upgrade in set(speed_upgrade_by_type(game_data).values()):
        upgrade_data = game_data.upgrades[upgrade]
        researcher_steps = min(
            0 if researcher in _STARTING_STRUCTURES else round(game_data.units[researcher].cost.time.steps)
            for researcher in upgrade_data.researchers
        )
        earliest_steps[upgrade.name] = researcher_steps + round(upgrade_data.cost.time.steps)
    return earliest_steps


def _proposed_vote(window: _Window) -> str | None:
    """The window's vote by the rule `check` scores: steady within the proposed band, and none under a buff that changes
    the unit's speed."""
    if window.buffs & _SPEED_CHANGING_BUFFS:
        return None
    return window.vote(_PROPOSED_BAND_WIDTH, steady=True)


def _vote_slices(
    playback: dict[str, Any], enemy: dict[str, Any] | None, earliest_finish_steps: dict[str, int]
) -> list[_VoteSlice]:
    """The slices of `playback`, in order, without the windows that began before their upgrade could have finished."""
    votes_by_slice: defaultdict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    enemy_had_it_by_slice: dict[tuple[str, int], bool | None] = {}
    for _, window in _joined_windows(playback, enemy, _PROPOSED_WINDOWS_JOINED):
        if window.start_step < earliest_finish_steps[window.upgrade] or (vote := _proposed_vote(window)) is None:
            continue
        slice_key = (window.upgrade, window.start_step)
        votes_by_slice[slice_key][vote] += 1
        enemy_had_it_by_slice[slice_key] = window.enemy_had_it
    joined_steps = playback["window_steps"] * _PROPOSED_WINDOWS_JOINED
    return sorted(
        (
            _VoteSlice(
                upgrade,
                start,
                start + joined_steps,
                counts["fast"],
                counts["slow"],
                enemy_had_it_by_slice[upgrade, start],
            )
            for (upgrade, start), counts in votes_by_slice.items()
        ),
        key=lambda vote_slice: vote_slice.start_step,
    )


@dataclass(frozen=True, slots=True)
class _BetaBinomial:
    """How many of a slice's votes are wrong: each is with probability `mean`, and two of one slice are correlated by
    `spread`, 0 for votes as independent as coin flips."""

    mean: float
    spread: float

    def log_probability(self, wrong_votes: int, votes: int) -> float:
        log_choices = math.lgamma(votes + 1) - math.lgamma(wrong_votes + 1) - math.lgamma(votes - wrong_votes + 1)
        if self.spread == 0:
            return log_choices + wrong_votes * math.log(self.mean) + (votes - wrong_votes) * math.log1p(-self.mean)
        concentration = 1 / self.spread - 1
        wrong_weight = self.mean * concentration
        right_weight = concentration - wrong_weight
        return (
            log_choices
            + math.lgamma(wrong_votes + wrong_weight)
            - math.lgamma(wrong_weight)
            + math.lgamma(votes - wrong_votes + right_weight)
            - math.lgamma(right_weight)
            + math.lgamma(concentration)
            - math.lgamma(votes + concentration)
        )

    def as_json(self) -> dict[str, float]:
        return {"mean": self.mean, "spread": self.spread}


def _fit_beta_binomial(slices: Counter[tuple[int, int]]) -> _BetaBinomial:
    """The beta-binomial most likely to give `slices`, counted by their wrong votes and votes, with half a wrong vote
    and half a right one added, so that the mean is above 0 though no vote was wrong.

    Where no vote was wrong, the most clustered spread fits best, and the mean comes to about half a vote over the
    number of slices: how strongly a slice counts then follows from how many slices it was fitted on, not from any
    error seen."""
    log_least_mean = math.log(_LEAST_MEAN)
    log_mean_range = math.log(0.5) - log_least_mean
    best_log_likelihood = -math.inf
    best_beta_binomial = _BetaBinomial(0.5, 0.0)
    for spread in _SPREADS_FITTED_OVER:
        for mean_index in range(_MEANS_FITTED_OVER):
            mean = math.exp(log_least_mean + log_mean_range * mean_index / (_MEANS_FITTED_OVER - 1))
            beta_binomial = _BetaBinomial(mean, spread)
            log_likelihood = 0.5 * math.log(mean) + 0.5 * math.log1p(-mean)
            log_likelihood += sum(
                count * beta_binomial.log_probability(wrong_votes, votes)
                for (wrong_votes, votes), count in slices.items()
            )
            if log_likelihood > best_log_likelihood:
                best_log_likelihood, best_beta_binomial = log_likelihood, beta_binomial
    return best_beta_binomial


@dataclass(frozen=True, slots=True)
class _VoteModel:
    """The likelihood of a slice's votes from an enemy that has the upgrade, and from one that lacks it."""

    had_it: _BetaBinomial
    lacked_it: _BetaBinomial

    def log_likelihood_ratio(self, fast_votes: int, slow_votes: int) -> float:
        """How much likelier the votes are from an enemy that has the upgrade, as a logarithm."""
        votes = fast_votes + slow_votes
        return self.had_it.log_probability(slow_votes, votes) - self.lacked_it.log_probability(fast_votes, votes)


def _log_odds_of_probability(probability: float) -> float:
    return math.log(probability / (1 - probability))


def _probability_of_log_odds(log_odds: float) -> float:
    return 1 / (1 + math.exp(-log_odds))


def _score_bin(score: float) -> int:
    return max(index for index, lower_bound in enumerate(_SCORE_BINS) if score >= lower_bound)


@dataclass(slots=True)
class _ScoreCheck:
    """What the score did over the checked playbacks, from one floor and assuming at one score."""

    floor: float
    assumed_at: float
    wrongly_assumed: list[str] = field(default_factory=list)
    wrongly_forgotten: list[str] = field(default_factory=list)
    playbacks_with_a_wrong_assumption_possible: set[_PlaybackKey] = field(default_factory=set)
    playbacks_with_a_wrong_forgetting_possible: set[_PlaybackKey] = field(default_factory=set)
    steps_to_assume: defaultdict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    steps_from_seeing_to_assuming: defaultdict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    not_assumed: Counter[str] = field(default_factory=Counter)
    readings: Counter[tuple[int, bool]] = field(default_factory=Counter)

    def score_upgrade(
        self,
        playback: _PlaybackKey,
        upgrade: str,
        finished: int | None,
        first_seen_moving: int | None,
        slices: Sequence[_VoteSlice],
        model: _VoteModel,
    ) -> None:
        """Run the score of `upgrade` over its `slices` in `playback`, the enemy having finished it at `finished`, and
        one of the units it speeds up having first been seen moving after that at `first_seen_moving`."""
        floor_log_odds = _log_odds_of_probability(self.floor)
        log_odds = floor_log_odds
        is_assumed = False
        assumed_after_finishing = False
        for vote_slice in slices:
            if is_assumed and vote_slice.enemy_had_it is True:
                self.playbacks_with_a_wrong_forgetting_possible.add(playback)
            log_odds = max(
                floor_log_odds, log_odds + model.log_likelihood_ratio(vote_slice.fast_votes, vote_slice.slow_votes)
            )
            score = _probability_of_log_odds(log_odds)
            if vote_slice.enemy_had_it is not None:
                self.readings[_score_bin(score), vote_slice.enemy_had_it] += 1
            if vote_slice.enemy_had_it is False:
                self.playbacks_with_a_wrong_assumption_possible.add(playback)
            has_it = finished is not None and finished <= vote_slice.end_step
            if not is_assumed and score >= self.assumed_at:
                is_assumed = True
                if not has_it:
                    self.wrongly_assumed.append(f"{playback[0]} as {playback[1]}, {upgrade} at {vote_slice.end_step}")
                elif finished is not None and not assumed_after_finishing:
                    assumed_after_finishing = True
                    self.steps_to_assume[upgrade].append(vote_slice.end_step - finished)
                    if first_seen_moving is not None:
                        self.steps_from_seeing_to_assuming[upgrade].append(vote_slice.end_step - first_seen_moving)
            elif is_assumed and score < _FORGOTTEN_BELOW:
                is_assumed = False
                if has_it:
                    self.wrongly_forgotten.append(f"{playback[0]} as {playback[1]}, {upgrade} at {vote_slice.end_step}")
        if finished is not None and not assumed_after_finishing:
            if is_assumed:
                reason_not_assumed = "assumed before it finished and still"
            elif first_seen_moving is None:
                reason_not_assumed = "none of its units seen moving after it finished"
            elif any(vote_slice.start_step >= finished for vote_slice in slices):
                reason_not_assumed = "voted on after it finished, too little"
            else:
                reason_not_assumed = "its units seen moving after it finished, but no vote"
            self.not_assumed[f"{upgrade}, {reason_not_assumed}"] += 1

    def as_json(self) -> dict[str, Any]:
        assumption_possible = len(self.playbacks_with_a_wrong_assumption_possible)
        forgetting_possible = len(self.playbacks_with_a_wrong_forgetting_possible)
        readings: dict[str, dict[str, Any]] = {}
        for index, lower_bound in enumerate(_SCORE_BINS):
            had_it = self.readings[index, True]
            lacked_it = self.readings[index, False]
            if had_it + lacked_it:
                upper_bound = _SCORE_BINS[index + 1] if index + 1 < len(_SCORE_BINS) else 1.0
                readings[f"{lower_bound} to {upper_bound}"] = {
                    "slices": had_it + lacked_it,
                    "share the enemy had it": round(had_it / (had_it + lacked_it), 5),
                }
        return {
            "floor": self.floor,
            "assumed at": self.assumed_at,
            "playbacks in which a wrong assumption was possible": assumption_possible,
            "wrong assumptions": self.wrongly_assumed,
            "a bound on wrong assumptions a playback, 95%": _rule_of_three(
                len(self.wrongly_assumed), assumption_possible
            ),
            "playbacks in which a slice followed an assumption of an upgrade the enemy had": forgetting_possible,
            "wrong forgettings": self.wrongly_forgotten,
            (
                "seconds to being assumed from first sight, after it finished, "
                "of one of its units in a window it moved in"
            ): _seconds_by_upgrade(self.steps_from_seeing_to_assuming),
            "seconds from finishing to being assumed": _seconds_by_upgrade(self.steps_to_assume),
            "finished but never assumed after it finished": dict(sorted(self.not_assumed.items())),
            "how often a score read true, by where it read": readings,
        }


def _seconds_by_upgrade(steps_by_upgrade: dict[str, list[int]]) -> dict[str, dict[str, float]]:
    """The median and the tail of `steps_by_upgrade` in seconds, of every upgrade and of each."""
    every_upgrade = [steps for steps_of_one in steps_by_upgrade.values() for steps in steps_of_one]
    return {
        "every upgrade": _median_and_tail([steps / STEPS_PER_SECOND for steps in every_upgrade]),
        **{
            upgrade: _median_and_tail([steps / STEPS_PER_SECOND for steps in steps_of_one])
            for upgrade, steps_of_one in sorted(steps_by_upgrade.items())
        },
    }


def _first_seen_moving_steps(playback: dict[str, Any], finished_steps: dict[str, int]) -> dict[str, int]:
    """The first step at which a unit of a type each upgrade in `finished_steps` speeds up was in sight after the
    upgrade finished, in a window it moved in: `playback` records no window a unit did not move in, so a unit seen
    standing still the whole of one is not counted, and one that stood still at the start of one is."""
    columns = playback["windows"]
    first_seen: dict[str, int] = {}
    for upgrade, start, end in zip(columns["upgrade"], columns["start"], columns["end"], strict=True):
        finished = finished_steps.get(upgrade)
        if finished is None or end < finished:
            continue
        seen_after_finishing = max(start, finished)
        if seen_after_finishing < first_seen.get(upgrade, seen_after_finishing + 1):
            first_seen[upgrade] = seen_after_finishing
    return first_seen


def _rule_of_three(wrong: int, possible: int) -> float | None:
    """Below what rate the wrong outcomes are, at 95%, where none of `possible` was; `None` where one was."""
    return round(3 / possible, 5) if wrong == 0 and possible else None


def _fit_vote_model(
    fit_paths: Sequence[Path], earliest_finish_steps: dict[str, int]
) -> tuple[_VoteModel, dict[str, Any]]:
    """The vote model fitted on the playbacks at `fit_paths`, and what it was fitted on."""
    headers = _headers_of(fit_paths)
    wrong_by_truth: dict[bool, Counter[tuple[int, int]]] = {True: Counter(), False: Counter()}
    for path in fit_paths:
        playback = _read_playback(path)
        if "windows" not in playback:
            continue
        replay, player_id = _key_of_playback(path)
        for vote_slice in _vote_slices(playback, headers.get((replay, 3 - player_id)), earliest_finish_steps):
            if vote_slice.enemy_had_it is None:
                continue
            wrong_votes = vote_slice.slow_votes if vote_slice.enemy_had_it else vote_slice.fast_votes
            wrong_by_truth[vote_slice.enemy_had_it][wrong_votes, vote_slice.vote_count] += 1
    model = _VoteModel(_fit_beta_binomial(wrong_by_truth[True]), _fit_beta_binomial(wrong_by_truth[False]))
    fitted_on: dict[str, Any] = {"playbacks": len(headers)}
    for truth, truth_name in ((True, "had it"), (False, "lacked it")):
        slice_counts = wrong_by_truth[truth]
        fitted_on[f"enemy {truth_name}"] = {
            "slices": sum(slice_counts.values()),
            "votes": sum(votes * count for (_, votes), count in slice_counts.items()),
            "wrong votes": sum(wrong_votes * count for (wrong_votes, _), count in slice_counts.items()),
            "slices with a wrong vote": sum(count for (wrong_votes, _), count in slice_counts.items() if wrong_votes),
        }
    fitted_on["model"] = {"had it": model.had_it.as_json(), "lacked it": model.lacked_it.as_json()}
    example_slices = ((1, 0), (2, 0), (5, 0), (20, 0), (0, 1), (0, 20), (1, 19), (19, 1))
    fitted_on["score after one slice of fast and slow votes, from each floor"] = {
        f"{fast} fast, {slow} slow": {
            str(floor): round(
                _probability_of_log_odds(_log_odds_of_probability(floor) + model.log_likelihood_ratio(fast, slow)), 6
            )
            for floor in sorted({floor for floor, _ in _SCORE_SETTINGS})
        }
        for fast, slow in example_slices
    }
    return model, fitted_on


def check_playbacks(out_directory: Path, fit_directories: Sequence[Path]) -> dict[str, Any]:
    """Fit the vote model on the playbacks in `fit_directories` and check the score on those in `out_directory` of
    other replays, as the module docstring says."""
    earliest_finish_steps = _earliest_finish_steps()
    # By playback, so that a playback copied into two of the folders is fitted on once.
    fit_paths_by_playback = {
        _key_of_playback(path): path for directory in fit_directories for path in directory.glob("*.json.gz")
    }
    fit_paths = sorted(fit_paths_by_playback.values())
    model, fitted_on = _fit_vote_model(fit_paths, earliest_finish_steps)
    fit_replays = {_key_of_playback(path)[0] for path in fit_paths}
    paths = [path for path in sorted(out_directory.glob("*.json.gz")) if _key_of_playback(path)[0] not in fit_replays]
    headers = _headers_of(paths)
    score_checks = [_ScoreCheck(floor, assumed_at) for floor, assumed_at in _SCORE_SETTINGS]
    playbacks_checked = 0
    for path in paths:
        replay, player_id = key = _key_of_playback(path)
        enemy = headers.get((replay, 3 - player_id))
        # A failed playback, its own or its enemy's, leaves when an upgrade finished unknown after it stopped.
        if enemy is None or enemy["failure"] is not None or headers[key]["failure"] is not None:
            continue
        playbacks_checked += 1
        playback = _read_playback(path)
        by_upgrade: defaultdict[str, list[_VoteSlice]] = defaultdict(list)
        for vote_slice in _vote_slices(playback, enemy, earliest_finish_steps):
            by_upgrade[vote_slice.upgrade].append(vote_slice)
        finished_steps = {
            upgrade: step for upgrade, step in enemy["upgrades"].items() if upgrade in earliest_finish_steps
        }
        first_seen_moving = _first_seen_moving_steps(playback, finished_steps)
        for upgrade in by_upgrade.keys() | finished_steps.keys():
            for score_check in score_checks:
                score_check.score_upgrade(
                    key,
                    upgrade,
                    finished_steps.get(upgrade),
                    first_seen_moving.get(upgrade),
                    by_upgrade[upgrade],
                    model,
                )
    return {
        "earliest finish, in steps": dict(sorted(earliest_finish_steps.items())),
        "fitted on": fitted_on,
        "playbacks checked": playbacks_checked,
        "playbacks left out, as one of the game's two failed or is missing": len(paths) - playbacks_checked,
        "checks, by floor and where it assumes": [score_check.as_json() for score_check in score_checks],
    }


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
    checking = commands.add_parser("check")
    checking.add_argument("playbacks", type=Path, nargs="?", default=DEFAULT_OUT_DIRECTORY)
    checking.add_argument("--fit", type=Path, nargs="+", required=True, help="folders of playbacks to fit on")
    checking.add_argument("--out", type=Path, default=DEFAULT_CHECK_PATH)
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
    elif arguments.command == "summarize":
        summary = summarize_playbacks(arguments.playbacks)
        arguments.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote the summary to {}", arguments.out)
    else:
        check_result = check_playbacks(arguments.playbacks, arguments.fit)
        arguments.out.write_text(json.dumps(check_result, indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote the check to {}", arguments.out)


if __name__ == "__main__":
    main()
