"""The recorded games everything above the protocol is tested against, and what they have to hold."""

import contextlib
from functools import cached_property
from pathlib import Path
from typing import Any

import pytest
from s2clientprotocol import sc2api_pb2

from sc2nachos import Api
from sc2nachos._enum import ReadableIntEnum
from sc2nachos.enemy import Enemy
from sc2nachos.events import (
    EnemyUnitDamagedEvent,
    EnemyUnitEnergyLostEvent,
    EnemyUnitEnteredSightEvent,
    EnemyUnitFirstSeenEvent,
    EnemyUnitLeftSightEvent,
    Event,
    OwnUnitCreatedEvent,
    OwnUnitDamagedEvent,
    OwnUnitEnergyLostEvent,
    UnitDiedEvent,
    UnitFoundDeadEvent,
)
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.ids import AbilityId, BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.match import Computer, Participant, Race, Result
from sc2nachos.protocol import Client, Recording, ReplayTransport
from sc2nachos.state._state import _State
from sc2nachos.units import NotReportedError, OwnUnit, Unit
from sc2nachos.units._tracker import _UnitTracker
from support import HAPPENINGS

# Recorded by `tools/record_corpus.py`, which says what each game is.
CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

_CURATED: tuple[type[ReadableIntEnum], ...] = (UnitTypeId, AbilityId, UpgradeId, BuffId, EffectId)


def _observations(recording: Recording) -> list[sc2api_pb2.ResponseObservation]:
    """Every observation in `recording`, in the order the game made them."""
    return [exchange.response.observation for exchange in recording if exchange.response.HasField("observation")]


def test_there_is_a_corpus() -> None:
    assert CORPUS, "tests/corpus holds no recordings, so nothing below ran"


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_a_recorded_game_replays_to_its_end_asking_what_it_asked(path: Path) -> None:
    """A change to what the library asks a game, or in what order, shows up here as a question unanswered."""
    recording = Recording(path)
    last = _observations(recording)[-1]
    client = Client(ReplayTransport(recording))
    # A recording answers each kind of request in turn and never reads what was asked, so the setup is asked again
    # without its details.
    client.create_game("recorded", [Participant(), Computer()])
    player = client.join_game(Race.RANDOM)

    api = Api()
    result = api.play(client)

    assert api.step == last.observation.game_loop
    assert result is {entry.player_id: Result(entry.result) for entry in last.player_result}[player]
    client.leave_game()
    client.quit()


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_every_id_a_game_reported_is_curated(path: Path) -> None:
    """A curated enum raises on an id it leaves out, so leaving out one a real game reports is a crash."""
    reported: dict[type[ReadableIntEnum], set[int]] = {enum: set() for enum in _CURATED}
    for observation in _observations(Recording(path)):
        raw = observation.observation.raw_data
        for unit in raw.units:
            reported[UnitTypeId].add(unit.unit_type)
            reported[UnitTypeId].update(passenger.unit_type for passenger in unit.passengers)
            reported[BuffId].update(unit.buff_ids)
            reported[AbilityId].update(order.ability_id for order in unit.orders)
        reported[UpgradeId].update(raw.player.upgrade_ids)
        reported[EffectId].update(effect.effect_id for effect in raw.effects)

    known = {enum: {int(member) for member in enum} for enum in _CURATED}
    missing = {enum.__name__: sorted(ids - known[enum]) for enum, ids in reported.items()}
    assert not any(missing.values()), f"{path.stem} reported ids with no curated member: {missing}"


def _tables(recording: Recording) -> GameData:
    """The tables `recording` was played by."""
    return GameData(next(exchange.response.data for exchange in recording if exchange.response.HasField("data")))


