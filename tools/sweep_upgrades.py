"""Find what each upgrade adds to each unit type's weapons, armor and speed, by researching it and asking again.

Needs StarCraft II installed. Plays one game as each race, or only those named, under the `free`, `fast_build` and
`food` cheats::

    uv run python tools/sweep_upgrades.py
    uv run python tools/sweep_upgrades.py zerg --out upgrades-zerg.json

`RequestData` folds the asking player's upgrades into the unit type rows, so each upgrade is researched on its own and
the rows are read again once it is done. Measured in game while writing this:

- The rows reflect weapon damage, damage bonuses and range, armor and speed, and a bonus can be new: Infernal
  Pre-Igniter gives a hellbat one against light. They do not reflect attack speed (Adrenal Glands, Resonating
  Glaives), shield armor, or anything an ability or a buff does. Anabolic Synthesis is in them, though it counts only
  off creep.
- Every level of a leveled upgrade adds the same amount to every unit type.
- A unit reports its attack upgrades as a level count, but `armor_upgrade_level` is the armor its upgrades add, which
  counts Chitinous Plating's 2 as well as each level.
- The `god` cheat multiplies every weapon's damage in the rows by 10 a few steps after it is turned on, so it is never
  on here.

Each game puts up every structure of the race and one of every other unit type, then researches one upgrade at a time
and records how every row changed and whose reported levels rose. Once everything is researched it checks that each row
equals the first row plus every change found, and after each upgrade that every unit's base armor plus its reported
armor upgrades equals its row's armor.

The findings are written as JSON, in the raw catalog's spelling, for `tools/generate_tech_tree.py`.
"""

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from _sandbox import Sandbox, playing
from loguru import logger
from s2clientprotocol import data_pb2, raw_pb2, sc2api_pb2
from sweep_tech_tree import Findings as TechFindings
from sweep_tech_tree import TechSweep

from sc2nachos.ids.raw import RawUnitTypeId, RawUpgradeId
from sc2nachos.launch import Installation
from sc2nachos.match import Race
from sc2nachos.protocol import GameEndedError

OUT = Path(__file__).parents[1] / "data" / "upgrades.json"

# The row fields upgrades change. A change to any other field is reported as unexplained.
_UPGRADED_FIELDS = ("armor", "movement_speed", "weapons")
# How far apart two values may be and still count as equal; the game keeps them as 32-bit floats.
_TOLERANCE = 1e-4

type Change = dict[str, object]
"""What one upgrade adds to one unit type's row, as it is written out."""


@dataclass(slots=True)
class Findings:
    """Everything read so far, in the raw catalog's spelling."""

    base_build: int = 0
    races: set[str] = field(default_factory=set)
    # Each upgrade researched, in order, with how every row changed and whose reported levels rose.
    research: list[dict[str, object]] = field(default_factory=list)
    # What the changes do not account for; the generator refuses these.
    unexplained: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "base_build": self.base_build,
            "races": sorted(self.races),
            "research": self.research,
            "unexplained": self.unexplained,
        }


