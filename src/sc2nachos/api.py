"""Everything a bot talks to."""

from collections.abc import Iterable
from typing import Any, overload

from loguru import logger

from sc2nachos._errors import NachOSError
from sc2nachos._game import _Game
from sc2nachos.constants import steps_to_seconds
from sc2nachos.enemy import Enemy, UpgradeInference
from sc2nachos.events import EventBus, GameEndEvent, GameStartEvent, TurnEvent, TurnStartEvent
from sc2nachos.gamedata import GameData, Resources
from sc2nachos.gamemap import Expansion, GameMap
from sc2nachos.geometry import Grid
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.match import Result
from sc2nachos.orders import OrderBook
from sc2nachos.protocol import Client, GameEndedError
from sc2nachos.state import ActionFailure, Effect, InProduction, Score, Supply, UiUnitCounts
from sc2nachos.units import Unit, Units, UnitType
from sc2nachos.units._units import _type_ids


class NotPlayingError(NachOSError, RuntimeError):
    """No game has been joined, so there is nothing to answer from."""


class Api:
    """Everything a bot talks to. It is built before there is a game.

    Construct one where the rest of your bot can reach it, module scope included, and pass it to `run_local` or
    `run_ladder` for every game it plays. Never subclass it: your own helpers belong in your own modules, as
    ordinary functions.

    Time is counted in steps. One step is one game loop, 22.4 steps make a second, and the bot takes a turn every
    `steps_per_turn` steps, which the game is played with. Each turn hands its handlers a `TurnStartEvent`, then
    the events its observation reports, in the order `sc2nachos.events` gives, then a `TurnEvent`.

    Anything of the game raises `NotPlayingError` until the first game starts. After a game ends, the api keeps
    answering from it until the next one starts.
    """

    def __init__(
        self,
        *,
        enemy_upgrade_inference: UpgradeInference = UpgradeInference.BASIC,
        time_handlers: bool = False,
        build_reach: float = 2.5,
    ) -> None:
        """Infer as much of `api.enemy.upgrades` as `enemy_upgrade_inference` says, time every handler call if
        `time_handlers`, and give a worker a held build once it is within `build_reach` of the site. This connects to
        nothing.

        Raises `ValueError` for a `build_reach` that is not positive.
        """
        if not build_reach > 0.0:
            raise ValueError(f"build_reach must be positive, not {build_reach}")
        self._enemy_upgrade_inference = enemy_upgrade_inference
        self._build_reach = build_reach
        self._events = EventBus(time_handlers=time_handlers)
        # Everything that belongs to one game and nothing that outlives it; each game replaces it whole.
        self._game: _Game | None = None

    @property
    def events(self) -> EventBus:
        """The event bus: the events the api hands out, and the handlers subscribed to them.

        Available before any game, so handlers can subscribe as their modules are imported.
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
    def expansions(self) -> tuple[Expansion, ...]:
        """Every place on the map to build a townhall, as the game began, nearest this player's start first by the
        ground a unit walks: its own start, then its natural. Those no walk reaches come last, nearest first in a line.
        """
        return self._current_game().expansions

    @property
    def data(self) -> GameData:
        """The game's data tables, as they were before any upgrade."""
        return self._current_game().game_data

    @property
    def step(self) -> int:
        """The step of the last observation."""
        return self._current_game().step

    @property
    def time(self) -> float:
        """The game time in seconds."""
        return steps_to_seconds(self.step)

    @property
    def result(self) -> Result | None:
        """How the game ended for this player, or `None` while it is being played."""
        return self._current_game().result

    @property
    def orders(self) -> OrderBook:
        """This player's orders for the turn.

        Orders are sent in one request after the turn's last handler returns. A turn that orders nothing sends nothing.
        """
        return self._current_game().orders

    @property
    def units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, including remembered structures out of sight and hidden units."""
        return self._current_game().tracker.unit_tracker.present

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation first, then those it left out."""
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
        """The game interface's counts of this player's idle workers, army units and warp gates."""
        return self._current_game().state.ui_unit_counts

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade this player has finished. Raises `UncuratedIdError` if one is one the curated ids leave out."""
        return self._current_game().state.upgrades

    @overload
    def in_production(
        self, unit_type: type[UnitType.AnyType], /, *unit_types: type[UnitType.AnyType]
    ) -> tuple[InProduction, ...]: ...

    @overload
    def in_production(self, type_ids: UnitTypeId | Iterable[UnitTypeId], /) -> tuple[InProduction, ...]: ...

    def in_production(
        self, types: type[UnitType.AnyType] | UnitTypeId | Iterable[UnitTypeId], /, *unit_types: type[UnitType.AnyType]
    ) -> tuple[InProduction, ...]:
        """The units of the given types this player has started making or paid for, in the last observation.

        There is one for each unit: a train a structure shows, queued ones included; an egg or a cocoon becoming one; a
        structure going up, a structure morphing into one, a unit warping in; and a build a worker has been sent to
        make, until the structure stands. A reactor's two marines are two. What NachOS holds and has not sent is not
        among them; it is in `api.orders.issued_to`. Takes the types as `Units.of_type` does.
        """
        wanted = frozenset((types,)) if isinstance(types, UnitTypeId) else _type_ids(types, unit_types)
        return self._current_game().state.production.of_types(wanted)

    def research_progress(self, upgrade: UpgradeId) -> float | None:
        """How far along this player's research of `upgrade` is, from 0 to 1, or `None` if nothing researches it. A
        finished one is in `upgrades`."""
        return self._current_game().state.production.progress(upgrade)

    @property
    def action_failures(self) -> tuple[ActionFailure, ...]:
        """The orders the game refused when the last turn's were sent, one for each unit a refused command named, then
        those it accepted earlier and gave up on since the observation before.

        Raises `UncuratedIdError` if one the game gave up on names an ability the curated ids leave out.
        """
        game = self._current_game()
        return game.orders._refusals + game.state.action_failures

    @property
    def enemy(self) -> Enemy:
        """The other player, and what is known of it.

        `api.enemy.upgrades` holds the upgrades the bot added with `assume_upgrades` and those enemy units have shown,
        which NachOS reads as far as `enemy_upgrade_inference` says. Everything an upgrade changes on an enemy unit,
        `Unit.weapons` and `Unit.speed` among them, counts them.
        """
        return self._current_game().enemy

    @property
    def vision(self) -> Grid[bool]:
        """Where this player can see now, over the playable area like the map's grids."""
        return self._current_game().state.vision

    @property
    def explored(self) -> Grid[bool]:
        """Where this player has seen at any point in the game, over the playable area."""
        return self._current_game().state.explored

    @property
    def creep(self) -> Grid[bool]:
        """Where creep covers the ground, over the playable area."""
        return self._current_game().state.creep

    @property
    def effects(self) -> tuple[Effect, ...]:
        """Every effect this player can see. Raises `UncuratedIdError` if one is an effect the curated ids leave out."""
        return self._current_game().state.effects

    def play(
        self, client: Client, *, steps_per_turn: int = 1, realtime: bool = False, time_limit: float | None = None
    ) -> Result:
        """Play the game `client` has joined to its end, a turn every `steps_per_turn` steps, and return how it ended
        for this player.

        `run_local` and `run_ladder` call this. Call it directly to play a game connected some other way, such as a
        recording. `time_limit`, in game seconds, calls a game that reaches it a tie.

        Each call starts from nothing, so one api plays any number of games in a row. Handlers stay subscribed from
        one game to the next; what each has done starts afresh.
        """
        if self._game is not None:
            self._game.tracker.end()
        game = _Game.start(client, enemy_upgrade_inference=self._enemy_upgrade_inference, build_reach=self._build_reach)
        self._game = game
        logger.info("Playing {} at {} steps a turn", game.game_map.name, steps_per_turn)
        events = self._events
        events._start_game()
        events._set_step(game.step)
        try:
            events.emit(GameStartEvent(step=game.step))
            result = self._play_turns(game, steps_per_turn=steps_per_turn, realtime=realtime, time_limit=time_limit)
            events.emit(GameEndEvent(result, step=game.step))
        finally:
            # Ended, or ended by a handler that raised: no game is being played to give an event its step.
            events._set_step(None)
        return result

    def _play_turns(self, game: _Game, *, steps_per_turn: int, realtime: bool, time_limit: float | None) -> Result:
        """Take the turns of `game` until it ends, and return how it ended."""
        events, client = self._events, game.client
        while (result := game.outcome()) is None:
            if time_limit is not None and self.time >= time_limit:
                logger.info("Calling the game a tie at its {:.0f} second limit", time_limit)
                return game.finish(Result.TIE)

            events.emit(TurnStartEvent(step=game.step))
            events._hand_out([*game.report(events), TurnEvent(step=game.step)])
            try:
                # The turn's handlers have all returned, so their orders go out now as one request.
                game.orders._send(client)
                if not realtime:
                    client.step(steps_per_turn)
            except GameEndedError:
                # The game ended while the handlers ran, as a realtime game can. Its last observation says how.
                game.observe()
            else:
                # A realtime game runs on its own, so each turn asks for the step it wants.
                game.observe(game.step + steps_per_turn if realtime else None)
            events._set_step(game.step)
        return result
