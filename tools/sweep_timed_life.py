"""Find what the game reports of how long a unit that lasts a while has left: a force field, a reaper's grenade, a MULE
and broodlings, as the player who made it sees it and as its enemy does.

Needs StarCraft II installed. Each trial plays its own game of two on the sandbox's map, with no cheat on. The first
player makes the unit for real at open ground in the middle of the map, from a unit a debug command made there:

- `force-field`: a sentry casts a force field 3 tiles away;
- `grenade`: a reaper throws its grenade 3 tiles away;
- `mule`: an orbital command calls down a MULE 5 tiles away;
- `broodlings`: an evolution chamber is killed, and the broodlings come out.

The second player watches from an observer above the site, which nothing of the first player's can see. Both players
observe every step from the set-off until every unit first listed near the site after it has been gone from both views
for a while, and the sweep records, for each such unit as each player saw it: its type, alliance, owner and display;
the steps it was first and last listed at; the `buff_duration_remain` and `buff_duration_max` it reported first and
last, and whether the first counted down a step at a time; its health and build progress; and the step it was reported
dead at, if it was. It also records any effect listed near the site. Run it as::

    uv run python tools/sweep_timed_life.py
    uv run python tools/sweep_timed_life.py force-field grenade --out timed-life.json

The findings are written as JSON, one object per trial.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _sandbox import OpenGround, Rivals, Sandbox, playing_rivals
from loguru import logger
from s2clientprotocol import debug_pb2, error_pb2, raw_pb2

from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.ids.raw import RawEffectId, RawUnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race

_OPEN_GROUND_HALF_WIDTH = 6
# A unit first listed this near the site after the set-off is the trial's, and followed wherever it goes.
_TRIAL_REACH = 12.0
# The steps let pass after units are made or their energy set, before anything else is done.
_SETTLE_STEPS = 8
# The most steps a trial is watched: a MULE lasts some 1430.
_MOST_TRIAL_STEPS = 2400
# The steps waited for the set-off to list a unit, and those watched after the last of its units is gone, for a death
# reported late.
_MOST_STEPS_UNTIL_LISTED = 96
_STEPS_WATCHED_AFTER_ALL_GONE = 32
_ENERGY_VALUE = debug_pb2.DebugSetUnitValue.Energy
_FULL_ENERGY = 200.0
_CAST_DISTANCE = 3.0
_MULE_DROP_DISTANCE = 5.0
_HEALTH_SAMPLE_EVERY = 16


@dataclass(slots=True)
class _UnitReadings:
    """What one player's observations listed of one unit, every step it was listed."""

    unit_type: str
    alliances: set[str] = field(default_factory=set)
    owners: set[int] = field(default_factory=set)
    displays: set[str] = field(default_factory=set)
    steps_listed: list[int] = field(default_factory=list)
    durations: list[tuple[int, int]] = field(default_factory=list)
    """`buff_duration_remain` and `buff_duration_max`, at each step listed."""
    health_readings: list[tuple[float, float]] = field(default_factory=list)
    build_progress_readings: list[float] = field(default_factory=list)
    reported_dead_at: int | None = None

    def add(self, unit: raw_pb2.Unit, step: int) -> None:
        """Take in the unit as listed at `step`."""
        self.alliances.add(raw_pb2.Alliance.Name(unit.alliance))
        self.owners.add(unit.owner)
        self.displays.add(raw_pb2.DisplayType.Name(unit.display_type))
        self.steps_listed.append(step)
        self.durations.append((unit.buff_duration_remain, unit.buff_duration_max))
        self.health_readings.append((unit.health, unit.health_max))
        self.build_progress_readings.append(unit.build_progress)

    def summary(self, set_off_step: int) -> dict[str, Any]:
        """What the readings show, as written to the findings."""
        first_step = self.steps_listed[0]
        last_step = self.steps_listed[-1]
        return {
            "type": self.unit_type,
            "alliances": sorted(self.alliances),
            "owners": sorted(self.owners),
            "displays": sorted(self.displays),
            "steps from the set-off to the first listing": first_step - set_off_step,
            "first listed at": first_step,
            "last listed at": last_step,
            "observations listing it": len(self.steps_listed),
            "buff_duration_remain and _max, first": self.durations[0],
            "buff_duration_remain and _max, last": self.durations[-1],
            "the remaining duration counts down a step at a time": self._remaining_duration_counts_down(),
            "last listing minus the first's step and remaining duration": self._last_listing_after_duration_ran_out(),
            "health and maximum, first": self.health_readings[0],
            "health and maximum, last": self.health_readings[-1],
            "step and health, every 16th listing and the last": self._health_samples(),
            "build progress, first and last": [self.build_progress_readings[0], self.build_progress_readings[-1]],
            "reported dead at": self.reported_dead_at,
        }

    def _health_samples(self) -> list[tuple[int, float]]:
        """The step and health at every `_HEALTH_SAMPLE_EVERY`th listing, and at the last."""
        listings = list(zip(self.steps_listed, (health for health, _ in self.health_readings), strict=True))
        samples = listings[::_HEALTH_SAMPLE_EVERY]
        if samples[-1] != listings[-1]:
            samples.append(listings[-1])
        return samples

    def _remaining_duration_counts_down(self) -> bool | None:
        """Whether the remaining duration fell by one a step at every listing, or None if it was never reported."""
        if not any(remaining for remaining, _ in self.durations):
            return None
        ends = {step + remaining for step, (remaining, _) in zip(self.steps_listed, self.durations, strict=True)}
        return len(ends) == 1

    def _last_listing_after_duration_ran_out(self) -> int | None:
        """How many steps after the end the first remaining duration gave the unit was last listed, or None if it was
        never reported."""
        first_remaining, _ = self.durations[0]
        if not first_remaining:
            return None
        return self.steps_listed[-1] - (self.steps_listed[0] + first_remaining)


