"""The commands a bot gives in a turn, sent after its handlers have run."""

from __future__ import annotations

from collections import deque
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, final

from s2clientprotocol import raw_pb2

from sc2nachos.commands._action_to_send import actions_giving
from sc2nachos.commands._command import Command
from sc2nachos.commands._targets import aimed_at, check_target, same_point, same_target
from sc2nachos.gamedata import CommandBehavior, Cost
from sc2nachos.gamedata._sent_as import SENT_UNCHANGED
from sc2nachos.gamedata._techtree import ABILITIES_SENT_AS_ANOTHER
from sc2nachos.gametime import GameTime
from sc2nachos.geometry import Point
from sc2nachos.ids import AbilityId, UnitTypeId, UpgradeId
from sc2nachos.protocol import ProtocolError
from sc2nachos.state import ActionFailure, ActionResult
from sc2nachos.units import OwnUnit, Unit, Units, UnitType
from sc2nachos.units._units import _unit_tuple

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from s2clientprotocol import sc2api_pb2

    from sc2nachos.gamedata import AbilityData, GameData
    from sc2nachos.gamedata._sent_as import SentAs
    from sc2nachos.geometry import PointLike
    from sc2nachos.protocol import Client
    from sc2nachos.units import Target

# The observations after the one a turn read by which its commands show: in a unit's orders, or, for one carried out
# at once, in its effect. The next in a stepped game, and the one after that on the ladder or in realtime
# (docs/reference/game-behavior/protocol.md).
_SHOWN_WITHIN = 2
# How long before the last observation a unit's sent commands are kept.
_COMMANDS_KEPT_FOR = GameTime(seconds=60)
# The ability each game ability is, aimed at the unit given it: an unload at a point aimed at the transport itself is
# `UNLOAD`.
_IS_AIMED_AT_ITSELF = MappingProxyType(
    {
        sending.ability: ability
        for ability, sent_as in ABILITIES_SENT_AS_ANOTHER.items()
        for sending in sent_as.values()
        if sending.aimed_at_itself
    }
)


