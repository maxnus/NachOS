"""Find what the game lists of a structure remembered in the fog that is seen again for a moment between two
observations, and what NachOS's tracker makes of the two observations.

Needs StarCraft II installed. Each trial plays its own game against the computer on the sandbox's map. An enemy
engineering bay stands near the middle of the map, made by a debug command; a marine made beside it shows it to this
player and is killed, and the bay is in the fog once the dead marine's sight lapses, which each trial measures. Then,
between two observations as far apart as that lapse and 8 steps, a marine made beside the bay shows it again and is
killed as soon as it is listed:

- `retag` records each of the two observations' copies of the bay, by its tag, type and exact position, and what an
  observation between them lists of it; and what NachOS's tracker reports found dead and first seen when it is updated
  with the two alone, as a bot taking a turn between them is.
- `rebuilt` first kills the bay out of sight and makes another at the same point, so that the copy before is of a
  bay that has died and the copy after of a new one, and records the same: whether the new bay's copy stands at the
  old copy's bit-identical position, as a rule matching copies by their position would need it not to.

Run it as::

    uv run python tools/sweep_fog.py
    uv run python tools/sweep_fog.py retag --out fog-retag.json

The findings are written as JSON, one object per sweep.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from _sandbox import MAP, OpenGround, Sandbox, playing
from loguru import logger
from s2clientprotocol import raw_pb2, sc2api_pb2

from sc2nachos.enemy import Enemy
from sc2nachos.gamedata import GameData
from sc2nachos.gamemap import GameMap
from sc2nachos.geometry import Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.units._tracking._tracker import _Tracker

_SNAPSHOT_DISPLAY = raw_pb2.DisplayType.Snapshot
_VISIBLE_DISPLAY = raw_pb2.DisplayType.Visible
_ENEMY_ALLIANCE = raw_pb2.Alliance.Enemy
# The most steps waited for the marine that shows the bay to be made, and for a dead watcher's sight to lapse.
_MOST_STEPS_TO_MAKE_THE_WATCHER = 16
_MOST_STEPS_SIGHT_LINGERS = 224
# The steps let pass after a unit is made or killed, before the next thing is done.
_SETTLE_STEPS = 8
# How far from the bay the marine that shows it stands.
_WATCHER_DISTANCE = 3.0


class _BayInTheFog:
    """A game against the computer with an enemy engineering bay this player has seen and lost sight of, and a
    tracker of this player's that has seen it so."""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        client = sandbox.client
        info = client.game_info()
        observation = client.observation()
        game_map = GameMap._from_game(info, observation)
        self.tracker = _Tracker(GameData(client.game_data()), Enemy._from_game(info, sandbox.player))
        self.enemy_player = 2 if sandbox.player == 1 else 1
        open_ground = OpenGround(game_map, observation.observation.raw_data.units)
        self.site = open_ground.claim(game_map.playable_area.center, half=2)
        self.watching_point = Point((self.site.x + _WATCHER_DISTANCE, self.site.y))
        self.make_bay()
        bay, self.steps_its_sight_lingers = self._watch_and_kill_the_watcher(steps_in_sight=_SETTLE_STEPS)
        self.bay_tag = bay.tag

    def observe(self) -> sc2api_pb2.ResponseObservation:
        """A fresh observation, taken into the tracker."""
        observation = self.sandbox.client.observation()
        self.tracker.update(observation.observation.raw_data, observation.observation.game_loop)
        return observation

    def make_bay(self) -> None:
        """Make an enemy engineering bay at the site."""
        self.sandbox.debug(self.sandbox.create(UnitTypeId.ENGINEERING_BAY, self.enemy_player, self.site))
        self.sandbox.client.step(_SETTLE_STEPS)

    def _watch_and_kill_the_watcher(self, *, steps_in_sight: int) -> tuple[raw_pb2.Unit, int]:
        """Show the bay to this player with a marine for `steps_in_sight` steps, observing it, then kill the marine
        and wait, observing every step, until the bay is in the fog. Returns the bay as the marine saw it, and the
        steps from the kill to the first observation of the bay in the fog."""
        client = self.sandbox.client
        (watcher,) = self.sandbox.spawn([(UnitTypeId.MARINE, self.sandbox.player, self.watching_point)])
        client.step(steps_in_sight)
        bay = next(unit for unit in self.observe().observation.raw_data.units if _is_the_bay(unit, _VISIBLE_DISPLAY))
        self.sandbox.kill([watcher.tag])
        for steps_since_the_kill in range(1, _MOST_STEPS_SIGHT_LINGERS + 1):
            client.step(1)
            if _positions_of_copies(self.observe()):
                return bay, steps_since_the_kill
        raise RuntimeError(f"the bay was not in the fog {_MOST_STEPS_SIGHT_LINGERS} steps after its watcher died")

    def show_between_observations(self) -> dict[str, Any]:
        """Show the bay between two observations, by a marine made beside it and killed as soon as it is listed, the
        two observations as far apart as the bay takes to be back in the fog; and record its copy in each, what the game
        listed of it in between, and what the tracker, updated with the two alone, found."""
        sandbox = self.sandbox
        client = sandbox.client
        before = self.observe()
        sandbox.debug(sandbox.create(UnitTypeId.MARINE, sandbox.player, self.watching_point))
        # Taken from the client, not the tracker: the bot takes no turn between the two.
        for _ in range(_MOST_STEPS_TO_MAKE_THE_WATCHER):
            client.step(1)
            between = client.observation()
            watchers = [
                unit.tag
                for unit in between.observation.raw_data.units
                if unit.owner == sandbox.player and unit.unit_type == UnitTypeId.MARINE
            ]
            if watchers:
                break
        else:
            raise RuntimeError(f"no marine was made within {_MOST_STEPS_TO_MAKE_THE_WATCHER} steps")
        sandbox.kill(watchers)
        client.step(self.steps_its_sight_lingers + _SETTLE_STEPS)
        after = self.observe()
        changes = self.tracker.last_changes
        return {
            "steps a dead watcher's sight lingers": self.steps_its_sight_lingers,
            "steps": [before.observation.game_loop, between.observation.game_loop, after.observation.game_loop],
            "copy before": _bays_listed(before),
            "listed between": _bays_listed(between),
            "copy after": _bays_listed(after),
            "the copies stand bit for bit at one position": _positions_of_copies(before) == _positions_of_copies(after),
            "found dead by the tracker": [unit.type_id.name for unit in changes.units_found_dead],
            "first seen by the tracker": [unit.type_id.name for unit in changes.enemy_units_first_seen],
        }


