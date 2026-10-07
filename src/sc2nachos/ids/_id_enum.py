"""The base of every curated id enum, which names the raw catalog member of an id it leaves out."""

from collections.abc import Sequence

from sc2nachos._enum import ReadableIntEnum, UnknownValueError
from sc2nachos.ids import raw

# The first custom id, above every id the game has.
CUSTOM_IDS_FROM = 1_000_000


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

    A member assigned `auto()` is a custom id, one the game does not have: the next above `CUSTOM_IDS_FROM` after the
    custom ids defined before it. Its value depends on its place among them, so a custom id is never written down
    by value.
    """

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: Sequence[int]) -> int:
        return max((value for value in last_values if value >= CUSTOM_IDS_FROM), default=CUSTOM_IDS_FROM - 1) + 1

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        return UncuratedIdError(cls, value)
