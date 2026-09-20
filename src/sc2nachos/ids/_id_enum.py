"""What every curated id enum shares: naming the raw catalog an id it leaves out belongs to."""

from sc2nachos._enum import ReadableIntEnum, UnknownValueError
from sc2nachos.ids import raw


class UncuratedIdError(UnknownValueError):
    """A game reported an id the curated enum leaves out."""

    def __init__(self, enum: type[ReadableIntEnum], value: int) -> None:
        catalog: type[ReadableIntEnum] = getattr(raw, f"Raw{enum.__name__}")
        try:
            name = str(catalog(value))
        except ValueError:
            name = f"not in {catalog.__name__} either"
        super().__init__(enum, value)
        self.args = (f"{enum.__name__} has no member for id {value} ({name})",)


class IdEnum(ReadableIntEnum):
    """A curated enum of the game's ids, each member named by the raw catalog member it is.

    Calling the enum on an id it leaves out raises `ValueError`, as any enum does; `read` raises
    `UncuratedIdError`, which names the raw catalog member the id is.
    """

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        return UncuratedIdError(cls, value)
