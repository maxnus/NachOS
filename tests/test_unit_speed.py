"""A unit's speed where it stands: its type's on creep or off, with its owner's upgrades, times what each buff it wears
makes of it, as `docs/reference/game-behavior/units.md` holds them measured."""

import dataclasses
from typing import Any

import numpy
import pytest

from sc2nachos import Api
from sc2nachos.events import TurnStartEvent
from sc2nachos.geometry import Grid
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.testing import MadeUpUnits, UnitReport, load_game_data
from sc2nachos.units import Alliance
from support import (
    ScriptedGame,
    make_bits,
    make_game_info,
    make_observation,
    make_tracker,
    make_unit,
    require_enemy,
    shipped_game_data,
)

_DATA = load_game_data()
_CREEP = Grid(numpy.ones((32, 32), dtype=bool), outside=False)
_NO_CREEP = Grid(numpy.zeros((32, 32), dtype=bool), outside=False)
_ZERGLING_SPEED = _DATA.units[UnitTypeId.ZERGLING].speed
_ZERGLING_CREEP_SPEED = 5.3703
_MARINE_SPEED = 3.15
_MAP_ROWS = ("#" * 40,) * 40


class TestATypesSpeedOnCreep:
    @pytest.mark.parametrize(
        ("unit_type", "creep_speed"),
        [
            (UnitTypeId.ZERGLING, _ZERGLING_CREEP_SPEED),
            (UnitTypeId.ROACH, 4.0906),
            (UnitTypeId.INFESTOR_BURROWED, 3.6367),
            (UnitTypeId.LOCUST, 3.6695),
            (UnitTypeId.QUEEN, 3.4945),
            (UnitTypeId.CHANGELING_ZERGLING, _ZERGLING_CREEP_SPEED),
        ],
    )
    def test_is_as_measured_for_a_type_creep_speeds_up(self, unit_type: UnitTypeId, creep_speed: float) -> None:
        assert _DATA.units[unit_type].creep_speed == creep_speed

    @pytest.mark.parametrize(
        "unit_type",
        [UnitTypeId.DRONE, UnitTypeId.BROODLING, UnitTypeId.CHANGELING, UnitTypeId.MUTALISK, UnitTypeId.MARINE],
    )
    def test_is_its_speed_off_creep_for_any_other(self, unit_type: UnitTypeId) -> None:
        row = _DATA.units[unit_type]
        assert row.creep_speed == row.speed

    @pytest.mark.parametrize(
        ("unit_type", "upgrade", "creep_speed"),
        [
            (UnitTypeId.ZERGLING, UpgradeId.ZERGLING_SPEED, 8.5477),
            (UnitTypeId.BANELING, UpgradeId.BANELING_SPEED, 5.3703),
            (UnitTypeId.ROACH, UpgradeId.ROACH_SPEED, 5.4578),
            (UnitTypeId.HYDRALISK, UpgradeId.HYDRALISK_SPEED, 4.8289),
            (UnitTypeId.ULTRALISK, UpgradeId.ULTRALISK_SPEED, 5.5344),
        ],
    )
    def test_with_a_speed_upgrade_is_as_measured_with_it(
        self, unit_type: UnitTypeId, upgrade: UpgradeId, creep_speed: float
    ) -> None:
        """Muscular Augments and Anabolic Synthesis add less on creep than off it."""
        assert _DATA.units[unit_type].with_upgrades({upgrade}).creep_speed == creep_speed

    @pytest.mark.parametrize(
        ("unit_type", "upgrade"),
        [
            (UnitTypeId.OVERLORD, UpgradeId.OVERLORD_SPEED),
            (UnitTypeId.OVERLORD_TRANSPORT, UpgradeId.OVERLORD_SPEED),
            (UnitTypeId.WARP_PRISM, UpgradeId.WARP_PRISM_SPEED),
            (UnitTypeId.OBSERVER, UpgradeId.OBSERVER_SPEED),
        ],
    )
    def test_stays_the_speed_off_creep_with_an_upgrade_of_a_type_creep_does_not_speed_up(
        self, unit_type: UnitTypeId, upgrade: UpgradeId
    ) -> None:
        """Equal, where adding the upgrade's change to each would leave them a rounding apart."""
        upgraded = _DATA.units[unit_type].with_upgrades({upgrade})
        assert upgraded.creep_speed == upgraded.speed > _DATA.units[unit_type].speed

    def test_gains_with_an_upgrade_measured_off_creep_alone_what_the_upgrade_adds_off_it(self) -> None:
        """No type creep speeds up has such an upgrade, so a made-up overlord stands for one."""
        overlord = dataclasses.replace(_DATA.units[UnitTypeId.OVERLORD], creep_speed=1.0)
        upgraded = overlord.with_upgrades({UpgradeId.OVERLORD_SPEED})
        assert upgraded.creep_speed == pytest.approx(1.0 + upgraded.speed - overlord.speed)


