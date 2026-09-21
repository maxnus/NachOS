"""The relationship tables: what the sweep's findings generate, and what the tables of a recorded game then say."""

import importlib.util
import json
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from s2clientprotocol import debug_pb2, query_pb2, sc2api_pb2

from sc2nachos.gamedata import GameData, TechRequirements
from sc2nachos.gamedata._techtree import UNNAMED_CREATION_ABILITIES, TechTree
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId
from sc2nachos.launch import GameProcess, MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, Recording, WebSocketTransport
from sc2nachos.units import Unit
from support import RealGame

_REPO = Path(__file__).parents[1]
_GENERATOR = _REPO / "tools" / "generate_tech_tree.py"
_FINDINGS = _REPO / "data" / "tech_tree.json"
_UPGRADE_FINDINGS = _REPO / "data" / "upgrades.json"
_CORPUS = sorted((_REPO / "tests" / "corpus").glob("*.sc2rec"))
# What the unit types are offered that no curated ability names, none of which a player gives. A force field a sentry
# makes belongs to no player and is offered nothing; only one a debug command makes for a player is offered Shatter.
# And once Burrow is researched every zerg unit that burrows is offered the infested terran's burrow, which burrows it
# as itself, reporting its own burrow running.
# A cyclone locked on is offered `Cancel_LockOn`, which is a cast to take back rather than anything a
# structure is making, and no curated id names it.
_UNCURATED_OFFERED = frozenset({"BurrowDown_InfestorTerran", "BurrowUp_InfestorTerran", "Cancel_LockOn", "Shatter"})


