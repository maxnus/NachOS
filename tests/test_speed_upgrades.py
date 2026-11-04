"""Upgrade inference's speed upgrades, read off how fast enemy units move: what votes, how the score moves, and when it
assumes and forgets, in made-up games."""

import itertools
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from s2clientprotocol import common_pb2, raw_pb2

from sc2nachos import Api
from sc2nachos.events import AnyUpgradeEvent, TurnStartEvent
from sc2nachos.extensions.upgrade_inference import (
    EnemyUpgradeAssumedEvent,
    EnemyUpgradeForgottenEvent,
    UpgradeInference,
)
from sc2nachos.extensions.upgrade_inference._movement_windows import _CREEP_SPEEDS
from sc2nachos.gamedata import GameData
from sc2nachos.gametime import STEPS_PER_SECOND
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance
from support import ScriptedGame, make_bits, make_game_info, make_observation, make_unit, shipped_game_data

_GAME_DATA = GameData(shipped_game_data())
_MAP_ROWS = ("#" * 128,) * 40
_CREEP_EVERYWHERE = make_bits(*_MAP_ROWS)
_NO_CREEP = make_bits(*_MAP_ROWS, drawn="")
_BLOCK_STEPS = 22
# A block's first step, after every speed upgrade could have finished: Metabolic Boost, the latest, at 2800.
_LATE_ENOUGH_STEP = _BLOCK_STEPS * 200
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
    first_step: int = _LATE_ENOUGH_STEP,
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

    def build_the_extensions(api: Api[Any]) -> UpgradeInference:
        upgrade_inference = UpgradeInference(api, **upgrade_inference_options)
        api.on(TurnStartEvent)(lambda _: upgrades_each_turn.append((api.step, api.enemy.upgrades)))
        started(api)
        return upgrade_inference

    api: Api[Any] = Api(extensions=build_the_extensions)
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
        if api.step == _LATE_ENOUGH_STEP + 2 * _BLOCK_STEPS:
            api.enemy.forget_upgrades(UpgradeId.ZERGLING_SPEED)


class TestWhatVotes:
    def test_a_zergling_at_its_boosted_speed_throughout_a_block_shows_metabolic_boost(self) -> None:
        assert _upgrades_at_block_ends(_unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))) == [_METABOLIC_BOOST]

    def test_one_at_its_speed_without_shows_nothing(self) -> None:
        assert _upgrades_at_block_ends(_unit_moving(_block_at(_ZERGLING_STEP))) == [_NO_UPGRADES]

    def test_one_between_the_two_votes_neither_way(self) -> None:
        between = _block_at((_ZERGLING_STEP + _BOOSTED_ZERGLING_STEP) / 2)
        assert _upgrades_at_block_ends(_unit_moving(between)) == [_NO_UPGRADES]
        assert (
            _upgrades_at_block_ends(_unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), between)) == [_METABOLIC_BOOST] * 2
        )

    def test_a_boosted_one_that_stops_now_and_then_votes_neither_way_though_it_covers_what_one_without_does(
        self,
    ) -> None:
        stopping = [0.0 if step % 3 == 2 or step == 21 else _BOOSTED_ZERGLING_STEP for step in range(_BLOCK_STEPS)]
        assert abs(sum(stopping) - _BLOCK_STEPS * _ZERGLING_STEP) < 0.05 * _BLOCK_STEPS * (
            _BOOSTED_ZERGLING_STEP - _ZERGLING_STEP
        )
        assert (
            _upgrades_at_block_ends(_unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), stopping)) == [_METABOLIC_BOOST] * 2
        )

    def test_on_creep_a_zergling_votes_by_its_speeds_on_creep(self) -> None:
        on_creep = _unit_moving(
            _block_at(_creep_distance_a_step(UnitTypeId.ZERGLING, upgraded=True)),
            _block_at(_creep_distance_a_step(UnitTypeId.ZERGLING, upgraded=False)),
        )
        assert _upgrades_at_block_ends(on_creep, creep=_CREEP_EVERYWHERE) == [_METABOLIC_BOOST, _NO_UPGRADES]

    def test_a_unit_missing_from_an_observation_votes_once_seen_through_a_whole_block(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_BOOSTED_ZERGLING_STEP))
        observed[5] = []
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES, _METABOLIC_BOOST]

    def test_a_unit_on_another_side_than_first_seen_votes_neither_way(self) -> None:
        # As a zergling of this player's under a neural parasite is reported the enemy's.
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        (first_seen,) = observed[0]
        observed[0] = [make_unit(first_seen.tag, UnitTypeId.ZERGLING, at=(first_seen.pos.x, first_seen.pos.y))]
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES]

    def test_a_unit_wearing_a_buff_that_changes_its_speed_votes_neither_way(self) -> None:
        sped_up = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), buff_ids=[BuffId.ACCELERATION_ZONE.value])
        assert _upgrades_at_block_ends(sped_up) == [_NO_UPGRADES]

    def test_no_vote_counts_before_the_upgrade_could_have_finished(self) -> None:
        """Metabolic Boost could finish at step 2800 at the earliest: its research after a spawning pool's build."""
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed, first_step=_BLOCK_STEPS * 127) == [_NO_UPGRADES, _METABOLIC_BOOST]

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


