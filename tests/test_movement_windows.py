"""The movement windows upgrade inference is to read speed upgrades off: how far each enemy unit moved against how far
it would have moved without its speed upgrade and with it, in made-up games."""

from collections.abc import Sequence
from typing import Any

import pytest
from s2clientprotocol import common_pb2, raw_pb2

from sc2nachos import Api
from sc2nachos.extensions.upgrade_inference._movement_windows import (
    _CREEP_SPEEDS,
    MovementWindow,
    MovementWindows,
    speed_upgrade_by_type,
)
from sc2nachos.gamedata import GameData
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, Visibility
from support import ScriptedGame, make_bits, make_game_info, make_observation, make_unit, shipped_game_data

_GAME_DATA = GameData(shipped_game_data())
_MAP_ROWS = ("#" * 40,) * 40
_CREEP_EVERYWHERE = make_bits(*_MAP_ROWS)
_NO_CREEP = make_bits(*_MAP_ROWS, drawn="")
_CREEP_LEFT_OF_X_10 = make_bits(*(("#" * 10 + "." * 30,) * 40))
_ZERGLING_STEP = _GAME_DATA.units[UnitTypeId.ZERGLING].speed / STEPS_PER_SECOND
_FAST_ZERGLING_STEP = (
    _GAME_DATA.units[UnitTypeId.ZERGLING].with_upgrades({UpgradeId.ZERGLING_SPEED}).speed / STEPS_PER_SECOND
)
_ZERGLING_TAG = 7


def _windows_handed_on(
    observed: Sequence[Sequence[raw_pb2.Unit]],
    *,
    steps_apart: int = 1,
    creep: common_pb2.ImageData = _NO_CREEP,
    window_steps: int = 11,
) -> list[Sequence[MovementWindow]]:
    """The windows `MovementWindows` hands on together, each time it does, over a game of a turn on each of
    `observed`, the units each observation lists, `steps_apart` steps apart, with creep where `creep` has it."""
    observations = [
        make_observation(index * steps_apart, units=units, creep=creep) for index, units in enumerate([*observed, ()])
    ]
    handed_on: list[Sequence[MovementWindow]] = []
    api: Api[Any] = Api(
        extensions=lambda api: MovementWindows(api, window_steps=window_steps, on_windows=handed_on.append)
    )
    api.play(ScriptedGame(observations, game_info=make_game_info(*_MAP_ROWS), data=shipped_game_data()).joined())
    return handed_on


def _windows_of(
    observed: Sequence[Sequence[raw_pb2.Unit]],
    *,
    steps_apart: int = 1,
    creep: common_pb2.ImageData = _NO_CREEP,
    window_steps: int = 11,
) -> list[MovementWindow]:
    """Every window `_windows_handed_on` gives, in the order handed on."""
    handed_on = _windows_handed_on(observed, steps_apart=steps_apart, creep=creep, window_steps=window_steps)
    return [window for windows in handed_on for window in windows]


def _zergling_at(x: float, **fields: Any) -> raw_pb2.Unit:
    return make_unit(_ZERGLING_TAG, UnitTypeId.ZERGLING, at=(x, 20.0), alliance=Alliance.ENEMY, **fields)


def _zergling_running(distance_a_step: float, steps: int, **fields: Any) -> list[list[raw_pb2.Unit]]:
    """A zergling running along x, `distance_a_step` each step, over `steps` observations a step apart."""
    return [[_zergling_at(2.0 + distance_a_step * step, **fields)] for step in range(steps)]


def _types_and_steps_of(windows: Sequence[MovementWindow]) -> list[tuple[UnitTypeId, int, int]]:
    return [(window.type_id, window.start_step, window.end_step) for window in windows]


class TestTheTypes:
    def test_they_are_the_twelve_one_speed_upgrade_changes_but_for_charge(self) -> None:
        assert speed_upgrade_by_type(_GAME_DATA) == {
            UnitTypeId.BANELING: UpgradeId.BANELING_SPEED,
            UnitTypeId.BANSHEE: UpgradeId.BANSHEE_SPEED,
            UnitTypeId.HYDRALISK: UpgradeId.HYDRALISK_SPEED,
            UnitTypeId.OBSERVER: UpgradeId.OBSERVER_SPEED,
            UnitTypeId.OVERLORD: UpgradeId.OVERLORD_SPEED,
            UnitTypeId.OVERLORD_TRANSPORT: UpgradeId.OVERLORD_SPEED,
            UnitTypeId.OVERSEER: UpgradeId.OVERLORD_SPEED,
            UnitTypeId.ROACH: UpgradeId.ROACH_SPEED,
            UnitTypeId.ULTRALISK: UpgradeId.ULTRALISK_SPEED,
            UnitTypeId.VOID_RAY: UpgradeId.VOID_RAY_SPEED,
            UnitTypeId.WARP_PRISM: UpgradeId.WARP_PRISM_SPEED,
            UnitTypeId.ZERGLING: UpgradeId.ZERGLING_SPEED,
        }

    def test_the_creep_speeds_are_for_some_of_them(self) -> None:
        assert _CREEP_SPEEDS.keys() <= speed_upgrade_by_type(_GAME_DATA).keys()


