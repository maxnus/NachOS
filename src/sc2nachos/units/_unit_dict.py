"""A bot's own value for each unit, which forgets a unit once it is dead."""

from __future__ import annotations

from collections.abc import ItemsView, Iterable, Iterator, Mapping, MutableMapping, ValuesView
from typing import Any, final, overload

from sc2nachos.units._unit import Unit

# Marks a unit with no entry, where `None` may be an entry's value.
_NO_ENTRY: Any = object()


@final
class UnitDict[K: Unit[Any], V](MutableMapping[K, V]):
    """A value for each unit, as a dict keyed by unit holds one, that forgets a unit once it is dead: a bot's own data
    for its units, such as a role or the mineral field a worker mines.

    A unit is the same key for the whole game, however often the game changes its tag. A dead key reads as absent
    wherever it is read: a lookup drops its entry, and iterating, `len` and the views skip it, so a unit's value cannot
    be read in the turn it dies. A unit is dead once `is_dead` says so: an enemy's unit that dies out of sight stays a
    key until NachOS finds it dead, and a dead unit held as a value stays. Storing a dead unit is not refused, and no
    read sees it. A unit a neural parasite takes over changes class in place, so an `OwnUnit` key can be the enemy's
    while it lasts. In a replay played back with the fog off, `api.units` and `api.all_units` hold different objects
    for one unit, each a key of its own.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[K, V] | Iterable[tuple[K, V]] = (), /) -> None:
        """Hold `values`: a mapping, or pairs of a unit and its value."""
        self._values: dict[K, V] = {}
        self.update(values)

    def __repr__(self) -> str:
        return f"UnitDict({dict(self.items())!r})"

    def __getitem__(self, unit: K, /) -> V:
        value = self._values[unit]
        if unit._dead:
            del self._values[unit]
            raise KeyError(unit)
        return value

    def __setitem__(self, unit: K, value: V, /) -> None:
        self._values[unit] = value

    def __delitem__(self, unit: K, /) -> None:
        del self._values[unit]
        if unit._dead:
            raise KeyError(unit)

    def __contains__(self, unit: object, /) -> bool:
        values = self._values
        if unit not in values:
            return False
        # A key, so a unit; `cast` would cost a call on every lookup.
        key: K = unit  # pyright: ignore[reportAssignmentType]
        if key._dead:
            del values[key]
            return False
        return True

    def __iter__(self) -> Iterator[K]:
        self._forget_dead_units()
        return iter(self._values)

    def __len__(self) -> int:
        self._forget_dead_units()
        return len(self._values)

    def __bool__(self) -> bool:
        return any(not unit._dead for unit in self._values)

    def clear(self) -> None:
        """Forget every unit."""
        self._values.clear()

    def copy(self) -> UnitDict[K, V]:
        """A new `UnitDict` of the living units with their values."""
        self._forget_dead_units()
        copied: UnitDict[K, V] = UnitDict()
        copied._values = dict(self._values)
        return copied

    __copy__ = copy

    @overload
    def get(self, unit: K, /) -> V | None: ...

    @overload
    def get(self, unit: K, default: V, /) -> V: ...

    @overload
    def get[T](self, unit: K, default: T, /) -> V | T: ...

    def get(self, unit: K, default: Any = None, /) -> Any:
        """The value of `unit`, or `default` if it has none or is dead."""
        value = self._values.get(unit, _NO_ENTRY)
        if value is _NO_ENTRY:
            return default
        if unit._dead:
            del self._values[unit]
            return default
        return value

    def items(self) -> ItemsView[K, V]:
        """Each living unit with its value."""
        return _LivingUnitItems(self)

    def values(self) -> ValuesView[V]:
        """The value of each living unit."""
        return _LivingUnitValues(self)

    def _forget_dead_units(self) -> None:
        """Drop the entries of the units now dead."""
        values = self._values
        for unit in [unit for unit in values if unit._dead]:
            del values[unit]


class _LivingUnitItems[K: Unit[Any], V](ItemsView[K, V]):
    """The items of a `UnitDict`, read off its dict in one pass rather than a lookup a unit."""

    __slots__ = ("_unit_dict",)

    def __init__(self, unit_dict: UnitDict[K, V]) -> None:
        super().__init__(unit_dict)
        self._unit_dict = unit_dict

    def __iter__(self) -> Iterator[tuple[K, V]]:
        unit_dict = self._unit_dict
        unit_dict._forget_dead_units()
        return iter(unit_dict._values.items())


class _LivingUnitValues[V](ValuesView[V]):
    """The values of a `UnitDict`, read off its dict in one pass rather than a lookup a unit."""

    __slots__ = ("_unit_dict",)

    def __init__(self, unit_dict: UnitDict[Any, V]) -> None:
        super().__init__(unit_dict)
        self._unit_dict = unit_dict

    def __iter__(self) -> Iterator[V]:
        unit_dict = self._unit_dict
        unit_dict._forget_dead_units()
        return iter(unit_dict._values.values())
