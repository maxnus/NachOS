"""The event upgrade inference hands out, an enemy upgrade it assumed, and how it changes `Enemy.upgrades` to hand it
out."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2nachos.events import AnyUpgradeEvent

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos import Api
    from sc2nachos.ids import UpgradeId


class EnemyUpgradeAssumedEvent(AnyUpgradeEvent):
    """Upgrade inference has added an upgrade to `Enemy.upgrades`, which did not hold it: one it read off what only the
    upgrade can cause, or off how fast the enemy's units move. Not for one the bot assumes, nor for a level NachOS
    reads off the enemy's units. One the bot forgot comes again once it is shown again.

    It comes at once, while upgrade inference handles `TurnStartEvent`, or `GameEndEvent` for the observation the game
    ended on, so before any of the bot's handlers of either. Several of one observation come in id order, those read off
    movement after the rest.
    """


def assume_and_emit_events(api: Api[object], upgrades: Iterable[UpgradeId]) -> None:
    """Assume the enemy of `api`'s game has `upgrades`, with `Enemy.assume_upgrades`, and hand out an
    `EnemyUpgradeAssumedEvent` for each upgrade `Enemy.upgrades` gained, in id order."""
    enemy = api.enemy
    held = enemy.upgrades
    enemy.assume_upgrades(*upgrades)
    for upgrade in sorted(enemy.upgrades - held):
        api.emit(EnemyUpgradeAssumedEvent(upgrade))