class TestAUnitsSpeed:
    def test_off_creep_is_its_base_speed(self) -> None:
        (zergling,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.ZERGLING), creep=_NO_CREEP)
        assert zergling.speed == zergling.base_speed == _ZERGLING_SPEED
        assert zergling.base_creep_speed == _ZERGLING_CREEP_SPEED

    def test_on_creep_is_its_base_creep_speed(self) -> None:
        (zergling,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.ZERGLING), creep=_CREEP)
        assert zergling.speed == _ZERGLING_CREEP_SPEED
        assert zergling.base_speed == _ZERGLING_SPEED

    def test_with_no_creep_known_is_as_off_creep(self) -> None:
        (zergling,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.ZERGLING))
        assert zergling.speed == _ZERGLING_SPEED

    def test_on_creep_is_a_drones_base_speed(self) -> None:
        (drone,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.DRONE), creep=_CREEP)
        assert drone.speed == drone.base_speed == _DATA.units[UnitTypeId.DRONE].speed

    def test_over_creep_is_a_flying_units_base_speed(self) -> None:
        """A made-up overlord that creep sped up would show it: none does, so a zergling the game reports flying,
        as it would one a phoenix lifts, stands for it."""
        (zergling,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.ZERGLING, is_flying=True), creep=_CREEP)
        assert zergling.speed == _ZERGLING_SPEED

    @pytest.mark.parametrize(
        ("buff", "factor"),
        [
            (BuffId.MARINE_STIMMED, 1.5),
            (BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW, 0.5),
            (BuffId.INHIBITOR_ZONE, 0.65),
            (BuffId.MOTHERSHIP_TIME_WARP, 0.6),
            (BuffId.INFESTOR_FUNGAL_GROWTH, 0.25),
        ],
    )
    def test_is_its_base_speed_times_what_a_buff_makes_of_it(self, buff: BuffId, factor: float) -> None:
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE, buffs=(buff,)), creep=_NO_CREEP)
        assert marine.speed == pytest.approx(_MARINE_SPEED * factor)

    @pytest.mark.parametrize(
        ("unit_type", "buff", "factor"),
        [(UnitTypeId.MEDIVAC, BuffId.MEDIVAC_BOOST, 1.7), (UnitTypeId.HYDRALISK, BuffId.HYDRALISK_LUNGE, 2.0)],
    )
    def test_is_its_base_speed_times_what_a_buff_it_puts_on_itself_makes_of_it(
        self, unit_type: UnitTypeId, buff: BuffId, factor: float
    ) -> None:
        (unit,) = MadeUpUnits().observe(UnitReport(1, unit_type, buffs=(buff,)))
        assert unit.speed == pytest.approx(_DATA.units[unit_type].speed * factor)

    @pytest.mark.parametrize(
        ("unit_type", "buff", "share"),
        [
            (UnitTypeId.VOID_RAY, BuffId.VOID_RAY_PRISMATIC_ALIGNMENT, 0.75),
            (UnitTypeId.ZEALOT, BuffId.ZEALOT_CHARGING, 3.2973),
        ],
    )
    def test_is_a_share_of_its_types_speed_before_upgrades_under_a_buff_that_sets_it(
        self, unit_type: UnitTypeId, buff: BuffId, share: float
    ) -> None:
        (unit,) = MadeUpUnits().observe(UnitReport(1, unit_type, buffs=(buff,)))
        assert unit.speed == pytest.approx(_DATA.units[unit_type].speed * share)

    def test_in_prismatic_alignment_leaves_flux_vanes_out(self) -> None:
        """A void ray of the enemy's known to have Flux Vanes moves at 0.75 of its 3.85, not of its 4.65."""
        tracker = make_tracker(_DATA)
        require_enemy(tracker.enemy).assume_upgrades(UpgradeId.VOID_RAY_SPEED)
        aligned = make_unit(
            1, UnitTypeId.VOID_RAY, alliance=Alliance.ENEMY, buff_ids=[BuffId.VOID_RAY_PRISMATIC_ALIGNMENT]
        )
        tracker.update(make_observation(0, units=[aligned]).observation.raw_data, 0)
        (void_ray,) = tracker.unit_tracker.present
        assert void_ray.base_speed > _DATA.units[UnitTypeId.VOID_RAY].speed
        assert void_ray.speed == pytest.approx(_DATA.units[UnitTypeId.VOID_RAY].speed * 0.75)

    def test_set_by_a_buff_is_still_multiplied_by_the_others(self) -> None:
        """Unmeasured together: the factors multiply the speed a buff sets, as they multiply any other."""
        buffs = (BuffId.VOID_RAY_PRISMATIC_ALIGNMENT, BuffId.MOTHERSHIP_TIME_WARP)
        (void_ray,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.VOID_RAY, alliance=Alliance.ENEMY, buffs=buffs))
        assert void_ray.speed == pytest.approx(_DATA.units[UnitTypeId.VOID_RAY].speed * 0.75 * 0.6)

    def test_stimmed_is_a_marauders_base_speed_half_as_fast_again(self) -> None:
        (marauder,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARAUDER, buffs=(BuffId.MARAUDER_STIMMED,)))
        assert marauder.speed == pytest.approx(_MARINE_SPEED * 1.5)

    def test_with_two_buffs_multiplies_both(self) -> None:
        buffs = (BuffId.MARINE_STIMMED, BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW)
        (marine,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.MARINE, buffs=buffs))
        assert marine.speed == pytest.approx(_MARINE_SPEED * 1.5 * 0.5)

    def test_on_creep_with_a_buff_multiplies_the_creep_speed(self) -> None:
        report = UnitReport(1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, buffs=(BuffId.INFESTOR_FUNGAL_GROWTH,))
        (zergling,) = MadeUpUnits().observe(report, creep=_CREEP)
        assert zergling.speed == pytest.approx(_ZERGLING_CREEP_SPEED * 0.25)

    def test_ignores_a_buff_no_measurement_covers(self) -> None:
        (zergling,) = MadeUpUnits().observe(UnitReport(1, UnitTypeId.ZERGLING, buffs=(BuffId.ACCELERATION_ZONE,)))
        assert zergling.speed == _ZERGLING_SPEED

    def test_out_of_sight_is_its_speed_on_the_ground_it_was_last_seen_on_with_no_buff(self) -> None:
        """A slow lasts a second; the creep under it is the creep there now."""
        made_up_units = MadeUpUnits()
        slowed = UnitReport(
            1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, buffs=(BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW,)
        )
        (zergling,) = made_up_units.observe(slowed, creep=_NO_CREEP)
        assert zergling.speed == pytest.approx(_ZERGLING_SPEED * 0.5)
        made_up_units.observe(creep=_CREEP)
        assert not zergling.is_visible
        assert zergling.speed == _ZERGLING_CREEP_SPEED


def test_in_a_game_a_unit_reads_the_creep_of_each_observation() -> None:
    """The game decodes the creep a unit's speed reads, an observation's for its turn."""
    zergling = make_unit(1, UnitTypeId.ZERGLING, at=(20.0, 20.0), alliance=Alliance.ENEMY)
    observations = [
        make_observation(0, units=[zergling], creep=make_bits(*_MAP_ROWS, drawn="")),
        make_observation(1, units=[zergling], creep=make_bits(*_MAP_ROWS)),
        make_observation(2),
    ]
    speeds: list[float] = []
    api: Api[Any] = Api()

    @api.on(TurnStartEvent)
    def _read_the_speed(_: TurnStartEvent) -> None:
        if api.units.enemy:
            speeds.append(api.units.enemy[0].speed)

    api.play(ScriptedGame(observations, game_info=make_game_info(*_MAP_ROWS), data=shipped_game_data()).joined())
    assert speeds == [_ZERGLING_SPEED, _ZERGLING_CREEP_SPEED]