def _is_the_bay(unit: raw_pb2.Unit, display: int) -> bool:
    """Whether `unit` is an enemy engineering bay shown as `display`."""
    return (
        unit.alliance == _ENEMY_ALLIANCE
        and unit.unit_type == UnitTypeId.ENGINEERING_BAY
        and unit.display_type == display
    )


def _bays_listed(observation: sc2api_pb2.ResponseObservation) -> list[dict[str, Any]]:
    """Every enemy engineering bay `observation` lists: its tag, display and exact position."""
    return [
        {
            "tag": hex(unit.tag),
            "display": raw_pb2.DisplayType.Name(unit.display_type),
            "position": [repr(unit.pos.x), repr(unit.pos.y)],
        }
        for unit in observation.observation.raw_data.units
        if unit.alliance == _ENEMY_ALLIANCE and unit.unit_type == UnitTypeId.ENGINEERING_BAY
    ]


def _positions_of_copies(observation: sc2api_pb2.ResponseObservation) -> list[tuple[float, float]]:
    """The exact positions of the copies of enemy engineering bays in `observation`."""
    return [
        (unit.pos.x, unit.pos.y)
        for unit in observation.observation.raw_data.units
        if _is_the_bay(unit, _SNAPSHOT_DISPLAY)
    ]


def sweep_retag(installation: Installation) -> dict[str, Any]:
    """The bay shown between two observations."""
    with playing(Race.TERRAN, installation, map_name=MAP) as sandbox:
        return _BayInTheFog(sandbox).show_between_observations()


def sweep_rebuilt(installation: Installation) -> dict[str, Any]:
    """A bay killed out of sight and another made at its point, shown between two observations."""
    with playing(Race.TERRAN, installation, map_name=MAP) as sandbox:
        game = _BayInTheFog(sandbox)
        sandbox.kill([game.bay_tag])
        sandbox.client.step(_SETTLE_STEPS)
        game.make_bay()
        return game.show_between_observations()


_SWEEPS_BY_NAME: dict[str, Callable[[Installation], object]] = {
    "retag": sweep_retag,
    "rebuilt": sweep_rebuilt,
}


def main(argv: Sequence[str]) -> None:
    """Run the sweeps named, or all of them, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweeps", nargs="*", help=f"{', '.join(_SWEEPS_BY_NAME)}; all when none are named")
    parser.add_argument("--out", type=Path, default=Path("fog.json"), help="where to write the findings")
    arguments = parser.parse_args(argv)
    if unknown := set(arguments.sweeps) - set(_SWEEPS_BY_NAME):
        raise SystemExit(f"No sweep is called {', '.join(sorted(unknown))}")
    installation = Installation.find()
    findings = {
        name: sweep(installation)
        for name, sweep in _SWEEPS_BY_NAME.items()
        if name in arguments.sweeps or not arguments.sweeps
    }
    arguments.out.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
