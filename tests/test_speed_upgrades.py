"""Upgrade inference's speed upgrades, read off how fast enemy units move: what shows one, over what windows and within
what tolerance, and when it is assumed, in made-up games."""

import itertools
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from s2clientprotocol import common_pb2, raw_pb2

from sc2nachos import Api
from sc2nachos.events import AnyUpgradeEvent, GameStartEvent, TurnStartEvent
from sc2nachos.extensions.upgrade_inference import EnemyUpgradeAssumedEvent, UpgradeInference
from sc2nachos.extensions.upgrade_inference._movement_windows import _CREEP_SPEEDS
from sc2nachos.gamedata import GameData
from sc2nachos.gametime import STEPS_PER_SECOND, GameTime
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance
from support import ScriptedGame, make_bits, make_game_info, make_observation, make_unit, shipped_game_data

_GAME_DATA = GameData(shipped_game_data())
_MAP_ROWS = ("#" * 128,) * 40
_CREEP_EVERYWHERE = make_bits(*_MAP_ROWS)
_NO_CREEP = make_bits(*_MAP_ROWS, drawn="")
_BLOCK_STEPS = 22
# A block's first step, some minutes into a game.
_FIRST_STEP = _BLOCK_STEPS * 200
_METABOLIC_BOOST = frozenset({UpgradeId.ZERGLING_SPEED})
_NO_UPGRADES: frozenset[UpgradeId] = frozenset()


def _distance_a_step(unit_type: UnitTypeId, *, upgraded: bool) -> float:
    row = _GAME_DATA.units[unit_type]
    speed_upgrades = {upgrade for upgrade, change in row.upgrades.items() if change.speed} if upgraded else set()
    return row.with_upgrades(speed_upgrades).speed / STEPS_PER_SECOND


def _creep_distance_a_step(unit_type: UnitTypeId, *, upgraded: bool) -> float:
    return _CREEP_SPEEDS[unit_type][upgraded] / STEPS_PER_SECOND


_ZERGLING_STEP = _distance_a_step(UnitTypeId.ZERGLING, upgraded=False)
_BOOSTED_ZERGLING_STEP = _distance_a_step(UnitTypeId.ZERGLING, upgraded=True)


def _block_at(distance_a_step: float) -> list[float]:
    return [distance_a_step] * _BLOCK_STEPS


def _unit_moving(
    *blocks: Sequence[float],
    unit_type: UnitTypeId = UnitTypeId.ZERGLING,
    tag: int = 7,
    y: float = 20.0,
    steps_apart: int = 1,
    **fields: Any,
) -> list[list[raw_pb2.Unit]]:
    """An enemy unit moving along x, the distance `blocks` give for each step of each block of 22, as observations
    `steps_apart` steps apart list it, from the first block's first step to the last block's end."""
    distances_a_step = [distance for block in blocks for distance in block]
    observed: list[list[raw_pb2.Unit]] = []
    for step in range(0, len(distances_a_step) + 1, steps_apart):
        x = 2.0 + sum(distances_a_step[:step])
        observed.append([make_unit(tag, unit_type, at=(x, y), alliance=Alliance.ENEMY, **fields)])
    return observed


def _observed_together(*observed: list[list[raw_pb2.Unit]]) -> list[list[raw_pb2.Unit]]:
    """The units of each of `observed` at each observation, in one list an observation."""
    return [[unit for units in at_one_step for unit in units] for at_one_step in zip(*observed, strict=True)]


def _upgrades_at_block_ends(
    observed: Sequence[Sequence[raw_pb2.Unit]],
    *,
    first_step: int = _FIRST_STEP,
    steps_apart: int = 1,
    creep: common_pb2.ImageData = _NO_CREEP,
    started: Callable[[Api[Any]], object] = lambda _: None,
    **upgrade_inference_options: Any,
) -> list[frozenset[UpgradeId]]:
    """The enemy's upgrades as the bot's handlers see them on the turn each block of 22 steps ends, the first
    observation in it, in a game of a turn on each of `observed`, `steps_apart` steps apart from `first_step`, with
    creep where `creep` has it, and upgrade inference built with `upgrade_inference_options`. `started` runs as the
    game starts, after upgrade inference is built."""
    observations = [
        make_observation(first_step + index * steps_apart, units=units, creep=creep)
        for index, units in enumerate([*observed, ()])
    ]
    upgrades_each_turn: list[tuple[int, frozenset[UpgradeId]]] = []

    api: Api[Any] = Api(extensions=lambda api: UpgradeInference(api, **upgrade_inference_options))
    api.on(TurnStartEvent)(lambda _: upgrades_each_turn.append((api.step, api.enemy.upgrades)))
    api.on(GameStartEvent)(lambda _: started(api))
    api.play(ScriptedGame(observations, game_info=make_game_info(*_MAP_ROWS), data=shipped_game_data()).joined())
    return [
        upgrades
        for (previous_step, _), (step, upgrades) in itertools.pairwise(upgrades_each_turn)
        if step // _BLOCK_STEPS != previous_step // _BLOCK_STEPS
    ]


