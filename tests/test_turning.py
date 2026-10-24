"""The turning tables: what `tools/generate_turning.py` makes of the findings of `tools/sweep_turning.py`."""

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sc2nachos.ids import UnitTypeId
from support import load_tool

_FINDINGS = Path(__file__).parents[1] / "data" / "turning.json"


def _generator() -> ModuleType:
    """The `tools/generate_turning.py` module."""
    return load_tool("generate_turning")


def _turn(off_facing_degrees: float, turned_degrees: list[float], steps_until_moving: int) -> dict[str, object]:
    """One turn as the sweep writes it, from the steps it turned in, each after the order's own."""
    turning_steps = [index for index, turned in enumerate(turned_degrees) if turned]
    return {
        "ordered_off_facing_degrees": off_facing_degrees,
        "start_facing": 0.0,
        "turned_degrees": turned_degrees,
        "steps_until_turned": turning_steps[-1] + 1 if turning_steps else None,
        "largest_turn_degrees": max((abs(turned) for turned in turned_degrees), default=None) or None,
        "steps_until_moving": steps_until_moving,
        "end_off_destination_degrees": 0.0,
    }


# A marine's turns, setting off while it turns, and a thor's, setting off once it faces where it goes (in game).
_MARINE_TURNS = [_turn(45.0, [0.0, 42.2, 0.0], 3), _turn(170.0, [0.0, 62.582, 52.3, 52.3, 0.0], 3)]
_THOR_TURNS = [_turn(45.0, [0.0, 45.0, 0.0], 3), _turn(170.0, [0.0, 45.04, 45.0, 40.0, 40.0, 0.0], 6)]
_COLOSSUS_TURNS = [_turn(45.0, [0.0, 0.0, 0.0], 3), _turn(170.0, [0.0, 0.0, 0.0], 3)]


def _shot(target: str, off_facing_degrees: float, turned_degrees: float, *, fired: bool = True) -> dict[str, object]:
    """One shot as the sweep writes it."""
    return {
        "target": target,
        "asked_off_facing_degrees": off_facing_degrees,
        "start_off_facing_degrees": off_facing_degrees,
        "distance": 5.0,
        "steps_until_shot": 4 if fired else None,
        "turned_degrees_before_shot": turned_degrees,
        "off_facing_at_shot_degrees": 0.0,
    }


def _findings(turning: dict[str, Any] | None = None, firing: dict[str, Any] | None = None) -> dict[str, object]:
    """Findings as the sweep writes them, of `turning` and `firing` alone."""
    return {"base_build": 1, "turning": turning or {}, "firing": firing or {}}


class TestReadingTheTurns:
    def test_a_types_rate_is_the_most_it_turned_in_a_step_to_a_tenth_of_a_degree(self) -> None:
        turning = _generator().read(_findings({"MARINE": _MARINE_TURNS, "THOR": _THOR_TURNS}))
        assert turning.turn_rate_degrees == {UnitTypeId.MARINE: 62.6, UnitTypeId.THOR: 45.0}

    def test_a_type_that_set_off_only_once_it_faced_where_it_went_is_set_apart(self) -> None:
        """A turn of one step shows nothing of it: every type sets off after that."""
        turning = _generator().read(_findings({"MARINE": _MARINE_TURNS, "THOR": _THOR_TURNS}))
        assert turning.set_off_only_facing == {UnitTypeId.THOR}

    def test_a_type_whose_facing_never_changed_is_left_out(self) -> None:
        turning = _generator().read(_findings({"COLOSSUS": _COLOSSUS_TURNS}))
        assert (turning.turn_rate_degrees, turning.set_off_only_facing) == ({}, frozenset())

    def test_a_type_that_turned_in_some_trials_and_not_others_is_refused(self) -> None:
        with pytest.raises(ValueError, match="turned in some trials"):
            _generator().read(_findings({"MARINE": [*_MARINE_TURNS, _COLOSSUS_TURNS[0]]}))

    def test_a_type_with_no_turn_of_two_steps_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no turn of a MARINE took two steps"):
            _generator().read(_findings({"MARINE": _MARINE_TURNS[:1]}))

    def test_a_type_the_sweep_could_not_turn_is_refused(self) -> None:
        with pytest.raises(ValueError, match="could not turn a MARINE"):
            _generator().read(_findings({"MARINE": {"error": "the game did not make a MARINE"}}))


