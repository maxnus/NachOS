"""Where a structure may stand, as this player knows the map in one observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.geometry import Footprint, Grid, Point
from sc2nachos.ids import UnitTypeId
from sc2nachos.state._production import _construction_has_started
from sc2nachos.units import Unit

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.geometry import PointLike
    from sc2nachos.orders import OrderBook
    from sc2nachos.units import OwnUnit, Units

# An add-on stands 2.5 right of its host's center and 0.5 below (#37).
_ADD_ON_OFFSET = (2.5, -0.5)
# What an add-on of any host counts as.
_ADD_ON_ALIASES = frozenset({UnitTypeId.TECH_LAB, UnitTypeId.REACTOR})


def static_placement(placement: Grid[bool], geysers: Iterable[Unit[Any]]) -> Grid[bool]:
    """`placement`, the game's placement grid at the start, with the footprint of each of `geysers` closed."""
    static = placement.copy()
    for geyser in geysers:
        if (footprint := geyser.footprint) is not None:
            footprint.fill(static, at=geyser.position, value=False)
    return _read_only(static)


@final
class PlacementGrids:
    """Where a structure may stand, as this player knows the map in one observation.

    A structure may stand where its footprint, `api.data.units[unit_type].footprint`, fits on `dynamic` and covers no
    tile of `reserved`, and, but for a zerg one, no creep: `footprint.centers_fitting(placement.dynamic &
    ~placement.reserved & ~api.creep)` gives the centers. None of the grids knows of power, of units in the way, or of
    the ground around resources that keeps a townhall off, as `api.expansions` does.
    """

    __slots__ = ("_dynamic", "_game_data", "_orders", "_static", "_units")

    def __init__(self, static: Grid[bool], *, units: Units[Unit[Any]], game_data: GameData, orders: OrderBook) -> None:
        """The grids of the observation that found `units`, from `static`, which `static_placement` built at the
        start, with this player's builds as `orders` holds them."""
        self._static = static
        self._units = units
        self._game_data = game_data
        self._orders = orders
        self._dynamic: Grid[bool] | None = None

    @property
    def static(self) -> Grid[bool]:
        """Where a structure may stand whatever happens in the game: the ground the map lets a structure stand on,
        less its geysers. It reads the ground under rocks, towers, mineral fields and townhalls as it is beneath them;
        cliffs, ramps and the ground beyond the playable area are false."""
        return self._static

    @property
    def dynamic(self) -> Grid[bool]:
        """Where a structure may stand now, as far as this player knows: `static` less the footprint of every
        structure, every player's and the map's, in sight or remembered in the fog, and of every mineral field. A unit
        that moves blocks nothing, nor does a structure in the air."""
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
        for unit in self._units.own:
            for row, target in self._builds_of(unit):
                self._reserve(reserved, row, target, unit)
        return _read_only(reserved)

    def _builds_of(self, unit: OwnUnit[Any]) -> Iterator[tuple[AbilityData, Point | Unit[Any] | None]]:
        """Each build `unit` was ordered whose structure does not stand yet, by its ability's row and its target."""
        orders = [*self._orders.issued_to(unit), *self._orders._not_shown_yet(unit)]
        builds = [(order.ability, order.target) for order in orders]
        if not _construction_has_started(unit):
            builds += [(unit_order.ability, unit_order.target) for unit_order in unit.orders]
        for ability, target in builds:
            row = self._game_data.abilities.get(ability)
            if row is not None and row.needs_placement:
                yield row, target

    def _reserve(
        self, reserved: Grid[bool], row: AbilityData, target: Point | Unit[Any] | None, unit: OwnUnit[Any]
    ) -> None:
        """Mark on `reserved` the ground the build of `row`'s ability, aimed at `target`, takes for `unit`, unless
        what it builds stands there already, as it does the observation it finishes, while its worker may still show
        the build (in game)."""
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
        if site is None or self._stands_at(product, _center_of(footprint, site)):
            return
        footprint.fill(reserved, at=site, value=True)
        if landing is not None:
            landing[0].fill(reserved, at=landing[1], value=True)

    def _stands_at(self, unit_type: UnitTypeId, center: Point) -> bool:
        """Whether one of this player's units of `unit_type` stands centered at `center`."""
        return any(unit.type_id is unit_type and unit.position.distance_to(center) < 0.25 for unit in self._units.own)


def _center_of(footprint: Footprint, near: PointLike) -> Point:
    """Where `footprint` centers when placed at `near`, as the game snaps a structure."""
    lower_left = footprint.lower_left_tile(near)
    return Point((lower_left[0] + footprint.width / 2, lower_left[1] + footprint.height / 2))


def _read_only(grid: Grid[bool]) -> Grid[bool]:
    return Grid(grid.values, origin=grid.origin, outside=grid.outside, readonly=True)
