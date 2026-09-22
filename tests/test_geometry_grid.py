"""Grids of per-tile values."""

import numpy
import pytest

from sc2nachos.geometry import Circle, Grid, Point, Rectangle, Tile, TileSet


def playable_grid() -> Grid[float]:
    """A grid over part of the map, as gamemap builds one over the playable area."""
    return Grid(numpy.arange(10 * 8, dtype=float).reshape(10, 8), origin=Tile(4, 6))


class TestConstruction:
    def test_rejects_data_that_is_not_a_plane(self) -> None:
        with pytest.raises(ValueError, match="two-dimensional"):
            Grid(numpy.zeros((3, 3, 3)))

    def test_zeros(self) -> None:
        grid = Grid.zeros(3, 4, origin=Tile(1, 2), dtype=bool)
        assert (grid.width, grid.height) == (3, 4)
        assert grid.origin == Tile(1, 2)
        assert not grid.values.any()

    def test_like_copies_the_geometry_but_not_the_values(self) -> None:
        grid = playable_grid()
        empty = Grid.like(grid, dtype=int)
        assert empty.origin == grid.origin
        assert empty.values.shape == grid.values.shape
        assert not empty.values.any()

    def test_bounds_are_the_ground_covered(self) -> None:
        assert playable_grid().bounds == Rectangle(4, 6, 10, 8)

    def test_values_are_read_only(self) -> None:
        with pytest.raises(ValueError, match="read-only"):
            playable_grid().values[0, 0] = 1.0


class TestAddressing:
    def test_a_point_a_tile_and_a_tuple_reach_the_same_value(self) -> None:
        grid = playable_grid()
        assert grid[Point((5.7, 7.3))] == grid[Tile(5, 7)] == grid[(5.7, 7.3)]

    def test_the_origin_is_the_first_entry(self) -> None:
        grid = playable_grid()
        assert grid[grid.origin] == grid.values[0, 0]

    def test_a_point_off_the_grid_raises_rather_than_wrapping(self) -> None:
        """The old point_to_indices used int(), so a point left of the grid read the far side of the map."""
        grid = playable_grid()
        for point in ((2.0, 7.0), (99.0, 7.0), (5.0, 2.0), (5.0, 99.0)):
            with pytest.raises(IndexError, match="lies outside"):
                _ = grid[point]

    def test_indexing_a_point_gives_a_python_scalar(self) -> None:
        """numpy scalars subclass neither int nor bool, so they leak into callers that check types."""
        for dtype in (bool, int, numpy.int32, float, numpy.float32):
            value = Grid(numpy.ones((3, 3), dtype=dtype))[Tile(1, 1)]
            assert type(value).__module__ == "builtins", (dtype, type(value))

    def test_a_point_just_below_the_origin_is_not_the_last_row(self) -> None:
        grid = playable_grid()
        with pytest.raises(IndexError):
            _ = grid[(3.5, 7.0)]


class TestOutside:
    def test_a_point_off_the_grid_raises_when_nothing_was_declared(self) -> None:
        with pytest.raises(IndexError, match="lies outside"):
            _ = playable_grid()[Point((100.5, 100.5))]

    def test_a_declared_value_answers_for_ground_the_grid_does_not_cover(self) -> None:
        grid = Grid(numpy.ones((4, 4)), origin=Tile(10, 10), outside=0.0)
        assert grid[Point((11.5, 11.5))] == 1.0
        assert grid[Point((100.5, 100.5))] == 0.0
        assert grid[Tile(-5, -5)] == 0.0

    def test_a_false_value_still_answers(self) -> None:
        # `outside` is absent only when None, so False and 0 are answers like any other.
        grid = Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(10, 10), outside=False)
        assert grid[Point((100.5, 100.5))] is False

    def test_writing_off_the_grid_always_raises(self) -> None:
        grid = Grid(numpy.ones((4, 4)), origin=Tile(10, 10), outside=0.0)
        with pytest.raises(IndexError, match="lies outside"):
            grid[Point((100.5, 100.5))] = 5.0

    def test_index_of_still_raises_since_no_index_lies_outside(self) -> None:
        grid = Grid(numpy.ones((4, 4)), origin=Tile(10, 10), outside=0.0)
        with pytest.raises(IndexError, match="lies outside"):
            grid.index_of(Point((100.5, 100.5)))

    def test_with_outside_shares_the_data(self) -> None:
        grid = Grid(numpy.ones((4, 4)), origin=Tile(10, 10))
        lenient = grid.with_outside(0.0)
        assert lenient[Point((100.5, 100.5))] == 0.0
        assert grid.outside is None
        grid[Tile(10, 10)] = 9.0
        assert lenient[Tile(10, 10)] == 9.0

    def test_with_outside_none_takes_it_away(self) -> None:
        grid = Grid(numpy.ones((4, 4)), origin=Tile(10, 10), outside=0.0)
        with pytest.raises(IndexError, match="lies outside"):
            _ = grid.with_outside(None)[Point((100.5, 100.5))]


