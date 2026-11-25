"""What a read's `default` holds where it was given none."""

from __future__ import annotations

from enum import Enum
from typing import Final


class _NoDefault(Enum):
    """What a call's `default` holds where it was given none, `None` being a default a bot gives."""

    NO_DEFAULT = "no default"

    def __repr__(self) -> str:
        """How a call's signature shows a default not given."""
        return self.name


NO_DEFAULT: Final = _NoDefault.NO_DEFAULT
