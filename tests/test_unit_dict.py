"""A bot's own value for each unit, which forgets a unit once it is dead."""

from typing import Any

import pytest

from sc2nachos.ids import UnitTypeId
from sc2nachos.testing import MadeUpUnits, UnitReport
from sc2nachos.units import Alliance, OwnUnit, Unit, UnitDict

_SCV = UnitReport(1, UnitTypeId.SCV)
_OTHER_SCV = UnitReport(2, UnitTypeId.SCV, at=(12.0, 10.0))
_FIELD = UnitReport(3, UnitTypeId.MINERAL_FIELD, at=(15.0, 10.0), alliance=Alliance.NEUTRAL)


def test_a_unit_keeps_its_value_until_it_dies_then_reads_as_absent_wherever_it_is_read() -> None:
    units = MadeUpUnits()
    scv, field = units.observe(_SCV, _FIELD)
    mining: UnitDict[OwnUnit[Any], Unit[Any]] = UnitDict()
    mining[scv] = field
    assert (mining[scv], mining.get(scv), scv in mining) == (field, field, True)
    units.observe(_FIELD, dead=[_SCV])
    assert (mining.get(scv), mining.get(scv, "none"), scv in mining) == (None, "none", False)
    with pytest.raises(KeyError):
        _ = mining[scv]


def test_iterating_len_and_the_views_hold_the_living_units_alone() -> None:
    units = MadeUpUnits()
    scv, other_scv = units.observe(_SCV, _OTHER_SCV)
    roles: UnitDict[OwnUnit[Any], str] = UnitDict({scv: "mining", other_scv: "building"})
    items = roles.items()
    units.observe(_OTHER_SCV, dead=[_SCV])
    assert list(roles) == [other_scv]
    assert len(roles) == 1
    assert list(roles.keys()) == [other_scv]
    assert list(items) == [(other_scv, "building")]
    assert list(roles.values()) == ["building"]
    assert (scv, "mining") not in items
    assert repr(roles).startswith("UnitDict({") and "building" in repr(roles) and "mining" not in repr(roles)


def test_a_unit_the_observation_leaves_out_keeps_its_value() -> None:
    """As a worker inside a gas building is: left out, not dead."""
    units = MadeUpUnits()
    (scv,) = units.observe(_SCV)
    roles: UnitDict[OwnUnit[Any], str] = UnitDict([(scv, "collecting")])
    units.observe()
    assert not scv.is_listed
    assert roles[scv] == "collecting"


def test_a_dead_unit_stored_is_not_refused_and_no_read_sees_it() -> None:
    units = MadeUpUnits()
    (scv,) = units.observe(_SCV)
    units.observe(dead=[_SCV])
    roles: UnitDict[OwnUnit[Any], str] = UnitDict()
    roles[scv] = "mining"
    assert scv not in roles
    assert not roles
    assert roles.setdefault(scv, "building") == "building"
    assert roles.get(scv) is None


def test_a_value_of_none_is_a_value() -> None:
    units = MadeUpUnits()
    (scv,) = units.observe(_SCV)
    targets: UnitDict[OwnUnit[Any], Unit[Any] | None] = UnitDict({scv: None})
    assert scv in targets
    assert targets.get(scv, "absent") is None


def test_deleting_or_popping_a_dead_unit_finds_it_absent() -> None:
    units = MadeUpUnits()
    scv, other_scv = units.observe(_SCV, _OTHER_SCV)
    roles: UnitDict[OwnUnit[Any], str] = UnitDict({scv: "mining", other_scv: "building"})
    units.observe(_OTHER_SCV, dead=[_SCV])
    with pytest.raises(KeyError):
        del roles[scv]
    assert roles.pop(scv, "gone") == "gone"
    assert roles.pop(other_scv) == "building"
    assert not roles
