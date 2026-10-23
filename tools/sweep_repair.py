"""Find how long a repair takes and what it costs: one SCV repairs each Terran mechanical unit type from half health.

Needs StarCraft II installed. Plays one game as Terran against the easiest computer, left one engineering bay in the
middle of the map so that it can neither fight nor train, under the `all_resources` and `show_map` cheats alone, never
`free`, which would make the repair cost nothing (`docs/reference/game-behavior/cheats.md`), and with this player's
starting workers removed, so that no mining adds to the minerals. For each type in turn, every Terran type the game's
tables call mechanical unless types are named, it creates an SCV and the target beside it, sets the target's life to
half its maximum, orders the SCV to repair it, and records, every step, the target's health and this player's minerals
and vespene, until the target is whole or gone, or twice its build time, and at least 1000 steps, have passed. A target
the game reports flying is made again over the ground its SCV stands on, a gas building on a geyser of the map, and an
add-on the game does not create on its own is built on a structure made for it. It also records what the game answers an
SCV ordered to repair a marine, which is not mechanical, a supply depot another SCV is building, and a target whose life
was set with no observation asked for since::

    uv run python tools/sweep_repair.py
    uv run python tools/sweep_repair.py SIEGE_TANK MULE --out repair.json --samples samples.json

It writes `data/repair.json`, which `tools/generate_repair_steps.py` turns into NachOS's table, unless told otherwise:
the build swept on, and for each type what the tables say it costs, what the repair took, the step its health first
fell if it ever did, and the steps and resources a repair of its whole health comes to, from the rate between the
first health regained and the last before whole. `--samples` also writes every step's sample.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _sandbox import SUCCESS, Cheat, OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import debug_pb2, error_pb2, raw_pb2

from sc2nachos.gamedata import Attribute, GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawAbilityId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.units import UnitType

REPO = Path(__file__).parents[1]
OUTPUT = REPO / "data" / "repair.json"

# Ordered to repair, to record what the game answers for a unit that is not mechanical.
_NOT_REPAIRABLE = UnitTypeId.MARINE
# Repaired with its life set and no observation asked for since, to record what the game judges the order against.
_SET_WITHOUT_AN_OBSERVATION = UnitTypeId.MISSILE_TURRET
_STEPS_SINCE_THE_LIFE_WAS_SET = 8
# A supply depot under construction is ordered repaired this many steps after it appears, some way into its 480.
_STEPS_INTO_THE_CONSTRUCTION = 120
# The gas buildings, made on a geyser of the map of the kind each stands on.
_GAS_BUILDINGS = {
    UnitTypeId.REFINERY: frozenset(UnitType.AnyVespeneGeyser._type_ids) - {UnitTypeId.VESPENE_GEYSER_RICH},
    UnitTypeId.REFINERY_RICH: frozenset({UnitTypeId.VESPENE_GEYSER_RICH}),
}
# The add-ons the game creates only by building them on their structure, with the structure and the ability that
# builds each on it.
_ATTACHED_ADD_ONS = {
    UnitTypeId.REACTOR_BARRACKS: (UnitTypeId.BARRACKS, RawAbilityId.Build_Reactor_Barracks),
    UnitTypeId.REACTOR_FACTORY: (UnitTypeId.FACTORY, RawAbilityId.Build_Reactor_Factory),
    UnitTypeId.REACTOR_STARPORT: (UnitTypeId.STARPORT, RawAbilityId.Build_Reactor_Starport),
    UnitTypeId.TECH_LAB_BARRACKS: (UnitTypeId.BARRACKS, RawAbilityId.Build_TechLab_Barracks),
    UnitTypeId.TECH_LAB_FACTORY: (UnitTypeId.FACTORY, RawAbilityId.Build_TechLab_Factory),
    UnitTypeId.TECH_LAB_STARPORT: (UnitTypeId.STARPORT, RawAbilityId.Build_TechLab_Starport),
}
# A 3x3 structure stands this far left of the middle of its ground, so that it and its 2x2 add-on, 2.5 tiles to
# its right, are centered on it.
_STRUCTURE_LEFT_OF_THE_MIDDLE = 1.0
# How many tiles beyond its center each target's ground reaches: a 5x5 townhall, or a structure with its add-on, the
# most.
_WIDEST_GROUND_HALF_SIZE = 3
_DEFAULT_GROUND_HALF_SIZE = 2
# A repair is watched for at most this many times the target's build time, and at least this many steps, which an
# add-on, whose build time in the tables is 2 steps, needs.
_WATCH_BUILD_TIMES = 2.0
_MINIMUM_WATCH_STEPS = 1000
# The most steps a unit is waited for, to appear and to be finished, and the steps between looks.
_WAIT_STEPS = 1600
_WAIT_STEPS_BETWEEN_LOOKS = 4


@dataclass(slots=True)
class RepairSample:
    """The target's health and this player's resources at one step of a repair."""

    step: int
    health: float
    minerals: int
    vespene: int


@dataclass(slots=True)
class RepairFinding:
    """One target's repair: what the tables say of it, and what the repair took."""

    target: str
    health_max: float
    minerals_cost: float
    vespene_cost: float
    build_steps: float
    chain_build_steps: float
    order_result: str
    started_step: int | None = None
    finished_step: int | None = None
    health_fell_step: int | None = None
    health_repaired: float = 0.0
    minerals_spent: int = 0
    vespene_spent: int = 0
    steps_for_whole_health: float | None = None
    minerals_for_whole_health: float | None = None
    vespene_for_whole_health: float | None = None
    samples: list[RepairSample] = field(default_factory=list)


