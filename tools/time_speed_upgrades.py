"""Time what upgrade inference's speed upgrades cost a turn: each recorded game played back with `UpgradeInference`
built with `include_speed_upgrades` and without it, in rounds that alternate the two, the whole playback timed; and,
in one round more, the time spent in the speed code itself, `MovementWindows` and the score it hands the windows to,
with the visible enemy units it measured a turn.

A game recorded through a bot, as AvocaDOS's are, plays back with no bot, `run_recording`'s `permissive`. The times
are a playback's own, with the recording read and parsed as it goes, so only the difference between the two settings
and the time in the speed code are its cost.

Run it as::

    uv run python tools/time_speed_upgrades.py
    uv run python tools/time_speed_upgrades.py ../AvocaDOS/tests/games/*.sc2rec --rounds 5

with the corpus's games by default.
"""

import argparse
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sc2nachos import Api
from sc2nachos.events import EventPriority, TurnStartEvent
from sc2nachos.extensions.upgrade_inference import UpgradeInference
from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindows, speed_upgrade_by_type
from sc2nachos.run import run_recording

_CORPUS_DIRECTORY = Path(__file__).resolve().parent.parent / "tests" / "corpus"


@dataclass(slots=True)
class _PlaybackTimes:
    """How long a recorded game took to play back, a turn, with the speed upgrades and without, round by round, and
    what the speed code took a turn in the round that timed it."""

    turns: int = 0
    seconds_with: list[float] = field(default_factory=list)
    seconds_without: list[float] = field(default_factory=list)
    seconds_in_the_speed_code_each_turn: list[float] = field(default_factory=list)
    units_measured_each_turn: list[int] = field(default_factory=list)
    upgrades_read: frozenset[str] = frozenset()

    @property
    def seconds_in_the_speed_code(self) -> float:
        return sum(self.seconds_in_the_speed_code_each_turn)

    @property
    def units_measured(self) -> int:
        return sum(self.units_measured_each_turn)

    def as_row(self, name: str) -> str:
        with_a_turn = statistics.median(self.seconds_with) / self.turns
        without_a_turn = statistics.median(self.seconds_without) / self.turns
        return (
            f"{name:44} {self.turns:6} turns  {without_a_turn * 1e6:7.1f} µs a turn without, {with_a_turn * 1e6:7.1f}"
            f" with, {(with_a_turn - without_a_turn) * 1e6:+6.1f}; in the speed code"
            f" {self.seconds_in_the_speed_code / self.turns * 1e6:5.1f} µs a turn, {_slowest_turns(self)};"
            f" {self.units_measured / self.turns:5.1f} units measured a turn, {max(self.units_measured_each_turn)} at"
            f" most; read {sorted(self.upgrades_read)}"
        )


def _slowest_turns(times: _PlaybackTimes) -> str:
    """The 99th percentile and the slowest of the speed code's turns, with the units the slowest measured."""
    each_turn = times.seconds_in_the_speed_code_each_turn
    slowest = max(range(len(each_turn)), key=each_turn.__getitem__)
    percentile_99 = statistics.quantiles(each_turn, n=100)[98]
    return (
        f"99% within {percentile_99 * 1e6:.1f}, the slowest {each_turn[slowest] * 1e6:.1f}"
        f" measuring {times.units_measured_each_turn[slowest]}"
    )


def _seconds_to_play_back(
    recording: Path, build_the_extensions: Callable[[Api[Any]], object]
) -> tuple[float, Api[Any]]:
    api: Api[Any] = Api(extensions=build_the_extensions)
    started = time.perf_counter()
    run_recording(recording, api, permissive=True)
    return time.perf_counter() - started, api


def _time_the_speed_code(recording: Path, times: _PlaybackTimes) -> None:
    """Play `recording` back once more with the speed upgrades, timing `MovementWindows`'s turns, which hand the
    windows to the score, and counting the units each measures."""
    measure = MovementWindows._measure

    def timed_measure(windows: MovementWindows, *arguments: object) -> None:
        started = time.perf_counter()
        measure(windows, *arguments)
        times.seconds_in_the_speed_code_each_turn.append(time.perf_counter() - started)

    def count_the_units_measured(api: Api[Any]) -> None:
        tracked_types = speed_upgrade_by_type(api.data).keys()

        @api.on(TurnStartEvent, priority=EventPriority.BEFORE)
        def count(_: object) -> None:
            times.units_measured_each_turn.append(
                sum(1 for unit in api.units.enemy if unit.type_id in tracked_types and unit.is_visible)
            )
            times.turns += 1

    def build_the_extensions(api: Api[Any]) -> UpgradeInference:
        upgrade_inference = UpgradeInference(api)
        count_the_units_measured(api)
        return upgrade_inference

    MovementWindows._measure = timed_measure  # type: ignore[method-assign]
    try:
        _, api = _seconds_to_play_back(recording, build_the_extensions)
    finally:
        MovementWindows._measure = measure  # type: ignore[method-assign]
    times.upgrades_read = frozenset(upgrade.name for upgrade in api.enemy.upgrades)


def time_the_recordings(recordings: Sequence[Path], *, rounds: int) -> dict[str, _PlaybackTimes]:
    """Each of `recordings` played back `rounds` times with the speed upgrades and without, alternating, and once
    more to time the speed code."""
    times_by_recording = {recording.name: _PlaybackTimes() for recording in recordings}
    for round_index in range(rounds):
        settings = (True, False) if round_index % 2 == 0 else (False, True)
        for recording in recordings:
            for include_speed_upgrades in settings:
                seconds, _ = _seconds_to_play_back(
                    recording,
                    lambda api, include=include_speed_upgrades: UpgradeInference(api, include_speed_upgrades=include),
                )
                times = times_by_recording[recording.name]
                (times.seconds_with if include_speed_upgrades else times.seconds_without).append(seconds)
    for recording in recordings:
        _time_the_speed_code(recording, times_by_recording[recording.name])
    return times_by_recording


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", type=Path, nargs="*", help="recorded games, by default the corpus's")
    parser.add_argument("--rounds", type=int, default=5)
    arguments = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    recordings = arguments.recordings or sorted(_CORPUS_DIRECTORY.glob("*.sc2rec"))
    times_by_recording = time_the_recordings(recordings, rounds=arguments.rounds)
    for name, times in times_by_recording.items():
        print(times.as_row(name))
    turns = sum(times.turns for times in times_by_recording.values())
    in_the_speed_code = sum(times.seconds_in_the_speed_code for times in times_by_recording.values())
    units_measured = sum(times.units_measured for times in times_by_recording.values())
    print(
        f"Every game: {turns} turns, {in_the_speed_code / turns * 1e6:.1f} µs a turn in the speed code,"
        f" {units_measured / turns:.1f} units measured a turn,"
        f" {in_the_speed_code / max(units_measured, 1) * 1e6:.2f} µs a unit measured"
    )


if __name__ == "__main__":
    main()
