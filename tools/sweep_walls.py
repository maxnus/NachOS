"""Check in game that the walls `api.placement` gives close each main's ramp.

Needs StarCraft II installed. On each map of the pool, it plays one game as Terran and one as Protoss, each against the
easiest computer of the same race, left one engineering bay in the middle of the map, so that it can neither fight nor
train and both mains stand empty. With the map shown, it checks the walls at both mains, each wall made at once by
debug commands. A zergling, the smallest ground unit, made below the ramp and ordered to the main's start, is the test
of closed. What it records for each main:

- the game's answer to placing every site of both walls, with the ground empty;
- as Terran, for each wall: the two corner depots with `barracks_without_add_on`, with `middle_depot`, with `barracks`
  alone, and with `barracks` and the reactor it builds: whether the zergling got in with the depots raised, and again
  once they are lowered, and whether the barracks at `barracks` took the order to build its reactor and built it;
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
    climb: tuple[int, int]
    """The signs of x and y along which the ramp climbs."""
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
    def climb(self) -> tuple[int, int]:
        top, bottom = self.ramp.top.center, self.ramp.bottom.center
        return (1 if top.x > bottom.x else -1, 1 if top.y > bottom.y else -1)

    @property
    def below_the_ramp(self) -> Point:
        bottom = self.ramp.bottom.center
        return Point((bottom.x - _BELOW_THE_RAMP * self.climb[0], bottom.y - _BELOW_THE_RAMP * self.climb[1]))


def _mains(game: Sandbox) -> list[_Main]:
    """Both mains of the map `game` plays, with their walls as NachOS's grids give them."""
    observation = game.client.observation()
    game_map = GameMap._from_game(game.client.game_info(), observation)
    data = GameData(game.client.game_data())
    tracker = _Tracker(data, None)
    raw, _ = without_uncurated_units(observation.observation.raw_data)
    tracker.update(raw, observation.observation.game_loop)
    grids = PlacementGrids._build_at_start(
        game_map,
        present_units=tracker.unit_tracker.present,
        state=_State(observation, tracker, game_map),
        step=observation.observation.game_loop,
        game_data=data,
        order_book=OrderBook(data),
    )
    mains = []
    for start in (game_map.start_location, *game_map.opponent_start_locations):
        ramp = game_map.main_ramp_of(start)
        if ramp is None:
            raise RuntimeError(f"the main at {start} has no ramp")
        terran_wall, protoss_wall = grids.terran_wall(ramp), grids.protoss_wall(ramp)
        if terran_wall is None or protoss_wall is None:
            raise RuntimeError(f"the ramp of the main at {start} has no wall")
        mains.append(_Main(start, ramp, terran_wall, protoss_wall))
    return mains


@contextmanager
def _standing(game: Sandbox, structures: Sequence[tuple[UnitTypeId, Point]]) -> Iterator[list[raw_pb2.Unit]]:
    """`structures` made for this player, each a type at its center, and removed again afterwards."""
    made = game.spawn([(unit_type, game.player, at) for unit_type, at in structures])
    try:
        yield made
    finally:
        game.remove(unit.tag for unit in made)


def _leave_the_computer_one_structure(game: Sandbox) -> None:
    """Give the computer one engineering bay in the middle of the map and remove all else it has, so that it can
    neither fight nor train, and both mains stand empty. Not a supply depot, which the computer lowers at once."""
    observation = game.client.observation()
    game_map = GameMap._from_game(game.client.game_info(), observation)
    units = list(observation.observation.raw_data.units)
    computer = 2 if game.player == 1 else 1
    middle = OpenGround(game_map, units).claim(game_map.playable_area.center, half=1)
    (kept,) = game.spawn([(UnitTypeId.ENGINEERING_BAY, computer, middle)])
    game.remove(unit.tag for unit in game.units() if unit.owner == computer and unit.tag != kept.tag)


