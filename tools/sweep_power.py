"""Find what powers a Protoss structure's placement, and check `api.placement.powered_for` against the game.

Needs StarCraft II installed. Each sweep plays a game as Protoss against the easiest computer, left one structure, with
the map shown and costs off, makes power sources by debug commands and asks the game to place structures that need
power around them. Every answer around a source that is a success or the power error is checked against
`powered_for`, and the ones it gets wrong are listed. What each sweep records:

- `sources`: what the game lists among this player's power sources at the start, and with a finished pylon; the answer
  to placing a gateway, a forge, a photon cannon and a shield battery at every center within 9 tiles of the pylon, and
  a pylon beside it; whether a probe's order to build a gateway agrees with the query at the edge of its reach; what the
  game lists once the pylon dies; an enemy's pylon, and the answer to a gateway around it; a warp prism in transport
  mode, the answers around it, and what the game lists every two steps after the order to phase, then the answers
  around it phasing; and a pylon a probe builds, every eight steps until it finishes, with the answer to a gateway
  beside it.
- `edge`: a gateway and a photon cannon centered exactly on a phasing warp prism's radius, and a tile either side.
- `levels`: on each map of the pool, or those named, pylons and then phasing warp prisms beside cliffs, above them and
  below, and the answer to a gateway and a photon cannon at every center within reach, with the height of the tiles the
  center touches and the source's.
- `uneven`: on each map of the pool, or those named, a pylon at every corner of an open tile whose height lies off the
  levels 2 apart, no two reaching a center at once, and the answer to a gateway and a photon cannon at every center
  within reach of each::

    uv run python tools/sweep_power.py --out power.json
    uv run python tools/sweep_power.py levels uneven --maps PylonAIE_v4 --out power-levels.json
"""

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy
from _sandbox import Cheat, OpenGround, Sandbox, playing, without_uncurated_units
from loguru import logger
from record_corpus import GAMES
from s2clientprotocol import error_pb2, raw_pb2

from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.match import Race
from sc2nachos.orders import OrderBook
from sc2nachos.placement import PlacementGrids
from sc2nachos.run import Installation
from sc2nachos.state._state import _State
from sc2nachos.units._tracking import _Tracker

_SUCCESS_ANSWER = "Success"
_TOO_FAR_FROM_POWER = "CantBuildTooFarFromBuildPowerSource"
# How far around a source the `sources` sweep asks, in tiles along x and along y.
_SOURCES_SWEEP_REACH = 9
# How many sites beside a cliff the `levels` sweep takes on each map, with the source above the cliff and below it.
_CLIFF_SITES_A_SIDE = 5
# How far apart the cliff sites lie, at least: no source reaches another's sites.
_CLIFF_SITES_APART = 16.0
# A site counts beside a cliff with at least this many flat 3x3 sites on the other level within 6 of it.
_MIN_SITES_ON_THE_OTHER_LEVEL = 4
# Ground no more uneven than this counts as flat.
_MAX_UNEVENNESS_OF_FLAT_GROUND = 0.3
# Ground levels lie this far apart.
_LEVELS_APART = 2.0
# How far apart the `uneven` sweep's pylons stand at once, at least: no center lies within reach of two.
_UNEVEN_PYLONS_APART = 14.0
_SETTLING_STEPS = 8

# The structures asked about, the ability that builds each, and whether its footprint is odd, centered on a tile.
_POWERED_STRUCTURES = {
    UnitTypeId.GATEWAY: (AbilityId.PROBE_BUILD_GATEWAY, True),
    UnitTypeId.FORGE: (AbilityId.PROBE_BUILD_FORGE, True),
    UnitTypeId.PHOTON_CANNON: (AbilityId.PROBE_BUILD_PHOTON_CANNON, False),
    UnitTypeId.SHIELD_BATTERY: (AbilityId.PROBE_BUILD_SHIELD_BATTERY, False),
}


def _answer_name(answer: error_pb2.ActionResult.ValueType) -> str:
    return error_pb2.ActionResult.Name(answer)