class TestWhereTheDistanceFalls:
    @pytest.mark.parametrize(("distance_a_step", "share"), [(_ZERGLING_STEP, 0.0), (_FAST_ZERGLING_STEP, 1.0)])
    def test_off_creep_a_zergling_at_full_speed_reads_whether_it_has_metabolic_boost(
        self, distance_a_step: float, share: float
    ) -> None:
        windows = _windows_of(_zergling_running(distance_a_step, 24))
        assert [window.upgraded_share for window in windows] == [pytest.approx(share, abs=1e-4)] * 2
        assert [(window.least_step_share, window.greatest_step_share) for window in windows] == [
            (pytest.approx(share, abs=1e-4), pytest.approx(share, abs=1e-4))
        ] * 2
        assert [window.steps_on_creep for window in windows] == [0, 0]

    @pytest.mark.parametrize(("speed_index", "share"), [(0, 0.0), (1, 1.0)])
    def test_on_creep_it_moves_at_its_creep_speeds(self, speed_index: int, share: float) -> None:
        distance_a_step = _CREEP_SPEEDS[UnitTypeId.ZERGLING][speed_index] / STEPS_PER_SECOND
        windows = _windows_of(_zergling_running(distance_a_step, 24), creep=_CREEP_EVERYWHERE)
        assert [window.upgraded_share for window in windows] == [pytest.approx(share, abs=1e-4)] * 2
        assert [window.steps_on_creep for window in windows] == [11, 11]

    def test_creep_does_not_change_a_flying_types_speeds(self) -> None:
        overlord = _GAME_DATA.units[UnitTypeId.OVERLORD]
        distance_a_step = overlord.speed / STEPS_PER_SECOND
        observed = [
            [make_unit(1, UnitTypeId.OVERLORD, at=(2.0 + distance_a_step * step, 20.0), alliance=Alliance.ENEMY)]
            for step in range(12)
        ]
        (window,) = _windows_of(observed, creep=_CREEP_EVERYWHERE)
        assert window.upgraded_share == pytest.approx(0.0, abs=1e-4)
        assert window.steps_on_creep == 0

    def test_across_a_creep_edge_each_move_takes_the_speed_for_where_it_began(self) -> None:
        on_creep, off_creep = _CREEP_SPEEDS[UnitTypeId.ZERGLING][0] / STEPS_PER_SECOND, _ZERGLING_STEP
        positions = [8.5]
        for _ in range(11):
            positions.append(positions[-1] + (on_creep if positions[-1] < 10 else off_creep))
        (window,) = _windows_of([[_zergling_at(x)] for x in positions], creep=_CREEP_LEFT_OF_X_10)
        assert window.steps_on_creep == sum(1 for x in positions[:-1] if x < 10) == 7
        assert window.upgraded_share == pytest.approx(0.0, abs=1e-4)
        assert (window.least_step_share, window.greatest_step_share) == (
            pytest.approx(0.0, abs=1e-3),
            pytest.approx(0.0, abs=1e-3),
        )

    def test_observations_steps_apart_predict_each_step_between(self) -> None:
        observed = [[_zergling_at(2.0 + _FAST_ZERGLING_STEP * 2 * index)] for index in range(7)]
        (window,) = _windows_of(observed, steps_apart=2)
        assert (window.start_step, window.end_step) == (0, 12)
        assert window.upgraded_share == pytest.approx(1.0, abs=1e-4)

    def test_its_steps_tell_a_unit_that_stopped_now_and_then_from_one_slower_throughout(self) -> None:
        positions = [2.0]
        for step in range(1, 12):
            positions.append(positions[-1] + (_FAST_ZERGLING_STEP if step % 4 else 0.0))
        moved_for = sum(1 for step in range(1, 12) if step % 4) * _FAST_ZERGLING_STEP
        (window,) = _windows_of([[_zergling_at(x)] for x in positions])
        gap = 11 * (_FAST_ZERGLING_STEP - _ZERGLING_STEP)
        assert window.upgraded_share == pytest.approx((moved_for - 11 * _ZERGLING_STEP) / gap)
        assert window.least_step_share == pytest.approx(-_ZERGLING_STEP / (_FAST_ZERGLING_STEP - _ZERGLING_STEP))
        assert window.greatest_step_share == pytest.approx(1.0, abs=1e-4)

    def test_one_standing_still_reads_below_zero(self) -> None:
        (window,) = _windows_of(_zergling_running(0.0, 12))
        gap = _FAST_ZERGLING_STEP - _ZERGLING_STEP
        assert window.upgraded_share == pytest.approx(-_ZERGLING_STEP / gap)


