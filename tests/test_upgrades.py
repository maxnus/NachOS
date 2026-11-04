"""The upgrade tables: what the sweep's findings generate, what the tables then say, and the levels units reveal."""

from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest
from s2clientprotocol import debug_pb2, raw_pb2

from sc2nachos import Api
from sc2nachos.extensions.upgrade_inference import UpgradeInference
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
from sc2nachos.gametime import FASTER_PER_NORMAL_SPEED
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, PlaybackTransport, Recording, WebSocketTransport
from sc2nachos.run import GameProcess, MapFile, MapNotFoundError
from sc2nachos.units import Alliance, Unit, Visibility
from sc2nachos.units._tracking import _Tracker
from support import RealGame, load_tool, make_enemy, make_observation, make_tables, make_unit, require_enemy

_REPOSITORY_ROOT = Path(__file__).parents[1]
_CORPUS_GAMES = sorted((_REPOSITORY_ROOT / "tests" / "corpus").glob("*.sc2rec"))
_NO_LEVELS = {"attack": [], "armor": [], "shield": []}
# A zergling's speed with Metabolic Boost: the game's rows give 4.6992188 per second at Normal speed.
_BOOSTED_ZERGLING_SPEED = 4.6992188 * FASTER_PER_NORMAL_SPEED


def _tech_tree_generator() -> ModuleType:
    """The `tools/generate_tech_tree.py` module."""
    return load_tool("generate_tech_tree")


def _upgrade_findings(*research: dict[str, object], unexplained: tuple[str, ...] = ()) -> dict[str, object]:
    """Findings as `tools/sweep_upgrades.py` writes them."""
    return {"base_build": 1, "races": [], "research": list(research), "unexplained": list(unexplained)}


def _research_entry(upgrade: str, changes: dict[str, object], **levels: list[str]) -> dict[str, object]:
    return {"upgrades": [upgrade], "changes": changes, "levels": _NO_LEVELS | levels}


def _stalker_weapons(damage: float, bonus: float) -> dict[str, object]:
    return {"weapons": [{"damage": damage, "bonuses": {"Armored": bonus}}]}