@dataclass(frozen=True, slots=True)
class _Trial:
    """How one trial makes the unit it sets off from, and sets it off: an order or a kill, whose verdict it returns."""

    make_actor: Callable[[Rivals, Point], raw_pb2.Unit]
    set_off: Callable[[Rivals, raw_pb2.Unit, Point], str]


def _make_for_me(rivals: Rivals, unit_type: UnitTypeId, at: Point) -> raw_pb2.Unit:
    """Make one `unit_type` for the first player at `at`, and return it as listed once made."""
    me = rivals.me
    me.debug(me.create(unit_type, me.player, at))
    rivals.step(_SETTLE_STEPS)
    made = [unit for unit in me.units() if unit.unit_type == unit_type and unit.owner == me.player]
    if not made:
        raise RuntimeError(f"the game did not make a {unit_type.name}")
    return min(made, key=lambda unit: Point((unit.pos.x, unit.pos.y)).distance_to(at))


def _make_at_the_site_with_full_energy(unit_type: UnitTypeId) -> Callable[[Rivals, Point], raw_pb2.Unit]:
    """A trial's actor of `unit_type` made at the site and given full energy."""

    def make_actor(rivals: Rivals, site: Point) -> raw_pb2.Unit:
        actor = _make_for_me(rivals, unit_type, site)
        rivals.me.set_value(_ENERGY_VALUE, _FULL_ENERGY, [actor.tag])
        rivals.step(_SETTLE_STEPS)
        return actor

    return make_actor


def _make_at_the_site(unit_type: UnitTypeId) -> Callable[[Rivals, Point], raw_pb2.Unit]:
    """A trial's actor of `unit_type` made at the site."""
    return lambda rivals, site: _make_for_me(rivals, unit_type, site)


def _order_at(ability: AbilityId, distance: float) -> Callable[[Rivals, raw_pb2.Unit, Point], str]:
    """A set-off ordering `ability` on the actor, aimed `distance` tiles from the site along the x axis."""

    def set_off(rivals: Rivals, actor: raw_pb2.Unit, site: Point) -> str:
        verdict = rivals.me.order(ability, actor.tag, Point((site.x + distance, site.y)))
        return error_pb2.ActionResult.Name(verdict)

    return set_off


def _kill_the_actor(rivals: Rivals, actor: raw_pb2.Unit, site: Point) -> str:
    """A set-off killing the actor by a debug command."""
    rivals.me.kill([actor.tag])
    return "killed by a debug command"


_TRIALS_BY_NAME: dict[str, _Trial] = {
    "force-field": _Trial(
        _make_at_the_site_with_full_energy(UnitTypeId.SENTRY), _order_at(AbilityId.SENTRY_FORCE_FIELD, _CAST_DISTANCE)
    ),
    "grenade": _Trial(_make_at_the_site(UnitTypeId.REAPER), _order_at(AbilityId.REAPER_GRENADE, _CAST_DISTANCE)),
    "mule": _Trial(
        _make_at_the_site_with_full_energy(UnitTypeId.ORBITAL_COMMAND),
        _order_at(AbilityId.ORBITAL_COMMAND_CALLDOWN_MULE, _MULE_DROP_DISTANCE),
    ),
    "broodlings": _Trial(_make_at_the_site(UnitTypeId.EVOLUTION_CHAMBER), _kill_the_actor),
}