def _generator() -> ModuleType:
    """`tools/generate_tech_tree.py`, which is no package to import from."""
    spec = importlib.util.spec_from_file_location("generate_tech_tree", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _findings(*, unread: bool = False, unconfirmed: bool = False, **parts: object) -> dict[str, object]:
    """Findings as the sweep writes them, empty but for `parts`: every ability offered read as needing nothing where
    `parts` holds no requirement for it, unless it is to be left `unread`, and every unnamed creation ability seen
    making its unit type, unless it is to be left `unconfirmed`."""
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
            _requirement(unit_type, ability, [], [])
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
    """Upgrade findings as `tools/sweep_upgrades.py` writes them, holding only `research`."""
    return {"base_build": 1, "races": [], "research": list(research), "unexplained": []}


def _requirement(performer: str, ability: str, structures: list[str], upgrades: list[str]) -> dict[str, object]:
    return {
        "performer": performer,
        "ability": ability,
        "structures": structures,
        # The sweep read every add-on it found required as counting only on the unit's own structure.
        "attached": any("TechLab" in structure or "Reactor" in structure for structure in structures),
        "upgrades": upgrades,
    }


class TestGeneratingTheTables:
    def test_what_is_offered_is_read_under_the_curated_ids(self) -> None:
        findings = _findings(offered={"Barracks": ["BarracksTrain_Marine", "Rally_Building"], "Ursadon": ["Smart"]})
        tree = _generator().read(findings, _no_upgrades())
        assert AbilityId.BARRACKS_TRAIN_MARINE in tree.ability_requirements[UnitTypeId.BARRACKS]
        assert set(tree.ability_requirements) == {UnitTypeId.BARRACKS}

    def test_an_ability_no_curated_id_names_is_left_out_and_named(self) -> None:
        shatter = "Shatter"
        findings = _findings(offered={"Barracks": ["BarracksTrain_Marine", shatter], "Factory": [shatter]})
        generator = _generator()
        assert generator.uncurated(findings) == {shatter}
        assert generator.read(findings, _no_upgrades()).ability_requirements[UnitTypeId.FACTORY] == {}

    def test_a_requirement_is_read_under_the_unit_type_and_the_ability(self) -> None:
        ghost = _requirement("Barracks", "BarracksTrain_Ghost", ["GhostAcademy", "BarracksTechLab"], [])
        findings = _findings(offered={"Barracks": ["BarracksTrain_Ghost"]}, requirements=[ghost])
        tree = _generator().read(findings, _no_upgrades())
        assert tree.ability_requirements[UnitTypeId.BARRACKS][AbilityId.BARRACKS_TRAIN_GHOST] == TechRequirements(
            structures=frozenset({UnitTypeId.GHOST_ACADEMY, UnitTypeId.TECH_LAB_BARRACKS})
        )

    def test_an_add_on_that_counts_on_another_structure_is_refused(self) -> None:
        """An add-on only ever counts on its own structure, which is what `TechRequirements` takes for granted."""
        marauder = _requirement("Barracks", "BarracksTrain_Marauder", ["BarracksTechLab"], []) | {"attached": False}
        findings = _findings(offered={"Barracks": ["BarracksTrain_Marauder"]}, requirements=[marauder])
        with pytest.raises(ValueError, match="another structure's add-on"):
            _generator().read(findings, _no_upgrades())

    def test_what_performs_and_makes_an_ability_comes_with_the_tables_the_sweep_read(self) -> None:
        findings = _findings(
            offered={"Zergling": ["BurrowDown_Zergling"], "Barracks": ["BarracksTrain_Marine"]},
            remaps={"BurrowDown_Zergling": "BurrowDown"},
            creation_abilities={"Marine": "BarracksTrain_Marine", "Baneling": "MorphZerglingToBaneling_Baneling"},
            research_abilities={"Stimpack": "BarracksTechLabResearch_Stimpack"},
        )
        tree = _generator().read(findings, _no_upgrades())
        assert tree.ability_performers[AbilityId.GENERAL_BURROW] == {UnitTypeId.ZERGLING}
        assert tree.creation_abilities[UnitTypeId.MARINE] is AbilityId.BARRACKS_TRAIN_MARINE
        # The table names a dead ability for a baneling, so the unnamed creation ability stands in.
        assert tree.creation_abilities[UnitTypeId.BANELING] is AbilityId.ZERGLING_MORPH_BANELING
        assert tree.ability_products[AbilityId.BARRACKS_TRAIN_MARINE] is UnitTypeId.MARINE
        assert tree.ability_products[AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK] is UpgradeId.STIMPACK

    def test_an_ability_needing_nothing_reads_as_needing_nothing(self) -> None:
        findings = _findings(offered={"Barracks": ["BarracksTrain_Marine"]})
        tree = _generator().read(findings, _no_upgrades())
        assert tree.ability_requirements == {UnitTypeId.BARRACKS: {AbilityId.BARRACKS_TRAIN_MARINE: TechRequirements()}}

    def test_one_ability_can_need_different_things_of_different_unit_types(self) -> None:
        """A burrowed roach moves only once Tunneling Claws is researched, and a marine needs nothing to."""
        offered = {"RoachBurrowed": ["Move_Move"], "Marine": ["Move_Move"]}
        claws = _requirement("RoachBurrowed", "Move_Move", [], ["TunnelingClaws"])
        tree = _generator().read(_findings(offered=offered, requirements=[claws]), _no_upgrades())
        needs = tree.ability_requirements
        assert needs[UnitTypeId.ROACH_BURROWED][AbilityId.GENERAL_MOVE_EXACT].upgrades == {UpgradeId.TUNNELING_CLAWS}
        assert needs[UnitTypeId.MARINE][AbilityId.GENERAL_MOVE_EXACT] == TechRequirements()

    def test_an_ability_whose_requirements_were_never_read_is_refused(self) -> None:
        """Rather than read as needing nothing."""
        findings = _findings(offered={"Ghost": ["Behavior_CloakOff_Ghost"]}, unread=True)
        with pytest.raises(ValueError, match="GHOST GHOST_CLOAK_OFF"):
            _generator().read(findings, _no_upgrades())

    def test_two_unnamed_creation_abilities_for_a_type_the_table_names_nothing_for_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Either could be its creation ability."""
        generator = _generator()
        second = AbilityId.LARVA_MORPH_ZERGLING
        monkeypatch.setattr(
            generator, "UNNAMED_CREATION_ABILITIES", {**UNNAMED_CREATION_ABILITIES, second: UnitTypeId.BANELING}
        )
        findings = _findings()
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
        """A patch that breaks one stops the regeneration, rather than keep an ability that makes nothing any more."""
        with pytest.raises(ValueError, match="BANELING by ZERGLING_MORPH_BANELING"):
            _generator().read(_findings(unconfirmed=True), _no_upgrades())

    def test_a_requirement_no_curated_id_names_is_refused(self) -> None:
        marine = _requirement("Barracks", "BarracksTrain_Marine", ["Ursadon"], [])
        findings = _findings(offered={"Barracks": ["BarracksTrain_Marine"]}, requirements=[marine])
        with pytest.raises(ValueError, match="Ursadon"):
            _generator().read(findings, _no_upgrades())

    def test_an_ability_that_turns_a_unit_into_something_else_is_not_its_to_perform(self) -> None:
        """Once Burrow is researched a zergling is offered a drone's burrow, and ordered it burrows as a zergling."""
        offered = {"Drone": ["BurrowDown_Drone"], "Zergling": ["BurrowDown_Drone", "BurrowDown_Zergling"]}
        made = [
            _trial("Drone", "BurrowDown_Drone", "DroneBurrowed", "morph"),
            _trial("Zergling", "BurrowDown_Drone", "DroneBurrowed", "other"),
        ]
        tree = _generator().read(_findings(offered=offered, made=made), _no_upgrades())
        assert set(tree.ability_requirements[UnitTypeId.ZERGLING]) == {AbilityId.ZERGLING_BURROW}
        assert tree.morph_sources[UnitTypeId.DRONE_BURROWED] is UnitTypeId.DRONE

    def test_what_an_ability_uses_up_is_what_its_product_is_made_out_of(self) -> None:
        made = [
            _trial("CommandCenter", "UpgradeToOrbital_OrbitalCommand", "OrbitalCommand", "morph"),
            _trial("Barracks", "BarracksTrain_Marine", "Marine", "build"),
        ]
        morphed_from = _generator().read(_findings(made=made), _no_upgrades()).morph_sources
        assert morphed_from == {UnitTypeId.ORBITAL_COMMAND: UnitTypeId.COMMAND_CENTER}

    def test_a_type_several_types_are_used_up_for_is_made_out_of_the_one_the_others_come_from(self) -> None:
        """An overlord and the overlord transport it becomes can both become an overseer."""
        made = [
            _trial("Overlord", "Morph_OverlordTransport", "OverlordTransport", "morph"),
            _trial("Overlord", "Morph_Overseer", "Overseer", "morph"),
            _trial("OverlordTransport", "Morph_Overseer", "Overseer", "morph"),
        ]
        morphed_from = _generator().read(_findings(made=made), _no_upgrades()).morph_sources
        assert morphed_from[UnitTypeId.OVERSEER] is UnitTypeId.OVERLORD
        assert morphed_from[UnitTypeId.OVERLORD_TRANSPORT] is UnitTypeId.OVERLORD

    def test_a_pylon_powering_itself_does_not_need_power(self) -> None:
        tree = _generator().read(_findings(powered=["Gateway", "Pylon"]), _no_upgrades())
        assert tree.power_consumers == {UnitTypeId.GATEWAY}

    def test_the_module_holds_the_tech_tree_read(self) -> None:
        generator = _generator()
        findings = _findings(
            offered={"Barracks": ["BarracksTrain_Ghost", "Lift_Barracks"]},
            requirements=[_requirement("Barracks", "BarracksTrain_Ghost", ["GhostAcademy"], [])],
            made=[_trial("Barracks", "Lift_Barracks", "BarracksFlying", "morph")],
            powered=["Gateway"],
        )
        igniter = {
            "upgrades": ["HighCapacityBarrels"],
            "changes": {"HellionTank": {"speed": 0.5, "weapons": [{"damage": 1.0, "bonuses": {"Light": 12.0}}, {}]}},
            "levels": {"attack": [], "armor": [], "shield": []},
        }
        tree = generator.read(findings, _no_upgrades(igniter))
        assert tree.unit_type_upgrades
        namespace: dict[str, object] = {}
        exec(generator.render(tree), namespace)  # noqa: S102
        assert namespace["TECH_TREE"] == tree

    def test_findings_from_two_builds_are_refused(self) -> None:
        with pytest.raises(ValueError, match="build 1 and the upgrades on 2"):
            _generator().read(_findings(), _no_upgrades() | {"base_build": 2})

    def test_the_module_is_as_generated_from_the_findings(self) -> None:
        generator = _generator()
        tree = generator.read(_findings_file(), _upgrade_findings_file())
        written = generator.output(tree).read_text(encoding="utf-8")
        assert written == generator.render(tree), "run tools/generate_tech_tree.py"


def _trial(performer: str, ability: str, product: str, result: str) -> dict[str, object]:
    return {"performer": performer, "ability": ability, "product": product, "result": result}


def _findings_file() -> dict[str, object]:
    """The committed findings."""
    return json.loads(_FINDINGS.read_text(encoding="utf-8"))


def _upgrade_findings_file() -> dict[str, object]:
    """The committed findings of `tools/sweep_upgrades.py`."""
    return json.loads(_UPGRADE_FINDINGS.read_text(encoding="utf-8"))


def _sweep() -> TechTree:
    """What the committed findings generate, as `tools/generate_tech_tree.py` reads them."""
    return _generator().read(_findings_file(), _upgrade_findings_file())


@pytest.fixture(scope="module")
def tables() -> GameData:
    """The tables of the first corpus game, which every game on the current ladder shares."""
    recording = Recording(_CORPUS[0])
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


class TestWhatTheTablesSay:
    def test_a_ghost_needs_an_academy_and_a_tech_lab_on_its_barracks(self, tables: GameData) -> None:
        ghost = tables.units[UnitTypeId.BARRACKS].ability_requirements[AbilityId.BARRACKS_TRAIN_GHOST]
        assert ghost == TechRequirements(structures=frozenset({UnitTypeId.GHOST_ACADEMY, UnitTypeId.TECH_LAB_BARRACKS}))

    def test_a_thor_needs_an_armory_and_a_tech_lab_on_its_factory(self, tables: GameData) -> None:
        thor = tables.units[UnitTypeId.FACTORY].ability_requirements[AbilityId.FACTORY_TRAIN_THOR]
        assert thor == TechRequirements(structures=frozenset({UnitTypeId.ARMORY, UnitTypeId.TECH_LAB_FACTORY}))

    def test_a_second_level_needs_the_first_and_what_the_second_needs(self, tables: GameData) -> None:
        bay = tables.units[UnitTypeId.ENGINEERING_BAY]
        assert bay.ability_requirements[AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2] == TechRequirements(
            structures=frozenset({UnitTypeId.ARMORY}), upgrades=frozenset({UpgradeId.TERRAN_INFANTRY_WEAPONS_1})
        )

    def test_every_ability_a_type_is_offered_says_what_it_needs(self, tables: GameData) -> None:
        barracks = tables.units[UnitTypeId.BARRACKS]
        assert barracks.ability_requirements[AbilityId.BARRACKS_TRAIN_MARINE] == TechRequirements()
        assert set(barracks.ability_requirements) == barracks.abilities

    def test_a_burrowed_roach_moves_only_with_tunneling_claws(self, tables: GameData) -> None:
        claws = TechRequirements(upgrades=frozenset({UpgradeId.TUNNELING_CLAWS}))
        assert tables.units[UnitTypeId.ROACH_BURROWED].ability_requirements[AbilityId.GENERAL_MOVE_EXACT] == claws
        assert tables.units[UnitTypeId.ROACH].ability_requirements[AbilityId.GENERAL_MOVE_EXACT] == TechRequirements()

    def test_a_barracks_needs_a_depot_which_a_lowered_one_counts_as(self, tables: GameData) -> None:
        barracks = tables.units[UnitTypeId.SCV].ability_requirements[AbilityId.SCV_BUILD_BARRACKS]
        assert barracks.structures == {UnitTypeId.SUPPLY_DEPOT}
        assert UnitTypeId.SUPPLY_DEPOT in tables.units[UnitTypeId.SUPPLY_DEPOT_LOWERED].tech_aliases

    def test_a_hive_counts_as_a_lair(self, tables: GameData) -> None:
        den = tables.units[UnitTypeId.DRONE].ability_requirements[AbilityId.DRONE_MORPH_HYDRALISK_DEN]
        assert den.structures == {UnitTypeId.LAIR}
        assert UnitTypeId.LAIR in tables.units[UnitTypeId.HIVE].tech_aliases

    def test_an_ability_is_performed_by_the_types_offered_it_and_makes_its_product(self, tables: GameData) -> None:
        marine = tables.abilities[AbilityId.BARRACKS_TRAIN_MARINE]
        assert marine.performers == {UnitTypeId.BARRACKS}
        assert marine.product is UnitTypeId.MARINE
        assert tables.abilities[AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK].product is UpgradeId.STIMPACK

    def test_a_general_ability_is_performed_by_whatever_performs_one_standing_for_it(self, tables: GameData) -> None:
        burrow = tables.abilities[AbilityId.GENERAL_BURROW].performers
        assert {UnitTypeId.DRONE, UnitTypeId.ZERGLING, UnitTypeId.ROACH, UnitTypeId.WIDOW_MINE} <= burrow
        assert tables.abilities[AbilityId.ZERGLING_BURROW].performers == {UnitTypeId.ZERGLING}

    def test_a_unit_made_out_of_another_says_which(self, tables: GameData) -> None:
        units = tables.units
        assert units[UnitTypeId.ORBITAL_COMMAND].morphed_from is UnitTypeId.COMMAND_CENTER
        assert units[UnitTypeId.LAIR].morphed_from is UnitTypeId.HATCHERY
        assert units[UnitTypeId.ZERGLING].morphed_from is UnitTypeId.LARVA
        assert units[UnitTypeId.SPAWNING_POOL].morphed_from is UnitTypeId.DRONE
        assert units[UnitTypeId.SIEGE_TANK_SIEGED].morphed_from is UnitTypeId.SIEGE_TANK
        assert units[UnitTypeId.BARRACKS].morphed_from is None
        assert units[UnitTypeId.MARINE].morphed_from is None

    def test_an_unnamed_creation_ability_makes_what_the_table_has_no_working_ability_for(
        self, tables: GameData
    ) -> None:
        baneling = tables.abilities[AbilityId.ZERGLING_MORPH_BANELING]
        assert baneling.product is UnitTypeId.BANELING
        assert tables.units[UnitTypeId.BANELING].creation_ability is AbilityId.ZERGLING_MORPH_BANELING
        assert tables.units[UnitTypeId.BANELING].morphed_from is UnitTypeId.ZERGLING
        assert tables.units[UnitTypeId.LURKER].morphed_from is UnitTypeId.HYDRALISK
        assert tables.units[UnitTypeId.EXTRACTOR_RICH].morphed_from is UnitTypeId.DRONE
        assert tables.units[UnitTypeId.ASSIMILATOR_RICH].morphed_from is None
        # The build a rich refinery shares with a refinery makes a refinery, as the table says.
        assert tables.abilities[AbilityId.SCV_BUILD_REFINERY].product is UnitTypeId.REFINERY

    def test_a_warp_gate_warps_in_what_a_gateway_trains(self, tables: GameData) -> None:
        zealot = tables.abilities[AbilityId.WARP_GATE_WARP_IN_ZEALOT]
        assert (zealot.product, zealot.performers) == (UnitTypeId.ZEALOT, {UnitTypeId.WARP_GATE})
        assert tables.units[UnitTypeId.ZEALOT].creation_ability is AbilityId.GATEWAY_TRAIN_ZEALOT

    def test_a_gateway_needs_power_and_a_nexus_and_a_pylon_do_not(self, tables: GameData) -> None:
        assert tables.units[UnitTypeId.GATEWAY].needs_power
        assert not tables.units[UnitTypeId.NEXUS].needs_power
        assert not tables.units[UnitTypeId.PYLON].needs_power

    def test_a_unit_type_carries_what_it_is_offered(self, tables: GameData) -> None:
        assert AbilityId.MARINE_STIM in tables.units[UnitTypeId.MARINE].abilities

    def test_only_the_known_makers_have_nothing_to_perform_them(self, tables: GameData) -> None:
        """A gateway turns into a warp gate by itself once the research is done, and a liberator reports the exact
        siege it was never offered. Every research ability has a performer."""
        makers = [row.creation_ability for row in tables.units.values()]
        makers += [row.research_ability for row in tables.upgrades.values()]
        unperformed = {
            ability for ability in makers if ability is not None and not tables.abilities[ability].performers
        }
        assert unperformed == {AbilityId.GATEWAY_MORPH_WARP_GATE, AbilityId.LIBERATOR_SIEGE_EXACT}

    def test_a_morph_several_types_perform_is_made_out_of_the_one_the_others_come_from(self, tables: GameData) -> None:
        """An overlord and the overlord transport it becomes can both become an overseer."""
        overseer = tables.abilities[AbilityId.OVERLORD_MORPH_OVERSEER]
        assert overseer.performers == {UnitTypeId.OVERLORD, UnitTypeId.OVERLORD_TRANSPORT}
        assert tables.units[UnitTypeId.OVERSEER].morphed_from is UnitTypeId.OVERLORD

    def test_only_creep_tumors_the_game_will_not_create_are_left_unswept(self, tables: GameData) -> None:
        """A debug command makes no creep tumor, so what one is offered is unknown rather than nothing."""
        unswept = {
            row.id
            for row in tables.units.values()
            if row.race in (Race.TERRAN, Race.PROTOSS, Race.ZERG) and row.id not in _sweep().ability_requirements
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
        other ability doing this would drop a unit type from what performs it without anyone noticing."""
        findings = json.loads(_FINDINGS.read_text(encoding="utf-8"))
        others = {record["ability"] for record in findings["made"] if record["result"] == "other"}
        assert others and all(ability.startswith("BurrowDown_") for ability in others)

    def test_what_is_offered_that_no_curated_id_names_is_known(self) -> None:
        """Anything else a unit type is offered has to be curated, or the tables leave it out."""
        assert _generator().uncurated(_findings_file()) == _UNCURATED_OFFERED


def _offered(transport: WebSocketTransport, unit: Unit) -> set[int]:
    query = query_pb2.RequestQuery(abilities=[query_pb2.RequestQueryAvailableAbilities(unit_tag=unit.tag)])
    return {
        ability.ability_id
        for ability in transport.request(sc2api_pb2.Request(query=query)).query.abilities[0].abilities
    }


@pytest.mark.integration
def test_in_a_real_game_the_tables_say_what_is_offered_and_made() -> None:
    """Run with `pytest -m integration`. Starts the game as terran, and rechecks a few of the tables' facts in it, so
    that a patch moving the tech tree fails here before anywhere else."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with GameProcess.launch(window=(640, 480)) as process:
        transport = WebSocketTransport.connect(process.url)
        with closing(Client(transport)) as client:
            client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
            game = RealGame(client, client.join_game(Race.TERRAN))
            tables = game.tracker.data
            state = debug_pb2.DebugGameState
            game.debug(*(debug_pb2.DebugCommand(game_state=cheat) for cheat in (state.free, state.fast_build)))
            units = game.turn(1)
            home = units.own.of_type(UnitTypeId.COMMAND_CENTER)[0].position
            toward = home.towards(game.map.playable_area.center, 12)

            # A ghost is offered once its barracks has a tech lab and an academy stands, and not before.
            game.debug(game.create(UnitTypeId.BARRACKS, game.open_ground(toward)))
            game.turn(2)
            barracks = game.newest(UnitTypeId.BARRACKS)
            game.order(AbilityId.GENERAL_BUILD_TECH_LAB, barracks, target=barracks.position)
            game.turn(22 * 8)
            assert AbilityId.BARRACKS_TRAIN_GHOST not in _offered(transport, barracks)
            game.debug(game.create(UnitTypeId.GHOST_ACADEMY, game.open_ground(toward.towards(home, -8))))
            game.turn(4)
            assert AbilityId.BARRACKS_TRAIN_GHOST in _offered(transport, barracks)
            ghost = tables.units[UnitTypeId.BARRACKS].ability_requirements[AbilityId.BARRACKS_TRAIN_GHOST]
            assert ghost.structures == {UnitTypeId.GHOST_ACADEMY, UnitTypeId.TECH_LAB_BARRACKS}

            # A second level is offered once the first is done.
            game.debug(game.create(UnitTypeId.ENGINEERING_BAY, game.open_ground(toward.towards(home, 6))))
            game.debug(game.create(UnitTypeId.ARMORY, game.open_ground(toward.towards(home, -14))))
            game.turn(2)
            bay = game.newest(UnitTypeId.ENGINEERING_BAY)
            second = AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_2
            assert second not in _offered(transport, bay)
            game.order(AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, bay)
            for _ in range(100):
                if UpgradeId.TERRAN_INFANTRY_WEAPONS_1 in game.state.upgrades:
                    break
                game.turn(22)
            game.turn(22)
            assert second in _offered(transport, bay)
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
