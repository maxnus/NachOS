"""Drawing, cheats and debug commands: what goes out after each turn's orders, and when."""

import math
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from s2clientprotocol import debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, GameTime, NotPlayingError
from sc2nachos.debug import (
    Cheat,
    Color,
    DebugBox,
    DebugGroup,
    DebugLine,
    DebugMark,
    DebugSphere,
    DebugText,
    Drawing,
    Marker,
    _debug_commands,
)
from sc2nachos.events import GameStartEvent, TurnEvent
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Race, Result
from sc2nachos.protocol import Client, Status
from sc2nachos.run import run_local
from sc2nachos.units import Alliance, Visibility
from support import make_game_info, make_observation, make_response, make_unit

# The ground height of `make_game_info`'s flat map: a height byte of 128 is an eighth above 0.
_GROUND = 0.125
_VITALS = debug_pb2.DebugSetUnitValue.UnitValue
_ONE_STEP = GameTime(steps=1)


class _ScriptedGame:
    """A game that answers every request by its kind: an observation from `observations` in turn, the last ending the
    game in a victory, and success for every action."""

    def __init__(
        self, observations: Sequence[sc2api_pb2.ResponseObservation], *, players: int, observers: int = 0
    ) -> None:
        self.observations = deque(observations)
        self.requests: list[sc2api_pb2.Request] = []
        self.info = make_game_info(players=())
        for player in range(1, players + 1):
            self.info.player_info.add(player_id=player, type=sc2api_pb2.PlayerType.Participant)
        for observer in range(players + 1, players + observers + 1):
            self.info.player_info.add(player_id=observer, type=sc2api_pb2.PlayerType.Observer)

    def request(self, request: sc2api_pb2.Request) -> sc2api_pb2.Response:
        self.requests.append(request)
        match request.WhichOneof("request"):
            case "join_game":
                return make_response(join_game=sc2api_pb2.ResponseJoinGame(player_id=1))
            case "game_info":
                return make_response(game_info=self.info)
            case "data":
                return make_response(data=sc2api_pb2.ResponseData())
            case "observation":
                observation = self.observations.popleft()
                if not self.observations:
                    observation.player_result.add(player_id=1, result=Result.VICTORY.value)
                return make_response(Status.ENDED if not self.observations else Status.IN_GAME, observation=observation)
            case "step":
                return make_response(step=sc2api_pb2.ResponseStep())
            case "action":
                results = [error_pb2.Success] * len(request.action.actions)
                return make_response(action=sc2api_pb2.ResponseAction(result=results))
            case "debug":
                return make_response(debug=sc2api_pb2.ResponseDebug())
            case other:
                raise AssertionError(f"the test game was asked for {other}")

    def close(self) -> None: ...


def _play(
    api: Api,
    turn: Callable[[int], object],
    *,
    turns: int,
    refresh_interval: GameTime = _ONE_STEP,
    units: Callable[[int], Sequence[raw_pb2.Unit]] = lambda _: (),
    dead: Callable[[int], Sequence[int]] = lambda _: (),
    players: int = 2,
    observers: int = 0,
) -> dict[int, list[debug_pb2.DebugCommand]]:
    """Play `turns` turns of one step with `api`, calling `turn` with each turn's step, each step's observation
    reporting `units` and the tags `dead`, with the drawing refreshed every `refresh_interval`, and return the debug
    commands sent, by the step of the turn that sent them."""
    observations = [make_observation(step, units=units(step), dead=dead(step)) for step in range(turns + 1)]
    game = _ScriptedGame(observations, players=players, observers=observers)
    client = Client(game)
    client.join_game(Race.TERRAN)

    @api.on(GameStartEvent)
    def on_start(event: GameStartEvent) -> None:
        api.debug.refresh_interval = refresh_interval

    @api.on(TurnEvent)
    def on_turn(event: TurnEvent) -> None:
        turn(api.step)

    api.play(client)
    api.unsubscribe(on_start)
    api.unsubscribe(on_turn)
    # A turn's debug request follows its observation, the steps counting from 0.
    sent: dict[int, list[debug_pb2.DebugCommand]] = {}
    step = -1
    for request in game.requests:
        if request.HasField("observation"):
            step += 1
        elif request.HasField("debug"):
            sent[step] = list(request.debug.debug)
    return sent


