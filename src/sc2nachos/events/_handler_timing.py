"""How long a handler has taken."""

from dataclasses import dataclass
from typing import final


@final
@dataclass(slots=True)
class HandlerTiming:
    """How long one handler has taken in the current game, or the last one played."""

    calls: int
    """The number of calls."""
    total_seconds: float
    """Wall-clock seconds over all calls."""
    max_seconds: float
    """Wall-clock seconds of the slowest call."""

    @property
    def seconds_per_call(self) -> float:
        """Wall-clock seconds per call, on average."""
        return self.total_seconds / self.calls
