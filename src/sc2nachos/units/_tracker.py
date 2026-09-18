"""Which unit each tag in a game's observations is."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from s2clientprotocol import raw_pb2

from sc2nachos._errors import NachOSError
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.units._changes import _Changes
from sc2nachos.units._errors import UnknownTagError
from sc2nachos.units._own_unit import OwnUnit
from sc2nachos.units._unit import Unit
from sc2nachos.units._units import Units
from sc2nachos.upgrade_reader import UpgradeReader

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sc2nachos.enemy import Enemy
    from sc2nachos.gamedata import GameData, UnitTypeData

_IN_VISION = raw_pb2.DisplayType.Visible
_IN_FOG = raw_pb2.DisplayType.Snapshot
_OWN = raw_pb2.Alliance.Self
_ENEMY = raw_pb2.Alliance.Enemy
# An id is its alliance's digit followed by this many digits counting that alliance's units.
_IDS_PER_ALLIANCE = 100_000
# The types that can leave the spot they are remembered at, by lifting off or uprooting: the performers the curated
# `*_LIFT` and `*_UPROOT` abilities name, which every other ability name puts first too.
_MOVABLE_UNIT_TYPE_IDS = frozenset(
    UnitTypeId[performer]
    for ability in AbilityId
    if ability.name.endswith(("_LIFT", "_UPROOT"))
    and (performer := ability.name.rsplit("_", 1)[0]) in UnitTypeId.__members__
)

# How far from where a build order was aimed a structure may stand and still be the one it builds. The game snaps a
# structure's center to the grid, at most half a tile along each axis from the point it was ordered at.
_BUILDER_REACH = 1.0

type _UnitsByTag = dict[int, Unit[Any]]


class _UpgradedUnitTypes:
    """The cache behind `Unit.weapons`, `speed` and `armor`: one upgraded row per unit type and set of upgrades held.

    Without it every read would build a type's weapons again, and a bot reads them for every unit every step, where
    the sets of upgrades a game ever holds are few.
    """

    __slots__ = ("_data", "_rows")

    def __init__(self, data: GameData) -> None:
        self._data = data
        self._rows: dict[tuple[UnitTypeId, frozenset[UpgradeId]], UnitTypeData] = {}

    def upgraded_row(self, unit_type: UnitTypeId, upgrades: frozenset[UpgradeId]) -> UnitTypeData:
        """The row of `unit_type` as `upgrades` leave it."""
        row = self._data.units[unit_type]
        if not row.upgrades or not upgrades:
            return row
        key = (unit_type, upgrades)
        if (upgraded := self._rows.get(key)) is None:
            upgraded = self._rows[key] = row.with_upgrades(upgrades)
        return upgraded


class _UnitTracker:
    """The units of one game: one object under one id, whatever tags the game reports it under."""

    __slots__ = (
        "_builders",
        "_builders_now",
        "_by_tag",
        "_changes",
        "_data",
        "_enemy",
        "_ignored_tags",
        "_ids",
        "_in_fog_tags",
        "_known_units",
        "_present_units",
        "_present_units_by_tag",
        "_stale_units",
        "_under_construction",
        "_unfinished",
        "_units_by_id",
        "_units_seen_per_alliance",
        "_updates",
        "_upgrade_reader",
        "_upgraded_unit_types",
        "_upgrades",
    )

    def __init__(self, data: GameData, enemy: Enemy) -> None:
        self._data = data
        self._enemy = enemy
        # Every tag of a unit not dead: a tag in vision for good, a tag in the fog until it leaves.
        self._by_tag: _UnitsByTag = {}
        # The tags among them of structures in the fog, which never come back once they leave.
        self._in_fog_tags: set[int] = set()
        # Tags in the fog of structures seen since elsewhere, left out for as long as the game goes on listing them.
        self._ignored_tags: set[int] = set()
        # Every tag ever linked, dead units' included, to the id it names, and every unit ever made by its id.
        self._ids: dict[int, int] = {}
        self._units_by_id: dict[int, Unit[Any]] = {}
        # The last observation's units, by the tag it reported each under, and as a collection.
        self._present_units_by_tag: _UnitsByTag = {}
        self._present_units: Units[Unit[Any]] = Units()
        # The units out of the observation and not dead, by id.
        self._stale_units: dict[int, Unit[Any]] = {}
        self._known_units: Units[Unit[Any]] | None = None
        # What the last update found changed, and how many updates there have been.
        self._changes = _Changes()
        self._updates = 0
        # This player's units first seen unfinished and not finished since, by id.
        self._unfinished: dict[int, OwnUnit[Any]] = {}
        # How many units of each alliance have been seen, indexed by the alliance's value, from which ids are made.
        self._units_seen_per_alliance = [0] * 5
        # Each structure still being built by a unit that became it, as a drone does, and that unit.
        self._builders: dict[Unit[Any], Unit[Any]] = {}
        # Worked out from the last observation when first asked for: this player's unfinished structures, and each
        # structure being built by a unit in the observation with that unit.
        self._under_construction: list[Unit[Any]] | None = None
        self._builders_now: dict[Unit[Any], Unit[Any]] | None = None
        # This player's finished upgrades, as the last observation listed them.
        self._upgrades: frozenset[UpgradeId] = frozenset()
        self._upgraded_unit_types = _UpgradedUnitTypes(data)
        self._upgrade_reader = UpgradeReader(data)

    @property
    def data(self) -> GameData:
        """The tables the game is played by."""
        return self._data

    @property
    def upgrades(self) -> frozenset[UpgradeId]:
        """Every upgrade this player has finished researching, as of the last observation."""
        return self._upgrades

    @property
    def enemy(self) -> Enemy:
        """The other player of the game, and what is known of it."""
        return self._enemy

    @property
    def upgrade_reader(self) -> UpgradeReader:
        """Which upgrades each unit type's reported levels stand for, for whoever reads the units' reports."""
        return self._upgrade_reader

    def upgraded_type(self, unit: Unit[Any]) -> UnitTypeData:
        """The type of `unit` as the upgrades its owner has leave it.

        Those are this player's upgrades for its own units, what `Enemy.upgrades` holds for the enemy's, and none for
        anyone else's.
        """
        return self._upgraded_unit_types.upgraded_row(unit.type_id, self._upgrades_of(unit))

    def armor_of(self, unit: Unit[Any]) -> float:
        """The armor of `unit`: its base armor with the armor its upgrades add.

        A unit in sight reports that armor itself, which is exact, where what its owner is known to have is a floor.
        """
        upgraded = self.upgraded_type(unit)
        if (seen := unit._latest_data_in_vision) is None:
            return upgraded.armor
        return max(upgraded.armor, self._data.units[unit.type_id].armor + seen.armor_upgrade_level)

    def shield_armor_of(self, unit: Unit[Any]) -> float:
        """The armor the shields of `unit` have, which is the shields levels its owner has, and 0 without shields."""
        levels = sum(
            1 for upgrade in self._upgrade_reader.shields_of(unit.type_id) if upgrade in self._upgrades_of(unit)
        )
        if (seen := unit._latest_data_in_vision) is None:
            return levels
        return max(levels, seen.shield_upgrade_level)

    def _upgrades_of(self, unit: Unit[Any]) -> frozenset[UpgradeId]:
        """The upgrades the owner of `unit` has: this player's own, the enemy's known ones, and nothing else."""
        alliance = unit._latest_data.alliance
        if alliance == _OWN:
            return self._upgrades
        return self._enemy.upgrades if alliance == _ENEMY else frozenset()

    @property
    def present_units(self) -> Units[Unit[Any]]:
        """Every unit in the last observation, in the order the game listed them."""
        return self._present_units

    @property
    def known_units(self) -> Units[Unit[Any]]:
        """Every unit not known to be dead: those in the last observation, then the stale ones."""
        if self._known_units is None:
            self._known_units = Units((*self._present_units, *self._stale_units.values()))
        return self._known_units

    @property
    def changes(self) -> _Changes:
        """What the last update found changed.

        The game also reports deaths under tags it never reported a unit under (corpus), which name no unit.
        """
        return self._changes

    @property
    def updates(self) -> int:
        """How many observations have been taken in."""
        return self._updates

    def unit_by_tag(self, tag: int) -> Unit[Any]:
        """The unit the game reported under `tag`, dead or alive."""
        try:
            return self._units_by_id[self._ids[tag]]
        except KeyError:
            raise UnknownTagError(f"the game never reported a unit under tag {tag}") from None

    def construction_of(self, unit: Unit[Any]) -> Unit[Any] | None:
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
            for unit in self._present_units_by_tag.values():
                if unit._own and (built := self.construction_of(unit)) is not None:
                    self._builders_now[built] = unit
        return self._builders_now.get(structure)

    def _order_target_position(self, order: raw_pb2.UnitOrder) -> tuple[float, float] | None:
        """Where `order` is aimed: its point, or where the unit it targets stands, such as the geyser a gas building
        goes on or an unfinished structure construction resumes on."""
        if order.HasField("target_world_space_pos"):
            return order.target_world_space_pos.x, order.target_world_space_pos.y
        if order.HasField("target_unit_tag") and (unit_id := self._ids.get(order.target_unit_tag)) is not None:
            position = self._units_by_id[unit_id]._position
            return position[0], position[1]
        return None

    def _order_makes_structure(self, order: raw_pb2.UnitOrder, structure: Unit[Any]) -> bool:
        """Whether `order` is the ability that makes `structure`'s type."""
        row = self._data.units.get(structure._type_id)
        return row is not None and row.creation_ability == order.ability_id

    def _structures_under_construction(self) -> list[Unit[Any]]:
        """This player's unfinished units in the last observation."""
        if self._under_construction is None:
            self._under_construction = [
                unit
                for unit in self._present_units_by_tag.values()
                if unit._own and unit._latest_data.build_progress < 1.0
            ]
        return self._under_construction

    def update(self, observation: raw_pb2.ObservationRaw, step: int) -> None:
        """Take in the units the observation at `step` reports, what it says died, and this player's upgrades.

        Raises `UncuratedIdError` where this player holds an upgrade the curated ids leave out, which belongs in them.
        """
        changes = self._changes = _Changes()
        self._updates += 1
        upgrades = frozenset(UpgradeId.read(upgrade) for upgrade in observation.player.upgrade_ids)
        # Upgrades are never lost, so a count unchanged is a set unchanged.
        if len(upgrades) != len(self._upgrades):
            changes.upgrades = sorted(upgrades - self._upgrades)
        self._upgrades = upgrades
        previous = self._present_units_by_tag
        dead = observation.event.dead_units
        present, new = self._update_known_tags(observation.units, previous, step)
        if new:
            self._identify_new_tags(new, previous, present, step, set(dead))
            # Placed after the rest, so put back in the order the game listed them.
            present = {proto.tag: present[proto.tag] for proto in observation.units if proto.tag in present}
        self._handle_departures(previous, present, step)
        for tag in dead:
            if (unit := self._by_tag.get(tag)) is not None:
                self._mark_dead(unit, present, reported=True)
        if self._builders:
            self._update_builders(present)
        if self._unfinished:
            self._update_unfinished()
        if changes.left_sight:
            # A unit that died went out of the observation first.
            changes.left_sight = [unit for unit in changes.left_sight if not unit._dead]
        self._present_units_by_tag = present
        self._present_units = Units(present.values())
        self._known_units = None
        self._under_construction = None
        self._builders_now = None

    def _update_known_tags(
        self, protos: Iterable[raw_pb2.Unit], previous: _UnitsByTag, step: int
    ) -> tuple[_UnitsByTag, list[raw_pb2.Unit]]:
        """Update the unit behind every tag already linked, and answer those units by tag and the protos left over.

        Takes each unit it updates out of `previous`, which is left holding the tags that did not come back.
        """
        present: _UnitsByTag = {}
        new: list[raw_pb2.Unit] = []
        by_tag = self._by_tag
        ignored = self._ignored_tags
        pop_previous = previous.pop
        # Runs for every unit in every observation, so the common case, a tag in the last observation too, comes first.
        for proto in protos:
            tag = proto.tag
            if not tag:
                # A radar blip or a structure placed but not yet started, neither of which is a unit yet.
                continue
            unit = pop_previous(tag, None)
            if unit is None:
                if tag in ignored:
                    continue
                unit = by_tag.get(tag)
                if unit is None:
                    new.append(proto)
                    continue
                if unit._stale:
                    del self._stale_units[unit._id]
                    self._note_back_in_sight(unit, proto)
                elif unit._tag in self._in_fog_tags:
                    self._ignore_fog_copy(unit._tag, previous, present)
                    self._note_back_in_sight(unit, proto)
                unit._tag = tag
            unit._update(proto, step)
            present[tag] = unit
        return present, new

    def _note_back_in_sight(self, unit: Unit[Any], proto: raw_pb2.Unit) -> None:
        """Note an enemy unit reported in sight again, having been out of the observation or in the fog."""
        if proto.alliance == _ENEMY and proto.display_type != _IN_FOG:
            self._changes.entered_sight.append(unit)

    def _ignore_fog_copy(self, tag: int, previous: _UnitsByTag, present: _UnitsByTag) -> None:
        """Leave out, for good, the copy in the fog under `tag` of a structure that is back in vision.

        Tested in game: a structure that lifts off or uproots out of vision and is then seen elsewhere is reported in
        vision there while its copy in the fog stays listed where it was, until that spot is in vision too.
        """
        self._in_fog_tags.discard(tag)
        del self._by_tag[tag]
        self._ignored_tags.add(tag)
        previous.pop(tag, None)
        present.pop(tag, None)

    def _identify_new_tags(
        self, protos: list[raw_pb2.Unit], previous: _UnitsByTag, present: _UnitsByTag, step: int, dead: set[int]
    ) -> None:
        """Find the unit each proto under a new tag is: a structure passing between vision and fog, or a new unit.

        A structure going out of vision is replaced, in the same observation, by a copy in the fog at the same
        position under a new tag, and coming back into vision swaps the other way. The type is not compared, since a
        structure can change form between two observations as it goes, as a supply depot lowering does (in game).
        Alliance is, since a refinery stands at the same position as its geyser.
        """
        # What left this observation without dying or being updated under another tag, and the own units among them
        # that had an order, one of which may have become a structure.
        departed: dict[tuple[int, float, float], Unit[Any]] = {}
        ordered: list[Unit[Any]] = []
        for tag, unit in previous.items():
            if unit._step != step and tag not in dead:
                report = unit._latest_data
                departed[(report.alliance, report.pos.x, report.pos.y)] = unit
                if unit._own and report.orders:
                    ordered.append(unit)
        for proto in protos:
            display = proto.display_type
            unit = departed.pop((proto.alliance, proto.pos.x, proto.pos.y), None)
            if unit is not None and {display, unit._latest_data.display_type} == {_IN_VISION, _IN_FOG}:
                if proto.alliance == _ENEMY:
                    sight = self._changes.entered_sight if display == _IN_VISION else self._changes.left_sight
                    sight.append(unit)
                unit._tag = proto.tag
                unit._update(proto, step)
            else:
                unit = self._create_unit(proto, step)
                if ordered and unit._own and (builder := self._find_builder(unit, ordered)) is not None:
                    ordered.remove(builder)
                    self._builders[unit] = builder
            tag = proto.tag
            self._by_tag[tag] = unit
            self._ids[tag] = unit._id
            if display == _IN_FOG:
                self._in_fog_tags.add(tag)
            present[tag] = unit

    def _find_builder(self, structure: Unit[Any], ordered: list[Unit[Any]]) -> Unit[Any] | None:
        """The unit that became `structure`: one that left this observation carrying out the order that makes its type.

        Tested in game: a drone that morphs into a structure leaves the observation with no death reported, and the
        structure appears under a new tag.
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
        return builder

    def _update_builders(self, present: _UnitsByTag) -> None:
        """Mark dead each unit that became a structure which has finished, or died without the unit coming back.

        Tested in game: a drone whose structure is cancelled comes back under its own tag, with its health, in the
        observation that reports the structure dead. One whose structure finishes or is killed the game reports dead
        itself, in that observation, so this marks only one the game did not.
        """
        for structure, builder in list(self._builders.items()):
            if structure._dead or structure.is_complete:
                del self._builders[structure]
                if builder._stale and not builder._dead:
                    self._mark_dead(builder, present, reported=False)

    def _update_unfinished(self) -> None:
        """Note each of this player's unfinished units that has finished, and forget those and the dead."""
        for unit_id, unit in list(self._unfinished.items()):
            if unit._dead:
                del self._unfinished[unit_id]
            elif unit._latest_data.build_progress == 1.0:
                del self._unfinished[unit_id]
                self._changes.finished.append(unit)

    def _handle_departures(self, departed: _UnitsByTag, present: _UnitsByTag, step: int) -> None:
        """Unlink the tags that did not come back, and mark the units behind them stale, or dead where they must be."""
        for tag, unit in departed.items():
            if tag in self._in_fog_tags:
                self._in_fog_tags.discard(tag)
                del self._by_tag[tag]
                if unit._step != step and unit._raw_type not in _MOVABLE_UNIT_TYPE_IDS:
                    # Its spot came into vision without it: tested in game, the game reports no death it cannot see.
                    self._mark_dead(unit, present, reported=False)
                    continue
            if unit._step != step and not unit._stale:
                report = unit._latest_data
                if report.alliance == _ENEMY and report.display_type != _IN_FOG:
                    self._changes.left_sight.append(unit)
                unit._mark_stale()
                self._stale_units[unit._id] = unit

    def _create_unit(self, proto: raw_pb2.Unit, step: int) -> Unit[Any]:
        """A unit first seen in `proto`, under the next id of its alliance."""
        alliance = proto.alliance
        count = self._units_seen_per_alliance[alliance] + 1
        if count >= _IDS_PER_ALLIANCE:
            raise NachOSError(f"a game has seen {_IDS_PER_ALLIANCE - 1} units of alliance {alliance}, all its ids")
        self._units_seen_per_alliance[alliance] = count
        unit_id = alliance * _IDS_PER_ALLIANCE + count
        changes = self._changes
        if alliance == _OWN:
            own: OwnUnit[Any] = OwnUnit(proto, self, unit_id, step)
            changes.created.append(own)
            if proto.build_progress < 1.0:
                self._unfinished[unit_id] = own
            unit: Unit[Any] = own
        else:
            unit = Unit(proto, self, unit_id, step)
            if alliance == _ENEMY:
                changes.first_seen.append(unit)
                if proto.display_type != _IN_FOG:
                    changes.entered_sight.append(unit)
        self._units_by_id[unit_id] = unit
        return unit

    def _mark_dead(self, unit: Unit[Any], present: _UnitsByTag, *, reported: bool) -> None:
        """Mark `unit` dead and let go of it: `reported` dead, or found dead, such as gone from a spot it could not have
        left."""
        # Its tags: the one it was last reported under, and the one it was last in vision under.
        sighting = unit._latest_data_in_vision
        tags = {unit._tag} if sighting is None else {unit._tag, sighting.tag}
        unit._mark_dead()
        (self._changes.died if reported else self._changes.found_dead).append(unit)
        self._stale_units.pop(unit._id, None)
        for tag in tags:
            if self._by_tag.get(tag) is unit:
                del self._by_tag[tag]
            self._in_fog_tags.discard(tag)
            present.pop(tag, None)

    def end(self) -> None:
        """Mark every unit stale, since the game is over."""
        for unit in self._present_units_by_tag.values():
            unit._mark_stale()
        self._present_units_by_tag = {}
        self._present_units = Units()
        self._known_units = None
        self._changes = _Changes()
        self._unfinished = {}
        self._under_construction = None
        self._builders_now = None
