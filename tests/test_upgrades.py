"""The upgrade tables: what the sweep's findings generate, what the tables then say, and what a unit reads with them."""

import importlib.util
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest
from s2clientprotocol import common_pb2, debug_pb2, raw_pb2

from sc2nachos import Api
from sc2nachos.constants import FASTER_PER_NORMAL_SPEED
from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import (
    Attribute,
    GameData,
    TargetDomain,
    UnitTypeData,
    UnitTypeUpgrade,
    UpgradeType,
    Weapon,
    WeaponUpgrade,
)
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, EffectId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawBuffId
from sc2nachos.launch import GameProcess, Map, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, Recording, ReplayTransport, WebSocketTransport
from sc2nachos.state import Effect
from sc2nachos.units import Alliance, Unit, Visibility
from sc2nachos.units._tracker import _UnitTracker
from sc2nachos.upgrade_reader import UpgradeInference, UpgradeReader
from support import RealGame, make_observation, make_unit

_REPO = Path(__file__).parents[1]
_GENERATOR = _REPO / "tools" / "generate_tech_tree.py"
_CORPUS = sorted((_REPO / "tests" / "corpus").glob("*.sc2rec"))
_NO_LEVELS = {"attack": [], "armor": [], "shield": []}
# A zergling's speed with Metabolic Boost: the game's rows give 4.6992188 a second of its Normal speed.
_BOOSTED_ZERGLING_SPEED = 4.6992188 * FASTER_PER_NORMAL_SPEED


def _generator() -> ModuleType:
    """`tools/generate_tech_tree.py`, which is no package to import from."""
    spec = importlib.util.spec_from_file_location("generate_tech_tree", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _findings(*research: dict[str, object], unexplained: tuple[str, ...] = ()) -> dict[str, object]:
    """Findings as `tools/sweep_upgrades.py` writes them."""
    return {"base_build": 1, "races": [], "research": list(research), "unexplained": list(unexplained)}


def _research(upgrade: str, changes: dict[str, object], **levels: list[str]) -> dict[str, object]:
    return {"upgrades": [upgrade], "changes": changes, "levels": _NO_LEVELS | levels}


def _stalker_weapons(damage: float, bonus: float) -> dict[str, object]:
    return {"weapons": [{"damage": damage, "bonuses": {"Armored": bonus}}]}


class TestGeneratingTheUpgrades:
    def test_a_change_is_read_under_the_curated_ids_a_bonus_the_weapon_lacked_included(self) -> None:
        igniter = _research("HighCapacityBarrels", {"HellionTank": {"weapons": [{"bonuses": {"Light": 12.0}}]}})
        found = _generator().read_upgrades(_findings(igniter))
        assert found.unit_types[UnitTypeId.HELLBAT][UpgradeId.BLUE_FLAME] == UnitTypeUpgrade(
            weapons=(WeaponUpgrade(damage_bonuses=MappingProxyType({Attribute.LIGHT: 12.0})),)
        )
        assert UpgradeId.BLUE_FLAME not in found.types

    def test_a_level_affects_every_type_it_raises_the_report_of_or_changes_the_row_of(self) -> None:
        """A void ray reports its attack level with no weapon in the rows, and no mothership stands in the sweep to
        report its level while air weapons change its weapon all the same."""
        weapons = _research(
            "ProtossAirWeaponsLevel1",
            {"Phoenix": {"weapons": [{"damage": 1.0}]}, "Mothership": {"weapons": [{"damage": 1.0}]}},
            attack=["Phoenix", "VoidRay"],
        )
        found = _generator().read_upgrades(_findings(weapons))
        assert set(found.unit_types) == {UnitTypeId.PHOENIX, UnitTypeId.VOID_RAY, UnitTypeId.MOTHERSHIP}
        assert found.unit_types[UnitTypeId.VOID_RAY][UpgradeId.PROTOSS_AIR_WEAPONS_1] == UnitTypeUpgrade()
        assert found.types[UpgradeId.PROTOSS_AIR_WEAPONS_1] is UpgradeType.ATTACK
        assert found.levels[UpgradeId.PROTOSS_AIR_WEAPONS_1] == 1

    def test_an_upgrade_raising_a_report_with_no_number_in_its_name_is_no_level(self) -> None:
        plating = _research("ChitinousPlating", {"Ultralisk": {"armor": 2.0}}, armor=["Ultralisk"])
        found = _generator().read_upgrades(_findings(plating))
        assert found.types[UpgradeId.ULTRALISK_ARMOR] is UpgradeType.ARMOR
        assert UpgradeId.ULTRALISK_ARMOR not in found.levels

    def test_a_shields_level_affects_the_types_that_report_it_and_changes_no_row(self) -> None:
        shields = _research("ProtossShieldsLevel1", {}, shield=["Zealot", "Pylon"])
        found = _generator().read_upgrades(_findings(shields))
        assert found.unit_types[UnitTypeId.PYLON] == {UpgradeId.PROTOSS_SHIELDS_1: UnitTypeUpgrade()}
        assert found.types[UpgradeId.PROTOSS_SHIELDS_1] is UpgradeType.SHIELD

    def test_levels_that_add_different_amounts_to_one_type_are_refused(self) -> None:
        """A unit reports only how many attack levels it has, so what each adds has to be the same."""
        levels = [
            _research("ProtossGroundWeaponsLevel1", {"Stalker": _stalker_weapons(1.0, 1.0)}, attack=["Stalker"]),
            _research("ProtossGroundWeaponsLevel2", {"Stalker": _stalker_weapons(2.0, 1.0)}, attack=["Stalker"]),
        ]
        with pytest.raises(ValueError, match="ATTACK levels of STALKER add different amounts"):
            _generator().read_upgrades(_findings(*levels))

    def test_levels_of_a_type_that_do_not_start_at_1_are_refused(self) -> None:
        second = _research("ProtossGroundWeaponsLevel2", {"Stalker": _stalker_weapons(1.0, 1.0)}, attack=["Stalker"])
        with pytest.raises(ValueError, match="ATTACK levels affecting STALKER are not 1 onwards"):
            _generator().read_upgrades(_findings(second))

    def test_an_upgrade_raising_two_kinds_of_report_is_refused(self) -> None:
        both = _research("ChitinousPlating", {"Ultralisk": {"armor": 2.0}}, armor=["Ultralisk"], attack=["Ultralisk"])
        with pytest.raises(ValueError, match="ARMOR and ATTACK"):
            _generator().read_upgrades(_findings(both))

    def test_what_the_changes_do_not_account_for_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Marine is"):
            _generator().read_upgrades(_findings(unexplained=("Marine is (0.0, 2.25, [])",)))

    def test_a_change_to_a_weapons_cooldown_is_refused(self) -> None:
        """The tables carry no change to it, since no upgrade makes one in the game's rows."""
        glands = _research("zerglingattackspeed", {"Zergling": {"weapons": [{"cooldown": -0.2}]}})
        with pytest.raises(ValueError, match="cooldown"):
            _generator().read_upgrades(_findings(glands))

    def test_one_research_finishing_two_upgrades_that_change_something_is_refused(self) -> None:
        both = {"upgrades": ["Charge", "BlinkTech"], "changes": {"Zealot": {"speed": 1.0}}, "levels": _NO_LEVELS}
        with pytest.raises(ValueError, match="Charge, BlinkTech"):
            _generator().read_upgrades(_findings(both))

    def test_an_uncurated_unit_type_is_left_out_and_named(self) -> None:
        weapons = _research(
            "TerranInfantryWeaponsLevel1",
            {"Marine": {"weapons": [{"damage": 1.0}]}, "HERC": {"weapons": [{"damage": 2.0}]}},
            attack=["Marine"],
        )
        generator = _generator()
        assert set(generator.read_upgrades(_findings(weapons)).unit_types) == {UnitTypeId.MARINE}
        assert generator.uncurated_upgraded(_findings(weapons)) == {"HERC"}

    def test_an_uncurated_upgrade_that_affects_a_curated_unit_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="CampaignUpgrade affects Marine"):
            _generator().read_upgrades(_findings(_research("CampaignUpgrade", {"Marine": {"armor": 1.0}})))


