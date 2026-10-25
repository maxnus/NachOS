"""A unit's weapon, reach, damage and dps against another, on the tables of the first corpus game."""

from typing import Any

import pytest
from s2clientprotocol import raw_pb2

from sc2nachos.gamedata import GameData, TargetDomain
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, OwnUnit, Unit, Units
from sc2nachos.units._tracking import _Tracker
from support import make_enemy, make_observation, make_unit

# The mean of the random delay the game adds to every attack's cooldown, in steps (tool `sweep_combat`).
_MEAN_DELAY = 0.4996
# What an oracle wears while its beam is on (tool `sweep_combat`, `no-weapon`).
_BEAM_ON = [BuffId.ORACLE_PULSAR_BEAM]


def _units_observed(tables: GameData, *units: raw_pb2.Unit, upgrades: tuple[int, ...] = ()) -> list[Unit[Any]]:
    """`units` as one observation shows them, this player having `upgrades`."""
    tracker = _Tracker(tables, make_enemy())
    tracker.update(make_observation(0, units=units, upgrades=upgrades).observation.raw_data, 0)
    by_tag = {unit.tag: unit for unit in tracker.unit_tracker.present}
    return [by_tag[unit.tag] for unit in units]


def _enemy_report(tag: int, unit_type: UnitTypeId, **fields: Any) -> raw_pb2.Unit:
    return make_unit(tag, unit_type, alliance=Alliance.ENEMY, at=(20.0, 10.0), **fields)


class TestTheWeaponAgainstATarget:
    def test_a_weapon_that_hits_both_is_fired_at_either(self, first_corpus_tables: GameData) -> None:
        marine, zergling, overlord = _units_observed(
            first_corpus_tables,
            make_unit(1),
            _enemy_report(2, UnitTypeId.ZERGLING),
            _enemy_report(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert marine.weapon_against(zergling) is marine.weapon_against(overlord) is marine.weapons[0]

    def test_a_flier_takes_the_air_weapon_and_a_unit_on_the_ground_the_ground_one(
        self, first_corpus_tables: GameData
    ) -> None:
        queen, marine, overlord = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.QUEEN),
            _enemy_report(2, UnitTypeId.MARINE),
            _enemy_report(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert _target_domain_of(queen.weapon_against(marine)) is TargetDomain.GROUND
        assert _target_domain_of(queen.weapon_against(overlord)) is TargetDomain.AIR

    def test_air_weapons_hit_a_colossus_when_there_is_no_ground_weapon(self, first_corpus_tables: GameData) -> None:
        viking, thor, colossus, zergling = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.VIKING, is_flying=True),
            make_unit(2, UnitTypeId.THOR),
            _enemy_report(3, UnitTypeId.COLOSSUS),
            _enemy_report(4, UnitTypeId.ZERGLING),
        )
        assert _target_domain_of(viking.weapon_against(colossus)) is TargetDomain.AIR
        assert viking.weapon_against(zergling) is None
        assert _target_domain_of(thor.weapon_against(colossus)) is TargetDomain.GROUND

    def test_an_oracle_fires_at_nothing_in_the_air(self, first_corpus_tables: GameData) -> None:
        oracle, overlord, scv = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.ORACLE, is_flying=True, buff_ids=_BEAM_ON),
            _enemy_report(2, UnitTypeId.OVERLORD, is_flying=True),
            _enemy_report(3, UnitTypeId.SCV),
        )
        assert oracle.weapon_against(overlord) is None
        assert oracle.weapon_against(scv) is oracle.weapons[0]

    def test_an_oracle_fires_nothing_while_its_beam_is_off(self, first_corpus_tables: GameData) -> None:
        oracle, scv = _units_observed(
            first_corpus_tables, make_unit(1, UnitTypeId.ORACLE, is_flying=True), _enemy_report(2, UnitTypeId.SCV)
        )
        assert oracle.weapon_against(scv) is None
        assert (oracle.has_in_range(scv, margin=100.0), oracle.dps_against(scv)) == (False, 0.0)

    def test_a_battlecruiser_fires_its_measured_lasers(self, first_corpus_tables: GameData) -> None:
        battlecruiser, marine, overlord = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.BATTLECRUISER, is_flying=True),
            _enemy_report(2, UnitTypeId.MARINE),
            _enemy_report(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert _damage_of_weapon(battlecruiser.weapon_against(marine)) == 8.0
        assert _damage_of_weapon(battlecruiser.weapon_against(overlord)) == 5.0


def _target_domain_of(weapon: Any) -> TargetDomain:
    assert weapon is not None
    return weapon.target_domain


def _damage_of_weapon(weapon: Any) -> float:
    assert weapon is not None
    return weapon.damage


class TestReach:
    # A marine's 5 and two radii of 0.375: it fired at an SCV out to 5.75 (tool `sweep_combat`, `range`).
    @pytest.mark.parametrize(
        ("x", "margin", "in_range"),
        [(15.75, 0.0, True), (15.76, 0.0, False), (15.75, -0.125, False), (15.875, 0.125, True)],
    )
    def test_a_target_is_in_range_out_to_the_range_and_both_radii(
        self, first_corpus_tables: GameData, x: float, margin: float, in_range: bool
    ) -> None:
        marine, scv = _units_observed(
            first_corpus_tables,
            make_unit(1, at=(10.0, 10.0), radius=0.375),
            make_unit(2, UnitTypeId.SCV, alliance=Alliance.ENEMY, at=(x, 10.0), radius=0.375),
        )
        assert marine.has_in_range(scv, margin=margin) is in_range

    def test_a_colossus_is_in_range_of_an_air_weapon(self, first_corpus_tables: GameData) -> None:
        """A viking's 9 and the radii, 0.75 and 1."""
        viking, colossus = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.VIKING, at=(10.0, 10.0), radius=0.75, is_flying=True),
            make_unit(2, UnitTypeId.COLOSSUS, alliance=Alliance.ENEMY, at=(20.75, 10.0), radius=1.0),
        )
        assert viking.has_in_range(colossus)
        assert not viking.has_in_range(colossus, margin=-0.125)

    def test_nothing_is_in_range_of_a_unit_with_no_weapon_against_it(self, first_corpus_tables: GameData) -> None:
        overlord, marine = _units_observed(
            first_corpus_tables, make_unit(1, UnitTypeId.OVERLORD, is_flying=True), _enemy_report(2, UnitTypeId.MARINE)
        )
        assert not overlord.has_in_range(marine, margin=100.0)