def _texts(commands: Sequence[debug_pb2.DebugCommand]) -> list[str]:
    """The text the drawing among `commands` writes."""
    (draw,) = [command.draw for command in commands if command.HasField("draw")]
    return [text.text for text in draw.text]


class TestDrawing:
    def test_what_a_turn_draws_goes_out_after_its_orders_as_one_debug_request(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.orders.issue(api.units.own[0], AbilityId.MOVE, target=(5.0, 5.0))
                api.debug.draw((2.0, 3.0), DebugText("here"))

        observations = [make_observation(step, units=[make_unit(1)]) for step in range(3)]
        game = _ScriptedGame(observations, players=2)
        client = Client(game)
        client.join_game(Race.TERRAN)
        api.on(TurnEvent)(lambda event: turn(event.time.steps))
        api.play(client)

        order = [request.WhichOneof("request") for request in game.requests]
        assert order[order.index("action") + 1] == "debug"
        # The second clears the drawing, its one step over.
        drawn, cleared = [request.debug for request in game.requests if request.HasField("debug")]
        assert list(cleared.debug) == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]
        (command,) = drawn.debug
        (text,) = command.draw.text
        assert (text.text, text.world_pos.x, text.world_pos.y, text.world_pos.z) == ("here", 2.0, 3.0, _GROUND)
        assert (text.color.r, text.color.g, text.color.b, text.size) == (255, 255, 255, 8)

    def test_a_fixed_drawing_is_sent_when_made_and_cleared_once_its_steps_have_passed(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw(
                    (1.0, 1.0), DebugLine(to=(2.0, 2.0, 5.0)), color=(255, 0, 0), shown_for=GameTime(steps=3)
                )

        sent = _play(api, turn, turns=6)

        assert list(sent) == [0, 3]
        (line,) = sent[0][0].draw.lines
        assert ((line.line.p0.z, line.line.p1.z), line.color.r) == ((_GROUND, 5.0), 255)
        assert sent[3] == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]

    def test_a_drawing_made_again_unchanged_every_turn_is_sent_once(self) -> None:
        api = Api()
        sent = _play(api, lambda step: api.debug.draw((1.0, 1.0), DebugText("fixed")), turns=5)
        assert list(sent) == [0]

    def test_with_a_refresh_every_four_steps_a_drawing_that_changes_every_turn_goes_out_every_fourth_step(self) -> None:
        api = Api()
        draw = lambda step: api.debug.draw_text_on_screen(f"step {step}", at=(0.1, 0.1))  # noqa: E731
        sent = _play(api, draw, turns=9, refresh_interval=GameTime(steps=4))
        assert {step: _texts(commands) for step, commands in sent.items()} == {
            0: ["step 0"],
            4: ["step 4"],
            8: ["step 8"],
        }

    def test_a_cheat_between_refreshes_goes_out_at_once_with_the_drawing_as_it_stands(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.draw((1.0, 1.0), DebugText(f"step {step}"))
            if step == 2:
                api.debug.turn_on(Cheat.GOD)

        sent = _play(api, turn, turns=7, refresh_interval=GameTime(steps=4))

        # The cheat's request is a refresh too, so the next comes 4 steps after it.
        assert list(sent) == [0, 2, 6]
        assert sent[2][0] == debug_pb2.DebugCommand(game_state=debug_pb2.god)
        assert _texts(sent[2]) == ["step 2"]

    def test_an_unchanged_drawing_is_looked_at_once_a_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        looked_at: list[int] = []
        draw = _debug_commands._draw_all

        def counted(drawings: Sequence[Drawing]) -> debug_pb2.DebugDraw:
            looked_at.append(len(drawings))
            return draw(drawings)

        monkeypatch.setattr(_debug_commands, "_draw_all", counted)
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((1.0, 1.0), DebugText("fixed"), shown_for=GameTime(steps=100))

        sent = _play(api, turn, turns=9, refresh_interval=GameTime(steps=4))

        assert list(sent) == [0]
        assert len(looked_at) == 3

    def test_a_command_with_nothing_drawn_leaves_the_refreshes_to_their_clock(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 1:
                api.debug.give_minerals()
            if step == 2:
                api.debug.draw((1.0, 1.0), DebugText("late"), shown_for=GameTime(steps=10))

        sent = _play(api, turn, turns=6, refresh_interval=GameTime(steps=4))

        assert list(sent) == [1, 4]
        assert sent[1] == [debug_pb2.DebugCommand(game_state=debug_pb2.minerals)]
        assert _texts(sent[4]) == ["late"]

    def test_a_drawing_at_a_unit_follows_it(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw(api.units.own[0], DebugSphere(1.5), shown_for=GameTime(steps=10))

        sent = _play(api, turn, turns=3, units=lambda step: [make_unit(1, at=(2.0 + step, 2.0))])

        centers = {
            step: [(sphere.p.x, sphere.p.y, sphere.r) for sphere in commands[0].draw.spheres]
            for step, commands in sent.items()
        }
        assert centers == {0: [(2.0, 2.0, 1.5)], 1: [(3.0, 2.0, 1.5)], 2: [(4.0, 2.0, 1.5)]}

    def test_a_drawing_at_a_unit_ends_once_the_unit_is_dead(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw(api.units.own[0], DebugText("doomed"), shown_for=GameTime(steps=10))

        sent = _play(
            api,
            turn,
            turns=4,
            units=lambda step: [make_unit(1)] if step < 2 else [],
            dead=lambda step: (1,) * (step == 2),
        )

        assert list(sent) == [0, 2]
        assert sent[2] == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]

    def test_a_drawing_at_a_unit_out_of_sight_stays_where_it_was_last_seen(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw(api.units.enemy[0], DebugText("scouted"), shown_for=GameTime(steps=10))

        enemy = make_unit(1, alliance=Alliance.ENEMY, at=(3.0, 3.0))
        sent = _play(api, turn, turns=4, units=lambda step: [enemy] if step < 2 else [])

        assert list(sent) == [0]

    def test_a_box_is_centered_on_its_place_and_stands_on_it(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((2.5, 4.0), DebugBox(3.0, 4.0))

        sent = _play(api, turn, turns=1)

        (box,) = sent[0][0].draw.boxes
        assert (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z) == (1, 2, _GROUND, 4, 6, _GROUND + 1)

    def test_a_game_that_draws_nothing_sends_no_debug_request(self) -> None:
        assert _play(Api(), lambda step: None, turns=3) == {}

    def test_a_new_game_forgets_the_last_ones_drawing(self) -> None:
        api = Api()
        _play(
            api,
            lambda step: api.debug.draw((1.0, 1.0), DebugText("first game"), shown_for=GameTime(steps=100)),
            turns=2,
        )
        assert _play(api, lambda step: None, turns=2) == {}

    @pytest.mark.parametrize(
        "draw",
        [
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x"), color=(256, 0, 0)),
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x"), color=(0.5, 0, 0)),
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x"), shown_for=GameTime(steps=0)),
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x"), shown_for=GameTime(steps=0.5)),
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x", size=0)),
            lambda api: api.debug.draw_text_on_screen("x", at=(1.5, 0.5)),
            lambda api: api.debug.draw_text_on_screen("x", at=(0.1, 0.1, 0.0)),  # pyright: ignore[reportArgumentType]
            lambda api: api.debug.draw((1.0, 1.0), DebugSphere(0)),
            lambda api: api.debug.draw((1.0, 1.0)),
            lambda api: api.debug.draw((1.0, 1.0), DebugMark(Marker.CROSS, radius=math.inf)),
            lambda api: api.debug.draw((1.0, 1.0), DebugText("x", offset=(1.0, 2.0))),  # pyright: ignore[reportArgumentType]
            lambda api: api.debug.draw((1.0, 1.0), DebugGroup()),
            lambda api: api.debug.draw((1.0, 1.0), DebugBox(1.0, 1.0, -1.0)),
        ],
        ids=[
            "color over 255",
            "color not whole",
            "shown for no steps",
            "shown for part of a step",
            "no size",
            "off the screen",
            "three coordinates on the screen",
            "no radius",
            "no shapes",
            "an infinite marker",
            "an offset of two",
            "an empty group",
            "a box below ground",
        ],
    )
    def test_a_drawing_given_what_cannot_be_drawn_raises(self, draw: Callable[[Api], None]) -> None:
        api = Api()
        raised: list[bool] = []

        def turn(step: int) -> None:
            with pytest.raises(ValueError):
                draw(api)
            raised.append(True)

        assert _play(api, turn, turns=1) == {}
        assert raised == [True]

    def test_a_drawing_shown_for_a_plain_number_raises(self) -> None:
        api = Api()
        raised: list[bool] = []

        def turn(step: int) -> None:
            with pytest.raises(TypeError, match="GameTime"):
                api.debug.draw((1.0, 1.0), DebugText("x"), shown_for=3)  # pyright: ignore[reportArgumentType]
            raised.append(True)

        _play(api, turn, turns=1)
        assert raised == [True]

    def test_the_refresh_interval_is_set_in_a_game_and_must_be_a_whole_number_of_steps_at_least_one(self) -> None:
        api = Api()
        seen: list[GameTime] = []

        def turn(step: int) -> None:
            seen.append(api.debug.refresh_interval)
            for interval in (GameTime(steps=0), GameTime(steps=1.5), GameTime.from_seconds(1)):
                with pytest.raises(ValueError, match="whole number of steps"):
                    api.debug.refresh_interval = interval
            with pytest.raises(TypeError, match="GameTime"):
                api.debug.refresh_interval = 4  # pyright: ignore[reportAttributeAccessIssue]

        _play(api, turn, turns=1, refresh_interval=GameTime(steps=4))

        assert seen == [GameTime(steps=4)]

    def test_each_game_starts_refreshing_every_step(self) -> None:
        api = Api()
        _play(api, lambda step: None, turns=1, refresh_interval=GameTime(steps=4))
        seen: list[GameTime] = []
        _play(api, lambda step: seen.append(api.debug.refresh_interval), turns=1)
        assert seen == [GameTime(steps=1)]

    def test_a_drawing_lasts_until_it_is_removed(self) -> None:
        api = Api()
        drawings: list[Drawing] = []

        def turn(step: int) -> None:
            if step == 0:
                drawings.append(api.debug.draw((1.0, 1.0), DebugLine(to=(2.0, 2.0)), shown_for=None))
            if step == 5:
                drawings[0].remove()
                drawings[0].remove()

        sent = _play(api, turn, turns=8)

        assert list(sent) == [0, 5]
        assert sent[5] == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]

    def test_a_drawing_is_drawn_in_a_standard_color_or_any_other(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.draw((1.0, 1.0), DebugText("red"), color=Color.RED)
            api.debug.draw((1.0, 1.0), DebugText("teal"), color=(0, 128, 128))

        sent = _play(api, turn, turns=1)

        colors = [(text.color.r, text.color.g, text.color.b) for text in sent[0][0].draw.text]
        assert colors == [(255, 0, 0), (0, 128, 128)]

    def test_a_square_mark_has_its_sides_along_the_axes(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((4.0, 4.0), DebugMark(Marker.SQUARE, radius=math.sqrt(2)))

        sent = _play(api, turn, turns=1)

        lines = [(line.line.p0, line.line.p1) for line in sent[0][0].draw.lines]
        ends = [((round(a.x, 6), round(a.y, 6)), (round(b.x, 6), round(b.y, 6))) for a, b in lines]
        assert ends == [((3, 3), (5, 3)), ((5, 3), (5, 5)), ((5, 5), (3, 5)), ((3, 5), (3, 3))]
        assert {point.z for pair in lines for point in pair} == {_GROUND}

    @pytest.mark.parametrize(
        ("marker", "sides"), [(Marker.TRIANGLE, 3), (Marker.SQUARE, 4), (Marker.PENTAGON, 5), (Marker.HEXAGON, 6)]
    )
    def test_a_polygon_mark_has_its_sides_and_a_level_bottom(self, marker: Marker, sides: int) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((4.0, 4.0), DebugMark(marker, radius=2))

        sent = _play(api, turn, turns=1)

        corners = [(round(line.line.p0.x, 6), round(line.line.p0.y, 6)) for line in sent[0][0].draw.lines]
        assert len(corners) == sides
        lowest = min(y for _, y in corners)
        assert sum(1 for _, y in corners if y == lowest) == 2
        assert all(math.dist(corner, (4, 4)) == pytest.approx(2) for corner in corners)

    @pytest.mark.parametrize(
        ("shape", "ends"),
        [
            (Marker.PLUS, [((3, 4), (5, 4)), ((4, 3), (4, 5))]),
            (
                Marker.CROSS,
                [((3.292893, 3.292893), (4.707107, 4.707107)), ((3.292893, 4.707107), (4.707107, 3.292893))],
            ),
            (
                Marker.STAR,
                [
                    ((3.292893, 3.292893), (4.707107, 4.707107)),
                    ((3.292893, 4.707107), (4.707107, 3.292893)),
                    ((3, 4), (5, 4)),
                    ((4, 3), (4, 5)),
                ],
            ),
        ],
        ids=["plus", "cross", "star"],
    )
    def test_a_cross_mark_is_arms_reaching_its_radius(self, shape: Marker, ends: list[Any]) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((4.0, 4.0), DebugMark(shape, radius=1))

        sent = _play(api, turn, turns=1)

        lines = [(line.line.p0, line.line.p1) for line in sent[0][0].draw.lines]
        assert [((round(a.x, 6), round(a.y, 6)), (round(b.x, 6), round(b.y, 6))) for a, b in lines] == ends

    def test_a_marker_is_read_by_its_symbol(self) -> None:
        assert (Marker("x"), Marker("+"), Marker("*")) == (Marker.CROSS, Marker.PLUS, Marker.STAR)

    def test_a_group_offsets_and_colors_its_shapes_each_of_which_may_have_its_own(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                hp = DebugText("hp", offset=(0.0, 0.0, 2.0), color=Color.RED)
                api.debug.draw((4.0, 4.0), DebugGroup(DebugBox(1.0, 1.0), hp, offset=(0.0, 0.0, 1.0), color=Color.BLUE))

        sent = _play(api, turn, turns=1)

        draw = sent[0][0].draw
        (box,) = draw.boxes
        (text,) = draw.text
        assert (box.min.z, (box.color.r, box.color.g, box.color.b)) == (_GROUND + 1, (0, 0, 255))
        assert (text.world_pos.z, (text.color.r, text.color.g, text.color.b)) == (_GROUND + 3, (255, 0, 0))

    def test_a_shape_is_a_value_that_cannot_be_changed_once_made(self) -> None:
        group = DebugGroup(DebugBox(1.0, 1.0), color=Color.RED)
        assert group == DebugGroup(DebugBox(1.0, 1.0), color=Color.RED)
        assert len({group, DebugGroup(DebugBox(1.0, 1.0), color=Color.RED)}) == 1
        with pytest.raises(AttributeError):
            group.color = Color.BLUE  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(AttributeError):
            DebugText("x").offset = (0.0, 0.0, 1.0)  # pyright: ignore[reportAttributeAccessIssue]

    @pytest.mark.parametrize(
        "make",
        [
            lambda: DebugMark("x"),  # pyright: ignore[reportArgumentType]
            lambda: DebugText(5),  # pyright: ignore[reportArgumentType]
        ],
        ids=["a marker by its symbol", "text that is not a str"],
    )
    def test_a_shape_of_the_wrong_kind_raises_where_it_is_made(self, make: Callable[[], object]) -> None:
        with pytest.raises(TypeError):
            make()

    def test_screen_text_that_is_not_a_str_raises_where_it_is_drawn(self) -> None:
        api = Api()
        raised: list[bool] = []

        def turn(step: int) -> None:
            with pytest.raises(TypeError):
                api.debug.draw_text_on_screen(5, at=(0.1, 0.1))  # pyright: ignore[reportArgumentType]
            raised.append(True)

        assert _play(api, turn, turns=1) == {}
        assert raised == [True]

    def test_a_color_and_an_offset_given_as_lists_are_kept_as_tuples(self) -> None:
        color, offset = [255, 0, 0], [0, 0, 1]
        text = DebugText("x", color=color, offset=offset)  # pyright: ignore[reportArgumentType]
        group = DebugGroup(text, color=color, offset=offset)  # pyright: ignore[reportArgumentType]
        color[0], offset[2] = 7, 9
        assert (text.color, text.offset, text._rgb) == ((255, 0, 0), (0.0, 0.0, 1.0), (255, 0, 0))
        assert (group.color, group.offset) == ((255, 0, 0), (0.0, 0.0, 1.0))
        assert len({text, DebugText("x", color=(255, 0, 0), offset=(0.0, 0.0, 1.0))}) == 1
        hash(group)

    def test_one_figure_is_drawn_at_several_units(self) -> None:
        api = Api()
        figure = DebugGroup(DebugMark(Marker.PLUS), DebugText("target", offset=(0.0, 0.0, 1.0)))

        def turn(step: int) -> None:
            if step == 0:
                for unit in api.units.own:
                    api.debug.draw(unit, figure)

        units = [make_unit(1, at=(2.0, 2.0)), make_unit(2, at=(6.0, 6.0))]
        sent = _play(api, turn, turns=1, units=lambda step: units)

        assert [(text.world_pos.x, text.world_pos.y) for text in sent[0][0].draw.text] == [(2.0, 2.0), (6.0, 6.0)]

    def test_a_line_to_a_unit_follows_it_and_ends_once_it_is_dead(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw((1.0, 1.0), DebugLine(to=api.units.own[0], to_offset=(0.0, 0.0, 1.0)), shown_for=None)

        sent = _play(
            api,
            turn,
            turns=4,
            units=lambda step: [make_unit(1, at=(2.0 + step, 2.0))] if step < 2 else [],
            dead=lambda step: (1,) * (step == 2),
        )

        ends = {
            step: [(line.line.p1.x, line.line.p1.z) for line in commands[0].draw.lines]
            for step, commands in sent.items()
        }
        assert ends == {0: [(2.0, 1.0)], 1: [(3.0, 1.0)], 2: []}

    def test_shapes_with_no_color_or_size_take_the_defaults_as_drawn(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.default_color = Color.ORANGE
                api.debug.default_text_size = 12
                api.debug.draw((1.0, 1.0), DebugText("defaults"))
                with pytest.raises(ValueError):
                    api.debug.default_text_size = 0
                with pytest.raises(ValueError):
                    api.debug.default_color = (1, 2)  # pyright: ignore[reportAttributeAccessIssue]

        sent = _play(api, turn, turns=1)

        (text,) = sent[0][0].draw.text
        assert ((text.color.r, text.color.g, text.color.b), text.size) == ((255, 165, 0), 12)


class TestCheats:
    def test_a_cheat_turned_on_twice_is_sent_once_and_reads_on(self) -> None:
        api = Api()
        seen: list[frozenset[Cheat]] = []

        def turn(step: int) -> None:
            if step == 0:
                api.debug.turn_on(Cheat.SHOW_MAP)
            if step == 1:
                api.debug.turn_on(Cheat.SHOW_MAP)
                api.debug.turn_off(Cheat.GOD)
            seen.append(api.debug.cheats)

        sent = _play(api, turn, turns=2)

        assert sent == {0: [debug_pb2.DebugCommand(game_state=debug_pb2.show_map)]}
        assert seen == [frozenset({Cheat.SHOW_MAP})] * 2

    def test_a_cheat_turned_off_in_a_later_turn_is_switched_again(self) -> None:
        api = Api()
        seen: list[frozenset[Cheat]] = []

        def turn(step: int) -> None:
            if step == 0:
                api.debug.turn_on(Cheat.GOD)
            if step == 1:
                api.debug.turn_off(Cheat.GOD)
            seen.append(api.debug.cheats)

        sent = _play(api, turn, turns=2)

        god = [debug_pb2.DebugCommand(game_state=debug_pb2.god)]
        assert sent == {0: god, 1: god}
        assert seen == [frozenset({Cheat.GOD}), frozenset()]

    def test_a_cheat_turned_on_and_off_in_one_turn_sends_nothing(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.turn_on(Cheat.FREE)
            api.debug.turn_off(Cheat.FREE)

        assert _play(api, turn, turns=2) == {}

    def test_the_givers_send_the_game_state_each_time_they_are_called(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.give_minerals()
            api.debug.give_resources()
            api.debug.give_upgrades()

        sent = _play(api, turn, turns=2)

        states = [debug_pb2.minerals, debug_pb2.all_resources, debug_pb2.upgrade]
        assert sent == {step: [debug_pb2.DebugCommand(game_state=state) for state in states] for step in (0, 1)}

    def test_before_any_game_debug_raises(self) -> None:
        with pytest.raises(NotPlayingError):
            Api().debug.turn_on(Cheat.GOD)


class TestCommands:
    @pytest.mark.parametrize(("owner", "player"), [(Alliance.OWN, 1), (Alliance.ENEMY, 2), (Alliance.NEUTRAL, 0)])
    def test_units_are_created_for_this_player_its_enemy_or_the_map(self, owner: Alliance, player: int) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.create_units(UnitTypeId.MARINE, at=Point((3.0, 4.0)), count=2, owner=owner)

        sent = _play(api, turn, turns=1)

        (command,) = sent[0]
        create = command.create_unit
        assert (create.unit_type, create.owner, create.pos.x, create.pos.y, create.quantity) == (
            UnitTypeId.MARINE,
            player,
            3.0,
            4.0,
            2,
        )

    @pytest.mark.parametrize(
        ("owner", "count", "players"),
        [(Alliance.ALLY, 1, 2), (Alliance.OWN, 0, 2), (Alliance.ENEMY, 1, 1)],
        ids=["an ally", "none", "an enemy where there is none"],
    )
    def test_creating_units_that_cannot_be_raises(self, owner: Alliance, count: int, players: int) -> None:
        api = Api()
        raised: list[bool] = []

        def turn(step: int) -> None:
            with pytest.raises(ValueError):
                api.debug.create_units(UnitTypeId.MARINE, at=(3.0, 4.0), count=count, owner=owner)
            raised.append(True)

        assert _play(api, turn, turns=1, players=players) == {}
        assert raised == [True]

    def test_units_are_killed_and_their_vitals_set_by_their_tags(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                marines = api.units.own
                api.debug.set_vitals(marines, life=10, energy=5)
                api.debug.kill(marines[0])

        sent = _play(api, turn, turns=1, units=lambda step: [make_unit(7), make_unit(8)])

        values = [(c.unit_value.unit_tag, c.unit_value.unit_value, c.unit_value.value) for c in sent[0][:4]]
        assert values == [(7, _VITALS.Life, 10), (7, _VITALS.Energy, 5), (8, _VITALS.Life, 10), (8, _VITALS.Energy, 5)]
        assert list(sent[0][4].kill_unit.tag) == [7]

    @pytest.mark.parametrize(
        ("vitals", "refused"),
        [
            ({"shields": 0}, "above 0"),
            ({"life": -1}, "above 0"),
            ({"energy": math.inf}, "finite"),
            ({}, "give the life, the energy or the shields"),
        ],
        ids=["0", "below 0", "infinite", "nothing"],
    )
    def test_a_vital_set_to_what_cannot_be_raises(self, vitals: dict[str, float], refused: str) -> None:
        api = Api()
        raised: list[bool] = []

        def turn(step: int) -> None:
            with pytest.raises(ValueError, match=refused):
                api.debug.set_vitals(api.units.own, **vitals)
            raised.append(True)

        assert _play(api, turn, turns=1, units=lambda step: [make_unit(7)]) == {}
        assert raised == [True]

    def test_killing_no_units_sends_nothing(self) -> None:
        api = Api()
        assert _play(api, lambda step: api.debug.kill([]), turns=2) == {}

    def test_an_observer_is_no_enemy_to_create_units_for(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.create_units(UnitTypeId.MARINE, at=(3.0, 4.0), owner=Alliance.ENEMY)

        sent = _play(api, turn, turns=1, observers=1)

        assert sent[0][0].create_unit.owner == 2

    def test_a_game_is_ended_in_a_victory_or_a_surrender(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.declare_victory()
            if step == 1:
                api.debug.surrender()

        sent = _play(api, turn, turns=2)

        assert [commands[0].end_game.end_result for commands in sent.values()] == [
            debug_pb2.DebugEndGame.DeclareVictory,
            debug_pb2.DebugEndGame.Surrender,
        ]


def _count_visible_enemies(api: Api) -> int:
    """How many of the enemy's units are visible."""
    return sum(1 for unit in api.units.enemy if unit.visibility is Visibility.VISIBLE)


@pytest.mark.integration
def test_in_a_real_game_debug_commands_create_set_and_kill_units_and_show_the_map() -> None:
    """Run with `pytest -m integration`. Plays a few seconds of a game, which it ends in a declared victory."""
    try:
        MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    api = Api()
    seen: dict[str, Any] = {}

    @api.on(TurnEvent)
    def turn(event: TurnEvent) -> None:
        turns = seen.setdefault("turns", 0)
        seen["turns"] = turns + 1
        townhall = api.units.own.of_type(UnitTypeId.COMMAND_CENTER)[0]
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        match turns:
            case 0:
                seen["enemy_visible"] = _count_visible_enemies(api)
                api.debug.create_units(
                    UnitTypeId.MARINE, at=townhall.position.towards(api.map.playable_area.center, 6), count=2
                )
                api.debug.turn_on(Cheat.SHOW_MAP)
                api.debug.draw(townhall, DebugText("NachOS"))
            case 2:
                seen["marines"] = len(marines)
                seen["enemy_visible_shown"] = _count_visible_enemies(api)
                api.debug.set_vitals(marines, life=10)
                api.debug.turn_off(Cheat.SHOW_MAP)
            case 4:
                seen["life"] = [marine.health for marine in marines]
                seen["enemy_visible_hidden"] = _count_visible_enemies(api)
                api.debug.kill(marines)
            case 6:
                seen["marines_left"] = len(marines)
                api.debug.declare_victory()

    result = run_local(
        "PylonAIE_v4",
        api,
        Computer(Race.ZERG, Difficulty.VERY_EASY),
        race=Race.TERRAN,
        steps_per_turn=8,
        window=(640, 480),
    )

    assert result is Result.VICTORY
    assert seen["marines"] == 2
    assert seen["life"] == [10, 10]
    assert seen["marines_left"] == 0
    assert seen["enemy_visible"] == 0 < seen["enemy_visible_shown"]
    assert seen["enemy_visible_hidden"] == 0
