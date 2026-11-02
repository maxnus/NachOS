"""The footprint table: what `tools/generate_footprints.py` makes of the findings of `tools/sweep_placement.py`."""

import json

from support import load_tool


def test_the_module_is_as_generated_from_the_findings() -> None:
    generator = load_tool("generate_footprints")
    findings = json.loads(generator.INPUT.read_text(encoding="utf-8"))
    footprints = generator.read(findings)
    requirements = generator.read_creep_requirements(findings)
    written = generator.OUTPUT.read_text(encoding="utf-8")
    assert written == generator.render(footprints, requirements, findings["base_build"]), (
        "run tools/generate_footprints.py"
    )