class _PowerSandbox:
    """A game as Protoss with the map shown, costs off, the computer left one structure, and a forge of this player's
    far off, so that a photon cannon may be placed."""

    def __init__(self, game: Sandbox) -> None:
        self.game = game
        game.cheat(Cheat.SHOW_MAP, Cheat.FREE)
        game.client.step(4)
        game.leave_the_computer_one_structure()
        observation = game.client.observation()
        self.game_map = GameMap._from_game(game.client.game_info(), observation)
        self.game_data = GameData(game.client.game_data())
        self.ground = OpenGround(self.game_map, game.units())
        self.heights = numpy.array(self.game_map.height.values, dtype=float)
        self.origin = self.game_map.placement.origin
        self.player = game.player
        self.enemy = 2 if game.player == 1 else 1
        middle = self.game_map.playable_area.center
        self.game.spawn([(UnitTypeId.FORGE, self.player, self.ground.claim(Point((middle.x - 30, middle.y)), half=2))])
        # The ground left open once the forge stands, before any sweep claims more.
        self.open_tiles = self.ground.free_tiles.copy()

    def listed_power_sources(self) -> list[dict[str, Any]]:
        raw = self.game.client.observation().observation.raw_data
        return [{"x": s.pos.x, "y": s.pos.y, "radius": s.radius, "tag": s.tag} for s in raw.player.power_sources]

    def placement_grids_now(self) -> PlacementGrids:
        """The grids `api.placement` would hold on a fresh observation."""
        observation = self.game.client.observation()
        order_book = OrderBook(self.game_data)
        tracker = _Tracker(self.game_data, None, order_book=order_book)
        curated_raw_data, _ = without_uncurated_units(observation.observation.raw_data)
        step = observation.observation.game_loop
        tracker.update(curated_raw_data, step)
        return PlacementGrids._build_at_start(
            self.game_map,
            present_units=tracker.unit_tracker.present,
            state=_State(observation, tracker, self.game_map),
            step=step,
            game_data=self.game_data,
            order_book=order_book,
        )

    def claim_largest_square(self, near: Point) -> Point:
        """The center of the largest free square near `near`, from 17 tiles across down to 7."""
        for half in range(8, 2, -1):
            try:
                at = self.ground.claim(near, half=half)
            except RuntimeError:
                continue
            logger.info("Claimed {} tiles across at {}", 2 * half + 1, at)
            return at
        raise RuntimeError(f"no free ground 7 tiles across is left near {near}")

    def heights_touching(self, x: float, y: float) -> tuple[float, float]:
        """The lowest and the highest height of the tiles touching the point: one at a tile's center, two at the middle
        of a tile's side, four at its corner."""
        grid_x, grid_y = x - self.origin.x, y - self.origin.y
        columns = [math.floor(grid_x)] if grid_x % 1 else [int(grid_x) - 1, int(grid_x)]
        rows = [math.floor(grid_y)] if grid_y % 1 else [int(grid_y) - 1, int(grid_y)]
        width, depth = self.heights.shape
        values = [float(self.heights[a, b]) for a in columns for b in rows if 0 <= a < width and 0 <= b < depth]
        return (min(values), max(values)) if values else (math.nan, math.nan)

    def answers_around(
        self, source: Point, structure: UnitTypeId, *, reach: float, grids: PlacementGrids | None = None
    ) -> list[dict[str, Any]]:
        """The answer to placing `structure` at every center that lies within `reach` of `source` along x and y, its
        distance from `source`, the heights of the tiles it touches, and with `grids`, whether `powered_for` says it
        is powered."""
        ability, odd = _POWERED_STRUCTURES[structure]
        offset = 0.5 if odd else 0.0
        center_x, center_y = math.floor(source.x), math.floor(source.y)
        steps = range(-math.ceil(reach), math.ceil(reach) + 1)
        points = [Point((center_x + dx + offset, center_y + dy + offset)) for dx in steps for dy in steps]
        answers = self.game.placement_answers(ability, points)
        records = []
        for point, answer in zip(points, answers, strict=True):
            record: dict[str, Any] = {
                "structure": structure.name,
                "x": point.x,
                "y": point.y,
                "distance": math.dist(point, source),
                "answer": _answer_name(answer),
                "heights_touching": self.heights_touching(point.x, point.y),
            }
            if grids is not None:
                record["powered_for"] = bool(grids.powered_for(structure)[point])
            records.append(record)
        return records