class UpgradeSweep(TechSweep):
    """One game played as `race`, and what each of its upgrades changes."""

    def __init__(self, game: Sandbox, race: Race, findings: Findings) -> None:
        # The rows before any cheat, which every change found is summed against.
        self._first = _rows(game.client.game_data(abilities=False, upgrades=False, buffs=False, effects=False))
        super().__init__(game, race, TechFindings(), cheats=("free", "fast_build", "food"))
        self.upgrade_findings = findings
        findings.base_build = self.findings.base_build

    def sweep_upgrades(self) -> None:
        """Research everything, one upgrade at a time, reading what each changes."""
        rows = self._asked()
        levels = self._levels()
        changed: dict[int, list[Change]] = {}
        for upgrades in self.research_each():
            names = sorted(RawUpgradeId(upgrade).name for upgrade in upgrades)
            now, now_levels = self._asked(), self._levels()
            changes: dict[str, Change] = {}
            for unit_type, row in now.items():
                if (change := _change(rows[unit_type], row, names, self.upgrade_findings.unexplained)) is not None:
                    changes[_unit_name(unit_type)] = change
                    changed.setdefault(unit_type, []).append(change)
            risen = {
                kind: sorted(
                    _unit_name(unit_type)
                    for unit_type, reported in now_levels.items()
                    if reported[index] > levels.get(unit_type, (0, 0, 0))[index]
                )
                for index, kind in enumerate(("attack", "armor", "shield"))
            }
            logger.info("{} changes {} and raises the levels of {}", names, sorted(changes), risen)
            self.upgrade_findings.research.append({"upgrades": names, "changes": changes, "levels": risen})
            self._check_armor(now, names)
            rows, levels = now, now_levels
        self._check_sums(rows, changed)

    def _asked(self) -> dict[int, data_pb2.UnitTypeData]:
        return _rows(self._client.game_data(abilities=False, upgrades=False, buffs=False, effects=False))

    def _levels(self) -> dict[int, tuple[int, int, int]]:
        """The attack, armor and shield upgrade levels reported by each type of this player's visible units."""
        levels: dict[int, tuple[int, int, int]] = {}
        for unit in self._seen_units():
            reported = (unit.attack_upgrade_level, unit.armor_upgrade_level, unit.shield_upgrade_level)
            levels[unit.unit_type] = max(reported, levels.get(unit.unit_type, (0, 0, 0)))
        return levels

    def _seen_units(self) -> list[raw_pb2.Unit]:
        return [unit for unit in self._mine() if unit.display_type == raw_pb2.DisplayType.Visible]

    def _check_armor(self, rows: dict[int, data_pb2.UnitTypeData], names: list[str]) -> None:
        """Note every unit whose base armor plus its reported armor upgrades is not its row's armor."""
        for unit in self._seen_units():
            expected = self._first[unit.unit_type].armor + unit.armor_upgrade_level
            if not math.isclose(rows[unit.unit_type].armor, expected, abs_tol=_TOLERANCE):
                self.upgrade_findings.unexplained.append(
                    f"after {names}, a {_unit_name(unit.unit_type)} reports {unit.armor_upgrade_level} armor from "
                    f"upgrades on {self._first[unit.unit_type].armor}, and its row says {rows[unit.unit_type].armor}"
                )

    def _check_sums(self, rows: dict[int, data_pb2.UnitTypeData], changed: dict[int, list[Change]]) -> None:
        """Note every row that is not the first row plus every change found."""
        for unit_type, row in rows.items():
            expected = _values(self._first[unit_type])
            for change in changed.get(unit_type, []):
                expected = _added(expected, change)
            if _rounded(expected) != _rounded(_values(row)):
                self.upgrade_findings.unexplained.append(
                    f"{_unit_name(unit_type)} is {_values(row)}, and its changes add up to {expected}"
                )


def _rows(data: sc2api_pb2.ResponseData) -> dict[int, data_pb2.UnitTypeData]:
    return {row.unit_id: row for row in data.units}


def _change(
    old: data_pb2.UnitTypeData, new: data_pb2.UnitTypeData, names: list[str], unexplained: list[str]
) -> Change | None:
    """What `new` adds to `old`, or `None` where nothing. A difference that cannot be written as a change goes in
    `unexplained` instead."""
    if old == new:
        return None
    rest_old, rest_new = data_pb2.UnitTypeData(), data_pb2.UnitTypeData()
    rest_old.CopyFrom(old)
    rest_new.CopyFrom(new)
    for name in _UPGRADED_FIELDS:
        rest_old.ClearField(name)
        rest_new.ClearField(name)
    name = _unit_name(new.unit_id)
    if rest_old != rest_new:
        unexplained.append(f"{names} changes more of {name} than its weapons, armor and speed")
    if [weapon.type for weapon in old.weapons] != [weapon.type for weapon in new.weapons]:
        unexplained.append(f"{names} changes which weapons {name} has")
        return None
    change: Change = {}
    if new.armor != old.armor:
        change["armor"] = new.armor - old.armor
    if new.movement_speed != old.movement_speed:
        change["speed"] = new.movement_speed - old.movement_speed
    weapons = [_weapon_change(before, after) for before, after in zip(old.weapons, new.weapons, strict=True)]
    if any(weapons):
        change["weapons"] = weapons
    return change or None


def _weapon_change(old: data_pb2.Weapon, new: data_pb2.Weapon) -> dict[str, object]:
    """What `new` adds to `old`: only the fields that changed."""
    change: dict[str, object] = {}
    for key, before, after in (
        ("damage", old.damage, new.damage),
        ("range", old.range, new.range),
        ("attacks", old.attacks, new.attacks),
        ("cooldown", old.speed, new.speed),
    ):
        if after != before:
            change[key] = after - before
    old_bonuses, new_bonuses = _bonuses(old), _bonuses(new)
    bonuses = {
        attribute: new_bonuses.get(attribute, 0.0) - old_bonuses.get(attribute, 0.0)
        for attribute in sorted(old_bonuses.keys() | new_bonuses.keys())
        if new_bonuses.get(attribute) != old_bonuses.get(attribute)
    }
    if bonuses:
        change["bonuses"] = bonuses
    return change