def _upgrade_events_handed_out(
    observed: Sequence[Sequence[raw_pb2.Unit]],
    *,
    started: Callable[[Api[Any]], object] = lambda _: None,
    **upgrade_inference_options: Any,
) -> list[tuple[type[AnyUpgradeEvent], UpgradeId, float]]:
    """Each `AnyUpgradeEvent` a handler of it is handed, by its type, its upgrade and its step, in the game
    `_upgrades_at_block_ends` plays on `observed`, with `started` and `upgrade_inference_options`."""
    handed_out: list[tuple[type[AnyUpgradeEvent], UpgradeId, float]] = []

    def watch(api: Api[Any]) -> None:
        api.on(AnyUpgradeEvent)(lambda event: handed_out.append((type(event), event.upgrade, event.time.steps)))
        started(api)

    _upgrades_at_block_ends(observed, started=watch, **upgrade_inference_options)
    return handed_out


def _forget_metabolic_boost_on_the_second_blocks_end(api: Api[Any]) -> None:
    @api.on(TurnStartEvent)
    def forget(_: object) -> None:
        if api.step == _FIRST_STEP + 2 * _BLOCK_STEPS:
            api.enemy.forget_upgrades(UpgradeId.ZERGLING_SPEED)


class TestWhatVotes:
    def test_a_zergling_at_its_boosted_speed_throughout_a_block_shows_metabolic_boost(self) -> None:
        assert _upgrades_at_block_ends(_unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))) == [_METABOLIC_BOOST]

    def test_one_at_its_speed_without_shows_nothing(self) -> None:
        assert _upgrades_at_block_ends(_unit_moving(_block_at(_ZERGLING_STEP))) == [_NO_UPGRADES]

    def test_one_between_the_two_shows_nothing(self) -> None:
        between = _block_at((_ZERGLING_STEP + _BOOSTED_ZERGLING_STEP) / 2)
        assert _upgrades_at_block_ends(_unit_moving(between)) == [_NO_UPGRADES]

    def test_a_boosted_one_that_stops_now_and_then_shows_nothing(self) -> None:
        stopping = [0.0 if step % 3 == 2 or step == 21 else _BOOSTED_ZERGLING_STEP for step in range(_BLOCK_STEPS)]
        assert _upgrades_at_block_ends(_unit_moving(stopping)) == [_NO_UPGRADES]

    def test_on_creep_a_zergling_is_measured_by_its_speeds_on_creep(self) -> None:
        boosted_on_creep = _unit_moving(_block_at(_creep_distance_a_step(UnitTypeId.ZERGLING, upgraded=True)))
        plain_on_creep = _unit_moving(_block_at(_creep_distance_a_step(UnitTypeId.ZERGLING, upgraded=False)))
        assert _upgrades_at_block_ends(boosted_on_creep, creep=_CREEP_EVERYWHERE) == [_METABOLIC_BOOST]
        assert _upgrades_at_block_ends(plain_on_creep, creep=_CREEP_EVERYWHERE) == [_NO_UPGRADES]
        # Off creep, the boosted speed on creep is too fast for either speed.
        assert _upgrades_at_block_ends(boosted_on_creep) == [_NO_UPGRADES]

    def test_a_unit_missing_from_an_observation_votes_once_seen_through_a_whole_block(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_BOOSTED_ZERGLING_STEP))
        observed[5] = []
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES, _METABOLIC_BOOST]

    def test_a_unit_on_another_side_than_first_seen_shows_nothing(self) -> None:
        # As a zergling of this player's under a neural parasite is reported the enemy's.
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        (first_seen,) = observed[0]
        observed[0] = [make_unit(first_seen.tag, UnitTypeId.ZERGLING, at=(first_seen.pos.x, first_seen.pos.y))]
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES]

    def test_a_unit_wearing_a_buff_that_changes_its_speed_shows_nothing(self) -> None:
        sped_up = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), buff_ids=[BuffId.ACCELERATION_ZONE.value])
        assert _upgrades_at_block_ends(sped_up) == [_NO_UPGRADES]

    def test_a_block_from_the_games_first_step_shows_it(self) -> None:
        """Though no enemy can have Metabolic Boost before step 2800, its research after a spawning pool's build."""
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed, first_step=0) == [_METABOLIC_BOOST]

    def test_observed_every_other_step_a_zergling_at_its_boosted_speed_shows_it(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), steps_apart=2)
        assert _upgrades_at_block_ends(observed, steps_apart=2) == [_METABOLIC_BOOST]

    def test_observed_every_16_steps_as_the_corpus_is_blocks_end_at_the_first_observation_in_the_next(self) -> None:
        """Blocks of 22 steps from step 4400 end at the observations of steps 4432, 4448, 4480 and 4496, windows of
        32 and 16 steps, and the fifth at the one the game ends on, which gets no turn."""
        observed = _unit_moving(*[_block_at(_BOOSTED_ZERGLING_STEP)] * 5, steps_apart=16)
        assert _upgrades_at_block_ends(observed, steps_apart=16) == [_METABOLIC_BOOST] * 4

    def test_a_hallucination_votes_as_the_unit_it_copies(self) -> None:
        hallucinated = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), is_hallucination=True)
        assert _upgrades_at_block_ends(hallucinated) == [_METABOLIC_BOOST]

    def test_an_ultralisk_votes_off_creep_but_not_on_it_where_anabolic_synthesis_adds_3_percent(self) -> None:
        off_creep = _unit_moving(
            _block_at(_distance_a_step(UnitTypeId.ULTRALISK, upgraded=True)), unit_type=UnitTypeId.ULTRALISK
        )
        on_creep = _unit_moving(
            _block_at(_creep_distance_a_step(UnitTypeId.ULTRALISK, upgraded=True)), unit_type=UnitTypeId.ULTRALISK
        )
        assert _upgrades_at_block_ends(off_creep) == [{UpgradeId.ULTRALISK_SPEED}]
        assert _upgrades_at_block_ends(on_creep, creep=_CREEP_EVERYWHERE) == [_NO_UPGRADES]

    def test_an_overlord_at_its_speed_with_pneumatized_carapace_shows_it(self) -> None:
        overlord = _unit_moving(
            _block_at(_distance_a_step(UnitTypeId.OVERLORD, upgraded=True)), unit_type=UnitTypeId.OVERLORD
        )
        assert _upgrades_at_block_ends(overlord, creep=_CREEP_EVERYWHERE) == [{UpgradeId.OVERLORD_SPEED}]


