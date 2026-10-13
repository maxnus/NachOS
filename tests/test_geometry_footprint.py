"""The tiles a structure covers around its center."""

import numpy
import pytest

from sc2nachos.geometry import Footprint, Grid, Point, Tile

_DIAGONAL = Footprint.from_rows(
    "..##",
    ".###",
    "###.",
    "##..",
)
_CUT_CORNERS = Footprint.from_rows(
    "..##..",
    ".####.",
    "######",
    "######",
    ".####.",
    "..##..",
)
_SHAPES = [
    Footprint.square(1),
    Footprint.square(2),
    Footprint.square(3),
    Footprint.square(5),
    Footprint.from_rows("##"),
    Footprint.from_rows("#", "#"),
    _DIAGONAL,
    _CUT_CORNERS,
]


def _ground(*rows: str) -> Grid[bool]:
    """A grid drawn top row first, `#` true, its lower left tile at (10, 20)."""
    values = numpy.array([[row[x] == "#" for row in reversed(rows)] for x in range(len(rows[0]))])
    return Grid(values, origin=Tile(10, 20))


def _patchy_ground() -> Grid[bool]:
    """Ground with gaps in it, so a footprint fits at some centers and not at others."""
    generator = numpy.random.default_rng(7)
    return Grid(generator.random((12, 10)) > 0.15, origin=Tile(10, 20), outside=False)


class TestConstruction:
    def test_rows_are_drawn_top_first(self) -> None:
        footprint = Footprint.from_rows("#.", "##")
        assert (footprint.width, footprint.height) == (2, 2)
        assert footprint.mask.tolist() == [[True, True], [True, False]]

    def test_rows_must_be_as_long(self) -> None:
        with pytest.raises(ValueError, match="as long"):
            Footprint.from_rows("##", "#")

    def test_a_mask_must_be_a_plane(self) -> None:
        with pytest.raises(ValueError, match="two-dimensional"):
            Footprint(numpy.ones(3, dtype=bool))

    def test_the_mask_is_read_only(self) -> None:
        with pytest.raises(ValueError, match="read-only"):
            Footprint.square(2).mask[0, 0] = False

    def test_equal_masks_are_equal_footprints(self) -> None:
        assert Footprint.from_rows("##", "##") == Footprint.square(2)
        assert hash(Footprint.from_rows("##", "##")) == hash(Footprint.square(2))
        assert Footprint.square(2) != Footprint.from_rows("##")

    def test_repr_draws_the_rows(self) -> None:
        assert repr(_DIAGONAL) == "Footprint.from_rows('..##', '.###', '###.', '##..')"


class TestCentering:
    @pytest.mark.parametrize(
        ("footprint", "center", "lower_left"),
        [
            (Footprint.square(3), Point((10.5, 20.5)), Tile(9, 19)),
            (Footprint.square(2), Point((10.0, 20.0)), Tile(9, 19)),
            (Footprint.square(5), Point((10.5, 20.5)), Tile(8, 18)),
            (Footprint.square(1), Point((10.5, 20.5)), Tile(10, 20)),
            # A mineral field: two tiles across, one up, standing on the edge between them.
            (Footprint.from_rows("##"), Point((10.0, 20.5)), Tile(9, 20)),
        ],
    )
    def test_a_snapped_center(self, footprint: Footprint, center: Point, lower_left: Tile) -> None:
        assert footprint.lower_left_tile(center) == lower_left

    def test_an_odd_side_centers_on_the_tile_holding_the_point(self) -> None:
        assert Footprint.square(3).lower_left_tile(Point((10.9, 20.1))) == Tile(9, 19)

    def test_an_even_side_centers_on_the_nearest_corner(self) -> None:
        assert Footprint.square(2).lower_left_tile(Point((10.3, 20.7))) == Tile(9, 20)

    @pytest.mark.parametrize("footprint", _SHAPES, ids=repr)
    def test_center_in_a_tile_is_where_the_footprint_centers(self, footprint: Footprint) -> None:
        center = footprint.center_in(Tile(10, 20))
        assert Tile.containing(center) == Tile(10, 20)
        assert footprint.lower_left_tile(center) == Tile(10 - footprint.width // 2, 20 - footprint.height // 2)


class TestFitting:
    @pytest.mark.parametrize("footprint", _SHAPES, ids=repr)
    def test_centers_fitting_agrees_with_fits_at_every_tile(self, footprint: Footprint) -> None:
        ground = _patchy_ground()
        fitting = footprint.centers_fitting(ground)
        for x in range(ground.width):
            for y in range(ground.height):
                tile = ground.tile_at((x, y))
                assert fitting[tile] == footprint.fits(ground, at=footprint.center_in(tile)), tile

    def test_a_masked_out_tile_may_be_blocked(self) -> None:
        ground = _ground(
            ".##.",
            "####",
            "####",
            ".##.",
        )
        assert not Footprint.square(4).fits(ground, at=Point((12.0, 22.0)))
        assert Footprint.from_rows(".##.", "####", "####", ".##.").fits(ground, at=Point((12.0, 22.0)))

    def test_beyond_the_grid_reads_outside(self) -> None:
        open_ground = _ground("###", "###", "###")
        assert not Footprint.square(3).fits(open_ground.with_outside(False), at=Point((10.5, 20.5)))
        assert Footprint.square(3).fits(open_ground.with_outside(True), at=Point((10.5, 20.5)))

    def test_beyond_a_grid_with_no_outside_raises(self) -> None:
        with pytest.raises(IndexError, match="reaches past"):
            Footprint.square(3).fits(_ground("###", "###", "###"), at=Point((10.5, 20.5)))

    def test_centers_fitting_reads_false_beyond_a_grid_with_no_outside(self) -> None:
        fitting = Footprint.square(3).centers_fitting(_ground("###", "###", "###"))
        assert fitting.values.tolist() == [[False] * 3, [False, True, False], [False] * 3]
        assert fitting.outside is False

    def test_covering_nothing_fits_everywhere(self) -> None:
        empty = Footprint.from_rows()
        ground = _ground("...", "...")
        assert empty.fits(ground, at=Point((11.5, 20.5)))
        assert empty.centers_fitting(ground).all()


class TestFill:
    def test_fills_the_tiles_covered(self) -> None:
        ground = _ground("....", "....", "....", "....")
        _DIAGONAL.fill(ground, at=Point((12.0, 22.0)), value=True)
        assert ground.equals(_ground("..##", ".###", "###.", "##.."))

    def test_skips_the_tiles_beyond_the_grid(self) -> None:
        ground = _ground("###", "###", "###")
        Footprint.square(3).fill(ground, at=Point((10.5, 20.5)), value=False)
        assert ground.equals(_ground("###", "..#", "..#"))

    def test_reaching_nowhere_on_the_grid_changes_nothing(self) -> None:
        ground = _ground("###", "###")
        Footprint.square(2).fill(ground, at=Point((50.0, 50.0)), value=False)
        assert ground.all()

    def test_a_read_only_grid_refuses(self) -> None:
        ground = Grid(numpy.ones((3, 3), dtype=bool), readonly=True)
        with pytest.raises(TypeError, match="read-only"):
            Footprint.square(2).fill(ground, at=Point((1.0, 1.0)), value=False)
