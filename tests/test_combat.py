"""A unit's weapon, reach, damage and dps against another, on the tables of the first corpus game."""

from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import raw_pb2

from sc2nachos.gamedata import GameData, TargetDomain
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.protocol import Recording
from sc2nachos.units import Alliance, Unit
from sc2nachos.units._tracking import _Tracker
from support import make_enemy, make_observation, make_unit

_CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))
# The mean of the random delay the game adds to every attack's cooldown, in steps (tool `sweep_combat`).
_MEAN_DELAY = 0.4996


@pytest.fixture(scope="module")
def tables() -> GameData:
    """The game data of the first corpus game."""
    recording = Recording(_CORPUS[0])
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


def _observe(tables: GameData, *units: raw_pb2.Unit, upgrades: tuple[int, ...] = ()) -> list[Unit[Any]]:
    """`units` as one observation shows them, this player having `upgrades`."""
    tracker = _Tracker(tables, make_enemy())
    tracker.update(make_observation(0, units=units, upgrades=upgrades).observation.raw_data, 0)
    by_tag = {unit.tag: unit for unit in tracker.unit_tracker.present}
    return [by_tag[unit.tag] for unit in units]


def _enemy(tag: int, unit_type: UnitTypeId, **fields: Any) -> raw_pb2.Unit:
    return make_unit(tag, unit_type, alliance=Alliance.ENEMY, at=(20.0, 10.0), **fields)