class TestAssuming:
    def test_one_unit_showing_it_among_units_at_their_speed_without_assumes_it(self) -> None:
        boosted = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), tag=7, y=20.0)
        kept_to_a_slower_pace = _unit_moving(_block_at(_ZERGLING_STEP), tag=8, y=24.0)
        assert _upgrades_at_block_ends(_observed_together(boosted, kept_to_a_slower_pace)) == [_METABOLIC_BOOST]

    def test_one_block_showing_it_assumes_it_after_any_number_at_the_speed_without(self) -> None:
        observed = _unit_moving(*[_block_at(_ZERGLING_STEP)] * 5, _block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES] * 5 + [_METABOLIC_BOOST]

    def test_what_it_assumed_it_never_forgets(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), *[_block_at(_ZERGLING_STEP)] * 3)
        assert _upgrades_at_block_ends(observed) == [_METABOLIC_BOOST] * 4

    def test_one_the_bot_forgot_comes_back_with_a_block_showing_it(self) -> None:
        boosted, slower = _block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP)
        observed = _unit_moving(boosted, boosted, slower, boosted)
        upgrades = _upgrades_at_block_ends(observed, started=_forget_metabolic_boost_on_the_second_blocks_end)
        assert upgrades == [_METABOLIC_BOOST, _METABOLIC_BOOST, _NO_UPGRADES, _METABOLIC_BOOST]

    def test_without_include_speed_upgrades_it_reads_none(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed, include_speed_upgrades=False) == [_NO_UPGRADES]


