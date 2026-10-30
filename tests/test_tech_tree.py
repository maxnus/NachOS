"""The tech tree: what the sweep's findings generate, and what a recorded game's tables then say."""

import json
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from s2clientprotocol import debug_pb2, query_pb2, sc2api_pb2

from sc2nachos.gamedata import GameData, Tech, TechRequirements
from sc2nachos.gamedata._techtree import (
    PRODUCERS_FALLBACK,
    TECH_TREE,
    UNNAMED_CREATION_ABILITIES,
    TechTree,
)
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.units import Unit
from support import TOOLS, RealGame, load_tool

_REPOSITORY_ROOT = Path(__file__).parents[1]
_TECH_TREE_FINDINGS = _REPOSITORY_ROOT / "data" / "tech_tree.json"
_UPGRADE_FINDINGS = _REPOSITORY_ROOT / "data" / "upgrades.json"
# The abilities unit types are offered that no curated ability names, none of which a player gives. A force field a
# sentry makes belongs to no player and is offered nothing; only one a debug command makes for a player is offered
# Shatter. Once Burrow is researched, every zerg unit that burrows is offered the infested terran's burrow, which
# burrows it as itself, reporting its own burrow running. A cyclone locked on is offered `Cancel_LockOn`, which takes
# back a cast rather than anything a structure is making, and no curated id names it.
_UNCURATED_OFFERED = frozenset({"BurrowDown_InfestorTerran", "BurrowUp_InfestorTerran", "Cancel_LockOn", "Shatter"})
# The creation abilities some unit type is offered that the sweep has no trial of, by product: a burrowed creep tumor
# was refused its spread for its cooldown, and a nydus network found no site for a worm (tool `sweep_tech_tree`).
_UNTRIED_CREATIONS = frozenset({("CreepTumor", "Build_CreepTumor_Tumor"), ("NydusCanal", "Build_NydusWorm")})


def _tech_tree_generator() -> ModuleType:
    """The `tools/generate_tech_tree.py` module."""
    return load_tool("generate_tech_tree")


def _tech_tree_findings(*, unread: bool = False, unconfirmed: bool = False, **parts: object) -> dict[str, object]:
    """Findings as the sweep writes them, empty except for `parts`. Every offered ability with no requirement in
    `parts` reads as needing nothing, unless `unread`, and every unnamed creation ability was seen making its unit
    type, unless `unconfirmed`."""
    findings: dict[str, object] = {
        "base_build": 1,
        "offered": {},
        "requirements": [],
        "made": [],
        "powered": [],
        "cancels": {},
        "remaps": {},
        "creation_abilities": {},
        "research_abilities": {},
        **parts,
    }
    records = cast("list[dict[str, object]]", findings["requirements"])
    if not unread:
        listed = {(record["performer"], record["ability"]) for record in records}
        offered = cast("dict[str, list[str]]", findings["offered"])
        records += [
            _requirement_entry(unit_type, ability, [], [])
            for unit_type, abilities in offered.items()
            for ability in abilities
            if (unit_type, ability) not in listed
        ]
    if not unconfirmed:
        trials = cast("list[dict[str, object]]", findings["made"])
        trials += [
            {
                "performer": "Probe",
                "ability": RawAbilityId(ability).name,
                "product": RawUnitTypeId(unit).name,
                "result": "build",
            }
            for ability, unit in UNNAMED_CREATION_ABILITIES.items()
        ]
    return findings


def _no_upgrades(*research: dict[str, object]) -> dict[str, object]:
    """Upgrade findings as `tools/sweep_upgrades.py` writes them, with only `research` filled."""
    return {"base_build": 1, "races": [], "research": list(research), "unexplained": []}


def _requirement_entry(performer: str, ability: str, structures: list[str], upgrades: list[str]) -> dict[str, object]:
    return {
        "performer": performer,
        "ability": ability,
        "structures": structures,
        # The sweep recorded every required add-on as attached to the performer's own structure.
        "attached": any("TechLab" in structure or "Reactor" in structure for structure in structures),
        "upgrades": upgrades,
    }