def _summary_of(records: list[dict[str, Any]]) -> dict[str, Any]:
    """How many centers the game accepted and how far out, how many it refused for power and from how near, the
    other answers it gave, and the answers `powered_for` got wrong."""
    accepted = [r["distance"] for r in records if r["answer"] == _SUCCESS_ANSWER]
    refused_for_power = [r["distance"] for r in records if r["answer"] == _TOO_FAR_FROM_POWER]
    other_answers = Counter(r["answer"] for r in records if r["answer"] not in (_SUCCESS_ANSWER, _TOO_FAR_FROM_POWER))
    return {
        "accepted": len(accepted),
        "farthest_accepted": max(accepted, default=None),
        "refused_for_power": len(refused_for_power),
        "nearest_refused_for_power": min(refused_for_power, default=None),
        "other_answers": dict(other_answers),
        "powered_for_wrong": _where_powered_for_is_wrong(records),
    }


def _where_powered_for_is_wrong(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records whose answer is a success or the power error and whose `powered_for` says otherwise."""
    return [
        record
        for record in records
        if "powered_for" in record
        and record["answer"] in (_SUCCESS_ANSWER, _TOO_FAR_FROM_POWER)
        and record["powered_for"] != (record["answer"] == _SUCCESS_ANSWER)
    ]


def _unit_under_tag(game: Sandbox, tag: int) -> raw_pb2.Unit:
    """The unit under `tag` in a fresh observation; raises `RuntimeError` if it lists none."""
    if (unit := next((unit for unit in game.units() if unit.tag == tag), None)) is None:
        raise RuntimeError(f"the game lists no unit under tag {tag}")
    return unit


def sweep_sources(installation: Installation) -> dict[str, Any]:
    """What the game lists as power sources, and how far a pylon and a phasing warp prism reach on open ground."""
    with playing(Race.PROTOSS, installation) as game:
        sandbox = _PowerSandbox(game)
        middle = sandbox.game_map.playable_area.center
        findings: dict[str, Any] = {"listed_at_the_start": sandbox.listed_power_sources()}
        findings["pylon"] = _sweep_a_pylon(sandbox, sandbox.claim_largest_square(Point((middle.x + 20, middle.y))))
        findings["enemy_pylon"] = _sweep_an_enemy_pylon(
            sandbox, sandbox.claim_largest_square(Point((middle.x, middle.y + 22)))
        )
        findings["warp_prism"] = _sweep_a_warp_prism(
            sandbox, sandbox.claim_largest_square(Point((middle.x, middle.y - 22)))
        )
        findings["built_pylon"] = _sweep_a_pylon_built(
            sandbox, sandbox.claim_largest_square(Point((middle.x - 20, middle.y + 16)))
        )
        return findings


def _sweep_a_pylon(sandbox: _PowerSandbox, site: Point) -> dict[str, Any]:
    game = sandbox.game
    (pylon,) = game.spawn([(UnitTypeId.PYLON, sandbox.player, site)])
    pylon_at = Point((pylon.pos.x, pylon.pos.y))
    findings: dict[str, Any] = {"at": list(pylon_at), "listed": sandbox.listed_power_sources(), "around": {}}
    grids = sandbox.placement_grids_now()
    for structure in _POWERED_STRUCTURES:
        records = sandbox.answers_around(pylon_at, structure, reach=_SOURCES_SWEEP_REACH, grids=grids)
        findings["around"][structure.name] = {"summary": _summary_of(records), "records": records}
    pylon_answers = game.placement_answers(AbilityId.PROBE_BUILD_PYLON, [Point((pylon_at.x + 2, pylon_at.y))])
    findings["a_pylon_beside_it"] = _answer_name(pylon_answers[0])
    findings["orders_at_the_edge"] = _orders_at_the_edge(sandbox, findings["around"]["GATEWAY"]["records"])
    game.remove([pylon.tag])
    findings["listed_once_it_died"] = sandbox.listed_power_sources()
    game.client.step(_SETTLING_STEPS)
    return findings


def _orders_at_the_edge(sandbox: _PowerSandbox, gateway_records: list[dict[str, Any]]) -> dict[str, Any]:
    """A probe's order to build a gateway at the farthest center the query accepted and at the nearest it refused for
    power, and the game's verdict on each."""
    game = sandbox.game
    middle = sandbox.game_map.playable_area.center
    (probe,) = game.spawn(
        [(UnitTypeId.PROBE, sandbox.player, sandbox.ground.claim(Point((middle.x + 20, middle.y - 16)), half=1))]
    )
    farthest_accepted = max((r for r in gateway_records if r["answer"] == _SUCCESS_ANSWER), key=lambda r: r["distance"])
    nearest_refused = min(
        (r for r in gateway_records if r["answer"] == _TOO_FAR_FROM_POWER), key=lambda r: r["distance"]
    )
    orders = {}
    for label, record in (("nearest_refused", nearest_refused), ("farthest_accepted", farthest_accepted)):
        verdict = game.order(AbilityId.PROBE_BUILD_GATEWAY, probe.tag, Point((record["x"], record["y"])))
        orders[label] = {"site": record, "order": _answer_name(verdict)}
        game.client.step(2)
    game.remove([probe.tag])
    return orders


def _sweep_an_enemy_pylon(sandbox: _PowerSandbox, site: Point) -> dict[str, Any]:
    game = sandbox.game
    (pylon,) = game.spawn([(UnitTypeId.PYLON, sandbox.enemy, site)])
    pylon_at = Point((pylon.pos.x, pylon.pos.y))
    records = sandbox.answers_around(
        pylon_at, UnitTypeId.GATEWAY, reach=_SOURCES_SWEEP_REACH, grids=sandbox.placement_grids_now()
    )
    findings = {
        "at": list(pylon_at),
        "listed": sandbox.listed_power_sources(),
        "gateway": {"summary": _summary_of(records), "records": records},
    }
    game.remove([pylon.tag])
    return findings


def _sweep_a_warp_prism(sandbox: _PowerSandbox, site: Point) -> dict[str, Any]:
    game = sandbox.game
    (prism,) = game.spawn([(UnitTypeId.WARP_PRISM, sandbox.player, site)])
    prism_at = Point((prism.pos.x, prism.pos.y))
    transport_records = sandbox.answers_around(
        prism_at, UnitTypeId.GATEWAY, reach=_SOURCES_SWEEP_REACH, grids=sandbox.placement_grids_now()
    )
    findings: dict[str, Any] = {
        "at": list(prism_at),
        "listed_in_transport_mode": sandbox.listed_power_sources(),
        "gateway_in_transport_mode": _summary_of(transport_records),
        "phasing_order": _answer_name(game.order(AbilityId.WARP_PRISM_PHASING_MODE, prism.tag)),
    }
    timeline = []
    for step in range(0, 64, 2):
        unit_type = UnitTypeId(_unit_under_tag(game, prism.tag).unit_type).name
        timeline.append({"step": step, "type": unit_type, "listed": sandbox.listed_power_sources()})
        game.client.step(2)
    findings["after_the_phasing_order"] = timeline
    unit = _unit_under_tag(game, prism.tag)
    prism_at = Point((unit.pos.x, unit.pos.y))
    findings["phasing_at"] = list(prism_at)
    grids = sandbox.placement_grids_now()
    findings["around"] = {}
    for structure in (UnitTypeId.GATEWAY, UnitTypeId.PHOTON_CANNON):
        records = sandbox.answers_around(prism_at, structure, reach=_SOURCES_SWEEP_REACH, grids=grids)
        findings["around"][structure.name] = {"summary": _summary_of(records), "records": records}
    game.remove([prism.tag])
    return findings


def _sweep_a_pylon_built(sandbox: _PowerSandbox, site: Point) -> dict[str, Any]:
    """A pylon a probe builds, every eight steps from when it stands until two observations after it finishes."""
    game = sandbox.game
    pylon_site = Point((math.floor(site.x), math.floor(site.y)))
    (builder,) = game.spawn([(UnitTypeId.PROBE, sandbox.player, Point((site.x + 4, site.y)))])
    findings: dict[str, Any] = {"order": _answer_name(game.order(AbilityId.PROBE_BUILD_PYLON, builder.tag, pylon_site))}
    gateway_site = Point((pylon_site.x + 3.5, pylon_site.y + 0.5))
    timeline: list[dict[str, Any]] = []
    for _ in range(60):
        game.client.step(8)
        built = [
            unit
            for unit in game.units()
            if unit.unit_type == UnitTypeId.PYLON and math.dist((unit.pos.x, unit.pos.y), pylon_site) < 1
        ]
        if not built:
            continue
        (answer,) = game.placement_answers(AbilityId.PROBE_BUILD_GATEWAY, [gateway_site])
        timeline.append(
            {
                "game_loop": game.client.observation().observation.game_loop,
                "build_progress": built[0].build_progress,
                "listed": sandbox.listed_power_sources(),
                "gateway_beside_it": _answer_name(answer),
            }
        )
        if built[0].build_progress >= 1 and len(timeline) > 2 and timeline[-2]["build_progress"] >= 1:
            break
    findings["timeline"] = timeline
    return findings


def sweep_edge(installation: Installation) -> dict[str, Any]:
    """A gateway and a photon cannon centered exactly on a phasing warp prism's radius, and a tile either side."""
    findings = {}
    with playing(Race.PROTOSS, installation) as game:
        sandbox = _PowerSandbox(game)
        middle = sandbox.game_map.playable_area.center
        prism_site = sandbox.ground.claim(Point((middle.x, middle.y - 20)), half=6)
        # The prism stands a quarter off the site, so that a center 3.75 along x from it is one the game snaps to.
        for structure, quarter in ((UnitTypeId.GATEWAY, 0.25), (UnitTypeId.PHOTON_CANNON, -0.25)):
            (prism,) = game.spawn(
                [(UnitTypeId.WARP_PRISM, sandbox.player, Point((prism_site.x + quarter, prism_site.y)))]
            )
            game.order(AbilityId.WARP_PRISM_PHASING_MODE, prism.tag)
            game.client.step(_SETTLING_STEPS)
            (source,) = sandbox.listed_power_sources()
            source_at = Point((source["x"], source["y"]))
            on_the_radius = Point((source_at.x + source["radius"], source_at.y))
            sites = [Point((on_the_radius.x + dx, on_the_radius.y)) for dx in (-1, 0, 1)]
            ability, _ = _POWERED_STRUCTURES[structure]
            answers = game.placement_answers(ability, sites)
            powered = sandbox.placement_grids_now().powered_for(structure)
            records = [
                {"x": p.x, "distance": math.dist(p, source_at), "answer": _answer_name(a), "powered_for": powered[p]}
                for p, a in zip(sites, answers, strict=True)
            ]
            findings[structure.name] = {
                "source": source,
                "sites": records,
                "powered_for_wrong": _where_powered_for_is_wrong(records),
            }
            logger.info("{} on the edge: {}", structure.name, findings[structure.name])
            game.remove([prism.tag])
            game.client.step(_SETTLING_STEPS)
    return findings


def _cliff_sites(sandbox: _PowerSandbox, *, source_higher: bool) -> list[Point]:
    """The tile corners a pylon may be centered on beside a cliff, on flat open ground, with at least
    `_MIN_SITES_ON_THE_OTHER_LEVEL` flat open 3x3 sites within 6 of it on the other level, below it if
    `source_higher`, the most such sites first, at least `_CLIFF_SITES_APART` from each other."""
    free = sandbox.open_tiles
    heights = sandbox.heights
    width, depth = free.shape
    candidates = []
    for i in range(2, width - 2):
        for j in range(2, depth - 2):
            pylon_heights = heights[i - 1 : i + 1, j - 1 : j + 1]
            if (
                not free[i - 1 : i + 1, j - 1 : j + 1].all()
                or pylon_heights.max() - pylon_heights.min() > _MAX_UNEVENNESS_OF_FLAT_GROUND
            ):
                continue
            pylon_height = float(pylon_heights.mean())
            count = 0
            for a in range(max(1, i - 7), min(width - 1, i + 7)):
                for b in range(max(1, j - 7), min(depth - 1, j + 7)):
                    site_heights = heights[a - 1 : a + 2, b - 1 : b + 2]
                    if math.dist((a + 0.5, b + 0.5), (i, j)) > 6.0 or not free[a - 1 : a + 2, b - 1 : b + 2].all():
                        continue
                    if site_heights.max() - site_heights.min() > _MAX_UNEVENNESS_OF_FLAT_GROUND:
                        continue
                    difference = float(site_heights.mean()) - pylon_height
                    if (difference < -1.0) if source_higher else (difference > 1.0):
                        count += 1
            if count >= _MIN_SITES_ON_THE_OTHER_LEVEL:
                candidates.append((count, i, j))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))
    chosen: list[Point] = []
    for _, i, j in candidates:
        corner = Point((sandbox.origin.x + i, sandbox.origin.y + j))
        if all(math.dist(corner, other) >= _CLIFF_SITES_APART for other in chosen):
            chosen.append(corner)
        if len(chosen) == _CLIFF_SITES_A_SIDE:
            break
    return chosen


