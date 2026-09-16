"""A set that hands out what it holds as a `frozenset`."""

import pytest

from sc2nachos.util import SnapshotSet


def _set(*members: str) -> SnapshotSet[str]:
    return SnapshotSet(members)


class TestSnapshotSet:
    def test_it_holds_what_it_was_made_of_and_what_is_added_and_discarded(self) -> None:
        members = _set("a", "b")
        members.add("c")
        members.discard("a")
        assert set(members) == {"b", "c"}
        assert "b" in members and "a" not in members
        assert len(members) == 2
        assert repr(members) == "SnapshotSet({b, c})"

    def test_an_empty_one_holds_nothing(self) -> None:
        assert not SnapshotSet[str]()

    def test_the_snapshot_is_what_it_holds_and_does_not_change_when_it_does(self) -> None:
        members = _set("a")
        snapshot = members.snapshot
        members.add("b")
        assert snapshot == {"a"}
        assert members.snapshot == {"a", "b"}

    def test_replacing_holds_the_members_given_and_nothing_else(self) -> None:
        members = _set("a", "b")
        members.replace(["c"])
        assert set(members) == {"c"}

    def test_what_the_set_operators_make_is_a_plain_set_and_leaves_it_as_it_was(self) -> None:
        members = _set("a", "b")
        made = [members | {"c"}, members - {"a"}, members & {"a"}, members ^ {"a", "c"}, {"c"} | members]
        assert made == [{"a", "b", "c"}, {"b"}, {"a"}, {"b", "c"}, {"a", "b", "c"}]
        assert all(type(result) is frozenset for result in made)
        assert set(members) == {"a", "b"}

    def test_the_in_place_set_operators_change_what_it_holds(self) -> None:
        members = _set("a", "b")
        members |= {"c"}
        members &= {"a", "c"}
        assert set(members) == {"a", "c"}
        members ^= {"a", "d"}
        assert set(members) == {"c", "d"}
        members -= {"c"}
        assert set(members) == {"d"}

    def test_it_cannot_be_used_as_a_key_since_it_changes(self) -> None:
        """The snapshot is what a key holds, as the unit tracker's does."""
        with pytest.raises(TypeError, match="unhashable"):
            {_set("a"): 1}  # type: ignore[misc]  # noqa: B018
