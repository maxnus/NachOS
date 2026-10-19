"""A value as it stood at one of the tracker's updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class _Snapshot[T]:
    """A value as it stood at one of the tracker's updates, which it counts one per observation taken in, not in
    steps."""

    value: T
    update: int = -1
    """The number of the tracker update it was taken at."""

    def at[D](self, update: int, *, otherwise: D) -> T | D:
        """The value if it was taken at `update`, else `otherwise`."""
        return self.value if self.update == update else otherwise
