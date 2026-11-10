"""What Terran can make, read from the game's data, and Terran's rules on top."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, final

from sc2nachos.gamedata import Tech
from sc2nachos.gametime import GameTime
from sc2nachos.ids import UnitTypeId, UpgradeId
from sc2nachos.match import Race
from sc2nachos.units import UnitType

if TYPE_CHECKING:
    from sc2nachos.gamedata import GameData, UnitTypeData

# The game's supply limit.
SUPPLY_LIMIT: Final = 200
# What the economy needs whatever the reward: workers, supply, expansions, orbital commands and refineries.
ECONOMY_TYPES: Final = (
    UnitTypeId.SCV,
    UnitTypeId.SUPPLY_DEPOT,
    UnitTypeId.COMMAND_CENTER,
    UnitTypeId.ORBITAL_COMMAND,
    UnitTypeId.REFINERY,
)
# By default a unit starts whole for this long for each 100 minerals and vespene it costs, but at least the shortest.
_DEFAULT_WHOLE_SECONDS_A_HUNDRED_RESOURCES: Final = 60.0
_SHORTEST_DEFAULT_WHOLE_SECONDS: Final = 30.0
_TECH_LAB_TYPES: Final = UnitType.AnyTechLab.type_ids
_REACTOR_TYPES: Final = UnitType.AnyReactor.type_ids


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Item:
    """Something the planner can start: a unit, a structure, a townhall's morph, an add-on or an upgrade."""

    unit_type: UnitTypeId | None
    """What it makes, or `None` for an upgrade."""
    upgrade: UpgradeId | None
    """What it researches, or `None` for anything else."""
    minerals: float
    """Its cost in minerals, paid as it starts."""
    vespene: float
    """Its cost in vespene, paid as it starts."""
    build_seconds: float
    """How long it takes, never rounded to the plan's buckets."""
    supply_used: float
    """The supply it takes from its start."""
    supply_provided: float
    """The supply it adds to the cap once done, less what the type it morphs from provided."""
    producers: frozenset[UnitTypeId]
    """The types that make or research it, each as the type it counts as, a barracks rather than a flying one; empty
    for a structure a worker builds."""
    needs_tech_lab: bool
    """Whether its producer must have a tech lab."""
    techs: frozenset[Tech]
    """The techs that must stand before it starts."""
    upgrades_needed: frozenset[UpgradeId]
    """The upgrades that must be done before it starts."""
    morphed_from: UnitTypeId | None
    """The type it is made from and uses up, a command center for an orbital command."""
    is_built_by_worker: bool
    """Whether an SCV builds it, away from its work from the walk there through the build to the walk back."""

    @property
    def default_whole_until(self) -> GameTime:
        """The time from a plan's start until which it starts in whole numbers, unless the bot chooses another: for a
        unit, 60 seconds for each 100 minerals and vespene it costs, but at least 30, so a command center 4:00, a
        barracks or an orbital command 1:30, a supply depot 1:00, and an SCV or a marine 0:30; for an upgrade, over any
        horizon, since each is made once at most."""
        if self.is_upgrade:
            return GameTime.INFINITE
        seconds = _DEFAULT_WHOLE_SECONDS_A_HUNDRED_RESOURCES * (self.minerals + self.vespene) / 100
        return GameTime(seconds=max(_SHORTEST_DEFAULT_WHOLE_SECONDS, seconds))

    @property
    def is_upgrade(self) -> bool:
        """Whether it is an upgrade."""
        return self.upgrade is not None

    @property
    def name(self) -> str:
        """The name of what it makes or researches."""
        made = self.unit_type if self.unit_type is not None else self.upgrade
        return "" if made is None else made.name


