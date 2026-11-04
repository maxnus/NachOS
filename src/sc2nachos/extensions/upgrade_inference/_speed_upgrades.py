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
# By default, the probabilities the replays checked (#224): where an upgrade's score starts and stays at the least,
# where speed assumes the upgrade, and below where it forgets one it assumed.
_DEFAULT_FLOOR: Final = 0.01
_DEFAULT_ASSUMED_AT: Final = 0.99
_DEFAULT_FORGOTTEN_BELOW: Final = 0.5


def check_the_thresholds(
    *, floor_probability: float, assumed_at_probability: float, forgotten_below_probability: float
) -> None:
    """Raise `ValueError` unless `0 < floor_probability < forgotten_below_probability < assumed_at_probability < 1`: a
    score can then climb from its floor to where it is assumed, and fall back below where it is forgotten."""
    if not 0 < floor_probability < forgotten_below_probability < assumed_at_probability < 1:
        raise ValueError(
            "The speed upgrades' floor, where one is forgotten and where it is assumed must rise in that order between"
            f" 0 and 1, not {floor_probability}, {forgotten_below_probability} and {assumed_at_probability}"
        )


@final
class SpeedUpgradesFromMovement:
    """The enemy's speed upgrades read off how fast its units move, assumed with `Enemy.assume_upgrades`, as
    `UpgradeInference`'s docstring says."""

    __slots__ = (
        "_api",
        "_assumed_at_log_odds",
        "_assumed_by_speed",
        "_earliest_finish_steps",
        "_floor_log_odds",
        "_forgotten_below_log_odds",
        "_log_odds_by_upgrade",
    )

    def __init__(
        self,
        api: Api[object],
        *,
        floor_probability: float,
        assumed_at_probability: float,
        forgotten_below_probability: float,
    ) -> None:
        """Read the speed upgrades of the enemy of `api`'s game, which must have started and have an enemy, from its
        next turn, each score from `floor_probability`, assuming an upgrade at `assumed_at_probability` and forgetting
        one below `forgotten_below_probability`, as `check_the_thresholds` allows them."""
        self._api = api
        self._floor_log_odds = _log_odds_of_probability(floor_probability)
        self._assumed_at_log_odds = _log_odds_of_probability(assumed_at_probability)
        self._forgotten_below_log_odds = _log_odds_of_probability(forgotten_below_probability)
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
            self._count_a_slice_for(upgrade)
        for upgrade in upgrades_voted_against - upgrades_voted_for:
            self._count_a_slice_against(upgrade)

    def _may_vote(self, window: MovementWindow) -> bool:
        """Whether `window` may vote: it ran its whole block, after its upgrade could have finished, with its unit
        wearing no buff that changes its speed, and its predictions far enough apart to tell."""
        return (
            window.covers_its_whole_block
            and window.start_step >= self._earliest_finish_steps[window.upgrade]
            and window.buffs_worn.isdisjoint(_SPEED_CHANGING_BUFFS)
            and window.with_upgrade - window.without_upgrade >= _LEAST_GAP_AS_SHARE_OF_SPEED * window.without_upgrade
        )

    def _count_a_slice_for(self, upgrade: UpgradeId) -> None:
        """Raise `upgrade`'s score by a slice with a vote for it, and assume the upgrade if the score reached the
        threshold and the enemy is not held to have it already: again after the bot forgot one speed assumed."""
        log_odds = self._log_odds_by_upgrade.get(upgrade, self._floor_log_odds) + _LOG_ODDS_OF_A_SLICE_FOR
        self._log_odds_by_upgrade[upgrade] = log_odds
        enemy = self._api.enemy
        if log_odds >= self._assumed_at_log_odds and upgrade not in enemy.upgrades:
            enemy.assume_upgrades(upgrade)
            self._assumed_by_speed.add(upgrade)

    def _count_a_slice_against(self, upgrade: UpgradeId) -> None:
        """Lower `upgrade`'s score by a slice of only votes against it, no lower than the floor, and forget the upgrade
        if the score fell below where it is forgotten and speed assumed it."""
        log_odds = self._log_odds_by_upgrade.get(upgrade, self._floor_log_odds) - _LOG_ODDS_OF_A_SLICE_AGAINST
        self._log_odds_by_upgrade[upgrade] = max(self._floor_log_odds, log_odds)
        if log_odds < self._forgotten_below_log_odds and upgrade in self._assumed_by_speed:
            self._api.enemy.forget_upgrades(upgrade)
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
