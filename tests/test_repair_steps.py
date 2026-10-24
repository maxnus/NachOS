"""The repair table: what `tools/generate_repair_steps.py` makes of the findings of `tools/sweep_repair.py`."""

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sc2nachos.ids import UnitTypeId
from support import load_tool

_FINDINGS = Path(__file__).parents[1] / "data" / "repair.json"


def _generator() -> ModuleType:
    """The `tools/generate_repair_steps.py` module."""
    return load_tool("generate_repair_steps")


def _siege_tank_repair(**changes: Any) -> dict[str, object]:
    """A siege tank's repair from half health as the sweep writes it, but for `changes`."""
    repair: dict[str, object] = {
        "target": "SIEGE_TANK",
        "health_max": 175.0,
        "minerals_cost": 150,
        "vespene_cost": 125,
        "cost_steps": 720.0,
        "order_result": "Success",
        "health_fell_step": None,
        "health_repaired": 87.5,
        "minerals_spent": 19,
        "vespene_spent": 16,
        "steps_for_whole_health": 726.0169969445902,
    }
    return repair | changes


def _findings(**repairs: dict[str, object]) -> dict[str, object]:
    """Findings as the sweep writes them, of `repairs` alone."""
    return {"base_build": 1, "repairs": repairs}


class TestReading:
    def test_a_type_repaired_in_the_steps_its_type_costs_is_left_out(self) -> None:
        """726 measured of the 720 it costs is within the measurement's error."""
        assert _generator().read(_findings(SIEGE_TANK=_siege_tank_repair())) == {}

    def test_a_type_repaired_in_other_steps_is_kept_to_the_nearest_step_under_its_curated_id(self) -> None:
        """As a widow mine, which costs 480 steps, is repaired in 320 (in game)."""
        repair_off_its_cost = _siege_tank_repair(cost_steps=480.0)
        assert _generator().read(_findings(SIEGE_TANK=repair_off_its_cost)) == {UnitTypeId.SIEGE_TANK: 726}

    @pytest.mark.parametrize(("cost_steps", "is_kept"), [(726.0169969445902 - 16, False), (709.5, True)])
    def test_a_type_is_kept_only_when_its_repair_is_more_than_16_steps_off_what_it_costs(
        self, cost_steps: float, is_kept: bool
    ) -> None:
        repair_off_its_cost = _siege_tank_repair(cost_steps=cost_steps)
        assert (UnitTypeId.SIEGE_TANK in _generator().read(_findings(SIEGE_TANK=repair_off_its_cost))) is is_kept

    def test_findings_from_before_the_steps_its_type_costs_were_recorded_are_refused(self) -> None:
        repair_without_cost_steps = _siege_tank_repair()
        del repair_without_cost_steps["cost_steps"]
        with pytest.raises(ValueError, match="predate `cost_steps`"):
            _generator().read(_findings(SIEGE_TANK=repair_without_cost_steps))

    def test_a_type_the_sweep_could_not_make_is_refused(self) -> None:
        with pytest.raises(ValueError, match="could not repair a SIEGE_TANK"):
            _generator().read(_findings(SIEGE_TANK={"error": "the game did not create a SIEGE_TANK"}))

    def test_a_repair_not_measured_is_refused(self) -> None:
        unmeasured_repair = _siege_tank_repair(order_result="NotSupported", steps_for_whole_health=None)
        with pytest.raises(ValueError, match="no repair of a SIEGE_TANK was measured: NotSupported"):
            _generator().read(_findings(SIEGE_TANK=unmeasured_repair))

    def test_a_target_whose_health_fell_while_watched_is_refused(self) -> None:
        """Something shot it, and its rate and cost are no repair's alone."""
        with pytest.raises(ValueError, match="fell at step 40"):
            _generator().read(_findings(SIEGE_TANK=_siege_tank_repair(health_fell_step=40)))

    @pytest.mark.parametrize(("minerals", "vespene"), [(38, 32), (18, 16), (19, 15)])
    def test_a_repair_that_did_not_cost_a_quarter_rounded_up_is_refused(self, minerals: int, vespene: int) -> None:
        """Half of a quarter of 150 minerals and 125 vespene is 18.75 and 15.625, which round up to 19 and 16: not the
        38 and 32 of a whole repair, and not rounded down."""
        mispriced_repair = _siege_tank_repair(minerals_spent=minerals, vespene_spent=vespene)
        with pytest.raises(ValueError, match="not a quarter of its cost, \\(19, 16\\)"):
            _generator().read(_findings(SIEGE_TANK=mispriced_repair))


class TestTheModule:
    def test_it_holds_what_it_was_rendered_from(self) -> None:
        generator = _generator()
        repair_steps = generator.read(_findings(SIEGE_TANK=_siege_tank_repair(cost_steps=480.0)))
        namespace: dict[str, object] = {}
        exec(generator.render(repair_steps, base_build=1), namespace)  # noqa: S102
        assert (namespace["REPAIR_STEPS_EXCEPTIONS"], namespace["REPAIR_COST_FRACTION"]) == (repair_steps, 0.25)

    def test_it_is_as_generated_from_the_findings(self) -> None:
        generator = _generator()
        findings = json.loads(_FINDINGS.read_text(encoding="utf-8"))
        written = generator.OUTPUT.read_text(encoding="utf-8")
        assert written == generator.render(generator.read(findings), findings["base_build"]), (
            "run tools/generate_repair_steps.py"
        )