class TestGeneratingTheTables:
    def test_what_is_offered_is_read_under_the_curated_ids(self) -> None:
        findings = _tech_tree_findings(
            offered={"Barracks": ["BarracksTrain_Marine", "Rally_Building"], "Ursadon": ["Smart"]}
        )
        tree = _tech_tree_generator().read(findings, _no_upgrades())
        assert AbilityId.BARRACKS_TRAIN_MARINE in tree.unit_types.ability_requirements[UnitTypeId.BARRACKS]
        assert set(tree.unit_types.ability_requirements) == {UnitTypeId.BARRACKS}

    def test_an_ability_no_curated_id_names_is_left_out_and_named(self) -> None:
        shatter = "Shatter"
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Marine", shatter], "Factory": [shatter]})
        generator = _tech_tree_generator()
        assert generator.uncurated(findings) == {shatter}
        assert generator.read(findings, _no_upgrades()).unit_types.ability_requirements[UnitTypeId.FACTORY] == {}

    def test_a_requirement_is_read_under_the_unit_type_and_the_ability(self) -> None:
        ghost = _requirement_entry("Barracks", "BarracksTrain_Ghost", ["GhostAcademy", "BarracksTechLab"], [])
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Ghost"]}, requirements=[ghost])
        tree = _tech_tree_generator().read(findings, _no_upgrades())
        assert tree.unit_types.ability_requirements[UnitTypeId.BARRACKS][
            AbilityId.BARRACKS_TRAIN_GHOST
        ] == TechRequirements(techs=frozenset({Tech.GHOST_ACADEMY}), add_on=UnitTypeId.TECH_LAB_BARRACKS)

    def test_a_required_structure_no_tech_is_named_after_is_refused(self) -> None:
        bunker = _requirement_entry("Barracks", "BarracksTrain_Marine", ["Bunker"], [])
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Marine"]}, requirements=[bunker])
        with pytest.raises(ValueError, match="no Tech is named after it"):
            _tech_tree_generator().read(findings, _no_upgrades())

    def test_an_add_on_that_counts_on_another_structure_is_refused(self) -> None:
        """An add-on only ever counts on its own structure, which `TechRequirements` takes for granted."""
        marauder = _requirement_entry("Barracks", "BarracksTrain_Marauder", ["BarracksTechLab"], []) | {
            "attached": False
        }
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Marauder"]}, requirements=[marauder])
        with pytest.raises(ValueError, match="another structure's add-on"):
            _tech_tree_generator().read(findings, _no_upgrades())

    def test_what_performs_and_makes_an_ability_comes_with_the_tables_the_sweep_read(self) -> None:
        findings = _tech_tree_findings(
            offered={
                "Zergling": ["BurrowDown_Zergling"],
                "Barracks": ["BarracksTrain_Marine"],
                "BarracksTechLab": ["BarracksTechLabResearch_Stimpack"],
            },
            remaps={"BurrowDown_Zergling": "BurrowDown"},
            creation_abilities={"Marine": "BarracksTrain_Marine", "Baneling": "MorphZerglingToBaneling_Baneling"},
            research_abilities={"Stimpack": "BarracksTechLabResearch_Stimpack"},
        )
        tree = _tech_tree_generator().read(findings, _no_upgrades())
        assert tree.abilities.performers[AbilityId.BURROW] == {UnitTypeId.ZERGLING}
        assert tree.unit_types.creation_abilities[UnitTypeId.MARINE] is AbilityId.BARRACKS_TRAIN_MARINE
        # The table names a dead ability for a baneling, so the unnamed creation ability stands in.
        assert tree.unit_types.creation_abilities[UnitTypeId.BANELING] is AbilityId.ZERGLING_MORPH_BANELING
        assert tree.abilities.products[AbilityId.BARRACKS_TRAIN_MARINE] == {UnitTypeId.BARRACKS: UnitTypeId.MARINE}
        assert tree.unit_types.producers[UnitTypeId.MARINE] == {UnitTypeId.BARRACKS}
        stimpack = tree.abilities.products[AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK]
        assert stimpack == {UnitTypeId.TECH_LAB_BARRACKS: UpgradeId.STIMPACK}
        assert UpgradeId.STIMPACK not in tree.unit_types.producers
        assert tree.upgrades.research_abilities == {UpgradeId.STIMPACK: AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK}

    def test_a_unit_type_whose_creation_ability_nothing_carries_out_has_no_producers(self) -> None:
        tree = _tech_tree_generator().read(
            _tech_tree_findings(creation_abilities={"Marine": "BarracksTrain_Marine"}), _no_upgrades()
        )
        assert UnitTypeId.MARINE not in tree.unit_types.producers

    def test_two_abilities_researching_one_upgrade_are_refused(self) -> None:
        """The plating's misnamed research stands in beside the research the findings name for it."""
        findings = _tech_tree_findings(
            offered={
                "Armory": ["ArmoryResearch_TerranVehicleAndShipPlatingLevel1"],
                "BarracksTechLab": ["BarracksTechLabResearch_Stimpack"],
            },
            research_abilities={"TerranVehicleAndShipArmorsLevel1": "BarracksTechLabResearch_Stimpack"},
        )
        with pytest.raises(ValueError, match="TERRAN_VEHICLE_AND_SHIP_ARMOR_1 is researched by two abilities"):
            _tech_tree_generator().read(findings, _no_upgrades())

    def test_an_action_several_types_perform_makes_what_each_one_s_own_id_makes(self) -> None:
        """A barracks offered its own lift becomes a flying barracks, and a starport a flying starport."""
        findings = _tech_tree_findings(
            offered={"Barracks": ["Lift_Barracks"], "Starport": ["Lift_Starport"]},
            creation_abilities={"BarracksFlying": "Lift_Barracks", "StarportFlying": "Lift_Starport"},
        )
        tree = _tech_tree_generator().read(findings, _no_upgrades())
        assert tree.unit_types.ability_requirements[UnitTypeId.BARRACKS].keys() == {AbilityId.LIFT}
        assert tree.abilities.products[AbilityId.LIFT] == {
            UnitTypeId.BARRACKS: UnitTypeId.BARRACKS_FLYING,
            UnitTypeId.STARPORT: UnitTypeId.STARPORT_FLYING,
        }
        assert tree.unit_types.creation_abilities[UnitTypeId.STARPORT_FLYING] is AbilityId.LIFT

    def test_an_ability_needing_nothing_reads_as_needing_nothing(self) -> None:
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Marine"]})
        tree = _tech_tree_generator().read(findings, _no_upgrades())
        assert tree.unit_types.ability_requirements == {
            UnitTypeId.BARRACKS: {AbilityId.BARRACKS_TRAIN_MARINE: TechRequirements()}
        }

    def test_one_ability_can_need_different_things_of_different_unit_types(self) -> None:
        """A burrowed roach moves only once Tunneling Claws is researched; a marine needs nothing to move."""
        offered = {"RoachBurrowed": ["Move_Move"], "Marine": ["Move_Move"]}
        claws = _requirement_entry("RoachBurrowed", "Move_Move", [], ["TunnelingClaws"])
        tree = _tech_tree_generator().read(_tech_tree_findings(offered=offered, requirements=[claws]), _no_upgrades())
        needs = tree.unit_types.ability_requirements
        assert needs[UnitTypeId.ROACH_BURROWED][AbilityId.MOVE].upgrades == {UpgradeId.TUNNELING_CLAWS}
        assert needs[UnitTypeId.MARINE][AbilityId.MOVE] == TechRequirements()

    def test_an_ability_whose_requirements_were_never_read_is_refused(self) -> None:
        """It is refused rather than read as needing nothing."""
        findings = _tech_tree_findings(offered={"Ghost": ["Behavior_CloakOff_Ghost"]}, unread=True)
        with pytest.raises(ValueError, match="GHOST Behavior_CloakOff_Ghost"):
            _tech_tree_generator().read(findings, _no_upgrades())

    def test_two_unnamed_creation_abilities_for_a_type_the_table_names_nothing_for_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Either could be its creation ability."""
        generator = _tech_tree_generator()
        second = AbilityId.LARVA_MORPH_ZERGLING
        monkeypatch.setattr(
            generator, "UNNAMED_CREATION_ABILITIES", {**UNNAMED_CREATION_ABILITIES, second: UnitTypeId.BANELING}
        )
        findings = _tech_tree_findings()
        trial: dict[str, object] = {
            "performer": "Larva",
            "ability": RawAbilityId(second).name,
            "product": "Baneling",
            "result": "morph",
        }
        cast("list[dict[str, object]]", findings["made"]).append(trial)
        with pytest.raises(ValueError, match=r"more than one is listed: \['BANELING'\]"):
            generator.read(findings, _no_upgrades())

    def test_an_unnamed_creation_ability_the_sweep_did_not_see_make_its_unit_type_is_refused(self) -> None:
        """A patch that breaks one stops the regeneration instead of keeping an ability that makes nothing now."""
        with pytest.raises(ValueError, match="BANELING by ZERGLING_MORPH_BANELING"):
            _tech_tree_generator().read(_tech_tree_findings(unconfirmed=True), _no_upgrades())

    def test_a_requirement_no_curated_id_names_is_refused(self) -> None:
        marine = _requirement_entry("Barracks", "BarracksTrain_Marine", ["Ursadon"], [])
        findings = _tech_tree_findings(offered={"Barracks": ["BarracksTrain_Marine"]}, requirements=[marine])
        with pytest.raises(ValueError, match="Ursadon"):
            _tech_tree_generator().read(findings, _no_upgrades())

    def test_an_ability_that_turns_a_unit_into_something_else_is_not_its_to_perform(self) -> None:
        """Once Burrow is researched a zergling is offered a drone's burrow, and given it burrows as a zergling."""
        offered = {"Drone": ["BurrowDown_Drone"], "Zergling": ["BurrowDown_Drone", "BurrowDown_Zergling"]}
        made = [
            _trial_entry("Drone", "BurrowDown_Drone", "DroneBurrowed", "morph"),
            _trial_entry("Zergling", "BurrowDown_Drone", "DroneBurrowed", "other"),
        ]
        tree = _tech_tree_generator().read(_tech_tree_findings(offered=offered, made=made), _no_upgrades())
        assert set(tree.unit_types.ability_requirements[UnitTypeId.ZERGLING]) == {AbilityId.BURROW}
        assert tree.unit_types.morph_sources[UnitTypeId.DRONE_BURROWED] is UnitTypeId.DRONE

    def test_what_an_ability_uses_up_is_what_its_product_is_made_out_of(self) -> None:
        made = [
            _trial_entry("CommandCenter", "UpgradeToOrbital_OrbitalCommand", "OrbitalCommand", "morph"),
            _trial_entry("Barracks", "BarracksTrain_Marine", "Marine", "build"),
        ]
        morphed_from = (
            _tech_tree_generator().read(_tech_tree_findings(made=made), _no_upgrades()).unit_types.morph_sources
        )
        assert morphed_from == {UnitTypeId.ORBITAL_COMMAND: UnitTypeId.COMMAND_CENTER}

    def test_a_type_several_types_are_used_up_for_is_made_out_of_the_one_the_others_come_from(self) -> None:
        """An overlord and the overlord transport it becomes can both become an overseer."""
        made = [
            _trial_entry("Overlord", "Morph_OverlordTransport", "OverlordTransport", "morph"),
            _trial_entry("Overlord", "Morph_Overseer", "Overseer", "morph"),
            _trial_entry("OverlordTransport", "Morph_Overseer", "Overseer", "morph"),
        ]
        morphed_from = (
            _tech_tree_generator().read(_tech_tree_findings(made=made), _no_upgrades()).unit_types.morph_sources
        )
        assert morphed_from[UnitTypeId.OVERSEER] is UnitTypeId.OVERLORD
        assert morphed_from[UnitTypeId.OVERLORD_TRANSPORT] is UnitTypeId.OVERLORD

    def test_a_pylon_powering_itself_does_not_need_power(self) -> None:
        tree = _tech_tree_generator().read(_tech_tree_findings(powered=["Gateway", "Pylon"]), _no_upgrades())
        assert tree.unit_types.power_consumers == {UnitTypeId.GATEWAY}

    def test_the_module_holds_the_tech_tree_read(self) -> None:
        generator = _tech_tree_generator()
        findings = _tech_tree_findings(
            offered={"Barracks": ["BarracksTrain_Ghost", "Lift_Barracks"]},
            requirements=[_requirement_entry("Barracks", "BarracksTrain_Ghost", ["GhostAcademy"], [])],
            made=[_trial_entry("Barracks", "Lift_Barracks", "BarracksFlying", "morph")],
            powered=["Gateway"],
        )
        igniter = {
            "upgrades": ["HighCapacityBarrels"],
            "changes": {"HellionTank": {"speed": 0.5, "weapons": [{"damage": 1.0, "bonuses": {"Light": 12.0}}, {}]}},
            "levels": {"attack": [], "armor": [], "shield": []},
        }
        tree = generator.read(findings, _no_upgrades(igniter))
        assert tree.unit_types.upgrades
        namespace: dict[str, object] = {}
        exec(generator.render(tree), namespace)  # noqa: S102
        assert namespace["TECH_TREE"] == tree

    def test_findings_from_two_builds_are_refused(self) -> None:
        with pytest.raises(ValueError, match="build 1 and the upgrades on 2"):
            _tech_tree_generator().read(_tech_tree_findings(), _no_upgrades() | {"base_build": 2})

    def test_the_module_is_as_generated_from_the_findings(self) -> None:
        generator = _tech_tree_generator()
        tree = generator.read(_findings_file(), _upgrade_findings_file())
        written = generator.output(tree).read_text(encoding="utf-8")
        assert written == generator.render(tree), "run tools/generate_tech_tree.py"


