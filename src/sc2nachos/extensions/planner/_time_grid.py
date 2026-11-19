"""The planner's grid of time buckets."""

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

    Raises `ValueError` for no segments, a bucket not longer than 0, or a segment's time not after the one before.
    """

    __slots__ = ("_segments",)

    def __init__(self, *, segments: Sequence[tuple[GameTime, GameTime]]) -> None:
        if not segments:
            raise ValueError("a time grid needs at least one segment")
        previous_end = GameTime.ZERO
        for bucket, end in segments:
            if bucket <= GameTime.ZERO:
                raise ValueError(f"a bucket must be longer than 0, not {bucket}")
            if end <= previous_end:
                raise ValueError(f"each segment must end after the one before, but {end} follows {previous_end}")
            previous_end = end
        self._segments = tuple(segments)

    @classmethod
    def doubling(
        cls,
        *,
        initial_bucket_length: GameTime = GameTime(steps=34),
        initial_bucket_count: int = 16,
        max_bucket_doublings: int = 4,
    ) -> Self:
        """A grid whose buckets double in length each time the time from the plan's start doubles:
        `initial_bucket_count` buckets of `initial_bucket_length`, then half as many of each doubling of it, the last of
        at most `max_bucket_doublings`, which runs on to any horizon. By default 16 buckets of 34 game steps, 8 each of
        68, 136 and 272, and buckets of 544 after: 1.5 seconds until 0:24, doubling to 24.3 seconds from 3:14, 57
        buckets to a 10-minute horizon.

        The default is cut to a worker's 272 steps, the same for every race, eight of its first buckets: workers
        trained one after another each start on a bucket's first second, as they do while the buckets double. A build
        time no bucket divides is planned up to a bucket longer where one follows another on a producer: a marine's 400
        steps as 408.

        Raises `ValueError` unless `initial_bucket_length` is longer than 0, `initial_bucket_count` is even and above
        0, and `max_bucket_doublings` is not negative.
        """
        if initial_bucket_count <= 0 or initial_bucket_count % 2:
            raise ValueError(f"the initial bucket count must be even and above 0, not {initial_bucket_count}")
        if max_bucket_doublings < 0:
            raise ValueError(f"the bucket doublings must not be negative, not {max_bucket_doublings}")
        segments: list[tuple[GameTime, GameTime]] = []
        bucket = initial_bucket_length
        end = initial_bucket_length * initial_bucket_count
        for _ in range(max_bucket_doublings):
            segments.append((bucket, end))
            bucket *= 2
            end *= 2
        segments.append((bucket, GameTime.INFINITE))
        return cls(segments=segments)

    @classmethod
    def uniform(cls, bucket: GameTime) -> Self:
        """A grid of buckets all `bucket` long."""
        return cls(segments=[(bucket, GameTime.INFINITE)])

    @property
    def segments(self) -> tuple[tuple[GameTime, GameTime], ...]:
        """Each segment's bucket length, and the time from the plan's start it runs to."""
        return self._segments

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
