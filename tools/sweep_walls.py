"""Check in game that the walls `api.placement.terran_wall` and `protoss_wall` give close each main's ramp.

Needs StarCraft II installed. On each map of the pool, it plays one game as Terran and one as Protoss, each against the
easiest computer of the same race, left one engineering bay in the middle of the map, so that it can neither fight nor
train and both mains stand empty. With the map shown, it checks the walls at both mains, each wall made at once by
debug commands. A zergling, the smallest ground unit, made below the ramp and ordered to the main's start, is the test
of closed. What it records for each main:

- the game's answer to placing every site of both walls, with the ground empty;
- as Terran, for each wall: the two corner depots with `barracks_in_wall`, with `middle_depot`, with
  `barracks_with_add_on` alone, and with `barracks_with_add_on` and the reactor it builds: whether the zergling got in
  with the depots raised, and again once they are lowered, and whether that barracks took the order to build its
  reactor and built it;
- as Protoss: the answer to placing each building before the pylon stands and after, and whether the zergling got in
  with a zealot holding the gap, and again once the zealot is gone::

    uv run python tools/sweep_walls.py --out walls.json
    uv run python tools/sweep_walls.py PylonAIE_v4 --out walls-pylon.json
"""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _sandbox import SUCCESS, Cheat, OpenGround, Sandbox, playing, without_uncurated_units
from loguru import logger
from record_corpus import GAMES
from s2clientprotocol import error_pb2, raw_pb2

from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap, Ramp
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.orders import OrderBook
from sc2nachos.placement import PlacementGrids, ProtossWall, TerranWall
from sc2nachos.state._state import _State
from sc2nachos.units._tracking import _Tracker

# How far below the ramp's bottom the zergling is made, along the way the ramp climbs.
_BELOW_THE_RAMP = 4.0
# How many steps the zergling is given to walk into the main, some three times what the walk takes in the open.
_WALKING_STEPS = 336
# A zergling this much nearer the start than the ramp's top is has passed the wall.
_PAST_THE_WALL = 4.0
_SETTLING_STEPS = 16
_BUILDING_STEPS = 64


@dataclass(slots=True)
class TerranWallCheck:
    """What the game made of the Terran wall at one main."""

    placement: dict[str, str]
    """The game's answer to placing each site, by the site's name."""
    zergling_got_in: dict[str, dict[str, bool]] = field(default_factory=dict)
    """For each way of closing the wall between the depots, whether the zergling got in with the depots raised and
    with them lowered."""
    reactor_order: str = ""
    reactor_built: bool = False


@dataclass(slots=True)
class ProtossWallCheck:
    """What the game made of the Protoss wall at one main."""

    placement: dict[str, str]
    """The game's answer to placing each site, by the site's name, the buildings with no pylon standing."""
    buildings_with_the_pylon: list[str] = field(default_factory=list)
    """The game's answer to placing each building once the pylon stands."""
    zergling_got_in_with_the_gap_held: bool = False
    zergling_got_in_with_the_gap_open: bool = False


@dataclass(slots=True)
class MainCheck:
    """Both walls at one main of one map."""

    map: str
    start: tuple[float, float]
    orientation: str
    """The way the ramp climbs, as `Ramp.orientation` names it."""
    terran: TerranWallCheck | None = None
    protoss: ProtossWallCheck | None = None


def _answer_name(answer: error_pb2.ActionResult.ValueType) -> str:
    return error_pb2.ActionResult.Name(answer)


@dataclass(frozen=True, slots=True)
class _Main:
    """One main of the map being played: its start, its ramp, and the walls `api.placement` gives it."""

    start: Point
    ramp: Ramp
    terran_wall: TerranWall
    protoss_wall: ProtossWall

    @property
    def below_the_ramp(self) -> Point:
        ramp_bottom = self.ramp.bottom.center
        step_x, step_y = self.ramp.orientation.value
        return Point((ramp_bottom.x - _BELOW_THE_RAMP * step_x, ramp_bottom.y - _BELOW_THE_RAMP * step_y))