class TestTheScore:
    def test_one_vote_for_among_votes_against_counts_for_the_upgrade(self) -> None:
        boosted = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), tag=7, y=20.0)
        kept_to_a_slower_pace = _unit_moving(_block_at(_ZERGLING_STEP), tag=8, y=24.0)
        assert _upgrades_at_block_ends(_observed_together(boosted, kept_to_a_slower_pace)) == [_METABOLIC_BOOST]

    def test_votes_against_leave_no_debt_below_the_floor(self) -> None:
        observed = _unit_moving(*[_block_at(_ZERGLING_STEP)] * 5, _block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed) == [_NO_UPGRADES] * 5 + [_METABOLIC_BOOST]

    def test_a_block_of_votes_against_forgets_what_one_block_of_votes_for_assumed(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed) == [_METABOLIC_BOOST, _NO_UPGRADES]

    def test_two_blocks_of_votes_for_outweigh_one_against(self) -> None:
        observed = _unit_moving(*[_block_at(_BOOSTED_ZERGLING_STEP)] * 2, _block_at(_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed) == [_METABOLIC_BOOST] * 3

    def test_an_upgrade_the_bot_assumed_first_is_never_forgotten(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP))
        assumed_first = _upgrades_at_block_ends(
            observed, started=lambda api: api.enemy.assume_upgrades(UpgradeId.ZERGLING_SPEED)
        )
        assert assumed_first == [_METABOLIC_BOOST] * 2

    def test_one_the_bot_forgot_comes_back_with_a_block_of_votes_for_it_but_not_with_one_against(self) -> None:
        boosted, slower = _block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP)
        observed = _unit_moving(boosted, boosted, slower, boosted)
        upgrades = _upgrades_at_block_ends(observed, started=_forget_metabolic_boost_on_the_second_blocks_end)
        assert upgrades == [_METABOLIC_BOOST, _METABOLIC_BOOST, _NO_UPGRADES, _METABOLIC_BOOST]

    def test_without_include_speed_upgrades_it_reads_none(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed, include_speed_upgrades=False) == [_NO_UPGRADES]


