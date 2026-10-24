"""Everything a bot talks to."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, overload

from loguru import logger

from sc2nachos._checks import check_file_to_write, check_game_time
from sc2nachos._errors import NachOSError
from sc2nachos._game import _Game
from sc2nachos.camera import Camera
from sc2nachos.debug import DebugCommands
from sc2nachos.enemy import Enemy, UpgradeInference
from sc2nachos.events import (
    Event,
    EventFilter,
    EventPriority,
    EventSubscriber,
    GameEndEvent,
    GameStartEvent,
    HandlerTiming,
    ParameterizedEvent,
    TurnEvent,
    TurnStartEvent,
)
from sc2nachos.events._event_bus import _EventBus
from sc2nachos.game_time import GameTime
from sc2nachos.gamedata import GameData, Resources
from sc2nachos.gamemap import Expansion, GameMap
from sc2nachos.geometry import Grid
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.match import Result
from sc2nachos.orders import OrderBook
from sc2nachos.pathing import Pathing
from sc2nachos.placement import PlacementGrids
from sc2nachos.player import Player
from sc2nachos.protocol import Client, GameEndedError
from sc2nachos.state import ActionFailure, Effect, InProduction, Research, Score, Supply, UiUnitCounts
from sc2nachos.units import Unit, Units, UnitType
from sc2nachos.units._units import _type_ids

# The most bytes of UTF-8 a chat message may take: one of 1040 kills the game client (in game).
_MOST_CHAT_BYTES = 1039


class NotPlayingError(NachOSError, RuntimeError):
    """No game has been joined, so there is nothing to answer from."""


class NoEnemyError(NachOSError, RuntimeError):
    """The game has no other player, as a mini-game has not, so there is no enemy to answer about."""


class FogOnError(NachOSError, RuntimeError):
    """The fog is on, as in every game, so the game reports only what this player sees."""


class Api:
    """Everything a bot talks to. It is built before there is a game.

    Construct one where the rest of your bot can reach it, module scope included, and pass it to `run_local` or
    `run_ladder` for every game it plays. Never subclass it: your own helpers belong in your own modules, as
    ordinary functions.

    Time is counted in steps: one step is one game loop, and 22.4 make a second. A moment or a length of game time is a
    `GameTime`, but for the two counts of steps, `step` and `steps_per_turn`. The bot takes a turn every
    `steps_per_turn` steps, which the game is played with. Each turn hands its handlers a `TurnStartEvent`, then
    the events its observation reports, in the order `docs/events.md` gives, then a `TurnEvent`.

    Anything of the game raises `NotPlayingError` until the first game starts. After a game ends, the api keeps
    answering from it until the next one starts.
    """

    def __init__(
        self,
        *,
        enemy_upgrade_inference: UpgradeInference = UpgradeInference.BASIC,
        time_handlers: bool = False,
    ) -> None:
        """Infer as much of `api.enemy.upgrades` as `enemy_upgrade_inference` says, and time every handler call, read
        as `handler_timings`, if `time_handlers`. This connects to nothing."""
        self._enemy_upgrade_inference = enemy_upgrade_inference
        self._events = _EventBus(time_handlers=time_handlers)
        # Everything that belongs to one game and nothing that outlives it; each game replaces it whole.
        self._game: _Game | None = None

    # A type checker refuses a parameterized event subscribed to bare: `None` is not callable.
    @overload
    def on[E: ParameterizedEvent](  # pyright: ignore[reportOverlappingOverload]
        self,
        event_type: type[E],
        /,
        *,
        priority: EventPriority = ...,
        min_interval: GameTime | None = ...,
        at_time: GameTime | None = ...,
        once: bool = ...,
        catch_exceptions: bool = ...,
    ) -> None: ...

    @overload
    def on[E: Event](
        self,
        event_type: type[E] | EventFilter[E],
        /,
        *,
        priority: EventPriority = ...,
        min_interval: GameTime | None = ...,
        at_time: GameTime | None = ...,
        once: bool = ...,
        catch_exceptions: bool = ...,
    ) -> EventSubscriber[E]: ...

    def on(self, event_type: type[Event] | EventFilter[Any], /, **options: Any) -> Any:
        """Subscribe the decorated function to `event_type`, or mark the decorated method for `subscribe`. This works
        before any game, so handlers can subscribe as their modules are imported.

        `event_type` is an event type, whose events and its subclasses' the handler is handed, or a filter from `only`
        or `of`. `where` on the `EventSubscriber` it returns narrows further. A type that has `of` is subscribed to only
        through it, and a type checker refuses it bare. A handler of `Event` is handed every event, which makes NachOS
        produce every type of event it otherwise would not, including comparing every unit with the observation
        before; a parameterized event comes only with the parameters other handlers asked for.

        A method is marked, and subscribed for each instance passed to `subscribe`: a function defined in a class body
        that takes at least two positional arguments without defaults, the instance and the event, or wraps one through
        `functools.wraps`. Any other function is subscribed at once, wherever it is defined, a static method taking the
        event alone included, and so is a method already bound to its instance. Either stays subscribed until it is
        passed to `unsubscribe`, held strongly, so it runs whether or not anything else keeps it alive.

        `priority` orders the handler among the event's handlers, highest first and then in subscription order, and
        among a turn's: every handler of one priority is handed each event of the turn it selects, in the turn's order,
        before any handler of the next. The handler runs on every event unless held back: `min_interval` waits until
        that long has passed since it last ran, `at_time` runs it once, on the first event at or after that time, and
        `once` on the first event. `min_interval` with either of the others, or not above zero, raises `ValueError`,
        and either of the two not a `GameTime`, `TypeError`. These count only the events selected, and start afresh
        each game, as does a handler that returned `Done`. An exception it raises ends the game, unless
        `catch_exceptions`, which logs it and goes on.

        The handler must take an event of `event_type`, which a type checker checks. One decorated for several event
        types takes an `Event`.
        """
        return self._events.on(event_type, **options)

    def subscribe(self, instance: object) -> None:
        """Subscribe every method of `instance` marked with `on`, until `instance` is passed to `unsubscribe`.

        An unmarked override leaves the method it overrides unsubscribed. Raises `ValueError` if `instance` has no
        marked method, or one of them is subscribed for it already, a method subscribed bound through `on` aside; and
        `TypeError` if a marked function is under a `classmethod` or a `staticmethod`.
        """
        self._events.subscribe(instance)

    def unsubscribe(self, target: object) -> None:
        """Unsubscribe `target`: a function, a bound method, or every method of an instance.

        Raises `ValueError` if nothing of it is subscribed.
        """
        self._events.unsubscribe(target)

    def emit(self, event: Event) -> None:
        """Hand `event` to every handler of its type or a base type that selects it and is due to run. All have returned
        when this returns. A handler may itself emit an event.

        An event made without a time is handed out as a copy with the current time, and the one passed in keeps none,
        so it can be emitted again. Raises `ValueError` for one when no game is being played.
        """
        self._events.emit(event)

    @property
    def handler_timings(self) -> Mapping[type[Event], Mapping[str, HandlerTiming]]:
        """How long each handler has taken in the current game, or the last one played, by event type and handler name.

        A handler is named by its module and qualified name, so every instance of a class counts under one name. Raises
        `RuntimeError` unless the api was made with `time_handlers=True`.
        """
        return self._events.timings

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
        """Every place on the map to build a townhall, as the game began, by pathing distance from this player's start,
        nearest first: its own start, then its natural. Those no path reaches come last, sorted by increasing
        straight-line distance from this player's start."""
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
    def time(self) -> GameTime:
        """The time of the last observation: `step` as a `GameTime`."""
        return self._current_game().time

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
        """Every unit in the last observation, including remembered structures out of sight and hidden units. In a
        replay played back with the fog off, those the observation would have listed with the fog on."""
        return self._current_game().tracker.unit_tracker.present

    @property
    def all_units(self) -> Units[Unit[Any]]:
        """Every unit of a replay played back with the fog off, as the game lists it then: those of `units`, under the
        same ids, and those this player did not see. Each reads as visible, in full, but one undetected in this
        player's sight, whose health and the like the game reports as 0 (in game): it reads as last reported in full.

        Raises `FogOnError` in a game, and in a replay played back with the fog on.
        """
        if (whole_game_tracker := self._current_game().whole_game_tracker) is None:
            raise FogOnError("the fog is on, so the game reports only what this player sees")
        return whole_game_tracker.unit_tracker.present

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
        make, until the structure stands. A reactor's two marines are two. The turn's orders are not among them; they
        are in `api.orders.issued_to`. Takes the types as `Units.of_type` does.
        """
        wanted = frozenset((types,)) if isinstance(types, UnitTypeId) else _type_ids(types, unit_types)
        return self._current_game().state.production.of_types(wanted)

    @property
    def researches(self) -> Mapping[UpgradeId, Research]:
        """Each research this player has running or queued in the last observation, by upgrade, in the order the
        observation lists the structures and each its orders. A finished one is in `upgrades`."""
        return self._current_game().state.production.researches

    @property
    def action_failures(self) -> tuple[ActionFailure, ...]:
        """The orders the game refused when the last turn's were sent, one for each unit a refused command named, then
        those it accepted earlier and gave up on since the observation before.

        Raises `UncuratedIdError` if one the game gave up on names an ability the curated ids leave out.
        """
        game = self._current_game()
        return game.orders._refusals + game.state.action_failures

    @property
    def player(self) -> Player:
        """This player: its id, its race and the one it asked for, and its name."""
        return self._current_game().player

    @property
    def enemy(self) -> Enemy:
        """The other player, and what is known of it. Raises `NoEnemyError` in a game with no other player, such as a
        mini-game.

        `api.enemy.upgrades` holds the upgrades the bot added with `assume_upgrades` and those enemy units have shown,
        which NachOS reads as far as `enemy_upgrade_inference` says. Everything an upgrade changes on an enemy unit,
        `Unit.weapons` and `Unit.speed` among them, counts them.
        """
        if (enemy := self._current_game().enemy) is None:
            raise NoEnemyError("this game has no other player")
        return enemy

    @property
    def vision(self) -> Grid[bool]:
        """Where this player can see now, over the playable area like the map's grids."""
        return self._current_game().state.vision

    @property
    def explored(self) -> Grid[bool]:
        """Where this player has seen at any point in the game, over the playable area."""
        return self._current_game().state.explored

    @property
    def placement(self) -> PlacementGrids:
        """Where a structure may stand: on the map whatever happens in the game, and now as far as this player knows;
        and the ground this player's ordered builds are to stand on, and what it has reserved."""
        return self._current_game().placement

    @property
    def debug(self) -> DebugCommands:
        """Drawing on the game's screen, cheats and debug commands, sent after each turn's orders."""
        return self._current_game().debug

    @property
    def camera(self) -> Camera:
        """This player's camera: where it is centered, and moving it."""
        return self._current_game().camera

    @property
    def pathing(self) -> Pathing:
        """How units get around the map as it stands now: the turn's grid of where ground units walk, paths over it, and
        distances the game answers."""
        return self._current_game().pathing

    @property
    def creep(self) -> Grid[bool]:
        """Where creep covers the ground, over the playable area."""
        return self._current_game().state.creep

    @property
    def effects(self) -> tuple[Effect, ...]:
        """Every effect this player can see. Raises `UncuratedIdError` if one is an effect the curated ids leave out."""
        return self._current_game().state.effects

    def chat(self, text: str, *, team_only: bool = False) -> None:
        """Send `text` to every player, or to this player's team only if `team_only`, with the turn's orders: after
        them and the camera's move, each message of the turn in the order given. The game drops chat that comes too
        fast without a word, once some 500 bytes have gone out in a row, counting by the clock rather than in steps
        (in game).

        Raises `ValueError` for an empty text, and for one of 1040 bytes or more in UTF-8, which would kill the game
        client (in game).
        """
        game = self._current_game()
        if not text:
            raise ValueError("a chat message cannot be empty")
        if (size := len(text.encode())) > _MOST_CHAT_BYTES:
            raise ValueError(
                f"a chat message of {_MOST_CHAT_BYTES + 1} bytes or more kills the game client, and this one is {size}"
            )
        game.queue_chat(text, team_only=team_only)

    def leave_game(self) -> None:
        """Leave the game, conceding it, once the turn's handlers have run and its orders, chat and debug commands have
        gone out. `play` then returns `Result.DEFEAT`, as the protocol counts leaving a surrender: the game reports
        nothing to a client that has left, even when both players leave in the same step (in game). Leaving a replay
        stops its playback, and `play` returns `Result.UNDECIDED`. Asking again in the turn changes nothing.
        """
        self._current_game().leaving = True

    def play(
        self,
        client: Client,
        *,
        steps_per_turn: int = 1,
        realtime: bool = False,
        time_limit: GameTime | None = None,
        enemy_ladder_id: str | None = None,
        save_replay_to: Path | str | None = None,
    ) -> Result:
        """Play the game `client` has joined to its end, a turn every `steps_per_turn` steps, and return how it ended
        for this player.

        `run_local`, `run_ladder`, `run_replay` and `run_recording` call this. Call it directly to play a game connected
        some other way. `time_limit` calls a game that reaches it a tie, and stops a replay at it `Result.UNDECIDED`;
        one that is not a `GameTime` raises `TypeError` before the game starts. `realtime` must say how the game was
        created: a stepped game played in real time never moves, which raises `ValueError` on the first turn.
        `enemy_ladder_id` is the ladder's id for the opponent, which `enemy.ladder_id` reads.

        `save_replay_to` is a file to save the game's replay to, however the game ends: at its end, at the time
        limit, before leaving it for `leave_game`, and after a handler raises. A save that fails is logged, never
        raised. Before the game starts, a `save_replay_to` in no folder that exists raises `FileNotFoundError`, and one
        that names a folder `IsADirectoryError`.

        Each call starts from nothing, so one api plays any number of games in a row. Handlers stay subscribed from
        one game to the next; what each has done starts afresh.
        """
        if time_limit is not None:
            check_game_time(time_limit, "time_limit")
        replay_path = None if save_replay_to is None else Path(save_replay_to)
        if replay_path is not None:
            check_file_to_write(replay_path, "save the replay into")
        if self._game is not None:
            self._game.end()
        game = _Game.start(
            client, enemy_upgrade_inference=self._enemy_upgrade_inference, enemy_ladder_id=enemy_ladder_id
        )
        self._game = game
        logger.info("Playing {} at {} steps a turn", game.game_map.name, steps_per_turn)
        events = self._events
        events._start_game()
        events._set_time(game.time)
        try:
            result = self._play_saving_the_replay(
                game,
                steps_per_turn=steps_per_turn,
                realtime=realtime,
                time_limit=time_limit,
                save_replay_to=replay_path,
            )
            if game.leaving and client.in_game:
                logger.info("Leaving the game")
                client.leave_game()
            events.emit(GameEndEvent(result, time=game.time))
        finally:
            # Ended, or ended by a handler that raised: no game is being played to give an event its time.
            events._set_time(None)
        return result

    def _play_saving_the_replay(
        self,
        game: _Game,
        *,
        steps_per_turn: int,
        realtime: bool,
        time_limit: GameTime | None,
        save_replay_to: Path | None,
    ) -> Result:
        """Start `game` and take its turns until it ends, or the bot asks to leave it, and return how it ended. Then,
        however the turns ended, save its replay to `save_replay_to` if given, while the client is still in the game."""
        try:
            self._events.emit(GameStartEvent(time=game.time))
            return self._play_turns(game, steps_per_turn=steps_per_turn, realtime=realtime, time_limit=time_limit)
        finally:
            if save_replay_to is not None:
                _save_replay(game.client, save_replay_to)

    def _play_turns(self, game: _Game, *, steps_per_turn: int, realtime: bool, time_limit: GameTime | None) -> Result:
        """Take the turns of `game` until it ends or the bot asks to leave it, and return how it ended."""
        events, client = self._events, game.client
        while (result := game.outcome()) is None:
            if time_limit is not None and game.time >= time_limit:
                if client.in_replay:
                    logger.info("Stopping the replay at its {:.0f} second limit", time_limit.seconds)
                    return game.finish(Result.UNDECIDED)
                logger.info("Calling the game a tie at its {:.0f} second limit", time_limit.seconds)
                return game.finish(Result.TIE)

            events.emit(TurnStartEvent(time=game.time))
            events._hand_out([*game.report(events), TurnEvent(time=game.time)])
            try:
                # The turn's handlers have all returned, so what they asked for goes out now.
                game.send_turn()
                if game.leaving:
                    # Left once the replay is saved, which the game gives no client that has left (in game).
                    return game.finish(Result.UNDECIDED if client.in_replay else Result.DEFEAT)
                if not realtime:
                    client.step(steps_per_turn)
            except GameEndedError:
                # The game ended while the handlers ran, as a realtime game can. Its last observation says how.
                game.observe()
            else:
                if realtime:
                    # A realtime game runs on its own, so each turn asks for the step it wants.
                    _observe_realtime(game, game.step + steps_per_turn)
                else:
                    game.observe()
            events._set_time(game.time)
        return result


def _save_replay(client: Client, path: Path) -> None:
    """Save the replay of the game `client` is in to `path`, logging rather than raising if the game gives none or the
    file cannot be written."""
    try:
        path.write_bytes(client.save_replay())
    except (NachOSError, OSError) as error:
        logger.warning("No replay was saved to {}: {}", path, error)
    else:
        logger.info("Saved the replay to {}", path)


def _observe_realtime(game: _Game, step: int) -> None:
    """Observe a realtime `game` once it reaches `step`. Raises `ValueError` if a game still in progress answers short
    of it, as a game created stepped does."""
    game.observe(step)
    if game.client.in_game and game.step < step:
        raise ValueError(
            f"the game answered at step {game.step} when asked for step {step}, as only a stepped game does: "
            "play it with realtime=False"
        )