class TestOutsidePropagation:
    def grids(self) -> tuple[Grid[float], Grid[float]]:
        return (
            Grid(numpy.ones((4, 4)), origin=Tile(0, 0), outside=2.0),
            Grid(numpy.ones((4, 4)), origin=Tile(0, 0), outside=3.0),
        )

    def test_arithmetic_runs_the_same_operation_on_the_outside_value(self) -> None:
        left, right = self.grids()
        assert (left + right).outside == 5.0
        assert (left - right).outside == -1.0
        assert (left * right).outside == 6.0

    def test_a_scalar_operand_is_used_as_it_stands(self) -> None:
        left, _ = self.grids()
        assert (left + 10).outside == 12.0
        assert (10 - left).outside == 8.0

    def test_a_comparison_carries_the_answer_not_the_value(self) -> None:
        left, _ = self.grids()
        assert (left <= 5).outside is True
        assert (left > 5).outside is False

    def test_an_operand_without_one_leaves_the_result_without_one(self) -> None:
        left, _ = self.grids()
        bare = Grid(numpy.ones((4, 4)), origin=Tile(0, 0))
        assert (left + bare).outside is None
        assert (bare + left).outside is None

    def test_negation_and_inversion_follow(self) -> None:
        left, _ = self.grids()
        assert (-left).outside == -2.0
        assert abs(-left).outside == 2.0
        assert (~Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(0, 0), outside=False)).outside is True

    def test_cropping_keeps_it_over_less_ground(self) -> None:
        left, _ = self.grids()
        assert left.cropped(Rectangle(1, 1, 2, 2)).outside == 2.0

    def test_where_lets_the_condition_choose_off_the_grid_too(self) -> None:
        left, right = self.grids()
        always = Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(0, 0), outside=True)
        never = always.with_outside(False)
        assert left.where(always, right).outside == 2.0
        assert left.where(never, right).outside == 3.0
        assert left.where(never, 9.0).outside == 9.0

    def test_where_drops_it_when_the_condition_has_none(self) -> None:
        left, right = self.grids()
        undeclared = Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(0, 0))
        assert left.where(undeclared, right).outside is None

    def test_where_ignores_the_operand_the_condition_does_not_pick(self) -> None:
        _, right = self.grids()
        bare = Grid(numpy.ones((4, 4)), origin=Tile(0, 0))
        never = Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(0, 0), outside=False)
        always = never.with_outside(True)
        assert bare.where(never, right).outside == 3.0
        assert bare.where(never, 9.0).outside == 9.0
        assert bare.where(always, right).outside is None

    def test_the_operations_that_cannot_carry_one_take_one(self) -> None:
        pathable = Grid(numpy.ones((4, 4), dtype=bool), origin=Tile(0, 0), outside=False)
        assert pathable.distance_transform().outside is None
        assert pathable.distance_transform(outside=0.0).outside == 0.0
        left, _ = self.grids()
        assert left.smoothed(1.0).outside is None
        assert left.smoothed(1.0, outside=0.0).outside == 0.0
        assert left.distance_from(Point((1.5, 1.5))).outside is None
        assert left.distance_from(Point((1.5, 1.5)), outside=0.0).outside == 0.0

    def test_copy_keeps_it(self) -> None:
        left, _ = self.grids()
        assert left.copy().outside == 2.0


