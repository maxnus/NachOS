"""Where a structure may stand, as this player knows the map in one observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.geometry import Footprint, Grid, Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import Unit, UnitType

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import PointLike
    from sc2nachos.orders import OrderBook
    from sc2nachos.units import OwnUnit, Units

# An add-on stands 2.5 right of its host's center and 0.5 below (#37).
_ADD_ON_OFFSET = (2.5, -0.5)
# What an add-on of any host counts as.
_ADD_ON_ALIASES = frozenset({UnitTypeId.TECH_LAB, UnitTypeId.REACTOR})


def static_placement(placement: Grid[bool], neutral_units: Units[Unit[Any]]) -> Grid[bool]:
    """`placement`, the game's placement grid at the start, with the footprint of each geyser and each collapsible tower
    of `neutral_units` closed."""
    static = placement.copy()
    for unit in neutral_units.of_type(UnitType.AnyVespeneGeyser, UnitType.CollapsibleTower):
        if (footprint := unit.footprint) is not None:
            footprint.fill(static, at=unit.position, value=False)
    return _read_only(static)


@final
class PlacementGrids:
    """Where a structure may stand, as this player knows the map in one observation.

    A structure may stand where its footprint, `api.data.units[unit_type].footprint`, fits on `dynamic`, covers no tile
    of `reserved`, and is off creep, or for a zerg structure but a hatchery, an extractor or a nydus worm, on it:
    `footprint.centers_fitting(placement.dynamic & ~placement.reserved & ~api.creep)` gives the centers for a terran or
    protoss one, and `& api.creep` for a zerg one. None of the grids knows of power, of units in the way, or of the
    ground around resources that keeps a townhall off, as `api.expansions` does.
    """

    __slots__ = ("_builders", "_dynamic", "_game_data", "_orders", "_static", "_units")

    def __init__(self, static: Grid[bool], *, units: Units[Unit[Any]], game_data: GameData, orders: OrderBook) -> None:
        """The grids of the observation that found `units`, from `static`, which `static_placement` built at the
        start, with this player's builds as `orders` holds them."""
        self._static = static
        self._units = units
        self._game_data = game_data
        self._orders = orders
        self._dynamic: Grid[bool] | None = None
        self._builders: frozenset[UnitTypeId] | None = None

    @property
    def static(self) -> Grid[bool]:
        """Where a structure may stand whatever happens in the game: the ground the map lets a structure stand on,
        less its geysers and its collapsible towers, which stay where they fall. It reads the ground under rocks,
        Xel'Naga towers, mineral fields and townhalls as it is beneath them; cliffs, ramps and the ground beyond the
        playable area are false."""
        return self._static

    @property
    def dynamic(self) -> Grid[bool]:
        """Where a structure may stand now, as far as this player knows: `static` less the footprint of every
        structure, every player's and the map's, in sight or remembered in the fog, and of every mineral field. A unit
        that moves blocks nothing, nor does a structure in the air or a force field."""
        if self._dynamic is None:
            dynamic = self._static.copy()
            for unit in self._units:
                if (footprint := unit.footprint) is not None:
                    footprint.fill(dynamic, at=unit.position, value=False)
            self._dynamic = _read_only(dynamic)
        return self._dynamic

    @property
    def reserved(self) -> Grid[bool]:
        """The tiles under this player's builds that are ordered and not standing yet: held until the unit can start
        one, issued this turn, or sent and not yet begun. A build issued or withdrawn earlier in the turn shows. An
        add-on reserves the ground beside its host, and a host's landing site too."""
        reserved = Grid.like(self._static, dtype=bool, outside=False)
        own = self._units.own
        standing = {_placement_key(unit.type_id, unit.position) for unit in own}
        builders = self._builder_types()
        for unit in own:
            if unit.type_id in builders:
                for row, target in self._builds_of(unit):
                    self._reserve(reserved, row, target, unit, standing)
        return _read_only(reserved)

    def _builder_types(self) -> frozenset[UnitTypeId]:
        """The types that perform an ability that places a structure: workers, add-on hosts, queens and the like."""
        if self._builders is None:
            builds = [row for row in self._game_data.abilities.values() if row.needs_placement]
            self._builders = frozenset(type_id for row in builds for type_id in row.performers)
        return self._builders

    def _builds_of(self, unit: OwnUnit[Any]) -> Iterator[tuple[AbilityData, Point | Unit[Any] | None]]:
        """Each build `unit` was ordered, by its ability's row and its target: held, issued this turn, sent and maybe
        not shown yet, or shown by the unit."""
        ordered = [(order.ability, order.target) for order in self._orders.issued_to(unit)]
        ordered += [(order.ability, order.target) for order in self._orders._not_shown_yet(unit)]
        # Asked of the report first, since reading every unit's orders whole costs more than the rest of the grid.
        reported = (AbilityId.get(order.ability_id) or AbilityId.NULL for order in unit._latest_report.orders)
        if any(self._places_a_structure(ability) for ability in reported):
            ordered += [(unit_order.ability, unit_order.target) for unit_order in unit.orders]
        for ability, target in ordered:
            if self._places_a_structure(ability):
                yield self._game_data.abilities[ability], target

    def _places_a_structure(self, ability: AbilityId) -> bool:
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
            host_center = _center_of(host_footprint, site if site is not None else unit.position)
            if site is not None:
                landing = host_footprint, host_center
            site = Point((host_center.x + _ADD_ON_OFFSET[0], host_center.y + _ADD_ON_OFFSET[1]))
        if site is None or _placement_key(product, _center_of(footprint, site)) in standing:
            return
        footprint.fill(reserved, at=site, value=True)
        if landing is not None:
            landing[0].fill(reserved, at=landing[1], value=True)


def _placement_key(unit_type: UnitTypeId, center: Point) -> tuple[UnitTypeId, int, int]:
    """`unit_type` centered at `center`, in half tiles, where a structure stands snapped: the same for a structure
    standing there and the site its build was ordered at."""
    return unit_type, round(2 * center.x), round(2 * center.y)


def _center_of(footprint: Footprint, near: PointLike) -> Point:
    """Where `footprint` centers when placed at `near`, as the game snaps a structure."""
    lower_left = footprint.lower_left_tile(near)
    return Point((lower_left[0] + footprint.width / 2, lower_left[1] + footprint.height / 2))


def _read_only(grid: Grid[bool]) -> Grid[bool]:
    return Grid(grid.values, origin=grid.origin, outside=grid.outside, readonly=True)