class TestTheEventsItHandsOut:
    def test_assuming_and_forgetting_each_come_as_an_event_on_the_turn_a_block_ends(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP))
        assert _upgrade_events_handed_out(observed) == [
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _LATE_ENOUGH_STEP + _BLOCK_STEPS),
            (EnemyUpgradeForgottenEvent, UpgradeId.ZERGLING_SPEED, _LATE_ENOUGH_STEP + 2 * _BLOCK_STEPS),
        ]

    def test_one_the_bot_assumed_first_comes_as_neither(self) -> None:
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP))
        handed_out = _upgrade_events_handed_out(
            observed, started=lambda api: api.enemy.assume_upgrades(UpgradeId.ZERGLING_SPEED)
        )
        assert handed_out == []

    def test_the_bots_forgetting_comes_as_nothing_and_assuming_again_comes_again(self) -> None:
        boosted, slower = _block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP)
        observed = _unit_moving(boosted, boosted, slower, boosted)
        handed_out = _upgrade_events_handed_out(observed, started=_forget_metabolic_boost_on_the_second_blocks_end)
        assert handed_out == [
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _LATE_ENOUGH_STEP + _BLOCK_STEPS),
            (EnemyUpgradeAssumedEvent, UpgradeId.ZERGLING_SPEED, _LATE_ENOUGH_STEP + 4 * _BLOCK_STEPS),
        ]

    def test_several_in_one_block_come_in_id_order(self) -> None:
        zergling = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP), tag=7, y=20.0)
        roach = _unit_moving(
            _block_at(_distance_a_step(UnitTypeId.ROACH, upgraded=True)), unit_type=UnitTypeId.ROACH, tag=8, y=24.0
        )
        handed_out = _upgrade_events_handed_out(_observed_together(zergling, roach))
        assert [upgrade for _, upgrade, _ in handed_out] == [UpgradeId.ROACH_SPEED, UpgradeId.ZERGLING_SPEED]


class TestTheThresholds:
    def test_from_0_1_percent_assuming_at_99_9_it_takes_two_blocks_of_votes_for(self) -> None:
        """One block of votes for takes the score from 0.1% to 95.8%, two to all but 100%."""
        observed = _unit_moving(*[_block_at(_BOOSTED_ZERGLING_STEP)] * 2)
        upgrades = _upgrades_at_block_ends(observed, speed_upgrade_floor=0.001, assume_speed_upgrade_at=0.999)
        assert upgrades == [_NO_UPGRADES, _METABOLIC_BOOST]

    def test_a_higher_floor_assumes_on_less(self) -> None:
        """One block of votes for takes the score from 5% to 99.9%, but from 1% to 99.6% only."""
        observed = _unit_moving(_block_at(_BOOSTED_ZERGLING_STEP))
        assert _upgrades_at_block_ends(observed, speed_upgrade_floor=0.05, assume_speed_upgrade_at=0.999) == [
            _METABOLIC_BOOST
        ]
        assert _upgrades_at_block_ends(observed, assume_speed_upgrade_at=0.999) == [_NO_UPGRADES]

    def test_a_higher_threshold_of_forgetting_forgets_on_less(self) -> None:
        """Two blocks of votes for and one against leave the score at 99.8%: forgotten below 99.9%, not below 50%."""
        boosted, slower = _block_at(_BOOSTED_ZERGLING_STEP), _block_at(_ZERGLING_STEP)
        observed = _unit_moving(boosted, boosted, slower)
        upgrades = _upgrades_at_block_ends(observed, assume_speed_upgrade_at=0.9999, forget_speed_upgrade_below=0.999)
        assert upgrades == [_NO_UPGRADES, _METABOLIC_BOOST, _NO_UPGRADES]

    @pytest.mark.parametrize(
        ("floor", "forgotten_below", "assumed_at"),
        [
            (0.0, 0.5, 0.99),
            (-0.01, 0.5, 0.99),
            (0.01, 0.5, 1.0),
            (0.5, 0.5, 0.99),
            (0.01, 0.99, 0.99),
            (0.01, 0.9, 0.8),
        ],
    )
    def test_thresholds_not_rising_from_0_to_1_raise(
        self, floor: float, forgotten_below: float, assumed_at: float
    ) -> None:
        with pytest.raises(ValueError, match="must rise in that order between 0 and 1"):
            UpgradeInference(
                Api(),
                speed_upgrade_floor=floor,
                forget_speed_upgrade_below=forgotten_below,
                assume_speed_upgrade_at=assumed_at,
            )
