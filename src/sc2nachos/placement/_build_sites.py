"""Where this player's ordered builds will stand."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

from sc2nachos.geometry import Grid, Point
from sc2nachos.ids import AbilityId, UnitTypeId
from sc2nachos.units import NotReportedError, Unit, UnitType

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator

    from sc2nachos.gamedata import GameData
    from sc2nachos.geometry import Footprint
    from sc2nachos.orders import Order, OrderBook
    from sc2nachos.placement._placement_reservation import PlacementReservation
    from sc2nachos.units import OwnUnit, UnitOrder, Units

# An add-on stands 2.5 right of its host's center and 0.5 below (#37).
_ADD_ON_OFFSET = (2.5, -0.5)


@final
class _BuildSites:
    """Where this player's builds will stand that are ordered and not standing yet: each issued this turn, or sent and
    not yet begun. One for a game."""

    __slots__ = ("_builder_types", "_game_data", "_order_book")

    def __init__(self, game_data: GameData, order_book: OrderBook) -> None:
        """The build sites of a game whose tables are `game_data`, with this player's orders as `order_book` holds
        them."""
        self._game_data = game_data
        self._order_book = order_book
        builds = [row for row in game_data.abilities.values() if row.needs_placement]
        # The types that perform an ability that places a structure: workers, add-on hosts, queens and the like.
        self._builder_types = frozenset(type_id for row in builds for type_id in row.performers)

    def reserved_grid(
        self, like: Grid[bool], own_units: Units[OwnUnit[Any]], reservations: Iterable[PlacementReservation]
    ) -> Grid[bool]:
        """A read-only grid over the tiles `like` covers, true under each build site of `own_units`, this player's
        units, and under each of `reservations`. An add-on reserves the ground beside its host, and a lifted host's
        landing site too."""
        reserved = Grid.like(like, dtype=bool, outside=False)
        for reservation in reservations:
            reserved.fill_footprint(reservation._footprint, at=reservation.at, value=True)
        standing: set[tuple[int, int]] | None = None
        for unit in own_units:
            if unit.type_id in self._builder_types:
                for build in self._builds_ordered_to(unit):
                    if standing is None:
                        standing = {_placement_key(own.position) for own in own_units if own.footprint is not None}
                    self._reserve_site(reserved, build, unit, standing)
        return reserved.as_readonly()

    def _builds_ordered_to(self, unit: OwnUnit[Any]) -> Iterator[Order[Any] | UnitOrder]:
        """Each build `unit` was ordered: issued this turn, sent and maybe not shown yet, or shown by the unit."""
        ordered: list[Order[Any] | UnitOrder] = [*self._order_book.issued_to(unit, include_sent=True)]
        # Asked of the report first, since reading every unit's orders whole costs more than the rest of the grid.
        reported = (AbilityId.get(order.ability_id) or AbilityId.NULL for order in unit._latest_report.orders)
        if any(self._ability_places_structure(ability) for ability in reported):
            ordered += unit.orders
        yield from (order for order in ordered if self._ability_places_structure(order.ability))

    def _ability_places_structure(self, ability: AbilityId) -> bool:
        """Whether the tables know `ability` as one that places a structure."""
        row = self._game_data.abilities.get(ability)
        return row is not None and row.needs_placement

    def _reserve_site(
        self,
        reserved: Grid[bool],
        build: Order[Any] | UnitOrder,
        builder: OwnUnit[Any],
        standing: Collection[tuple[int, int]],
    ) -> None:
        """Mark on `reserved` the ground that `build`, ordered to `builder`, takes, unless one of this player's
        structures stands centered on its site already, `standing` being their centers by `_placement_key`: the build
        begun, whatever type it stands as, a rich geyser's refinery among them, or finished while its worker still
        shows the build, as an SCV did the observation its depot finished (in game)."""
        product = self._game_data.abilities[build.ability].products.get(builder.type_id)
        if not isinstance(product, UnitTypeId) or (footprint := self._game_data.units[product].footprint) is None:
            return
        try:
            target = build.target
        except NotReportedError:
            # Shown aimed at a unit the game names by no tag it reports: no site to reserve.
            return
        site = target.position if isinstance(target, Unit) else target
        if product in UnitType.AnyAddOn._type_ids:
            self._reserve_add_on(reserved, footprint, site, builder, standing)
        elif site is not None and _placement_key(footprint.snapped_center(at=site)) not in standing:
            reserved.fill_footprint(footprint, at=site, value=True)

    def _reserve_add_on(
        self,
        reserved: Grid[bool],
        footprint: Footprint,
        landing_site: Point | None,
        host: OwnUnit[Any],
        standing: Collection[tuple[int, int]],
    ) -> None:
        """Mark on `reserved` the ground an add-on covering `footprint` takes beside `host`: beside where the host
        stands, or, for a lifted host, beside `landing_site`, which it reserves too. Nothing, if a structure stands
        there already, `standing` being as `_reserve_site` says."""
        host_footprint = self._landed_footprint(host.type_id)
        if host_footprint is None:
            return
        host_center = host_footprint.snapped_center(at=landing_site if landing_site is not None else host.position)
        add_on_center = Point((host_center.x + _ADD_ON_OFFSET[0], host_center.y + _ADD_ON_OFFSET[1]))
        if _placement_key(add_on_center) in standing:
            return
        reserved.fill_footprint(footprint, at=add_on_center, value=True)
        if landing_site is not None:
            reserved.fill_footprint(host_footprint, at=host_center, value=True)

    def _landed_footprint(self, host_type: UnitTypeId) -> Footprint | None:
        """The footprint of a host of `host_type` once landed: its own, or, lifted, that of the type it lifted from."""
        row = self._game_data.units[host_type]
        if row.footprint is None and row.morphed_from is not None:
            return self._game_data.units[row.morphed_from].footprint
        return row.footprint


def _placement_key(center: Point) -> tuple[int, int]:
    """`center` in half tiles, where a structure stands snapped: the same for a structure standing there and the site
    its build was ordered at."""
    return round(2 * center.x), round(2 * center.y)