def _mains_with_their_walls(game: Sandbox) -> list[_Main]:
    """Both mains of the map `game` plays, with their walls as NachOS's placement grids give them."""
    observation = game.client.observation()
    game_map = GameMap._from_game(game.client.game_info(), observation)
    game_data = GameData(game.client.game_data())
    tracker = _Tracker(game_data, None)
    curated_raw_data, _ = without_uncurated_units(observation.observation.raw_data)
    tracker.update(curated_raw_data, observation.observation.game_loop)
    placement_grids = PlacementGrids._build_at_start(
        game_map,
        present_units=tracker.unit_tracker.present,
        state=_State(observation, tracker, game_map),
        step=observation.observation.game_loop,
        game_data=game_data,
        order_book=OrderBook(game_data),
    )
    mains = []
    for start_location in game_map.all_start_locations:
        main_ramp = game_map.main_ramp_of(start_location)
        if main_ramp is None:
            raise RuntimeError(f"the main at {start_location} has no ramp")
        terran_wall = placement_grids.terran_wall(start_location)
        protoss_wall = placement_grids.protoss_wall(start_location)
        if terran_wall is None or protoss_wall is None:
            raise RuntimeError(f"the ramp of the main at {start_location} has no wall")
        mains.append(_Main(start_location, main_ramp, terran_wall, protoss_wall))
    return mains


@contextmanager
def _structures_standing(game: Sandbox, structures: Sequence[tuple[UnitTypeId, Point]]) -> Iterator[list[raw_pb2.Unit]]:
    """`structures` made for this player, each a type at its center, and removed again afterwards."""
    structures_made = game.spawn([(unit_type, game.player, at) for unit_type, at in structures])
    try:
        yield structures_made
    finally:
        game.remove(unit.tag for unit in structures_made)


def _leave_the_computer_one_structure(game: Sandbox) -> None:
    """Give the computer one engineering bay in the middle of the map and remove all else it has, so that it can
    neither fight nor train, and both mains stand empty. Not a supply depot, which the computer lowers at once."""
    observation = game.client.observation()
    game_map = GameMap._from_game(game.client.game_info(), observation)
    units_at_the_start = list(observation.observation.raw_data.units)
    computer_id = 2 if game.player == 1 else 1
    map_middle = OpenGround(game_map, units_at_the_start).claim(game_map.playable_area.center, half=1)
    (kept_bay,) = game.spawn([(UnitTypeId.ENGINEERING_BAY, computer_id, map_middle)])
    game.remove(unit.tag for unit in game.units() if unit.owner == computer_id and unit.tag != kept_bay.tag)


def _zergling_gets_in(game: Sandbox, main: _Main) -> bool:
    """Whether a zergling made below `main`'s ramp and ordered to its start gets past the wall in the time given."""
    (zergling,) = game.spawn([(UnitTypeId.ZERGLING, game.player, main.below_the_ramp)])
    try:
        game.order(AbilityId.MOVE, zergling.tag, main.start)
        game.client.step(_WALKING_STEPS)
        zergling_positions = [unit.pos for unit in game.units() if unit.tag == zergling.tag]
        if not zergling_positions:
            raise RuntimeError(f"the zergling sent into the main at {main.start} is gone")
        (zergling_position,) = zergling_positions
        return Point((zergling_position.x, zergling_position.y)).distance_to(main.start) < (
            main.ramp.top.center.distance_to(main.start) - _PAST_THE_WALL
        )
    finally:
        game.remove([zergling.tag])


def _lower_the_depots(game: Sandbox, depots: Sequence[raw_pb2.Unit]) -> None:
    for depot in depots:
        game.order(AbilityId.SUPPLY_DEPOT_LOWER, depot.tag)
    game.client.step(_SETTLING_STEPS)