@pytest.fixture(scope="module")
def tables() -> GameData:
    """The tables of the first corpus game, which every game on the current ladder shares."""
    recording = Recording(_CORPUS[0])
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


def _levels(family: str) -> tuple[UpgradeId, UpgradeId, UpgradeId]:
    """The three levels of the upgrade `family`, such as `TERRAN_INFANTRY_WEAPONS`."""
    return UpgradeId[f"{family}_1"], UpgradeId[f"{family}_2"], UpgradeId[f"{family}_3"]


_GROUND, _AIR, _ANY = TargetDomain.GROUND, TargetDomain.AIR, TargetDomain.ANY
_LIGHT, _ARMORED, _MASSIVE = Attribute.LIGHT, Attribute.ARMORED, Attribute.MASSIVE

# A weapon's damage before any level and at each of the three, and its bonus against one attribute likewise, as the
# game's help gives them. One level adds different amounts to different types, and to a type's bonus or not.
_WEAPON_LEVELS: list[tuple[UnitTypeId, str, TargetDomain, list[int], Attribute | None, list[int]]] = [
    (UnitTypeId.MARINE, "TERRAN_INFANTRY_WEAPONS", _ANY, [6, 7, 8, 9], None, []),
    (UnitTypeId.MARAUDER, "TERRAN_INFANTRY_WEAPONS", _GROUND, [10, 11, 12, 13], _ARMORED, [10, 11, 12, 13]),
    (UnitTypeId.GHOST, "TERRAN_INFANTRY_WEAPONS", _ANY, [10, 11, 12, 13], _LIGHT, [10, 11, 12, 13]),
    (UnitTypeId.HELLION, "TERRAN_VEHICLE_WEAPONS", _GROUND, [8, 9, 10, 11], _LIGHT, [6, 7, 8, 9]),
    (UnitTypeId.SIEGE_TANK, "TERRAN_VEHICLE_WEAPONS", _GROUND, [15, 17, 19, 21], _ARMORED, [10, 11, 12, 13]),
    (UnitTypeId.SIEGE_TANK_SIEGED, "TERRAN_VEHICLE_WEAPONS", _GROUND, [40, 44, 48, 52], _ARMORED, [30, 31, 32, 33]),
    (UnitTypeId.THOR, "TERRAN_VEHICLE_WEAPONS", _GROUND, [30, 33, 36, 39], None, []),
    (UnitTypeId.THOR, "TERRAN_VEHICLE_WEAPONS", _AIR, [6, 7, 8, 9], _LIGHT, [6, 7, 8, 9]),
    (UnitTypeId.VIKING, "TERRAN_SHIP_WEAPONS", _AIR, [10, 11, 12, 13], _ARMORED, [4, 4, 4, 4]),
    (UnitTypeId.BANSHEE, "TERRAN_SHIP_WEAPONS", _GROUND, [12, 13, 14, 15], None, []),
    (UnitTypeId.LIBERATOR_SIEGED, "TERRAN_SHIP_WEAPONS", _GROUND, [75, 80, 85, 90], None, []),
    (UnitTypeId.ZEALOT, "PROTOSS_GROUND_WEAPONS", _GROUND, [8, 9, 10, 11], None, []),
    (UnitTypeId.STALKER, "PROTOSS_GROUND_WEAPONS", _ANY, [13, 14, 15, 16], _ARMORED, [5, 6, 7, 8]),
    (UnitTypeId.IMMORTAL, "PROTOSS_GROUND_WEAPONS", _GROUND, [20, 22, 24, 26], _ARMORED, [30, 33, 36, 39]),
    (UnitTypeId.DARK_TEMPLAR, "PROTOSS_GROUND_WEAPONS", _GROUND, [45, 50, 55, 60], None, []),
    (UnitTypeId.PHOENIX, "PROTOSS_AIR_WEAPONS", _AIR, [5, 6, 7, 8], _LIGHT, [5, 5, 5, 5]),
    (UnitTypeId.TEMPEST, "PROTOSS_AIR_WEAPONS", _AIR, [30, 33, 36, 39], _MASSIVE, [22, 24, 26, 28]),
    (UnitTypeId.TEMPEST, "PROTOSS_AIR_WEAPONS", _GROUND, [40, 44, 48, 52], None, []),
    (UnitTypeId.ZERGLING, "ZERG_MELEE_WEAPONS", _GROUND, [5, 6, 7, 8], None, []),
    (UnitTypeId.ULTRALISK, "ZERG_MELEE_WEAPONS", _GROUND, [35, 38, 41, 44], None, []),
    (UnitTypeId.ROACH, "ZERG_RANGE_WEAPONS", _GROUND, [16, 18, 20, 22], None, []),
    (UnitTypeId.HYDRALISK, "ZERG_RANGE_WEAPONS", _ANY, [12, 13, 14, 15], None, []),
    (UnitTypeId.LURKER_BURROWED, "ZERG_RANGE_WEAPONS", _GROUND, [20, 22, 24, 26], _ARMORED, [10, 11, 12, 13]),
    (UnitTypeId.MUTALISK, "ZERG_AIR_WEAPONS", _ANY, [9, 10, 11, 12], None, []),
    (UnitTypeId.CORRUPTOR, "ZERG_AIR_WEAPONS", _AIR, [14, 15, 16, 17], _MASSIVE, [6, 7, 8, 9]),
    (UnitTypeId.BROOD_LORD, "ZERG_AIR_WEAPONS", _GROUND, [20, 22, 24, 26], None, []),
]