class TestDamage:
    @pytest.mark.parametrize(
        ("attacker", "target", "damage"),
        [
            # Each as `tools/sweep_combat.py`'s `damage` and `no-weapon` measured it.
            (UnitTypeId.MARINE, UnitTypeId.ROACH, 5.0),
            (UnitTypeId.MARAUDER, UnitTypeId.ROACH, 19.0),
            (UnitTypeId.COLOSSUS, UnitTypeId.ZERGLING, 30.0),
            (UnitTypeId.VOID_RAY, UnitTypeId.ROACH, 9.0),
            (UnitTypeId.ORACLE, UnitTypeId.SCV, 22.0),
        ],
    )
    def test_a_hit_is_its_damage_and_bonus_less_the_armor(
        self, first_corpus_tables: GameData, attacker: UnitTypeId, target: UnitTypeId, damage: float
    ) -> None:
        unit, enemy = _units_observed(
            first_corpus_tables, make_unit(1, attacker, buff_ids=_BEAM_ON), _enemy_report(2, target)
        )
        assert unit.damage_against(enemy) == damage

    def test_a_hit_takes_off_health_at_least_half_a_point(self, first_corpus_tables: GameData) -> None:
        marine, ultralisk = _units_observed(
            first_corpus_tables, make_unit(1), _enemy_report(2, UnitTypeId.ULTRALISK, armor_upgrade_level=5)
        )
        assert marine.damage_against(ultralisk) == 0.5

    def test_an_oracles_beam_ignores_armor_and_shield_armor(self, first_corpus_tables: GameData) -> None:
        """It took 15 off a roach with 3 more armor, and 22 off a zealot with 6 shields at shields level 3 and armor 4,
        6 off its shields and 16 off its health."""
        oracle, roach, zealot = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.ORACLE, buff_ids=_BEAM_ON),
            _enemy_report(2, UnitTypeId.ROACH, armor_upgrade_level=3),
            _enemy_report(3, UnitTypeId.ZEALOT, shield=6.0, shield_upgrade_level=3, armor_upgrade_level=3),
        )
        assert (oracle.damage_against(roach), oracle.damage_against(zealot)) == (15.0, 6.0 + 16.0)

    def test_a_hit_on_shields_is_less_the_shield_armor(self, first_corpus_tables: GameData) -> None:
        marine, zealot = _units_observed(
            first_corpus_tables, make_unit(1), _enemy_report(2, UnitTypeId.ZEALOT, shield=50.0, shield_upgrade_level=3)
        )
        assert marine.damage_against(zealot) == 3.0

    def test_what_a_hit_has_past_the_shields_goes_on_to_health_less_the_armor(
        self, first_corpus_tables: GameData
    ) -> None:
        """An upgraded zealot's blade of 11, on a zealot with 2 shields, shields level 3 and armor 4, took the 2 and 2
        of its health; the second, 11 less 4."""
        levels = (
            UpgradeId.PROTOSS_GROUND_WEAPONS_1,
            UpgradeId.PROTOSS_GROUND_WEAPONS_2,
            UpgradeId.PROTOSS_GROUND_WEAPONS_3,
        )
        zealot, target = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.ZEALOT),
            _enemy_report(2, UnitTypeId.ZEALOT, shield=2.0, shield_upgrade_level=3, armor_upgrade_level=3),
            upgrades=levels,
        )
        assert zealot.damage_against(target) == (2.0 + 2.0) + 7.0

    def test_a_unit_with_no_weapon_against_a_target_does_it_no_damage(self, first_corpus_tables: GameData) -> None:
        viking, zergling = _units_observed(
            first_corpus_tables, make_unit(1, UnitTypeId.VIKING, is_flying=True), _enemy_report(2, UnitTypeId.ZERGLING)
        )
        assert viking.damage_against(zergling) == viking.dps_against(zergling) == 0.0

    def test_a_hallucination_does_no_damage(self, first_corpus_tables: GameData) -> None:
        marine, scv = _units_observed(
            first_corpus_tables, make_unit(1, is_hallucination=True), _enemy_report(2, UnitTypeId.SCV)
        )
        assert marine.weapon_against(scv) is marine.weapons[0]
        assert marine.damage_against(scv) == marine.dps_against(scv) == 0.0


