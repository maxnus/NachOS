"""Routes returned by a pathfinder."""

import math

import pytest

from sc2nachos.geometry import Point, Tile, TilePath, TileSet


def diagonal() -> TilePath:
    """A three-step diagonal walk, with the distance a pathfinder would give it."""
    return TilePath([Tile(0, 0), Tile(1, 1), Tile(2, 2), Tile(3, 3)], 3 * math.sqrt(2))


class TestConstruction:
    def test_it_keeps_the_tiles_and_the_distance_it_was_given(self) -> None:
        path = diagonal()
        assert list(path) == [Tile(0, 0), Tile(1, 1), Tile(2, 2), Tile(3, 3)]
        assert path.distance == pytest.approx(3 * math.sqrt(2))

    def test_a_single_tile_is_a_path_going_nowhere(self) -> None:
        path = TilePath([Tile(5, 5)])
        assert len(path) == 1
        assert path.start == path.end == Tile(5, 5)
        assert path.distance == 0.0

    def test_an_empty_walk_is_a_path_covering_nothing(self) -> None:
        empty = TilePath([])
        assert len(empty) == 0
        assert not empty
        assert empty.distance == 0.0

    def test_an_empty_path_leaves_from_nowhere(self) -> None:
        with pytest.raises(ValueError, match="no start"):
            _ = TilePath([]).start
        with pytest.raises(ValueError, match="no end"):
            _ = TilePath([]).end

    def test_an_unreachable_route_is_not_a_path(self) -> None:
        with pytest.raises(ValueError, match="cannot be inf"):
            TilePath([Tile(0, 0)], float("inf"))

    def test_a_negative_distance_is_not_a_path(self) -> None:
        with pytest.raises(ValueError, match="cannot be -1"):
            TilePath([Tile(0, 0)], -1.0)


class TestDistance:
    def test_it_is_measured_when_none_was_given(self) -> None:
        assert TilePath([Tile(0, 0), Tile(1, 0), Tile(1, 1)]).distance == pytest.approx(2.0)

    def test_it_spans_the_gap_between_tiles_that_do_not_touch(self) -> None:
        assert TilePath([Tile(0, 0), Tile(10, 0)]).distance == pytest.approx(10.0)

    def test_measuring_happens_once(self) -> None:
        path = TilePath([Tile(0, 0), Tile(3, 4)])
        assert path.distance == pytest.approx(5.0)
        assert path.distance is path.distance

    def test_a_given_distance_is_kept_over_a_measured_one(self) -> None:
        # A pathfinder's distance is trusted even when it exceeds the straight line.
        assert TilePath([Tile(0, 0), Tile(1, 0)], 7.0).distance == 7.0


class TestSequence:
    def test_it_indexes_from_either_end(self) -> None:
        path = diagonal()
        assert path[0] == Tile(0, 0)
        assert path[-1] == Tile(3, 3)

    def test_the_ends_are_the_first_and_last_tiles(self) -> None:
        path = diagonal()
        assert path.start == path[0]
        assert path.end == path[-1]

    def test_a_slice_is_a_path_over_that_stretch(self) -> None:
        stretch = diagonal()[1:3]
        assert isinstance(stretch, TilePath)
        assert list(stretch) == [Tile(1, 1), Tile(2, 2)]

    def test_a_slice_is_priced_on_its_own(self) -> None:
        assert diagonal()[1:3].distance == pytest.approx(math.sqrt(2))

    def test_a_slice_that_selects_nothing_is_an_empty_path(self) -> None:
        assert diagonal()[2:2] == TilePath([])

    def test_its_tiles_make_a_tile_set_for_reading_a_grid(self) -> None:
        assert TileSet(diagonal()) == TileSet([Tile(0, 0), Tile(1, 1), Tile(2, 2), Tile(3, 3)])


class TestCoverage:
    def test_it_covers_the_tiles_it_walks(self) -> None:
        assert Tile(2, 2) in diagonal()
        assert Tile(2, 3) not in diagonal()

    def test_a_position_is_on_the_walk_when_its_tile_is(self) -> None:
        assert Point((2.5, 2.5)) in diagonal()
        assert Point((2.99, 2.01)) in diagonal()
        assert Point((2.5, 3.5)) not in diagonal()

    def test_a_coordinate_pair_reads_as_a_position(self) -> None:
        # (3.0, 3.0) is a corner of Tile(3, 3), the walk's last tile.
        assert (3.0, 3.0) in diagonal()
        assert (4.0, 4.0) not in diagonal()

    def test_an_empty_path_covers_nothing(self) -> None:
        assert Tile(0, 0) not in TilePath([])

    def test_anything_that_is_not_a_position_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="expected a point"):
            "somewhere" in diagonal()  # type: ignore[operator]  # noqa: B015


class TestValue:
    def test_paths_along_the_same_walk_are_equal(self) -> None:
        assert diagonal() == diagonal()
        assert hash(diagonal()) == hash(diagonal())

    def test_a_measured_path_equals_the_same_walk_priced_by_a_pathfinder(self) -> None:
        # The two distances differ in the last bits, so equality cannot rest on them.
        assert TilePath(list(diagonal())) == diagonal()

    def test_different_walks_are_different_paths(self) -> None:
        assert diagonal() != TilePath([Tile(0, 0), Tile(1, 1)])

    def test_it_reads_as_its_ends_and_its_distance(self) -> None:
        assert repr(diagonal()) == "TilePath(Tile(0, 0) to Tile(3, 3), 4 tiles, 4.24 long)"
        assert repr(TilePath([])) == "TilePath(empty)"
