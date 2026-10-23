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
    def test_a_types_whole_repair_is_read_to_the_nearest_step_under_its_curated_id(self) -> None:
        assert _generator().read(_findings(SIEGE_TANK=_siege_tank_repair())) == {UnitTypeId.SIEGE_TANK: 726}

    def test_a_type_the_sweep_could_not_make_is_refused(self) -> None:
        with pytest.raises(ValueError, match="could not repair a SIEGE_TANK"):
            _generator().read(_findings(SIEGE_TANK={"error": "the game did not create a SIEGE_TANK"}))

    def test_a_repair_not_measured_is_refused(self) -> None:
        repair = _siege_tank_repair(order_result="NotSupported", steps_for_whole_health=None)
        with pytest.raises(ValueError, match="no repair of a SIEGE_TANK was measured: NotSupported"):
            _generator().read(_findings(SIEGE_TANK=repair))

    def test_a_target_whose_health_fell_while_watched_is_refused(self) -> None:
        """Something shot it, and its rate and cost are no repair's alone."""
        with pytest.raises(ValueError, match="fell at step 40"):
            _generator().read(_findings(SIEGE_TANK=_siege_tank_repair(health_fell_step=40)))

    @pytest.mark.parametrize(("minerals", "vespene"), [(38, 32), (18, 16), (19, 15)])
    def test_a_repair_that_did_not_cost_a_quarter_rounded_up_is_refused(self, minerals: int, vespene: int) -> None:
        """Half of a quarter of 150 minerals and 125 vespene is 18.75 and 15.625: neither the whole cost's half, nor
        rounded down."""
        repair = _siege_tank_repair(minerals_spent=minerals, vespene_spent=vespene)
        with pytest.raises(ValueError, match="not a quarter of its cost, \\(19, 16\\)"):
            _generator().read(_findings(SIEGE_TANK=repair))


class TestTheModule:
    def test_it_holds_what_it_was_rendered_from(self) -> None:
        generator = _generator()
        repair_steps = generator.read(_findings(SIEGE_TANK=_siege_tank_repair()))
        namespace: dict[str, object] = {}
        exec(generator.render(repair_steps, base_build=1), namespace)  # noqa: S102
        assert (namespace["REPAIR_STEPS"], namespace["REPAIR_COST_SHARE"]) == (repair_steps, 0.25)

    def test_it_is_as_generated_from_the_findings(self) -> None:
        generator = _generator()
        findings = json.loads(_FINDINGS.read_text(encoding="utf-8"))
        written = generator.OUTPUT.read_text(encoding="utf-8")
        assert written == generator.render(generator.read(findings), findings["base_build"]), (
            "run tools/generate_repair_steps.py"
        )
