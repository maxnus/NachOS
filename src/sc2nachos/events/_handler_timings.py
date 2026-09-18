"""How long a handler has taken."""

from dataclasses import dataclass
from typing import final


@final
@dataclass(slots=True)
class HandlerTimings:
    """How long one handler has taken in the game being played, or the one played last."""

    calls: int
    """How many times it has been called."""
    total_seconds: float
    """How long it took over them all, in seconds of the clock rather than of the game."""
    max_seconds: float
    """How long the slowest call took."""

    @property
    def seconds_per_call(self) -> float:
        """How long a call took on average."""
        return self.total_seconds / self.calls
