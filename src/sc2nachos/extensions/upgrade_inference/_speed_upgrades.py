"""The enemy's speed upgrades read off how fast its units move: what upgrade inference assumes with
`include_speed_upgrades`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, final

from sc2nachos.extensions.upgrade_inference._events import assume_and_emit_events
from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindows
from sc2nachos.gametime import GameTime
from sc2nachos.ids import BuffId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sc2nachos import Api
    from sc2nachos.extensions.upgrade_inference._movement_windows import MovementWindow

# The length of the windows tool `replay_movement` checked the rule over, 1,296 playbacks of AI Arena's 2026 Season 1
# (#224), on one clock: none of the 69,452 slices from an enemy that lacked an upgrade, after the earliest it could
# finish, held a vote for it.
_DEFAULT_WINDOW: Final = GameTime(steps=22)
# By default, a window votes for an upgrade where every observation's move in it came within this share of the gap
# between the two predictions of the one with the upgrade: the band the replays checked (#224).
_DEFAULT_TOLERANCE: Final = 0.05
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


def check_the_speed_arguments(*, window: object, tolerance: float) -> None:
    """Raise `TypeError` unless `window` is a `GameTime`, and `ValueError` unless it is longer than 0 and
    `0 < tolerance < 0.5`: a unit then counts only nearer its speed with the upgrade than its speed without."""
    if not isinstance(window, GameTime):
        raise TypeError(f"the speed upgrades' window is a GameTime, not {window!r}")
    if not window.steps > 0:
        raise ValueError(f"the speed upgrades' window must be longer than 0, not {window}")
    if not 0 < tolerance < 0.5:
        raise ValueError(f"the speed upgrades' tolerance must lie above 0 and below 0.5, not {tolerance}")


@final
class SpeedUpgradesFromMovement:
    """The enemy's speed upgrades read off how fast its units move, assumed with `Enemy.assume_upgrades`, each one new
    handed out as an event, as `UpgradeInference`'s docstring says."""

    __slots__ = ("_api", "_tolerance")

    def __init__(self, api: Api[object], *, window: GameTime, tolerance: float) -> None:
        """Read the speed upgrades of the enemy of `api`'s game, which must have started and have an enemy, from its
        next turn, over windows of `window`, within `tolerance`, as `check_the_speed_arguments` allows them."""
        self._api = api
        self._tolerance = tolerance
        MovementWindows(api, window_steps=window.steps, on_windows=self._assume_the_upgrades_voted_for)

    def _assume_the_upgrades_voted_for(self, windows: Sequence[MovementWindow]) -> None:
        """Assume each upgrade a window of `windows`, those of one block of steps, voted for."""
        tolerance = self._tolerance
        voted_for = {window.upgrade for window in windows if _votes_for_its_upgrade(window, tolerance)}
        assume_and_emit_events(self._api, voted_for)


def _votes_for_its_upgrade(window: MovementWindow, tolerance: float) -> bool:
    """Whether `window` votes for its upgrade: it ran its whole block, with its unit wearing no buff that changes its
    speed, its predictions far enough apart to tell, and every observation's move in it came within `tolerance` of the
    gap between them of the speed with the upgrade. The window's whole move then did too."""
    return (
        window.covers_its_whole_block
        and window.buffs_worn.isdisjoint(_SPEED_CHANGING_BUFFS)
        and window.with_upgrade - window.without_upgrade >= _LEAST_GAP_AS_SHARE_OF_SPEED * window.without_upgrade
        and window.least_step_share >= 1 - tolerance
        and window.greatest_step_share <= 1 + tolerance
    )
