"""Drawing, cheats and debug commands: what goes out after each turn's orders, and when."""

import math
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from s2clientprotocol import debug_pb2, error_pb2, raw_pb2, sc2api_pb2

from sc2nachos import Api, NotPlayingError
from sc2nachos.debug import Cheat, Color, Drawing, Marker, _debug_commands
from sc2nachos.events import GameStartEvent, TurnEvent
from sc2nachos.geometry import Point, Rectangle
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import MapFile, MapNotFoundError
from sc2nachos.match import Computer, Difficulty, Race, Result
from sc2nachos.protocol import Client, Status
from sc2nachos.run import ApiBot, run_local
from sc2nachos.units import Alliance, Visibility
from support import make_game_info, make_observation, make_response, make_unit

# The ground height of `make_game_info`'s flat map: a height byte of 128 is an eighth above 0.
_GROUND = 0.125
_VITALS = debug_pb2.DebugSetUnitValue.UnitValue


class _ScriptedGame:
    """A game that answers every request by its kind: an observation from `observations` in turn, the last ending the
    game in a victory, and success for every action."""

    def __init__(
        self, observations: Sequence[sc2api_pb2.ResponseObservation], *, players: int, observers: int = 0
    ) -> None:
        self.observations = deque(observations)
        self.requests: list[sc2api_pb2.Request] = []
        self.info = make_game_info()
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
    refresh_steps: int = 1,
    units: Callable[[int], Sequence[raw_pb2.Unit]] = lambda _: (),
    dead: Callable[[int], Sequence[int]] = lambda _: (),
    players: int = 2,
    observers: int = 0,
) -> dict[int, list[debug_pb2.DebugCommand]]:
    """Play `turns` turns of one step with `api`, calling `turn` with each turn's step, each step's observation
    reporting `units` and the tags `dead`, with the drawing refreshed every `refresh_steps`, and return the debug
    commands sent, by the step of the turn that sent them."""
    observations = [make_observation(step, units=units(step), dead=dead(step)) for step in range(turns + 1)]
    game = _ScriptedGame(observations, players=players, observers=observers)
    client = Client(game)
    client.join_game(Race.TERRAN)

    @api.events.on(GameStartEvent)
    def on_start(event: GameStartEvent) -> None:
        api.debug.refresh_steps = refresh_steps

    @api.events.on(TurnEvent)
    def on_turn(event: TurnEvent) -> None:
        turn(event.step)

    api.play(client)
    api.events.unsubscribe(on_start)
    api.events.unsubscribe(on_turn)
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
                api.debug.draw_text("here", at=(2.0, 3.0))

        observations = [make_observation(step, units=[make_unit(1)]) for step in range(3)]
        game = _ScriptedGame(observations, players=2)
        client = Client(game)
        client.join_game(Race.TERRAN)
        api.events.on(TurnEvent)(lambda event: turn(event.step))
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
                api.debug.draw_line((1.0, 1.0), (2.0, 2.0, 5.0), color=(255, 0, 0), steps=3)

        sent = _play(api, turn, turns=6)

        assert list(sent) == [0, 3]
        (line,) = sent[0][0].draw.lines
        assert ((line.line.p0.z, line.line.p1.z), line.color.r) == ((_GROUND, 5.0), 255)
        assert sent[3] == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]

    def test_a_drawing_made_again_unchanged_every_turn_is_sent_once(self) -> None:
        api = Api()
        sent = _play(api, lambda step: api.debug.draw_text("fixed", at=(1.0, 1.0)), turns=5)
        assert list(sent) == [0]

    def test_with_a_refresh_every_four_steps_a_drawing_that_changes_every_turn_goes_out_every_fourth_step(self) -> None:
        api = Api()
        draw = lambda step: api.debug.draw_text_on_screen(f"step {step}", at=(0.1, 0.1))  # noqa: E731
        sent = _play(api, draw, turns=9, refresh_steps=4)
        assert {step: _texts(commands) for step, commands in sent.items()} == {
            0: ["step 0"],
            4: ["step 4"],
            8: ["step 8"],
        }

    def test_a_cheat_between_refreshes_goes_out_at_once_with_the_drawing_as_it_stands(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.draw_text(f"step {step}", at=(1.0, 1.0))
            if step == 2:
                api.debug.turn_on(Cheat.GOD)

        sent = _play(api, turn, turns=7, refresh_steps=4)

        # The cheat's request is a refresh too, so the next comes 4 steps after it.
        assert list(sent) == [0, 2, 6]
        assert sent[2][0] == debug_pb2.DebugCommand(game_state=debug_pb2.god)
        assert _texts(sent[2]) == ["step 2"]

    def test_an_unchanged_drawing_is_looked_at_once_a_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        looked_at: list[int] = []
        draw = _debug_commands._draw

        def counted(drawings: Sequence[Drawing]) -> debug_pb2.DebugDraw:
            looked_at.append(len(drawings))
            return draw(drawings)

        monkeypatch.setattr(_debug_commands, "_draw", counted)
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw_text("fixed", at=(1.0, 1.0), steps=100)

        sent = _play(api, turn, turns=9, refresh_steps=4)

        assert list(sent) == [0]
        assert len(looked_at) == 3

    def test_a_command_with_nothing_drawn_leaves_the_refreshes_to_their_clock(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 1:
                api.debug.give_minerals()
            if step == 2:
                api.debug.draw_text("late", at=(1.0, 1.0), steps=10)

        sent = _play(api, turn, turns=6, refresh_steps=4)

        assert list(sent) == [1, 4]
        assert sent[1] == [debug_pb2.DebugCommand(game_state=debug_pb2.minerals)]
        assert _texts(sent[4]) == ["late"]

    def test_a_drawing_at_a_unit_follows_it(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw_sphere(api.units.own[0], 1.5, steps=10)

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
                api.debug.draw_text("doomed", at=api.units.own[0], steps=10)

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
                api.debug.draw_text("scouted", at=api.units.enemy[0], steps=10)

        enemy = make_unit(1, alliance=Alliance.ENEMY, at=(3.0, 3.0))
        sent = _play(api, turn, turns=4, units=lambda step: [enemy] if step < 2 else [])

        assert list(sent) == [0]

    def test_a_box_stands_over_its_rectangle_from_the_ground_up_one(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw_box(Rectangle(1.0, 2.0, 3.0, 4.0))

        sent = _play(api, turn, turns=1)

        (box,) = sent[0][0].draw.boxes
        assert (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z) == (1, 2, _GROUND, 4, 6, _GROUND + 1)

    def test_a_game_that_draws_nothing_sends_no_debug_request(self) -> None:
        assert _play(Api(), lambda step: None, turns=3) == {}

    def test_a_new_game_forgets_the_last_ones_drawing(self) -> None:
        api = Api()
        _play(api, lambda step: api.debug.draw_text("first game", at=(1.0, 1.0), steps=100), turns=2)
        assert _play(api, lambda step: None, turns=2) == {}

    @pytest.mark.parametrize(
        "draw",
        [
            lambda api: api.debug.draw_text("x", at=(1.0, 1.0), color=(256, 0, 0)),
            lambda api: api.debug.draw_text("x", at=(1.0, 1.0), color=(0.5, 0, 0)),
            lambda api: api.debug.draw_text("x", at=(1.0, 1.0), steps=0),
            lambda api: api.debug.draw_text("x", at=(1.0, 1.0), size=0),
            lambda api: api.debug.draw_text_on_screen("x", at=(1.5, 0.5)),
            lambda api: api.debug.draw_text_on_screen("x", at=(0.1, 0.1, 0.0)),  # pyright: ignore[reportArgumentType]
            lambda api: api.debug.draw_sphere((1.0, 1.0), 0),
            lambda api: api.debug.draw_polygon((1.0, 1.0), 1, sides=2),
            lambda api: api.debug.draw_marker((1.0, 1.0), Marker.CROSS, radius=math.inf),
        ],
        ids=[
            "color over 255",
            "color not whole",
            "no steps",
            "no size",
            "off the screen",
            "three coordinates on the screen",
            "no radius",
            "two sides",
            "an infinite marker",
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

    def test_the_refresh_steps_are_set_in_a_game_and_must_be_at_least_one(self) -> None:
        api = Api()
        seen: list[int] = []

        def turn(step: int) -> None:
            seen.append(api.debug.refresh_steps)
            with pytest.raises(ValueError):
                api.debug.refresh_steps = 0

        _play(api, turn, turns=1, refresh_steps=4)

        assert seen == [4]

    def test_each_game_starts_refreshing_every_step(self) -> None:
        api = Api()
        _play(api, lambda step: None, turns=1, refresh_steps=4)
        seen: list[int] = []
        _play(api, lambda step: seen.append(api.debug.refresh_steps), turns=1)
        assert seen == [1]

    def test_a_drawing_lasts_until_it_is_removed(self) -> None:
        api = Api()
        drawings: list[Drawing] = []

        def turn(step: int) -> None:
            if step == 0:
                drawings.append(api.debug.draw_line((1.0, 1.0), (2.0, 2.0), steps=None))
            if step == 5:
                drawings[0].remove()
                drawings[0].remove()

        sent = _play(api, turn, turns=8)

        assert list(sent) == [0, 5]
        assert sent[5] == [debug_pb2.DebugCommand(draw=debug_pb2.DebugDraw())]

    def test_a_drawing_is_drawn_in_a_standard_color_or_any_other(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            api.debug.draw_text("red", at=(1.0, 1.0), color=Color.RED)
            api.debug.draw_text("teal", at=(1.0, 1.0), color=(0, 128, 128))

        sent = _play(api, turn, turns=1)

        colors = [(text.color.r, text.color.g, text.color.b) for text in sent[0][0].draw.text]
        assert colors == [(255, 0, 0), (0, 128, 128)]

    def test_a_polygon_is_its_sides_around_its_center_the_first_corner_due_east(self) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw_polygon((4.0, 4.0), 2, sides=4)

        sent = _play(api, turn, turns=1)

        lines = [(line.line.p0, line.line.p1) for line in sent[0][0].draw.lines]
        ends = [((round(a.x, 6), round(a.y, 6)), (round(b.x, 6), round(b.y, 6))) for a, b in lines]
        assert ends == [((6, 4), (4, 6)), ((4, 6), (2, 4)), ((2, 4), (4, 2)), ((4, 2), (6, 4))]
        assert {point.z for pair in lines for point in pair} == {_GROUND}

    @pytest.mark.parametrize(
        ("shape", "ends"),
        [
            (Marker.PLUS, [((3, 4), (5, 4)), ((4, 3), (4, 5))]),
            (
                Marker.CROSS,
                [((3.292893, 3.292893), (4.707107, 4.707107)), ((3.292893, 4.707107), (4.707107, 3.292893))],
            ),
        ],
        ids=["plus", "cross"],
    )
    def test_a_marker_is_two_arms_reaching_its_radius(self, shape: Marker, ends: list[Any]) -> None:
        api = Api()

        def turn(step: int) -> None:
            if step == 0:
                api.debug.draw_marker((4.0, 4.0), shape, radius=1)

        sent = _play(api, turn, turns=1)

        lines = [(line.line.p0, line.line.p1) for line in sent[0][0].draw.lines]
        assert [((round(a.x, 6), round(a.y, 6)), (round(b.x, 6), round(b.y, 6))) for a, b in lines] == ends


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


def _in_sight(api: Api) -> int:
    """How many of the enemy's units are in sight."""
    return sum(1 for unit in api.units.enemy if unit.visibility is Visibility.IN_VISION)


@pytest.mark.integration
def test_in_a_real_game_debug_commands_create_set_and_kill_units_and_show_the_map() -> None:
    """Run with `pytest -m integration`. Plays a few seconds of a game, which it ends in a declared victory."""
    try:
        MapFile.find("PylonAIE_v4")
    except MapNotFoundError as missing:
        pytest.skip(str(missing))

    api = Api()
    seen: dict[str, Any] = {}

    @api.events.on(TurnEvent)
    def turn(event: TurnEvent) -> None:
        turns = seen.setdefault("turns", 0)
        seen["turns"] = turns + 1
        townhall = api.units.own.of_type(UnitTypeId.COMMAND_CENTER)[0]
        marines = api.units.own.of_type(UnitTypeId.MARINE)
        match turns:
            case 0:
                seen["enemy_in_sight"] = _in_sight(api)
                api.debug.create_units(
                    UnitTypeId.MARINE, at=townhall.position.towards(api.map.playable_area.center, 6), count=2
                )
                api.debug.turn_on(Cheat.SHOW_MAP)
                api.debug.draw_text("NachOS", at=townhall)
            case 2:
                seen["marines"] = len(marines)
                seen["enemy_in_sight_shown"] = _in_sight(api)
                api.debug.set_vitals(marines, life=10)
                api.debug.turn_off(Cheat.SHOW_MAP)
            case 4:
                seen["life"] = [marine.health for marine in marines]
                seen["enemy_in_sight_hidden"] = _in_sight(api)
                api.debug.kill(marines)
            case 6:
                seen["marines_left"] = len(marines)
                api.debug.declare_victory()

    result = run_local(
        "PylonAIE_v4",
        ApiBot(api, Race.TERRAN),
        Computer(Race.ZERG, Difficulty.VERY_EASY),
        steps_per_turn=8,
        window=(640, 480),
    )

    assert result is Result.VICTORY
    assert seen["marines"] == 2
    assert seen["life"] == [10, 10]
    assert seen["marines_left"] == 0
    assert seen["enemy_in_sight"] == 0 < seen["enemy_in_sight_shown"]
    assert seen["enemy_in_sight_hidden"] == 0