# A type's armor before any level and at each of the three.
_ARMOR_LEVELS = [
    (UnitTypeId.MARINE, "TERRAN_INFANTRY_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.MARAUDER, "TERRAN_INFANTRY_ARMOR", [1, 2, 3, 4]),
    (UnitTypeId.SCV, "TERRAN_INFANTRY_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.SIEGE_TANK, "TERRAN_VEHICLE_AND_SHIP_ARMOR", [1, 2, 3, 4]),
    (UnitTypeId.VIKING, "TERRAN_VEHICLE_AND_SHIP_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.BATTLECRUISER, "TERRAN_VEHICLE_AND_SHIP_ARMOR", [3, 4, 5, 6]),
    (UnitTypeId.ZEALOT, "PROTOSS_GROUND_ARMOR", [1, 2, 3, 4]),
    (UnitTypeId.PROBE, "PROTOSS_GROUND_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.ARCHON, "PROTOSS_GROUND_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.PHOENIX, "PROTOSS_AIR_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.CARRIER, "PROTOSS_AIR_ARMOR", [2, 3, 4, 5]),
    (UnitTypeId.ZERGLING, "ZERG_GROUND_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.ROACH, "ZERG_GROUND_ARMOR", [1, 2, 3, 4]),
    (UnitTypeId.ULTRALISK, "ZERG_GROUND_ARMOR", [2, 3, 4, 5]),
    (UnitTypeId.OVERLORD, "ZERG_AIR_ARMOR", [0, 1, 2, 3]),
    (UnitTypeId.CORRUPTOR, "ZERG_AIR_ARMOR", [2, 3, 4, 5]),
]


def _reports(tables: GameData, upgrades: Iterable[UpgradeId]) -> list[tuple[UpgradeType | None, int]]:
    """The upgrade level each of `upgrades` adds to where units report it, and which level of its line it is."""
    return [(tables.upgrades[upgrade].type, tables.upgrades[upgrade].level) for upgrade in upgrades]


def _weapon(row: UnitTypeData, domain: TargetDomain) -> Weapon:
    return next(weapon for weapon in row.weapons if weapon.target_domain is domain)


class TestWhatTheTablesSay:
    @pytest.mark.parametrize(
        ("unit_type", "family", "domain", "damages", "attribute", "bonuses"),
        _WEAPON_LEVELS,
        ids=[f"{unit_type.name}-{family}-{domain.name}" for unit_type, family, domain, *_ in _WEAPON_LEVELS],
    )
    def test_each_weapons_level_adds_what_it_adds_to_that_type(
        self,
        tables: GameData,
        unit_type: UnitTypeId,
        family: str,
        domain: TargetDomain,
        damages: list[int],
        attribute: Attribute | None,
        bonuses: list[int],
    ) -> None:
        row = tables.units[unit_type]
        levels = _levels(family)
        assert _reports(tables, levels) == [(UpgradeType.ATTACK, level) for level in (1, 2, 3)]
        assert set(levels) <= row.upgrades.keys()
        for level in range(4):
            weapon = _weapon(row.with_upgrades(levels[:level]), domain)
            expected = {attribute: bonuses[level]} if attribute is not None else {}
            assert (weapon.damage, dict(weapon.damage_bonuses)) == (damages[level], expected), f"level {level}"

    @pytest.mark.parametrize(
        ("unit_type", "family", "by_level"),
        _ARMOR_LEVELS,
        ids=[f"{unit_type.name}-{family}" for unit_type, family, _ in _ARMOR_LEVELS],
    )
    def test_each_armor_level_adds_one_armor(
        self, tables: GameData, unit_type: UnitTypeId, family: str, by_level: list[float]
    ) -> None:
        row = tables.units[unit_type]
        levels = _levels(family)
        assert _reports(tables, levels) == [(UpgradeType.ARMOR, level) for level in (1, 2, 3)]
        assert [row.with_upgrades(levels[:level]).armor for level in range(4)] == by_level

    def test_one_level_adds_different_amounts_to_different_types(self, tables: GameData) -> None:
        first = UpgradeId.TERRAN_INFANTRY_WEAPONS_1
        infantry = (UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.GHOST)
        changes = {unit: tables.units[unit].upgrades[first].weapons[0] for unit in infantry}
        assert changes == {
            UnitTypeId.MARINE: WeaponUpgrade(damage=1.0),
            UnitTypeId.MARAUDER: WeaponUpgrade(damage=1.0, damage_bonuses=MappingProxyType({_ARMORED: 1.0})),
            UnitTypeId.GHOST: WeaponUpgrade(damage=1.0, damage_bonuses=MappingProxyType({_LIGHT: 1.0})),
        }

    def test_neosteel_armor_adds_two_to_a_terran_structure(self, tables: GameData) -> None:
        structures = (UnitTypeId.COMMAND_CENTER, UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.MISSILE_TURRET)
        armor = [tables.units[unit].with_upgrades({UpgradeId.BUILDING_ARMOR}).armor for unit in structures]
        assert armor == [3, 4, 2]

    def test_chitinous_plating_adds_two_on_top_of_the_armor_levels(self, tables: GameData) -> None:
        ultralisk = tables.units[UnitTypeId.ULTRALISK]
        assert ultralisk.with_upgrades({*_levels("ZERG_GROUND_ARMOR"), UpgradeId.ULTRALISK_ARMOR}).armor == 7
        assert _reports(tables, [UpgradeId.ULTRALISK_ARMOR]) == [(UpgradeType.ARMOR, 0)]

    def test_a_shields_level_affects_protoss_types_and_changes_no_row(self, tables: GameData) -> None:
        """The game's rows hold no armor for shields, so the levels, which add to it, change nothing a row holds."""
        shields = _levels("PROTOSS_SHIELDS")
        assert _reports(tables, shields) == [(UpgradeType.SHIELD, level) for level in (1, 2, 3)]
        for unit_type in (UnitTypeId.ZEALOT, UnitTypeId.PYLON, UnitTypeId.VOID_RAY, UnitTypeId.NEXUS):
            row = tables.units[unit_type]
            assert all(row.upgrades[upgrade] == UnitTypeUpgrade() for upgrade in shields)
            assert row.with_upgrades(shields) is row

    @pytest.mark.parametrize(
        ("unit_type", "family"),
        [(UnitTypeId.VOID_RAY, "PROTOSS_AIR_WEAPONS"), (UnitTypeId.SENTRY, "PROTOSS_GROUND_WEAPONS")],
    )
    def test_attack_levels_affect_a_type_the_rows_give_no_weapon(
        self, tables: GameData, unit_type: UnitTypeId, family: str
    ) -> None:
        row = tables.units[unit_type]
        assert not row.weapons
        assert all(row.upgrades[upgrade] == UnitTypeUpgrade() for upgrade in _levels(family))

    def test_an_upgrade_no_unit_reports_is_of_no_kind_and_no_level(self, tables: GameData) -> None:
        other = (UpgradeType.OTHER, 0)
        assert _reports(tables, [UpgradeId.HYDRALISK_RANGE, UpgradeId.ZERGLING_SPEED]) == [other, other]

    def test_infernal_pre_igniter_gives_a_hellbat_a_bonus_against_light(self, tables: GameData) -> None:
        hellbat = tables.units[UnitTypeId.HELLBAT]
        assert Attribute.LIGHT not in hellbat.weapons[0].damage_bonuses
        assert hellbat.with_upgrades({UpgradeId.BLUE_FLAME}).weapons[0].damage_bonuses[Attribute.LIGHT] > 0

    def test_grooved_spines_adds_to_a_hydralisks_range(self, tables: GameData) -> None:
        hydralisk = tables.units[UnitTypeId.HYDRALISK]
        assert hydralisk.with_upgrades({UpgradeId.HYDRALISK_RANGE}).weapons[0].range == hydralisk.weapons[0].range + 1

    def test_metabolic_boost_makes_a_zergling_faster(self, tables: GameData) -> None:
        zergling = tables.units[UnitTypeId.ZERGLING]
        assert zergling.with_upgrades({UpgradeId.ZERGLING_SPEED}).speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)

    def test_upgrades_that_change_nothing_about_a_type_leave_its_row_as_it_is(self, tables: GameData) -> None:
        marine = tables.units[UnitTypeId.MARINE]
        assert marine.with_upgrades(()) is marine
        assert marine.with_upgrades({UpgradeId.ADRENAL_GLANDS, UpgradeId.STIMPACK}) is marine


def _tracker(tables: GameData) -> _UnitTracker:
    return _UnitTracker(tables, Enemy())


def _observe(
    tracker: _UnitTracker, *units: raw_pb2.Unit, upgrades: tuple[int, ...] = (), step: int = 0
) -> list[Unit[Any]]:
    tracker.update(make_observation(step, units=units, upgrades=upgrades).observation.raw_data, step)
    tracker.enemy.assume_upgrades(*tracker.upgrade_reader.levels_shown_by(tracker.present_units))
    by_tag = {unit.tag: unit for unit in tracker.present_units}
    return [by_tag[unit.tag] for unit in units]


class TestWhatAUnitReads:
    def test_this_players_units_read_with_its_upgrades(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        (zergling,) = _observe(tracker, make_unit(1, UnitTypeId.ZERGLING), upgrades=(UpgradeId.ZERGLING_SPEED,))
        assert zergling.speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)

    def test_this_players_units_show_it_nothing_it_has_not_researched(self, tables: GameData) -> None:
        """What its own units report is what its own upgrades say, so nothing is read off them."""
        tracker = _tracker(tables)
        _observe(tracker, make_unit(1, attack_upgrade_level=3))
        assert not tracker.enemy.upgrades

    def test_the_enemys_units_read_with_what_it_is_assumed_to_have(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        (zergling,) = _observe(tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY))
        base = tables.units[UnitTypeId.ZERGLING].speed
        assert zergling.speed == base
        tracker.enemy.assume_upgrades(UpgradeId.ZERGLING_SPEED)
        assert zergling.speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)
        tracker.enemy.forget_upgrades(UpgradeId.ZERGLING_SPEED)
        assert zergling.speed == base

    def test_a_neutral_unit_reads_with_no_upgrade(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        tracker.enemy.assume_upgrades(UpgradeId.ZERGLING_SPEED)
        (zergling,) = _observe(
            tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.NEUTRAL), upgrades=(UpgradeId.ZERGLING_SPEED,)
        )
        assert zergling.speed == tables.units[UnitTypeId.ZERGLING].speed

    def test_an_enemy_unit_in_sight_shows_the_levels_of_its_own_lines(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        _observe(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=2))
        assert tracker.enemy.upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_1, UpgradeId.TERRAN_INFANTRY_WEAPONS_2}

    def test_what_one_unit_shows_counts_for_every_type_of_its_line(self, tables: GameData) -> None:
        """A marine's attack level is Terran Infantry Weapons, which a marauder has too and a viking does not."""
        tracker = _tracker(tables)
        marauder = make_unit(2, UnitTypeId.MARAUDER, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        viking = make_unit(3, UnitTypeId.VIKING, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        units = _observe(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=2), marauder, viking)
        assert units[1].weapons[0].damage == tables.units[UnitTypeId.MARAUDER].weapons[0].damage + 2
        assert units[2].weapons[0].damage == tables.units[UnitTypeId.VIKING].weapons[0].damage

    def test_a_unit_out_of_sight_counts_a_level_another_showed_since(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        (marine,) = _observe(tracker, make_unit(1, alliance=Alliance.ENEMY))
        base = tables.units[UnitTypeId.MARINE].weapons[0].damage
        assert marine.weapons[0].damage == base
        _observe(tracker, make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=1), step=1)
        assert marine.is_stale
        assert marine.weapons[0].damage == base + 1

    def test_a_level_a_unit_showed_stays_known_once_it_is_gone(self, tables: GameData) -> None:
        """Levels are never lost, so what one unit showed holds for the rest of the game."""
        tracker = _tracker(tables)
        _observe(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=1))
        _observe(tracker, make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=0))
        assert tracker.enemy.upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_1}

    @pytest.mark.parametrize("level", [0, 1, 2, 3])
    def test_an_enemy_marauder_reads_the_damage_and_bonus_of_the_level_it_shows(
        self, tables: GameData, level: int
    ) -> None:
        tracker = _tracker(tables)
        (marauder,) = _observe(
            tracker, make_unit(1, UnitTypeId.MARAUDER, alliance=Alliance.ENEMY, attack_upgrade_level=level)
        )
        weapon = marauder.weapons[0]
        assert (weapon.damage, dict(weapon.damage_bonuses)) == (10 + level, {Attribute.ARMORED: 10 + level})

    def test_the_armor_a_zergling_shows_is_the_ground_armor_levels(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        _observe(tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, armor_upgrade_level=2))
        assert tracker.enemy.upgrades == {UpgradeId.ZERG_GROUND_ARMOR_1, UpgradeId.ZERG_GROUND_ARMOR_2}

    def test_the_armor_an_ultralisk_shows_says_nothing_about_the_levels(self, tables: GameData) -> None:
        """Its 2 could be two levels or Chitinous Plating, and taking it for levels would armor every zergling."""
        tracker = _tracker(tables)
        (ultralisk,) = _observe(
            tracker, make_unit(1, UnitTypeId.ULTRALISK, alliance=Alliance.ENEMY, armor_upgrade_level=2)
        )
        assert not tracker.enemy.upgrades
        assert ultralisk.armor == tables.units[UnitTypeId.ULTRALISK].armor + 2

    def test_a_unit_in_sight_reads_the_armor_it_reports(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        base = tables.units[UnitTypeId.ULTRALISK].armor
        (ultralisk,) = _observe(
            tracker, make_unit(1, UnitTypeId.ULTRALISK, alliance=Alliance.ENEMY, armor_upgrade_level=5)
        )
        assert ultralisk.armor == base + 5

    def test_a_unit_never_shown_in_sight_reads_the_armor_the_enemy_is_known_to_have(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        tracker.enemy.assume_upgrades(UpgradeId.BUILDING_ARMOR)
        remembered = make_unit(1, UnitTypeId.MISSILE_TURRET, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
        assert _observe(tracker, remembered)[0].armor == tables.units[UnitTypeId.MISSILE_TURRET].armor + 2

    def test_shield_armor_is_the_shields_levels(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        (zealot,) = _observe(tracker, make_unit(1, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, shield_upgrade_level=2))
        assert zealot.shield_armor == 2
        assert tracker.enemy.upgrades == {UpgradeId.PROTOSS_SHIELDS_1, UpgradeId.PROTOSS_SHIELDS_2}
        never_seen = make_unit(2, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert _observe(tracker, never_seen)[0].shield_armor == 2

    def test_a_unit_without_shields_has_no_shield_armor(self, tables: GameData) -> None:
        tracker = _tracker(tables)
        tracker.enemy.assume_upgrades(UpgradeId.PROTOSS_SHIELDS_1)
        (marine,) = _observe(tracker, make_unit(1, alliance=Alliance.ENEMY))
        assert marine.shield_armor == 0


def _evident(tables: GameData, *units: raw_pb2.Unit, effects: tuple[Effect, ...] = ()) -> frozenset[UpgradeId]:
    """What `units` and `effects`, in one observation, show of the enemy's upgrades beyond their levels."""
    tracker = _tracker(tables)
    _observe(tracker, *units)
    return UpgradeReader(tables).signs_shown_by(tracker.present_units, effects)


def _storm(alliance: Alliance) -> Effect:
    position = common_pb2.Point2D(x=10.0, y=10.0)
    return Effect.from_proto(
        raw_pb2.Effect(effect_id=EffectId.HIGH_TEMPLAR_STORM, pos=[position], radius=1.5, alliance=alliance.value)
    )


class TestWhatAUnitGivesAway:
    def test_an_enemy_warp_gate_shows_warp_gate(self, tables: GameData) -> None:
        remembered = make_unit(1, UnitTypeId.WARP_GATE, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
        assert _evident(tables, remembered) == {UpgradeId.WARP_GATE}

    def test_a_burrowed_enemy_shows_burrow_undetected(self, tables: GameData) -> None:
        zergling = make_unit(1, UnitTypeId.ZERGLING_BURROWED, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert _evident(tables, zergling) == {UpgradeId.BURROW}

    @pytest.mark.parametrize("unit_type", [UnitTypeId.LURKER_BURROWED, UnitTypeId.WIDOW_MINE_BURROWED])
    def test_what_burrows_without_burrow_shows_nothing(self, tables: GameData, unit_type: UnitTypeId) -> None:
        assert not _evident(tables, make_unit(1, unit_type, alliance=Alliance.ENEMY))

    @pytest.mark.parametrize(
        ("unit_type", "buff", "upgrade"),
        [
            (UnitTypeId.MARINE, BuffId.MARINE_STIMMED, UpgradeId.STIMPACK),
            (UnitTypeId.MARAUDER, BuffId.MARAUDER_STIMMED, UpgradeId.STIMPACK),
            (UnitTypeId.ZEALOT, BuffId.ZEALOT_CHARGING, UpgradeId.CHARGE),
            (UnitTypeId.HYDRALISK, BuffId.HYDRALISK_LUNGE, UpgradeId.HYDRALISK_LUNGE),
            (UnitTypeId.BANSHEE, BuffId.BANSHEE_CLOAK, UpgradeId.BANSHEE_CLOAK),
            (UnitTypeId.GHOST, BuffId.GHOST_CLOAK, UpgradeId.GHOST_CLOAK),
        ],
    )
    def test_a_buff_an_enemy_puts_on_itself_shows_the_upgrade_its_ability_needs(
        self, tables: GameData, unit_type: UnitTypeId, buff: BuffId, upgrade: UpgradeId
    ) -> None:
        assert _evident(tables, make_unit(1, unit_type, alliance=Alliance.ENEMY, buff_ids=[buff])) == {upgrade}

    def test_a_buff_on_this_players_unit_shows_the_enemy_put_it_on(self, tables: GameData) -> None:
        slowed = make_unit(1, buff_ids=[BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW])
        matrixed = make_unit(2, UnitTypeId.SIEGE_TANK, buff_ids=[BuffId.RAVEN_INTERFERENCE_MATRIX])
        assert _evident(tables, slowed, matrixed) == {UpgradeId.CONCUSSIVE_SHELLS, UpgradeId.INTERFERENCE_MATRIX}

    def test_a_buff_on_an_enemy_this_player_put_on_shows_nothing_of_the_enemy(self, tables: GameData) -> None:
        slowed = make_unit(
            1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, buff_ids=[BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW]
        )
        assert not _evident(tables, slowed)

    def test_an_enemy_marine_with_more_than_45_health_shows_combat_shield(self, tables: GameData) -> None:
        shielded = make_unit(1, alliance=Alliance.ENEMY, health=55.0, health_max=55.0)
        plain = make_unit(2, alliance=Alliance.ENEMY, health=45.0, health_max=45.0)
        assert _evident(tables, shielded) == {UpgradeId.COMBAT_SHIELD}
        assert not _evident(tables, plain)

    def test_a_unit_the_enemy_has_parasited_shows_only_neural_parasite(self, tables: GameData) -> None:
        """What it wore and was made as belong to the player it was taken from."""
        taken = make_unit(
            1,
            alliance=Alliance.ENEMY,
            health_max=55.0,
            buff_ids=[BuffId.INFESTOR_NEURAL_PARASITE, BuffId.MARINE_STIMMED],
        )
        assert _evident(tables, taken) == {UpgradeId.NEURAL_PARASITE}

    def test_only_the_enemys_storm_shows_storm(self, tables: GameData) -> None:
        assert _evident(tables, effects=(_storm(Alliance.ENEMY),)) == {UpgradeId.STORM}
        assert not _evident(tables, effects=(_storm(Alliance.OWN),))

    def test_nothing_out_of_sight_shows_what_it_wore(self, tables: GameData) -> None:
        remembered = make_unit(
            1,
            UnitTypeId.MISSILE_TURRET,
            alliance=Alliance.ENEMY,
            visibility=Visibility.IN_FOG,
            buff_ids=[BuffId.GHOST_CLOAK],
        )
        assert not _evident(tables, remembered)

    def test_a_buff_the_curated_ids_leave_out_raises(self, tables: GameData) -> None:
        with pytest.raises(UncuratedIdError, match="DutchMarauderSlow"):
            _evident(tables, make_unit(1, alliance=Alliance.ENEMY, buff_ids=[RawBuffId.DutchMarauderSlow]))


# What the enemy's units show of their upgrades in each recorded game, which is nothing in the two the enemy
# researched nothing in.
_LEARNED_IN_THE_CORPUS = {
    "IncorporealAIE_v4-PvZ": {
        UpgradeId.ZERG_GROUND_ARMOR_1,
        UpgradeId.ZERG_MELEE_WEAPONS_1,
        UpgradeId.ZERG_RANGE_WEAPONS_1,
        UpgradeId.ZERG_RANGE_WEAPONS_2,
    },
    "LeyLinesAIE_v3-ZvP": set(),
    "MagannathaAIE_v2-TvT": {UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_1, UpgradeId.TERRAN_VEHICLE_WEAPONS_1},
    "PersephoneAIE_v4-PvT": {
        UpgradeId.TERRAN_INFANTRY_ARMOR_1,
        UpgradeId.TERRAN_INFANTRY_WEAPONS_1,
        UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_1,
        UpgradeId.TERRAN_VEHICLE_WEAPONS_1,
    },
    "PylonAIE_v4-TvZ": {
        UpgradeId.ZERG_GROUND_ARMOR_1,
        UpgradeId.ZERG_MELEE_WEAPONS_1,
        UpgradeId.ZERG_RANGE_WEAPONS_1,
    },
    "TorchesAIE_v4-TvP": {UpgradeId.PROTOSS_GROUND_ARMOR_1, UpgradeId.PROTOSS_GROUND_WEAPONS_1},
    "UltraloveAIE_v2-ZvT": set(),
}


def _replay(path: Path, api: Api) -> Api:
    """`api` once it has played the recorded game at `path` to its end."""
    client = Client(ReplayTransport(Recording(path)))
    client.create_game("recorded", [Participant(), Computer()])
    client.join_game(Race.RANDOM)
    api.play(client)
    return api


@pytest.mark.parametrize("inference", [UpgradeInference.BASIC, UpgradeInference.INTERMEDIATE])
@pytest.mark.parametrize("path", _CORPUS, ids=lambda path: path.stem)
def test_a_recorded_game_shows_what_its_enemy_researched(path: Path, inference: UpgradeInference) -> None:
    """The computer researches while a corpus game runs, and its units carry the levels where NachOS reads them. None
    of them shows anything more, since nothing of this player's leaves its base to see it."""
    api = Api(infer_enemy_upgrades=inference)
    assert _replay(path, api).enemy.upgrades == _LEARNED_IN_THE_CORPUS[path.stem]


@pytest.mark.parametrize("path", _CORPUS, ids=lambda path: path.stem)
def test_an_api_told_to_infer_nothing_leaves_the_enemys_upgrades_to_the_bot(path: Path) -> None:
    api = Api(infer_enemy_upgrades=UpgradeInference.NONE)
    assert not _replay(path, api).enemy.upgrades


def _upgraded(row: UnitTypeData) -> list[float]:
    """What upgrades change of `row`, as numbers in a fixed order."""
    values = [row.armor, row.speed]
    for weapon in row.weapons:
        values += [weapon.damage, weapon.range, *(weapon.damage_bonuses.get(a, 0.0) for a in Attribute)]
    return values


@pytest.mark.integration
def test_in_a_real_game_the_tables_with_this_players_upgrades_are_what_the_game_says_asked_again() -> None:
    """Run with `pytest -m integration`. Starts the game as terran, researches a leveled and an unleveled upgrade, and
    checks every curated unit type's row against the game's rows asked again, and every unit's armor."""
    try:
        game_map = Map.find("PylonAIE_v4")
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
            game.debug(
                game.create(UnitTypeId.ENGINEERING_BAY, game.open_ground(toward)),
                game.create(UnitTypeId.MARINE, toward.towards(home, 4)),
                game.create(UnitTypeId.MISSILE_TURRET, game.open_ground(toward.towards(home, -8))),
            )
            game.turn(2)
            bay = game.newest(UnitTypeId.ENGINEERING_BAY)
            for ability, upgrade in (
                (AbilityId.ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS_1, UpgradeId.TERRAN_INFANTRY_WEAPONS_1),
                (AbilityId.ENGINEERING_BAY_RESEARCH_HISEC_AUTO_TRACKING, UpgradeId.HISEC_AUTO_TRACKING),
                (AbilityId.ENGINEERING_BAY_RESEARCH_BUILDING_ARMOR, UpgradeId.BUILDING_ARMOR),
            ):
                game.order(ability, bay)
                for _ in range(100):
                    if upgrade in game.state.upgrades:
                        break
                    game.turn(22)
                assert upgrade in game.state.upgrades
            game.turn(22)

            asked = GameData(client.game_data())
            differ = [
                unit_type.name
                for unit_type, row in tables.units.items()
                if _upgraded(row.with_upgrades(game.state.upgrades)) != pytest.approx(_upgraded(asked.units[unit_type]))
            ]
            assert not differ
            for unit in game.tracker.present_units.own:
                assert unit.armor == asked.units[unit.type_id].armor
            marine = game.newest(UnitTypeId.MARINE)
            assert marine.weapons[0].damage == asked.units[UnitTypeId.MARINE].weapons[0].damage
            assert marine.shield_armor == 0

            # The shields levels are the armor a protoss unit's shields have, which no row carries.
            # A forge and the pylon beside it fill five tiles a side.
            spot = game.open_ground(toward.towards(home, -16), size=5) - (1, 1)
            game.debug(
                game.create(UnitTypeId.FORGE, spot),
                # Where `tools/sweep_tech_tree.py` puts a pylon to power a structure it has just created.
                game.create(UnitTypeId.PYLON, spot + (2.5, 2.5)),
                game.create(UnitTypeId.ZEALOT, toward.towards(home, 6)),
            )
            game.turn(22)
            forge = game.newest(UnitTypeId.FORGE)
            assert forge.is_powered, "the pylon did not go up beside the forge"
            zealot = game.newest(UnitTypeId.ZEALOT)
            assert zealot.shield_armor == 0
            game.order(AbilityId.FORGE_RESEARCH_SHIELDS_1, forge)
            for _ in range(100):
                if UpgradeId.PROTOSS_SHIELDS_1 in game.state.upgrades:
                    break
                game.turn(22)
            game.turn(22)
            assert UpgradeId.PROTOSS_SHIELDS_1 in game.state.upgrades
            assert zealot.shield_armor == 1
            client.leave_game()


@contextmanager
def _signs_game(race: Race) -> Iterator[tuple[RealGame, UpgradeReader, Point]]:
    """A game as `race` under `free` and `fast_build`, how its units give away upgrades, and ground 10 tiles from home
    toward the middle."""
    try:
        game_map = Map.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))
    with GameProcess.launch(window=(640, 480)) as process:
        transport = WebSocketTransport.connect(process.url)
        with closing(Client(transport)) as client:
            client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
            game = RealGame(client, client.join_game(race))
            state = debug_pb2.DebugGameState
            game.debug(*(debug_pb2.DebugCommand(game_state=cheat) for cheat in (state.free, state.fast_build)))
            units = game.turn(1)
            townhalls = (UnitTypeId.COMMAND_CENTER, UnitTypeId.NEXUS, UnitTypeId.HATCHERY)
            home = next(unit for unit in units.own if unit.type_id in townhalls).position
            yield game, game.tracker.upgrade_reader, home.towards(game.map.playable_area.center, 10)
            client.leave_game()


def _made(game: RealGame, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> Unit[Any]:
    """A new `unit_type` at `at`, with its energy full."""
    game.debug(game.create(unit_type, at, owner=owner))
    game.turn(4)
    unit = game.newest(unit_type)
    energy = debug_pb2.DebugSetUnitValue(unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=unit.tag)
    game.debug(debug_pb2.DebugCommand(unit_value=energy))
    game.turn(2)
    return unit


def _research_in_game(game: RealGame, *research: tuple[AbilityId, Unit[Any], UpgradeId]) -> None:
    """Research each upgrade in turn, and wait until it is done."""
    for ability, structure, upgrade in research:
        game.order(ability, structure)
        _until(game, lambda upgrade=upgrade: upgrade in game.state.upgrades, steps=22)


def _until(game: RealGame, done: Callable[[], bool], *, steps: int = 2, turns: int = 100) -> None:
    for _ in range(turns):
        if done():
            return
        game.turn(steps)
    assert done()


def _with_tech_lab(game: RealGame, unit_type: UnitTypeId, tech_lab: UnitTypeId, at: Point) -> Unit[Any]:
    """The tech lab a new `unit_type` builds, both standing in the 5 tiles a side around `at`."""
    structure = _made(game, unit_type, at - (1, 0))
    game.order(AbilityId.GENERAL_BUILD_TECH_LAB, structure)
    _until(game, lambda: bool(game.tracker.present_units.own.of_type(tech_lab)), steps=22)
    return game.newest(tech_lab)


@pytest.mark.integration
def test_in_a_real_game_terran_units_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. Researches what each terran sign needs, brings each about with this player's
    units, and reads what each gives away as NachOS reads the enemy's."""
    with _signs_game(Race.TERRAN) as (game, reader, toward):
        enemy = 3 - game.player
        barracks_lab = _with_tech_lab(
            game, UnitTypeId.BARRACKS, UnitTypeId.TECH_LAB_BARRACKS, game.open_ground(toward, size=5)
        )
        starport_lab = _with_tech_lab(
            game, UnitTypeId.STARPORT, UnitTypeId.TECH_LAB_STARPORT, game.open_ground(toward + (0, 8), size=5)
        )
        academy = _made(game, UnitTypeId.GHOST_ACADEMY, game.open_ground(toward + (0, -8), size=3))
        marine = _made(game, UnitTypeId.MARINE, toward + (-6, 0))
        assert (marine.health_max, reader.of_owner(marine)) == (45.0, frozenset())

        _research_in_game(
            game,
            (AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK, barracks_lab, UpgradeId.STIMPACK),
            (AbilityId.BARRACKS_TECH_LAB_RESEARCH_COMBAT_SHIELD, barracks_lab, UpgradeId.COMBAT_SHIELD),
            (AbilityId.BARRACKS_TECH_LAB_RESEARCH_CONCUSSIVE_SHELLS, barracks_lab, UpgradeId.CONCUSSIVE_SHELLS),
            (AbilityId.STARPORT_TECH_LAB_RESEARCH_BANSHEE_CLOAK, starport_lab, UpgradeId.BANSHEE_CLOAK),
            (AbilityId.STARPORT_TECH_LAB_RESEARCH_INTERFERENCE_MATRIX, starport_lab, UpgradeId.INTERFERENCE_MATRIX),
            (AbilityId.GHOST_ACADEMY_RESEARCH_GHOST_CLOAK, academy, UpgradeId.GHOST_CLOAK),
        )
        game.turn(22)
        assert (marine.health_max, reader.of_owner(marine)) == (55.0, {UpgradeId.COMBAT_SHIELD})
        game.order(AbilityId.MARINE_STIM, marine)
        game.turn(4)
        assert reader.of_owner(marine) == {UpgradeId.COMBAT_SHIELD, UpgradeId.STIMPACK}

        marauder = _made(game, UnitTypeId.MARAUDER, toward + (-6, 2))
        game.order(AbilityId.MARAUDER_STIM, marauder)
        game.turn(4)
        assert reader.of_owner(marauder) == {UpgradeId.STIMPACK}

        banshee = _made(game, UnitTypeId.BANSHEE, toward + (-6, 4))
        ghost = _made(game, UnitTypeId.GHOST, toward + (-6, 6))
        game.order(AbilityId.BANSHEE_CLOAK_ON, banshee)
        game.order(AbilityId.GHOST_CLOAK_ON, ghost)
        game.turn(8)
        assert (reader.of_owner(banshee), reader.of_owner(ghost)) == (
            {UpgradeId.BANSHEE_CLOAK},
            {UpgradeId.GHOST_CLOAK},
        )

        roach = _made(game, UnitTypeId.ROACH, toward + (-1, 2), owner=enemy)
        game.order(AbilityId.GENERAL_ATTACK, marauder, target=roach)
        _until(game, lambda: BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW in roach.buffs)
        assert reader.of_opponent(roach) == {UpgradeId.CONCUSSIVE_SHELLS}
        # Gone, or the marauder would slow the tank too.
        game.debug(game.kill(marauder, roach))

        raven = _made(game, UnitTypeId.RAVEN, toward + (-6, 8))
        tank = _made(game, UnitTypeId.SIEGE_TANK, toward + (-1, 8), owner=enemy)
        game.order(AbilityId.RAVEN_INTERFERENCE_MATRIX, raven, target=tank)
        _until(game, lambda: BuffId.RAVEN_INTERFERENCE_MATRIX in tank.buffs)
        assert reader.of_opponent(tank) == {UpgradeId.INTERFERENCE_MATRIX}


@pytest.mark.integration
def test_in_a_real_game_protoss_units_and_effects_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. As the terran test, for the protoss signs."""
    with _signs_game(Race.PROTOSS) as (game, reader, toward):
        enemy = 3 - game.player
        spots = [game.open_ground(toward + (0, 7 * index), size=5) - (1, 1) for index in range(4)]
        for spot in spots:
            # Where `tools/sweep_tech_tree.py` puts a pylon to power a structure it has just created.
            game.debug(game.create(UnitTypeId.PYLON, spot + (2.5, 2.5)))
        core = _made(game, UnitTypeId.CYBERNETICS_CORE, spots[0])
        council = _made(game, UnitTypeId.TWILIGHT_COUNCIL, spots[1])
        archive = _made(game, UnitTypeId.TEMPLAR_ARCHIVE, spots[2])
        gateway = _made(game, UnitTypeId.GATEWAY, spots[3])
        assert reader.of_owner(gateway) == frozenset()

        _research_in_game(
            game,
            (AbilityId.CYBERNETICS_CORE_RESEARCH_WARP_GATE, core, UpgradeId.WARP_GATE),
            (AbilityId.TWILIGHT_COUNCIL_RESEARCH_CHARGE, council, UpgradeId.CHARGE),
            (AbilityId.TEMPLAR_ARCHIVE_RESEARCH_STORM, archive, UpgradeId.STORM),
        )
        _until(game, lambda: gateway.type_id is UnitTypeId.WARP_GATE, steps=22)
        assert reader.of_owner(gateway) == {UpgradeId.WARP_GATE}

        zealot = _made(game, UnitTypeId.ZEALOT, toward + (-8, 0))
        roach = _made(game, UnitTypeId.ROACH, toward + (-14, 0), owner=enemy)
        game.order(AbilityId.GENERAL_ATTACK, zealot, target=roach)
        _until(game, lambda: BuffId.ZEALOT_CHARGING in zealot.buffs, turns=60)
        assert reader.of_owner(zealot) == {UpgradeId.CHARGE}

        templar = _made(game, UnitTypeId.HIGH_TEMPLAR, toward + (-8, 4))
        game.order(AbilityId.HIGH_TEMPLAR_STORM, templar, target=toward + (-10, 4))
        _until(game, lambda: any(effect.id is EffectId.HIGH_TEMPLAR_STORM for effect in game.state.effects))
        (storm,) = [effect for effect in game.state.effects if effect.id is EffectId.HIGH_TEMPLAR_STORM]
        assert (storm.alliance, reader.of_effect(storm)) == (Alliance.OWN, {UpgradeId.STORM})


@pytest.mark.integration
def test_in_a_real_game_zerg_units_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. As the terran test, for the zerg signs, on the creep around the hatchery."""
    with _signs_game(Race.ZERG) as (game, reader, _):
        enemy = 3 - game.player
        hatchery = game.tracker.present_units.own.of_type(UnitTypeId.HATCHERY)[0]
        near = hatchery.position.towards(game.map.playable_area.center, 7)
        game.debug(game.create(UnitTypeId.HIVE, game.open_ground(near + (6, 0), size=5)))
        game.turn(4)
        hive = game.newest(UnitTypeId.HIVE)
        den = _made(game, UnitTypeId.HYDRALISK_DEN, game.open_ground(near, size=3))
        pit = _made(game, UnitTypeId.INFESTATION_PIT, game.open_ground(near + (0, 5), size=3))

        _research_in_game(
            game,
            (AbilityId.LAIR_RESEARCH_BURROW, hive, UpgradeId.BURROW),
            (AbilityId.HYDRALISK_DEN_RESEARCH_HYDRALISK_LUNGE, den, UpgradeId.HYDRALISK_LUNGE),
            (AbilityId.INFESTATION_PIT_RESEARCH_NEURAL_PARASITE, pit, UpgradeId.NEURAL_PARASITE),
        )
        zergling = _made(game, UnitTypeId.ZERGLING, near + (-4, 0))
        assert reader.of_owner(zergling) == frozenset()
        game.order(AbilityId.ZERGLING_BURROW, zergling)
        _until(game, lambda: zergling.type_id is UnitTypeId.ZERGLING_BURROWED)
        assert reader.of_owner(zergling) == {UpgradeId.BURROW}

        hydralisk = _made(game, UnitTypeId.HYDRALISK, near + (-4, 3))
        game.order(AbilityId.HYDRALISK_LUNGE, hydralisk)
        _until(game, lambda: BuffId.HYDRALISK_LUNGE in hydralisk.buffs)
        assert reader.of_owner(hydralisk) == {UpgradeId.HYDRALISK_LUNGE}

        infestor = _made(game, UnitTypeId.INFESTOR, near + (-4, 6))
        marine = _made(game, UnitTypeId.MARINE, near + (-1, 6), owner=enemy)
        game.order(AbilityId.INFESTOR_NEURAL_PARASITE, infestor, target=marine)
        _until(game, lambda: marine.alliance is Alliance.OWN)
        assert BuffId.INFESTOR_NEURAL_PARASITE in marine.buffs
        assert reader.of_owner(marine) == {UpgradeId.NEURAL_PARASITE}
