"""Everything a bot talks to."""

from typing import Any

from loguru import logger

from sc2nachos._errors import NachOSError
from sc2nachos._game import _Game
from sc2nachos.constants import steps_to_seconds
from sc2nachos.enemy import Enemy, UpgradeInference
from sc2nachos.events import EventBus, GameEndEvent, GameStartEvent, TurnEvent, TurnStartEvent
from sc2nachos.gamedata import GameData, Resources
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Grid
from sc2nachos.ids import UpgradeId
from sc2nachos.match import Result
from sc2nachos.orders import OrderBook
from sc2nachos.protocol import Client
from sc2nachos.state import ActionFailure, Effect, Score, Supply, UiUnitCounts
from sc2nachos.units import Unit, Units


class NotPlayingError(NachOSError, RuntimeError):
    """There is no game to answer from, because none has been joined."""


class Api:
    """Everything a bot talks to, built before there is a game to talk to.

    Construct one where the rest of your bot can reach it, which may be module scope, and hand it to `run_local`
    or `run_ladder` for every game it plays. Never subclass it: helpers of your own belong in your own modules, as
    ordinary functions.

    Time is counted in steps. One step is one game loop, 22.4 of them make a second, and the bot takes a turn
    every `steps_per_turn` of them, which the game is played with. Each turn hands its handlers a `TurnStartEvent`,
    then what its observation reports has happened, in the order `sc2nachos.events` gives, then a `TurnEvent`.

    What belongs to a game raises `NotPlayingError` until the first game starts. Once a game is over it goes on
    answering from that game until the next one starts.
    """

    def __init__(
        self, *, enemy_upgrade_inference: UpgradeInference = UpgradeInference.BASIC, time_handlers: bool = False
    ) -> None:
        """Work out as much of `api.enemy.upgrades` as `enemy_upgrade_inference` says, and time every handler's calls
        if `time_handlers`. Nothing here connects to anything."""
        self._enemy_upgrade_inference = enemy_upgrade_inference
        self._events = EventBus(time_handlers=time_handlers)
        # Everything that belongs to one game and nothing that outlives it, so each game replaces it whole.
        self._game: _Game | None = None

    @property
    def events(self) -> EventBus:
        """What the api tells its handlers about as a game goes on, and who they are.

        It answers before any game, so that handlers can subscribe as their modules are imported.
        """
        return self._events

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
        return self._current_game().game_map

    @property
    def data(self) -> GameData:
        """The tables the game is played by, as they stood before any upgrade."""
        return self._current_game().game_data

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
    def orders(self) -> OrderBook:
        """What this player orders this turn, and what became of the orders it has given.

        Orders given while the turn's handlers run go out in one request once the last of them has returned, so a
        turn that orders nothing sends nothing.
        """
        return self._current_game().orders

    @property
    def units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, structures remembered out of sight and hidden units included."""
        return self._current_game().tracker.unit_tracker.present

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation, then those it left out."""
        return self._current_game().tracker.unit_tracker.known

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
    def action_failures(self) -> tuple[ActionFailure, ...]:
        """The orders the game took and has given up on since the observation before.

        Raises `UncuratedIdError` where one names an ability the curated ids leave out.
        """
        return self._current_game().state.action_failures

    @property
    def enemy(self) -> Enemy:
        """The other player of this game, and what is known of it.

        `api.enemy.upgrades` holds any upgrades a bot adds with `assume_upgrades`, and those its units have shown, which
        NachOS reads as far as `enemy_upgrade_inference` says. Every read of an enemy unit that
        upgrades change counts them, `Unit.weapons` and `Unit.speed` among them.
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

    def play(
        self, client: Client, *, steps_per_turn: int = 1, realtime: bool = False, time_limit: float | None = None
    ) -> Result:
        """Play the game `client` has already joined to its end, taking a turn every `steps_per_turn` steps, and return
        how it ended for this player.

        `run_local` and `run_ladder` call this. Call it directly to play a game connected some other way,
        such as a recording. `time_limit` gives up on a game that is taking too long, in game seconds.

        Each call starts its game from nothing, so one api plays any number of games, one after another. Handlers
        stay subscribed from one to the next, and what each has done starts afresh.
        """
        if self._game is not None:
            self._game.tracker.end()
        game = _Game.start(client, enemy_upgrade_inference=self._enemy_upgrade_inference)
        self._game = game
        logger.info("Playing {} at {} steps a turn", game.game_map.name, steps_per_turn)
        events = self._events
        events._start_game()
        events._set_step(game.step)
        events.emit(GameStartEvent(step=game.step))

        while (result := game.outcome()) is None:
            if time_limit is not None and self.time >= time_limit:
                logger.info("Calling the game a tie at its {:.0f} second limit", time_limit)
                result = game.finish(Result.TIE)
                break

            events.emit(TurnStartEvent(step=game.step))
            events._hand_out([*game.report(events), TurnEvent(step=game.step)])
            # Every handler of the turn has returned, so what they ordered goes out now, as one request.
            game.orders._send(client)

            if realtime:
                # A realtime game runs whether or not anyone is watching, so each turn asks for the step it wants.
                game.observe(game.step + steps_per_turn)
            else:
                client.step(steps_per_turn)
                game.observe()
            events._set_step(game.step)
        events.emit(GameEndEvent(result, step=game.step))
        events._set_step(None)
        return result
