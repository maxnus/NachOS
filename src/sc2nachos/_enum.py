"""Base class for the package's enums."""

from enum import IntEnum
from typing import Self, cast

from sc2nachos._errors import NachOSError


class UnknownValueError(NachOSError, ValueError):
    """A game reported a value the enum leaves out."""

    def __init__(self, enum: "type[ReadableIntEnum]", value: int) -> None:
        super().__init__(f"{enum.__name__} has no member for {value}")
        self.enum = enum
        self.value = value


class ReadableIntEnum(IntEnum):
    """An `IntEnum` that prints as its name rather than its value, and reads a value a game reported.

    Python 3.11 made `IntEnum` format as a bare number, so logs read `48` instead of `UnitTypeId.MARINE`. An
    explicit format spec still formats the integer.
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
        """The member a game's `value` names. Raises `UnknownValueError` where the enum leaves it out."""
        # The enum's own map from value to member, which reads in under half the time calling the enum takes.
        if (member := cls._value2member_map_.get(value)) is None:
            raise cls._unknown(value)
        return cast("Self", member)

    @classmethod
    def get(cls, value: int) -> Self | None:
        """The member `value` names, or `None` where it is zero, which names nothing, or the enum leaves it out."""
        if not value:
            return None
        return cast("Self | None", cls._value2member_map_.get(value))

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        """The error `read` raises, which an enum overrides to say more about where the value should have come
        from."""
        return UnknownValueError(cls, value)
