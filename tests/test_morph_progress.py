"""How far a morph the game reports no progress for has got: a structure's of this player's, and a cocoon's of either
side, timed from the observation before the first that shows it."""

from contextlib import closing
from typing import Any

import pytest
from s2clientprotocol import data_pb2, debug_pb2, raw_pb2

from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.run import GameProcess, MapFile, MapNotFoundError
from sc2nachos.units import Alliance, NotReportedError, Progress, Unit
from support import RealGame, make_observation, make_tables, make_tracker, make_unit

# At the build times of the game's tables, an overseer's no whole number of steps, and a lurker's 553.33, which NachOS
# corrects to the 403 it takes (in game).
_TABLES = make_tables(
    *(
        data_pb2.UnitTypeData(unit_id=unit_type, build_time=build_time)
        for unit_type, build_time in (
            (UnitTypeId.COMMAND_CENTER, 1600.0),
            (UnitTypeId.ORBITAL_COMMAND, 560.0),
            (UnitTypeId.PLANETARY_FORTRESS, 800.0),
            (UnitTypeId.HATCHERY, 1600.0),
            (UnitTypeId.LAIR, 1280.0),
            (UnitTypeId.HIVE, 1600.0),
            (UnitTypeId.ZERGLING, 384.0),
            (UnitTypeId.BANELING_COCOON, 0.0),
            (UnitTypeId.BANELING, 320.0),
            (UnitTypeId.OVERLORD, 400.0),
            (UnitTypeId.OVERLORD_COCOON, 0.0),
            (UnitTypeId.OVERSEER, 266.6796875),
            (UnitTypeId.HYDRALISK, 528.0),
            (UnitTypeId.LURKER_EGG, 0.0),
            (UnitTypeId.LURKER, 553.3333),
        )
    )
)
_ORBITAL = raw_pb2.UnitOrder(ability_id=AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND)
_FORTRESS = raw_pb2.UnitOrder(ability_id=AbilityId.COMMAND_CENTER_MORPH_PLANETARY_FORTRESS)
_TRAIN_SCV = raw_pb2.UnitOrder(ability_id=AbilityId.COMMAND_CENTER_TRAIN_SCV, progress=0.5)
_HIVE = raw_pb2.UnitOrder(ability_id=AbilityId.LAIR_MORPH_HIVE)
_BANELING = raw_pb2.UnitOrder(ability_id=AbilityId.ZERGLING_MORPH_BANELING)
_ENEMY = Alliance.ENEMY


class _Game:
    """A game whose observations are written here, one at a time."""

    def __init__(self) -> None:
        self.tracker = make_tracker(_TABLES)

    def observe(self, step: int, *units: raw_pb2.Unit, dead: tuple[int, ...] = ()) -> None:
        self.tracker.update(make_observation(step, units=units, dead=dead).observation.raw_data, step)

    def unit(self, tag: int) -> Unit[Any]:
        return self.tracker.unit_tracker.by_tag(tag)

    def progress(self, tag: int) -> Progress[UnitTypeId] | None:
        return self.unit(tag).morph_progress


def _command_center(*orders: raw_pb2.UnitOrder, alliance: Alliance = Alliance.OWN) -> raw_pb2.Unit:
    """A command center under tag 1 carrying out `orders`."""
    return make_unit(1, UnitTypeId.COMMAND_CENTER, orders=orders, build_progress=1.0, alliance=alliance)


def _steps(progress: Progress[UnitTypeId] | None) -> tuple[UnitTypeId, float, float, float]:
    """What `progress` makes, its build time, and the steps it has done and left."""
    assert progress is not None
    steps_done = round(progress.fraction * progress.total_time.steps)
    return progress.product, progress.total_time.steps, steps_done, progress.time_left.steps