def _zergling_gets_in(game: Sandbox, main: _Main) -> bool:
    """Whether a zergling made below `main`'s ramp and ordered to its start gets past the wall in the time given."""
    (zergling,) = game.spawn([(UnitTypeId.ZERGLING, game.player, main.below_the_ramp)])
    try:
        game.order(AbilityId.MOVE, zergling.tag, main.start)
        game.client.step(_WALKING_STEPS)
        positions = [unit.pos for unit in game.units() if unit.tag == zergling.tag]
        if not positions:
            raise RuntimeError(f"the zergling sent into the main at {main.start} is gone")
        (position,) = positions
        return Point((position.x, position.y)).distance_to(main.start) < (
            main.ramp.top.center.distance_to(main.start) - _PAST_THE_WALL
        )
    finally:
        game.remove([zergling.tag])


def _lower(game: Sandbox, depots: Sequence[raw_pb2.Unit]) -> None:
    for depot in depots:
        game.order(AbilityId.SUPPLY_DEPOT_LOWER, depot.tag)
    game.client.step(_SETTLING_STEPS)


def _check_terran_wall(game: Sandbox, main: _Main) -> TerranWallCheck:
    wall = main.terran_wall
    sites = {
        "first depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, wall.depots[0]),
        "second depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, wall.depots[1]),
        "middle depot": (AbilityId.SCV_BUILD_SUPPLY_DEPOT, wall.middle_depot),
        "barracks": (AbilityId.SCV_BUILD_BARRACKS, wall.barracks),
        "barracks without add-on": (AbilityId.SCV_BUILD_BARRACKS, wall.barracks_without_add_on),
    }
    check = TerranWallCheck(
        placement={
            name: _answer_name(game.placement_answers(ability, [at])[0]) for name, (ability, at) in sites.items()
        }
    )
    closings = {
        "barracks without add-on": (UnitTypeId.BARRACKS, wall.barracks_without_add_on),
        "middle depot": (UnitTypeId.SUPPLY_DEPOT, wall.middle_depot),
        "barracks": (UnitTypeId.BARRACKS, wall.barracks),
    }
    corner_depots = [(UnitTypeId.SUPPLY_DEPOT, depot) for depot in wall.depots]
    for name, closing in closings.items():
        with _standing(game, [*corner_depots, closing]) as made:
            depots = [unit for unit in made if unit.unit_type == UnitTypeId.SUPPLY_DEPOT]
            raised = _zergling_gets_in(game, main)
            _lower(game, depots)
            check.zergling_got_in[name] = {"raised": raised, "lowered": _zergling_gets_in(game, main)}
    with _standing(game, [*corner_depots, (UnitTypeId.BARRACKS, wall.barracks)]) as made:
        barracks = next(unit for unit in made if unit.unit_type == UnitTypeId.BARRACKS)
        check.reactor_order = _answer_name(game.order(AbilityId.BUILD_REACTOR, barracks.tag))
        game.client.step(_BUILDING_STEPS)
        reactors = [unit for unit in game.units() if unit.unit_type == UnitTypeId.REACTOR_BARRACKS]
        check.reactor_built = any(reactor.build_progress == 1 for reactor in reactors)
        try:
            depots = [unit for unit in made if unit.unit_type == UnitTypeId.SUPPLY_DEPOT]
            raised = _zergling_gets_in(game, main)
            _lower(game, depots)
            check.zergling_got_in["barracks and its reactor"] = {
                "raised": raised,
                "lowered": _zergling_gets_in(game, main),
            }
        finally:
            game.remove(reactor.tag for reactor in reactors)
    return check


