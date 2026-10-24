"""Find how long a repair takes and what it costs: one SCV repairs each Terran mechanical unit type from half health.

Needs StarCraft II installed. Plays one game as Terran against the easiest computer, left one engineering bay in the
middle of the map so that it can neither fight nor train, under the `all_resources` and `show_map` cheats alone, never
`free`, which would make the repair cost nothing (`docs/reference/game-behavior/cheats.md`), and with this player's
starting workers removed, so that no mining adds to the minerals. For each type in turn, every Terran type the game's
tables call mechanical unless types are named, it creates an SCV and the target beside it, sets the target's life to
half its maximum, orders the SCV to repair it, and records, every step, the target's health and this player's minerals
and vespene, until the target is whole or gone, or twice the steps its type costs, and at least 1000 steps, have passed.
A target the game reports flying is made again over the ground its SCV stands on, a gas building on a geyser of the map,
and an add-on the game does not create on its own is built on a structure made for it. It also records what the game
answers an SCV ordered to repair a marine, which is not mechanical, a supply depot another SCV is building, and a target
whose life was set with no observation asked for since::

    uv run python tools/sweep_repair.py
    uv run python tools/sweep_repair.py SIEGE_TANK MULE --out repair.json --samples samples.json

It writes `data/repair.json`, which `tools/generate_repair_steps.py` turns into NachOS's table, or for a sweep of the
types named, the file `--out` names, which it then needs: the build swept on, and for each type what the tables say it
costs, what the repair took, the step its health first fell if it ever did, and the steps and resources a repair of its
whole health comes to, from the rate between the first health regained and the last before whole. `--samples` also
writes every step's sample.
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
# A repair is watched for at most this many times the steps the target's type costs, and at least this many steps,
# which an add-on, whose build time in the tables is 2 steps, needs.
_WATCH_COST_STEPS_TIMES = 2.0
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
    cost_steps: float
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
        """Ready `game` for repairs: turn on `all_resources` and `show_map`, leave the computer one structure, remove
        this player's starting SCVs, and note the free ground and the geysers."""
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
        units_left = list(observation.observation.raw_data.units)
        self._map = GameMap._from_game(client.game_info(), observation)
        self._ground = OpenGround(self._map, units_left)
        self._geysers = [unit for unit in units_left if unit.unit_type in UnitType.AnyVespeneGeyser._type_ids]

    def mechanical_types(self) -> list[UnitTypeId]:
        """Every Terran type the tables call mechanical, by name."""
        table_rows = self.data.units.items()
        mechanical_types = (
            unit_type
            for unit_type, row in table_rows
            if row.race == Race.TERRAN and Attribute.MECHANICAL in row.attributes
        )
        return sorted(mechanical_types, key=lambda unit_type: unit_type.name)

    def repair_from_half_health(self, target_type: UnitTypeId) -> RepairFinding:
        """Create an SCV and `target_type` beside it, halve the target's life, have the SCV repair it, and record the
        repair."""
        if target_type in _GAS_BUILDINGS:
            ground_middle, half_size, ground_claimed = self._free_geyser(target_type), _WIDEST_GROUND_HALF_SIZE, False
        else:
            needs_wide_ground = target_type in UnitType.Townhall._type_ids or target_type in _ATTACHED_ADD_ONS
            half_size = _WIDEST_GROUND_HALF_SIZE if needs_wide_ground else _DEFAULT_GROUND_HALF_SIZE
            ground_middle, ground_claimed = self._ground.claim(self._map.start_location, half_size), True
        made_tags: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(target_type, ground_middle, half_size, made_tags)
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(2)
            # An order is judged against a unit's life as the last observation asked for saw it.
            finding = self._finding_for(target_type, self._report_of(target.tag))
            finding.order_result = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
            if finding.order_result == error_pb2.ActionResult.Name(SUCCESS):
                self._watch(finding, target.tag)
        finally:
            self._remove_those_listed(made_tags)
            if ground_claimed:
                self._ground.release(ground_middle)
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

    def refusal_for_a_unit_that_is_not_mechanical(self) -> dict[str, object]:
        """What the game answers an SCV ordered to repair a marine."""
        ground_middle = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made_tags: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(
                _NOT_REPAIRABLE, ground_middle, _DEFAULT_GROUND_HALF_SIZE, made_tags
            )
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(2)
            self._client.observation()
            repair_answer = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, scv.tag, target.tag))
        finally:
            self._remove_those_listed(made_tags)
            self._ground.release(ground_middle)
        logger.info("A repair of a {} is answered {}", _NOT_REPAIRABLE.name, repair_answer)
        return {"target": _NOT_REPAIRABLE.name, "order_result": repair_answer}

    def answers_around_an_observation(self) -> dict[str, object]:
        """What the game answers an SCV ordered to repair a missile turret whose life was halved some steps before: with
        no observation asked for since, and then with one."""
        ground_middle = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made_tags: list[int] = []
        try:
            target, scv = self._spawn_target_and_scv(
                _SET_WITHOUT_AN_OBSERVATION, ground_middle, _DEFAULT_GROUND_HALF_SIZE, made_tags
            )
            self._game.set_value(debug_pb2.DebugSetUnitValue.Life, target.health_max / 2, [target.tag])
            self._client.step(_STEPS_SINCE_THE_LIFE_WAS_SET)
            answer_without_an_observation = error_pb2.ActionResult.Name(
                self._game.order(AbilityId.REPAIR, scv.tag, target.tag)
            )
            self._client.observation()
            answer_after_an_observation = error_pb2.ActionResult.Name(
                self._game.order(AbilityId.REPAIR, scv.tag, target.tag)
            )
        finally:
            self._remove_those_listed(made_tags)
            self._ground.release(ground_middle)
        logger.info(
            "A repair ordered with no observation since the life was set is answered {}, after one {}",
            answer_without_an_observation,
            answer_after_an_observation,
        )
        return {
            "target": _SET_WITHOUT_AN_OBSERVATION.name,
            "steps_since_the_life_was_set": _STEPS_SINCE_THE_LIFE_WAS_SET,
            "without_an_observation": answer_without_an_observation,
            "after_an_observation": answer_after_an_observation,
        }

    def refusal_for_a_structure_under_construction(self) -> dict[str, object]:
        """What the game answers an SCV ordered to repair a supply depot another SCV is building, with the depot's
        progress then."""
        ground_middle = self._ground.claim(self._map.start_location, _DEFAULT_GROUND_HALF_SIZE)
        made_tags: list[int] = []
        try:
            scv_offset_from_the_middle = _DEFAULT_GROUND_HALF_SIZE + 0.5
            scvs = self._game.spawn(
                [
                    (
                        UnitTypeId.SCV,
                        self._player,
                        Point((ground_middle.x + scv_offset_from_the_middle, ground_middle.y)),
                    ),
                    (
                        UnitTypeId.SCV,
                        self._player,
                        Point((ground_middle.x - scv_offset_from_the_middle, ground_middle.y)),
                    ),
                ]
            )
            made_tags += [scv.tag for scv in scvs]
            if len(scvs) < 2:
                raise RuntimeError("the game did not create two SCVs")
            builder, repairer = scvs
            # A 2x2 structure centers on a tile's corner.
            depot_at = Point((ground_middle.x + 0.5, ground_middle.y + 0.5))
            build_answer = self._game.order(AbilityId.SCV_BUILD_SUPPLY_DEPOT, builder.tag, depot_at)
            if build_answer != SUCCESS:
                raise RuntimeError(f"the SCV was refused the supply depot: {error_pb2.ActionResult.Name(build_answer)}")
            made_tags.append(self._wait_for(UnitTypeId.SUPPLY_DEPOT).tag)
            self._client.step(_STEPS_INTO_THE_CONSTRUCTION)
            depot = self._report_of(made_tags[-1])
            repair_answer = error_pb2.ActionResult.Name(self._game.order(AbilityId.REPAIR, repairer.tag, depot.tag))
        finally:
            self._remove_those_listed(made_tags)
            self._ground.release(ground_middle)
        logger.info("A repair of a supply depot {:.0%} built is answered {}", depot.build_progress, repair_answer)
        return {
            "target": UnitTypeId.SUPPLY_DEPOT.name,
            "build_progress": depot.build_progress,
            "order_result": repair_answer,
        }

    def _free_geyser(self, gas_building: UnitTypeId) -> Point:
        """A geyser of the kind `gas_building` stands on, far from both starts. Raises `RuntimeError` if the map has
        none."""
        geyser_types = _GAS_BUILDINGS[gas_building]
        geysers = [Point((unit.pos.x, unit.pos.y)) for unit in self._geysers if unit.unit_type in geyser_types]
        if not geysers:
            raise RuntimeError(f"the map has no geyser for a {gas_building.name}")
        start_locations = self._map.all_start_locations
        return max(geysers, key=lambda geyser: min(geyser.distance_to(start) for start in start_locations))

    def _spawn_target_and_scv(
        self, target_type: UnitTypeId, ground_middle: Point, half_size: int, made_tags: list[int]
    ) -> tuple[raw_pb2.Unit, raw_pb2.Unit]:
        """An SCV created just beyond the edge of the ground at `ground_middle`, and the target: created at its
        middle, or, for a target the game reports flying, over the ground the SCV was put on, or, for an add-on the
        game creates only on its structure, built on one created for it. Made one at a time, so that an SCV repairing
        an SCV is told from its target, and each unit's tag added to `made_tags` as it is made. Raises `RuntimeError`
        if the game made either not."""
        scv_at = Point((ground_middle.x + half_size + 0.5, ground_middle.y))
        scvs = self._game.spawn([(UnitTypeId.SCV, self._player, scv_at)])
        made_tags += [scv.tag for scv in scvs]
        if not scvs:
            raise RuntimeError("the game did not create an SCV")
        if target_type in _ATTACHED_ADD_ONS:
            return self._build_add_on(target_type, ground_middle, made_tags), scvs[0]
        targets = self._game.spawn([(target_type, self._player, ground_middle)])
        made_tags += [target.tag for target in targets]
        if targets and targets[0].is_flying:
            # An SCV kept from beneath an air unit, as by a command center under it, holds the order 32 to 48 steps and
            # drops it.
            self._game.remove([targets[0].tag])
            scv_position = scvs[0].pos
            beside_the_scv = Point((scv_position.x + 1.0, scv_position.y))
            targets = self._game.spawn([(target_type, self._player, beside_the_scv)])
            made_tags += [target.tag for target in targets]
        if not targets:
            raise RuntimeError(f"the game did not create a {target_type.name}")
        return targets[0], scvs[0]

    def _build_add_on(self, add_on_type: UnitTypeId, ground_middle: Point, made_tags: list[int]) -> raw_pb2.Unit:
        """Create the structure `add_on_type` is built on, beside `ground_middle`, so that the two center on it, build
        the add-on on it, and return the add-on once it is finished. Adds each unit's tag to `made_tags` as it is
        made. Raises `RuntimeError` if the game made either not."""
        structure_type, add_on_ability = _ATTACHED_ADD_ONS[add_on_type]
        structure_at = Point((ground_middle.x - _STRUCTURE_LEFT_OF_THE_MIDDLE, ground_middle.y))
        structures = self._game.spawn([(structure_type, self._player, structure_at)])
        made_tags += [structure.tag for structure in structures]
        if not structures:
            raise RuntimeError(f"the game did not create a {structure_type.name}")
        if (add_on_answer := self._game.order(add_on_ability, structures[0].tag)) != SUCCESS:
            raise RuntimeError(
                f"the {structure_type.name} was refused its add-on: {error_pb2.ActionResult.Name(add_on_answer)}"
            )
        made_tags.append(self._wait_for(add_on_type).tag)
        return self._wait_for(add_on_type, finished=True)

    def _wait_for(self, unit_type: UnitTypeId, *, finished: bool = False) -> raw_pb2.Unit:
        """This player's first unit of `unit_type`, or if `finished` its first one built, looked for every few steps.
        Never the placeholder the game lists for a structure ordered and not yet begun, which has a tag of its own.
        Raises `RuntimeError` if none comes in time."""
        for _ in range(0, _WAIT_STEPS, _WAIT_STEPS_BETWEEN_LOOKS):
            own_units_of_the_type = (
                unit
                for unit in self._game.units()
                if unit.owner == self._player and unit.unit_type == unit_type and unit.display_type == raw_pb2.Visible
            )
            waited_for = (unit for unit in own_units_of_the_type if unit.build_progress == 1.0 or not finished)
            if (first_found := next(waited_for, None)) is not None:
                return first_found
            self._client.step(_WAIT_STEPS_BETWEEN_LOOKS)
        raise RuntimeError(f"no {unit_type.name} came in {_WAIT_STEPS} steps")

    def _finding_for(self, target_type: UnitTypeId, target: raw_pb2.Unit) -> RepairFinding:
        """A finding for `target`, holding what the tables say of its type and the repair not yet watched."""
        table_row = self.data.units[target_type]
        return RepairFinding(
            target=target_type.name,
            health_max=target.health_max,
            minerals_cost=table_row.cost.minerals,
            vespene_cost=table_row.cost.vespene,
            build_steps=table_row.build_time.steps,
            cost_steps=table_row.cost.time.steps,
            order_result="",
        )

    def _watch(self, finding: RepairFinding, tag: int) -> None:
        """Step one at a time and sample the target and the resources until it is whole or gone or the watch runs out,
        then work out what a repair of its whole health comes to."""
        watch_steps = max(_MINIMUM_WATCH_STEPS, round(_WATCH_COST_STEPS_TIMES * finding.cost_steps))
        if (first_sample := self._sample(tag)) is None:
            return
        samples = finding.samples
        samples.append(first_sample)
        for _ in range(watch_steps):
            self._client.step(1)
            if (sample := self._sample(tag)) is None:
                # Gone, as a MULE or an auto-turret goes when its time is up.
                break
            samples.append(sample)
            if finding.health_fell_step is None and sample.health < samples[-2].health:
                finding.health_fell_step = sample.step
            if finding.started_step is None and sample.health > first_sample.health:
                finding.started_step = samples[-2].step
            if sample.health >= finding.health_max:
                finding.finished_step = sample.step
                break
        last_sample = samples[-1]
        finding.health_repaired = last_sample.health - first_sample.health
        finding.minerals_spent = first_sample.minerals - last_sample.minerals
        finding.vespene_spent = first_sample.vespene - last_sample.vespene
        samples_regaining = [sample for sample in samples if first_sample.health < sample.health < finding.health_max]
        if len(samples_regaining) < 2 or samples_regaining[-1].step == samples_regaining[0].step:
            return
        first_regained, last_regained = samples_regaining[0], samples_regaining[-1]
        health_per_step = (last_regained.health - first_regained.health) / (last_regained.step - first_regained.step)
        finding.steps_for_whole_health = finding.health_max / health_per_step
        share_repaired = finding.health_repaired / finding.health_max
        finding.minerals_for_whole_health = finding.minerals_spent / share_repaired
        finding.vespene_for_whole_health = finding.vespene_spent / share_repaired

    def _sample(self, tag: int) -> RepairSample | None:
        """The target `tag`'s health and this player's resources, from a fresh observation; `None` once it is gone."""
        observation = self._client.observation().observation
        player_common = observation.player_common
        target_health = next((unit.health for unit in observation.raw_data.units if unit.tag == tag), None)
        if target_health is None:
            return None
        return RepairSample(observation.game_loop, target_health, player_common.minerals, player_common.vespene)

    def _report_of(self, tag: int) -> raw_pb2.Unit:
        """The latest report of the unit `tag`, from a fresh observation. Raises `RuntimeError` if it lists none."""
        if (latest_report := next((unit for unit in self._game.units() if unit.tag == tag), None)) is None:
            raise RuntimeError(f"the game no longer lists the unit {tag}")
        return latest_report

    def _remove_those_listed(self, tags: Sequence[int]) -> None:
        """Remove each of the units `tags` that a fresh observation still lists."""
        listed_tags = {unit.tag for unit in self._game.units()}
        self._game.remove(tag for tag in tags if tag in listed_tags)