class TestReadingTheShots:
    def test_a_type_that_turned_before_its_wide_shots_turns_to_fire_and_one_that_did_not_does_not(self) -> None:
        firing = {
            "MARINE": [_shot("SCV", 30.0, 30.0), _shot("SCV", 135.0, 134.9), _shot("SCV", 180.0, 179.9)],
            "PLANETARY_FORTRESS": [_shot("SCV", 135.0, 0.0), _shot("SCV", 180.0, 0.0)],
        }
        turning = _generator().read(_findings(firing=firing))
        assert turning.turns_to_fire_at_ground == {UnitTypeId.MARINE: True, UnitTypeId.PLANETARY_FORTRESS: False}
        assert turning.turns_to_fire_at_air == {}

    def test_a_shot_some_steps_short_of_facing_still_turned_to_fire(self) -> None:
        """A zealot fired some 10 degrees off a target 135 off (in game)."""
        turning = _generator().read(_findings(firing={"ZEALOT": [_shot("SCV", 135.0, 125.1)]}))
        assert turning.turns_to_fire_at_ground == {UnitTypeId.ZEALOT: True}

    def test_a_type_that_fired_at_no_wide_target_is_left_out(self) -> None:
        """As a sieged liberator, which fired at no target made around it (in game)."""
        firing = {"LIBERATOR_SIEGED": [_shot("SCV", 135.0, 0.0, fired=False)], "MARINE": [_shot("SCV", 30.0, 30.0)]}
        assert _generator().read(_findings(firing=firing)).turns_to_fire_at_ground == {}

    def test_a_type_that_turned_before_some_wide_shots_and_not_others_is_refused(self) -> None:
        firing = {"MARINE": [_shot("OVERLORD", 135.0, 134.9), _shot("OVERLORD", 180.0, 0.0)]}
        with pytest.raises(ValueError, match="turned before some shots at a OVERLORD"):
            _generator().read(_findings(firing=firing))

    def test_a_shot_the_sweep_could_not_set_up_is_refused(self) -> None:
        firing = {"MARINE": [{"target": "SCV", "asked_off_facing_degrees": 0.0, "error": "no SCV"}]}
        with pytest.raises(ValueError, match="could not set a MARINE upon a SCV"):
            _generator().read(_findings(firing=firing))


class TestTheModule:
    def test_it_holds_what_it_was_rendered_from(self) -> None:
        generator = _generator()
        firing = {"MARINE": [_shot("SCV", 135.0, 134.9)], "PHOENIX": [_shot("OVERLORD", 180.0, 0.0)]}
        turning = generator.read(_findings({"MARINE": _MARINE_TURNS, "THOR": _THOR_TURNS}, firing))
        namespace: dict[str, object] = {}
        exec(generator.render(turning, base_build=1), namespace)  # noqa: S102
        rendered = (
            namespace["TURN_RATE_DEGREES"],
            namespace["SET_OFF_ONLY_FACING"],
            namespace["TURNS_TO_FIRE_AT_GROUND"],
            namespace["TURNS_TO_FIRE_AT_AIR"],
        )
        assert rendered == (
            turning.turn_rate_degrees,
            turning.set_off_only_facing,
            turning.turns_to_fire_at_ground,
            turning.turns_to_fire_at_air,
        )

    def test_it_is_as_generated_from_the_findings(self) -> None:
        generator = _generator()
        findings = json.loads(_FINDINGS.read_text(encoding="utf-8"))
        written = generator.OUTPUT.read_text(encoding="utf-8")
        assert written == generator.render(generator.read(findings), findings["base_build"]), (
            "run tools/generate_turning.py"
        )