def _check_protoss_wall(game: Sandbox, main: _Main) -> ProtossWallCheck:
    wall = main.protoss_wall
    sites = {
        "pylon": (AbilityId.PROBE_BUILD_PYLON, wall.pylon),
        "building at the corner": (AbilityId.PROBE_BUILD_GATEWAY, wall.buildings[0]),
        "building beside the gap": (AbilityId.PROBE_BUILD_GATEWAY, wall.buildings[1]),
    }
    check = ProtossWallCheck(
        placement={
            name: _answer_name(game.placement_answers(ability, [at])[0]) for name, (ability, at) in sites.items()
        }
    )
    with _standing(game, [(UnitTypeId.PYLON, wall.pylon)]):
        answers = game.placement_answers(AbilityId.PROBE_BUILD_GATEWAY, list(wall.buildings))
        check.buildings_with_the_pylon = [_answer_name(answer) for answer in answers]
        buildings = [(UnitTypeId.GATEWAY, building) for building in wall.buildings]
        with _standing(game, buildings), _standing(game, [(UnitTypeId.ZEALOT, wall.gap)]) as (zealot,):
            game.order(AbilityId.HOLD_POSITION, zealot.tag)
            game.client.step(_SETTLING_STEPS)
            check.zergling_got_in_with_the_gap_held = _zergling_gets_in(game, main)
            game.remove([zealot.tag])
            check.zergling_got_in_with_the_gap_open = _zergling_gets_in(game, main)
    return check


def check_walls(installation: Installation, map_names: Sequence[str]) -> list[MainCheck]:
    """Check both walls at both mains of each of `map_names`."""
    checks: dict[tuple[str, Point], MainCheck] = {}
    for map_name in map_names:
        for race in (Race.TERRAN, Race.PROTOSS):
            with playing(race, installation, map_name=map_name) as game:
                game.cheat(Cheat.SHOW_MAP, Cheat.FREE, Cheat.FAST_BUILD)
                game.client.step(4)
                _leave_the_computer_one_structure(game)
                for main in _mains(game):
                    check = checks.setdefault(
                        (map_name, main.start), MainCheck(map_name, (main.start.x, main.start.y), main.climb)
                    )
                    if race is Race.TERRAN:
                        check.terran = _check_terran_wall(game, main)
                    else:
                        check.protoss = _check_protoss_wall(game, main)
                    logger.info("{} at {}: {}", map_name, main.start, check)
    return list(checks.values())


def _summary(checks: Sequence[MainCheck]) -> str:
    """One line for each main: the sites the game refused, the Protoss buildings' answers with no pylon, and how the
    zergling fared."""
    lines = []
    for check in checks:
        parts = []
        if check.terran is not None:
            refused = [name for name, answer in check.terran.placement.items() if answer != _answer_name(SUCCESS)]
            parts.append(f"Terran sites refused: {refused or 'none'}")
            for name, got_in in check.terran.zergling_got_in.items():
                raised, lowered = ("in" if got_in[state] else "out" for state in ("raised", "lowered"))
                parts.append(f"{name}: {raised} raised, {lowered} lowered")
            parts.append(f"reactor {check.terran.reactor_order}, built {check.terran.reactor_built}")
        if check.protoss is not None:
            placement = check.protoss.placement
            unpowered = [placement["building at the corner"], placement["building beside the gap"]]
            held, opened = (
                "in" if got_in else "out"
                for got_in in (
                    check.protoss.zergling_got_in_with_the_gap_held,
                    check.protoss.zergling_got_in_with_the_gap_open,
                )
            )
            parts.append(
                f"pylon {placement['pylon']}, buildings with no pylon {unpowered}, with it "
                f"{check.protoss.buildings_with_the_pylon}, gap held: {held}, gap open: {opened}"
            )
        lines.append(f"{check.map} {check.start} climbing {check.climb}: " + "; ".join(parts))
    return "\n".join(lines)


def main(argv: Sequence[str]) -> None:
    pool = sorted({game.map for game in GAMES})
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("maps", nargs="*", default=pool, help="the maps to check (default: the pool's)")
    parser.add_argument("--out", type=Path, help="write every check here as JSON")
    arguments = parser.parse_args(argv)
    checks = check_walls(Installation.find(), arguments.maps)
    print(_summary(checks))
    if arguments.out is not None:
        arguments.out.write_text(json.dumps([asdict(check) for check in checks], indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