class TestWhenAWindowRuns:
    def test_windows_end_on_one_clock_so_a_units_first_may_be_shorter(self) -> None:
        first_seen_on_step_5 = [[], [], [], [], [], *_zergling_running(_ZERGLING_STEP, 40)[5:]]
        windows = _windows_of(first_seen_on_step_5)
        assert _types_and_steps_of(windows) == [
            (UnitTypeId.ZERGLING, 5, 11),
            (UnitTypeId.ZERGLING, 11, 22),
            (UnitTypeId.ZERGLING, 22, 33),
        ]
        assert [window.covers_its_whole_block for window in windows] == [False, True, True]

    def test_one_first_seen_on_a_blocks_first_observation_covers_its_whole_block(self) -> None:
        first_seen_on_step_11 = [[]] * 11 + _zergling_running(_ZERGLING_STEP, 23)[11:]
        windows = _windows_of(first_seen_on_step_11)
        assert _types_and_steps_of(windows) == [(UnitTypeId.ZERGLING, 11, 22)]
        assert windows[0].covers_its_whole_block

    def test_the_windows_of_a_block_are_handed_on_together_once_it_ends(self) -> None:
        observed = [
            [
                _zergling_at(2.0 + _ZERGLING_STEP * step),
                make_unit(1, UnitTypeId.ROACH, at=(2.0 + 0.1 * step, 25.0), alliance=Alliance.ENEMY),
            ]
            for step in range(23)
        ]
        handed_on = _windows_handed_on(observed)
        assert [[window.type_id for window in windows] for windows in handed_on] == [
            [],
            [UnitTypeId.ZERGLING, UnitTypeId.ROACH],
            [UnitTypeId.ZERGLING, UnitTypeId.ROACH],
        ]

    @pytest.mark.parametrize(
        "unseen", [[], [_zergling_at(5.0, visibility=Visibility.INVISIBLE)]], ids=["missing", "invisible"]
    )
    def test_a_unit_not_visible_in_an_observation_begins_again(self, unseen: list[raw_pb2.Unit]) -> None:
        observed = _zergling_running(_ZERGLING_STEP, 24)
        observed[6] = unseen
        windows = _windows_of(observed)
        assert _types_and_steps_of(windows) == [(UnitTypeId.ZERGLING, 7, 11), (UnitTypeId.ZERGLING, 11, 22)]
        assert [window.covers_its_whole_block for window in windows] == [False, True]

    def test_a_unit_that_changes_type_begins_again(self) -> None:
        observed = [
            [
                make_unit(
                    1,
                    UnitTypeId.OVERLORD if step < 5 else UnitTypeId.OVERLORD_TRANSPORT,
                    at=(2.0 + 0.04 * step, 20.0),
                    alliance=Alliance.ENEMY,
                )
            ]
            for step in range(24)
        ]
        assert _types_and_steps_of(_windows_of(observed)) == [
            (UnitTypeId.OVERLORD_TRANSPORT, 5, 11),
            (UnitTypeId.OVERLORD_TRANSPORT, 11, 22),
        ]

    def test_a_unit_on_another_side_than_first_seen_has_none(self) -> None:
        # As a zergling of this player's under a neural parasite is reported the enemy's.
        observed = [
            [make_unit(_ZERGLING_TAG, UnitTypeId.ZERGLING, at=(2.0 + _ZERGLING_STEP * step, 20.0))]
            if step == 0
            else [_zergling_at(2.0 + _ZERGLING_STEP * step)]
            for step in range(24)
        ]
        assert _windows_of(observed) == []

    def test_only_enemy_units_of_those_types_have_windows(self) -> None:
        observed = [
            [
                make_unit(1, UnitTypeId.ZERGLING, at=(2.0 + _ZERGLING_STEP * step, 20.0)),
                make_unit(2, UnitTypeId.MARINE, at=(2.0 + 0.1 * step, 25.0), alliance=Alliance.ENEMY),
            ]
            for step in range(24)
        ]
        assert _windows_of(observed) == []


class TestWhatAWindowRecords:
    def test_every_buff_worn_during_it(self) -> None:
        observed = _zergling_running(_ZERGLING_STEP, 12)
        observed[3] = [_zergling_at(2.0 + _ZERGLING_STEP * 3, buff_ids=[BuffId.INFESTOR_FUNGAL_GROWTH.value])]
        (window,) = _windows_of(observed)
        assert window.buffs_worn == {BuffId.INFESTOR_FUNGAL_GROWTH}

    def test_whether_the_unit_reads_as_a_hallucination(self) -> None:
        (window,) = _windows_of(_zergling_running(_ZERGLING_STEP, 12, is_hallucination=True))
        assert window.is_hallucination
