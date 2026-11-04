"""Upgrade inference: the enemy's upgrades read off what only an upgrade can cause."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, final

from sc2nachos import NoEnemyError
from sc2nachos.events import EventPriority, TurnStartEvent
from sc2nachos.extensions.upgrade_inference._events import assume_and_emit_events
from sc2nachos.extensions.upgrade_inference._speed_upgrades import (
    _DEFAULT_TOLERANCE,
    _DEFAULT_WINDOW,
    SpeedUpgradesFromMovement,
    check_the_speed_arguments,
)
from sc2nachos.ids import BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos import Api
    from sc2nachos.gametime import GameTime
    from sc2nachos.state import Effect
    from sc2nachos.units import Unit

_NO_UPGRADES: Final[frozenset[UpgradeId]] = frozenset()
# Buffs whose wearer shows its owner has the upgrade the buff's ability needs.
_BUFF_EVIDENCE_OF_OWNER: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
    {
        BuffId.BANSHEE_CLOAK: UpgradeId.BANSHEE_CLOAK,
        BuffId.GHOST_CLOAK: UpgradeId.GHOST_CLOAK,
        BuffId.HYDRALISK_LUNGE: UpgradeId.HYDRALISK_LUNGE,
        # On the parasited unit, which the game reports as its controller's while the parasite lasts.
        BuffId.INFESTOR_NEURAL_PARASITE: UpgradeId.NEURAL_PARASITE,
        BuffId.MARAUDER_STIMMED: UpgradeId.STIMPACK,
        BuffId.MARINE_STIMMED: UpgradeId.STIMPACK,
        BuffId.ZEALOT_CHARGING: UpgradeId.CHARGE,
    }
)
# Buffs whose wearer shows its owner's opponent has the upgrade the buff's ability needs. The mappings stay separate:
# a dict cannot key on ids of two enums, since a buff id and a unit type id of one number are one key.
_BUFF_EVIDENCE_OF_OPPONENT: Final[Mapping[BuffId, UpgradeId]] = MappingProxyType(
    {
        BuffId.MARAUDER_CONCUSSIVE_SHELLS_SLOW: UpgradeId.CONCUSSIVE_SHELLS,
        BuffId.RAVEN_INTERFERENCE_MATRIX: UpgradeId.INTERFERENCE_MATRIX,
    }
)
_EFFECT_EVIDENCE: Final[Mapping[EffectId, UpgradeId]] = MappingProxyType({EffectId.HIGH_TEMPLAR_STORM: UpgradeId.STORM})
# A gateway becomes a warp gate on its own once Warp Gate is researched, and nothing else makes one.
_UNIT_TYPE_EVIDENCE: Final[Mapping[UnitTypeId, UpgradeId]] = MappingProxyType(
    {UnitTypeId.WARP_GATE: UpgradeId.WARP_GATE}
)
# A unit's health without the one upgrade that raises it, measured in game: the game's tables hold no health.
_HEALTH_EVIDENCE: Final[Mapping[UnitTypeId, tuple[float, UpgradeId]]] = MappingProxyType(
    {UnitTypeId.MARINE: (45.0, UpgradeId.COMBAT_SHIELD)}
)


@final
class UpgradeInference:
    """Upgrade inference: the enemy's upgrades read off what only an upgrade can cause, and added to
    `Enemy.upgrades` with `Enemy.assume_upgrades`, beyond the attack, armor and shield levels NachOS reads there
    itself.

    Build it once the game has started, in the bot's own object an api is given as `extensions`:

    ```python
    from sc2nachos import Api
    from sc2nachos.extensions.upgrade_inference import UpgradeInference


    class Extensions:
        def __init__(self, api: Api) -> None:
            self.upgrade_inference = UpgradeInference(api)


    api = Api(extensions=Extensions)
    ```

    It reads each turn's observation on its `TurnStartEvent`, at `EventPriority.BEFORE`, before any handler of the
    bot's, so every handler sees what it read; not the one the game ended on, which gets no turn:

    - An enemy unit of a type nothing makes without an upgrade, in sight or remembered: a burrowed unit shows Burrow,
      but a lurker, a widow mine or a creep tumor, which burrow without it, and a warp gate shows Warp Gate.
    - On a visible enemy unit, a buff only an upgrade gives: Stimpack, Charge, the hydralisk's lunge, and the banshee's
      and the ghost's cloak; and a marine with more than 45 health shows Combat Shield.
    - On a visible unit of this player's, the slow of Concussive Shells and Interference Matrix.
    - An enemy storm, Psionic Storm.
    - A unit the enemy holds with a neural parasite shows Neural Parasite, and nothing of the player it was taken from.
      One on another side than when first seen shows nothing by its type.
    - With `include_speed_upgrades`, as by default, how fast its units move shows ten speed upgrades: Metabolic Boost,
      Centrifugal Hooks, Glial Reconstitution, Muscular Augments, Anabolic Synthesis, Pneumatized Carapace, Hyperflight
      Rotors, Gravitic Boosters, Gravitic Drive and Flux Vanes. Over windows of `speed_window`, 22 steps by default, on
      one clock, it measures each visible enemy unit of a type one of them speeds up that was in sight throughout, and
      on the side it was first seen on: one that moved at its speed with the upgrade at every observation of a window,
      within `speed_tolerance` of the difference between its speeds without the upgrade and with it, 5% by default,
      shows the upgrade; a Zerg ground unit at its speeds on creep where it stood on creep. None shows one while
      wearing a buff that changes its speed, as Time Warp does, or where the upgrade adds less than 10% to its speed, as
      Anabolic Synthesis does on creep. The rule was checked on games observed at every step, over windows of 22 steps
      within 5%: a bot that observes fewer sees fewer moves in a window, and so tells less well a unit that moved
      steadily from one that stopped and started again.

    Upgrades are never lost, so what it reads stays. `Enemy.forget_upgrades` takes one back until it is shown again. In
    a game with no other player it reads nothing.

    Each upgrade it adds to `Enemy.upgrades` comes as an `EnemyUpgradeAssumedEvent`, an `AnyUpgradeEvent`, which `only`
    selects by the upgrade. It comes at once, as it reads, so before any of the bot's handlers of `TurnStartEvent`.

    Raises `TypeError` unless `speed_window` is a `GameTime`, `ValueError` unless it is longer than 0 and
    `speed_tolerance` lies above 0 and below 0.5, and `NotPlayingError` if built before the game starts. A buff the
    curated ids leave out, on any visible unit, raises `UncuratedIdError` from its handler, as reading `Unit.buffs`
    does.
    """

    __slots__ = ("_api",)

    def __init__(
        self,
        api: Api[object],
        *,
        include_speed_upgrades: bool = True,
        speed_window: GameTime = _DEFAULT_WINDOW,
        speed_tolerance: float = _DEFAULT_TOLERANCE,
    ) -> None:
        """Upgrade inference for the enemy of `api`'s game, from its first turn, reading the speed upgrades too if
        `include_speed_upgrades`, over windows of `speed_window`, within `speed_tolerance`."""
        check_the_speed_arguments(window=speed_window, tolerance=speed_tolerance)
        self._api = api
        try:
            _ = api.enemy
        except NoEnemyError:
            return
        api.on(TurnStartEvent, priority=EventPriority.BEFORE)(self._assume_the_upgrades_shown)
        if include_speed_upgrades:
            SpeedUpgradesFromMovement(api, window=speed_window, tolerance=speed_tolerance)

    def _assume_the_upgrades_shown(self, _: TurnStartEvent) -> None:
        """Assume the enemy has the upgrades the last observation shows, handing out an event for each new one."""
        api = self._api
        assume_and_emit_events(api, self._enemy_upgrades_shown(api.units, api.effects))

    @classmethod
    def _enemy_upgrades_shown(cls, units: Iterable[Unit[Any]], effects: Iterable[Effect]) -> frozenset[UpgradeId]:
        """The enemy's upgrades shown by its units and effects and by the buffs on this player's units, beyond the
        levels its units report."""
        shown: set[UpgradeId] = set()
        for unit in units:
            if unit.alliance is Alliance.ENEMY:
                shown |= cls._owner_upgrades_shown_by_unit(unit)
            elif unit.alliance is Alliance.OWN:
                shown |= cls._opponent_upgrades_shown_by_unit(unit)
        for effect in effects:
            if effect.alliance is Alliance.ENEMY:
                shown |= cls._owner_upgrades_shown_by_effect(effect)
        return frozenset(shown)

    @classmethod
    def _owner_upgrades_shown_by_unit(cls, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades `unit` shows its owner has: by its type, and while visible, by its buffs and its health.

        A unit under a neural parasite shows only that its controller has Neural Parasite: its buffs and type belong to
        the player it was taken from. One on another side than when first seen shows nothing by its type, even out of
        sight.
        """
        shown = cls._upgrades_its_type_needs(unit) if unit.alliance is unit.first_alliance else _NO_UPGRADES
        if unit.visibility is not Visibility.VISIBLE:
            return shown
        buffs = unit.buffs
        if BuffId.INFESTOR_NEURAL_PARASITE in buffs:
            return frozenset({UpgradeId.NEURAL_PARASITE})
        worn = {_BUFF_EVIDENCE_OF_OWNER[buff] for buff in buffs if buff in _BUFF_EVIDENCE_OF_OWNER}
        if (health := _HEALTH_EVIDENCE.get(unit.type_id)) is not None and unit.health_max > health[0]:
            worn.add(health[1])
        return shown | worn if worn else shown

    @classmethod
    def _upgrades_its_type_needs(cls, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades nothing makes a unit of `unit`'s type without: those its type's `tech_requirements` name, as a
        zergling needs Burrow to burrow, and those of `_UNIT_TYPE_EVIDENCE`."""
        needed = unit.type_data.tech_requirements.upgrades
        only = _UNIT_TYPE_EVIDENCE.get(unit.type_id)
        return needed if only is None else needed | {only}

    @classmethod
    def _opponent_upgrades_shown_by_unit(cls, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades the buffs on `unit` show its owner's opponent has. Nothing for a unit not visible."""
        if unit.visibility is not Visibility.VISIBLE:
            return _NO_UPGRADES
        return frozenset(_BUFF_EVIDENCE_OF_OPPONENT[buff] for buff in unit.buffs if buff in _BUFF_EVIDENCE_OF_OPPONENT)

    @classmethod
    def _owner_upgrades_shown_by_effect(cls, effect: Effect) -> frozenset[UpgradeId]:
        """The upgrades `effect` shows its owner has."""
        upgrade = _EFFECT_EVIDENCE.get(effect.id)
        return _NO_UPGRADES if upgrade is None else frozenset({upgrade})