def _trial_entry(performer: str, ability: str, product: str, result: str) -> dict[str, object]:
    return {"performer": performer, "ability": ability, "product": product, "result": result}


def _findings_file() -> dict[str, object]:
    """The committed findings."""
    return json.loads(_TECH_TREE_FINDINGS.read_text(encoding="utf-8"))


def _upgrade_findings_file() -> dict[str, object]:
    """The committed findings of `tools/sweep_upgrades.py`."""
    return json.loads(_UPGRADE_FINDINGS.read_text(encoding="utf-8"))


def _tech_tree_from_the_findings() -> TechTree:
    """The tech tree `tools/generate_tech_tree.py` generates from the committed findings."""
    return _tech_tree_generator().read(_findings_file(), _upgrade_findings_file())


class TestWhatTheTablesSay:
    def test_a_ghost_needs_an_academy_and_a_tech_lab_on_its_barracks(self, first_corpus_tables: GameData) -> None:
        ghost = first_corpus_tables.units[UnitTypeId.BARRACKS].ability_requirements[AbilityId.BARRACKS_TRAIN_GHOST]
        assert ghost == TechRequirements(techs=frozenset({Tech.GHOST_ACADEMY}), add_on=UnitTypeId.TECH_LAB_BARRACKS)

    def test_a_thor_needs_an_armory_and_a_tech_lab_on_its_factory(self, first_corpus_tables: GameData) -> None:
        thor = first_corpus_tables.units[UnitTypeId.FACTORY].ability_requirements[AbilityId.FACTORY_TRAIN_THOR]
        assert thor == TechRequirements(techs=frozenset({Tech.ARMORY}), add_on=UnitTypeId.TECH_LAB_FACTORY)

    def test_a_second_level_needs_the_first_and_what_the_second_needs(self, first_corpus_tables: GameData) -> None:
        bay = first_corpus_tables.units[UnitTypeId.ENGINEERING_BAY]
        assert bay.ability_requirements[AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2] == TechRequirements(
            techs=frozenset({Tech.ARMORY}), upgrades=frozenset({UpgradeId.TERRAN_INFANTRY_WEAPONS_1})
        )

    def test_every_ability_a_type_is_offered_says_what_it_needs(self, first_corpus_tables: GameData) -> None:
        barracks = first_corpus_tables.units[UnitTypeId.BARRACKS]
        assert barracks.ability_requirements[AbilityId.BARRACKS_TRAIN_MARINE] == TechRequirements()
        assert set(barracks.ability_requirements) == barracks.abilities

    def test_a_burrowed_roach_moves_only_with_tunneling_claws(self, first_corpus_tables: GameData) -> None:
        claws = TechRequirements(upgrades=frozenset({UpgradeId.TUNNELING_CLAWS}))
        assert first_corpus_tables.units[UnitTypeId.ROACH_BURROWED].ability_requirements[AbilityId.MOVE] == claws
        assert first_corpus_tables.units[UnitTypeId.ROACH].ability_requirements[AbilityId.MOVE] == TechRequirements()

    def test_a_barracks_needs_a_depot_which_a_lowered_one_grants(self, first_corpus_tables: GameData) -> None:
        barracks = first_corpus_tables.units[UnitTypeId.SCV].ability_requirements[AbilityId.SCV_BUILD_BARRACKS]
        assert barracks.techs == {Tech.SUPPLY_DEPOT}
        assert first_corpus_tables.units[UnitTypeId.SUPPLY_DEPOT_LOWERED].techs_granted == {Tech.SUPPLY_DEPOT}

    def test_a_hive_grants_a_lair(self, first_corpus_tables: GameData) -> None:
        den = first_corpus_tables.units[UnitTypeId.DRONE].ability_requirements[AbilityId.DRONE_MORPH_HYDRALISK_DEN]
        assert den.techs == {Tech.LAIR}
        assert first_corpus_tables.units[UnitTypeId.HIVE].techs_granted == {Tech.HATCHERY, Tech.LAIR, Tech.HIVE}

    def test_an_ability_is_performed_by_the_types_offered_it_and_makes_its_product(
        self, first_corpus_tables: GameData
    ) -> None:
        marine = first_corpus_tables.abilities[AbilityId.BARRACKS_TRAIN_MARINE]
        assert marine.performers == {UnitTypeId.BARRACKS}
        assert marine.products == {UnitTypeId.BARRACKS: UnitTypeId.MARINE}
        stimpack = first_corpus_tables.abilities[AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK].products
        assert set(stimpack.values()) == {UpgradeId.STIMPACK}

    def test_an_action_several_types_perform_is_offered_to_each_and_makes_each_its_own(
        self, first_corpus_tables: GameData
    ) -> None:
        burrow = first_corpus_tables.abilities[AbilityId.BURROW]
        assert {UnitTypeId.DRONE, UnitTypeId.ZERGLING, UnitTypeId.ROACH, UnitTypeId.WIDOW_MINE} <= burrow.performers
        assert burrow.products[UnitTypeId.ZERGLING] is UnitTypeId.ZERGLING_BURROWED
        assert burrow.products[UnitTypeId.ROACH] is UnitTypeId.ROACH_BURROWED
        assert AbilityId.BURROW in first_corpus_tables.units[UnitTypeId.ZERGLING].abilities

    def test_every_unit_type_and_upgrade_is_made_by_its_ability(self, first_corpus_tables: GameData) -> None:
        """The generator leaves out a product nothing carries out, so a sweep that loses one shows here. A rich
        refinery and its kin share the plain one's build, which makes the plain one."""
        for unit_type, row in first_corpus_tables.units.items():
            ability = row.creation_ability
            if ability is not None and UNNAMED_CREATION_ABILITIES.get(ability) is not unit_type:
                assert unit_type in first_corpus_tables.abilities[ability].products.values(), unit_type.name
        for upgrade, row in first_corpus_tables.upgrades.items():
            if (ability := row.research_ability) is not None:
                assert upgrade in first_corpus_tables.abilities[ability].products.values(), upgrade.name

    def test_a_unit_made_out_of_another_says_which(self, first_corpus_tables: GameData) -> None:
        units = first_corpus_tables.units
        assert units[UnitTypeId.ORBITAL_COMMAND].morphed_from is UnitTypeId.COMMAND_CENTER
        assert units[UnitTypeId.LAIR].morphed_from is UnitTypeId.HATCHERY
        assert units[UnitTypeId.ZERGLING].morphed_from is UnitTypeId.LARVA
        assert units[UnitTypeId.SPAWNING_POOL].morphed_from is UnitTypeId.DRONE
        assert units[UnitTypeId.SIEGE_TANK_SIEGED].morphed_from is UnitTypeId.SIEGE_TANK
        assert units[UnitTypeId.LIBERATOR_SIEGED].morphed_from is UnitTypeId.LIBERATOR
        assert units[UnitTypeId.BARRACKS].morphed_from is None
        assert units[UnitTypeId.MARINE].morphed_from is None

    def test_an_unnamed_creation_ability_makes_what_the_table_has_no_working_ability_for(
        self, first_corpus_tables: GameData
    ) -> None:
        baneling = first_corpus_tables.abilities[AbilityId.ZERGLING_MORPH_BANELING]
        assert baneling.products == {UnitTypeId.ZERGLING: UnitTypeId.BANELING}
        assert first_corpus_tables.units[UnitTypeId.BANELING].creation_ability is AbilityId.ZERGLING_MORPH_BANELING
        assert first_corpus_tables.units[UnitTypeId.BANELING].morphed_from is UnitTypeId.ZERGLING
        assert first_corpus_tables.units[UnitTypeId.LURKER].morphed_from is UnitTypeId.HYDRALISK
        assert first_corpus_tables.units[UnitTypeId.EXTRACTOR_RICH].morphed_from is UnitTypeId.DRONE
        assert first_corpus_tables.units[UnitTypeId.ASSIMILATOR_RICH].morphed_from is None
        # The build ability a rich refinery shares with a refinery makes a refinery, as the table says.
        assert first_corpus_tables.abilities[AbilityId.SCV_BUILD_REFINERY].products == {
            UnitTypeId.SCV: UnitTypeId.REFINERY
        }

    def test_a_unit_type_names_every_type_that_makes_one(self, first_corpus_tables: GameData) -> None:
        units = first_corpus_tables.units
        assert units[UnitTypeId.ZEALOT].producers == {UnitTypeId.GATEWAY, UnitTypeId.WARP_GATE}
        assert units[UnitTypeId.ARCHON].producers == {UnitTypeId.HIGH_TEMPLAR, UnitTypeId.DARK_TEMPLAR}
        assert units[UnitTypeId.ORBITAL_COMMAND].producers == {UnitTypeId.COMMAND_CENTER}
        assert units[UnitTypeId.SCV].producers == {
            UnitTypeId.COMMAND_CENTER,
            UnitTypeId.ORBITAL_COMMAND,
            UnitTypeId.PLANETARY_FORTRESS,
        }
        assert units[UnitTypeId.REFINERY_RICH].producers == {UnitTypeId.SCV}
        assert units[UnitTypeId.LARVA].producers == frozenset()

    def test_a_fallback_is_only_for_what_the_tech_tree_records_nothing_of(self) -> None:
        """Once the tech tree records it, the entry can go."""
        sweep = _tech_tree_from_the_findings()
        for unit_type in PRODUCERS_FALLBACK:
            assert unit_type not in sweep.unit_types.producers, unit_type.name

    def test_a_warp_gate_warps_in_what_a_gateway_trains(self, first_corpus_tables: GameData) -> None:
        zealot = first_corpus_tables.abilities[AbilityId.WARP_GATE_WARP_IN_ZEALOT]
        assert (zealot.products, zealot.performers) == (
            {UnitTypeId.WARP_GATE: UnitTypeId.ZEALOT},
            {UnitTypeId.WARP_GATE},
        )
        assert first_corpus_tables.units[UnitTypeId.ZEALOT].creation_ability is AbilityId.GATEWAY_TRAIN_ZEALOT

    def test_a_type_needs_what_its_producers_need_to_make_it(self, first_corpus_tables: GameData) -> None:
        units = first_corpus_tables.units
        assert units[UnitTypeId.GHOST].creation_requirements == TechRequirements(
            techs=frozenset({Tech.GHOST_ACADEMY}), add_on=UnitTypeId.TECH_LAB_BARRACKS
        )
        assert units[UnitTypeId.ORBITAL_COMMAND].creation_requirements == TechRequirements(
            techs=frozenset({Tech.BARRACKS})
        )
        assert units[UnitTypeId.BROOD_LORD].creation_requirements.techs == {Tech.GREATER_SPIRE}
        assert units[UnitTypeId.MARINE].creation_requirements == TechRequirements()
        assert units[UnitTypeId.LARVA].creation_requirements == TechRequirements()

    def test_what_a_warp_gate_warps_in_needs_what_a_gateway_needs_to_train_it(
        self, first_corpus_tables: GameData
    ) -> None:
        units = first_corpus_tables.units
        core = TechRequirements(techs=frozenset({Tech.CYBERNETICS_CORE}))
        assert units[UnitTypeId.WARP_GATE].ability_requirements[AbilityId.WARP_GATE_WARP_IN_ADEPT] == core
        assert units[UnitTypeId.GATEWAY].ability_requirements[AbilityId.GATEWAY_TRAIN_ADEPT] == core
        assert units[UnitTypeId.ADEPT].creation_requirements == core

    def test_every_producer_of_a_type_needs_the_same_to_make_it(self, first_corpus_tables: GameData) -> None:
        """So what a type needs is what each of its producers needs, as `creation_requirements` says."""
        units, abilities = first_corpus_tables.units, first_corpus_tables.abilities
        for unit_type, row in units.items():
            needs = {
                requirements
                for producer in row.producers
                if (producer_row := units.get(producer)) is not None
                for ability, requirements in producer_row.ability_requirements.items()
                if ability is row.creation_ability
                or (
                    (ability_row := abilities.get(ability)) is not None
                    and ability_row.products.get(producer) is unit_type
                )
            }
            assert needs <= {row.creation_requirements}, unit_type.name
            if not needs:
                assert row.creation_requirements == TechRequirements(), unit_type.name

    def test_an_upgrade_names_what_researches_it_and_what_that_needs(self, first_corpus_tables: GameData) -> None:
        upgrades = first_corpus_tables.upgrades
        weapons = upgrades[UpgradeId.TERRAN_INFANTRY_WEAPONS_2]
        assert weapons.researchers == {UnitTypeId.ENGINEERING_BAY}
        assert weapons.research_requirements == TechRequirements(
            techs=frozenset({Tech.ARMORY}), upgrades=frozenset({UpgradeId.TERRAN_INFANTRY_WEAPONS_1})
        )
        assert upgrades[UpgradeId.ZERG_AIR_WEAPONS_1].researchers == {UnitTypeId.SPIRE, UnitTypeId.GREATER_SPIRE}
        assert upgrades[UpgradeId.STIMPACK].research_requirements == TechRequirements()

    def test_every_upgrade_names_the_types_its_research_makes_it_for(self, first_corpus_tables: GameData) -> None:
        for upgrade, row in first_corpus_tables.upgrades.items():
            assert row.research_ability is not None, upgrade.name
            products = first_corpus_tables.abilities[row.research_ability].products
            assert row.researchers, upgrade.name
            assert all(products.get(researcher) is upgrade for researcher in row.researchers), upgrade.name

    def test_every_researcher_of_an_upgrade_needs_the_same_for_it(self, first_corpus_tables: GameData) -> None:
        units = first_corpus_tables.units
        for upgrade, row in first_corpus_tables.upgrades.items():
            assert row.research_ability is not None, upgrade.name
            needs = {units[researcher].ability_requirements[row.research_ability] for researcher in row.researchers}
            assert needs == {row.research_requirements}, upgrade.name

    def test_a_type_grants_the_tech_named_after_it_and_after_each_type_the_tables_count_it_as(
        self, first_corpus_tables: GameData
    ) -> None:
        units = first_corpus_tables.units
        assert units[UnitTypeId.SPIRE].techs_granted == {Tech.SPIRE}
        assert units[UnitTypeId.GREATER_SPIRE].techs_granted == {Tech.SPIRE, Tech.GREATER_SPIRE}
        assert units[UnitTypeId.BARRACKS_FLYING].techs_granted == {Tech.BARRACKS}
        assert units[UnitTypeId.WARP_GATE].techs_granted == {Tech.GATEWAY}
        assert units[UnitTypeId.ORBITAL_COMMAND].techs_granted == {Tech.COMMAND_CENTER}
        assert units[UnitTypeId.MARINE].techs_granted == frozenset()

    def test_a_type_granting_a_tech_grants_every_tech_its_base_type_grants(self, first_corpus_tables: GameData) -> None:
        """A hive grants `Tech.HATCHERY` as a lair does, though it counts as one only through a lair."""
        units = first_corpus_tables.units
        for unit_type, row in units.items():
            for tech in row.techs_granted:
                assert units[tech.base_type].techs_granted <= row.techs_granted, (unit_type.name, tech.name)

    def test_the_types_granting_a_tech_are_its_base_type_and_those_counted_as_it(
        self, first_corpus_tables: GameData
    ) -> None:
        assert first_corpus_tables.types_granting(Tech.SPIRE) == {UnitTypeId.SPIRE, UnitTypeId.GREATER_SPIRE}
        assert first_corpus_tables.types_granting(Tech.LAIR) == {UnitTypeId.LAIR, UnitTypeId.HIVE}
        assert first_corpus_tables.types_granting(Tech.SUPPLY_DEPOT) == {
            UnitTypeId.SUPPLY_DEPOT,
            UnitTypeId.SUPPLY_DEPOT_LOWERED,
        }
        assert first_corpus_tables.types_granting(Tech.GREATER_SPIRE) == {UnitTypeId.GREATER_SPIRE}
        for tech in Tech:
            assert tech.base_type in first_corpus_tables.types_granting(tech), tech.name

    def test_every_tech_is_what_some_requirement_names(self) -> None:
        """No tech is left that nothing needs; `tools/generate_tech_tree.py` refuses a requirement no tech names."""
        tree = TECH_TREE.unit_types
        named = {
            tech
            for needs_by_ability in tree.ability_requirements.values()
            for needs in needs_by_ability.values()
            for tech in needs.techs
        }
        assert named == set(Tech)

    def test_a_tech_never_equals_the_type_it_is_named_after(self) -> None:
        """So a tech and a unit type never stand for each other as keys."""
        assert Tech.SPIRE.base_type is UnitTypeId.SPIRE
        assert Tech.SPIRE != UnitTypeId.SPIRE
        assert Tech.SPIRE not in {UnitTypeId.SPIRE: 1}
        assert UnitTypeId.SPIRE not in {Tech.SPIRE: 1}

    def test_a_gateway_needs_power_and_a_nexus_and_a_pylon_do_not(self, first_corpus_tables: GameData) -> None:
        assert first_corpus_tables.units[UnitTypeId.GATEWAY].needs_power
        assert not first_corpus_tables.units[UnitTypeId.NEXUS].needs_power
        assert not first_corpus_tables.units[UnitTypeId.PYLON].needs_power

    def test_a_unit_type_carries_what_it_is_offered(self, first_corpus_tables: GameData) -> None:
        assert AbilityId.STIM in first_corpus_tables.units[UnitTypeId.MARINE].abilities

    def test_only_the_known_makers_have_nothing_to_perform_them(self, first_corpus_tables: GameData) -> None:
        """The game orders every gateway to become a warp gate once the research is done, so the sweep saw none
        offered it. Every research ability has a performer."""
        makers = [row.creation_ability for row in first_corpus_tables.units.values()]
        makers += [row.research_ability for row in first_corpus_tables.upgrades.values()]
        unperformed = {
            ability
            for ability in makers
            if ability is not None and not first_corpus_tables.abilities[ability].performers
        }
        assert unperformed == {AbilityId.GATEWAY_MORPH_WARP_GATE}

    def test_only_the_abilities_the_sweep_never_saw_offered_have_no_performers(
        self, first_corpus_tables: GameData
    ) -> None:
        """Those `AbilityData.performers` names, and `NULL`, which is no ability at all."""
        unoffered = {row.id for row in first_corpus_tables.abilities.values() if not row.performers}
        assert unoffered == {
            AbilityId.NULL,
            AbilityId.GATEWAY_MORPH_WARP_GATE,
            AbilityId.MORPH_ARCHON,
            AbilityId.CARRIER_BUILD_INTERCEPTORS,
        }

    def test_the_off_half_of_a_toggle_is_carried_out_by_what_carries_out_the_on_half(
        self, first_corpus_tables: GameData
    ) -> None:
        """A unit is offered the off half only once the on half has taken, which the sweep never set up for these."""
        abilities = first_corpus_tables.abilities
        assert abilities[AbilityId.HOLD_FIRE_OFF].performers == {UnitTypeId.GHOST, UnitTypeId.LURKER_BURROWED}
        assert abilities[AbilityId.BANELING_ATTACK_STRUCTURES_OFF].performers == {UnitTypeId.BANELING}

    def test_a_morph_several_types_perform_is_made_out_of_the_one_the_others_come_from(
        self, first_corpus_tables: GameData
    ) -> None:
        """An overlord and the overlord transport it becomes can both become an overseer."""
        overseer = first_corpus_tables.abilities[AbilityId.OVERLORD_MORPH_OVERSEER]
        assert overseer.performers == {UnitTypeId.OVERLORD, UnitTypeId.OVERLORD_TRANSPORT}
        assert first_corpus_tables.units[UnitTypeId.OVERSEER].morphed_from is UnitTypeId.OVERLORD

    def test_only_creep_tumors_the_game_will_not_create_are_left_unswept(self, first_corpus_tables: GameData) -> None:
        """A debug command cannot create a creep tumor, so what one is offered is unknown rather than nothing."""
        unswept = {
            row.id
            for row in first_corpus_tables.units.values()
            if row.race in (Race.TERRAN, Race.PROTOSS, Race.ZERG)
            and row.id not in _tech_tree_from_the_findings().unit_types.ability_requirements
        }
        assert unswept == {
            UnitTypeId.CREEP_TUMOR,
            UnitTypeId.CREEP_TUMOR_QUEEN,
            UnitTypeId.TECH_LAB,
            UnitTypeId.REACTOR,
        }


