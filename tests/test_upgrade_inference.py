"""Upgrade inference, the extension that reads the enemy's upgrades off what only an upgrade can cause: what each sign
shows, in made-up observations and in a real game, and when a game's api learns it."""

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from typing import Any

import pytest
from s2clientprotocol import common_pb2, debug_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, NoEnemyError
from sc2nachos.events import AnyUpgradeEvent, EventPriority, GameEndEvent, GameStartEvent, TurnEvent, TurnStartEvent
from sc2nachos.extensions.upgrade_inference import EnemyUpgradeAssumedEvent, UpgradeInference
from sc2nachos.gamedata import GameData
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, BuffId, EffectId, UncuratedIdError, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawBuffId
from sc2nachos.match import Computer, Difficulty, Participant, Race
from sc2nachos.protocol import Client, WebSocketTransport
from sc2nachos.run import GameProcess, MapFile, MapNotFoundError
from sc2nachos.state import Effect
from sc2nachos.units import Alliance, Unit, Visibility
from sc2nachos.units._tracking import _Tracker
from support import (
    PLAYER_ID,
    RealGame,
    ScriptedGame,
    make_enemy,
    make_game_info,
    make_observation,
    make_unit,
    shipped_game_data,
)


def _tracker_against_the_computer(tables: GameData) -> _Tracker:
    return _Tracker(tables, make_enemy(tables))


def _units_observed(tracker: _Tracker, *units: raw_pb2.Unit, step: int = 0) -> None:
    tracker.update(make_observation(step, units=units).observation.raw_data, step)


def _upgrades_revealed(
    tables: GameData, *units: raw_pb2.Unit, effects: tuple[Effect, ...] = ()
) -> frozenset[UpgradeId]:
    """The enemy upgrades `UpgradeInference` reads in one observation listing `units` and `effects`."""
    tracker = _tracker_against_the_computer(tables)
    _units_observed(tracker, *units)
    return UpgradeInference._enemy_upgrades_shown(tracker.unit_tracker.present, effects)


def _storm_of(alliance: Alliance) -> Effect:
    position = common_pb2.Point2D(x=10.0, y=10.0)
    return Effect._from_proto(
        raw_pb2.Effect(effect_id=EffectId.HIGH_TEMPLAR_STORM, pos=[position], radius=1.5, alliance=alliance.value)
    )


