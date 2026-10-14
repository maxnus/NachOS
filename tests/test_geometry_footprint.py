"""The tiles a structure covers around its center."""

import numpy
import pytest

from sc2nachos.geometry import Footprint, Point, Rectangle, Tile, TileSet

DIAGONAL = Footprint.from_rows(
    "..##",
    ".###",
    "###.",
    "##..",
)
SHAPES = [
    Footprint.square(1),
    Footprint.square(2),
    Footprint.square(3),
    Footprint.square(5),
    Footprint.rectangle(2, 1),
    Footprint.rectangle(1, 2),
    DIAGONAL,
    Footprint.square(6, cut_corners=2),
]


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

    def test_a_rectangle_is_as_wide_and_high_as_asked(self) -> None:
        assert Footprint.rectangle(4, 2) == Footprint.from_rows("####", "####")

    @pytest.mark.parametrize(
        ("footprint", "rows"),
        [
            # A 4x4 rock loses its corner tiles (tool `sweep_placement`).
            (Footprint.square(4, cut_corners=1), (".##.", "####", "####", ".##.")),
            # A 6x6 rock loses the corner and the tile beside it on each side.
            (Footprint.square(6, cut_corners=2), ("..##..", ".####.", "######", "######", ".####.", "..##..")),
            (Footprint.rectangle(4, 3, cut_corners=1), (".##.", "####", ".##.")),
        ],
        ids=repr,
    )
    def test_cut_corners_takes_the_tiles_that_few_steps_from_each_corner(
        self, footprint: Footprint, rows: tuple[str, ...]
    ) -> None:
        assert footprint == Footprint.from_rows(*rows)

    def test_equal_masks_are_equal_footprints(self) -> None:
        assert Footprint.from_rows("##", "##") == Footprint.square(2)
        assert hash(Footprint.from_rows("##", "##")) == hash(Footprint.square(2))
        assert Footprint.square(2) != Footprint.rectangle(2, 1)

    @pytest.mark.parametrize(
        ("footprint", "shown"),
        [
            (Footprint.square(3), "Footprint.square(3)"),
            (Footprint.from_rows("####", "####"), "Footprint.rectangle(4, 2)"),
            (Footprint.from_rows(".##.", "####", "####", ".##."), "Footprint.square(4, cut_corners=1)"),
            (DIAGONAL, "Footprint(4x4, 10 tiles)"),
            (Footprint.from_rows(), "Footprint.from_rows()"),
        ],
    )
    def test_repr_names_a_rectangle_by_its_size_and_any_other_shape_by_its_tiles(
        self, footprint: Footprint, shown: str
    ) -> None:
        assert repr(footprint) == shown


class TestCentering:
    @pytest.mark.parametrize(
        ("footprint", "center", "box"),
        [
            (Footprint.square(3), Point((10.5, 20.5)), Rectangle(9, 19, 3, 3)),
            (Footprint.square(2), Point((10.0, 20.0)), Rectangle(9, 19, 2, 2)),
            (Footprint.square(5), Point((10.5, 20.5)), Rectangle(8, 18, 5, 5)),
            (Footprint.square(1), Point((10.5, 20.5)), Rectangle(10, 20, 1, 1)),
            # A mineral field: two tiles across, one up, standing on the edge between them.
            (Footprint.rectangle(2, 1), Point((10.0, 20.5)), Rectangle(9, 20, 2, 1)),
        ],
    )
    def test_the_bounding_box_of_a_snapped_center(self, footprint: Footprint, center: Point, box: Rectangle) -> None:
        assert footprint.bounding_box(center) == box

    @pytest.mark.parametrize(
        ("footprint", "ordered_at", "stood_at"),
        [
            (Footprint.square(2), Point((86.3, 159.7)), Point((86.0, 160.0))),
            (Footprint.square(3), Point((82.3, 158.7)), Point((82.5, 158.5))),
        ],
        ids=["depot", "barracks"],
    )
    def test_it_snaps_as_the_game_snapped_a_build_ordered_off_the_grid(
        self, footprint: Footprint, ordered_at: Point, stood_at: Point
    ) -> None:
        """Where a builder's order showed its structure's site (tool `sweep_orders`)."""
        assert footprint.snapped_center(ordered_at) == stood_at

    def test_an_odd_side_centers_on_the_tile_holding_the_point(self) -> None:
        assert Footprint.square(3).snapped_center(Point((10.9, 20.1))) == Point((10.5, 20.5))

    def test_an_even_side_centers_on_the_nearest_corner(self) -> None:
        assert Footprint.square(2).snapped_center(Point((10.3, 20.7))) == Point((10.0, 21.0))

    @pytest.mark.parametrize("footprint", SHAPES, ids=repr)
    def test_center_in_a_tile_is_where_the_footprint_centers(self, footprint: Footprint) -> None:
        center = footprint.center_in(Tile(10, 20))
        assert Tile.containing(center) == Tile(10, 20)
        assert footprint.snapped_center(center) == center


class TestTiles:
    def test_they_are_the_tiles_covered_where_it_centers(self) -> None:
        tiles = Footprint.square(2).tiles(at=Point((10.3, 20.7)))
        assert tiles == TileSet([Tile(9, 20), Tile(10, 20), Tile(9, 21), Tile(10, 21)])

    @pytest.mark.parametrize("footprint", SHAPES, ids=repr)
    def test_they_are_the_masks_tiles_in_its_bounding_box(self, footprint: Footprint) -> None:
        center = Point((10.5, 20.5))
        box = footprint.bounding_box(center)
        tiles = footprint.tiles(at=center)
        assert len(tiles) == numpy.count_nonzero(footprint.mask)
        assert all(footprint.mask[tile.x - int(box.x), tile.y - int(box.y)] for tile in tiles)
