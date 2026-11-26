"""The planner's grid of time intervals."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Self, final

from sc2nachos.gametime import GameTime

# Within this of an edge, a time counts as on it, so that seconds summed in floats land where they were meant to.
SECONDS_TOLERANCE: Final = 1e-9


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class GridSegment:
    """A stretch of a `TimeGrid` whose intervals are all one length.

    Raises `ValueError` for an `interval_length` not longer than 0.
    """

    interval_length: GameTime
    """How long each of its intervals is."""
    end: GameTime
    """The time from the plan's start the segment runs to: `GameTime.INFINITE` for one that runs on to any horizon."""

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Raise `ValueError` for a field that holds what the class's docstring refuses."""
        if self.interval_length <= GameTime.ZERO:
            raise ValueError(f"an interval must be longer than 0, not {self.interval_length}")


@final
class TimeGrid:
    """The intervals a plan's starts fall in, from the moment it is made: each start is planned at an interval's first
    second, and a build counts as done from the first edge at or after its end. Durations are never rounded.

    It is laid out in segments, each a `GridSegment`: a segment's intervals follow one another while the next would
    still end by the segment's `end`, and the next segment carries on from the last edge. The grid ends at the horizon,
    its last interval cut short there; past the last segment's `end`, one interval runs to the horizon, however long.

    Raises `ValueError` for no segments, or a segment that does not end after the one before.
    """

    __slots__ = ("_segments",)

    def __init__(self, *, segments: Sequence[GridSegment]) -> None:
        if not segments:
            raise ValueError("a time grid needs at least one segment")
        previous_end = GameTime.ZERO
        for segment in segments:
            if segment.end <= previous_end:
                raise ValueError(
                    f"each segment must end after the one before, but {segment.end} follows {previous_end}"
                )
            previous_end = segment.end
        self._segments = tuple(segments)

    @classmethod
    def doubling(
        cls,
        *,
        initial_interval_length: GameTime = GameTime(steps=34),
        initial_interval_count: int = 16,
        max_interval_doublings: int = 4,
    ) -> Self:
        """A grid whose intervals double in length each time the time from the plan's start doubles:
        `initial_interval_count` intervals of `initial_interval_length`, then half as many of each doubling of it, the
        last of at most `max_interval_doublings`, which runs on to any horizon. By default 16 intervals of 34 game
        steps, 8 each of 68, 136 and 272, and intervals of 544 after: 1.5 seconds until 0:24, doubling to 24.3 seconds
        from 3:14, 57 intervals to a 10-minute horizon.

        The default is cut to a worker's 272 steps, the same for every race, eight of its first intervals: workers
        trained one after another each start on an interval's first second, as they do while the intervals double. A
        build time no interval divides is planned up to an interval longer where one follows another on a producer: a
        marine's 400 steps as 408.

        Raises `ValueError` unless `initial_interval_length` is longer than 0, `initial_interval_count` is even and
        above 0, and `max_interval_doublings` is not negative.
        """
        if initial_interval_count <= 0 or initial_interval_count % 2:
            raise ValueError(f"the initial interval count must be even and above 0, not {initial_interval_count}")
        if max_interval_doublings < 0:
            raise ValueError(f"the interval doublings must not be negative, not {max_interval_doublings}")
        segments: list[GridSegment] = []
        interval = initial_interval_length
        end = initial_interval_length * initial_interval_count
        for _ in range(max_interval_doublings):
            segments.append(GridSegment(interval_length=interval, end=end))
            interval *= 2
            end *= 2
        segments.append(GridSegment(interval_length=interval, end=GameTime.INFINITE))
        return cls(segments=segments)

    @classmethod
    def uniform(cls, interval_length: GameTime) -> Self:
        """A grid of intervals all `interval_length` long."""
        return cls(segments=[GridSegment(interval_length=interval_length, end=GameTime.INFINITE)])

    @property
    def segments(self) -> tuple[GridSegment, ...]:
        """Its segments, in order."""
        return self._segments

    def edges(self, horizon: GameTime) -> tuple[GameTime, ...]:
        """The times the grid's intervals start and end at, up to `horizon`, each counted from the plan's start: zero
        first and the horizon last.

        Raises `ValueError` for a horizon not longer than 0, or infinite.
        """
        return tuple(GameTime(seconds=seconds) for seconds in edge_seconds_of(self, horizon))


def edge_seconds_of(grid: TimeGrid, horizon: GameTime) -> tuple[float, ...]:
    """`grid`'s edges to `horizon` in seconds from the plan's start, 0 first and the horizon last, as the planner's
    program counts time: summed in seconds, so that a grid of whole seconds has its edges on them exactly.

    Raises `ValueError` for a horizon not longer than 0, or infinite.
    """
    if horizon <= GameTime.ZERO or not math.isfinite(horizon.seconds):
        raise ValueError(f"a horizon must be longer than 0 and finite, not {horizon}")
    end_of_plan = horizon.seconds
    edges = [0.0]
    for segment in grid.segments:
        interval_seconds = segment.interval_length.seconds
        segment_end = min(segment.end.seconds, end_of_plan)
        while edges[-1] + interval_seconds <= segment_end + SECONDS_TOLERANCE:
            edges.append(edges[-1] + interval_seconds)
        if segment_end >= end_of_plan:
            break
    if end_of_plan - edges[-1] > SECONDS_TOLERANCE:
        edges.append(end_of_plan)
    else:
        edges[-1] = end_of_plan
    return tuple(edges)