class TestTheEventsItHandsOut:
    def test_assuming_comes_as_an_event_on_the_turn_its_block_ends(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrade_events_handed_out(observed) == [
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _FIRST_STEP + _BLOCK_STEPS)
        ]

    def test_one_the_bot_assumed_first_comes_as_no_event(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        handed_out = _upgrade_events_handed_out(
            observed, started=lambda api: api.enemy.assume_upgrades(UpgradeId.ZERGLING_SPEED)
        )
        assert handed_out == []

    def test_one_the_bot_forgot_comes_again_once_shown_again(self) -> None:
        boosted, slower = _block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP)
        observed = _unit_moving(boosted, boosted, slower, boosted)
        handed_out = _upgrade_events_handed_out(observed, started=_forget_metabolic_boost_on_the_second_blocks_end)
        assert handed_out == [
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _FIRST_STEP + _BLOCK_STEPS),
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _FIRST_STEP + 4 * _BLOCK_STEPS),
        ]

    def test_several_in_one_block_come_in_id_order(self) -> None:
        zergling = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), tag=7, y=20.0)
        roach = _unit_moving(
            _block_at(_distance_a_step(UnitTypeId.ROACH, upgraded=True)), unit_type=UnitTypeId.ROACH, tag=8, y=24.0
        )
        handed_out = _upgrade_events_handed_out(_observed_together(zergling, roach))
        assert [upgrade for _, upgrade, _ in handed_out] == [UpgradeId.ROACH_SPEED, UpgradeId.ZERGLING_SPEED]


class TestTheSpeedArguments:
    def test_a_shorter_window_shows_an_upgrade_a_unit_moved_at_for_part_of_a_block_of_22(self) -> None:
        boosted_then_still = _unit_moving([_BOOSTED_ZERGLING_STEP] * 11 + [0.0] * 11)
        assert _upgrades_at_block_ends(boosted_then_still) == [_NO_UPGRADES]
        assert _upgrades_at_block_ends(boosted_then_still, speed_window=GameTime(steps=11)) == [_METABOLIC_BOOST]

    def test_a_window_not_of_whole_steps_begins_a_block_where_its_quotient_rounded_down_is_new(self) -> None:
        """Blocks of 11.5 steps begin at steps 4405, 4416 and 4428: the window from 4405 to 4416 is boosted throughout,
        though no window of blocks of 11 steps, which begin at 4400, 4411 and 4422, is."""
        boosted_from_4404 = _unit_moving([0.0] * 4 + [_BOOSTED_ZERGLING_STEP] * 12 + [0.0] * 6)
        assert _upgrades_at_block_ends(boosted_from_4404, speed_window=GameTime(steps=11.5)) == [_METABOLIC_BOOST]
        assert _upgrades_at_block_ends(boosted_from_4404, speed_window=GameTime(steps=11)) == [_NO_UPGRADES]

    @pytest.mark.parametrize("steps", [0, -22])
    def test_a_window_not_longer_than_0_raises(self, steps: float) -> None:
        with pytest.raises(ValueError, match="must be longer than 0"):
            UpgradeInference(Api(), speed_window=GameTime(steps=steps))

    def test_a_window_not_a_game_time_raises(self) -> None:
        with pytest.raises(TypeError, match="is a GameTime, not 22"):
            UpgradeInference(Api(), speed_window=22)  # pyright: ignore[reportArgumentType]

    def test_a_wider_tolerance_shows_an_upgrade_a_unit_moved_a_tenth_of_the_gap_short_of(self) -> None:
        short_of_boosted = _block_at(_ZERGLING_STEP + 0.9 * (_BOOSTED_ZERGLING_STEP - _ZERGLING_STEP))
        assert _upgrades_at_block_ends(_unit_moving(short_of_boosted)) == [_NO_UPGRADES]
        assert _upgrades_at_block_ends(_unit_moving(short_of_boosted), speed_tolerance=0.15) == [_METABOLIC_BOOST]

    @pytest.mark.parametrize("tolerance", [0.0, -0.05, 0.5, 1.0])
    def test_a_tolerance_not_above_0_and_below_0_5_raises(self, tolerance: float) -> None:
        with pytest.raises(ValueError, match="must lie above 0 and below 0.5"):
            UpgradeInference(Api(), speed_tolerance=tolerance)
