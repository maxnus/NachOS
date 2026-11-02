"""Upgrade inference: the enemy's upgrades read off what only an upgrade can cause."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, final

from sc2nachos import NoEnemyError
from sc2nachos.events import EventPriority, TurnStartEvent
from sc2nachos.ids import BuffId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.units import Alliance, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sc2nachos import Api
    from sc2nachos.gamedata import GameData
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

    It reads the observation the game started on as it is built, and each later one on its `TurnStartEvent`, at
    `EventPriority.HIGHEST`, before any handler of the bot's, so every handler sees what it read that turn:

    - An enemy unit of a type nothing makes without an upgrade, in sight or remembered: a burrowed unit shows Burrow,
      but a lurker, a widow mine or a creep tumor, which burrow without it, and a warp gate shows Warp Gate.
    - On a visible enemy unit, a buff only an upgrade gives: Stimpack, Charge, the hydralisk's lunge, and the banshee's
      and the ghost's cloak; and a marine with more than 45 health shows Combat Shield.
    - On a visible unit of this player's, the slow of Concussive Shells and Interference Matrix.
    - An enemy storm, Psionic Storm.
    - A unit the enemy holds with a neural parasite shows Neural Parasite, and nothing of the player it was taken from.
      One on another side than when first seen shows nothing by its type.

    Upgrades are never lost, so what it reads stays; `Enemy.forget_upgrades` takes one back until it is shown again.
    In a game with no other player it reads nothing.

    Raises `NotPlayingError` if built before the game starts. A buff the curated ids leave out, on any visible unit,
    raises `UncuratedIdError` from its handler, as reading `Unit.buffs` does.
    """

    __slots__ = ("_api", "_enemy", "_upgrades_needed_by_type")

    def __init__(self, api: Api[object]) -> None:
        """Upgrade inference for the enemy of `api`'s game, from the observation the game started on."""
        self._api = api
        self._upgrades_needed_by_type = _upgrades_needed_by_each_unit_type(api.data)
        try:
            self._enemy = api.enemy
        except NoEnemyError:
            return
        self._assume_the_upgrades_shown()
        api.on(TurnStartEvent, priority=EventPriority.HIGHEST)(self._infer_as_the_turn_starts)

    def _infer_as_the_turn_starts(self, event: TurnStartEvent) -> None:
        self._assume_the_upgrades_shown()

    def _assume_the_upgrades_shown(self) -> None:
        """Assume the enemy has the upgrades the last observation shows."""
        api = self._api
        self._enemy.assume_upgrades(*_enemy_upgrades_shown(api.units, api.effects, self._upgrades_needed_by_type))


def _enemy_upgrades_shown(
    units: Iterable[Unit[Any]],
    effects: Iterable[Effect],
    upgrades_needed_by_type: Mapping[UnitTypeId, frozenset[UpgradeId]],
) -> frozenset[UpgradeId]:
    """The enemy's upgrades shown by its units and effects and by the buffs on this player's units, beyond the levels
    its units report, with `upgrades_needed_by_type` the upgrades each unit type cannot exist without."""
    shown: set[UpgradeId] = set()
    for unit in units:
        if unit.alliance is Alliance.ENEMY:
            shown |= _owner_upgrades_shown_by_unit(unit, upgrades_needed_by_type)
        elif unit.alliance is Alliance.OWN:
            shown |= _opponent_upgrades_shown_by_unit(unit)
    for effect in effects:
        if effect.alliance is Alliance.ENEMY:
            shown |= _owner_upgrades_shown_by_effect(effect)
    return frozenset(shown)


def _owner_upgrades_shown_by_unit(
    unit: Unit[Any], upgrades_needed_by_type: Mapping[UnitTypeId, frozenset[UpgradeId]]
) -> frozenset[UpgradeId]:
    """The upgrades `unit` shows its owner has: by its type, from `upgrades_needed_by_type`, and while visible, by its
    buffs and its health.

    A unit under a neural parasite shows only that its controller has Neural Parasite: its buffs and type belong to the
    player it was taken from. One on another side than when first seen shows nothing by its type, even out of sight.
    """
    shown = (
        upgrades_needed_by_type.get(unit.type_id, _NO_UPGRADES)
        if unit.alliance is unit.first_alliance
        else _NO_UPGRADES
    )
    if unit.visibility is not Visibility.VISIBLE:
        return shown
    buffs = unit.buffs
    if BuffId.INFESTOR_NEURAL_PARASITE in buffs:
        return frozenset({UpgradeId.NEURAL_PARASITE})
    worn = {_BUFF_EVIDENCE_OF_OWNER[buff] for buff in buffs if buff in _BUFF_EVIDENCE_OF_OWNER}
    if (health := _HEALTH_EVIDENCE.get(unit.type_id)) is not None and unit.health_max > health[0]:
        worn.add(health[1])
    return shown | worn if worn else shown


def _opponent_upgrades_shown_by_unit(unit: Unit[Any]) -> frozenset[UpgradeId]:
    """The upgrades the buffs on `unit` show its owner's opponent has. Nothing for a unit not visible."""
    if unit.visibility is not Visibility.VISIBLE:
        return _NO_UPGRADES
    return frozenset(_BUFF_EVIDENCE_OF_OPPONENT[buff] for buff in unit.buffs if buff in _BUFF_EVIDENCE_OF_OPPONENT)


def _owner_upgrades_shown_by_effect(effect: Effect) -> frozenset[UpgradeId]:
    """The upgrades `effect` shows its owner has."""
    upgrade = _EFFECT_EVIDENCE.get(effect.id)
    return _NO_UPGRADES if upgrade is None else frozenset({upgrade})


def _upgrades_needed_by_each_unit_type(game_data: GameData) -> Mapping[UnitTypeId, frozenset[UpgradeId]]:
    """The upgrades a unit of each type of `game_data` cannot exist without, for each type that needs any: those every
    type that makes it by its creation ability needs for that, as a zergling needs Burrow to burrow, and those of
    `_UNIT_TYPE_EVIDENCE`."""
    needed_by_type: dict[UnitTypeId, frozenset[UpgradeId]] = {}
    for unit_type in game_data.units:
        needed = _upgrades_needed_to_make(unit_type, game_data)
        if (only := _UNIT_TYPE_EVIDENCE.get(unit_type)) is not None:
            needed |= {only}
        if needed:
            needed_by_type[unit_type] = needed
    return MappingProxyType(needed_by_type)


def _upgrades_needed_to_make(unit_type: UnitTypeId, game_data: GameData) -> frozenset[UpgradeId]:
    """The upgrades every type that makes `unit_type` by its creation ability needs for that, in `game_data`."""
    if (ability := game_data.units[unit_type].creation_ability) is None:
        return _NO_UPGRADES
    needs = [
        requirements.upgrades
        for maker, product in game_data.abilities[ability].products.items()
        if product is unit_type
        and (requirements := game_data.units[maker].ability_requirements.get(ability)) is not None
    ]
    return frozenset.intersection(*needs) if needs else _NO_UPGRADES