class TestArrayIndices:
    """`values` goes to code that uses array indices — pathfinding, scipy — so both conversions are needed."""

    def test_index_of_is_relative_to_the_origin(self) -> None:
        grid = playable_grid()
        assert grid.index_of(grid.origin) == (0, 0)
        assert grid.index_of(Point((5.7, 7.3))) == (1, 1)
        assert grid.values[grid.index_of(Point((5.7, 7.3)))] == grid[Point((5.7, 7.3))]

    def test_tile_at_inverts_index_of(self) -> None:
        grid = playable_grid()
        for point in (Point((4.0, 6.0)), Point((5.7, 7.3)), Point((13.9, 13.9))):
            assert grid.tile_at(grid.index_of(point)) == Tile.containing(point)

    def test_a_point_off_the_grid_raises_rather_than_going_negative(self) -> None:
        """An index the caller passes on is not bounds-checked again: pathfinding returns inf for every tile."""
        grid = playable_grid()
        with pytest.raises(IndexError, match="lies outside"):
            _ = grid.index_of(Point((1.0, 7.0)))

    def test_an_index_off_the_array_raises(self) -> None:
        grid = playable_grid()
        for index in ((-1, 0), (10, 0), (0, 8)):
            with pytest.raises(IndexError, match="lies outside"):
                _ = grid.tile_at(index)


class TestAreaAccess:
    def test_a_rectangle_reads_a_plane_and_an_area_reads_a_line(self) -> None:
        """A circle is not rectangular, so its values cannot come back laid out in a grid."""
        grid = playable_grid()
        assert grid[Rectangle(5, 7, 3, 2)].shape == (3, 2)
        assert grid[Circle((8, 9), 2)].ndim == 1

    def test_a_rectangle_reads_the_tiles_it_covers(self) -> None:
        grid = playable_grid()
        assert numpy.array_equal(grid[Rectangle(4, 6, 2, 2)], grid.values[0:2, 0:2])

    def test_reading_past_the_edge_clips(self) -> None:
        grid = playable_grid()
        assert grid[Rectangle(0, 0, 8, 10)].shape == (4, 4)
        assert grid[Rectangle(50, 50, 4, 4)].size == 0

    def test_writing_past_the_edge_clips(self) -> None:
        """An effect near the map edge is ordinary, so a circle hanging off it must not raise."""
        grid = Grid.zeros(10, 10, origin=Tile(4, 4))
        circle = Circle((5, 5), 4)
        grid[circle] = 1.0
        assert 0 < grid.sum() < len(circle.tiles())

    def test_writing_entirely_outside_changes_nothing(self) -> None:
        grid = Grid.zeros(10, 10, origin=Tile(4, 4))
        grid[Rectangle(50, 50, 4, 4)] = 1.0
        grid[Circle((50, 50), 3)] = 1.0
        assert not grid.values.any()

    def test_an_empty_area_reads_and_writes_nothing(self) -> None:
        grid = Grid.zeros(10, 10)
        nothing = Circle((5.9, 5.9), 0.4)
        assert not nothing.tiles()
        assert grid[nothing].size == 0
        grid[nothing] = 1.0
        assert not grid.values.any()

    def test_a_tile_set_writes_exactly_its_tiles(self) -> None:
        grid = Grid.zeros(10, 10, dtype=bool)
        tiles = TileSet([Tile(1, 1), Tile(3, 4), Tile(9, 9)])
        grid[tiles] = True
        assert grid.sum() == 3
        assert all(grid[tile] for tile in tiles)