class TestWhatAUnitGivesAway:
    def test_an_enemy_warp_gate_shows_warp_gate(self, first_corpus_tables: GameData) -> None:
        remembered = make_unit(1, UnitTypeId.WARP_GATE, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
        assert _upgrades_revealed(first_corpus_tables, remembered) == {UpgradeId.WARP_GATE}

    def test_a_burrowed_enemy_shows_burrow_undetected(self, first_corpus_tables: GameData) -> None:
        zergling = make_unit(1, UnitTypeId.ZERGLING_BURROWED, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert _upgrades_revealed(first_corpus_tables, zergling) == {UpgradeId.BURROW}

    @pytest.mark.parametrize("unit_type", [UnitTypeId.LURKER_BURROWED, UnitTypeId.WIDOW_MINE_BURROWED])
    def test_what_burrows_without_burrow_shows_nothing(
        self, first_corpus_tables: GameData, unit_type: UnitTypeId
    ) -> None:
        assert not _upgrades_revealed(first_corpus_tables, make_unit(1, unit_type, alliance=Alliance.ENEMY))

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
        self, first_corpus_tables: GameData, unit_type: UnitTypeId, buff: BuffId, upgrade: UpgradeId
    ) -> None:
        assert _upgrades_revealed(
            first_corpus_tables, make_unit(1, unit_type, alliance=Alliance.ENEMY, buff_ids=[buff])
        ) == {upgrade}

    def test_a_buff_on_this_players_unit_shows_the_enemy_put_it_on(self, first_corpus_tables: GameData) -> None:
        slowed = make_unit(1, buff_ids=[BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW])
        matrixed = make_unit(2, UnitTypeId.SIEGE_TANK, buff_ids=[BuffId.RAVEN_INTERFERENCE_MATRIX])
        assert _upgrades_revealed(first_corpus_tables, slowed, matrixed) == {
            UpgradeId.CONCUSSIVE_SHELLS,
            UpgradeId.INTERFERENCE_MATRIX,
        }

    def test_a_buff_on_an_enemy_this_player_put_on_shows_nothing_of_the_enemy(
        self, first_corpus_tables: GameData
    ) -> None:
        slowed = make_unit(
            1, UnitTypeId.ZERGLING, alliance=Alliance.ENEMY, buff_ids=[BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW]
        )
        assert not _upgrades_revealed(first_corpus_tables, slowed)

    def test_an_enemy_marine_with_more_than_45_health_shows_combat_shield(self, first_corpus_tables: GameData) -> None:
        shielded = make_unit(1, alliance=Alliance.ENEMY, health=55.0, health_max=55.0)
        plain = make_unit(2, alliance=Alliance.ENEMY, health=45.0, health_max=45.0)
        assert _upgrades_revealed(first_corpus_tables, shielded) == {UpgradeId.COMBAT_SHIELD}
        assert not _upgrades_revealed(first_corpus_tables, plain)

    def test_a_unit_the_enemy_has_parasited_shows_only_neural_parasite(self, first_corpus_tables: GameData) -> None:
        """Its buffs and its type belong to the player it was taken from."""
        taken = make_unit(
            1,
            alliance=Alliance.ENEMY,
            health_max=55.0,
            buff_ids=[BuffId.INFESTOR_NEURAL_PARASITE, BuffId.MARINE_STIMMED],
        )
        assert _upgrades_revealed(first_corpus_tables, taken) == {UpgradeId.NEURAL_PARASITE}

    def test_a_unit_taken_from_this_player_shows_nothing_by_its_type(self, first_corpus_tables: GameData) -> None:
        """Its type is this player's doing: a burrowed zergling of this player's, now the enemy's and out of sight,
        shows no Burrow."""
        tracker = _tracker_against_the_computer(first_corpus_tables)
        _units_observed(tracker, make_unit(1, UnitTypeId.ZERGLING_BURROWED))
        taken = make_unit(1, UnitTypeId.ZERGLING_BURROWED, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        _units_observed(tracker, taken, step=1)
        assert not UpgradeInference._enemy_upgrades_shown(tracker.unit_tracker.present, ())

    def test_only_the_enemys_storm_shows_storm(self, first_corpus_tables: GameData) -> None:
        assert _upgrades_revealed(first_corpus_tables, effects=(_storm_of(Alliance.ENEMY),)) == {UpgradeId.STORM}
        assert not _upgrades_revealed(first_corpus_tables, effects=(_storm_of(Alliance.OWN),))

    def test_nothing_out_of_sight_shows_what_it_wore(self, first_corpus_tables: GameData) -> None:
        remembered = make_unit(
            1,
            UnitTypeId.MISSILE_TURRET,
            alliance=Alliance.ENEMY,
            visibility=Visibility.IN_FOG,
            buff_ids=[BuffId.GHOST_CLOAK],
        )
        assert not _upgrades_revealed(first_corpus_tables, remembered)

    def test_a_buff_the_curated_ids_leave_out_raises(self, first_corpus_tables: GameData) -> None:
        with pytest.raises(UncuratedIdError, match="DutchMarauderSlow"):
            _upgrades_revealed(
                first_corpus_tables, make_unit(1, alliance=Alliance.ENEMY, buff_ids=[RawBuffId.DutchMarauderSlow])
            )


def _api_after_playing(
    *observed: tuple[raw_pb2.Unit, ...],
    ended_on: tuple[raw_pb2.Unit, ...] = (),
    with_inference: bool = True,
    game_info: sc2api_pb2.ResponseGameInfo | None = None,
    started: Callable[[Api[Any]], object] = lambda _: None,
) -> Api[Any]:
    """An api, built with `UpgradeInference` if `with_inference`, after playing a turn on each of `observed`, the units
    each observation lists, and ending on an observation listing `ended_on`, in the game `game_info` describes, or
    against the computer of `make_game_info`. `started` runs before the game starts, once the api is built."""
    observations = [make_observation(step, units=units) for step, units in enumerate([*observed, ended_on])]
    game = ScriptedGame(observations, game_info=game_info, data=shipped_game_data())
    api: Api[Any] = Api(extensions=UpgradeInference) if with_inference else Api()
    started(api)
    api.play(game.joined())
    return api


_BURROWED_ZERGLING = make_unit(
    1, UnitTypeId.ZERGLING_BURROWED, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE
)


class TestWhenAGameLearnsIt:
    def test_an_api_without_it_reads_only_the_levels(self) -> None:
        marine = make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=1)
        api = _api_after_playing((_BURROWED_ZERGLING, marine), with_inference=False)
        assert api.enemy.upgrades == {UpgradeId.TERRAN_INFANTRY_WEAPONS_1}

    def test_what_the_first_observation_shows_is_known_on_the_first_turn(self) -> None:
        known_each_turn: list[frozenset[UpgradeId]] = []

        def watch(api: Api[Any]) -> None:
            api.on(TurnStartEvent, priority=EventPriority.HIGHEST)(lambda _: known_each_turn.append(api.enemy.upgrades))

        _api_after_playing((_BURROWED_ZERGLING,), started=watch)
        assert known_each_turn == [{UpgradeId.BURROW}]

    def test_what_a_turn_shows_is_known_to_the_bots_first_handler_of_it(self) -> None:
        known_each_turn: list[frozenset[UpgradeId]] = []

        def watch(api: Api[Any]) -> None:
            api.on(TurnStartEvent, priority=EventPriority.HIGHEST)(lambda _: known_each_turn.append(api.enemy.upgrades))

        _api_after_playing((), (_BURROWED_ZERGLING,), started=watch)
        assert known_each_turn == [frozenset(), {UpgradeId.BURROW}]

    def test_what_only_the_observation_the_game_ended_on_shows_is_known_as_it_ends(self) -> None:
        known_at_end: list[frozenset[UpgradeId]] = []

        def watch(api: Api[Any]) -> None:
            api.on(GameEndEvent)(lambda _: known_at_end.append(api.enemy.upgrades))

        _api_after_playing((), ended_on=(_BURROWED_ZERGLING,), started=watch)
        assert known_at_end == [{UpgradeId.BURROW}]

    def test_a_buff_the_curated_ids_leave_out_ends_the_game_from_its_handler(self) -> None:
        slowed = make_unit(1, alliance=Alliance.ENEMY, buff_ids=[RawBuffId.DutchMarauderSlow])
        with pytest.raises(UncuratedIdError, match="DutchMarauderSlow"):
            _api_after_playing((), (slowed,))

    def test_in_a_game_with_no_other_player_it_reads_nothing(self) -> None:
        alone = sc2api_pb2.PlayerInfo(
            player_id=PLAYER_ID, type=sc2api_pb2.PlayerType.Participant, race_requested=common_pb2.Race.Terran
        )
        api = _api_after_playing((_BURROWED_ZERGLING,), game_info=make_game_info(players=[alone]))
        with pytest.raises(NoEnemyError):
            _ = api.enemy


_WARP_GATE = make_unit(2, UnitTypeId.WARP_GATE, alliance=Alliance.ENEMY, visibility=Visibility.IN_FOG)
_STIMMED_MARINE = make_unit(3, alliance=Alliance.ENEMY, buff_ids=[BuffId.MARINE_STIMMED])


def _upgrade_events_handed_out(
    *observed: tuple[raw_pb2.Unit, ...],
    ended_on: tuple[raw_pb2.Unit, ...] = (),
    started: Callable[[Api[Any]], object] = lambda _: None,
) -> list[tuple[type[AnyUpgradeEvent], UpgradeId, float]]:
    """Each `AnyUpgradeEvent` a bot's handler of it is handed, by its type, its upgrade and its step, in the game
    `_api_after_playing` plays on `observed` and `ended_on`. `started` runs before the game starts."""
    handed_out: list[tuple[type[AnyUpgradeEvent], UpgradeId, float]] = []

    def watch(api: Api[Any]) -> None:
        api.on(AnyUpgradeEvent)(lambda event: handed_out.append((type(event), event.upgrade, event.time.steps)))
        started(api)

    _api_after_playing(*observed, ended_on=ended_on, started=watch)
    return handed_out


class TestTheEventsItHandsOut:
    def test_an_upgrade_shown_comes_as_assumed_on_the_turn_it_is_first_shown_and_not_again(self) -> None:
        handed_out = _upgrade_events_handed_out((), (_BURROWED_ZERGLING,), (_BURROWED_ZERGLING,))
        assert handed_out == [(EnemyUpgradeAssumedEvent, UpgradeId.BURROW, 1)]

    def test_several_shown_at_once_come_in_id_order(self) -> None:
        handed_out = _upgrade_events_handed_out((_WARP_GATE, _BURROWED_ZERGLING, _STIMMED_MARINE))
        assert [upgrade for _, upgrade, _ in handed_out] == [UpgradeId.STIMPACK, UpgradeId.BURROW, UpgradeId.WARP_GATE]

    def test_neither_a_level_nachos_reads_nor_an_upgrade_the_bot_assumed_first_comes_as_assumed(self) -> None:
        marine = make_unit(2, alliance=Alliance.ENEMY, attack_upgrade_level=1)

        def assume_burrow(api: Api[Any]) -> None:
            api.on(GameStartEvent)(lambda _: api.enemy.assume_upgrades(UpgradeId.BURROW))

        assert _upgrade_events_handed_out((_BURROWED_ZERGLING, marine), started=assume_burrow) == []

    def test_one_the_bot_forgot_comes_again_once_shown_again(self) -> None:
        def forget_burrow(api: Api[Any]) -> None:
            api.on(TurnEvent)(lambda _: api.enemy.forget_upgrades(UpgradeId.BURROW))

        handed_out = _upgrade_events_handed_out((_BURROWED_ZERGLING,), (_BURROWED_ZERGLING,), started=forget_burrow)
        assert handed_out == [
            (EnemyUpgradeAssumedEvent, UpgradeId.BURROW, 0),
            (EnemyUpgradeAssumedEvent, UpgradeId.BURROW, 1),
        ]

    def test_it_comes_before_the_bots_first_handler_of_the_turn(self) -> None:
        handed_out: list[type[object]] = []

        def watch(api: Api[Any]) -> None:
            api.on(TurnStartEvent, priority=EventPriority.HIGHEST)(lambda event: handed_out.append(type(event)))
            api.on(EnemyUpgradeAssumedEvent, priority=EventPriority.LOWEST)(
                lambda event: handed_out.append(type(event))
            )

        _api_after_playing((_BURROWED_ZERGLING,), started=watch)
        assert handed_out == [EnemyUpgradeAssumedEvent, TurnStartEvent]

    def test_what_only_the_observation_the_game_ended_on_shows_comes_before_the_bots_handlers_of_its_end(
        self,
    ) -> None:
        handed_out: list[type[object]] = []

        def watch(api: Api[Any]) -> None:
            api.on(GameEndEvent, priority=EventPriority.HIGHEST)(lambda event: handed_out.append(type(event)))
            api.on(EnemyUpgradeAssumedEvent)(lambda event: handed_out.append(type(event)))

        _api_after_playing((), ended_on=(_BURROWED_ZERGLING,), started=watch)
        assert handed_out == [EnemyUpgradeAssumedEvent, GameEndEvent]

    def test_only_selects_by_the_upgrade(self) -> None:
        handed_out: list[UpgradeId] = []

        def watch(api: Api[Any]) -> None:
            selected = EnemyUpgradeAssumedEvent.only(UpgradeId.WARP_GATE)
            api.on(selected)(lambda event: handed_out.append(event.upgrade))

        _api_after_playing((_WARP_GATE, _BURROWED_ZERGLING), started=watch)
        assert handed_out == [UpgradeId.WARP_GATE]


@contextmanager
def _signs_game(race: Race) -> Iterator[tuple[RealGame, Point]]:
    """A game as `race` under the `free` and `fast_build` cheats, and a point 10 tiles from home toward the middle."""
    try:
        game_map = MapFile.find("PylonAIE_v4")
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
            yield game, home.towards(game.map.playable_area.center, 10)
            client.leave_game()


def _unit_made(game: RealGame, unit_type: UnitTypeId, at: Point, *, owner: int | None = None) -> Unit[Any]:
    """A new `unit_type` at `at`, with its energy full."""
    game.debug(game.create(unit_type, at, owner=owner))
    game.turn(4)
    unit = game.newest(unit_type)
    energy = debug_pb2.DebugSetUnitValue(unit_value=debug_pb2.DebugSetUnitValue.Energy, value=200, unit_tag=unit.tag)
    game.debug(debug_pb2.DebugCommand(unit_value=energy))
    game.turn(2)
    return unit


def _research_in_game(game: RealGame, *research: tuple[AbilityId, Unit[Any], UpgradeId]) -> None:
    """Research each upgrade in turn, waiting for each to finish."""
    for ability, structure, upgrade in research:
        game.order(ability, structure)
        _turn_until(game, lambda upgrade=upgrade: upgrade in game.state.upgrades, steps=22)


def _turn_until(game: RealGame, done: Callable[[], bool], *, steps: int = 2, turns: int = 100) -> None:
    for _ in range(turns):
        if done():
            return
        game.turn(steps)
    assert done()


def _with_tech_lab(game: RealGame, unit_type: UnitTypeId, tech_lab: UnitTypeId, at: Point) -> Unit[Any]:
    """The tech lab a new `unit_type` builds, both within the five by five square around `at`."""
    structure = _unit_made(game, unit_type, at - (1, 0))
    game.order(AbilityId.BUILD_TECH_LAB, structure)
    _turn_until(game, lambda: bool(game.tracker.unit_tracker.present.own.of_type(tech_lab)), steps=22)
    return game.newest(tech_lab)


@pytest.mark.integration
def test_in_a_real_game_terran_units_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. Researches what each terran sign needs, produces each with this player's
    units, and reads what each reveals as `UpgradeInference` reads the enemy's."""
    with _signs_game(Race.TERRAN) as (game, toward):
        enemy = 3 - game.player
        barracks_lab = _with_tech_lab(
            game, UnitTypeId.BARRACKS, UnitTypeId.TECH_LAB_BARRACKS, game.open_ground(toward, size=5)
        )
        starport_lab = _with_tech_lab(
            game, UnitTypeId.STARPORT, UnitTypeId.TECH_LAB_STARPORT, game.open_ground(toward + (0, 8), size=5)
        )
        academy = _unit_made(game, UnitTypeId.GHOST_ACADEMY, game.open_ground(toward + (0, -8), size=3))
        marine = _unit_made(game, UnitTypeId.MARINE, toward + (-6, 0))
        assert (marine.health_max, UpgradeInference._owner_upgrades_shown_by_unit(marine)) == (45.0, frozenset())

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
        assert (marine.health_max, UpgradeInference._owner_upgrades_shown_by_unit(marine)) == (
            55.0,
            {UpgradeId.COMBAT_SHIELD},
        )
        game.order(AbilityId.STIM, marine)
        game.turn(4)
        assert UpgradeInference._owner_upgrades_shown_by_unit(marine) == {UpgradeId.COMBAT_SHIELD, UpgradeId.STIMPACK}

        marauder = _unit_made(game, UnitTypeId.MARAUDER, toward + (-6, 2))
        game.order(AbilityId.STIM, marauder)
        game.turn(4)
        assert UpgradeInference._owner_upgrades_shown_by_unit(marauder) == {UpgradeId.STIMPACK}

        banshee = _unit_made(game, UnitTypeId.BANSHEE, toward + (-6, 4))
        ghost = _unit_made(game, UnitTypeId.GHOST, toward + (-6, 6))
        game.order(AbilityId.CLOAK_ON, banshee)
        game.order(AbilityId.CLOAK_ON, ghost)
        game.turn(8)
        assert (
            UpgradeInference._owner_upgrades_shown_by_unit(banshee),
            UpgradeInference._owner_upgrades_shown_by_unit(ghost),
        ) == (
            {UpgradeId.BANSHEE_CLOAK},
            {UpgradeId.GHOST_CLOAK},
        )

        roach = _unit_made(game, UnitTypeId.ROACH, toward + (-1, 2), owner=enemy)
        game.order(AbilityId.ATTACK, marauder, target=roach)
        _turn_until(game, lambda: BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW in roach.buffs)
        assert UpgradeInference._opponent_upgrades_shown_by_unit(roach) == {UpgradeId.CONCUSSIVE_SHELLS}
        # Killed, or the marauder would slow the tank too.
        game.debug(game.kill(marauder, roach))

        raven = _unit_made(game, UnitTypeId.RAVEN, toward + (-6, 8))
        tank = _unit_made(game, UnitTypeId.SIEGE_TANK, toward + (-1, 8), owner=enemy)
        game.order(AbilityId.RAVEN_INTERFERENCE_MATRIX, raven, target=tank)
        _turn_until(game, lambda: BuffId.RAVEN_INTERFERENCE_MATRIX in tank.buffs)
        assert UpgradeInference._opponent_upgrades_shown_by_unit(tank) == {UpgradeId.INTERFERENCE_MATRIX}


@pytest.mark.integration
def test_in_a_real_game_protoss_units_and_effects_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. As the terran test, for the protoss signs."""
    with _signs_game(Race.PROTOSS) as (game, toward):
        enemy = 3 - game.player
        spots = [game.open_ground(toward + (0, 7 * index), size=5) - (1, 1) for index in range(4)]
        for spot in spots:
            # Where `tools/sweep_tech_tree.py` puts a pylon to power a structure it has just created.
            game.debug(game.create(UnitTypeId.PYLON, spot + (2.5, 2.5)))
        core = _unit_made(game, UnitTypeId.CYBERNETICS_CORE, spots[0])
        council = _unit_made(game, UnitTypeId.TWILIGHT_COUNCIL, spots[1])
        archive = _unit_made(game, UnitTypeId.TEMPLAR_ARCHIVE, spots[2])
        gateway = _unit_made(game, UnitTypeId.GATEWAY, spots[3])
        assert UpgradeInference._owner_upgrades_shown_by_unit(gateway) == frozenset()

        _research_in_game(
            game,
            (AbilityId.CYBERNETICS_CORE_RESEARCH_WARP_GATE, core, UpgradeId.WARP_GATE),
            (AbilityId.TWILIGHT_COUNCIL_RESEARCH_CHARGE, council, UpgradeId.CHARGE),
            (AbilityId.TEMPLAR_ARCHIVE_RESEARCH_STORM, archive, UpgradeId.STORM),
        )
        _turn_until(game, lambda: gateway.type_id is UnitTypeId.WARP_GATE, steps=22)
        assert UpgradeInference._owner_upgrades_shown_by_unit(gateway) == {UpgradeId.WARP_GATE}

        zealot = _unit_made(game, UnitTypeId.ZEALOT, toward + (-8, 0))
        roach = _unit_made(game, UnitTypeId.ROACH, toward + (-14, 0), owner=enemy)
        game.order(AbilityId.ATTACK, zealot, target=roach)
        _turn_until(game, lambda: BuffId.ZEALOT_CHARGING in zealot.buffs, turns=60)
        assert UpgradeInference._owner_upgrades_shown_by_unit(zealot) == {UpgradeId.CHARGE}

        templar = _unit_made(game, UnitTypeId.HIGH_TEMPLAR, toward + (-8, 4))
        game.order(AbilityId.HIGH_TEMPLAR_STORM, templar, target=toward + (-10, 4))
        _turn_until(game, lambda: any(effect.id is EffectId.HIGH_TEMPLAR_STORM for effect in game.state.effects))
        (storm,) = [effect for effect in game.state.effects if effect.id is EffectId.HIGH_TEMPLAR_STORM]
        assert (storm.alliance, UpgradeInference._owner_upgrades_shown_by_effect(storm)) == (
            Alliance.OWN,
            {UpgradeId.STORM},
        )


@pytest.mark.integration
def test_in_a_real_game_zerg_units_give_away_their_upgrades() -> None:
    """Run with `pytest -m integration`. As the terran test, for the zerg signs, on the creep around the hatchery."""
    with _signs_game(Race.ZERG) as (game, _):
        enemy = 3 - game.player
        hatchery = game.tracker.unit_tracker.present.own.of_type(UnitTypeId.HATCHERY)[0]
        near = hatchery.position.towards(game.map.playable_area.center, 7)
        game.debug(game.create(UnitTypeId.HIVE, game.open_ground(near + (6, 0), size=5)))
        game.turn(4)
        hive = game.newest(UnitTypeId.HIVE)
        den = _unit_made(game, UnitTypeId.HYDRALISK_DEN, game.open_ground(near, size=3))
        pit = _unit_made(game, UnitTypeId.INFESTATION_PIT, game.open_ground(near + (0, 5), size=3))

        _research_in_game(
            game,
            (AbilityId.LAIR_RESEARCH_BURROW, hive, UpgradeId.BURROW),
            (AbilityId.HYDRALISK_DEN_RESEARCH_HYDRALISK_LUNGE, den, UpgradeId.HYDRALISK_LUNGE),
            (AbilityId.INFESTATION_PIT_RESEARCH_NEURAL_PARASITE, pit, UpgradeId.NEURAL_PARASITE),
        )
        zergling = _unit_made(game, UnitTypeId.ZERGLING, near + (-4, 0))
        assert UpgradeInference._owner_upgrades_shown_by_unit(zergling) == frozenset()
        game.order(AbilityId.BURROW, zergling)
        _turn_until(game, lambda: zergling.type_id is UnitTypeId.ZERGLING_BURROWED)
        assert UpgradeInference._owner_upgrades_shown_by_unit(zergling) == {UpgradeId.BURROW}

        hydralisk = _unit_made(game, UnitTypeId.HYDRALISK, near + (-4, 3))
        game.order(AbilityId.HYDRALISK_LUNGE, hydralisk)
        _turn_until(game, lambda: BuffId.HYDRALISK_LUNGE in hydralisk.buffs)
        assert UpgradeInference._owner_upgrades_shown_by_unit(hydralisk) == {UpgradeId.HYDRALISK_LUNGE}

        infestor = _unit_made(game, UnitTypeId.INFESTOR, near + (-4, 6))
        marine = _unit_made(game, UnitTypeId.MARINE, near + (-1, 6), owner=enemy)
        game.order(AbilityId.INFESTOR_NEURAL_PARASITE, infestor, target=marine)
        _turn_until(game, lambda: marine.alliance is Alliance.OWN)
        assert BuffId.INFESTOR_NEURAL_PARASITE in marine.buffs
        assert UpgradeInference._owner_upgrades_shown_by_unit(marine) == {UpgradeId.NEURAL_PARASITE}