class TestDps:
    def test_a_unit_does_one_attack_each_cooldown(self, first_corpus_tables: GameData) -> None:
        marine, scv = _units_observed(first_corpus_tables, make_unit(1), _enemy_report(2, UnitTypeId.SCV))
        cooldown = first_corpus_tables.units[UnitTypeId.MARINE].weapons[0].average_cooldown.steps
        assert marine.dps_against(scv) == pytest.approx(6.0 * 22.4 / cooldown)

    def test_stim_has_a_marine_attack_one_and_a_half_times_as_often(self, first_corpus_tables: GameData) -> None:
        """Its tables' cooldown divided, and the delay added to what is left."""
        marine, scv = _units_observed(
            first_corpus_tables, make_unit(1, buff_ids=[BuffId.MARINE_STIMMED]), _enemy_report(2, UnitTypeId.SCV)
        )
        tables_cooldown = first_corpus_tables.units[UnitTypeId.MARINE].weapons[0].base_cooldown.steps
        assert marine.dps_against(scv) == pytest.approx(6.0 * 22.4 / (tables_cooldown / 1.5 + _MEAN_DELAY), abs=1e-3)

    def test_stim_has_a_marauder_attack_one_and_a_half_times_as_often(self, first_corpus_tables: GameData) -> None:
        marauder, scv = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.MARAUDER, buff_ids=[BuffId.MARAUDER_STIMMED]),
            _enemy_report(2, UnitTypeId.SCV),
        )
        tables_cooldown = first_corpus_tables.units[UnitTypeId.MARAUDER].weapons[0].base_cooldown.steps
        assert marauder.dps_against(scv) == pytest.approx(10.0 * 22.4 / (tables_cooldown / 1.5 + _MEAN_DELAY), abs=1e-3)


