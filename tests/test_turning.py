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


def _turn(off_facing_degrees: float, turned_degrees: list[float], steps_until_moving: int | None) -> dict[str, object]:
    """One turn while moving as the sweep writes it, from the steps after the order up to the last it turned in."""
    turning_indexes = [index for index, turned_that_step in enumerate(turned_degrees) if turned_that_step]
    return {
        "ordered_off_facing_degrees": off_facing_degrees,
        "start_facing": 0.0,
        "turned_degrees": turned_degrees,
        "turning_steps": len(turning_indexes) or None,
        "steps_until_turned": turning_indexes[-1] + 1 if turning_indexes else None,
        "largest_turn_degrees": max((abs(turned) for turned in turned_degrees), default=None) or None,
        "steps_until_moving": steps_until_moving,
    }


def _turn_in_place(off_facing_degrees: float, turned_degrees: list[float]) -> dict[str, object]:
    """One turn in place as the sweep writes it."""
    return {
        "target": "SCV",
        "asked_off_facing_degrees": off_facing_degrees,
        "start_off_facing_degrees": off_facing_degrees,
        "turned_degrees": turned_degrees,
        "turning_steps": len([turned for turned in turned_degrees if turned]) or None,
    }


# Turns while moving, in the steps the game gave them (in game). A marine sets off while it turns, its full steps
# 711 and 712 of 4096 a turn; a thor only once it faces where it goes, its 512; a colossus never turns.
_MARINE_TURNS = [
    _turn(45.0, [0.0, 42.2], 3),
    _turn(160.0, [0.0, 62.582, 48.7, 48.7], 3),
    _turn(170.0, [0.0, 62.49, 53.8, 53.8], 3),
]
_THOR_TURNS = [_turn(45.0, [0.0, 45.0], 3), _turn(170.0, [0.0, 45.0, 45.0, 40.0, 40.0], 6)]
_COLOSSUS_TURNS = [_turn(45.0, [], 3), _turn(170.0, [], 3)]
# A banshee never takes three steps to turn while moving, and turns in place 93.75 degrees in one step and 94 in two,
# the first of them 93.832, 1067.6 units (in game).
_BANSHEE_TURNS = [_turn(45.0, [0.0, 42.2], 3), _turn(170.0, [0.0, 83.3, 83.2], 3)]
_BANSHEE_TURNS_IN_PLACE = [_turn_in_place(93.75, [93.748]), _turn_in_place(94.0, [93.832, 0.14])]
# A phoenix neither turns in place nor takes three steps to turn: the most it turned in one step was 93 (in game).
_PHOENIX_TURNS = [_turn(96.0, [0.0, 93.0], 3), _turn(170.0, [0.0, 83.4, 83.3], 3)]


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