def sweep(installation: Installation, targets: Sequence[UnitTypeId]) -> tuple[dict[str, object], dict[str, object]]:
    """Repair each of `targets`, or every Terran mechanical type, in one game, and ask for the repairs the game
    refuses. A type or a refusal the game would not set up is recorded as its error. Returns the findings and every
    step's samples, by type."""
    with playing(Race.TERRAN, installation) as game:
        repair_sweep = RepairSweep(game)
        repairs: dict[str, object] = {}
        samples: dict[str, object] = {}
        for target in targets or repair_sweep.mechanical_types():
            try:
                finding = asdict(repair_sweep.repair_from_half_health(target))
            except RuntimeError as error:
                logger.warning("{}: {}", target.name, error)
                repairs[target.name] = {"error": str(error)}
                continue
            samples[target.name] = finding.pop("samples")
            repairs[target.name] = finding
        findings: dict[str, object] = {"base_build": repair_sweep.base_build, "repairs": repairs}
        refusal_checks = {
            "not_mechanical": repair_sweep.refusal_for_a_unit_that_is_not_mechanical,
            "under_construction": repair_sweep.refusal_for_a_structure_under_construction,
            "around_an_observation": repair_sweep.answers_around_an_observation,
        }
        for finding_name, ask_the_game in refusal_checks.items():
            try:
                findings[finding_name] = ask_the_game()
            except RuntimeError as error:
                logger.warning("{}: {}", finding_name, error)
                findings[finding_name] = {"error": str(error)}
    return findings, samples


def main(argv: Sequence[str]) -> None:
    """Sweep the types named, or every Terran mechanical type, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help="unit types to repair, as `UnitTypeId` names; every mechanical one")
    parser.add_argument(
        "--out", type=Path, default=None, help=f"where to write the findings; {OUTPUT}, for a sweep of every type"
    )
    parser.add_argument("--samples", type=Path, default=None, help="where to write every step's samples, if anywhere")
    arguments = parser.parse_args(argv)
    if unknown_names := [name for name in arguments.targets if name not in UnitTypeId.__members__]:
        raise SystemExit(f"No unit type is called {', '.join(unknown_names)}")
    if arguments.targets and arguments.out is None:
        # The generator would make a table of the types named alone.
        raise SystemExit(f"Name --out for a sweep of some types, so that {OUTPUT} keeps every type")
    findings, samples = sweep(Installation.find(), [UnitTypeId[name] for name in arguments.targets])
    out_path = arguments.out or OUTPUT
    out_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote the findings to {}", out_path)
    if arguments.samples is not None:
        arguments.samples.write_text(json.dumps(samples), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
