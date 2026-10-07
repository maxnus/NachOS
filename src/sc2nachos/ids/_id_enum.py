"""The base of every curated id enum, which names the raw catalog member of an id it leaves out."""

from collections.abc import Sequence
from enum import nonmember
from types import MappingProxyType
from typing import Self, cast

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

    `read` and `get` also take a game id that is not a member but reads as one (`_REMAPPED_IDS`). Calling the enum
    takes only a member's own id, as reading the game's tables needs.

    A member assigned `auto()` is a custom id, one the game does not have: the next from `CUSTOM_IDS_FROM` up after
    the custom ids defined before it. Its value depends on its place among them, so a custom id is never written down
    by value.
    """

    CUSTOM_IDS_FROM = nonmember(1_000_000)
    """The first custom id, above every id the game has."""

    _REMAPPED_IDS = nonmember(MappingProxyType[int, int]({}))
    """Game ids that are no member's own, each with the id of the member it reads as. An enum sets its own where the
    game has such ids."""

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: Sequence[int]) -> int:
        first = IdEnum.CUSTOM_IDS_FROM
        return max((value for value in last_values if value >= first), default=first - 1) + 1

    @property
    def is_custom(self) -> bool:
        """Whether this is a custom id: the game has none of it, and NachOS sends it as one of the game's."""
        return self >= self.CUSTOM_IDS_FROM

    @classmethod
    def read(cls, value: int) -> Self:
        """The member for `value`, or the member a remapped `value` reads as. Raises `UncuratedIdError` if there is
        none."""
        # The enum's own value-to-member map: under half the time of calling the enum.
        if (member := cls._value2member_map_.get(cls._REMAPPED_IDS.get(value, value))) is None:
            raise cls._unknown(value)
        return cast("Self", member)

    @classmethod
    def get(cls, value: int) -> Self | None:
        """The member for `value`, or the member a remapped `value` reads as; `None` if `value` is zero or there is
        none."""
        if not value:
            return None
        return cast("Self | None", cls._value2member_map_.get(cls._REMAPPED_IDS.get(value, value)))

    @classmethod
    def _unknown(cls, value: int) -> UnknownValueError:
        return UncuratedIdError(cls, value)
