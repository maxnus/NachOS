"""Everything a bot talks to."""

from dataclasses import dataclass
from typing import Any, Final, Self

from loguru import logger
from s2clientprotocol import sc2api_pb2

from sc2nachos._errors import NachOSError
from sc2nachos.constants import steps_to_seconds
from sc2nachos.enemy import Enemy, upgrades_shown
from sc2nachos.gamedata import GameData, Resources
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Grid
from sc2nachos.ids import UpgradeId
from sc2nachos.match import Result
from sc2nachos.protocol import Client
from sc2nachos.state import Effect, Score, Supply, UiUnitCounts
from sc2nachos.state._state import _State
from sc2nachos.units import Unit, Units
from sc2nachos.units._tracker import _UnitTracker


class NotPlayingError(NachOSError, RuntimeError):
    """There is no game to answer from, because none has been joined."""


@dataclass(slots=True)
class _Game:
    """One game as it is played: the client it is played on, and everything seen of it so far."""

    client: Final[Client]
    map: Final[GameMap]
    data: Final[GameData]
    enemy: Final[Enemy]
    unit_tracker: Final[_UnitTracker]
    observation: sc2api_pb2.ResponseObservation
    state: _State
    # Kept beside the observation, because reading it out of the protobuf costs over ten times as much.
    step: int
    result: Result | None = None

    @classmethod
    def start(cls, client: Client) -> Self:
        """Start on the game `client` has joined: ask once for its map and pre-upgrade tables, and observe it."""
        info, data = client.game_info(), client.game_data()
        observation = client.observation()
        step = _step(observation)
        tables = GameData(data)
        enemy = Enemy()
        unit_tracker = _UnitTracker(tables, enemy)
        game_map = GameMap(info)
        game = cls(
            client,
            game_map,
            tables,
            enemy,
            unit_tracker,
            observation,
            _State(observation, unit_tracker, game_map),
            step,
        )
        game._take_in(observation, step)
        return game

    def observe(self, step: int | None = None) -> None:
        """Observe the game now, or once it reaches `step`."""
        observation = self.client.observation(game_loop=step)
        self._take_in(observation, _step(observation))

    def _take_in(self, observation: sc2api_pb2.ResponseObservation, step: int) -> None:
        """Read `observation`: its units, what they show of the enemy's upgrades, and the rest of what it reports."""
        self.observation = observation
        self.step = step
        self.unit_tracker.update(observation.observation.raw_data, step)
        self.enemy.assume_upgrades(*upgrades_shown(self.unit_tracker.present_units, self.unit_tracker.upgrade_lines))
        self.state = _State(observation, self.unit_tracker, self.map)

    def outcome(self) -> Result | None:
        """How the game ended as of the last observation, settled once it has, or `None` while it goes on."""
        if (result := self.client.result) is not None:
            return self.finish(result)
        if not self.client.in_game:
            # Over, and the game would not say how even when the client asked it again.
            return self.finish(Result.UNDECIDED)
        return None

    def finish(self, result: Result) -> Result:
        """Settle how the game ended, and say so."""
        self.result = result
        seconds = steps_to_seconds(self.step)
        logger.info("The game ended in a {} at step {}, {:.0f} seconds in", result, self.step, seconds)
        return result


def _step(observation: sc2api_pb2.ResponseObservation) -> int:
    """The step `observation` was made at."""
    # The protocol's game loop is what NachOS calls a step, and this is the one place the two meet.
    return observation.observation.game_loop


