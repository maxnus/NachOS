"""The planner's grid of time buckets, and which starts it keeps whole."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final, Self, final

from sc2nachos.gametime import GameTime

# Within this of an edge, a time counts as on it, so that seconds summed in floats land where they were meant to.
SECONDS_TOLERANCE: Final = 1e-9


@final
class TimeGrid:
    """The buckets a plan's starts fall in, from the moment it is made: each start is planned at a bucket's first
    second, and a build counts as done from the first edge at or after its end. Durations are never rounded.

    It is laid out in segments, each a bucket length and the time from the plan's start it runs to: a segment's buckets
    follow one another while the next would still end by its time, and the next segment carries on from the last edge.
    The grid ends at the horizon, its last bucket cut short there.

    An item made few times and dearly, a townhall, a production or tech structure, an upgrade or a townhall's morph,
    starts in whole numbers over the whole horizon; the rest, units, supply depots, refineries, add-ons and the other
    structures, until `whole_until` from the plan's start, and in fractions after it.

    Raises `ValueError` for no segments, a bucket not longer than 0, a segment's time not after the one before, or a
    negative `whole_until`.
    """

    __slots__ = ("_segments", "_whole_until")

    def __init__(
        self, *, segments: Sequence[tuple[GameTime, GameTime]], whole_until: GameTime = GameTime(seconds=90)
    ) -> None:
        if not segments:
            raise ValueError("a time grid needs at least one segment")
        previous_end = GameTime.ZERO
        for bucket, end in segments:
            if bucket <= GameTime.ZERO:
                raise ValueError(f"a bucket must be longer than 0, not {bucket}")
            if end <= previous_end:
                raise ValueError(f"each segment must end after the one before, but {end} follows {previous_end}")
            previous_end = end
        if whole_until < GameTime.ZERO:
            raise ValueError(f"whole_until must not be negative, not {whole_until}")
        self._segments = tuple(segments)
        self._whole_until = whole_until

    @classmethod
    def doubling(
        cls,
        *,
        first_bucket: GameTime = GameTime(seconds=2),
        first_doubling: GameTime = GameTime(seconds=45),
        longest_bucket: GameTime = GameTime(seconds=32),
        whole_until: GameTime = GameTime(seconds=90),
    ) -> Self:
        """A grid whose buckets double in length each time the time from the plan's start doubles, from
        `first_bucket` until `first_doubling` to the longest doubling of it not above `longest_bucket`, which runs on to
        any horizon: by default, 2 seconds
        until 0:45, 4 until 1:30, 8 until 3:00, 16 until 6:00, and 32 after, 63 buckets to a 10-minute horizon.

        Raises `ValueError` unless `first_bucket` and `first_doubling` are longer than 0 and `longest_bucket` is at
        least `first_bucket`.
        """
        if first_doubling <= GameTime.ZERO:
            raise ValueError(f"the first doubling must come after 0, not at {first_doubling}")
        if longest_bucket < first_bucket:
            raise ValueError(f"the longest bucket, {longest_bucket}, is shorter than the first, {first_bucket}")
        segments: list[tuple[GameTime, GameTime]] = []
        bucket = first_bucket
        end = first_doubling
        while bucket * 2 <= longest_bucket:
            segments.append((bucket, end))
            bucket *= 2
            end *= 2
        segments.append((bucket, GameTime.INFINITE))
        return cls(segments=segments, whole_until=whole_until)

    @classmethod
    def uniform(cls, bucket: GameTime, *, whole_until: GameTime = GameTime(seconds=90)) -> Self:
        """A grid of buckets all `bucket` long."""
        return cls(segments=[(bucket, GameTime.INFINITE)], whole_until=whole_until)

    @property
    def segments(self) -> tuple[tuple[GameTime, GameTime], ...]:
        """Each segment's bucket length, and the time from the plan's start it runs to."""
        return self._segments

    @property
    def whole_until(self) -> GameTime:
        """The time from the plan's start until which units, supply depots, refineries, add-ons and the structures that
        make, research and grant nothing start in whole numbers."""
        return self._whole_until

    def edges(self, horizon: GameTime) -> tuple[float, ...]:
        """The buckets' edges to `horizon`, in seconds from the plan's start, 0 first and the horizon last.

        Raises `ValueError` for a horizon not longer than 0, or infinite.
        """
        if horizon <= GameTime.ZERO or not math.isfinite(horizon.seconds):
            raise ValueError(f"a horizon must be longer than 0 and finite, not {horizon}")
        end_of_plan = horizon.seconds
        edges = [0.0]
        for bucket, end in self._segments:
            segment_end = min(end.seconds, end_of_plan)
            while edges[-1] + bucket.seconds <= segment_end + SECONDS_TOLERANCE:
                edges.append(edges[-1] + bucket.seconds)
            if segment_end >= end_of_plan:
                break
        if end_of_plan - edges[-1] > SECONDS_TOLERANCE:
            edges.append(end_of_plan)
        else:
            edges[-1] = end_of_plan
        return tuple(edges)
