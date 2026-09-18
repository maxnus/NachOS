"""Handing events to the handlers subscribed to them, driven by hand rather than by a game."""

# Each `type: ignore` below marks a handler a type checker must refuse, so one that is not needed fails.
# pyright: reportUnnecessaryTypeIgnoreComment=true

import functools
import gc
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property

import pytest
from loguru import logger

from sc2nachos.events import (
    Done,
    Event,
    EventBus,
    EventPriority,
    GameEndEvent,
    GameStartEvent,
    TurnEvent,
    TurnStartEvent,
)
from sc2nachos.match import Result


def _turns(bus: EventBus, *steps: int) -> None:
    """A turn at each of `steps`."""
    for step in steps:
        bus._emit(TurnEvent(step))


@pytest.fixture
def logged() -> Iterator[list[str]]:
    """What is logged while the test runs."""
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    yield messages
    logger.remove(sink)


# Defined at module level, as a bot's would be, so that they are functions and a class of the module's own.
_MODULE_BUS = EventBus()
_module_calls: list[int] = []


@_MODULE_BUS.on(TurnEvent)
def _on_turn(event: TurnEvent) -> None:
    _module_calls.append(event.step)


class _ModuleHandlers:
    def __init__(self) -> None:
        self.calls: list[int] = []
        _MODULE_BUS.subscribe(self)

    @_MODULE_BUS.on(TurnEvent)
    def on_turn(self, event: TurnEvent) -> None:
        self.calls.append(event.step)