class TestTheWeaponForEachDomain:
    def test_a_weapon_that_hits_both_is_each_domains(self, first_corpus_tables: GameData) -> None:
        (marine,) = _units_observed(first_corpus_tables, make_unit(1))
        weapon = marine.weapons[0]
        assert marine.ground_weapon is marine.air_weapon is weapon
        assert marine.ground_range == marine.air_range == weapon.range
        assert marine.ground_damage == marine.air_damage == weapon.damage * weapon.attacks
        assert marine.ground_dps == marine.air_dps == weapon.dps

    def test_a_queen_has_one_weapon_for_each_domain(self, first_corpus_tables: GameData) -> None:
        (queen,) = _units_observed(first_corpus_tables, make_unit(1, UnitTypeId.QUEEN))
        assert _target_domain_of(queen.ground_weapon) is TargetDomain.GROUND
        assert _target_domain_of(queen.air_weapon) is TargetDomain.AIR
        assert queen.ground_range != queen.air_range

    def test_a_domain_without_a_weapon_reads_none_and_zero(self, first_corpus_tables: GameData) -> None:
        zergling, medivac = _units_observed(
            first_corpus_tables, make_unit(1, UnitTypeId.ZERGLING), make_unit(2, UnitTypeId.MEDIVAC, is_flying=True)
        )
        assert zergling.ground_weapon is not None and zergling.air_weapon is None
        assert (zergling.air_range, zergling.air_damage, zergling.air_dps) == (0.0, 0.0, 0.0)
        assert medivac.ground_weapon is medivac.air_weapon is None
        assert (medivac.ground_range, medivac.ground_damage, medivac.ground_dps) == (0.0, 0.0, 0.0)

    def test_an_oracle_has_a_ground_weapon_only_while_its_beam_is_on(self, first_corpus_tables: GameData) -> None:
        beaming, idle = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.ORACLE, is_flying=True, buff_ids=_BEAM_ON),
            make_unit(2, UnitTypeId.ORACLE, is_flying=True),
        )
        assert beaming.ground_weapon is beaming.weapons[0] and beaming.air_weapon is None
        assert idle.ground_weapon is None and idle.ground_range == 0.0

    def test_melee_fires_at_the_ground_from_no_farther_than_one(self, first_corpus_tables: GameData) -> None:
        zealot, zergling, marine, medivac = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.ZEALOT),
            make_unit(2, UnitTypeId.ZERGLING),
            make_unit(3),
            make_unit(4, UnitTypeId.MEDIVAC, is_flying=True),
        )
        assert [unit.is_melee for unit in (zealot, zergling, marine, medivac)] == [True, True, False, False]


class TestBonusDamage:
    def test_it_is_the_bonus_against_the_targets_attributes(self, first_corpus_tables: GameData) -> None:
        """A marauder does bonus damage against armored, which a stalker is and a zergling is not."""
        marauder, stalker, zergling = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.MARAUDER),
            _enemy_report(2, UnitTypeId.STALKER),
            _enemy_report(3, UnitTypeId.ZERGLING),
        )
        weapon = marauder.weapon_against(stalker)
        assert weapon is not None
        expected = sum(weapon.damage_bonuses.values()) * weapon.attacks
        assert marauder.bonus_damage_against(stalker) == expected > 0
        assert marauder.bonus_damage_against(zergling) == 0.0

    def test_there_is_none_without_a_weapon_against_the_target(self, first_corpus_tables: GameData) -> None:
        medivac, stalker = _units_observed(
            first_corpus_tables, make_unit(1, UnitTypeId.MEDIVAC, is_flying=True), _enemy_report(2, UnitTypeId.STALKER)
        )
        assert medivac.bonus_damage_against(stalker) == 0.0


