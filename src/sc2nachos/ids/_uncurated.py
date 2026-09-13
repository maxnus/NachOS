"""Reading an id a game reported into its curated member."""

from sc2nachos._enum import ReadableIntEnum
from sc2nachos._errors import NachOSError
from sc2nachos.ids.ability import AbilityId
from sc2nachos.ids.buff import BuffId
from sc2nachos.ids.effect import EffectId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawEffectId, RawUnitTypeId, RawUpgradeId
from sc2nachos.ids.unit_type import UnitTypeId
from sc2nachos.ids.upgrade import UpgradeId

_CATALOGS: dict[type[ReadableIntEnum], type[ReadableIntEnum]] = {
    AbilityId: RawAbilityId,
    BuffId: RawBuffId,
    EffectId: RawEffectId,
    UnitTypeId: RawUnitTypeId,
    UpgradeId: RawUpgradeId,
}


class UncuratedIdError(NachOSError, ValueError):
    """A game reported an id the curated enum leaves out."""

    def __init__(self, enum: type[ReadableIntEnum], value: int) -> None:
        catalog = _CATALOGS[enum]
        try:
            name = str(catalog(value))
        except ValueError:
            name = f"not in {catalog.__name__} either"
        super().__init__(f"{enum.__name__} has no member for id {value} ({name})")
        self.enum = enum
        self.value = value


def read_id[IdT: ReadableIntEnum](enum: type[IdT], value: int) -> IdT:
    """The member of `enum` that a game's `value` names. Raises `UncuratedIdError` where the enum leaves it out."""
    try:
        return enum(value)
    except ValueError:
        raise UncuratedIdError(enum, value) from None