class TestTheWeaponAgainstATarget:
    def test_a_weapon_that_hits_both_is_fired_at_either(self, tables: GameData) -> None:
        marine, zergling, overlord = _observe(
            tables,
            make_unit(1),
            _enemy(2, UnitTypeId.ZERGLING),
            _enemy(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert marine.weapon_against(zergling) is marine.weapon_against(overlord) is marine.weapons[0]

    def test_a_flier_takes_the_air_weapon_and_a_unit_on_the_ground_the_ground_one(self, tables: GameData) -> None:
        queen, marine, overlord = _observe(
            tables,
            make_unit(1, UnitTypeId.QUEEN),
            _enemy(2, UnitTypeId.MARINE),
            _enemy(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert _domain(queen.weapon_against(marine)) is TargetDomain.GROUND
        assert _domain(queen.weapon_against(overlord)) is TargetDomain.AIR

    def test_air_weapons_hit_a_colossus_when_there_is_no_ground_weapon(self, tables: GameData) -> None:
        viking, thor, colossus, zergling = _observe(
            tables,
            make_unit(1, UnitTypeId.VIKING, is_flying=True),
            make_unit(2, UnitTypeId.THOR),
            _enemy(3, UnitTypeId.COLOSSUS),
            _enemy(4, UnitTypeId.ZERGLING),
        )
        assert _domain(viking.weapon_against(colossus)) is TargetDomain.AIR
        assert viking.weapon_against(zergling) is None
        assert _domain(thor.weapon_against(colossus)) is TargetDomain.GROUND

    def test_an_oracle_fires_at_nothing_in_the_air(self, tables: GameData) -> None:
        oracle, overlord, scv = _observe(
            tables,
            make_unit(1, UnitTypeId.ORACLE, is_flying=True),
            _enemy(2, UnitTypeId.OVERLORD, is_flying=True),
            _enemy(3, UnitTypeId.SCV),
        )
        assert oracle.weapon_against(overlord) is None
        assert oracle.weapon_against(scv) is oracle.weapons[0]

    def test_a_battlecruiser_fires_its_measured_lasers(self, tables: GameData) -> None:
        battlecruiser, marine, overlord = _observe(
            tables,
            make_unit(1, UnitTypeId.BATTLECRUISER, is_flying=True),
            _enemy(2, UnitTypeId.MARINE),
            _enemy(3, UnitTypeId.OVERLORD, is_flying=True),
        )
        assert _damage(battlecruiser.weapon_against(marine)) == 8.0
        assert _damage(battlecruiser.weapon_against(overlord)) == 5.0


def _domain(weapon: Any) -> TargetDomain:
    assert weapon is not None
    return weapon.target_domain


def _damage(weapon: Any) -> float:
    assert weapon is not None
    return weapon.damage


class TestReach:
    # A marine's 5 and two radii of 0.375: it fired at an SCV out to 5.75 (tool `sweep_combat`, `range`).
    @pytest.mark.parametrize(
        ("x", "margin", "in_range"),
        [(15.75, 0.0, True), (15.76, 0.0, False), (15.75, -0.125, False), (15.875, 0.125, True)],
    )
    def test_a_target_is_in_range_out_to_the_range_and_both_radii(
        self, tables: GameData, x: float, margin: float, in_range: bool
    ) -> None:
        marine, scv = _observe(
            tables,
            make_unit(1, at=(10.0, 10.0), radius=0.375),
            make_unit(2, UnitTypeId.SCV, alliance=Alliance.ENEMY, at=(x, 10.0), radius=0.375),
        )
        assert marine.has_in_range(scv, margin=margin) is in_range

    def test_nothing_is_in_range_of_a_unit_with_no_weapon_against_it(self, tables: GameData) -> None:
        overlord, marine = _observe(
            tables, make_unit(1, UnitTypeId.OVERLORD, is_flying=True), _enemy(2, UnitTypeId.MARINE)
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
        self, tables: GameData, attacker: UnitTypeId, target: UnitTypeId, damage: float
    ) -> None:
        unit, enemy = _observe(tables, make_unit(1, attacker), _enemy(2, target))
        assert unit.damage_against(enemy) == damage

    def test_a_hit_takes_off_health_at_least_half_a_point(self, tables: GameData) -> None:
        marine, ultralisk = _observe(tables, make_unit(1), _enemy(2, UnitTypeId.ULTRALISK, armor_upgrade_level=5))
        assert marine.damage_against(ultralisk) == 0.5

    def test_an_oracles_beam_ignores_armor(self, tables: GameData) -> None:
        oracle, roach = _observe(
            tables, make_unit(1, UnitTypeId.ORACLE), _enemy(2, UnitTypeId.ROACH, armor_upgrade_level=3)
        )
        assert oracle.damage_against(roach) == 15.0

    def test_a_hit_on_shields_is_less_the_shield_armor(self, tables: GameData) -> None:
        marine, zealot = _observe(
            tables, make_unit(1), _enemy(2, UnitTypeId.ZEALOT, shield=50.0, shield_upgrade_level=3)
        )
        assert marine.damage_against(zealot) == 3.0

    def test_what_a_hit_has_past_the_shields_goes_on_to_health_less_the_armor(self, tables: GameData) -> None:
        """An upgraded zealot's blade of 11, on a zealot with 2 shields, shields level 3 and armor 4, took the 2 and 2
        of its health; the second, 11 less 4."""
        levels = (
            UpgradeId.PROTOSS_GROUND_WEAPONS_1,
            UpgradeId.PROTOSS_GROUND_WEAPONS_2,
            UpgradeId.PROTOSS_GROUND_WEAPONS_3,
        )
        zealot, target = _observe(
            tables,
            make_unit(1, UnitTypeId.ZEALOT),
            _enemy(2, UnitTypeId.ZEALOT, shield=2.0, shield_upgrade_level=3, armor_upgrade_level=3),
            upgrades=levels,
        )
        assert zealot.damage_against(target) == (2.0 + 2.0) + 7.0

    def test_a_unit_with_no_weapon_against_a_target_does_it_no_damage(self, tables: GameData) -> None:
        viking, zergling = _observe(
            tables, make_unit(1, UnitTypeId.VIKING, is_flying=True), _enemy(2, UnitTypeId.ZERGLING)
        )
        assert viking.damage_against(zergling) == viking.dps_against(zergling) == 0.0


class TestDps:
    def test_a_unit_does_one_attack_each_cooldown(self, tables: GameData) -> None:
        marine, scv = _observe(tables, make_unit(1), _enemy(2, UnitTypeId.SCV))
        cooldown = tables.units[UnitTypeId.MARINE].weapons[0].cooldown_steps
        assert marine.dps_against(scv) == pytest.approx(6.0 * 22.4 / cooldown)

    def test_stim_has_a_marine_attack_one_and_a_half_times_as_often(self, tables: GameData) -> None:
        """Its tables' cooldown divided, and the delay added to what is left."""
        marine, scv = _observe(tables, make_unit(1, buff_ids=[BuffId.MARINE_STIMMED]), _enemy(2, UnitTypeId.SCV))
        tables_cooldown = tables.units[UnitTypeId.MARINE].weapons[0].cooldown_steps - _MEAN_DELAY
        assert marine.dps_against(scv) == pytest.approx(6.0 * 22.4 / (tables_cooldown / 1.5 + _MEAN_DELAY), abs=1e-3)
