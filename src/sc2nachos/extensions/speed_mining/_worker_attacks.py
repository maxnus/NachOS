"""Speed mining's attacks: a worker it holds attacks an enemy it can hit as it stands, and goes back to work once it has
fired."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.gametime import GameTime
from sc2nachos.ids import AbilityId
from sc2nachos.units import UnitDict

if TYPE_CHECKING:
    from sc2nachos import Api
    from sc2nachos.units import OwnUnit, Target, Unit, Units, UnitType

# The most weapon cooldown, and the most turn toward an enemy, with which a worker attacks it: the first step of a turn
# costs nothing (in game).
_MOST_COOLDOWN_TO_ATTACK = GameTime(steps=1)
_MOST_TURN_TO_ATTACK = GameTime(steps=1)
# No worker's weapon reaches farther: an SCV's, a drone's and a probe's reach 0.19995, and no upgrade changes it
# (corpus). An enemy farther than this and both radii from a worker is out of its reach without the full check.
_MOST_WEAPON_RANGE_OF_A_WORKER = 0.2


@final
class WorkerAttacks:
    """The attacks speed mining gives the workers it holds, each on an enemy the worker can hit as it stands, kept until
    the worker has fired."""

    __slots__ = ("_api", "_target_by_attacker")

    def __init__(self, api: Api[object]) -> None:
        """No attack yet, for the workers of `api`'s game."""
        self._api = api
        self._target_by_attacker: UnitDict[OwnUnit[UnitType.Worker], Unit[Any]] = UnitDict()

    def attacks(self, worker: OwnUnit[UnitType.Worker], enemies: Units[Unit[Any]]) -> bool:
        """Whether `worker` attacks this turn: it is on speed mining's attack and has yet to fire, or it is given one on
        the weakest of `enemies` it can hit as it stands. Speed mining forgets an attack once the worker has fired."""
        ready = worker.weapon_cooldown <= _MOST_COOLDOWN_TO_ATTACK
        if (target := self._target_by_attacker.get(worker)) is not None:
            current_order = worker.current_order
            if (
                ready
                and current_order is not None
                and current_order.ability is AbilityId.ATTACK
                and current_order.known_target is target
            ):
                return True
            del self._target_by_attacker[worker]
        if not ready or not enemies:
            return False
        position, reach = worker.position, _MOST_WEAPON_RANGE_OF_A_WORKER + worker.radius
        reachable = [
            enemy
            for enemy in enemies
            if position.distance_to(enemy.position) <= reach + enemy.radius and _can_attack(worker, enemy)
        ]
        if not reachable:
            return False
        target = min(reachable, key=lambda enemy: enemy.life)
        self._api.orders.issue(worker, AbilityId.ATTACK, target=target)
        self._target_by_attacker[worker] = target
        return True

    def is_speed_minings_attack(self, worker: OwnUnit[UnitType.Worker], target: Target | None) -> bool:
        """Whether an attack of `worker`'s on `target`, the one it carries out or one it was given lately, is the
        attack speed mining gave it."""
        attacked = self._target_by_attacker.get(worker)
        return attacked is not None and target is attacked


def _can_attack(worker: OwnUnit[UnitType.Worker], enemy: Unit[Any]) -> bool:
    """Whether `worker` can hit `enemy` as it stands: in range, and no more than a step's turn away."""
    if not worker.has_in_range(enemy):
        return False
    turn = worker.time_to_face(enemy)
    return turn is not None and turn <= _MOST_TURN_TO_ATTACK