class TestAStructuresMorph:
    def test_it_began_at_the_observation_before_the_first_that_shows_it(self) -> None:
        """An order sent after the observation at step 0 shows 8 steps done at step 8 (in game)."""
        game = _Game()
        game.observe(0, _command_center())
        game.observe(8, _command_center(_ORBITAL))
        assert _steps(game.progress(1)) == (UnitTypeId.ORBITAL_COMMAND, 560, 8, 552)
        game.observe(16, _command_center(_ORBITAL))
        assert _steps(game.progress(1)) == (UnitTypeId.ORBITAL_COMMAND, 560, 16, 544)

    def test_one_step_a_turn_counts_the_one_step_done(self) -> None:
        """The first observation after the order shows one step done, as the orbital's 559/560 a step before it is done
        shows (in game)."""
        game = _Game()
        game.observe(1000, _command_center())
        game.observe(1001, _command_center(_ORBITAL))
        assert _steps(game.progress(1)) == (UnitTypeId.ORBITAL_COMMAND, 560, 1, 559)
        game.observe(1558, _command_center(_ORBITAL))
        assert _steps(game.progress(1)) == (UnitTypeId.ORBITAL_COMMAND, 560, 558, 2)

    def test_a_command_center_not_morphing_is_at_none(self) -> None:
        game = _Game()
        game.observe(0, _command_center(_TRAIN_SCV))
        assert game.progress(1) is None

    def test_a_cancelled_morph_is_gone_and_one_ordered_again_begins_afresh(self) -> None:
        game = _Game()
        game.observe(0, _command_center())
        game.observe(8, _command_center(_ORBITAL))
        game.observe(16, _command_center())
        assert game.progress(1) is None
        game.observe(24, _command_center(_ORBITAL))
        assert _steps(game.progress(1)) == (UnitTypeId.ORBITAL_COMMAND, 560, 8, 552)

    def test_another_morph_in_its_place_begins_afresh(self) -> None:
        game = _Game()
        game.observe(0, _command_center())
        game.observe(8, _command_center(_ORBITAL))
        game.observe(16, _command_center(_FORTRESS))
        assert _steps(game.progress(1)) == (UnitTypeId.PLANETARY_FORTRESS, 800, 8, 792)

    def test_a_morph_under_way_in_the_first_observation_was_not_seen_to_begin(self) -> None:
        game = _Game()
        game.observe(0, _command_center(_ORBITAL))
        with pytest.raises(NotReportedError, match="not seen to begin"):
            _ = game.progress(1)

    def test_a_structure_that_became_one_that_morphs_is_watched(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.HATCHERY))
        game.observe(8, make_unit(1, UnitTypeId.LAIR))
        game.observe(16, make_unit(1, UnitTypeId.LAIR, orders=[_HIVE]))
        assert _steps(game.progress(1)) == (UnitTypeId.HIVE, 1600, 8, 1592)

    def test_it_ends_with_the_type_it_became(self) -> None:
        """For a step the orbital reads unfinished; its build progress says how far it has got (in game)."""
        game = _Game()
        game.observe(0, _command_center())
        game.observe(8, _command_center(_ORBITAL))
        game.observe(559, make_unit(1, UnitTypeId.ORBITAL_COMMAND, build_progress=559 / 560))
        assert game.progress(1) is None

    def test_an_enemys_is_not_reported(self) -> None:
        """The game shows an enemy's orders to nobody, and its type changes only once the morph is done (in game)."""
        game = _Game()
        game.observe(0, _command_center(alliance=_ENEMY))
        with pytest.raises(NotReportedError, match="not this player's"):
            _ = game.progress(1)


class TestACocoon:
    def test_one_of_this_players_began_at_the_observation_before_its_first(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.ZERGLING))
        game.observe(8, make_unit(1, UnitTypeId.BANELING_COCOON, orders=[_BANELING]))
        assert _steps(game.progress(1)) == (UnitTypeId.BANELING, 320, 8, 312)

    def test_an_enemys_seen_turning_began_at_the_observation_before(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.OVERLORD, alliance=_ENEMY))
        game.observe(4, make_unit(1, UnitTypeId.OVERLORD_COCOON, alliance=_ENEMY))
        assert _steps(game.progress(1)) == (UnitTypeId.OVERSEER, 267, 4, 263)

    def test_its_fraction_is_the_steps_done_over_the_unrounded_build_time(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.OVERLORD))
        game.observe(100, make_unit(1, UnitTypeId.OVERLORD_COCOON))
        progress = game.progress(1)
        assert progress is not None
        assert progress.fraction == 100 / 266.6796875

    def test_a_lurker_egg_takes_the_lurkers_corrected_build_time(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.HYDRALISK))
        game.observe(8, make_unit(1, UnitTypeId.LURKER_EGG))
        assert _steps(game.progress(1)) == (UnitTypeId.LURKER, 403, 8, 395)

    def test_one_first_seen_a_cocoon_was_not_seen_to_begin(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.BANELING_COCOON, alliance=_ENEMY))
        with pytest.raises(NotReportedError, match="first seen a cocoon"):
            _ = game.progress(1)

    def test_one_turned_out_of_sight_was_not_seen_to_begin(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.ZERGLING, alliance=_ENEMY))
        game.observe(4)
        game.observe(8, make_unit(1, UnitTypeId.BANELING_COCOON, alliance=_ENEMY))
        with pytest.raises(NotReportedError, match="not seen to begin"):
            _ = game.progress(1)

    def test_one_out_of_sight_counts_on_and_stops_at_done(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.ZERGLING, alliance=_ENEMY))
        game.observe(4, make_unit(1, UnitTypeId.BANELING_COCOON, alliance=_ENEMY))
        game.observe(100)
        assert _steps(game.progress(1)) == (UnitTypeId.BANELING, 320, 100, 220)
        game.observe(400)
        progress = game.progress(1)
        assert progress is not None
        assert (progress.fraction, progress.time_left.steps) == (1.0, 0)

    def test_one_hatched_or_given_up_is_no_longer_morphing(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.ZERGLING), make_unit(2, UnitTypeId.ZERGLING))
        game.observe(8, make_unit(1, UnitTypeId.BANELING_COCOON), make_unit(2, UnitTypeId.BANELING_COCOON))
        game.observe(328, make_unit(1, UnitTypeId.BANELING), make_unit(2, UnitTypeId.ZERGLING))
        assert (game.progress(1), game.progress(2)) == (None, None)

    def test_a_dead_ones_morph_is_forgotten(self) -> None:
        game = _Game()
        game.observe(0, make_unit(1, UnitTypeId.ZERGLING))
        game.observe(8, make_unit(1, UnitTypeId.BANELING_COCOON))
        game.observe(16, dead=(1,))
        assert game.progress(1) is None