class TestGeneratingTheUpgrades:
    def test_a_change_is_read_under_the_curated_ids_a_bonus_the_weapon_lacked_included(self) -> None:
        igniter = _research_entry("HighCapacityBarrels", {"HellionTank": {"weapons": [{"bonuses": {"Light": 12.0}}]}})
        found = _tech_tree_generator().read_upgrades(_upgrade_findings(igniter))
        assert found.unit_types[UnitTypeId.HELLBAT][UpgradeId.BLUE_FLAME] == UnitTypeUpgrade(
            weapons=(WeaponUpgrade(damage_bonuses=MappingProxyType({Attribute.LIGHT: 12.0})),)
        )
        assert UpgradeId.BLUE_FLAME not in found.types

    def test_a_level_affects_every_type_it_raises_the_report_of_or_changes_the_row_of(self) -> None:
        """A void ray reports its attack level though the rows give it no weapon, and the sweep has no mothership to
        report its level though air weapons change its weapon."""
        weapons = _research_entry(
            "ProtossAirWeaponsLevel1",
            {"Phoenix": {"weapons": [{"damage": 1.0}]}, "Mothership": {"weapons": [{"damage": 1.0}]}},
            attack=["Phoenix", "VoidRay"],
        )
        found = _tech_tree_generator().read_upgrades(_upgrade_findings(weapons))
        assert set(found.unit_types) == {UnitTypeId.PHOENIX, UnitTypeId.VOID_RAY, UnitTypeId.MOTHERSHIP}
        assert found.unit_types[UnitTypeId.VOID_RAY][UpgradeId.PROTOSS_AIR_WEAPONS_1] == UnitTypeUpgrade()
        assert found.types[UpgradeId.PROTOSS_AIR_WEAPONS_1] is UpgradeType.ATTACK
        assert found.levels[UpgradeId.PROTOSS_AIR_WEAPONS_1] == 1

    def test_an_upgrade_raising_a_report_with_no_number_in_its_name_is_no_level(self) -> None:
        plating = _research_entry("ChitinousPlating", {"Ultralisk": {"armor": 2.0}}, armor=["Ultralisk"])
        found = _tech_tree_generator().read_upgrades(_upgrade_findings(plating))
        assert found.types[UpgradeId.ULTRALISK_ARMOR] is UpgradeType.ARMOR
        assert UpgradeId.ULTRALISK_ARMOR not in found.levels

    def test_a_shields_level_affects_the_types_that_report_it_and_changes_no_row(self) -> None:
        shields = _research_entry("ProtossShieldsLevel1", {}, shield=["Zealot", "Pylon"])
        found = _tech_tree_generator().read_upgrades(_upgrade_findings(shields))
        assert found.unit_types[UnitTypeId.PYLON] == {UpgradeId.PROTOSS_SHIELDS_1: UnitTypeUpgrade()}
        assert found.types[UpgradeId.PROTOSS_SHIELDS_1] is UpgradeType.SHIELD

    def test_levels_that_add_different_amounts_to_one_type_are_refused(self) -> None:
        """A unit reports only how many attack levels it has, so each level must add the same amount."""
        levels = [
            _research_entry("ProtossGroundWeaponsLevel1", {"Stalker": _stalker_weapons(1.0, 1.0)}, attack=["Stalker"]),
            _research_entry("ProtossGroundWeaponsLevel2", {"Stalker": _stalker_weapons(2.0, 1.0)}, attack=["Stalker"]),
        ]
        with pytest.raises(ValueError, match="ATTACK levels of STALKER add different amounts"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(*levels))

    def test_levels_of_a_type_that_do_not_start_at_1_are_refused(self) -> None:
        second = _research_entry(
            "ProtossGroundWeaponsLevel2", {"Stalker": _stalker_weapons(1.0, 1.0)}, attack=["Stalker"]
        )
        with pytest.raises(ValueError, match="ATTACK levels affecting STALKER are not 1 onwards"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(second))

    def test_an_upgrade_raising_two_kinds_of_report_is_refused(self) -> None:
        both = _research_entry(
            "ChitinousPlating", {"Ultralisk": {"armor": 2.0}}, armor=["Ultralisk"], attack=["Ultralisk"]
        )
        with pytest.raises(ValueError, match="ARMOR and ATTACK"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(both))

    def test_what_the_changes_do_not_account_for_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Marine is"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(unexplained=("Marine is (0.0, 2.25, [])",)))

    def test_a_change_to_a_weapons_cooldown_is_refused(self) -> None:
        """The tables carry no cooldown change, since no upgrade makes one in the game's rows."""
        glands = _research_entry("zerglingattackspeed", {"Zergling": {"weapons": [{"cooldown": -0.2}]}})
        with pytest.raises(ValueError, match="cooldown"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(glands))

    def test_one_research_finishing_two_upgrades_that_change_something_is_refused(self) -> None:
        both = {"upgrades": ["Charge", "BlinkTech"], "changes": {"Zealot": {"speed": 1.0}}, "levels": _NO_LEVELS}
        with pytest.raises(ValueError, match="Charge, BlinkTech"):
            _tech_tree_generator().read_upgrades(_upgrade_findings(both))

    def test_an_uncurated_unit_type_is_left_out_and_named(self) -> None:
        weapons = _research_entry(
            "TerranInfantryWeaponsLevel1",
            {"Marine": {"weapons": [{"damage": 1.0}]}, "HERC": {"weapons": [{"damage": 2.0}]}},
            attack=["Marine"],
        )
        generator = _tech_tree_generator()
        assert set(generator.read_upgrades(_upgrade_findings(weapons)).unit_types) == {UnitTypeId.MARINE}
        assert generator.uncurated_upgraded(_upgrade_findings(weapons)) == {"HERC"}

    def test_an_uncurated_upgrade_that_affects_a_curated_unit_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="CampaignUpgrade affects Marine"):
            _tech_tree_generator().read_upgrades(
                _upgrade_findings(_research_entry("CampaignUpgrade", {"Marine": {"armor": 1.0}}))
            )


def _three_levels_of(family: str) -> tuple[UpgradeId, UpgradeId, UpgradeId]:
    """The three levels of the upgrade `family`, such as `TERRAN_INFANTRY_WEAPONS`."""
    return UpgradeId[f"{family}_1"], UpgradeId[f"{family}_2"], UpgradeId[f"{family}_3"]