class Api:
    """Everything a bot talks to, built before there is a game to talk to.

    Construct one where the rest of your bot can reach it, which may be module scope, and hand it to `run_local`
    or `run_ladder` for every game it plays. Never subclass it: helpers of your own belong in your own modules, as
    ordinary functions.

    Time is counted in steps. One step is one game loop, 22.4 of them make a second, and the bot takes a turn
    every `steps_per_turn` of them.

    What belongs to a game raises `NotPlayingError` until the first game starts. Once a game is over it goes on
    answering from that game until the next one starts.
    """

    def __init__(self, *, steps_per_turn: int = 1) -> None:
        """Take a turn every `steps_per_turn` steps. Nothing here connects to anything."""
        self._steps_per_turn = steps_per_turn
        # Everything that belongs to one game and nothing that outlives it, so each game replaces it whole.
        self._game: _Game | None = None

    @property
    def steps_per_turn(self) -> int:
        """How many steps pass between one turn and the next."""
        return self._steps_per_turn

    def _current_game(self) -> _Game:
        """The game being played, or the one played last."""
        if self._game is None:
            raise NotPlayingError("no game has been joined")
        return self._game

    @property
    def client(self) -> Client:
        """The client the game is played on."""
        return self._current_game().client

    @property
    def map(self) -> GameMap:
        """The map the game is played on."""
        return self._current_game().map

    @property
    def data(self) -> GameData:
        """The tables the game is played by, as they stood before any upgrade."""
        return self._current_game().data

    @property
    def step(self) -> int:
        """The step the game had reached when it was last observed."""
        return self._current_game().step

    @property
    def time(self) -> float:
        """How long the game has been played, in seconds."""
        return steps_to_seconds(self.step)

    @property
    def result(self) -> Result | None:
        """How the game ended for this player, or `None` while it is still being played."""
        return self._current_game().result

    @property
    def units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, structures remembered out of sight and hidden units included."""
        return self._current_game().unit_tracker.present_units

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation, then those it left out."""
        return self._current_game().unit_tracker.known_units

    @property
    def score(self) -> Score:
        """The score the game keeps for this player."""
        return self._current_game().state.score

    @property
    def resources(self) -> Resources:
        """The minerals and vespene this player has to spend."""
        return self._current_game().state.resources

    @property
    def supply(self) -> Supply:
        """The supply this player's units take and its structures and units provide."""
        return self._current_game().state.supply

    @property
    def ui_unit_counts(self) -> UiUnitCounts:
        """The counts of this player's idle workers, army units and warp gates the game's interface shows."""
        return self._current_game().state.ui_unit_counts

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade this player has finished researching.

        Raises `UncuratedIdError` where one is an upgrade the curated ids leave out.
        """
        return self._current_game().state.upgrades

    @property
    def enemy(self) -> Enemy:
        """The other player of this game, and what is known of it.

        `api.enemy.upgrades` holds the upgrades its units have shown, which NachOS reads off their levels, and any a
        bot adds with `assume_upgrade`. Every read of an enemy unit that upgrades change counts them, `Unit.weapons`
        and `Unit.speed` among them.
        """
        return self._current_game().enemy

    @property
    def vision(self) -> Grid[bool]:
        """Where this player can see now, over the playable area, as the map's grids are."""
        return self._current_game().state.vision

    @property
    def explored(self) -> Grid[bool]:
        """Where this player has seen at some point in the game, over the playable area."""
        return self._current_game().state.explored

    @property
    def creep(self) -> Grid[bool]:
        """Where creep covers the ground, over the playable area."""
        return self._current_game().state.creep

    @property
    def effects(self) -> tuple[Effect, ...]:
        """Every effect this player can see.

        Raises `UncuratedIdError` where one is an effect the curated ids leave out.
        """
        return self._current_game().state.effects

    def play(self, client: Client, *, realtime: bool = False, time_limit: float | None = None) -> Result:
        """Play the game `client` has already joined to its end, and return how it ended for this player.

        `run_local` and `run_ladder` call this. Call it directly to play a game connected some other way,
        such as a recording. `time_limit` gives up on a game that is taking too long, in game seconds.

        Each call starts its game from nothing, so one api plays any number of games, one after another.
        """
        if self._game is not None:
            self._game.unit_tracker.end()
        game = _Game.start(client)
        self._game = game
        logger.info("Playing {} at {} steps a turn", game.map.name, self._steps_per_turn)

        while (result := game.outcome()) is None:
            if time_limit is not None and self.time >= time_limit:
                logger.info("Calling the game a tie at its {:.0f} second limit", time_limit)
                return game.finish(Result.TIE)

            # A turn's work belongs here, once there is any: the event dispatch and the flush of orders.

            if realtime:
                # A realtime game runs whether or not anyone is watching, so each turn asks for the step it wants.
                game.observe(game.step + self._steps_per_turn)
            else:
                client.step(self._steps_per_turn)
                game.observe()
        return result