@pytest.mark.integration
@pytest.mark.parametrize("steps_per_turn", [1, 7])
def test_in_a_real_game_a_morphs_time_left_runs_out_as_it_is_done(steps_per_turn: int) -> None:
    """Run with `pytest -m integration`. Morphs a command center into an orbital command and a zergling into a
    baneling, observing every step or every 7: the orbital is done its 560 steps after the step its order was sent
    after, and the baneling, a step short of its 320, is listed within a step of them (in game)."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    with (
        GameProcess.launch(window=(640, 480)) as process,
        closing(Client(WebSocketTransport.connect(process.url))) as client,
    ):
        client.create_game(game_map.path, [Participant(), Computer(Race.ZERG, Difficulty.VERY_EASY)])
        game = RealGame(client, client.join_game(Race.TERRAN))
        command_center = game.turn(1).own.of_type(UnitTypeId.COMMAND_CENTER)[0]
        home = command_center.position
        middle = game.map.playable_area.center
        # The orbital needs a barracks, the baneling a baneling nest; `free` pays for both morphs.
        game.debug(
            debug_pb2.DebugCommand(game_state=debug_pb2.DebugGameState.free),
            game.create(UnitTypeId.BARRACKS, game.open_ground(home.towards(middle, 9), size=3)),
            game.create(UnitTypeId.BANELING_NEST, game.open_ground(home.towards(middle, 14), size=3)),
            game.create(UnitTypeId.ZERGLING, game.open_ground(home.towards(middle, 18))),
        )
        game.turn(8)
        zergling = game.newest(UnitTypeId.ZERGLING)
        ordered_after = game.step
        game.order(AbilityId.COMMAND_CENTER_MORPH_ORBITAL_COMMAND, command_center)
        game.order(AbilityId.ZERGLING_MORPH_BANELING, zergling)
        orbital_reads: list[tuple[int, int | None, bool]] = []
        baneling_reads: list[tuple[int, int | None, bool]] = []
        while game.step < ordered_after + 580:
            game.turn(steps_per_turn)
            orbital_reads.append(_read_morph(game.step, command_center, UnitTypeId.ORBITAL_COMMAND))
            baneling_reads.append(_read_morph(game.step, zergling, UnitTypeId.BANELING))
            if game.step == ordered_after + 7 * steps_per_turn:
                morph = command_center.morph_progress
                (orbital,) = game.state.production.of_types({UnitTypeId.ORBITAL_COMMAND})
                assert morph is not None
                assert orbital.time_left == morph.time_left
        client.leave_game()
        client.quit()

    _assert_time_left_runs_out_as_it_is_done(orbital_reads, done_at=ordered_after + 560)
    _assert_time_left_runs_out_as_it_is_done(baneling_reads, done_at=ordered_after + 320)


def _read_morph(step: int, unit: Unit[Any], becoming: UnitTypeId) -> tuple[int, int | None, bool]:
    """At `step`, the steps `unit`'s morph has left, `None` once it is no longer morphing, and whether it is done:
    a finished unit of `becoming`."""
    progress = unit.morph_progress
    done = unit.type_id is becoming and unit.is_complete
    return step, None if progress is None else progress.time_left.nearest_step, done


def _assert_time_left_runs_out_as_it_is_done(reads: list[tuple[int, int | None, bool]], *, done_at: int) -> None:
    """Each read of `reads` while morphing has the steps left until `done_at`; every read from `done_at` on is done,
    and every read before the step ahead of it still morphing."""
    assert reads, "nothing was read"
    for step, steps_left, done in reads:
        if steps_left is not None:
            assert step + steps_left == done_at, f"at step {step}, {steps_left} left"
        if step >= done_at:
            assert done, f"not done at step {step}"
        elif step < done_at - 1:
            assert steps_left is not None, f"not morphing at step {step}"