def _level_of(heights_touching: tuple[float, float], source_heights: tuple[float, float]) -> str:
    """Where the tiles a center touches lie against the highest tile under the source: a level or more below it, on
    its level, all of them a level or more above it, or some of them above it by less or not all."""
    low, high = heights_touching
    source_high = source_heights[1]
    if high <= source_high:
        return "lower" if high < source_high - 1.0 else "same"
    return "higher" if low > source_high + 1.0 else "slightly or partly higher"


def _sweep_levels_on(installation: Installation, map_name: str) -> dict[str, Any]:
    findings: dict[str, Any] = {"map": map_name, "sources": []}
    with playing(Race.PROTOSS, installation, map_name=map_name) as game:
        sandbox = _PowerSandbox(game)
        for source_higher in (True, False):
            sites = _cliff_sites(sandbox, source_higher=source_higher)
            side = "above" if source_higher else "below"
            logger.info("{}: {} sites with the source {} the cliff", map_name, len(sites), side)
            for source_type in (UnitTypeId.PYLON, UnitTypeId.WARP_PRISM):
                findings["sources"].extend(_sweep_sources_at(sandbox, sites, source_type, side))
    return findings


def _sweep_sources_at(
    sandbox: _PowerSandbox, sites: Sequence[Point], source_type: UnitTypeId, side: str
) -> list[dict[str, Any]]:
    """A source of `source_type` at each of `sites`, phasing if a warp prism, and the answers around each."""
    game = sandbox.game
    made = game.spawn([(source_type, sandbox.player, site) for site in sites])
    if source_type is UnitTypeId.WARP_PRISM:
        for prism in made:
            game.order(AbilityId.WARP_PRISM_PHASING_MODE, prism.tag)
        game.client.step(_SETTLING_STEPS)
    grids = sandbox.placement_grids_now()
    raw = game.client.observation().observation.raw_data
    swept = []
    for source in raw.player.power_sources:
        unit = next(u for u in raw.units if u.tag == source.tag)
        source_at = Point((source.pos.x, source.pos.y))
        # A pylon stands on the four tiles around the corner it is centered on; under a prism lies the tile it is over.
        nudge = 0.0 if source_type is UnitTypeId.PYLON else 1e-6
        source_heights = sandbox.heights_touching(source_at.x + nudge, source_at.y + nudge)
        records = [
            record
            for structure in (UnitTypeId.GATEWAY, UnitTypeId.PHOTON_CANNON)
            for record in sandbox.answers_around(source_at, structure, reach=source.radius, grids=grids)
            if record["distance"] < source.radius
        ]
        for record in records:
            record["level"] = _level_of(record["heights_touching"], source_heights)
        swept.append(
            {
                "type": UnitTypeId(unit.unit_type).name,
                "side": side,
                "at": list(source_at),
                "radius": source.radius,
                "heights": source_heights,
                "tally": _tally_by_level(records),
                "powered_for_wrong": _where_powered_for_is_wrong(records),
                "records": records,
            }
        )
    game.remove([unit.tag for unit in made])
    game.client.step(_SETTLING_STEPS)
    return swept