def _trial_site(rivals: Rivals) -> Point:
    """The center of open ground in the middle of the map, with the second player's observer above it."""
    me = rivals.me
    game_map = GameMap._from_game(me.client.game_info(), me.client.observation())
    site = OpenGround(game_map, me.units()).claim(game_map.playable_area.center, _OPEN_GROUND_HALF_WIDTH)
    me.debug(me.create(UnitTypeId.OBSERVER, rivals.enemy.player, site))
    rivals.step(_SETTLE_STEPS)
    return site


def _distance_from(site: Point, unit: raw_pb2.Unit) -> float:
    return site.distance_to(Point((unit.pos.x, unit.pos.y)))


class _TrialWatch:
    """Both players' observations of a trial, every step from its set-off."""

    def __init__(self, rivals: Rivals, site: Point) -> None:
        self.rivals = rivals
        self.site = site
        self.views: dict[str, Sandbox] = {"maker's view": rivals.me, "enemy's view": rivals.enemy}
        self.tags_listed_before = {name: {unit.tag for unit in view.units()} for name, view in self.views.items()}
        self.readings: dict[str, dict[int, _UnitReadings]] = {name: {} for name in self.views}
        self.effects_near_the_site: dict[str, set[str]] = {name: set() for name in self.views}

    def watch(self) -> None:
        """Observe both views every step until the trial's units have been gone from both for a while."""
        steps_with_nothing_listed = 0
        for steps_watched in range(1, _MOST_TRIAL_STEPS + 1):
            self.rivals.step(1)
            anything_listed = False
            for name, view in self.views.items():
                anything_listed |= self._take_in(name, view)
            seen_any = any(self.readings.values())
            if anything_listed or (not seen_any and steps_watched < _MOST_STEPS_UNTIL_LISTED):
                steps_with_nothing_listed = 0
            else:
                steps_with_nothing_listed += 1
            if steps_with_nothing_listed >= _STEPS_WATCHED_AFTER_ALL_GONE:
                return
        logger.warning("The trial's units were still listed after {} steps", _MOST_TRIAL_STEPS)

    def _take_in(self, name: str, view: Sandbox) -> bool:
        """Take in one fresh observation of `view`, and return whether it lists any of the trial's units."""
        observation = view.client.observation().observation
        step = observation.game_loop
        readings = self.readings[name]
        anything_listed = False
        for unit in observation.raw_data.units:
            if unit.tag in self.tags_listed_before[name]:
                continue
            if unit.tag not in readings:
                if _distance_from(self.site, unit) > _TRIAL_REACH:
                    continue
                readings[unit.tag] = _UnitReadings(RawUnitTypeId(unit.unit_type).name)
            readings[unit.tag].add(unit, step)
            anything_listed = True
        for tag in observation.raw_data.event.dead_units:
            if tag in readings:
                readings[tag].reported_dead_at = step
        for effect in observation.raw_data.effects:
            if any(self.site.distance_to(Point((point.x, point.y))) <= _TRIAL_REACH for point in effect.pos):
                self.effects_near_the_site[name].add(RawEffectId(effect.effect_id).name)
        return anything_listed


def run_trial(trial: _Trial, installation: Installation) -> dict[str, Any]:
    """Play one trial in a game of its own, and return its findings."""
    with playing_rivals(Race.TERRAN, installation) as rivals:
        site = _trial_site(rivals)
        actor = trial.make_actor(rivals, site)
        enemy_sees_the_actor = any(unit.tag == actor.tag for unit in rivals.enemy.units())
        trial_watch = _TrialWatch(rivals, site)
        set_off_step = rivals.me.client.observation().observation.game_loop
        verdict = trial.set_off(rivals, actor, site)
        trial_watch.watch()
    return {
        "set off at": set_off_step,
        "verdict": verdict,
        "the enemy saw the actor before the set-off": enemy_sees_the_actor,
        **{
            name: [unit_readings.summary(set_off_step) for unit_readings in readings.values()]
            for name, readings in trial_watch.readings.items()
        },
        "effects listed near the site": {
            name: sorted(effects) for name, effects in trial_watch.effects_near_the_site.items()
        },
    }


def main(argv: Sequence[str]) -> None:
    """Run the trials named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trials", nargs="*", help=f"{', '.join(_TRIALS_BY_NAME)}; all when none are named")
    parser.add_argument("--out", type=Path, default=Path("timed-life.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.trials) - set(_TRIALS_BY_NAME):
        raise SystemExit(f"No trial is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings = {
        name: run_trial(trial, installation)
        for name, trial in _TRIALS_BY_NAME.items()
        if name in arguments.trials or not arguments.trials
    }
    arguments.out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