_GROUND, _AIR, _ANY = TargetDomain.GROUND, TargetDomain.AIR, TargetDomain.ANY
_LIGHT, _ARMORED, _MASSIVE = Attribute.LIGHT, Attribute.ARMORED, Attribute.MASSIVE

# A weapon's damage before any level and at each of the three, and likewise its bonus against one attribute, as the
# game's help gives them. One level adds different amounts to different types, and may or may not add to a bonus.
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


def _reported_levels(tables: GameData, upgrades: Iterable[UpgradeId]) -> list[tuple[UpgradeType | None, int]]:
    """For each of `upgrades`, the level type units report it under, and which level of its line it is."""
    return [(tables.upgrades[upgrade].upgrade_type, tables.upgrades[upgrade].level) for upgrade in upgrades]


def _weapon_hitting(row: UnitTypeData, domain: TargetDomain) -> Weapon:
    return next(weapon for weapon in row.weapons if weapon.target_domain is domain)


class TestWhatTheTablesSay:
    @pytest.mark.parametrize(
        ("unit_type", "family", "domain", "damages", "attribute", "bonuses"),
        _WEAPON_LEVELS,
        ids=[f"{unit_type.name}-{family}-{domain.name}" for unit_type, family, domain, *_ in _WEAPON_LEVELS],
    )
    def test_each_weapons_level_adds_what_it_adds_to_that_type(
        self,
        first_corpus_tables: GameData,
        unit_type: UnitTypeId,
        family: str,
        domain: TargetDomain,
        damages: list[int],
        attribute: Attribute | None,
        bonuses: list[int],
    ) -> None:
        row = first_corpus_tables.units[unit_type]
        levels = _three_levels_of(family)
        assert _reported_levels(first_corpus_tables, levels) == [(UpgradeType.ATTACK, level) for level in (1, 2, 3)]
        assert set(levels) <= row.upgrades.keys()
        for level in range(4):
            weapon = _weapon_hitting(row.with_upgrades(levels[:level]), domain)
            expected = {attribute: bonuses[level]} if attribute is not None else {}
            assert (weapon.damage, dict(weapon.damage_bonuses)) == (damages[level], expected), f"level {level}"

    @pytest.mark.parametrize(
        ("unit_type", "family", "by_level"),
        _ARMOR_LEVELS,
        ids=[f"{unit_type.name}-{family}" for unit_type, family, _ in _ARMOR_LEVELS],
    )
    def test_each_armor_level_adds_one_armor(
        self, first_corpus_tables: GameData, unit_type: UnitTypeId, family: str, by_level: list[float]
    ) -> None:
        row = first_corpus_tables.units[unit_type]
        levels = _three_levels_of(family)
        assert _reported_levels(first_corpus_tables, levels) == [(UpgradeType.ARMOR, level) for level in (1, 2, 3)]
        assert [row.with_upgrades(levels[:level]).armor for level in range(4)] == by_level

    def test_two_changes_to_a_weapon_add_up_and_their_attack_speeds_compound(self) -> None:
        first = WeaponUpgrade(damage=1.0, damage_bonuses=MappingProxyType({_ARMORED: 1.0}), attack_speed_multiplier=1.4)
        second = WeaponUpgrade(damage_bonuses=MappingProxyType({_ARMORED: 2.0, _LIGHT: 1.0}), range=1.0)
        assert first + second == WeaponUpgrade(
            damage=1.0,
            damage_bonuses=MappingProxyType({_ARMORED: 3.0, _LIGHT: 1.0}),
            range=1.0,
            attack_speed_multiplier=1.4,
        )
        assert (first + first).attack_speed_multiplier == pytest.approx(1.96)

    def test_one_level_adds_different_amounts_to_different_types(self, first_corpus_tables: GameData) -> None:
        first = UpgradeId.TERRAN_INFANTRY_WEAPONS_1
        infantry = (UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.GHOST)
        changes = {unit: first_corpus_tables.units[unit].upgrades[first].weapons[0] for unit in infantry}
        assert changes == {
            UnitTypeId.MARINE: WeaponUpgrade(damage=1.0),
            UnitTypeId.MARAUDER: WeaponUpgrade(damage=1.0, damage_bonuses=MappingProxyType({_ARMORED: 1.0})),
            UnitTypeId.GHOST: WeaponUpgrade(damage=1.0, damage_bonuses=MappingProxyType({_LIGHT: 1.0})),
        }

    def test_neosteel_armor_adds_two_to_a_terran_structure(self, first_corpus_tables: GameData) -> None:
        structures = (UnitTypeId.COMMAND_CENTER, UnitTypeId.PLANETARY_FORTRESS, UnitTypeId.MISSILE_TURRET)
        armor = [first_corpus_tables.units[unit].with_upgrades({UpgradeId.BUILDING_ARMOR}).armor for unit in structures]
        assert armor == [3, 4, 2]

    def test_chitinous_plating_adds_two_on_top_of_the_armor_levels(self, first_corpus_tables: GameData) -> None:
        ultralisk = first_corpus_tables.units[UnitTypeId.ULTRALISK]
        assert ultralisk.with_upgrades({*_three_levels_of("ZERG_GROUND_ARMOR"), UpgradeId.ULTRALISK_ARMOR}).armor == 7
        assert _reported_levels(first_corpus_tables, [UpgradeId.ULTRALISK_ARMOR]) == [(UpgradeType.ARMOR, 0)]

    def test_a_shields_level_affects_protoss_types_and_changes_no_row(self, first_corpus_tables: GameData) -> None:
        """The game's rows hold no shield armor, so the levels that add to it change nothing in a row."""
        shields = _three_levels_of("PROTOSS_SHIELDS")
        assert _reported_levels(first_corpus_tables, shields) == [(UpgradeType.SHIELD, level) for level in (1, 2, 3)]
        for unit_type in (UnitTypeId.ZEALOT, UnitTypeId.PYLON, UnitTypeId.VOID_RAY, UnitTypeId.NEXUS):
            row = first_corpus_tables.units[unit_type]
            assert all(row.upgrades[upgrade] == UnitTypeUpgrade() for upgrade in shields)
            assert row.with_upgrades(shields) is row

    @pytest.mark.parametrize(
        ("unit_type", "family", "damages"),
        [
            (UnitTypeId.VOID_RAY, "PROTOSS_AIR_WEAPONS", [(6, 7, 8, 9)]),
            (UnitTypeId.SENTRY, "PROTOSS_GROUND_WEAPONS", [(6, 7, 8, 9)]),
            (UnitTypeId.BATTLECRUISER, "TERRAN_SHIP_WEAPONS", [(8, 9, 10, 11), (5, 6, 7, 8)]),
        ],
    )
    def test_each_attack_level_adds_one_to_a_measured_weapon(
        self, first_corpus_tables: GameData, unit_type: UnitTypeId, family: str, damages: list[tuple[int, ...]]
    ) -> None:
        """The rows give these types no weapon, and the weapons measured gained 3 a hit with the third levels, a void
        ray's bonus against armored nothing (tool `sweep_combat`, `no-weapon`)."""
        row = first_corpus_tables.units[unit_type]
        levels = _three_levels_of(family)
        by_level = [[weapon.damage for weapon in row.with_upgrades(levels[:level]).weapons] for level in range(4)]
        assert by_level == [list(level) for level in zip(*damages, strict=True)]
        assert row.with_upgrades(levels).weapons[0].damage_bonuses == row.weapons[0].damage_bonuses

    def test_no_attack_level_adds_to_an_oracles_beam(self, first_corpus_tables: GameData) -> None:
        oracle = first_corpus_tables.units[UnitTypeId.ORACLE]
        assert oracle.with_upgrades(_three_levels_of("PROTOSS_AIR_WEAPONS")) is oracle

    @pytest.mark.parametrize(
        ("unit_type", "upgrade", "sped_up"),
        [
            # What every cooldown started from, the delay aside (tool `sweep_combat`, `delay-sped-up`).
            (UnitTypeId.ZERGLING, UpgradeId.ADRENAL_GLANDS, 32585 / 4096),
            (UnitTypeId.ADEPT, UpgradeId.GLAIVES, 36 / 1.45),
        ],
    )
    def test_an_upgrade_that_speeds_an_attack_divides_the_tables_cooldown_and_keeps_the_delay(
        self, first_corpus_tables: GameData, unit_type: UnitTypeId, upgrade: UpgradeId, sped_up: float
    ) -> None:
        row = first_corpus_tables.units[unit_type]
        (weapon,) = row.with_upgrades({upgrade}).weapons
        assert weapon.base_cooldown.steps == pytest.approx(sped_up, abs=1e-3)
        assert (weapon.mean_delay.steps, weapon.damage) == (row.weapons[0].mean_delay.steps, row.weapons[0].damage)

    def test_an_upgrade_no_unit_reports_is_of_no_kind_and_no_level(self, first_corpus_tables: GameData) -> None:
        other = (UpgradeType.OTHER, 0)
        assert _reported_levels(first_corpus_tables, [UpgradeId.HYDRALISK_RANGE, UpgradeId.ZERGLING_SPEED]) == [
            other,
            other,
        ]

    def test_infernal_pre_igniter_gives_a_hellbat_a_bonus_against_light(self, first_corpus_tables: GameData) -> None:
        hellbat = first_corpus_tables.units[UnitTypeId.HELLBAT]
        assert Attribute.LIGHT not in hellbat.weapons[0].damage_bonuses
        assert hellbat.with_upgrades({UpgradeId.BLUE_FLAME}).weapons[0].damage_bonuses[Attribute.LIGHT] > 0

    def test_grooved_spines_adds_to_a_hydralisks_range(self, first_corpus_tables: GameData) -> None:
        hydralisk = first_corpus_tables.units[UnitTypeId.HYDRALISK]
        assert hydralisk.with_upgrades({UpgradeId.HYDRALISK_RANGE}).weapons[0].range == hydralisk.weapons[0].range + 1

    def test_metabolic_boost_makes_a_zergling_faster(self, first_corpus_tables: GameData) -> None:
        zergling = first_corpus_tables.units[UnitTypeId.ZERGLING]
        assert zergling.with_upgrades({UpgradeId.ZERGLING_SPEED}).speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)

    def test_upgrades_that_change_nothing_about_a_type_leave_its_row_as_it_is(
        self, first_corpus_tables: GameData
    ) -> None:
        marine = first_corpus_tables.units[UnitTypeId.MARINE]
        assert marine.with_upgrades(()) is marine
        assert marine.with_upgrades({UpgradeId.ADRENAL_GLANDS, UpgradeId.STIMPACK}) is marine