class TestSubscribing:
    def test_a_module_level_function_is_subscribed_as_it_is_defined(self) -> None:
        _module_calls.clear()
        _turns(_MODULE_BUS, 3)
        assert _module_calls == [3]

    def test_a_method_of_a_module_level_class_is_marked_and_each_instance_subscribes_it(self) -> None:
        first, second = _ModuleHandlers(), _ModuleHandlers()
        _turns(_MODULE_BUS, 5)
        assert first.calls == second.calls == [5]
        _MODULE_BUS.unsubscribe(first)
        _MODULE_BUS.unsubscribe(second)

    def test_a_nested_function_and_a_lambda_are_subscribed_as_they_are_defined(self) -> None:
        bus, calls = EventBus(), []

        @bus.on(TurnEvent)
        def nested(event: TurnEvent) -> None:
            calls.append("nested")

        bus.on(TurnEvent)(lambda event: calls.append("lambda"))
        _turns(bus, 0)
        assert calls == ["nested", "lambda"]

    def test_a_method_already_bound_to_its_instance_is_subscribed_at_once(self) -> None:
        class Plain:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def on_turn(self, event: TurnEvent) -> None:
                self.calls.append(event.step)

        bus, instance = EventBus(), Plain()
        bus.on(TurnEvent)(instance.on_turn)
        _turns(bus, 7)
        assert instance.calls == [7]

    def test_a_method_in_a_class_body_waits_for_its_instance_to_subscribe(self) -> None:
        bus, calls = EventBus(), []

        class Handlers:
            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                calls.append(event.step)

        handlers = Handlers()
        _turns(bus, 0)
        assert calls == []
        bus.subscribe(handlers)
        _turns(bus, 1)
        assert calls == [1]

    def test_stacked_decorators_subscribe_to_each_event(self) -> None:
        bus, calls = EventBus(), []

        @bus.on(TurnStartEvent)
        @bus.on(TurnEvent)
        def both(event: Event) -> None:
            calls.append(type(event))

        bus._emit(TurnStartEvent(0))
        bus._emit(TurnEvent(0))
        assert calls == [TurnStartEvent, TurnEvent]

    def test_nothing_is_wanted_until_something_subscribes(self) -> None:
        bus = EventBus()
        assert not bus._has_handlers(TurnEvent)
        bus.on(TurnEvent)(lambda event: None)
        assert bus._has_handlers(TurnEvent)
        assert not bus._has_handlers(TurnStartEvent)

    def test_nothing_is_wanted_once_every_handler_is_done_until_the_next_game(self) -> None:
        bus = EventBus()
        bus.on(TurnEvent, once=True)(lambda event: None)
        bus.on(TurnEvent)(lambda event: Done)
        bus._emit(TurnEvent(0))
        assert not bus._has_handlers(TurnEvent)
        bus._start_game()
        assert bus._has_handlers(TurnEvent)

    def test_a_coroutine_function_is_refused(self) -> None:
        """NachOS calls its handlers synchronously, so one would only make a coroutine nobody awaits."""

        async def later(event: TurnEvent) -> None:
            pass

        with pytest.raises(TypeError, match="later is a coroutine function"):
            EventBus().on(TurnEvent)(later)

    def test_only_a_function_or_a_method_can_handle_an_event(self) -> None:
        with pytest.raises(TypeError, match="a handler is a function or a method"):
            EventBus().on(TurnEvent)(functools.partial(print))

    def test_only_an_event_type_can_be_subscribed_to(self) -> None:
        with pytest.raises(TypeError, match="a handler subscribes to an event type"):
            EventBus().on(int)  # type: ignore

    @pytest.mark.parametrize(
        ("every_steps", "at_step", "message"),
        [(0, None, "every_steps is at least 1"), (4, 100, "not both")],
    )
    def test_a_cadence_that_cannot_be_kept_is_refused(
        self, every_steps: int, at_step: int | None, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            EventBus().on(TurnEvent, every_steps=every_steps, at_step=at_step)


class TestOrder:
    def test_the_highest_priority_runs_first_and_one_priority_in_the_order_it_subscribed(self) -> None:
        bus, calls = EventBus(), []
        for name, priority in [
            ("low", EventPriority.LOW),
            ("highest", EventPriority.HIGHEST),
            ("high", EventPriority.HIGH),
            ("medium", EventPriority.MEDIUM),
        ]:
            bus.on(TurnEvent, priority=priority)(lambda event, name=name: calls.append(name))
        bus.on(TurnEvent, priority=EventPriority.HIGHEST)(lambda event: calls.append("highest again"))
        bus.on(TurnEvent, priority=EventPriority.LOWEST)(lambda event: calls.append("lowest"))
        _turns(bus, 0)
        assert calls == ["highest", "highest again", "high", "medium", "low", "lowest"]

    def test_only_the_handlers_of_the_events_own_type_run(self) -> None:
        bus, calls = EventBus(), []
        bus.on(TurnEvent)(lambda event: calls.append("turn"))
        bus._emit(TurnStartEvent(0))
        bus._emit(GameStartEvent(0))
        assert calls == []


class TestCadence:
    def _fired(self, bus: EventBus, *steps: int, **cadence: int | bool) -> list[int]:
        fired: list[int] = []
        bus.on(TurnEvent, **cadence)(lambda event: fired.append(event.step))  # type: ignore
        _turns(bus, *steps)
        return fired

    def test_every_steps_at_a_step_a_turn_fires_on_its_multiples(self) -> None:
        """What `step % 4 == 0` picks, since the first turn is step 0."""
        assert self._fired(EventBus(), *range(13), every_steps=4) == [0, 4, 8, 12]

    def test_every_steps_never_fires_closer_together_than_asked(self) -> None:
        """At three steps a turn, four steps apart is six."""
        assert self._fired(EventBus(), *range(0, 25, 3), every_steps=4) == [0, 6, 12, 18, 24]

    def test_a_handler_subscribed_part_way_through_a_game_counts_from_its_first_event(self) -> None:
        bus = EventBus()
        _turns(bus, *range(990, 1001))
        assert self._fired(bus, *range(1001, 1012), every_steps=8) == [1001, 1009]

    def test_at_step_fires_once_on_the_first_turn_at_or_after_it(self) -> None:
        assert self._fired(EventBus(), 498, 499, 501, 502, 503, at_step=500) == [501]

    def test_at_step_already_past_fires_on_the_next_turn(self) -> None:
        assert self._fired(EventBus(), 900, 901, at_step=500) == [900]

    def test_once_fires_on_the_first_turn_only(self) -> None:
        assert self._fired(EventBus(), 3, 4, 5, once=True) == [3]


class TestStopping:
    def test_a_handler_that_returns_done_is_called_no_more_this_game(self) -> None:
        bus, fired = EventBus(), []

        @bus.on(TurnEvent)
        def until_two(event: TurnEvent) -> type[Done] | None:
            fired.append(event.step)
            return Done if event.step >= 2 else None

        _turns(bus, 0, 1, 2, 3, 4)
        assert fired == [0, 1, 2]

    def test_any_other_return_value_is_ignored(self) -> None:
        """A truthy value returned by accident must not stop a handler without a word."""
        bus, fired = EventBus(), []

        @bus.on(TurnEvent)
        def truthy(event: TurnEvent) -> bool:
            fired.append(event.step)
            return True

        _turns(bus, 0, 1)
        assert fired == [0, 1]

    def test_done_is_returned_itself(self) -> None:
        with pytest.raises(TypeError, match="returns `Done` itself"):
            Done()

    def test_a_new_game_starts_what_every_handler_has_done_afresh(self) -> None:
        bus, fired = EventBus(), []
        bus.on(TurnEvent, once=True)(lambda event: fired.append(("once", event.step)))
        bus.on(TurnEvent, at_step=4)(lambda event: fired.append(("at_step", event.step)))
        bus.on(TurnEvent, every_steps=100)(lambda event: fired.append(("every_steps", event.step)))

        @bus.on(TurnEvent)
        def done(event: TurnEvent) -> type[Done]:
            fired.append(("done", event.step))
            return Done

        for _ in range(2):
            bus._start_game()
            _turns(bus, 0, 4, 8)
        assert fired == 2 * [("once", 0), ("every_steps", 0), ("done", 0), ("at_step", 4)]


class TestUnsubscribing:
    def test_a_function(self) -> None:
        bus, fired = EventBus(), []

        @bus.on(TurnEvent)
        def handler(event: TurnEvent) -> None:
            fired.append(event.step)

        _turns(bus, 0)
        bus.unsubscribe(handler)
        _turns(bus, 1)
        assert fired == [0]
        assert not bus._has_handlers(TurnEvent)

    def test_one_method_of_an_instance_or_all_of_them(self) -> None:
        bus = EventBus()

        class Handlers:
            def __init__(self) -> None:
                self.calls: list[str] = []
                bus.subscribe(self)

            @bus.on(TurnEvent)
            def turn(self, event: TurnEvent) -> None:
                self.calls.append("turn")

            @bus.on(TurnStartEvent)
            def start(self, event: TurnStartEvent) -> None:
                self.calls.append("start")

        mine, other = Handlers(), Handlers()
        bus.unsubscribe(mine.turn)
        bus._emit(TurnStartEvent(0))
        _turns(bus, 0)
        assert mine.calls == ["start"]
        bus.unsubscribe(other)
        bus._emit(TurnStartEvent(1))
        _turns(bus, 1)
        assert other.calls == ["start", "turn"]
        assert mine.calls == ["start", "start"]

    def test_what_is_not_subscribed_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is subscribed"):
            EventBus().unsubscribe(_on_turn)

    def test_a_handler_unsubscribed_while_an_event_is_handed_out_is_not_handed_it(self) -> None:
        bus, fired = EventBus(), []

        def second(event: TurnEvent) -> None:
            fired.append("second")

        bus.on(TurnEvent)(lambda event: bus.unsubscribe(second))
        bus.on(TurnEvent)(second)
        _turns(bus, 0)
        assert fired == []

    def test_a_handler_subscribed_while_an_event_is_handed_out_waits_for_the_next(self) -> None:
        bus, fired = EventBus(), []

        @bus.on(TurnEvent, once=True)
        def subscribing(event: TurnEvent) -> None:
            bus.on(TurnEvent)(lambda event: fired.append(event.step))

        _turns(bus, 0, 1)
        assert fired == [1]


class TestInstances:
    def test_an_instance_is_held_until_it_is_unsubscribed(self) -> None:
        """So its handlers run whether or not the bot kept it, and when they stop is the bot's to say."""
        bus, calls = EventBus(), []

        class Handlers:
            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                calls.append(event.step)

        handlers = Handlers()
        reference = weakref.ref(handlers)
        bus.subscribe(handlers)
        del handlers
        gc.collect()
        _turns(bus, 0)
        assert calls == [0]
        instance = reference()
        assert instance is not None
        bus.unsubscribe(instance)
        del instance
        gc.collect()
        assert reference() is None

    def test_an_instance_is_subscribed_once(self) -> None:
        bus = EventBus()

        class Handlers:
            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                pass

        handlers = Handlers()
        bus.subscribe(handlers)
        with pytest.raises(ValueError, match="subscribed already"):
            bus.subscribe(handlers)

    def test_what_the_bus_holds_reads_as_its_event_types(self) -> None:
        bus = EventBus()
        assert repr(bus) == "EventBus()"
        bus.on(TurnEvent)(lambda event: None)
        bus.on(TurnEvent)(lambda event: None)
        bus.on(GameEndEvent)(lambda event: None)
        assert repr(bus) == "EventBus(TurnEvent: 2, GameEndEvent: 1)"

    def test_a_handler_reads_as_its_name_event_and_how_often_it_runs(self) -> None:
        bus = EventBus()

        @bus.on(TurnEvent, priority=EventPriority.LOW, every_steps=16, catch_exceptions=True)
        def planned(event: TurnEvent) -> None:
            pass

        @bus.on(GameStartEvent, once=True)
        def started(event: GameStartEvent) -> None:
            pass

        assert repr(bus._handlers[TurnEvent][0]).endswith(
            ".planned, TurnEvent, priority=LOW, every_steps=16, catch_exceptions=True)"
        )
        assert repr(bus._handlers[GameStartEvent][0]).endswith(".started, GameStartEvent, priority=MEDIUM, once=True)")

    def test_an_instance_without_a_marked_method_is_refused(self) -> None:
        """Most likely its methods were marked with another api's `on`, or not at all."""
        with pytest.raises(ValueError, match="object has no method marked with `on`"):
            EventBus().subscribe(object())

    def test_a_subclass_subscribes_what_it_marks_and_what_it_inherits(self) -> None:
        bus = EventBus()

        class Base:
            def __init__(self) -> None:
                self.calls: list[str] = []
                bus.subscribe(self)

            @bus.on(TurnEvent)
            def base(self, event: TurnEvent) -> None:
                self.calls.append("base")

        class Derived(Base):
            @bus.on(TurnEvent)
            def derived(self, event: TurnEvent) -> None:
                self.calls.append("derived")

        instance = Derived()
        _turns(bus, 0)
        assert sorted(instance.calls) == ["base", "derived"]

    def test_an_override_that_is_not_marked_hides_the_marked_method(self) -> None:
        bus = EventBus()

        class Base:
            def __init__(self) -> None:
                self.calls: list[str] = []

            @bus.on(TurnEvent)
            def handle(self, event: TurnEvent) -> None:
                self.calls.append("base")

            @bus.on(TurnStartEvent)
            def start(self, event: TurnStartEvent) -> None:
                self.calls.append("start")

        class Derived(Base):
            def handle(self, event: TurnEvent) -> None:
                self.calls.append("derived")

        instance = Derived()
        bus.subscribe(instance)
        _turns(bus, 0)
        assert instance.calls == []

    def test_finding_the_handlers_evaluates_no_property(self) -> None:
        bus, evaluated = EventBus(), []

        class Handlers:
            @cached_property
            def expensive(self) -> int:
                evaluated.append("expensive")
                return 1

            @property
            def live(self) -> int:
                evaluated.append("live")
                return 2

            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                pass

        bus.subscribe(Handlers())
        _turns(bus, 0)
        assert evaluated == []

    def test_a_dataclass_and_a_slotted_class_subscribe_like_any_other(self) -> None:
        bus, calls = EventBus(), []

        @dataclass
        class Data:
            name: str

            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                calls.append(self.name)

        class Slotted:
            __slots__ = ()

            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                calls.append("slotted")

        bus.subscribe(Data("data"))
        bus.subscribe(Slotted())
        _turns(bus, 0)
        assert calls == ["data", "slotted"]


class TestTyping:
    def test_a_handler_of_another_event_is_refused_by_a_type_checker(self) -> None:
        bus = EventBus()

        @bus.on(TurnEvent)  # type: ignore
        def wrong(event: GameEndEvent) -> None:
            pass

        class Handlers:
            @bus.on(TurnEvent)  # type: ignore
            def wrong(self, event: GameEndEvent) -> None:
                pass

        assert Handlers

    def test_a_handler_of_several_events_takes_any_event(self) -> None:
        bus = EventBus()

        @bus.on(TurnStartEvent)
        @bus.on(TurnEvent)
        def any_event(event: Event) -> None:
            pass

        @bus.on(TurnStartEvent)  # type: ignore
        @bus.on(TurnEvent)
        def some_events(event: TurnStartEvent | TurnEvent) -> None:
            pass

        def called(event: TurnStartEvent | TurnEvent) -> None:
            pass

        bus.on(TurnStartEvent)(called)
        bus.on(TurnEvent)(called)
        assert len(bus._handlers[TurnEvent]) == 3


class TestExceptions:
    def test_an_exception_a_handler_raises_goes_on_up(self) -> None:
        bus = EventBus()

        @bus.on(TurnEvent)
        def failing(event: TurnEvent) -> None:
            raise RuntimeError("the handler failed")

        with pytest.raises(RuntimeError, match="the handler failed"):
            _turns(bus, 0)

    def test_one_caught_is_logged_and_the_next_handler_still_runs(self, logged: list[str]) -> None:
        bus, fired = EventBus(), []

        @bus.on(TurnEvent, catch_exceptions=True)
        def failing(event: TurnEvent) -> None:
            raise RuntimeError("the handler failed")

        bus.on(TurnEvent)(lambda event: fired.append(event.step))
        _turns(bus, 0)
        assert fired == [0]
        assert any("failing raised on TurnEvent(step=0)" in message for message in logged)


class TestTimings:
    def test_handlers_are_not_timed_unless_asked(self) -> None:
        with pytest.raises(RuntimeError, match="time_handlers=True"):
            _ = EventBus().timings

    def test_every_call_is_counted_under_the_handlers_name(self) -> None:
        bus = EventBus(time_handlers=True)

        class Handlers:
            @bus.on(TurnEvent)
            def on_turn(self, event: TurnEvent) -> None:
                pass

        bus.subscribe(Handlers())
        bus.subscribe(Handlers())
        bus._start_game()
        _turns(bus, 0, 1, 2)
        (name, timings), *others = bus.timings[TurnEvent].items()
        assert not others
        assert name.endswith("Handlers.on_turn")
        assert timings.calls == 6
        assert 0 <= timings.max_seconds <= timings.total_seconds
        assert timings.seconds_per_call == pytest.approx(timings.total_seconds / 6)

    def test_a_new_game_starts_them_afresh(self) -> None:
        bus = EventBus(time_handlers=True)
        bus.on(GameEndEvent)(lambda event: None)
        bus._emit(GameEndEvent(10, Result.VICTORY))
        assert bus.timings[GameEndEvent]
        bus._start_game()
        assert not bus.timings
