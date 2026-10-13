"""Where this player's ordered builds will stand."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.geometry import Grid, Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import Footprint
    from sc2nachos.orders import OrderBook
    from sc2nachos.units import OwnUnit, Units

# An add-on stands 2.5 right of its host's center and 0.5 below (#37).
_ADD_ON_OFFSET = (2.5, -0.5)
# What an add-on of any host counts as.
_ADD_ON_ALIASES = frozenset({UnitTypeId.TECH_LAB, UnitTypeId.REACTOR})


@final
class _BuildSites:
    """Where this player's builds will stand that are ordered and not standing yet: each held until its unit can start
    it, issued this turn, or sent and not yet begun. One for a game."""

    __slots__ = ("_builder_types", "_game_data", "_orders")

    def __init__(self, game_data: GameData, orders: OrderBook) -> None:
        """The build sites of a game whose tables are `game_data`, with this player's orders as `orders` holds them."""
        self._game_data = game_data
        self._orders = orders
        builds = [row for row in game_data.abilities.values() if row.needs_placement]
        # The types that perform an ability that places a structure: workers, add-on hosts, queens and the like.
        self._builder_types = frozenset(type_id for row in builds for type_id in row.performers)

    def reserved(self, like: Grid[bool], units: Units[Unit[Any]]) -> Grid[bool]:
        """A read-only grid over the tiles `like` covers, true under each build site, `units` being the units the
        observation found. An add-on reserves the ground beside its host, and a lifted host's landing site too."""
        reserved = Grid.like(like, dtype=bool, outside=False)
        own = units.own
        standing = {_placement_key(unit.type_id, unit.position) for unit in own}
        for unit in own:
            if unit.type_id in self._builder_types:
                for row, target in self._builds_ordered_to(unit):
                    self._reserve(reserved, row, target, unit, standing)
        return reserved.as_readonly()

    def _builds_ordered_to(self, unit: OwnUnit[Any]) -> Iterator[tuple[AbilityData, Point | Unit[Any] | None]]:
        """Each build `unit` was ordered, by its ability's row and its target: held, issued this turn, sent and maybe
        not shown yet, or shown by the unit."""
        ordered = [(order.ability, order.target) for order in self._orders.issued_to(unit)]
        ordered += [(order.ability, order.target) for order in self._orders._not_shown_yet(unit)]
        # Asked of the report first, since reading every unit's orders whole costs more than the rest of the grid.
        reported = (AbilityId.get(order.ability_id) or AbilityId.NULL for order in unit._latest_report.orders)
        if any(self._ability_places_structure(ability) for ability in reported):
            ordered += [(unit_order.ability, unit_order.target) for unit_order in unit.orders]
        for ability, target in ordered:
            if self._ability_places_structure(ability):
                yield self._game_data.abilities[ability], target

    def _ability_places_structure(self, ability: AbilityId) -> bool:
        """Whether the tables know `ability` as one that places a structure."""
        row = self._game_data.abilities.get(ability)
        return row is not None and row.needs_placement

    def _reserve(
        self,
        reserved: Grid[bool],
        row: AbilityData,
        target: Point | Unit[Any] | None,
        unit: OwnUnit[Any],
        standing: Collection[tuple[UnitTypeId, int, int]],
    ) -> None:
        """Mark on `reserved` the ground the build of `row`'s ability, aimed at `target`, takes for `unit`, unless
        what it builds stands there already, `standing` being this player's units by `_placement_key`: begun, or
        finished while its worker still shows the build, as an SCV did the observation its depot finished (in
        game)."""
        units = self._game_data.units
        product = row.products.get(unit.type_id)
        if not isinstance(product, UnitTypeId) or (footprint := units[product].footprint) is None:
            return
        site = target.position if isinstance(target, Unit) else target
        landing: tuple[Footprint, Point] | None = None
        if _ADD_ON_ALIASES & set(units[product].tech_aliases):
            host = units[unit.type_id]
            host_type = host.morphed_from if host.footprint is None and host.morphed_from else unit.type_id
            host_footprint = units[host_type].footprint
            if host_footprint is None:
                return
            host_center = host_footprint.snapped_center(site if site is not None else unit.position)
            if site is not None:
                landing = host_footprint, host_center
            site = Point((host_center.x + _ADD_ON_OFFSET[0], host_center.y + _ADD_ON_OFFSET[1]))
        if site is None or _placement_key(product, footprint.snapped_center(site)) in standing:
            return
        reserved.fill_footprint(footprint, at=site, value=True)
        if landing is not None:
            reserved.fill_footprint(landing[0], at=landing[1], value=True)


def _placement_key(unit_type: UnitTypeId, center: Point) -> tuple[UnitTypeId, int, int]:
    """`unit_type` centered at `center`, in half tiles, where a structure stands snapped: the same for a structure
    standing there and the site its build was ordered at."""
    return unit_type, round(2 * center.x), round(2 * center.y)