def _tracker_against_the_computer(tables: GameData) -> _Tracker:
    return _Tracker(tables, make_enemy(tables))


def _units_observed(
    tracker: _Tracker, *units: raw_pb2.Unit, upgrades: tuple[int, ...] = (), step: int = 0
) -> list[Unit[Any]]:
    tracker.update(make_observation(step, units=units, upgrades=upgrades).observation.raw_data, step)
    require_enemy(tracker.enemy).assume_upgrades(
        *tracker.upgrade_tracker.level_reader.read_levels(tracker.unit_tracker.present)
    )
    by_tag = {unit.tag: unit for unit in tracker.unit_tracker.present}
    return [by_tag[unit.tag] for unit in units]


class TestWhatAUnitReads:
    def test_this_players_units_read_with_its_upgrades(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (zergling,) = _units_observed(tracker, make_unit(1, UnitTypeId.ZERGLING), upgrades=(UpgradeId.ZERGLING_SPEED,))
        assert zergling.speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)

    def test_this_players_units_attack_as_fast_as_its_upgrades_have_them(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        upgrades = (UpgradeId.ADRENAL_GLANDS,)
        (zergling,) = _units_observed(tracker, make_unit(1, UnitTypeId.ZERGLING), upgrades=upgrades)
        row = first_corpus_tables.units[UnitTypeId.ZERGLING]
        expected = row.with_upgrades(upgrades).weapons[0].average_cooldown.steps
        assert zergling.weapons[0].average_cooldown.steps == expected < row.weapons[0].average_cooldown.steps

    def test_this_players_units_show_it_nothing_it_has_not_researched(self, first_corpus_tables: GameData) -> None:
        """This player's units report only this player's upgrades, so nothing is inferred from them."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, attack_upgrade_level=3))
        assert not require_enemy(tracker.enemy).upgrades

    def test_a_unit_taken_from_this_player_shows_nothing(self, first_corpus_tables: GameData) -> None:
        """A neural parasite puts it on the enemy's side, but the levels it reports may be this player's."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, attack_upgrade_level=3))
        _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=3), step=1)
        assert not require_enemy(tracker.enemy).upgrades

    def test_a_negative_level_shows_nothing(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=-1, armor_upgrade_level=-2))
        assert not require_enemy(tracker.enemy).upgrades

    def test_the_enemys_units_read_with_what_it_is_assumed_to_have(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (zergling,) = _units_observed(tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY))
        base = first_corpus_tables.units[UnitTypeId.ZERGLING].speed
        assert zergling.speed == base
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.ZERGLING_SPEED)
        assert zergling.speed == pytest.approx(_BOOSTED_ZERGLING_SPEED)
        require_enemy(tracker.enemy).forget_upgrades(UpgradeId.ZERGLING_SPEED)
        assert zergling.speed == base

    def test_assuming_a_level_assumes_the_levels_below_it(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        enemy = require_enemy(tracker.enemy)
        enemy.assume_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_3, UpgradeId.PROTOSS_SHIELDS_2)
        assert enemy.upgrades == {
            UpgradeId.TERRAN_INFANTRY_WEAPONS_1,
            UpgradeId.TERRAN_INFANTRY_WEAPONS_2,
            UpgradeId.TERRAN_INFANTRY_WEAPONS_3,
            UpgradeId.PROTOSS_SHIELDS_1,
            UpgradeId.PROTOSS_SHIELDS_2,
        }
        marine = make_unit(1, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        zealot = make_unit(2, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        never_seen = _units_observed(tracker, marine, zealot)
        assert never_seen[0].weapons[0].damage == first_corpus_tables.units[UnitTypeId.MARINE].weapons[0].damage + 3
        assert never_seen[1].shield_armor == 2

    def test_forgetting_a_level_forgets_the_levels_above_it_and_nothing_else(
        self, first_corpus_tables: GameData
    ) -> None:
        enemy = make_enemy(first_corpus_tables)
        enemy.assume_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_3, UpgradeId.TERRAN_INFANTRY_ARMOR_1)
        enemy.forget_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_2)
        assert enemy.upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_1, UpgradeId.TERRAN_INFANTRY_ARMOR_1}

    def test_assuming_only_what_is_held_keeps_the_same_set(self, first_corpus_tables: GameData) -> None:
        """`BASIC` assumes what the enemy's units show on every step, which is almost always nothing new."""
        enemy = make_enemy(first_corpus_tables)
        enemy.assume_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_2)
        held = enemy.upgrades
        enemy.assume_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_1)
        enemy.assume_upgrades()
        assert enemy.upgrades is held

    def test_an_upgrade_the_tables_leave_out_is_assumed_alone(self) -> None:
        enemy = make_enemy(make_tables())
        enemy.assume_upgrades(UpgradeId.TERRAN_INFANTRY_WEAPONS_2)
        assert enemy.upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_2}

    def test_a_neutral_unit_reads_with_no_upgrade(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.ZERGLING_SPEED)
        (zergling,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.NEUTRAL), upgrades=(UpgradeId.ZERGLING_SPEED,)
        )
        assert zergling.speed == first_corpus_tables.units[UnitTypeId.ZERGLING].speed

    def test_a_visible_enemy_unit_shows_the_levels_of_its_own_lines(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=2))
        assert require_enemy(tracker.enemy).upgrades == {
            UpgradeId.TERRAN_INFANTRY_WEAPONS_1,
            UpgradeId.TERRAN_INFANTRY_WEAPONS_2,
        }

    def test_what_one_unit_shows_counts_for_every_type_of_its_line(self, first_corpus_tables: GameData) -> None:
        """A marine's attack level is Terran Infantry Weapons, which a marauder has too and a viking does not."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        marauder = make_unit(2, UnitTypeId.MARAUDER, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        viking = make_unit(3, UnitTypeId.VIKING, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        units = _units_observed(
            tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=2), marauder, viking
        )
        assert units[1].weapons[0].damage == first_corpus_tables.units[UnitTypeId.MARAUDER].weapons[0].damage + 2
        assert units[2].weapons[0].damage == first_corpus_tables.units[UnitTypeId.VIKING].weapons[0].damage

    def test_a_unit_out_of_sight_counts_a_level_another_showed_since(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (marine,) = _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY))
        base = first_corpus_tables.units[UnitTypeId.MARINE].weapons[0].damage
        assert marine.weapons[0].damage == base
        _units_observed(tracker, make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=1), step=1)
        assert not marine.is_listed
        assert marine.weapons[0].damage == base + 1

    def test_a_level_a_unit_showed_stays_known_once_it_is_gone(self, first_corpus_tables: GameData) -> None:
        """Levels are never lost, so what one unit showed holds for the rest of the game."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY, attack_upgrade_level=1))
        _units_observed(tracker, make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=0))
        assert require_enemy(tracker.enemy).upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_1}

    @pytest.mark.parametrize("level", [0, 1, 2, 3])
    def test_an_enemy_marauder_reads_the_damage_and_bonus_of_the_level_it_shows(
        self, first_corpus_tables: GameData, level: int
    ) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (marauder,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.MARAUDER, alliance=Alliance.ENEMY, attack_upgrade_level=level)
        )
        weapon = marauder.weapons[0]
        assert (weapon.damage, dict(weapon.damage_bonuses)) == (10 + level, {Attribute.ARMORED: 10 + level})

    def test_the_armor_a_zergling_shows_is_the_ground_armor_levels(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, armor_upgrade_level=2))
        assert require_enemy(tracker.enemy).upgrades == {UpgradeId.ZERG_GROUND_ARMOR_1, UpgradeId.ZERG_GROUND_ARMOR_2}

    def test_the_armor_an_ultralisk_shows_says_nothing_about_the_levels(self, first_corpus_tables: GameData) -> None:
        """Its 2 could be two levels or Chitinous Plating, and reading it as levels would armor every zergling."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (ultralisk,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.ULTRALISK, alliance=Alliance.ENEMY, armor_upgrade_level=2)
        )
        assert not require_enemy(tracker.enemy).upgrades
        assert ultralisk.armor == first_corpus_tables.units[UnitTypeId.ULTRALISK].armor + 2

    def test_a_visible_unit_reads_the_armor_it_reports(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        base = first_corpus_tables.units[UnitTypeId.ULTRALISK].armor
        (ultralisk,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.ULTRALISK, alliance=Alliance.ENEMY, armor_upgrade_level=5)
        )
        assert ultralisk.armor == base + 5

    def test_a_visible_unit_reads_the_armor_it_reports_alone(self, first_corpus_tables: GameData) -> None:
        """Though its owner is known to have more."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.TERRAN_INFANTRY_ARMOR_1)
        (marine,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.MARINE, alliance=Alliance.ENEMY, armor_upgrade_level=0)
        )
        assert marine.armor == first_corpus_tables.units[UnitTypeId.MARINE].armor

    def test_a_unit_out_of_sight_reads_the_more_of_its_last_report_and_its_owners(
        self, first_corpus_tables: GameData
    ) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.TERRAN_INFANTRY_ARMOR_1)
        (marine,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.MARINE, alliance=Alliance.ENEMY, armor_upgrade_level=0)
        )
        tracker.update(make_observation(16, units=[]).observation.raw_data, 16)
        assert not marine.is_listed
        assert marine.armor == first_corpus_tables.units[UnitTypeId.MARINE].armor + 1

    def test_a_unit_never_shown_visible_reads_the_armor_the_enemy_is_known_to_have(
        self, first_corpus_tables: GameData
    ) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.BUILDING_ARMOR)
        remembered = make_unit(1, UnitTypeId.MISSILE_TURRET, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
        assert (
            _units_observed(tracker, remembered)[0].armor
            == first_corpus_tables.units[UnitTypeId.MISSILE_TURRET].armor + 2
        )

    def test_shield_armor_is_the_shields_levels(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        (zealot,) = _units_observed(
            tracker, make_unit(1, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, shield_upgrade_level=2)
        )
        assert zealot.shield_armor == 2
        assert require_enemy(tracker.enemy).upgrades == {UpgradeId.PROTOSS_SHIELDS_1, UpgradeId.PROTOSS_SHIELDS_2}
        never_seen = make_unit(2, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert _units_observed(tracker, never_seen)[0].shield_armor == 2
        visible = make_unit(3, UnitTypeId.ZEALOT, alliance=Alliance.ENEMY, shield_upgrade_level=0)
        assert _units_observed(tracker, visible)[0].shield_armor == 0

    def test_a_unit_without_shields_has_no_shield_armor(self, first_corpus_tables: GameData) -> None:
        tracker = _tracker_against_the_computer(first_corpus_tables)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.PROTOSS_SHIELDS_1)
        (marine,) = _units_observed(tracker, make_unit(1, alliance=Alliance.ENEMY))
        assert marine.shield_armor == 0


# The upgrades the enemy's units reveal in each recorded game: nothing in the two where the enemy researched nothing.
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


def _api_after_playing_back(path: Path, api: Api) -> Api:
    """`api` after playing the recorded game at `path` to its end."""
    client = Client(PlaybackTransport(Recording(path)))
    client.create_game("recorded", [Participant(), Computer()])
    client.join_game(Race.RANDOM)
    api.play(client)
    return api


# What `UpgradeInference` reads in the recorded games beyond the levels: Metabolic Boost, off zerglings that moved
# within 0.4% of their boosted speed over 16 and 32 steps, when the computer attacked.
_SPEED_UPGRADES_READ_IN_THE_CORPUS = {
    "IncorporealAIE_v4-PvZ": {UpgradeId.ZERGLING_SPEED},
    "PylonAIE_v4-TvZ": {UpgradeId.ZERGLING_SPEED},
}


@pytest.mark.parametrize("path", _CORPUS_GAMES, ids=lambda path: path.stem)
def test_a_recorded_game_shows_what_its_enemy_researched(path: Path) -> None:
    """The computer researches during a corpus game, and its units carry the levels NachOS reads."""
    assert _api_after_playing_back(path, Api()).enemy.upgrades == _LEARNED_IN_THE_CORPUS[path.stem]


@pytest.mark.parametrize("path", _CORPUS_GAMES, ids=lambda path: path.stem)
def test_upgrade_inference_reads_only_the_speed_upgrades_more_in_a_recorded_game(path: Path) -> None:
    """None of this player's units leaves its base to see the computer's, so they show `UpgradeInference` nothing
    more but how fast the computer's units move when it attacks."""
    upgrades = _api_after_playing_back(path, Api(extensions=UpgradeInference)).enemy.upgrades
    assert upgrades == _LEARNED_IN_THE_CORPUS[path.stem] | _SPEED_UPGRADES_READ_IN_THE_CORPUS.get(path.stem, set())


@pytest.mark.parametrize("path", _CORPUS_GAMES, ids=lambda path: path.stem)
def test_without_the_speed_upgrades_upgrade_inference_reads_nothing_more_in_a_recorded_game(path: Path) -> None:
    upgrade_inference_alone = Api(extensions=lambda api: UpgradeInference(api, include_speed_upgrades=False))
    assert _api_after_playing_back(path, upgrade_inference_alone).enemy.upgrades == _LEARNED_IN_THE_CORPUS[path.stem]


def _values_upgrades_change(row: UnitTypeData) -> list[float]:
    """The values of `row` that upgrades change, as numbers in a fixed order."""
    values = [row.armor, row.speed]
    for weapon in row.weapons:
        values += [weapon.damage, weapon.range, *(weapon.damage_bonuses.get(a, 0.0) for a in Attribute)]
    return values


@pytest.mark.integration
def test_in_a_real_game_the_tables_with_this_players_upgrades_are_what_the_game_says_asked_again() -> None:
    """Run with `pytest -m integration`. Plays as terran, researches a leveled and an unleveled upgrade, then checks
    every curated unit type's row against the game's rows asked again, and every unit's armor."""
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
                if _values_upgrades_change(row.with_upgrades(game.state.upgrades))
                != pytest.approx(_values_upgrades_change(asked.units[unit_type]))
            ]
            assert not differ
            for unit in game.tracker.unit_tracker.present.own:
                assert unit.armor == asked.units[unit.type_id].armor
            marine = game.newest(UnitTypeId.MARINE)
            assert marine.weapons[0].damage == asked.units[UnitTypeId.MARINE].weapons[0].damage
            assert marine.shield_armor == 0

            # The shield levels are a protoss unit's shield armor, which no row carries.
            # A forge and the pylon beside it fill a five by five square.
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