class RepairSweep:
    """The game a sweep plays, with the ground it puts targets on and the tables it reads."""

    def __init__(self, game: Sandbox) -> None:
        client = game.client
        self._game = game
        self._client = client
        self._player = game.player
        self.base_build = client.ping().base_build
        self.data = GameData(client.game_data())
        game.cheat(Cheat.ALL_RESOURCES, Cheat.SHOW_MAP)
        client.step(1)
        # Nothing guards this player's base.
        game.leave_the_computer_one_structure()
        # This player's starting workers mine, which would add to the minerals a repair spends.
        game.remove(
            unit.tag for unit in game.units() if unit.owner == self._player and unit.unit_type == UnitTypeId.SCV
        )
        observation = client.observation()
        units = list(observation.observation.raw_data.units)
        self._map = GameMap._from_game(client.game_info(), observation)
        self._ground = OpenGround(self._map, units)
        self._geysers = [unit for unit in units if unit.unit_type in UnitType.AnyVespeneGeyser._type_ids]

    def mechanical_types(self) -> list[UnitTypeId]:
        """Every Terran type the tables call mechanical, by name."""
        rows = self.data.units.items()
        mechanical = (
            unit_type for unit_type, row in rows if row.race == Race.TERRAN and Attribute.MECHANICAL in row.attributes
        )
        return sorted(mechanical, key=lambda unit_type: unit_type.name)

    def repair(self, target_type: UnitTypeId) -> RepairFinding:
        """Create an SCV and `target_type` beside it, halve the target's life, have the SCV repair it, and record the
        repair."""
        if target_type in _GAS_BUILDINGS:
            at, half_size, claimed = self._free_geyser(target_type), _WIDEST_GROUND_HALF_SIZE, False
        else:
            is_wide = target_type in UnitType.Townhall._type_ids or target_type in _ATTACHED_ADD_ONS
            half_size = _WIDEST_GROUND_HALF_SIZE if is_wide else _DEFAULT_GROUND_HALF_SIZE
            at, claimed = self._ground.claim(self._map.start_location, half_size), True
        made: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(target_type, at, half_size, made)
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(2)
            # An order is judged against the game as the last observation asked for saw it.
            finding = self._finding_for(target_type, self._report_of(target.tag))
            finding.order_result = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
            if finding.order_result == error_pb2.ActionResult.Name(SUCCESS):
                self._watch(finding, target.tag)
        finally:
            self._remove_those_listed(made)
            if claimed:
                self._ground.release(at)
        logger.info(
            "{}: {} health for {} minerals and {} vespene; a whole repair {} steps{}",
            finding.target,
            finding.health_repaired,
            finding.minerals_spent,
            finding.vespene_spent,
            None if finding.steps_for_whole_health is None else round(finding.steps_for_whole_health, 1),
            "" if finding.health_fell_step is None else f"; its health fell at step {finding.health_fell_step}",
        )
        return finding

    def refusal_for_a_unit_that_is_not_mechanical(self) -> str:
        """What the game answers an SCV ordered to repair a marine."""
        at = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(_NOT_REPAIRABLE, at, _DEFAULT_GROUND_HALF_SIZE, made)
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(2)
            self._client.observation()
            answer = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
        finally:
            self._remove_those_listed(made)
            self._ground.release(at)
        logger.info("A repair of a {} is answered {}", _NOT_REPAIRABLE.name, answer)
        return answer

    def answers_around_an_observation(self) -> dict[str, object]:
        """What the game answers an SCV ordered to repair a missile turret whose life was halved some steps before: with
        no observation asked for since, and then with one."""
        at = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(_SET_WITHOUT_AN_OBSERVATION, at, _DEFAULT_GROUND_HALF_SIZE, made)
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(_STEPS_SINCE_THE_LIFE_WAS_SET)
            without = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
            self._client.observation()
            after = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
        finally:
            self._remove_those_listed(made)
            self._ground.release(at)
        logger.info(
            "A repair ordered with no observation since the life was set is answered {}, after one {}", without, after
        )
        return {
            "target": _SET_WITHOUT_AN_OBSERVATION.name,
            "steps_since_the_life_was_set": _STEPS_SINCE_THE_LIFE_WAS_SET,
            "without_an_observation": without,
            "after_an_observation": after,
        }

    def refusal_for_a_structure_under_construction(self) -> dict[str, object]:
        """What the game answers an SCV ordered to repair a supply depot another SCV is building, with the depot's
        progress then."""
        at = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made: list[int] = []
        try:
            beside = _DEFAULT_GROUND_HALF_SIZE + 0.5
            scvs = self._game.spawn(
                [
                    (UnitTypeId.SCV, self._player, Point((at.x + beside, at.y))),
                    (UnitTypeId.SCV, self._player, Point((at.x - beside, at.y))),
                ]
            )
            made += [scv.tag for scv in scvs]
            if len(scvs) < 2:
                raise RuntimeError("the game did not create two SCVs")
            builder, repairer = scvs
            # A 2x2 structure centers on a tile's corner.
            depot_at = Point((at.x + 0.5, at.y + 0.5))
            if (answer := self._game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, builder.tag, depot_at)) != SUCCESS:
                raise RuntimeError(f"the SCV was refused the supply depot: {error_pb2.ActionResult.Name(answer)}")
            made.append(self._wait_for(UnitTypeId.SUPPLY_DEPOT).tag)
            self._client.step(_STEPS_INTO_THE_CONSTRUCTION)
            depot = self._report_of(made[-1])
            answer = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, repairer.tag, depot.tag))
        finally:
            self._remove_those_listed(made)
            self._ground.release(at)
        logger.info("A repair of a supply depot {:.0%} built is answered {}", depot.build_progress, answer)
        return {"target": UnitTypeId.SUPPLY_DEPOT.name, "build_progress": depot.build_progress, "order_result": answer}

    def _free_geyser(self, gas_building: UnitTypeId) -> Point:
        """A geyser of the kind `gas_building` stands on, far from both starts. Raises `RuntimeError` if the map has
        none."""
        kinds = _GAS_BUILDINGS[gas_building]
        geysers = [Point((unit.pos.x, unit.pos.y)) for unit in self._geysers if unit.unit_type in kinds]
        if not geysers:
            raise RuntimeError(f"the map has no geyser for a {gas_building.name}")
        starts = self._map.all_start_locations
        return max(geysers, key=lambda geyser: min(geyser.distance_to(start) for start in starts))

    def _spawn_target_and_scv(
        self, target_type: UnitTypeId, at: Point, half_size: int, made: list[int]
    ) -> tuple[raw_pb2.Unit, raw_pb2.Unit]:
        """An SCV created just beyond the edge of the ground at `at`, and the target: created at `at`, or, for a target
        the game reports flying, over the ground the SCV was put on, or, for an add-on the game creates only on its
        structure, built on one created for it. Made one at a time, so that an SCV repairing an SCV is told from its
        target, and each unit's tag added to `made` as it is made. Raises `RuntimeError` if the game made either
        not."""
        scvs = self._game.spawn([(UnitTypeId.SCV, self._player, Point((at.x + half_size + 0.5, at.y)))])
        made += [scv.tag for scv in scvs]
        if not scvs:
            raise RuntimeError("the game did not create an SCV")
        if target_type in _ATTACHED_ADD_ONS:
            return self._build_add_on(target_type, at, made), scvs[0]
        targets = self._game.spawn([(target_type, self._player, at)])
        made += [target.tag for target in targets]
        if targets and targets[0].is_flying:
            # An SCV kept from beneath an air unit, as by a command center under it, holds the order 32 to 48 steps and
            # drops it.
            self._game.remove([targets[0].tag])
            stands = scvs[0].pos
            targets = self._game.spawn([(target_type, self._player, Point((stands.x + 1.0, stands.y)))])
            made += [target.tag for target in targets]
        if not targets:
            raise RuntimeError(f"the game did not create a {target_type.name}")
        return targets[0], scvs[0]

    def _build_add_on(self, add_on_type: UnitTypeId, at: Point, made: list[int]) -> raw_pb2.Unit:
        """Create the structure `add_on_type` is built on, beside `at`, so that the two center on it, build the add-on
        on it, and return the add-on once it is finished. Adds each unit's tag to `made` as it is made. Raises
        `RuntimeError` if the game made either not."""
        structure_type, ability = _ATTACHED_ADD_ONS[add_on_type]
        structure_at = Point((at.x - _STRUCTURE_LEFT_OF_THE_MIDDLE, at.y))
        structures = self._game.spawn([(structure_type, self._player, structure_at)])
        made += [structure.tag for structure in structures]
        if not structures:
            raise RuntimeError(f"the game did not create a {structure_type.name}")
        if (answer := self._game.order(ability, structures[0].tag)) != SUCCESS:
            raise RuntimeError(
                f"the {structure_type.name} was refused its add-on: {error_pb2.ActionResult.Name(answer)}"
            )
        made.append(self._wait_for(add_on_type).tag)
        return self._wait_for(add_on_type, finished=True)

    def _wait_for(self, unit_type: UnitTypeId, *, finished: bool = False) -> raw_pb2.Unit:
        """This player's first unit of `unit_type`, or if `finished` its first one built, looked for every few steps.
        Never the placeholder the game lists for a structure ordered and not yet begun, which has a tag of its own.
        Raises `RuntimeError` if none comes in time."""
        for _ in range(0, _WAIT_STEPS, _WAIT_STEPS_BETWEEN_LOOKS):
            units = self._game.units()
            ours = (
                unit
                for unit in units
                if unit.owner == self._player and unit.unit_type == unit_type and unit.display_type == raw_pb2.Visible
            )
            if (found := next((unit for unit in ours if unit.build_progress == 1.0 or not finished), None)) is not None:
                return found
            self._client.step(_WAIT_STEPS_BETWEEN_LOOKS)
        raise RuntimeError(f"no {unit_type.name} came in {_WAIT_STEPS} steps")

    def _finding_for(self, target_type: UnitTypeId, target: raw_pb2.Unit) -> RepairFinding:
        """A finding for `target`, holding what the tables say of its type and the repair not yet watched."""
        row = self.data.units[target_type]
        chain_build_steps = 0.0
        stage: UnitTypeId | None = target_type
        while stage is not None:
            chain_build_steps += self.data.units[stage].build_steps
            stage = self.data.units[stage].morphed_from
        return RepairFinding(
            target=target_type.name,
            health_max=target.health_max,
            minerals_cost=row.cost.minerals,
            vespene_cost=row.cost.vespene,
            build_steps=row.build_steps,
            chain_build_steps=chain_build_steps,
            order_result="",
        )

    def _watch(self, finding: RepairFinding, tag: int) -> None:
        """Step one at a time and sample the target and the resources until it is whole or gone or the watch runs out,
        then work out what a repair of its whole health comes to."""
        watch_steps = max(_MINIMUM_WATCH_STEPS, round(_WATCH_BUILD_TIMES * finding.chain_build_steps))
        if (first := self._sample(tag)) is None:
            return
        samples = finding.samples
        samples.append(first)
        for _ in range(watch_steps):
            self._client.step(1)
            if (sample := self._sample(tag)) is None:
                # Gone, as a MULE or an auto-turret goes when its time is up.
                break
            samples.append(sample)
            if finding.health_fell_step is None and sample.health < samples[-2].health:
                finding.health_fell_step = sample.step
            if finding.started_step is None and sample.health > first.health:
                finding.started_step = samples[-2].step
            if sample.health >= finding.health_max:
                finding.finished_step = sample.step
                break
        last = samples[-1]
        finding.health_repaired = last.health - first.health
        finding.minerals_spent = first.minerals - last.minerals
        finding.vespene_spent = first.vespene - last.vespene
        rising = [sample for sample in samples if first.health < sample.health < finding.health_max]
        if len(rising) < 2 or rising[-1].step == rising[0].step:
            return
        rate = (rising[-1].health - rising[0].health) / (rising[-1].step - rising[0].step)
        finding.steps_for_whole_health = finding.health_max / rate
        share = finding.health_repaired / finding.health_max
        finding.minerals_for_whole_health = finding.minerals_spent / share
        finding.vespene_for_whole_health = finding.vespene_spent / share

    def _sample(self, tag: int) -> RepairSample | None:
        """The target `tag`'s health and this player's resources, from a fresh observation; `None` once it is gone."""
        observation = self._client.observation().observation
        common = observation.player_common
        health = next((unit.health for unit in observation.raw_data.units if unit.tag == tag), None)
        if health is None:
            return None
        return RepairSample(observation.game_loop, health, common.minerals, common.vespene)

    def _report_of(self, tag: int) -> raw_pb2.Unit:
        """The latest report of the unit `tag`, from a fresh observation."""
        return next(unit for unit in self._game.units() if unit.tag == tag)

    def _remove_those_listed(self, tags: Sequence[int]) -> None:
        """Remove each of the units `tags` that a fresh observation still lists."""
        listed = {unit.tag for unit in self._game.units()}
        self._game.remove(tag for tag in tags if tag in listed)