class TestOperators:
    def test_scalar_arithmetic_both_ways(self) -> None:
        grid = Grid.zeros(3, 3) + 2.0
        assert (grid * 3).max() == 6.0
        assert (3 * grid).max() == 6.0
        assert (10 - grid).max() == 8.0
        assert (grid - 1).max() == 1.0
        assert (-grid).min() == -2.0

    def test_grids_combine_tilewise(self) -> None:
        a, b = Grid.zeros(2, 2) + 1.0, Grid.zeros(2, 2) + 4.0
        assert (a + b).min() == 5.0
        assert a.maximum(b).min() == 4.0
        assert a.minimum(b).max() == 1.0

    def test_logical_composition(self) -> None:
        """placement & ~creep, the pattern gamemap uses to find buildable ground."""
        placement = Grid.zeros(4, 4, dtype=bool)
        placement[Rectangle(0, 0, 4, 4)] = True
        creep = Grid.like(placement, dtype=bool)
        creep[Rectangle(0, 0, 2, 4)] = True
        assert (placement & ~creep).sum() == 8

    def test_the_logical_operators_are_boolean(self) -> None:
        """`~` was bitwise while `&` and `|` were logical, so De Morgan failed away from bool."""
        grid = Grid(numpy.array([[True, False], [False, True]]))
        assert (~(grid & grid)).values.dtype == bool
        assert (~(grid & grid)).equals(~grid | ~grid)

    def test_numpy_scalars_are_operands(self) -> None:
        """A grid hands them out itself, and without this they round-trip through numpy and back."""
        grid = Grid(numpy.ones((3, 3)))
        for scalar in (numpy.int32(3), numpy.int64(3), numpy.float32(3), numpy.float64(3)):
            assert (grid + scalar).min() == 4.0, scalar
            assert (grid < scalar).all(), scalar

    def test_grids_over_different_tiles_refuse_to_combine(self) -> None:
        with pytest.raises(ValueError, match="same tiles"):
            _ = Grid.zeros(2, 2) + Grid.zeros(3, 3)
        with pytest.raises(ValueError, match="same tiles"):
            _ = Grid.zeros(2, 2) + Grid.zeros(2, 2, origin=Tile(1, 1))

    def test_every_operation_taking_a_second_grid_checks_the_tiles(self) -> None:
        """`where` and mask indexing reach a second grid too, and must check it like the operators do."""
        grid = Grid.zeros(3, 3)
        elsewhere = Grid.zeros(3, 3, origin=Tile(50, 50))
        far_mask = Grid.zeros(3, 3, origin=Tile(50, 50), dtype=bool)
        here_mask = Grid.zeros(3, 3, dtype=bool)
        for operation in (
            lambda: grid + elsewhere,
            lambda: grid.minimum(elsewhere),
            lambda: grid.where(far_mask, 0.0),
            lambda: grid.where(here_mask, elsewhere),
            lambda: grid[far_mask],
            lambda: grid.__setitem__(far_mask, 0.0),
        ):
            with pytest.raises(ValueError, match="same tiles"):
                operation()

    def test_an_unsupported_operand_is_refused(self) -> None:
        with pytest.raises(TypeError):
            _ = Grid.zeros(2, 2) + "two"


class TestComparisons:
    def test_equality_answers_per_tile(self) -> None:
        """`grid == 2` must be a mask like `grid > 2`, or `pathing & (threat == 0)` is quietly empty."""
        grid = playable_grid()
        assert isinstance(grid == 40, Grid)
        assert (grid == 40).sum() == 1
        assert (grid != 40).sum() == grid.values.size - 1

    def test_equals_compares_grids_as_wholes(self) -> None:
        a, b = Grid.zeros(3, 3), Grid.zeros(3, 3)
        assert a.equals(b)
        b[Tile(1, 1)] = 1.0
        assert not a.equals(b)
        assert not a.equals("not a grid")

    def test_equals_takes_the_origin_into_account(self) -> None:
        assert not Grid.zeros(2, 2).equals(Grid.zeros(2, 2, origin=Tile(5, 5)))

    def test_grids_over_different_tiles_refuse_to_compare(self) -> None:
        """`equals` answers that question; `==` is tilewise, so there is nothing to line up."""
        with pytest.raises(ValueError, match="same tiles"):
            _ = Grid.zeros(2, 2) == Grid.zeros(3, 3)

    def test_ordering_answers_per_tile(self) -> None:
        """Grids have no total order, so this is the only reading `<` can have."""
        grid = playable_grid()
        assert isinstance(grid > 40, Grid)
        assert (grid > 40).sum() == (grid.values > 40).sum()

    def test_a_grid_has_no_truth_value(self) -> None:
        """The one mistake tilewise comparison invites, so it raises instead of reading as true."""
        with pytest.raises(TypeError, match="no truth value"):
            bool(Grid.zeros(2, 2) > 1)
        with pytest.raises(TypeError, match="no truth value"):
            _ = Grid.zeros(2, 2) == Grid.zeros(2, 2) and True

    def test_a_grid_is_not_hashable(self) -> None:
        """It is mutable, so it must not be usable as a key."""
        with pytest.raises(TypeError, match="unhashable"):
            hash(Grid.zeros(2, 2))

    def test_any_and_all(self) -> None:
        grid = Grid.zeros(3, 3, dtype=bool)
        assert not grid.any()
        grid[Tile(0, 0)] = True
        assert grid.any()
        assert not grid.all()