# Every read a unit has, which a unit in a real game answers or refuses as never shown in sight, and nothing else.
_READS = {
    cls: sorted(name for base in cls.__mro__ for name, member in vars(base).items() if isinstance(member, property))
    for cls in (Unit, OwnUnit)
}
# The reads that name other units, whose tags must all be ones the game reported a unit under.
_NAMING = ("orders", "rally_targets", "passengers", "add_on", "engaged_target", "construction", "builder")


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_the_units_are_every_tagged_unit_the_game_reported_each_one_object_under_one_id(path: Path) -> None:
    recording = Recording(path)
    tracker = _UnitTracker(_tables(recording), Enemy())
    objects: dict[int, Unit[Any]] = {}
    for index, observation in enumerate(_observations(recording)):
        step = observation.observation.game_loop
        tracker.update(observation.observation.raw_data, step)
        units = tracker.present_units
        assert [unit.tag for unit in units] == [unit.tag for unit in observation.observation.raw_data.units if unit.tag]
        assert len({id(unit) for unit in units}) == len(units), "two tags of one observation are one unit"
        for unit in units:
            assert objects.setdefault(unit.id, unit) is unit
            assert not unit.is_stale
        for unit in units.own:
            for name in _NAMING:
                getattr(unit, name)
        if index % 50 == 0:
            for unit in tracker.known_units:
                for name in _READS[type(unit)]:
                    with contextlib.suppress(NotReportedError):
                        getattr(unit, name)


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_what_happened_holds_together_over_a_whole_game(path: Path) -> None:
    """Every unit of this player's is created once and every enemy unit first seen once, the dead are dead, a unit
    enters and leaves sight in turn, and damage is always some."""
    recording = Recording(path)
    client = Client(ReplayTransport(recording))
    client.create_game("recorded", [Participant(), Computer()])
    client.join_game(Race.RANDOM)
    api = Api()
    seen: list[Event] = []
    for event_type in HAPPENINGS:
        api.event.on(event_type)(lambda event: seen.append(event))
    api.play(client)

    tracker = api._current_game().unit_tracker
    # The last observation gets no turn, so what it first saw is never reported.
    unreported = {
        unit.id for unit in (*tracker.last_changes.own_units_created, *tracker.last_changes.enemy_units_first_seen)
    }
    ever = [unit_id for unit_id in tracker._units_by_id if unit_id not in unreported]
    created = [event.unit for event in seen if isinstance(event, OwnUnitCreatedEvent)]
    first_seen = [event.unit for event in seen if isinstance(event, EnemyUnitFirstSeenEvent)]
    assert sorted(unit.id for unit in created) == [unit_id for unit_id in sorted(ever) if unit_id // 100_000 == 1]
    assert sorted(unit.id for unit in first_seen) == [unit_id for unit_id in sorted(ever) if unit_id // 100_000 == 4]
    assert all(event.unit.is_dead for event in seen if isinstance(event, UnitDiedEvent | UnitFoundDeadEvent))
    in_sight: dict[int, bool] = {}
    for event in seen:
        if isinstance(event, EnemyUnitEnteredSightEvent | EnemyUnitLeftSightEvent):
            entering = isinstance(event, EnemyUnitEnteredSightEvent)
            assert in_sight.get(event.unit.id, False) is not entering, f"{event} twice in a row"
            in_sight[event.unit.id] = entering
    assert all(event.damage > 0 for event in seen if isinstance(event, OwnUnitDamagedEvent | EnemyUnitDamagedEvent))
    drained = [event for event in seen if isinstance(event, OwnUnitEnergyLostEvent | EnemyUnitEnergyLostEvent)]
    assert all(event.energy_lost > 0 for event in drained)
    assert in_sight, "no enemy unit ever came into sight"
    client.leave_game()
    client.quit()


# Every read of an observation beyond its units.
_STATE_READS = sorted(
    name
    for name, member in vars(_State).items()
    if isinstance(member, property | cached_property) and not name.startswith("_")
)


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
def test_every_observation_answers_every_read_beyond_its_units(path: Path) -> None:
    recording = Recording(path)
    tracker = _UnitTracker(_tables(recording), Enemy())
    game_map = GameMap(
        next(exchange.response.game_info for exchange in recording if exchange.response.HasField("game_info"))
    )
    for observation in _observations(recording):
        tracker.update(observation.observation.raw_data, observation.observation.game_loop)
        state = _State(observation, tracker, game_map)
        for name in _STATE_READS:
            getattr(state, name)
    assert "actions" in _STATE_READS and "vision" in _STATE_READS
