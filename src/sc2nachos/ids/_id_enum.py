"""The base of every curated id enum, which names the raw catalog member of an id it leaves out."""

from sc2nachos._enum import ReadableIntEnum, UnknownValueError
from sc2nachos.ids import raw


class UncuratedIdError(UnknownValueError):
    """The game reported an id the curated enum leaves out."""

    def __init__(self, enum: type[ReadableIntEnum], value: int) -> None:
        catalog: type[ReadableIntEnum] = getattr(raw, f"Raw{enum.__name__}")
        try:
            name = str(catalog(value))
        except ValueError:
            name = f"not in {catalog.__name__} either"
        super().__init__(enum, value)
        self.args = (f"{enum.__name__} has no member for id {value} ({name})",)


class IdEnum(ReadableIntEnum):
    """A curated enum of game ids, each member defined by its raw catalog member.

    Calling the enum on an id it leaves out raises `ValueError`, as with any enum; `read` raises `UncuratedIdError`,
    which names the raw catalog member.
    """

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        return UncuratedIdError(cls, value)