def _tally_by_level(records: list[dict[str, Any]]) -> dict[str, int]:
    """How many centers on each level got each answer."""
    return dict(Counter(f"{r['structure']} {r['level']}: {r['answer']}" for r in records))


def sweep_levels(installation: Installation, map_names: Sequence[str]) -> dict[str, Any]:
    """Pylons and phasing warp prisms beside cliffs on each of `map_names`, and the answers around each, with a tally
    of all of them by the source's type, the center's level and the answer."""
    maps = [_sweep_levels_on(installation, map_name) for map_name in map_names]
    tally: Counter[str] = Counter()
    wrong = 0
    for swept_map in maps:
        for source in swept_map["sources"]:
            tally.update({f"{source['type']} {key}": count for key, count in source["tally"].items()})
            wrong += len(source["powered_for_wrong"])
    return {"tally": dict(sorted(tally.items())), "powered_for_wrong": wrong, "maps": maps}


def _is_off_level(height: float) -> bool:
    """Whether `height` lies off the levels `_LEVELS_APART` apart; not for a height off the map, not a number."""
    return not math.isnan(height) and abs(height - round(height / _LEVELS_APART) * _LEVELS_APART) > 1e-6


def _uneven_sites(sandbox: _PowerSandbox) -> list[Point]:
    """Every tile corner a pylon may be centered on, on open ground, among the four tiles around which lies an open tile
    whose height lies off the levels."""
    free = sandbox.open_tiles
    width, depth = free.shape
    off_level = numpy.vectorize(_is_off_level)(sandbox.heights) & free
    corners = set()
    for i, j in zip(*numpy.nonzero(off_level), strict=True):
        for corner in ((int(i) + di, int(j) + dj) for di in (0, 1) for dj in (0, 1)):
            inside = 1 <= corner[0] < width and 1 <= corner[1] < depth
            if inside and free[corner[0] - 1 : corner[0] + 1, corner[1] - 1 : corner[1] + 1].all():
                corners.add(corner)
    return [Point((sandbox.origin.x + i, sandbox.origin.y + j)) for i, j in sorted(corners)]


