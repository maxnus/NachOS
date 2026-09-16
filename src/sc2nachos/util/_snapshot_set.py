"""A set that can be handed out as an immutable value as often as it is read."""

from __future__ import annotations

from collections.abc import MutableSet
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


@final
class SnapshotSet[T](MutableSet[T]):
    """A set kept as a `frozenset` and swapped whole on every change, so `snapshot` costs nothing to read.

    Changing it is a little dearer than changing a `set`, and reading it as a value is free, which is what something
    read many times a step and used as part of a key wants. It is unhashable, as every mutable set is: `snapshot` is
    what a key holds, since a key that changed with the set would lose what was filed under it.

    What `|`, `-`, `&` and `^` make of one is a `frozenset`; the in-place `|=`, `-=`, `&=` and `^=` change it.
    """

    __slots__ = ("_members",)

    def __init__(self, members: Iterable[T] = ()) -> None:
        self._members: frozenset[T] = frozenset(members)

    @classmethod
    def _from_iterable[S](cls, members: Iterable[S]) -> frozenset[S]:
        """What the set operators make, which `collections.abc.Set` asks this for."""
        return frozenset(members)

    @property
    def snapshot(self) -> frozenset[T]:
        """What it holds now, which does not change when it does."""
        return self._members

    def replace(self, members: Iterable[T]) -> None:
        """Hold `members` from now on, and nothing else."""
        self._members = frozenset(members)

    def add(self, value: T) -> None:
        self._members |= {value}

    def discard(self, value: T) -> None:
        self._members -= {value}

    def __contains__(self, member: object) -> bool:
        return member in self._members

    def __iter__(self) -> Iterator[T]:
        return iter(self._members)

    def __len__(self) -> int:
        return len(self._members)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({{{', '.join(sorted(str(member) for member in self._members))}}})"
