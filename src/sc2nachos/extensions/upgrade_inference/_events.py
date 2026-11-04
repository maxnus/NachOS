"""The events upgrade inference hands out, an enemy upgrade it assumed and one it forgot, and how it changes
`Enemy.upgrades` to hand them out."""

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
    reads off the enemy's units. One the bot or upgrade inference forgot comes again once it is shown again.

    It comes at once, while upgrade inference handles `TurnStartEvent`, or `GameEndEvent` for the observation the game
    ended on, so before any of the bot's handlers of either. Several of one observation come in id order, those read off
    movement after the rest.
    """


class EnemyUpgradeForgottenEvent(AnyUpgradeEvent):
    """Upgrade inference has taken a speed upgrade it assumed out of `Enemy.upgrades`, its score having fallen below
    `forget_speed_upgrade_below`. Not for one the bot forgets, nor for one the bot forgot first. It comes at once, while
    upgrade inference handles `TurnStartEvent`, so before any of the bot's handlers of it. Several of one observation
    come in id order.
    """


def assume_and_emit_events(api: Api[object], upgrades: Iterable[UpgradeId]) -> None:
    """Assume the enemy of `api`'s game has `upgrades`, with `Enemy.assume_upgrades`, and hand out an
    `EnemyUpgradeAssumedEvent` for each upgrade `Enemy.upgrades` gained, in id order."""
    enemy = api.enemy
    held = enemy.upgrades
    enemy.assume_upgrades(*upgrades)
    for upgrade in sorted(enemy.upgrades - held):
        api.emit(EnemyUpgradeAssumedEvent(upgrade))


def forget_and_emit_events(api: Api[object], upgrades: Iterable[UpgradeId]) -> None:
    """Forget that the enemy of `api`'s game has `upgrades`, with `Enemy.forget_upgrades`, and hand out an
    `EnemyUpgradeForgottenEvent` for each upgrade `Enemy.upgrades` lost, in id order."""
    enemy = api.enemy
    held = enemy.upgrades
    enemy.forget_upgrades(*upgrades)
    for upgrade in sorted(held - enemy.upgrades):
        api.emit(EnemyUpgradeForgottenEvent(upgrade))