@final
class CommandBook:
    """This player's commands for the turn.

    A bot gives commands through `api.commands` while its handlers run. They are sent in one request after the turn's
    last handler returns. A unit carries out only the last command it is given in a turn, plus any abilities it
    carries out at once.
    """

    __slots__ = (
        "_built_abilities",
        "_forced",
        "_game_data",
        "_staged",
        "_staged_by_unit",
        "_last_sent",
        "_observations",
        "_sent_at_once",
        "_sent_by_unit",
        "_sent_not_replacing",
        "_refusals",
        "_time",
        "_trained_abilities",
    )

    def __init__(self, game_data: GameData) -> None:
        """A book for one game. `game_data` says what each ability does and what it must be aimed at."""
        self._game_data = game_data
        # The turn's commands in the order they were given, and those of each unit by its id.
        self._staged: list[Command] = []
        self._staged_by_unit: dict[int, list[Command]] = {}
        # The turn's commands given with `force`, sent even to units already carrying them out.
        self._forced: set[Command] = set()
        # By unit id, the last unqueued command the game took that replaces the unit's orders, and the number of the
        # observation its turn read.
        self._last_sent: dict[int, tuple[Command, int]] = {}
        # By unit id, each command the game took that does not replace the unit's orders, one queued, a train, a
        # research, an add-on or a morph, with the number of the observation its turn read, until the observations must
        # show it or a command replaces the unit's orders.
        self._sent_not_replacing: dict[int, list[tuple[Command, int]]] = {}
        # By unit id, each unqueued command the game took that the unit carries out at once, a stim, a rally or a MULE,
        # with the number of the observation its turn read, until the observations must show its effect. A command
        # that replaces the unit's orders leaves these, since the unit did both.
        self._sent_at_once: dict[int, list[tuple[Command, int]]] = {}
        # By unit id, each command that went out to the unit and was not refused, oldest first, for
        # `_COMMANDS_KEPT_FOR` before the last observation.
        self._sent_by_unit: dict[int, deque[Command]] = {}
        # What the game refused of the last turn's commands, one entry per unit a refused command named.
        self._refusals: tuple[ActionFailure, ...] = ()
        self._observations = 0
        self._time = GameTime.ZERO
        # By maker and product, the ability `train` gives, and the one `build` gives.
        abilities_making = _abilities_making(game_data)
        self._trained_abilities = {pair: ability for pair, ability in abilities_making.items() if _is_trained(*pair)}
        self._built_abilities = {pair: ability for pair, ability in abilities_making.items() if _is_built(*pair)}

    def command(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        ability: AbilityId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
    ) -> Command:
        """Command `units` to use `ability`, aimed at a point, a unit or nothing, and return the `Command`.

        Every unit given carries it out, and a unit given twice counts once. An ability every unit of one command
        carries out (`AbilityData.multi_unit`) goes out as one action naming them all, so units commanded together
        keep their spacing where the game spreads them; any other goes out as an action to each. `queued` puts the
        command behind each unit's orders. Nothing is sent until the turn's handlers have all run.

        Commands of an ability that joins other orders (`AbilityData.joins_other_orders`), given apart in one turn, go
        out as one action where they are aimed at the same unit, or at the same point as the game keeps it, and are
        queued alike, so units commanded one at a time keep their spacing too. A command joins the last such action
        before it unless one of its units is named in that action or a later one, so a unit's commands go out in the
        order it was given them; another unit's may go out ahead of commands given before them. A command aimed at one
        of its own units, as a transport's unload where it stands is, joins nothing, and nothing joins it. The game
        answers an action naming several units `Success` if any of them can take it, so then every command it carries
        counts as sent: one whose units could not take it is not listed in `api.action_failures`, and the same command
        given them again is left out as one they are carrying out until the observations would show it.

        An unqueued command that replaces a unit's orders is not sent to a unit already doing it: sending it would only
        drop what the unit has queued behind it. What a unit is doing is the last such command sent to it, while the
        observation after its turn may not show it yet, and otherwise its first reported order. `force` sends it to
        those units too: that is how a unit is made to drop its queue and go on with its current order. It still
        competes with the turn's other commands like any other.

        An ability that goes out as another for some types (`AbilityData.sent_as`) goes out as that one to their units,
        in actions of its own, which join other commands' as the ability's own do. An action the game refuses is listed
        in `api.action_failures` once for each unit it names, under the ability of that unit's command.

        Raises `TypeError` for a target the ability cannot be aimed at, for an add-on with no point given to a flying
        structure, and for a transport's unload at a point aimed at one of `units` itself, which is `UNLOAD` for that
        one: a group given an unload at one of its own transports is two commands. Raises `ValueError` for a research
        given to several units, and for an ability that places something at its target (`AbilityData.places_at_target`)
        given to several aimed at one; and for a custom ability given to a type it has nothing to be sent as for, or
        which this game's tables lack an ability it is sent as for.
        """
        given = _unit_tuple(units)
        if len(given) > 1:
            given = tuple(dict.fromkeys(given))
        if not given:
            raise ValueError(_NEEDS_A_UNIT)
        row = self._game_data.abilities.get(ability)
        sent_as = ABILITIES_SENT_AS_ANOTHER.get(ability, SENT_UNCHANGED)
        if ability.is_custom:
            if row is None:
                raise ValueError(f"{ability.name} has nothing in this game's tables to be sent as")
            _check_sent_as(ability, sent_as, given)
        aimed = aimed_at(target)
        check_target(ability, aimed, row)
        _check_each_can_carry_it_out(ability, row, aimed, len(given))
        if aimed is None:
            _check_placed_where_flying(ability, row, given)
        if isinstance(aimed, Unit):
            _check_not_aimed_at_itself(ability, aimed, given)
        command = Command(ability, Units(given), aimed, queued=queued, time=self._time)
        self._add(command)
        if force:
            self._forced.add(command)
        return command

    def train(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        unit_type: UnitTypeId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
    ) -> Command:
        """Command `units` to make a unit of `unit_type` and return the `Command`, as `command` gives the ability
        their type makes it with: a barracks's `BARRACKS_TRAIN_MARINE`, a warp gate's `WARP_GATE_WARP_IN_ZEALOT`, aimed
        at a point, a larva's `LARVA_MORPH_DRONE`.

        It commands what a structure or a larva makes that is not a structure; `build` commands a structure a worker
        makes and an add-on, and `command` the rest, a morph such as an orbital command's or a baneling's among them.

        Raises `ValueError` where one of `units` does not train `unit_type`, saying what does, and where two of them
        make it with different abilities, as a gateway and a warp gate a zealot: they are commanded apart. Raises
        otherwise as `command` does.
        """
        given = _unit_tuple(units)
        ability = self._ability_making(given, unit_type, self._trained_abilities, "train")
        return self.command(given, ability, target=target, queued=queued, force=force)

    def build(
        self,
        units: OwnUnit[Any] | Iterable[OwnUnit[Any]],
        unit_type: UnitTypeId,
        *,
        target: PointLike | Unit[Any] | None = None,
        queued: bool = False,
        force: bool = False,
    ) -> Command:
        """Command `units` to make a structure of `unit_type` and return the `Command`, as `command` gives the
        ability their type makes it with: an SCV's `SCV_BUILD_SUPPLY_DEPOT` aimed at a point, a drone's
        `DRONE_MORPH_EXTRACTOR` aimed at a geyser, a barracks's `BUILD_TECH_LAB`.

        It commands a structure a worker makes, and an add-on; `train` commands what a structure or a larva makes that
        is not a structure, and `command` the rest, a morph such as an orbital command's or a lair's among them.

        Raises `ValueError` where one of `units` does not build `unit_type`, saying what does, and where two of them
        make it with different abilities. Raises otherwise as `command` does: `ValueError` for workers given one site.
        """
        given = _unit_tuple(units)
        ability = self._ability_making(given, unit_type, self._built_abilities, "build")
        return self.command(given, ability, target=target, queued=queued, force=force)

    def research(self, unit: OwnUnit[Any], upgrade: UpgradeId, *, queued: bool = False, force: bool = False) -> Command:
        """Command `unit` to research `upgrade` and return the `Command`, as `command` gives the upgrade's
        `research_ability`: Stimpack's `BARRACKS_TECH_LAB_RESEARCH_STIMPACK`.

        Raises `ValueError` where nothing researches `upgrade`, and where `unit`'s type is not one of its
        `researchers`: a barracks's upgrades are its tech lab's. Raises otherwise as `command` does.
        """
        row = self._game_data.upgrades.get(upgrade)
        if row is None or row.research_ability is None:
            raise ValueError(f"nothing researches {upgrade.name}")
        if unit.type_id not in row.researchers:
            researchers = _listed_with_or(row.researchers)
            raise ValueError(f"{upgrade.name} is researched by {researchers}, not {_with_article(unit.type_id)}")
        return self.command(unit, row.research_ability, queued=queued, force=force)

    def _ability_making(
        self,
        units: tuple[OwnUnit[Any], ...],
        product: UnitTypeId,
        abilities: Mapping[tuple[UnitTypeId, UnitTypeId], AbilityId],
        method: str,
    ) -> AbilityId:
        """The one ability `units` make `product` with, of `abilities`, those the command book's `method` gives.
        Raises `ValueError` as `train` and `build` say."""
        if not units:
            raise ValueError(_NEEDS_A_UNIT)
        makers_by_ability: dict[AbilityId, list[UnitTypeId]] = {}
        for maker in dict.fromkeys(unit.type_id for unit in units):
            ability = abilities.get((maker, product))
            if ability is None:
                raise ValueError(self._why_not_made(maker, product, method))
            makers_by_ability.setdefault(ability, []).append(maker)
        if len(makers_by_ability) > 1:
            makers = " and ".join(_with_article(maker) for makers in makers_by_ability.values() for maker in makers)
            used = " and ".join(ability.name for ability in makers_by_ability)
            made = _with_article(product)
            raise ValueError(f"{makers} make {made} with different abilities, {used}: command them apart")
        return next(iter(makers_by_ability))

    def _why_not_made(self, maker: UnitTypeId, product: UnitTypeId, method: str) -> str:
        """Why `method` cannot command a `maker` to make a `product`: what commands it instead, or what makes one."""
        abilities_making = _abilities_making(self._game_data)
        if (ability := abilities_making.get((maker, product))) is not None:
            ordered_by = "train" if _is_trained(maker, product) else "build" if _is_built(maker, product) else "command"
            made_with = f"{_with_article(maker)} makes {_with_article(product)} with {ability.name}"
            return f"{made_with}, which `{ordered_by}` orders, not `{method}`"
        makers = {made_by for made_by, made in abilities_making if made is product}
        if makers:
            return f"{_with_article(maker)} does not make {_with_article(product)}: {_listed_with_or(makers)} does"
        if (own_add_on := _own_add_on_of_kind(maker, product, abilities_making)) is not None:
            return f"nothing makes a bare {product.name}: {_with_article(maker)} makes {_with_article(own_add_on)}"
        return f"nothing makes {_with_article(product)}"

    def _staged_for(self, unit: OwnUnit[Any]) -> tuple[Command, ...]:
        """The commands given to `unit` this turn and not withdrawn, in the order they were given."""
        return tuple(command for command in self._staged_by_unit.get(unit.id, ()) if not command._withdrawn)

    def _pending_for(self, unit: OwnUnit[Any]) -> tuple[Command, ...]:
        """The commands `unit` may not show yet: those sent lately, then those given this turn."""
        return (*self._sent_but_not_observed_yet(unit), *self._staged_for(unit))

    def _commands_since(self, unit: OwnUnit[Any], since: GameTime) -> tuple[Command, ...]:
        """The commands given to `unit` at or after `since` that went out to it and were not refused, oldest first,
        then those given this turn. Raises `ValueError` for a `since` further back than the commands are kept."""
        if self._time - since > _COMMANDS_KEPT_FOR:
            raise ValueError(
                f"a unit's commands are kept for {_COMMANDS_KEPT_FOR.seconds:g} seconds before the last observation, "
                f"at {self._time}: {since} is further back"
            )
        sent = (command for command in self._sent_by_unit.get(unit.id, ()) if command.time >= since)
        return (*sent, *(command for command in self._staged_for(unit) if command.time >= since))

    @property
    def staged(self) -> tuple[Command, ...]:
        """Every command given this turn and still to be sent. Withdrawn commands are left out."""
        return tuple(command for command in self._staged if not command._withdrawn)

    @property
    def staged_cost(self) -> Cost:
        """What the bank does not show yet: the cost of the commands of `staged` that will go out, each its ability's
        `cost` in the game's tables for each unit it goes out to. A unit given commands that replace one another pays
        for the last, and one already carrying a command out pays nothing for it again. Its time is every command's
        added up, though the units may carry them out side by side."""
        total = Cost(0, 0)
        for command, units in self._commands_to_send(self._staged, self._forced):
            row = self._game_data.abilities.get(command.ability)
            if row is not None:
                total += row.cost * len(units)
        return total

    def _send(self, client: Client, *, then: Sequence[sc2api_pb2.Action] = ()) -> None:
        """Send the turn's commands and after them `then`, actions that belong to no command, in one request, and keep
        what the game refused of the commands. Sends nothing if there is nothing to send."""
        staged, forced = self._staged, self._forced
        self._staged, self._staged_by_unit, self._forced = [], {}, set()
        self._refusals = ()
        actions_to_send = actions_giving(self._commands_to_send(staged, forced))
        actions = [*(action_to_send.to_action() for action_to_send in actions_to_send), *then]
        if not actions:
            return
        results = client.act(actions).result
        if len(results) < len(actions_to_send):
            raise ProtocolError(f"the game answered {len(results)} of the {len(actions_to_send)} actions it was sent")
        # What comes after the commands' actions is answered after them, and belongs to no command.
        refusals: list[ActionFailure] = []
        for action_to_send, result in zip(actions_to_send, results, strict=False):
            action_result = ActionResult.read(result)
            for command, units in action_to_send.parts:
                if action_result is ActionResult.SUCCESS:
                    self._remember_sent(command, units)
                else:
                    refusals += [ActionFailure(self._time, unit, command.ability, action_result) for unit in units]
        self._refusals = tuple(refusals)

    def _observe(self, time: GameTime, lost: Iterable[Unit[Any]]) -> None:
        """Take in the observation at `time`, which found `lost` dead or no longer this player's."""
        self._time = time
        self._observations += 1
        for unit in lost:
            self._sent_by_unit.pop(unit.id, None)
            self._last_sent.pop(unit.id, None)
            self._sent_not_replacing.pop(unit.id, None)
            self._sent_at_once.pop(unit.id, None)
        recent = self._observations - _SHOWN_WITHIN + 1
        for sent_by_unit in (self._sent_not_replacing, self._sent_at_once):
            for unit_id, sent in list(sent_by_unit.items()):
                if not (sent := [(command, sent_at) for command, sent_at in sent if sent_at >= recent]):
                    del sent_by_unit[unit_id]
                else:
                    sent_by_unit[unit_id] = sent
        kept_from = time - _COMMANDS_KEPT_FOR
        for unit_id, sent_to_unit in list(self._sent_by_unit.items()):
            while sent_to_unit and sent_to_unit[0].time < kept_from:
                sent_to_unit.popleft()
            if not sent_to_unit:
                del self._sent_by_unit[unit_id]

    def _commands_to_send(
        self, staged: Sequence[Command], forced: Collection[Command]
    ) -> Iterator[tuple[Command, tuple[OwnUnit[Any], ...]]]:
        """Each command of `staged` that goes out, with the units it goes out to.

        A command that acts at once, or that is queued behind a unit's orders, competes with nothing. Of the rest, a
        unit keeps only the last it was given. A command is then left out for the units already carrying it out, unless
        it is in `forced`. A withdrawn command, or one left with no unit, is not sent.
        """
        holder: dict[int, Command] = {}
        for command in staged:
            if not command._withdrawn:
                for unit in command.units:
                    if self._competes_for(command, unit):
                        holder[unit.id] = command
        for command in staged:
            if command._withdrawn:
                continue
            units = tuple(
                unit
                for unit in command.units
                if holder.get(unit.id) is command or not self._competes_for(command, unit)
            )
            if command not in forced:
                units = self._units_not_carrying_it_out(command, units)
            if units:
                yield command, units

    def _competes_for(self, command: Command, unit: OwnUnit[Any]) -> bool:
        """Whether `command` competes with the turn's other commands for `unit`, which then takes the last of them.

        Every unqueued command does, a structure's train included, whatever it would do to the unit. A queued command
        competes with nothing, and nor does an ability the unit's type carries out at once: the unit does both (in
        game). Each unit of a group is judged by its own type.
        """
        if command.queued:
            return False
        row = self._game_data.abilities.get(command.ability)
        return _behavior_for(row, unit) is not CommandBehavior.KEEPS_ORDERS

    def _add(self, command: Command) -> None:
        """Count `command` among the turn's, last."""
        self._staged.append(command)
        for unit_id in {unit.id for unit in command.units}:
            self._staged_by_unit.setdefault(unit_id, []).append(command)

    def _remember_sent(self, command: Command, units: Sequence[OwnUnit[Any]]) -> None:
        """Record `command`, which the game took, as sent to each of `units`: the last sent to a unit whose orders it
        replaces, unqueued, one sent that the unit carries out at once, unqueued, and otherwise one sent that does not
        replace them."""
        row = self._game_data.abilities.get(command.ability)
        for unit in units:
            self._sent_by_unit.setdefault(unit.id, deque()).append(command)
            behavior = _behavior_for(row, unit)
            sent = (command, self._observations)
            if not command.queued and behavior is CommandBehavior.REPLACES:
                self._last_sent[unit.id] = sent
                self._sent_not_replacing.pop(unit.id, None)
            elif not command.queued and behavior is CommandBehavior.KEEPS_ORDERS:
                self._sent_at_once.setdefault(unit.id, []).append(sent)
            else:
                self._sent_not_replacing.setdefault(unit.id, []).append(sent)

    def _units_not_carrying_it_out(self, command: Command, units: tuple[OwnUnit[Any], ...]) -> tuple[OwnUnit[Any], ...]:
        """The units of `units` that `command` would change anything for: all of them for a queued command, else all
        but those whose orders it replaces and which are already doing it."""
        if command.queued:
            return units
        row = self._game_data.abilities.get(command.ability)
        return tuple(
            unit
            for unit in units
            if _behavior_for(row, unit) is not CommandBehavior.REPLACES
            or not self._is_doing(unit, command.ability, command.target)
        )

    def _is_doing(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit` is doing `ability` at `target`: the last command sent to it says, while it may not show yet,
        and otherwise its first reported order."""
        sent = self._sent_and_not_shown(unit)
        if sent is not None:
            return sent.ability is ability and same_target(sent.target, target)
        return self._unit_is_at(unit, ability, target)

    def _sent_and_not_shown(self, unit: OwnUnit[Any]) -> Command | None:
        """The last command sent to `unit` that replaced its orders, while the observations may not show it yet."""
        last = self._last_sent.get(unit.id)
        if last is not None and self._observations - last[1] < _SHOWN_WITHIN:
            return last[0]
        return None

    def _sent_but_not_observed_yet(self, unit: OwnUnit[Any]) -> list[Command]:
        """The commands that went out to `unit` lately and that the observations may not show yet: the last sent that
        replaced its orders, those sent since that did not, and those it carries out at once, whose effect may not
        show yet."""
        commands = []
        if (sent := self._sent_and_not_shown(unit)) is not None:
            commands.append(sent)
        recent = self._observations - _SHOWN_WITHIN + 1
        commands += [command for command, sent_at in self._sent_not_replacing.get(unit.id, ()) if sent_at >= recent]
        commands += [command for command, sent_at in self._sent_at_once.get(unit.id, ()) if sent_at >= recent]
        return commands

    def _is_carried_out_at_once(self, command: Command, unit: OwnUnit[Any]) -> bool:
        """Whether `unit` carries `command` out at once, leaving its orders as they were: unqueued, an ability its type
        keeps its orders through."""
        return not command.queued and _behavior_for(self._game_data.abilities.get(command.ability), unit) is (
            CommandBehavior.KEEPS_ORDERS
        )

    def _unit_is_at(self, unit: OwnUnit[Any], ability: AbilityId, target: Target | None) -> bool:
        """Whether `unit`'s first order runs `ability` at `target`. A unit the last observation left out, in a
        transport for one, is at nothing."""
        orders = unit._latest_report.orders
        if not unit.is_listed or not orders:
            return False
        first = orders[0]
        if _ability_of_unit_order(first) is not ability:
            return False
        match first.WhichOneof("target"):
            case "target_world_space_pos":
                point = first.target_world_space_pos
                return isinstance(target, Point) and same_point(target, point.x, point.y)
            case "target_unit_tag":
                return isinstance(target, Unit) and unit._unit_target_of_order(first) is target
            case _:
                return target is None


def _behavior_for(row: AbilityData | None, unit: OwnUnit[Any]) -> CommandBehavior:
    """What an ability does to the orders of `unit`, a unit of its type. An ability with no row replaces
    them."""
    return CommandBehavior.REPLACES if row is None else row.command_behavior_for(unit.type_id)


def _ability_of_unit_order(order: raw_pb2.UnitOrder) -> AbilityId:
    """The ability a raw order runs, or `NULL` if the curated ids leave it out. No command of ours gives such an
    ability."""
    return AbilityId.get(order.ability_id) or AbilityId.NULL


_NEEDS_A_UNIT = "a command needs a unit to give it to"


def _abilities_making(game_data: GameData) -> dict[tuple[UnitTypeId, UnitTypeId], AbilityId]:
    """The ability each unit type makes each unit type with, by maker and product: one for each pair (tool
    `sweep_tech_tree`), a warp gate's zealot a warp-in where a gateway's is a train."""
    abilities_making = {
        (maker, product): ability
        for ability, row in game_data.abilities.items()
        for maker, product in row.products.items()
        if isinstance(product, UnitTypeId)
    }
    # An ability's products name one type for each maker, so a rich gas building, made with its plain one's ability on a
    # rich geyser, is read off its own creation ability and producers.
    for unit_type, row in game_data.units.items():
        if row.creation_ability is not None:
            for producer in row.producers:
                abilities_making.setdefault((producer, unit_type), row.creation_ability)
    return abilities_making


def _is_trained(maker: UnitTypeId, product: UnitTypeId) -> bool:
    """Whether `CommandBook.train` commands a `maker` to make a `product`: a unit not a structure, made by a structure
    or a larva."""
    structures = UnitType.Structure.type_ids
    return product not in structures and (maker in structures or maker is UnitTypeId.LARVA)


def _is_built(maker: UnitTypeId, product: UnitTypeId) -> bool:
    """Whether `CommandBook.build` commands a `maker` to make a `product`: a structure made by a worker, or an
    add-on."""
    if product in UnitType.AnyAddOn.type_ids:
        return True
    return product in UnitType.Structure.type_ids and maker in UnitType.Worker.type_ids


def _own_add_on_of_kind(
    maker: UnitTypeId, add_on: UnitTypeId, abilities_making: Mapping[tuple[UnitTypeId, UnitTypeId], AbilityId]
) -> UnitTypeId | None:
    """The add-on of `add_on`'s kind that `maker` makes, a `TECH_LAB_BARRACKS` for a barracks given a bare `TECH_LAB`;
    `None` for anything but a tech lab or a reactor, or a maker of neither."""
    for kind in (UnitType.AnyTechLab, UnitType.AnyReactor):
        if add_on in kind.type_ids:
            return next(
                (made for made_by, made in abilities_making if made_by is maker and made in kind.type_ids), None
            )
    return None


def _with_article(unit_type: UnitTypeId) -> str:
    """The type's name with "a" or "an" before it, by its first letter: "a BARRACKS", "an ORBITAL_COMMAND"."""
    return f"{'an' if unit_type.name[0] in 'AEIOU' else 'a'} {unit_type.name}"


def _listed_with_or(unit_types: Collection[UnitTypeId]) -> str:
    """`unit_types` as a phrase, in alphabetical order: "a HATCHERY, a HIVE or a LAIR"."""
    names = [_with_article(unit_type) for unit_type in sorted(unit_types, key=lambda unit_type: unit_type.name)]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def _check_sent_as(ability: AbilityId, sent_as: Mapping[UnitTypeId, SentAs], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `ValueError` if the custom `ability` has nothing to be sent as for one of `units`."""
    if missing := sorted({unit.type_id.name for unit in units if unit.type_id not in sent_as}):
        raise ValueError(f"{ability.name} has nothing to be sent as for {', '.join(missing)}")


def _check_each_can_carry_it_out(
    ability: AbilityId, row: AbilityData | None, target: Target | None, count: int
) -> None:
    """Raise `ValueError` if `ability`, given to `count` units, is a research, or places something at `target`."""
    if count < 2 or row is None:
        return
    if row.is_research:
        raise ValueError(f"give {ability.name} to one unit, not {count}: only one of them can research it")
    if row.places_at_target and target is not None:
        raise ValueError(f"give {ability.name} to one unit, not {count}: only one of them can carry it out there")


def _check_placed_where_flying(ability: AbilityId, row: AbilityData | None, units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability`, given no target, places something and one of `units` is flying."""
    if row is None or not row.needs_placement:
        return
    if flying := sorted({unit.type_id.name for unit in units if unit.is_flying}):
        raise TypeError(f"{ability.name} takes a point for a flying {', '.join(flying)}, and was given no target")


def _check_not_aimed_at_itself(ability: AbilityId, target: Unit[Any], units: Sequence[OwnUnit[Any]]) -> None:
    """Raise `TypeError` if `ability` is aimed at one of `units` and another id is that."""
    custom = _IS_AIMED_AT_ITSELF.get(ability)
    if custom is not None and any(unit.id == target.id for unit in units):
        raise TypeError(f"{ability.name} aimed at the unit itself is {custom.name}")