def _zergling_raised_and_lowered(
    game: Sandbox, main: _Main, structures_made: Sequence[raw_pb2.Unit]
) -> dict[str, bool]:
    """Whether the zergling gets into `main` with the depots among `structures_made` raised, and then lowered."""
    got_in_raised = _zergling_gets_in(game, main)
    _lower_the_depots(game, [unit for unit in structures_made if unit.unit_type == UnitTypeId.SUPPLY_DEPOT])
    return {"raised": got_in_raised, "lowered": _zergling_gets_in(game, main)}


def _check_terran_wall(game: Sandbox, main: _Main) -> TerranWallCheck:
    terran_wall = main.terran_wall
    sites_by_name = {
        "near depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, terran_wall.near_depot),
        "far depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, terran_wall.far_depot),
        "middle depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, terran_wall.middle_depot),
        "barracks with add-on": (AbilityId.SCV_BUILD_BARRACKS, terran_wall.barracks_with_add_on),
        "barracks in wall": (AbilityId.SCV_BUILD_BARRACKS, terran_wall.barracks_in_wall),
    }
    terran_check = TerranWallCheck(
        placement={
            name: _answer_name(game.placement_answers(ability, [site])[0])
            for name, (ability, site) in sites_by_name.items()
        }
    )
    between_the_depots = {
        "barracks in wall": (UnitTypeId.BARRACKS, terran_wall.barracks_in_wall),
        "middle depot": (UnitTypeId.SUPPLY_DEPOT, terran_wall.middle_depot),
        "barracks with add-on": (UnitTypeId.BARRACKS, terran_wall.barracks_with_add_on),
    }
    corner_depots = [
        (UnitTypeId.SUPPLY_DEPOT, terran_wall.near_depot),
        (UnitTypeId.SUPPLY_DEPOT, terran_wall.far_depot),
    ]
    for name, structure_between in between_the_depots.items():
        with _structures_standing(game, [*corner_depots, structure_between]) as structures_made:
            terran_check.zergling_got_in[name] = _zergling_raised_and_lowered(game, main, structures_made)
    with_room_for_the_add_on = (UnitTypeId.BARRACKS, terran_wall.barracks_with_add_on)
    with _structures_standing(game, [*corner_depots, with_room_for_the_add_on]) as structures_made:
        barracks_made = next(unit for unit in structures_made if unit.unit_type == UnitTypeId.BARRACKS)
        terran_check.reactor_order = _answer_name(game.order(AbilityId.BUILD_REACTOR, barracks_made.tag))
        game.client.step(_BUILDING_STEPS)
        reactors_made = [unit for unit in game.units() if unit.unit_type == UnitTypeId.REACTOR_BARRACKS]
        terran_check.reactor_built = any(reactor.build_progress == 1 for reactor in reactors_made)
        try:
            terran_check.zergling_got_in["barracks and its reactor"] = _zergling_raised_and_lowered(
                game, main, structures_made
            )
        finally:
            game.remove(reactor.tag for reactor in reactors_made)
    return terran_check


def _check_protoss_wall(game: Sandbox, main: _Main) -> ProtossWallCheck:
    protoss_wall = main.protoss_wall
    sites_by_name = {
        "pylon": (AbilityId.PROBE_BUILD_PYLON, protoss_wall.pylon),
        "near building": (AbilityId.PROBE_BUILD_GATEWAY, protoss_wall.near_building),
        "far building": (AbilityId.PROBE_BUILD_GATEWAY, protoss_wall.far_building),
    }
    protoss_check = ProtossWallCheck(
        placement={
            name: _answer_name(game.placement_answers(ability, [site])[0])
            for name, (ability, site) in sites_by_name.items()
        }
    )
    with _structures_standing(game, [(UnitTypeId.PYLON, protoss_wall.pylon)]):
        building_sites = [protoss_wall.near_building, protoss_wall.far_building]
        answers_with_the_pylon = game.placement_answers(AbilityId.PROBE_BUILD_GATEWAY, building_sites)
        protoss_check.buildings_with_the_pylon = [_answer_name(answer) for answer in answers_with_the_pylon]
        protoss_buildings = [(UnitTypeId.GATEWAY, building_site) for building_site in building_sites]
        with (
            _structures_standing(game, protoss_buildings),
            _structures_standing(game, [(UnitTypeId.ZEALOT, protoss_wall.gap)]) as (zealot,),
        ):
            game.order(AbilityId.HOLD_POSITION, zealot.tag)
            game.client.step(_SETTLING_STEPS)
            protoss_check.zergling_got_in_with_the_gap_held = _zergling_gets_in(game, main)
            game.remove([zealot.tag])
            protoss_check.zergling_got_in_with_the_gap_open = _zergling_gets_in(game, main)
    return protoss_check