class TestAWeaponReady:
    def test_it_is_ready_with_a_weapon_and_no_cooldown(self, first_corpus_tables: GameData) -> None:
        ready, cooling, unarmed = _units_observed(
            first_corpus_tables,
            make_unit(1, weapon_cooldown=0.0),
            make_unit(2, weapon_cooldown=6.0),
            make_unit(3, UnitTypeId.MEDIVAC, is_flying=True),
        )
        assert isinstance(ready, OwnUnit) and isinstance(cooling, OwnUnit) and isinstance(unarmed, OwnUnit)
        assert [unit.is_weapon_ready for unit in (ready, cooling, unarmed)] == [True, False, False]


class TestAddOnsAndCapacity:
    def test_a_structure_trains_two_at_once_with_a_reactor_one_without_and_others_none(
        self, first_corpus_tables: GameData
    ) -> None:
        """The game's tables give the barracks, the starport and the command center as makers of units; an SCV makes
        structures, and an engineering bay none."""
        units = _units_observed(
            first_corpus_tables,
            make_unit(1, UnitTypeId.BARRACKS, add_on_tag=11, build_progress=1.0),
            make_unit(11, UnitTypeId.REACTOR_BARRACKS, build_progress=1.0),
            make_unit(2, UnitTypeId.STARPORT, add_on_tag=12, build_progress=1.0),
            make_unit(12, UnitTypeId.TECH_LAB_STARPORT, build_progress=1.0),
            make_unit(3, UnitTypeId.COMMAND_CENTER, build_progress=1.0),
            make_unit(4, UnitTypeId.ENGINEERING_BAY, build_progress=1.0),
            make_unit(5, UnitTypeId.SCV),
            make_unit(6),
        )
        barracks, _, starport, _, command_center, engineering_bay, scv, marine = units
        own = [
            unit
            for unit in (barracks, starport, command_center, engineering_bay, scv, marine)
            if isinstance(unit, OwnUnit)
        ]
        assert len(own) == 6
        assert [unit.production_capacity for unit in own] == [2, 1, 1, 0, 0, 0]
        assert [(unit.has_reactor, unit.has_tech_lab) for unit in own[:3]] == [
            (True, False),
            (False, True),
            (False, False),
        ]


class TestTheUnitsInRange:
    """A marine of range 5 at (10, 10), against an enemy stalker of range 6 five away, a zergling of range 0.1 four
    away, and a marine thirty away; every radius zero."""

    @staticmethod
    def _armies(tables: GameData) -> tuple[Unit[Any], list[Unit[Any]]]:
        marine, stalker, zergling, far_marine = _units_observed(
            tables,
            make_unit(1, at=(10.0, 10.0)),
            make_unit(2, UnitTypeId.STALKER, alliance=Alliance.ENEMY, at=(15.0, 10.0)),
            make_unit(3, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, at=(14.0, 10.0)),
            make_unit(4, alliance=Alliance.ENEMY, at=(40.0, 10.0)),
        )
        return marine, [stalker, zergling, far_marine]

    def test_the_units_having_a_target_in_range_are_those_that_can_hit_it(self, first_corpus_tables: GameData) -> None:
        marine, enemies = self._armies(first_corpus_tables)
        stalker = enemies[0]
        assert list(Units(enemies).having_in_range(marine)) == [stalker]
        assert list(Units(enemies).having_in_range(marine, margin=-1.5)) == []

    def test_the_units_in_range_of_an_attacker_are_those_it_can_hit(self, first_corpus_tables: GameData) -> None:
        marine, enemies = self._armies(first_corpus_tables)
        stalker, zergling, _ = enemies
        assert list(Units(enemies).in_range_of(marine)) == [stalker, zergling]
        assert list(Units(enemies).in_range_of(marine, margin=-0.5)) == [zergling]
