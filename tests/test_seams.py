"""Where one module reads or writes another object's private members, held to a list.

A leading underscore marks what a bot must not use, not what another NachOS module must not call, so the library
crosses its own object boundaries: the unit tracker sets a unit's tag, the order book settles an order. Each such
seam is listed here, so that a new one is a line added on purpose and a leak is caught in review rather than found
later. The scan is by token, over `src`, and leaves out what a module's own classes keep on themselves.
"""

import io
import tokenize
from collections.abc import Mapping, Sequence
from pathlib import Path
from token import NAME, NEWLINE, OP, STRING

_SRC = Path(__file__).parent.parent / "src" / "sc2nachos"

# Each module, with the private members it reads or writes on objects of other classes.
SEAMS: Mapping[str, frozenset[str]] = {
    "_game.py": frozenset({"_key_of", "_subscriptions"}),
    "api.py": frozenset({"_hand_out", "_send", "_set_step", "_start_game"}),
    "events/_event.py": frozenset({"_type_ids"}),
    "events/_event_bus.py": frozenset({"_key"}),
    "events/_event_subscriber.py": frozenset({"_add"}),
    "events/_handler.py": frozenset({"_details"}),
    "gamedata/_game_data.py": frozenset({"_from_proto"}),
    "gamedata/_unit_type_data.py": frozenset({"_from_proto", "_with_weapon_upgrades"}),
    "gamemap/_game_map.py": frozenset({"_from_proto"}),
    "orders/_order_book.py": frozenset(
        {"_acting_units", "_forced", "_latest_report", "_seen_carrying", "_settle", "_taken_by"}
    ),
    "orders/_targets.py": frozenset({"_tracker"}),
    "state/_actions.py": frozenset({"_from_proto"}),
    "state/_score.py": frozenset({"_from_proto"}),
    "state/_state.py": frozenset({"_from_proto", "_type_id"}),
    "units/_own_unit.py": frozenset({"_from_proto"}),
    "units/_tracking/_builder_tracker.py": frozenset(
        {
            "_dead",
            "_ids",
            "_latest_report",
            "_mark_unit_dead",
            "_own",
            "_position",
            "_stale",
            "_type_id",
            "_units_by_id",
        }
    ),
    "units/_tracking/_unit_comparer.py": frozenset({"_id", "_latest_report"}),
    "units/_tracking/_unit_tracker.py": frozenset(
        {
            "_dead",
            "_id",
            "_latest_report",
            "_latest_report_in_vision",
            "_mark_dead",
            "_mark_stale",
            "_own",
            "_raw_type",
            "_stale",
            "_step",
            "_tag",
            "_update",
        }
    ),
    "units/_tracking/_unit_watcher.py": frozenset({"_id", "_latest_report"}),
    "units/_tracking/_upgrade_tracker.py": frozenset({"_latest_report", "_latest_report_in_vision"}),
    "units/_tracking/_watches.py": frozenset({"_id", "_position"}),
    "units/_units.py": frozenset({"_position", "_type_ids"}),
}


def _own_names(tokens: Sequence[tokenize.TokenInfo]) -> set[str]:
    """The private names a module's classes keep on themselves: `self._x`, a `_x:` annotation, and the strings of
    `__slots__`. An access to one of these is class-internal, whatever the receiver is called."""
    own: set[str] = set()
    in_slots = False
    for index, token in enumerate(tokens):
        if token.type == NAME and token.string.startswith("_") and not token.string.startswith("__"):
            before = tokens[index - 1] if index else None
            after = tokens[index + 1].string if index + 1 < len(tokens) else ""
            on_self = before is not None and before.string == "." and tokens[index - 2].string == "self"
            # An annotation opens a logical line; a colon after `if unit._stale` is the statement's.
            annotated = after == ":" and (before is None or before.type == NEWLINE)
            if on_self or annotated:
                own.add(token.string)
        if token.type == NAME and token.string == "__slots__":
            in_slots = True
        elif in_slots and token.type == STRING:
            own.add(token.string.strip("'\""))
        elif in_slots and token.type == NEWLINE:
            in_slots = False
    return own


def _seams_of(path: Path) -> frozenset[str]:
    """The private members `path` reads or writes on objects of other classes: each `receiver._name` that is not
    `self`, `cls`, a protobuf module or an import, and that no class of the module keeps on itself."""
    kept = (NAME, OP, STRING, NEWLINE)
    tokens = [token for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)]
    tokens = [token for token in tokens if token.type in kept]
    own = _own_names(tokens)
    found: set[str] = set()
    importing = line_start = True
    for index, token in enumerate(tokens):
        if line_start:
            importing = token.type == NAME and token.string in ("from", "import")
            line_start = False
        if token.type == NEWLINE:
            line_start = True
            continue
        if importing or token.string != "." or not 0 < index < len(tokens) - 1:
            continue
        receiver, attribute = tokens[index - 1], tokens[index + 1]
        if receiver.type != NAME or attribute.type != NAME:
            continue
        name = attribute.string
        if not name.startswith("_") or name.startswith("__") or name in own:
            continue
        if receiver.string in ("self", "cls") or receiver.string.endswith("_pb2"):
            continue
        found.add(name)
    return frozenset(found)


def test_every_seam_between_modules_is_listed() -> None:
    """The private members each module reaches into are exactly those `SEAMS` lists: one added is a line to add
    here, on purpose, and one gone is a line to take out."""
    found = {
        str(path.relative_to(_SRC)).replace("\\", "/"): seams
        for path in sorted(_SRC.rglob("*.py"))
        if (seams := _seams_of(path))
    }
    unlisted = {module: sorted(seams - SEAMS.get(module, frozenset())) for module, seams in found.items()}
    unlisted = {module: names for module, names in unlisted.items() if names}
    stale = {module: sorted(names - found.get(module, frozenset())) for module, names in SEAMS.items()}
    stale = {module: names for module, names in stale.items() if names}
    assert not unlisted, f"private members reached into that SEAMS does not list, to add on purpose: {unlisted}"
    assert not stale, f"SEAMS lists members no module reaches into any more, to take out: {stale}"


def test_the_scan_finds_a_reach_into_another_object_and_nothing_else(tmp_path: Path) -> None:
    """A seam is `receiver._name` on something other than `self`, `cls` or a protobuf module, outside an import;
    what a class keeps on itself, by `self._x`, an annotation or `__slots__`, is not one however it is reached."""
    module = tmp_path / "_probe.py"
    module.write_text(
        "from sc2nachos.units._tracking import _Tracker\n"
        "\n"
        "\n"
        "class Probe:\n"
        "    __slots__ = ('_slotted',)\n"
        "    _annotated: int\n"
        "\n"
        "    def read(self, unit, other, raw_pb2):\n"
        "        self._own = unit._tag\n"
        "        if unit._stale:\n"
        "            return other._own, other._slotted, other._annotated, raw_pb2._internal\n"
        "        return unit._tracker._parts, self.__class__, unit.__dict__, type(unit)._tags\n",
        encoding="utf-8",
    )
    assert _seams_of(module) == {"_tag", "_stale", "_tracker", "_parts"}
