"""What every curated id enum shares: reading an id a game reported."""

from typing import Self, cast

from sc2nachos._enum import ReadableIntEnum
from sc2nachos._errors import NachOSError
from sc2nachos.ids import raw


class UncuratedIdError(NachOSError, ValueError):
    """A game reported an id the curated enum leaves out."""

    def __init__(self, enum: type[ReadableIntEnum], value: int) -> None:
        catalog: type[ReadableIntEnum] = getattr(raw, f"Raw{enum.__name__}")
        try:
            name = str(catalog(value))
        except ValueError:
            name = f"not in {catalog.__name__} either"
        super().__init__(f"{enum.__name__} has no member for id {value} ({name})")
        self.enum = enum
        self.value = value


class IdEnum(ReadableIntEnum):
    """A curated enum of the game's ids, each member named by the raw catalog member it is.

    Calling the enum on an id it leaves out raises `ValueError`, as any enum does.
    """

    @classmethod
    def read(cls, value: int) -> Self:
        """The member a game's `value` names. Raises `UncuratedIdError` where the enum leaves it out."""
        # The enum's own map from value to member, which reads in under half the time calling the enum takes.
        if (member := cls._value2member_map_.get(value)) is None:
            raise UncuratedIdError(cls, value)
        return cast("Self", member)

    @classmethod
    def get(cls, value: int) -> Self | None:
        """The member `value` names, or `None` where it is zero, which names nothing, or the enum leaves it out."""
        if not value:
            return None
        return cast("Self | None", cls._value2member_map_.get(value))
