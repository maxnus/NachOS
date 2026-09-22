"""Base class for the package's enums."""

from enum import IntEnum
from typing import Self, cast

from sc2nachos._errors import NachOSError


class UnknownValueError(NachOSError, ValueError):
    """The game reported a value the enum has no member for."""

    def __init__(self, enum: "type[ReadableIntEnum]", value: int) -> None:
        super().__init__(f"{enum.__name__} has no member for {value}")
        self.enum = enum
        self.value = value


class ReadableIntEnum(IntEnum):
    """An `IntEnum` that prints as its name, not its value, and reads values the game reports.

    An explicit format spec still formats the integer.
    """

    def __str__(self) -> str:
        return f"{type(self).__name__}.{self.name}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}.{self.name}: {self.value}>"

    def __format__(self, format_spec: str) -> str:
        if format_spec:
            return int.__format__(int(self), format_spec)
        return str(self)

    @classmethod
    def read(cls, value: int) -> Self:
        """The member for `value`. Raises `UnknownValueError` if the enum has none."""
        # The enum's own value-to-member map: under half the time of calling the enum.
        if (member := cls._value2member_map_.get(value)) is None:
            raise cls._unknown(value)
        return cast("Self", member)

    @classmethod
    def get(cls, value: int) -> Self | None:
        """The member for `value`, or `None` if `value` is zero or the enum has no member for it."""
        if not value:
            return None
        return cast("Self | None", cls._value2member_map_.get(value))

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        """The error `read` raises. An enum overrides it to say where the value should have come from."""
        return UnknownValueError(cls, value)