@final
class TerranModel:
    """Every item Terran can make, from the game's data: what it costs, how long it takes, the supply it takes and
    gives, what makes it and what it needs first.

    Each Terran unit type counts as one item's, of its `equivalent_types`: the one an order makes rather than one
    morphed from another of them, and of two, the one needing fewer techs. So a flying barracks counts as a barracks, a
    sieged tank as a siege tank, and a hellbat as a hellion, which needs no armory; a bot that wants hellbats values an
    armory itself. A type its order makes as another, a rich refinery, counts as that, a refinery."""

    __slots__ = ("_counted_types", "_game_data", "_items_by_type", "_items_by_upgrade")

    def __init__(self, game_data: GameData) -> None:
        self._game_data = game_data
        self._counted_types = _counted_types_of_terran_types(game_data)
        item_rows = [
            row
            for unit_type, row in game_data.units.items()
            if self._counted_types.get(unit_type) == unit_type and _is_made_by_an_order(row)
        ]
        items_by_type = {row.id: self._unit_item(row) for row in item_rows}
        self._items_by_type: Mapping[UnitTypeId, Item] = MappingProxyType(items_by_type)
        items_by_upgrade: dict[UpgradeId, Item] = {}
        for upgrade, upgrade_row in game_data.upgrades.items():
            researchers = frozenset(self.counted_as(researcher) for researcher in upgrade_row.researchers)
            if researchers and researchers <= items_by_type.keys():
                items_by_upgrade[upgrade] = Item(
                    unit_type=None,
                    upgrade=upgrade,
                    minerals=upgrade_row.cost.minerals,
                    vespene=upgrade_row.cost.vespene,
                    build_seconds=upgrade_row.cost.time.seconds,
                    supply_used=0.0,
                    supply_provided=0.0,
                    producers=researchers,
                    needs_tech_lab=False,
                    techs=upgrade_row.tech_requirements.techs,
                    upgrades_needed=upgrade_row.tech_requirements.upgrades,
                    morphed_from=None,
                    is_built_by_worker=False,
                )
        self._items_by_upgrade: Mapping[UpgradeId, Item] = MappingProxyType(items_by_upgrade)

    @property
    def game_data(self) -> GameData:
        """The game's data the items were read from."""
        return self._game_data

    @property
    def unit_items(self) -> Mapping[UnitTypeId, Item]:
        """The item making each unit type that is one."""
        return self._items_by_type

    @property
    def upgrade_items(self) -> Mapping[UpgradeId, Item]:
        """The item researching each upgrade Terran researches."""
        return self._items_by_upgrade

    @property
    def types_counted_as_items(self) -> frozenset[UnitTypeId]:
        """Every unit type that counts as an item's: the items' types, and the types that count as them."""
        counted_types = self._counted_types.items()
        return frozenset(unit_type for unit_type, counted in counted_types if counted in self._items_by_type)

    def counted_as(self, unit_type: UnitTypeId) -> UnitTypeId:
        """The type a unit of `unit_type` counts as, an item's or one nothing makes: a barracks for a flying one, a
        hellion for a hellbat, a refinery for a rich one; `unit_type` itself for a type of another race."""
        return self._counted_types.get(unit_type, unit_type)

    def types_granting(self, tech: Tech) -> frozenset[UnitTypeId]:
        """The types that grant `tech`, each as the type it counts as."""
        return frozenset(self.counted_as(granting) for granting in self._game_data.types_granting(tech))

    def tech_lab_of(self, host: UnitTypeId) -> UnitTypeId | None:
        """The tech lab type `host` builds, or `None` if it builds none."""
        return _add_on_of(host, _TECH_LAB_TYPES, self._items_by_type)

    def reactor_of(self, host: UnitTypeId) -> UnitTypeId | None:
        """The reactor type `host` builds, or `None` if it builds none."""
        return _add_on_of(host, _REACTOR_TYPES, self._items_by_type)

    def is_add_on(self, unit_type: UnitTypeId) -> bool:
        """Whether `unit_type` is a tech lab or a reactor."""
        return unit_type in _TECH_LAB_TYPES or unit_type in _REACTOR_TYPES

    def items_for(self, *, unit_types: Iterable[UnitTypeId], upgrades: Iterable[UpgradeId]) -> tuple[Item, ...]:
        """The items a plan valuing `unit_types` and `upgrades` needs: those, what makes each, what each needs first, a
        tech lab where one is needed, a reactor for a producer of units that need none, and the economy, workers, supply
        depots, command centers, orbital commands and refineries. In the order the game's data lists the unit types,
        then the upgrades.

        Raises `ValueError` for a unit type or an upgrade Terran does not make.
        """
        wanted_types = {self._item_type_of(unit_type) for unit_type in unit_types}
        wanted_types.update(ECONOMY_TYPES)
        wanted_upgrades: set[UpgradeId] = set()
        for upgrade in upgrades:
            if upgrade not in self._items_by_upgrade:
                raise ValueError(f"the planner cannot research {upgrade.name} for Terran")
            wanted_upgrades.add(upgrade)
        to_visit = [self._items_by_type[unit_type] for unit_type in wanted_types]
        to_visit += [self._items_by_upgrade[upgrade] for upgrade in wanted_upgrades]
        while to_visit:
            for needed in self._needs_of(to_visit.pop(), wanted_types):
                if needed.unit_type is not None and needed.unit_type not in wanted_types:
                    wanted_types.add(needed.unit_type)
                    to_visit.append(needed)
                elif needed.upgrade is not None and needed.upgrade not in wanted_upgrades:
                    wanted_upgrades.add(needed.upgrade)
                    to_visit.append(needed)
        return tuple(item for unit_type, item in self._items_by_type.items() if unit_type in wanted_types) + tuple(
            item for upgrade, item in self._items_by_upgrade.items() if upgrade in wanted_upgrades
        )

    def _item_type_of(self, unit_type: UnitTypeId) -> UnitTypeId:
        """The type `unit_type` counts as, raising `ValueError` unless it is an item's."""
        counted = self.counted_as(unit_type)
        if counted not in self._items_by_type:
            raise ValueError(f"the planner cannot make {unit_type.name} for Terran")
        return counted

    def _needs_of(self, item: Item, wanted_types: set[UnitTypeId]) -> list[Item]:
        """The items `item` cannot be made without, beside `wanted_types`: a producer, the cheapest of its producers if
        none is wanted; what it morphs from; the type each tech it needs is named after; its producers' tech lab if it
        needs one, or their reactor for a unit that needs none; and the upgrades it needs."""
        producers = item.producers & wanted_types or {
            min(item.producers, key=lambda producer: self._game_data.units[producer].total_cost.minerals)
        }
        needed_types = set(producers)
        if item.morphed_from is not None:
            needed_types.add(item.morphed_from)
        needed_types.update(self.counted_as(tech.base_type) for tech in item.techs)
        add_on_of = self.tech_lab_of if item.needs_tech_lab else self.reactor_of
        if item.unit_type is not None and not _is_structure(self._game_data.units[item.unit_type]):
            needed_types.update(add_on for producer in producers if (add_on := add_on_of(producer)) is not None)
        needed = [
            self._items_by_type[needed_type] for needed_type in needed_types if needed_type in self._items_by_type
        ]
        return needed + [self._items_by_upgrade[upgrade] for upgrade in item.upgrades_needed]

    def _unit_item(self, row: UnitTypeData) -> Item:
        """The item making the unit type of `row`."""
        producers = frozenset(self.counted_as(producer) for producer in row.producers)
        morphed_from = None if row.morphed_from is None else self.counted_as(row.morphed_from)
        provided_before = 0.0 if morphed_from is None else self._game_data.units[morphed_from].supply_provided
        return Item(
            unit_type=row.id,
            upgrade=None,
            minerals=row.cost.minerals,
            vespene=row.cost.vespene,
            build_seconds=row.build_time.seconds,
            supply_used=max(row.cost.supply, 0.0),
            supply_provided=row.supply_provided - provided_before,
            producers=producers,
            needs_tech_lab=row.tech_requirements.needs_tech_lab,
            techs=row.tech_requirements.techs,
            upgrades_needed=row.tech_requirements.upgrades,
            morphed_from=morphed_from,
            is_built_by_worker=UnitTypeId.SCV in producers,
        )