class TestDerivedGrids:
    def test_cropped_keeps_the_tiles_it_covers(self) -> None:
        grid = playable_grid()
        part = grid.cropped(Rectangle(6, 8, 3, 2))
        assert part.origin == Tile(6, 8)
        assert (part.width, part.height) == (3, 2)
        assert part[Tile(6, 8)] == grid[Tile(6, 8)]

    def test_cropped_clips_to_the_grid(self) -> None:
        grid = playable_grid()
        part = grid.cropped(Rectangle(0, 0, 8, 10))
        assert part.origin == grid.origin
        assert (part.width, part.height) == (4, 4)

    def test_cropped_is_independent(self) -> None:
        """The old get_subfield returned a view, so writing to it reached back into the parent."""
        grid = playable_grid()
        part = grid.cropped(Rectangle(6, 8, 3, 2))
        part[Tile(6, 8)] = -1.0
        assert grid[Tile(6, 8)] != -1.0

    def test_distance_transform(self) -> None:
        """openness, as gamemap builds it from the pathing grid."""
        pathable = Grid.zeros(5, 5, dtype=bool)
        pathable[Rectangle(1, 1, 3, 3)] = True
        distances = pathable.distance_transform()
        assert distances[Tile(2, 2)] == pytest.approx(2.0)
        assert distances[Tile(1, 1)] == pytest.approx(1.0)
        assert distances[Tile(0, 0)] == 0.0

    def test_smoothed_spreads_a_spike_without_moving_it(self) -> None:
        grid = Grid.zeros(9, 9)
        grid[Tile(4, 4)] = 100.0
        blurred = grid.smoothed(1.0)
        assert blurred.argmax() == Tile(4, 4)
        assert blurred[Tile(4, 4)] < 100.0
        assert blurred[Tile(3, 4)] > 0.0
        assert blurred.sum() == pytest.approx(100.0, rel=1e-3)

    def test_smoothed_within_a_region_is_not_dragged_down_by_what_is_outside(self) -> None:
        grid = Grid.zeros(9, 9)
        region = Grid.zeros(9, 9, dtype=bool)
        region[Rectangle(0, 0, 5, 9)] = True
        grid[region] = 10.0  # ten inside the region, zero outside it
        plain = grid.smoothed(2.0)
        weighted = grid.smoothed(2.0, within=region)
        edge = Tile(4, 4)  # the region's last column
        assert plain[edge] < 10.0  # pulled toward the zeros outside
        assert weighted[edge] == pytest.approx(10.0)

    def test_smoothed_refuses_a_region_over_other_tiles(self) -> None:
        with pytest.raises(ValueError, match="same tiles"):
            Grid.zeros(4, 4).smoothed(1.0, within=Grid.zeros(4, 4, origin=Tile(9, 9), dtype=bool))


