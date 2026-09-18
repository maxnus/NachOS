"""Which unit builds which of this player's structures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from s2clientprotocol import raw_pb2

    from sc2nachos.units._tracker import _UnitsByTag, _UnitTracker
    from sc2nachos.units._unit import Unit

# How far from where a build order was aimed a structure may stand and still be the one it builds. The game snaps a
# structure's center to the grid, at most half a tile along each axis from the point it was ordered at.
_BUILDER_REACH = 1.0


@final
class _Construction:
    """The builder of each of this player's structures being built: a unit in the observation carrying out the order
    that builds it, or the unit that became it, as a drone does."""

    __slots__ = ("_builders", "_builders_now", "_tracker", "_under_construction", "_used_up_builders")

    def __init__(self, tracker: _UnitTracker) -> None:
        self._tracker = tracker
        # Each structure still being built by a unit that became it, as a drone does, and that unit.
        self._builders: dict[Unit[Any], Unit[Any]] = {}
        # The units whose structure finished or died in the last update, which the game has one more to report dead.
        self._used_up_builders: list[Unit[Any]] = []
        # Worked out from the last observation when first asked for: this player's unfinished structures, and each
        # structure being built by a unit in the observation with that unit.
        self._under_construction: list[Unit[Any]] | None = None
        self._builders_now: dict[Unit[Any], Unit[Any]] | None = None

    def structure_built_by(self, unit: Unit[Any]) -> Unit[Any] | None:
        """The unfinished structure of this player's that `unit`'s first order builds, or `None`.

        Tested in game: an SCV's build order is aimed at the structure's center once construction starts, or at the
        structure itself when construction is resumed, and it has no orders once construction is halted.
        """
        orders = unit._latest_data.orders
        if not orders:
            return None
        order = orders[0]
        if (target := self._order_target_position(order)) is None:
            return None
        nearest = _BUILDER_REACH * _BUILDER_REACH
        built = None
        for structure in self._structures_under_construction():
            if not self._order_makes_structure(order, structure):
                continue
            dx, dy = target[0] - structure._position[0], target[1] - structure._position[1]
            if dx * dx + dy * dy <= nearest:
                nearest = dx * dx + dy * dy
                built = structure
        return built

    def builder_of(self, structure: Unit[Any]) -> Unit[Any] | None:
        """The unit building `structure` now: the drone that became it, or a unit in the observation building it."""
        if (drone := self._builders.get(structure)) is not None:
            return drone
        if self._builders_now is None:
            self._builders_now = {}
            for unit in self._tracker.present_units:
                if unit._own and (built := self.structure_built_by(unit)) is not None:
                    self._builders_now[built] = unit
        return self._builders_now.get(structure)

    def link_builder(self, structure: Unit[Any], ordered: list[Unit[Any]]) -> None:
        """Link `structure`, new to this observation, to the unit that became it, if one of `ordered` did, and take
        that one out of `ordered`.

        `ordered` holds this player's units that left this observation carrying out an order. Tested in game: a drone
        that morphs into a structure leaves the observation with no death reported, and the structure appears under a
        new tag.
        """
        builder = None
        nearest = _BUILDER_REACH * _BUILDER_REACH
        for unit in ordered:
            order = unit._latest_data.orders[0]
            if (
                not self._order_makes_structure(order, structure)
                or (target := self._order_target_position(order)) is None
            ):
                continue
            dx, dy = target[0] - structure._position[0], target[1] - structure._position[1]
            if dx * dx + dy * dy <= nearest:
                nearest = dx * dx + dy * dy
                builder = unit
        if builder is not None:
            ordered.remove(builder)
            self._builders[structure] = builder

    def update(self, present: _UnitsByTag) -> None:
        """Mark dead each unit that became a structure which finished or died an update ago, unless the game has
        reported it dead since or it came back, and leave those whose structure finished or died now to the next update.

        Tested in game: a drone whose structure is cancelled comes back under its own tag, with its health, in the
        observation that reports the structure dead. One whose structure finishes the game reports dead itself, in the
        step the structure finishes or the one after, so an observation later at most; this marks only one the game did
        not.
        """
        if not self._builders and not self._used_up_builders:
            return
        for builder in self._used_up_builders:
            if builder._stale and not builder._dead:
                self._tracker._mark_unit_dead(builder, present, reported=False)
        self._used_up_builders = []
        for structure, builder in list(self._builders.items()):
            if structure._dead or structure.is_complete:
                del self._builders[structure]
                self._used_up_builders.append(builder)

    def forget_observation(self) -> None:
        """Forget what was worked out from the last observation, as a new one comes."""
        self._under_construction = None
        self._builders_now = None

    def end(self) -> None:
        """Forget the builders the game has yet to report dead, since the game is over."""
        self._used_up_builders = []
        self.forget_observation()

    def _order_target_position(self, order: raw_pb2.UnitOrder) -> tuple[float, float] | None:
        """Where `order` is aimed: its point, or where the unit it targets stands, such as the geyser a gas building
        goes on or an unfinished structure construction resumes on."""
        if order.HasField("target_world_space_pos"):
            return order.target_world_space_pos.x, order.target_world_space_pos.y
        tracker = self._tracker
        if order.HasField("target_unit_tag") and (unit_id := tracker._ids.get(order.target_unit_tag)) is not None:
            position = tracker._units_by_id[unit_id]._position
            return position[0], position[1]
        return None

    def _order_makes_structure(self, order: raw_pb2.UnitOrder, structure: Unit[Any]) -> bool:
        """Whether `order` is the ability that makes `structure`'s type."""
        row = self._tracker.data.units.get(structure._type_id)
        return row is not None and row.creation_ability == order.ability_id

    def _structures_under_construction(self) -> list[Unit[Any]]:
        """This player's unfinished units in the last observation."""
        if self._under_construction is None:
            self._under_construction = [
                unit for unit in self._tracker.present_units if unit._own and unit._latest_data.build_progress < 1.0
            ]
        return self._under_construction