def check_walls(installation: Installation, map_names: Sequence[str]) -> list[MainCheck]:
    """Check both walls at both mains of each of `map_names`."""
    checks_by_main: dict[tuple[str, Point], MainCheck] = {}
    for map_name in map_names:
        for race in (Race.TERRAN, Race.PROTOSS):
            with playing(race, installation, map_name=map_name) as game:
                game.cheat(Cheat.SHOW_MAP, Cheat.FREE, Cheat.FAST_BUILD)
                game.client.step(4)
                _leave_the_computer_one_structure(game)
                for main in _mains_with_their_walls(game):
                    main_check = checks_by_main.setdefault(
                        (map_name, main.start),
                        MainCheck(map_name, (main.start.x, main.start.y), main.ramp.orientation.name),
                    )
                    if race is Race.TERRAN:
                        main_check.terran = _check_terran_wall(game, main)
                    else:
                        main_check.protoss = _check_protoss_wall(game, main)
                    logger.info("{} at {}: {}", map_name, main.start, main_check)
    return list(checks_by_main.values())


def _in_or_out(got_in: bool) -> str:
    return "in" if got_in else "out"


def _summary_of_each_main(main_checks: Sequence[MainCheck]) -> str:
    """One line for each main: the sites the game refused, the Protoss buildings' answers with no pylon, and how the
    zergling fared."""
    summary_lines = []
    for main_check in main_checks:
        summary_parts = []
        if main_check.terran is not None:
            terran_check = main_check.terran
            refused_sites = [name for name, answer in terran_check.placement.items() if answer != _answer_name(SUCCESS)]
            summary_parts.append(f"Terran sites refused: {refused_sites or 'none'}")
            for name, got_in in terran_check.zergling_got_in.items():
                summary_parts.append(
                    f"{name}: {_in_or_out(got_in['raised'])} raised, {_in_or_out(got_in['lowered'])} lowered"
                )
            summary_parts.append(f"reactor {terran_check.reactor_order}, built {terran_check.reactor_built}")
        if main_check.protoss is not None:
            protoss_check = main_check.protoss
            answers_with_no_pylon = [
                protoss_check.placement["near building"],
                protoss_check.placement["far building"],
            ]
            summary_parts.append(
                f"pylon {protoss_check.placement['pylon']}, buildings with no pylon {answers_with_no_pylon}, with it "
                f"{protoss_check.buildings_with_the_pylon}, "
                f"gap held: {_in_or_out(protoss_check.zergling_got_in_with_the_gap_held)}, "
                f"gap open: {_in_or_out(protoss_check.zergling_got_in_with_the_gap_open)}"
            )
        summary_lines.append(
            f"{main_check.map} {main_check.start} climbing {main_check.orientation}: " + "; ".join(summary_parts)
        )
    return "\n".join(summary_lines)


def main(argv: Sequence[str]) -> None:
    pool_maps = sorted({game.map for game in GAMES})
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("maps", nargs="*", default=pool_maps, help="the maps to check (default: the pool's)")
    parser.add_argument("--out", type=Path, help="write every check here as JSON")
    arguments = parser.parse_args(argv)
    main_checks = check_walls(Installation.find(), arguments.maps)
    print(_summary_of_each_main(main_checks))
    if arguments.out is not None:
        checks_as_json = json.dumps([asdict(main_check) for main_check in main_checks], indent=2)
        arguments.out.write_text(checks_as_json, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
