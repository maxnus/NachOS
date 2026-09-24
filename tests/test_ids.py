"""Identifier enums: the curated public API and the raw catalog it is defined from."""

import importlib.util
from pathlib import Path

import pytest

from sc2nachos._enum import ReadableIntEnum
from sc2nachos.ids import AbilityId, BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawBuffId, RawEffectId, RawUnitTypeId, RawUpgradeId

CURATED = (UnitTypeId, AbilityId, UpgradeId, BuffId, EffectId)
RAW = (RawUnitTypeId, RawAbilityId, RawUpgradeId, RawBuffId, RawEffectId)
PAIRS = tuple(zip(CURATED, RAW, strict=True))


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
    """Curated modules define members from the raw catalog, never as literal ids.

    No game id is written as a number, so a patch that renumbers something needs a regeneration, not a hand-edit.
    """
    import inspect

    source = inspect.getsource(enum)
    assignments = [
        line.split("=", 1)[1].strip()
        for line in source.splitlines()
        if "=" in line and not line.strip().startswith(("#", '"', "'"))
    ]
    assert assignments, f"{enum.__name__} has no members"
    for assignment in assignments:
        assert assignment.startswith("Raw"), f"{enum.__name__} member assigned a literal: {assignment}"


@pytest.mark.parametrize(("curated", "raw"), PAIRS)
def test_curated_bridges_to_raw_by_value(curated: type[ReadableIntEnum], raw: type[ReadableIntEnum]) -> None:
    """A curated member equals its raw counterpart and interchanges with it as a mapping key."""
    for member in curated:
        counterpart = raw(int(member))
        assert member == counterpart
        assert {counterpart: "value"}[member] == "value"


@pytest.mark.parametrize(("curated", "raw"), PAIRS)
def test_curated_is_a_subset_of_the_catalog(curated: type[ReadableIntEnum], raw: type[ReadableIntEnum]) -> None:
    """Every curated id exists in the generated catalog — curation filters, it never invents."""
    catalog = {int(member) for member in raw}
    assert {int(member) for member in curated} <= catalog


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


def test_known_ids_have_expected_values() -> None:
    """A few ids pinned to the game's numbering, as a canary for a bad regeneration."""
    assert UnitTypeId.SCV == 45
    assert UnitTypeId.MARINE == 48
    assert UnitTypeId.COMMAND_CENTER == 18
    assert AbilityId.GENERAL_SMART == 1


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