def _batches_apart(sites: Sequence[Point], apart: float) -> list[list[Point]]:
    """`sites` in batches, each site in the first batch whose every site lies at least `apart` from it."""
    batches: list[list[Point]] = []
    for site in sites:
        batch = next((batch for batch in batches if all(math.dist(site, other) >= apart for other in batch)), None)
        if batch is None:
            batches.append([site])
        else:
            batch.append(site)
    return batches


def _relation_of(center_heights: tuple[float, float], pylon_heights: tuple[float, float]) -> str:
    """Whether the highest tile a center touches lies below the highest a pylon stands on, level with it, or above."""
    if center_heights[1] < pylon_heights[1]:
        return "lower"
    return "same" if center_heights[1] == pylon_heights[1] else "higher"


def _sweep_uneven_on(installation: Installation, map_name: str) -> dict[str, Any]:
    """A pylon at each of `_uneven_sites` on `map_name`, in batches no two of which reach a center together, the
    heights of the tiles each stands on, and the answer to a gateway and a photon cannon at every center within its
    reach."""
    swept_sources = []
    with playing(Race.PROTOSS, installation, map_name=map_name) as game:
        sandbox = _PowerSandbox(game)
        batches = _batches_apart(_uneven_sites(sandbox), _UNEVEN_PYLONS_APART)
        logger.info("{}: {} pylons in {} batches", map_name, sum(map(len, batches)), len(batches))
        for batch in batches:
            made = game.spawn([(UnitTypeId.PYLON, sandbox.player, site) for site in batch])
            grids = sandbox.placement_grids_now()
            for source in game.client.observation().observation.raw_data.player.power_sources:
                source_at = Point((source.pos.x, source.pos.y))
                pylon_heights = sandbox.heights_touching(source_at.x, source_at.y)
                records = [
                    record
                    for structure in (UnitTypeId.GATEWAY, UnitTypeId.PHOTON_CANNON)
                    for record in sandbox.answers_around(source_at, structure, reach=source.radius, grids=grids)
                    if record["distance"] < source.radius
                ]
                for record in records:
                    record["relation"] = _relation_of(record["heights_touching"], pylon_heights)
                swept_sources.append({"at": list(source_at), "heights": pylon_heights, "records": records})
            game.remove([unit.tag for unit in made])
            game.client.step(_SETTLING_STEPS)
    records = [record for source in swept_sources for record in source["records"]]
    return {"map": map_name, "sources": swept_sources, "powered_for_wrong": _where_powered_for_is_wrong(records)}