def _is_structure(row: UnitTypeData) -> bool:
    """Whether the unit type of `row` is a structure."""
    return row.id in UnitType.Structure.type_ids


def _is_made_by_an_order(row: UnitTypeData) -> bool:
    """Whether an order that costs something makes the unit type of `row`: neither a MULE nor an auto-turret."""
    return bool(row.producers) and not row.cost.is_free


def _counted_types_of_terran_types(game_data: GameData) -> dict[UnitTypeId, UnitTypeId]:
    """The type each Terran unit type counts as: of the `equivalent_types` of what its order makes, the one an order
    makes rather than one morphed from another of them, and of two, the one needing fewer techs."""
    rows = {unit_type: row for unit_type, row in game_data.units.items() if row.race == Race.TERRAN}
    counted_types: dict[UnitTypeId, UnitTypeId] = {}
    for unit_type, row in rows.items():
        made = _made_by_its_order(game_data, row)
        if made not in rows:
            made = unit_type
        equivalents = {equivalent for equivalent in rows[made].equivalent_types.type_ids if equivalent in rows}
        made_first = [equivalent for equivalent in equivalents if rows[equivalent].morphed_from not in equivalents]
        counted_types[unit_type] = min(
            made_first or [made], key=lambda equivalent: (len(rows[equivalent].tech_requirements.techs), equivalent)
        )
    return counted_types


def _made_by_its_order(game_data: GameData, row: UnitTypeData) -> UnitTypeId:
    """The type the order that makes the unit type of `row` makes: a refinery for a rich one, since the geyser
    and not the order makes it rich; the type itself where an order makes it, or nothing does."""
    ability = None if row.creation_ability is None else game_data.abilities.get(row.creation_ability)
    if ability is not None:
        for producer in sorted(row.producers):
            made = ability.products.get(producer)
            if isinstance(made, UnitTypeId) and made != row.id and made in game_data.units:
                return made
    return row.id


def _add_on_of(
    host: UnitTypeId, add_on_types: frozenset[UnitTypeId], items: Mapping[UnitTypeId, Item]
) -> UnitTypeId | None:
    """The add-on of `add_on_types` that `host` builds, or `None`."""
    for add_on_type in sorted(add_on_types):
        item = items.get(add_on_type)
        if item is not None and host in item.producers:
            return add_on_type
    return None