def _bonuses(weapon: data_pb2.Weapon) -> dict[str, float]:
    return {data_pb2.Attribute.Name(bonus.attribute): bonus.bonus for bonus in weapon.damage_bonus}


type _Weapon = dict[str, Any]
type _Values = tuple[float, float, list[_Weapon]]
"""A row's armor, speed and weapons, each weapon keyed as a change is."""


def _values(row: data_pb2.UnitTypeData) -> _Values:
    """The parts of `row` that upgrades change."""
    weapons = [
        {
            "damage": weapon.damage,
            "range": weapon.range,
            "attacks": weapon.attacks,
            "cooldown": weapon.speed,
            "bonuses": _bonuses(weapon),
        }
        for weapon in row.weapons
    ]
    return row.armor, row.movement_speed, weapons


def _added(values: _Values, change: Change) -> _Values:
    """`values` with `change` added."""
    armor, speed, weapons = values
    added: list[_Weapon] = []
    weapon_changes = cast("list[_Weapon]", change.get("weapons", [{} for _ in weapons]))
    for weapon, weapon_change in zip(weapons, weapon_changes, strict=True):
        bonuses = dict(weapon["bonuses"])
        for attribute, bonus in weapon_change.get("bonuses", {}).items():
            bonuses[attribute] = bonuses.get(attribute, 0.0) + bonus
        added.append(
            {key: weapon[key] + weapon_change.get(key, 0) for key in ("damage", "range", "attacks", "cooldown")}
            | {"bonuses": bonuses}
        )
    return armor + cast("float", change.get("armor", 0.0)), speed + cast("float", change.get("speed", 0.0)), added


def _rounded(values: _Values) -> object:
    """`values` rounded to what the game's 32-bit floats can tell apart, with zero bonuses dropped."""
    armor, speed, weapons = values
    return (
        round(armor, 3),
        round(speed, 3),
        [
            {key: round(weapon[key], 3) for key in ("damage", "range", "attacks", "cooldown")}
            | {"bonuses": {attribute: round(bonus, 3) for attribute, bonus in weapon["bonuses"].items() if bonus}}
            for weapon in weapons
        ],
    )


def _unit_name(unit_type: int) -> str:
    try:
        return RawUnitTypeId(unit_type).name
    except ValueError:
        return str(unit_type)


def sweep(race: Race, installation: Installation, findings: Findings) -> None:
    """Read everything as `race` into `findings`, trying a second game if the computer ends the first."""
    for attempt in (1, 2):
        research, unexplained = len(findings.research), len(findings.unexplained)
        try:
            with playing(race, installation) as game:
                run = UpgradeSweep(game, race, findings)
                run.set_up()
                run.sweep_upgrades()
        except GameEndedError:
            if attempt == 2:
                raise
            del findings.research[research:], findings.unexplained[unexplained:]
            logger.warning("The game ended as {}; trying again", race)
        else:
            findings.races.add(race.name.lower())
            return


def main(argv: Sequence[str]) -> None:
    """Sweep the races named, or all three, and write the findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("races", nargs="*", help="terran, protoss or zerg; all three when none are named")
    parser.add_argument("--out", type=Path, default=OUT, help="where to write the findings")
    arguments = parser.parse_args(argv)
    playable = {race.name.lower(): race for race in (Race.TERRAN, Race.PROTOSS, Race.ZERG)}
    if unknown := set(arguments.races) - set(playable):
        raise SystemExit(f"No race is called {', '.join(sorted(unknown))}")
    races = [playable[name] for name in arguments.races] or list(playable.values())
    installation = Installation.find()
    findings = Findings()
    for race in races:
        sweep(race, installation, findings)
    arguments.out.write_text(json.dumps(findings.as_json(), indent=1) + "\n", encoding="utf-8")
    if findings.unexplained:
        logger.warning("{} findings the changes do not account for", len(findings.unexplained))
    logger.info("Wrote the findings to {}", arguments.out)


if __name__ == "__main__":
    main(sys.argv[1:])