def _findings(
    turning: dict[str, Any] | None = None,
    firing: dict[str, Any] | None = None,
    in_place: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Findings as the sweep writes them, of `turning`, `firing` and `in_place` alone."""
    return {"base_build": 1, "turning": turning or {}, "firing": firing or {}, "in_place": in_place or {}}


class TestReadingTheTurns:
    def test_a_rate_is_the_mean_of_its_full_steps_rounded_down_to_a_whole_unit(self) -> None:
        """A full step comes out at the rate or a unit or so above it, in 1/4096 of a full turn: a marine's 711.0 and
        712.1 make 711."""
        turning = _generator().read(_findings({"MARINE": _MARINE_TURNS, "THOR": _THOR_TURNS}))
        assert turning.turn_rate_units == {UnitTypeId.MARINE: 711, UnitTypeId.THOR: 512}

    def test_a_turn_in_place_gives_the_full_steps_of_a_type_that_never_takes_three_while_moving(self) -> None:
        findings = _findings({"BANSHEE": _BANSHEE_TURNS}, in_place={"BANSHEE": _BANSHEE_TURNS_IN_PLACE})
        assert _generator().read(findings).turn_rate_units == {UnitTypeId.BANSHEE: 1067}

    def test_a_type_with_no_full_step_takes_the_slowest_rate_no_less_than_its_largest_step(self) -> None:
        """A phoenix's 93 degrees in a step is 1058 units, of which a banshee's 1067 is the slowest rate above."""
        turning_findings = {"MARINE": _MARINE_TURNS, "BANSHEE": _BANSHEE_TURNS, "PHOENIX": _PHOENIX_TURNS}
        turning = _generator().read(_findings(turning_findings, in_place={"BANSHEE": _BANSHEE_TURNS_IN_PLACE}))
        assert turning.turn_rate_units[UnitTypeId.PHOENIX] == 1067

    def test_a_type_with_no_full_step_faster_than_every_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="PHOENIX has no full step and turned faster"):
            _generator().read(_findings({"MARINE": _MARINE_TURNS, "PHOENIX": _PHOENIX_TURNS}))

    def test_a_type_that_turned_before_it_moved_is_set_apart(self) -> None:
        """A turn of one step shows nothing of it: every type moves after that."""
        turning = _generator().read(_findings({"MARINE": _MARINE_TURNS, "THOR": _THOR_TURNS}))
        assert turning.turn_before_move == {UnitTypeId.THOR}

    def test_a_type_whose_facing_never_changed_is_left_out(self) -> None:
        turning = _generator().read(_findings({"COLOSSUS": _COLOSSUS_TURNS}))
        assert (turning.turn_rate_units, turning.turn_before_move) == ({}, frozenset())

    def test_a_type_that_turned_in_some_trials_and_not_others_is_refused(self) -> None:
        with pytest.raises(ValueError, match="turned in some trials"):
            _generator().read(_findings({"MARINE": [*_MARINE_TURNS, _COLOSSUS_TURNS[0]]}))

    def test_a_type_that_never_set_off_is_refused(self) -> None:
        held_up = [*_MARINE_TURNS, _turn(90.0, [0.0, 43.6, 43.7], None)]
        with pytest.raises(ValueError, match="never set off when ordered to move 90.0 degrees"):
            _generator().read(_findings({"MARINE": held_up}))

    def test_a_type_with_no_turn_of_two_steps_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no turn of a MARINE took two steps"):
            _generator().read(_findings({"MARINE": _MARINE_TURNS[:1]}))

    def test_a_type_the_sweep_could_not_turn_is_refused(self) -> None:
        with pytest.raises(ValueError, match="could not turn a MARINE"):
            _generator().read(_findings({"MARINE": {"error": "the game did not make a MARINE"}}))


class TestReadingTheShots:
    def test_a_type_that_fired_without_turning_at_wide_targets_is_set_apart(self) -> None:
        """At the ground and in the air alike; one that turned is not."""
        firing = {
            "MARINE": [_shot("SCV", 135.0, 134.9), _shot("OVERLORD", 180.0, 179.9)],
            "PLANETARY_FORTRESS": [_shot("SCV", 135.0, 0.0), _shot("SCV", 180.0, 0.0)],
            "MISSILE_TURRET": [_shot("OVERLORD", 135.0, 0.0)],
        }
        fire_without_turning = _generator().read(_findings(firing=firing)).fire_without_turning
        assert fire_without_turning == {UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.MISSILE_TURRET}

    def test_a_shot_some_steps_short_of_facing_still_turned_to_fire(self) -> None:
        """A zealot fired some 10 degrees off a target 135 off (in game)."""
        firing = {"ZEALOT": [_shot("SCV", 135.0, 125.1)]}
        assert _generator().read(_findings(firing=firing)).fire_without_turning == frozenset()

    def test_a_type_that_fired_at_no_wide_target_is_taken_to_turn(self) -> None:
        """As a sieged liberator, which fired at no target made around it (in game)."""
        firing = {"LIBERATOR_SIEGED": [_shot("SCV", 135.0, 0.0, fired=False)]}
        assert _generator().read(_findings(firing=firing)).fire_without_turning == frozenset()

    def test_a_type_that_turned_before_some_wide_shots_and_not_others_is_refused(self) -> None:
        firing = {"MARINE": [_shot("OVERLORD", 135.0, 134.9), _shot("OVERLORD", 180.0, 0.0)]}
        with pytest.raises(ValueError, match="turned before some shots at a OVERLORD"):
            _generator().read(_findings(firing=firing))

    def test_a_type_that_turned_to_fire_at_one_target_and_not_the_other_is_refused(self) -> None:
        firing = {"VOID_RAY": [_shot("SCV", 135.0, 134.9), _shot("OVERLORD", 135.0, 0.0)]}
        with pytest.raises(ValueError, match="turned to fire at one target and not at another"):
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
        namespace: dict[str, Any] = {}
        exec(generator.render(turning, base_build=1), namespace)  # noqa: S102
        rendered_rates = {
            unit_type: rate_units
            for rate_units, unit_types in namespace["TURN_RATE_UNITS"].items()
            for unit_type in unit_types
        }
        rendered = (rendered_rates, namespace["TURN_BEFORE_MOVE_UNITS"], namespace["FIRE_WITHOUT_TURNING_UNITS"])
        assert rendered == (turning.turn_rate_units, turning.turn_before_move, turning.fire_without_turning)

    def test_it_is_as_generated_from_the_findings(self) -> None:
        generator = _generator()
        findings = json.loads(_FINDINGS.read_text(encoding="utf-8"))
        written = generator.OUTPUT.read_text(encoding="utf-8")
        assert written == generator.render(generator.read(findings), findings["base_build"]), (
            "run tools/generate_turning.py"
        )