class TestSummaries:
    def test_summing_a_boolean_grid_counts_its_true_tiles(self) -> None:
        grid = Grid.zeros(4, 4, dtype=bool)
        grid[Rectangle(0, 0, 3, 1)] = True
        assert grid.sum() == 3
        assert type(grid.sum()) is int

    def test_min_max_sum_are_python_scalars(self) -> None:
        grid = playable_grid()
        assert type(grid.min()) is float
        assert (grid.min(), grid.max(), grid.sum()) == (0.0, 79.0, 79.0 * 80 / 2)

    def test_argmin_and_argmax_answer_in_map_coordinates(self) -> None:
        grid = playable_grid()
        assert grid.argmin() == grid.origin
        assert grid.argmax() == Tile(13, 13)
        assert grid[grid.argmax()] == grid.max()

    def test_distance_from(self) -> None:
        grid = Grid.zeros(3, 3)
        distances = grid.distance_from((0.5, 0.5))
        assert distances[Tile(0, 0)] == 0.0
        assert distances[Tile(2, 0)] == pytest.approx(2.0)
        assert distances.origin == grid.origin

    def test_where(self) -> None:
        grid = Grid.zeros(3, 3) + 5.0
        assert grid.where(grid > 1, 0.0).min() == 5.0
        assert grid.where(grid > 10, 0.0).max() == 0.0

    def test_where_fills_from_another_grid(self) -> None:
        grid = Grid.zeros(3, 3) + 5.0
        other = Grid.zeros(3, 3) + 1.0
        mixed = grid.where(grid.distance_from((0.5, 0.5)) < 1.0, other)
        assert (mixed.min(), mixed.max()) == (1.0, 5.0)

    def test_where_refuses_a_condition_that_is_not_a_grid(self) -> None:
        with pytest.raises(TypeError, match="expected a grid"):
            Grid.zeros(3, 3).where(numpy.ones((3, 3), dtype=bool), 0.0)  # pyright: ignore[reportArgumentType]


class TestReadOnly:
    def grid(self) -> Grid[float]:
        """A grid as the library returns one, sharing its values with every reader."""
        return Grid(numpy.ones((4, 4)), readonly=True)

    def test_writing_to_one_raises_and_says_what_to_do(self) -> None:
        with pytest.raises(TypeError, match="is read-only"):
            self.grid()[Tile(0, 0)] = 2.0

    def test_filling_one_raises_too(self) -> None:
        with pytest.raises(TypeError, match="is read-only"):
            self.grid().fill(2.0)

    def test_a_copy_can_be_written_to(self) -> None:
        copy = self.grid().copy()
        copy[Tile(0, 0)] = 2.0
        assert not copy.readonly

    def test_reading_a_different_value_outside_keeps_it_read_only(self) -> None:
        """It shares the values it was built on, so it cannot come back writable."""
        assert self.grid().with_outside(0.0).readonly

    def test_what_a_grid_derives_can_be_written_to(self) -> None:
        grid = self.grid()
        derived = (grid + 1, grid.cropped(Rectangle(0, 0, 2, 2)), grid.smoothed(1.0), Grid.like(grid))
        assert not any(one.readonly for one in derived)

    def test_a_grid_is_writable_unless_it_says_otherwise(self) -> None:
        assert not Grid.zeros(2, 2).readonly


class TestMutation:
    def test_copy_is_independent(self) -> None:
        grid = playable_grid()
        clone = grid.copy()
        clone[grid.origin] = -1.0
        assert grid[grid.origin] != clone[grid.origin]

    def test_fill(self) -> None:
        grid = Grid.zeros(3, 3)
        grid.fill(7.0)
        assert grid.min() == grid.max() == 7.0

    def test_a_mask_writes_only_the_tiles_it_selects(self) -> None:
        """`grid[grid > 4] = 4.0` clamps; `where` can only say it by inverting the predicate."""
        grid = Grid(numpy.arange(9.0).reshape(3, 3))
        grid[grid > 4] = 4.0
        assert grid.max() == 4.0
        assert grid.values.tolist() == [[0.0, 1.0, 2.0], [3.0, 4.0, 4.0], [4.0, 4.0, 4.0]]

    def test_a_mask_reads_the_values_it_selects(self) -> None:
        grid = Grid(numpy.arange(9.0).reshape(3, 3))
        selected = grid[grid > 6]
        assert sorted(selected.tolist()) == [7.0, 8.0]

    def test_a_mask_must_hold_booleans(self) -> None:
        """An integer grid would be read as row indices and answer with the wrong shape."""
        grid = Grid(numpy.arange(9.0).reshape(3, 3))
        with pytest.raises(TypeError, match="must hold booleans"):
            _ = grid[Grid(numpy.ones((3, 3), dtype=int))]  # pyright: ignore[reportArgumentType]
