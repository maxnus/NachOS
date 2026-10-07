"""Identifier enums: the curated public API and the raw catalog it is defined from."""

import importlib.util
import re
from enum import auto
from pathlib import Path

import pytest

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.ids import AbilityId, BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.ids._id_enum import IdEnum
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawEffectId, RawUnitTypeId, RawUpgradeId

CURATED = (UnitTypeId, AbilityId, UpgradeId, BuffId, EffectId)
RAW = (RawUnitTypeId, RawAbilityId, RawUpgradeId, RawBuffId, RawEffectId)
PAIRS = tuple(zip(CURATED, RAW, strict=True))
# A member's line in an enum's source, and what it is assigned.
_MEMBER = re.compile(r"^    [A-Z][A-Z0-9_]* = (.+)$")


def _game_ids(enum: type[IdEnum]) -> list[IdEnum]:
    """The members of `enum` the game has, leaving out custom ids."""
    return [member for member in enum if not member.is_custom]


@pytest.mark.parametrize("enum", CURATED + RAW)
def test_ids_are_readable_int_enums(enum: type[ReadableIntEnum]) -> None:
    """Every id enum is an int enum that prints as its name."""
    assert issubclass(enum, ReadableIntEnum)
    member = next(iter(enum))
    assert isinstance(member, int)
    assert str(member) == f"{enum.__name__}.{member.name}"
    assert f"{member}" == str(member)
    assert f"{member:d}" == str(int(member))


@pytest.mark.parametrize("enum", CURATED)
def test_curated_members_are_not_bare_integers(enum: type[ReadableIntEnum]) -> None:
    """Curated modules define members from the raw catalog, never as literal ids, and custom ids by `auto()`, which
    numbers them above the game's.

    No game id is written as a number, so a patch that renumbers something needs a regeneration, not a hand-edit.
    """
    import inspect

    source = inspect.getsource(enum)
    assignments = [member[1].strip() for line in source.splitlines() if (member := _MEMBER.match(line))]
    assert assignments, f"{enum.__name__} has no members"
    for assignment in assignments:
        literal = f"{enum.__name__} member assigned a literal: {assignment}"
        assert assignment.startswith("Raw") or assignment == "auto()", literal


@pytest.mark.parametrize(("curated", "raw"), PAIRS)
def test_curated_bridges_to_raw_by_value(curated: type[IdEnum], raw: type[ReadableIntEnum]) -> None:
    """A curated member equals its raw counterpart and interchanges with it as a mapping key."""
    for member in _game_ids(curated):
        counterpart = raw(int(member))
        assert member == counterpart
        assert {counterpart: "value"}[member] == "value"


@pytest.mark.parametrize(("curated", "raw"), PAIRS)
def test_curated_is_a_subset_of_the_catalog(curated: type[IdEnum], raw: type[ReadableIntEnum]) -> None:
    """Every curated id the game has exists in the generated catalog — curation filters, and invents only custom
    abilities."""
    catalog = {int(member) for member in raw}
    assert {int(member) for member in _game_ids(curated)} <= catalog


def test_auto_numbers_custom_ids_after_each_other_whatever_game_ids_lie_between() -> None:
    class Sample(IdEnum):
        GAME = RawAbilityId.Smart
        FIRST = auto()
        OTHER_GAME = RawAbilityId.Stop
        SECOND = auto()

    assert (Sample.FIRST, Sample.SECOND) == (IdEnum.CUSTOM_IDS_FROM, IdEnum.CUSTOM_IDS_FROM + 1)
    assert Sample.FIRST.is_custom and not Sample.GAME.is_custom


def test_custom_abilities_lie_above_every_game_id() -> None:
    custom = [member for member in AbilityId if member.is_custom]
    assert custom, "there are custom abilities"
    assert min(custom) > max(int(member) for member in RawAbilityId)


@pytest.mark.parametrize("enum", CURATED + RAW)
def test_ids_are_unique(enum: type[ReadableIntEnum]) -> None:
    """No two names share an id.

    An `IntEnum` makes a second name for an id an alias of the first, so two spellings of one ability would read as
    the same member instead of failing.
    """
    assert len(enum.__members__) == len({int(member) for member in enum}), f"{enum.__name__} has aliased members"


@pytest.mark.parametrize("enum", CURATED)
def test_unknown_ids_raise(enum: type[ReadableIntEnum]) -> None:
    """An id outside the curated set raises instead of resolving to a fallback.

    A missing id is a gap to fill, so it fails loudly. A permissive mode, if ever wanted, goes in `_missing_`.
    """
    unknown = max(int(member) for member in enum) + 10_000
    with pytest.raises(ValueError):
        enum(unknown)


@pytest.mark.parametrize(
    ("reported", "ordered"),
    [
        (RawAbilityId.LiberatorMorphtoAG_LiberatorAGMode, AbilityId.SIEGE),
        (RawAbilityId.LiberatorMorphtoAA_LiberatorAAMode, AbilityId.UNSIEGE),
        (RawAbilityId.SiegeMode_SiegeMode, AbilityId.SIEGE),
        (RawAbilityId.Morph_ObserverMode, AbilityId.UNSIEGE),
        (RawAbilityId.Archon_Warp_Target, AbilityId.MORPH_ARCHON),
    ],
)
def test_an_id_a_unit_reports_for_another_reads_as_the_one_ordered(reported: RawAbilityId, ordered: AbilityId) -> None:
    """Read and got, it is the member; called, it is not one, since the game's tables hold a row of its own."""
    assert AbilityId.read(reported) is ordered
    assert AbilityId.get(reported) is ordered
    with pytest.raises(ValueError):
        AbilityId(reported)


@pytest.mark.parametrize("enum", CURATED)
def test_a_remapped_id_is_no_members_own_and_reads_as_a_member(enum: type[IdEnum]) -> None:
    """Calling the enum must not answer a remapped id, since the game's tables hold a row of its own under it."""
    for remapped, own in enum._REMAPPED_IDS.items():
        assert remapped not in enum._value2member_map_
        assert own in enum._value2member_map_


def test_known_ids_have_expected_values() -> None:
    """A few ids pinned to the game's numbering, as a canary for a bad regeneration."""
    assert UnitTypeId.SCV == 45
    assert UnitTypeId.MARINE == 48
    assert UnitTypeId.COMMAND_CENTER == 18
    assert AbilityId.SMART == 1


def test_renaming_preserves_identity() -> None:
    """A curated name may differ from Blizzard's and still be the same id."""
    assert RawUnitTypeId.LurkerMP == UnitTypeId.LURKER
    assert RawUnitTypeId.Lurker != UnitTypeId.LURKER


def test_a_buff_is_the_id_the_game_puts_on_units() -> None:
    """Where the catalog has two spellings for one buff, the curated name is the one the game reports (in game).

    Concussive shells put `Slow` on their target, never `DutchMarauderSlow`, and an immortal's Barrier is
    `TakenDamage`, never `ImmortalOverload`.
    """
    assert RawBuffId.Slow == BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW
    assert RawBuffId.TakenDamage == BuffId.IMMORTAL_BARRIER
    assert RawBuffId.DutchMarauderSlow.value not in {int(member) for member in BuffId}
    assert RawBuffId.ImmortalOverload.value not in {int(member) for member in BuffId}


def test_the_raw_catalog_is_as_generated_from_stableid() -> None:
    path = Path(__file__).parents[1] / "tools" / "generate_ids.py"
    spec = importlib.util.spec_from_file_location("generate_ids", path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    for module, text in generator.render().items():
        assert module.read_text(encoding="utf-8") == text, "run tools/generate_ids.py"