def sweep(installation: Installation, targets: Sequence[UnitTypeId]) -> tuple[dict[str, object], dict[str, object]]:
    """Repair each of `targets`, or every Terran mechanical type, in one game, and ask for the repairs the game
    refuses. Returns the findings and every step's samples, by type."""
    with playing(Race.TERRAN, installation) as game:
        run = RepairSweep(game)
        repairs: dict[str, object] = {}
        samples: dict[str, object] = {}
        for target in targets or run.mechanical_types():
            try:
                finding = asdict(run.repair(target))
            except RuntimeError as error:
                logger.warning("{}: {}", target.name, error)
                repairs[target.name] = {"error": str(error)}
                continue
            samples[target.name] = finding.pop("samples")
            repairs[target.name] = finding
        not_mechanical = run.refusal_for_a_unit_that_is_not_mechanical()
        under_construction = run.refusal_for_a_structure_under_construction()
        around_an_observation = run.answers_around_an_observation()
    findings = {
        "base_build": run.base_build,
        "repairs": repairs,
        "not_mechanical": {"target": _NOT_REPAIRABLE.name, "order_result": not_mechanical},
        "under_construction": under_construction,
        "around_an_observation": around_an_observation,
    }
    return findings, samples


def main(argv: Sequence[str]) -> None:
    """Sweep the types named, or every Terran mechanical type, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help="unit types to repair, as `UnitTypeId` names; every mechanical one")
    parser.add_argument("--out", type=Path, default=OUTPUT, help="where to write the findings")
    parser.add_argument("--samples", type=Path, default=None, help="where to write every step's samples, if anywhere")
    arguments = parser.parse_args(argv)
    if unknown := [name for name in arguments.targets if name not in UnitTypeId.__members__]:
        raise SystemExit(f"No unit type is called {', '.join(unknown)}")
    findings, samples = sweep(Installation.find(), [UnitTypeId[name] for name in arguments.targets])
    arguments.out.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)
    if arguments.samples is not None:
        arguments.samples.write_text(json.dumps(samples), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