class TestWhatTheSweepFound:
    def test_only_the_zerg_burrows_turn_a_unit_into_something_other_than_they_make(self) -> None:
        """Once Burrow is researched every zerg unit is offered every zerg unit's burrow, and burrows as itself. Any
        other ability doing this would silently drop a unit type from its performers."""
        findings = json.loads(_TECH_TREE_FINDINGS.read_text(encoding="utf-8"))
        others = {record["ability"] for record in findings["made"] if record["result"] == "other"}
        assert others and all(ability.startswith("BurrowDown_") for ability in others)

    def test_what_is_offered_that_no_curated_id_names_is_known(self) -> None:
        """Anything else a unit type is offered must be curated, or the tables leave it out."""
        assert _tech_tree_generator().uncurated(_findings_file()) == _UNCURATED_OFFERED

    def test_what_makes_a_unit_type_is_tried_by_every_game_id_a_unit_may_be_offered_it_by(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A barracks is offered the id its tech lab's row names, not the curated one standing for every tech lab; a
        liberator the curated id, not the one its sieged form's row names (tool `sweep_tech_tree`). A custom id is
        none of the game's."""
        monkeypatch.syspath_prepend(str(TOOLS))
        offered = load_tool("sweep_tech_tree").creation_abilities_offered(
            load_tool("generate_unit_types").corpus_answer()
        )
        assert offered[UnitTypeId.TECH_LAB_BARRACKS] == {
            RawAbilityId.Build_TechLab_Barracks,
            RawAbilityId.Build_TechLab,
        }
        assert offered[UnitTypeId.LIBERATOR_SIEGED] == {
            RawAbilityId.LiberatorMorphtoAG_LiberatorAGMode,
            RawAbilityId.Morph_LiberatorAGMode,
        }
        assert offered[UnitTypeId.SIEGE_TANK_SIEGED] == {RawAbilityId.SiegeMode_SiegeMode}
        assert offered[UnitTypeId.REFINERY_RICH] == {RawAbilityId.TerranBuild_Refinery}
        assert offered[UnitTypeId.MARINE] == {RawAbilityId.BarracksTrain_Marine}

    def test_every_creation_ability_a_unit_type_is_offered_was_tried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A trial a sweep loses drops what the tech tree reads from it, as every trial of a collapsed family was once
        dropped."""
        monkeypatch.syspath_prepend(str(TOOLS))
        abilities_making = load_tool("sweep_tech_tree").creation_abilities_offered(
            load_tool("generate_unit_types").corpus_answer()
        )
        products_and_abilities = {
            (product, ability) for product, abilities in abilities_making.items() for ability in abilities
        }
        products_and_abilities |= {
            (product, RawAbilityId(ability)) for ability, product in UNNAMED_CREATION_ABILITIES.items()
        }
        findings = json.loads(_TECH_TREE_FINDINGS.read_text(encoding="utf-8"))
        offered_anywhere = {ability for abilities in findings["offered"].values() for ability in abilities}
        tried = {(trial["product"], trial["ability"]) for trial in findings["made"]}
        untried = {
            (RawUnitTypeId(product).name, ability.name)
            for product, ability in products_and_abilities
            if ability.name in offered_anywhere
        } - tried
        assert untried == _UNTRIED_CREATIONS


def _abilities_offered(transport: WebSocketTransport, unit: Unit) -> set[int]:
    query = query_pb2.RequestQuery(abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=unit.tag)])
    return {
        ability.ability_id
        for ability in transport.request(sc2api_pb2.Request(query=query)).query.abilities[0].abilities
    }


@pytest.mark.integration
def test_in_a_real_game_the_tables_say_what_is_offered_and_made() -> None:
    """Run with `pytest -m integration`. Plays a game as terran and rechecks a few of the tables' facts in it, so a
    patch that moves the tech tree fails here first."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with GameProcess.launch(window=(640, 480)) as process:
        transport = WebSocketTransport.connect(process.url)
        with closing(Client(transport)) as client:
            client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
            game = RealGame(client, client.join_game(Race.TERRAN))
            tables = game.tracker.game_data
            state = debug_pb2.DebugGameState
            game.debug(*(debug_pb2.DebugCommand(game_state=cheat) for cheat in (state.free, state.fast_build)))
            units = game.turn(1)
            home = units.own.of_type(UnitTypeId.COMMAND_CENTER)[0].position
            toward = home.towards(game.map.playable_area.center, 12)

            # A ghost is offered once its barracks has a tech lab and an academy stands, and not before.
            game.debug(game.create(UnitTypeId.BARRACKS, game.open_ground(toward)))
            game.turn(2)
            barracks = game.newest(UnitTypeId.BARRACKS)
            game.order(AbilityId.BUILD_TECH_LAB, barracks, target=barracks.position)
            game.turn(22 * 8)
            assert AbilityId.BARRACKS_TRAIN_GHOST not in _abilities_offered(transport, barracks)
            game.debug(game.create(UnitTypeId.GHOST_ACADEMY, game.open_ground(toward.towards(home, -8))))
            game.turn(4)
            assert AbilityId.BARRACKS_TRAIN_GHOST in _abilities_offered(transport, barracks)
            ghost = tables.units[UnitTypeId.BARRACKS].ability_requirements[AbilityId.BARRACKS_TRAIN_GHOST]
            assert (ghost.techs, ghost.add_on) == ({Tech.GHOST_ACADEMY}, UnitTypeId.TECH_LAB_BARRACKS)

            # A second level is offered once the first is done.
            game.debug(game.create(UnitTypeId.ENGINEERING_BAY, game.open_ground(toward.towards(home, 6))))
            game.debug(game.create(UnitTypeId.ARMORY, game.open_ground(toward.towards(home, -14))))
            game.turn(2)
            bay = game.newest(UnitTypeId.ENGINEERING_BAY)
            second = AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2
            assert second not in _abilities_offered(transport, bay)
            game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, bay)
            for _ in range(100):
                if UpgradeId.TERRAN_INFANTRY_WEAPONS_1 in game.state.upgrades:
                    break
                game.turn(22)
            game.turn(22)
            assert second in _abilities_offered(transport, bay)
            assert (
                UpgradeId.TERRAN_INFANTRY_WEAPONS_1
                in tables.units[UnitTypeId.ENGINEERING_BAY].ability_requirements[second].upgrades
            )

            # A command center becomes the orbital command it morphs into, under its own tag.
            command_center = units.own.of_type(UnitTypeId.COMMAND_CENTER)[0]
            game.order(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND, command_center)
            for _ in range(100):
                if game.turn(22).own.of_type(UnitTypeId.ORBITAL_COMMAND):
                    break
            assert game.newest(UnitTypeId.ORBITAL_COMMAND).tag == command_center.tag
            assert tables.units[UnitTypeId.ORBITAL_COMMAND].morphed_from is UnitTypeId.COMMAND_CENTER
            client.leave_game()