def sweep_uneven(installation: Installation, map_names: Sequence[str]) -> dict[str, Any]:
    """Pylons on and beside ground off the levels on each of `map_names`, each alone, and the answers around each,
    tallied by whether the pylon's highest tile lies off the levels, by where the highest tile a center touches lies
    against the pylon's, and by the answer."""
    maps = [_sweep_uneven_on(installation, map_name) for map_name in map_names]
    tally: Counter[str] = Counter()
    for source in (source for swept_map in maps for source in swept_map["sources"]):
        pylon = "pylon off the levels" if _is_off_level(source["heights"][1]) else "pylon on a level"
        tally.update(
            f"{pylon}, center {r['relation']}: {r['answer']}"
            for r in source["records"]
            if r["answer"] in (_SUCCESS_ANSWER, _TOO_FAR_FROM_POWER)
        )
    return {
        "pylons": sum(len(swept_map["sources"]) for swept_map in maps),
        "tally": dict(sorted(tally.items())),
        "powered_for_wrong": sum(len(swept_map["powered_for_wrong"]) for swept_map in maps),
        "maps": maps,
    }


def main(argv: Sequence[str]) -> None:
    """Run the sweeps named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sweeps: dict[str, Callable[[Installation, Sequence[str]], object]] = {
        "sources": lambda installation, _: sweep_sources(installation),
        "edge": lambda installation, _: sweep_edge(installation),
        "levels": sweep_levels,
        "uneven": sweep_uneven,
    }
    parser.add_argument("sweeps", nargs="*", help=f"{', '.join(sweeps)}; all when none are named")
    pool = sorted({game.map for game in GAMES})
    parser.add_argument(
        "--maps", nargs="*", default=pool, help="the maps `levels` and `uneven` sweep; the pool's by default"
    )
    parser.add_argument("--out", type=Path, default=Path("power.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(sweeps):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings = {
        name: sweep(installation, arguments.maps)
        for name, sweep in sweeps.items()
        if name in arguments.sweeps or not arguments.sweeps
    }
    arguments.out.write_text(json.dumps(findings, indent=1, default=str), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
