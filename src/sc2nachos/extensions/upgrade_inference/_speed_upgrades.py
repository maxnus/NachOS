"""The enemy's speed upgrades read off how fast its units move: what upgrade inference assumes with
`include_speed_upgrades`."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final, final

from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindows, speed_upgrade_by_type
from sc2nachos.ids import BuffId, UnitTypeId, UpgradeId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sc2nachos import Api
    from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindow
    from sc2nachos.gamedata import GameData


def _log_odds_of_probability(probability: float) -> float:
    return math.log(probability / (1 - probability))


# The rule, which tool `replay_movement` checked over 1,296 playbacks of AI Arena's 2026 Season 1 (#224): windows of
# this many steps on one clock, the windows of one upgrade's units in one of them a slice.
_WINDOW_STEPS: Final = 22
# A window votes for an upgrade where every observation's move in it came within this share of the gap between the
# two predictions of the one with the upgrade, and against it where every one came within it of the one without.
_VOTE_BAND_AS_SHARE_OF_GAP: Final = 0.05
# No window votes where the prediction with the upgrade is less than this share further than the one without, as an
# ultralisk's on creep is, 3% (tool `sweep_movement`).
_LEAST_GAP_AS_SHARE_OF_SPEED: Final = 0.1
# Nor one in which its unit wore a buff that changes its speed: Time Warp's slow to 0.6 is measured
# (`docs/reference/game-behavior/damage-and-buffs.md`), the others are taken from what their names say.
_SPEED_CHANGING_BUFFS: Final = frozenset(
    {
        BuffId.ACCELERATION_ZONE,
        BuffId.ACCELERATION_ZONE_FLYING,
        BuffId.HYDRALISK_LUNGE,
        BuffId.INFESTOR_FUNGAL_GROWTH,
        BuffId.INHIBITOR_ZONE,
        BuffId.INHIBITOR_ZONE_FLYING,
        BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW,
        BuffId.MOTHERSHIP_TIME_WARP,
        BuffId.PHOENIX_GRAVITON_BEAM,
        BuffId.VOID_RAY_PRISMATIC_ALIGNMENT,
    }
)
# The townhalls the players start with, which the earliest an upgrade one of them researches can finish waits for
# none of to be built.
_STARTING_TOWNHALLS: Final = frozenset({UnitTypeId.COMMAND_CENTER, UnitTypeId.HATCHERY, UnitTypeId.NEXUS})
# How much likelier a slice with a vote for the upgrade is from an enemy that has it than from one that lacks it, and a
# slice of only votes against from one that lacks it, as logarithms: by the rule of three, none of the 69,452 slices of
# an enemy that lacked an upgrade held a vote for it, and none of the 33,558 of one that had it only votes against
# (tool `replay_movement`, #224).
_LOG_ODDS_OF_A_SLICE_FOR: Final = math.log(23_000)
_LOG_ODDS_OF_A_SLICE_AGAINST: Final = math.log(11_000)
# Where an upgrade's score starts and stays at the least, a probability of 1%; where it is assumed, 99%; and below
# where speed forgets one it assumed, 50%.
_FLOOR_LOG_ODDS: Final = _log_odds_of_probability(0.01)
_ASSUMED_AT_LOG_ODDS: Final = _log_odds_of_probability(0.99)
_FORGOTTEN_BELOW_LOG_ODDS: Final = _log_odds_of_probability(0.5)


@final
class SpeedUpgradesFromMovement:
    """The enemy's speed upgrades read off how fast its units move, assumed with `Enemy.assume_upgrades`, as
    `UpgradeInference`'s docstring says."""

    __slots__ = ("_api", "_assumed_by_speed", "_earliest_finish_steps", "_log_odds_by_upgrade")

    def __init__(self, api: Api[object]) -> None:
        """Read the speed upgrades of the enemy of `api`'s game, which must have started and have an enemy, from its
        next turn."""
        self._api = api
        self._earliest_finish_steps = _earliest_finish_steps(api.data)
        self._log_odds_by_upgrade: dict[UpgradeId, float] = {}
        self._assumed_by_speed: set[UpgradeId] = set()
        MovementWindows(api, window_steps=_WINDOW_STEPS, on_windows=self._score_the_slices)

    def _score_the_slices(self, windows: Sequence[MovementWindow]) -> None:
        """Move each upgrade's score by its slice of `windows`, those of one block of steps, and assume or forget the
        upgrade where its score crossed a threshold."""
        upgrades_voted_for: set[UpgradeId] = set()
        upgrades_voted_against: set[UpgradeId] = set()
        for window in windows:
            if not self._may_vote(window):
                continue
            if _moved_steadily_at(window, 1.0):
                upgrades_voted_for.add(window.upgrade)
            elif _moved_steadily_at(window, 0.0):
                upgrades_voted_against.add(window.upgrade)
        for upgrade in upgrades_voted_for:
            self._move_the_score(upgrade, _LOG_ODDS_OF_A_SLICE_FOR)
        for upgrade in upgrades_voted_against - upgrades_voted_for:
            self._move_the_score(upgrade, -_LOG_ODDS_OF_A_SLICE_AGAINST)

    def _may_vote(self, window: MovementWindow) -> bool:
        """Whether `window` may vote: it ran its whole block, after its upgrade could have finished, with its unit
        wearing no buff that changes its speed, and its predictions far enough apart to tell."""
        return (
            window.covers_its_whole_block
            and window.start_step >= self._earliest_finish_steps[window.upgrade]
            and window.buffs_worn.isdisjoint(_SPEED_CHANGING_BUFFS)
            and window.with_upgrade - window.without_upgrade >= _LEAST_GAP_AS_SHARE_OF_SPEED * window.without_upgrade
        )

    def _move_the_score(self, upgrade: UpgradeId, log_odds_of_the_slice: float) -> None:
        """Add `log_odds_of_the_slice` to `upgrade`'s score, no lower than the floor, and assume the upgrade at or above
        the threshold, or forget it below the other if speed assumed it."""
        log_odds = max(_FLOOR_LOG_ODDS, self._log_odds_by_upgrade.get(upgrade, _FLOOR_LOG_ODDS) + log_odds_of_the_slice)
        self._log_odds_by_upgrade[upgrade] = log_odds
        enemy = self._api.enemy
        if log_odds >= _ASSUMED_AT_LOG_ODDS:
            if upgrade not in enemy.upgrades:
                enemy.assume_upgrades(upgrade)
                self._assumed_by_speed.add(upgrade)
        elif log_odds < _FORGOTTEN_BELOW_LOG_ODDS and upgrade in self._assumed_by_speed:
            enemy.forget_upgrades(upgrade)
            self._assumed_by_speed.discard(upgrade)


def _moved_steadily_at(window: MovementWindow, upgraded_share: float) -> bool:
    """Whether every observation's move in `window` came within the band of `upgraded_share` of the gap between its
    predictions: 0 at the speed without the upgrade, 1 at the speed with it. The window's whole move then did too."""
    return (
        window.least_step_share >= upgraded_share - _VOTE_BAND_AS_SHARE_OF_GAP
        and window.greatest_step_share <= upgraded_share + _VOTE_BAND_AS_SHARE_OF_GAP
    )


def _earliest_finish_steps(game_data: GameData) -> dict[UpgradeId, int]:
    """The earliest step each speed upgrade could finish: its research time, after the quickest of its researchers to
    build, but for a townhall a player starts with."""
    earliest_steps: dict[UpgradeId, int] = {}
    for upgrade in set(speed_upgrade_by_type(game_data).values()):
        upgrade_data = game_data.upgrades[upgrade]
        researcher_steps = min(
            (
                0 if researcher in _STARTING_TOWNHALLS else round(game_data.units[researcher].build_time.steps)
                for researcher in upgrade_data.researchers
            ),
            default=0,
        )
        earliest_steps[upgrade] = researcher_steps + round(upgrade_data.cost.time.steps)
    return earliest_steps
