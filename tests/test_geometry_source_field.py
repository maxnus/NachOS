"""`SourceField`: circles and rectangles, each adding its value over the ground it covers, read as a `Field`."""

import math
import random

import pytest

from sc2nachos.geometry import Area, Circle, Field, Point, PointLike, Rectangle, SourceField, Tile, TileSet
from test_geometry import OVERLAP_PROBES


def _threat() -> SourceField[float]:
    """A cone of 8 at (10, 10), falling to 0 at 4 away, and 3 over the rectangle from (11, 9) to (14, 12)."""
    field = SourceField()
    field.add(Circle((10.0, 10.0), 4.0), 8.0, full_within=0.0)
    field.add(Rectangle(11.0, 9.0, 3.0, 3.0), 3.0)
    return field


def _mixed_field() -> SourceField[float]:
    """Circles that fall off and that do not, one of them negative, and a rectangle, over the shapes of
    `OVERLAP_PROBES`."""
    field = SourceField()
    field.add(Circle((3.0, 3.0), 2.5), 4.0, full_within=1.0)
    field.add(Circle((6.2, 1.1), 1.5), -2.0, full_within=0.0)
    field.add(Circle((1.0, 5.0), 1.2), 1.5)
    field.add(Rectangle(2.0, 0.5, 1.5, 2.0), 2.5)
    return field


def _mixed_mask() -> SourceField[bool]:
    """The shapes of `_mixed_field`, as booleans."""
    field = SourceField(dtype=bool)
    field.add(Circle((3.0, 3.0), 2.5), True)
    field.add(Circle((6.2, 1.1), 1.5), True)
    field.add(Rectangle(2.0, 0.5, 1.5, 2.0), True)
    return field


class TestBuilding:
    def test_a_new_field_reads_nothing_everywhere(self) -> None:
        floats = SourceField()
        booleans = SourceField(dtype=bool)
        assert floats.value_at((3.0, 4.0)) == 0.0
        assert booleans.value_at((3.0, 4.0)) is False
        assert floats.max(within=Circle((3.0, 4.0), 2.0)) == 0.0
        assert booleans.min(within=Circle((3.0, 4.0), 2.0)) is False
        assert floats.gradient_at((3.0, 4.0)) == (0.0, 0.0)

    def test_a_field_holds_floats_or_booleans(self) -> None:
        with pytest.raises(TypeError, match="floats or booleans, not int"):
            SourceField(dtype=int)

    @pytest.mark.parametrize("spacing", [0.0, -0.25])
    def test_the_spacing_is_positive(self, spacing: float) -> None:
        with pytest.raises(ValueError, match="positive spacing"):
            SourceField(spacing=spacing)

    def test_the_spacing_reads_back(self) -> None:
        assert SourceField().spacing == 0.25
        assert SourceField(spacing=0.5).spacing == 0.5

    def test_a_field_adds_circles_and_rectangles_alone(self) -> None:
        field = SourceField()
        with pytest.raises(TypeError, match=r"not a Tile; add a tile's bounding_rectangle\(\)"):
            field.add(Tile(1, 1), 2.0)  # pyright: ignore[reportArgumentType]
        with pytest.raises(TypeError, match="not a TileSet$"):
            field.add(TileSet([Tile(1, 1)]), 2.0)  # pyright: ignore[reportArgumentType]
        field.add(Tile(1, 1).bounding_rectangle(), 2.0)
        assert field.value_at((1.5, 1.5)) == 2.0

    def test_only_a_circle_of_floats_falls_off(self) -> None:
        with pytest.raises(ValueError, match="rectangle holds its value throughout"):
            SourceField().add(Rectangle(0, 0, 2, 2), 1.0, full_within=1.0)
        with pytest.raises(ValueError, match="booleans"):
            SourceField(dtype=bool).add(Circle((0, 0), 2), True, full_within=1.0)

    @pytest.mark.parametrize("full_within", [-0.5, 2.5])
    def test_a_circle_falls_off_within_its_radius(self, full_within: float) -> None:
        with pytest.raises(ValueError, match="between 0 and the radius"):
            SourceField().add(Circle((0, 0), 2), 1.0, full_within=full_within)

    def test_repr(self) -> None:
        assert repr(_threat()) == "SourceField(floats, 1 circles, 1 rectangles)"
        assert repr(SourceField(dtype=bool)) == "SourceField(booleans, 0 circles, 0 rectangles)"


class TestValueAt:
    def test_a_circle_covers_its_edge_and_a_rectangle_its_lower_edges_alone(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 2.0), 1.0)
        field.add(Rectangle(5.0, 5.0, 1.0, 1.0), 2.0)
        assert field.value_at((2.0, 0.0)) == 1.0
        assert field.value_at((2.0, 0.01)) == 0.0
        assert field.value_at((5.0, 5.0)) == field.value_at((5.99, 5.99)) == 2.0
        assert field.value_at((6.0, 5.5)) == field.value_at((5.5, 6.0)) == 0.0

    def test_a_circle_holds_its_value_out_to_full_within_then_falls_linearly_to_zero_at_its_radius(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 4.0), 6.0, full_within=2.0)
        distances_and_values = [(0.0, 6.0), (1.5, 6.0), (2.0, 6.0), (3.0, 3.0), (3.5, 1.5), (4.0, 0.0), (5.0, 0.0)]
        assert [field.value_at((distance, 0.0)) for distance, _ in distances_and_values] == [
            value for _, value in distances_and_values
        ]
        assert field.value_at((0.0, -3.0)) == 3.0
        assert field.value_at((1.8, 2.4)) == pytest.approx(3.0)

    def test_a_cone_peaks_at_its_center(self) -> None:
        field = SourceField()
        field.add(Circle((10.0, 10.0), 4.0), 8.0, full_within=0.0)
        assert field.value_at((10.0, 10.0)) == 8.0
        assert field.value_at((12.0, 10.0)) == 4.0

    def test_overlapping_shapes_add_up(self) -> None:
        threat = _threat()
        assert threat.value_at((12.0, 10.0)) == 4.0 + 3.0
        assert threat.value_at((10.0, 10.0)) == 8.0
        assert threat.value_at((13.0, 11.0)) == pytest.approx(8.0 * (4.0 - math.sqrt(10.0)) / 4.0 + 3.0)
        threat.add(Circle((12.0, 10.0), 1.0), -5.0)
        assert threat.value_at((12.0, 10.0)) == 2.0

    def test_booleans_are_or_ed(self) -> None:
        mask = SourceField(dtype=bool)
        mask.add(Circle((0.0, 0.0), 2.0), True)
        mask.add(Rectangle(1.0, -1.0, 3.0, 2.0), True)
        mask.add(Circle((10.0, 10.0), 2.0), False)
        assert mask.value_at((1.5, 0.0)) is True
        assert mask.value_at((3.5, 0.0)) is True
        assert mask.value_at((-1.5, 0.0)) is True
        assert mask.value_at((10.0, 10.0)) is False
        assert repr(mask) == "SourceField(booleans, 1 circles, 1 rectangles)"

    def test_the_value_is_a_python_scalar(self) -> None:
        assert type(_threat().value_at((12.0, 10.0))) is float
        assert type(_threat().value_at((50.0, 50.0))) is float
        assert type(_mixed_mask().value_at((3.0, 3.0))) is bool


class TestGradient:
    def test_a_falling_circle_slopes_up_toward_its_center(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 4.0), 6.0, full_within=2.0)
        assert field.gradient_at((3.0, 0.0)) == (-3.0, 0.0)
        assert field.gradient_at((0.0, -3.0)) == (0.0, 3.0)
        slope_x, slope_y = field.gradient_at((2.1, 2.1))
        assert math.hypot(slope_x, slope_y) == pytest.approx(3.0)
        assert slope_x == pytest.approx(slope_y)

    def test_a_negative_circle_slopes_up_away_from_its_center(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 4.0), -6.0, full_within=2.0)
        assert field.gradient_at((3.0, 0.0)) == (3.0, 0.0)

    @pytest.mark.parametrize(
        ("point", "slope"),
        [
            ((1.0, 0.0), (0.0, 0.0)),  # on the plateau
            ((2.0, 0.0), (0.0, 0.0)),  # where the falloff begins
            ((4.0, 0.0), (-3.0, 0.0)),  # on the edge, which slopes
            ((4.5, 0.0), (0.0, 0.0)),  # beyond the radius
        ],
    )
    def test_a_circle_slopes_from_beyond_full_within_out_to_its_edge(
        self, point: PointLike, slope: tuple[float, float]
    ) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 4.0), 6.0, full_within=2.0)
        assert field.gradient_at(point) == slope

    def test_a_cone_and_a_constant_circle_are_flat_at_their_center(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 4.0), 8.0, full_within=0.0)
        field.add(Circle((10.0, 0.0), 4.0), 8.0)
        assert field.gradient_at((0.0, 0.0)) == (0.0, 0.0)
        assert field.gradient_at((10.0, 0.0)) == field.gradient_at((13.0, 0.0)) == (0.0, 0.0)

    def test_a_rectangle_is_flat_even_at_its_edge(self) -> None:
        field = SourceField()
        field.add(Rectangle(11.0, 9.0, 3.0, 3.0), 3.0)
        assert field.gradient_at((11.0, 10.0)) == field.gradient_at((12.0, 10.0)) == (0.0, 0.0)

    @pytest.mark.parametrize("point", [(2.2, 3.7), (4.6, 2.1), (5.9, 0.4), (3.1, 1.9), (2.0, 4.5)], ids=repr)
    def test_the_gradient_is_the_slope_of_the_values_around_it(self, point: tuple[float, float]) -> None:
        field = _mixed_field()
        step = 1e-6
        x, y = point
        finite_difference_x = (field.value_at((x + step, y)) - field.value_at((x - step, y))) / (2 * step)
        finite_difference_y = (field.value_at((x, y + step)) - field.value_at((x, y - step))) / (2 * step)
        assert field.gradient_at(point) == pytest.approx((finite_difference_x, finite_difference_y), abs=1e-6)

    def test_a_field_of_booleans_has_no_gradient(self) -> None:
        with pytest.raises(TypeError, match="booleans"):
            _mixed_mask().gradient_at((3.0, 3.0))


class TestAreaReads:
    def test_a_source_field_is_a_field(self) -> None:
        def largest_near(field: Field[float], point: PointLike) -> tuple[float, Point]:
            return field.max(within=Circle(point, 1.0)), field.argmax(within=Circle(point, 1.0))

        assert largest_near(_threat(), (10.0, 10.0)) == (8.0, Point((10.0, 10.0)))
        mask: Field[bool] = SourceField(dtype=bool)
        assert mask.max(within=Tile(1, 1)) is False

    @pytest.mark.parametrize("area", OVERLAP_PROBES, ids=repr)
    @pytest.mark.parametrize("field", [_mixed_field(), _mixed_mask()], ids=["floats", "booleans"])
    def test_every_read_is_the_value_at_a_point_the_area_holds(self, field: SourceField, area: Area) -> None:
        """An area's reads work out many values at once, `value_at` one, and the two must agree to the last bit."""
        for extreme, where in ((field.min, field.argmin), (field.max, field.argmax)):
            point = where(within=area)
            assert point in area
            assert field.value_at(point) == extreme(within=area)

    def test_one_circles_largest_value_is_exact_where_the_spaced_points_miss_it(self) -> None:
        field = SourceField()
        field.add(Circle((5.0, 0.0), 4.0), 8.0, full_within=0.0)
        area = Circle((0.0, 0.0), 2.0)
        assert field.max(within=area) == 2.0
        assert field.argmax(within=area) == Point((2.0, 0.0))

    @pytest.mark.parametrize(
        ("area", "circle"),
        [
            (Circle((0.0, 0.0), 2.0), Circle((3.0, 0.0), 1.001)),
            (Rectangle(0.0, 0.0, 2.0, 2.0), Circle((2.999, 1.0), 1.0)),  # through the right edge, which it leaves out
            (Tile(0, 0), Circle((0.5, 1.999), 1.0)),  # through the top edge, which it leaves out
            (TileSet([Tile(0, 0), Tile(5, 5)]), Circle((5.5, 7.0), 1.001)),
        ],
        ids=repr,
    )
    def test_a_boolean_circle_reaching_the_area_is_found_however_little_it_reaches_in(
        self, area: Area, circle: Circle
    ) -> None:
        mask = SourceField(dtype=bool)
        mask.add(circle, True)
        assert mask.max(within=area) is True
        point = mask.argmax(within=area)
        assert point in area
        assert point in circle

    def test_a_boolean_circle_reaching_a_units_circle_from_any_side_is_found(self) -> None:
        """200 seeded circles, each reaching 0.001 into a unit's circle from its own angle. The point of the unit's
        circle nearest each one's center rounded outside the unit's about half the time, and was dropped."""
        rng = random.Random(5)
        for _ in range(200):
            unit = Circle((rng.uniform(0, 200), rng.uniform(0, 200)), rng.uniform(0.375, 1.0))
            radius = rng.uniform(0.5, 3.0)
            angle = rng.uniform(0, 2 * math.pi)
            reach = unit.radius + radius - 0.001
            splash = Circle((unit.center.x + reach * math.cos(angle), unit.center.y + reach * math.sin(angle)), radius)
            mask = SourceField(dtype=bool)
            mask.add(splash, True)
            assert mask.max(within=unit) is True, (unit, splash)

    def test_the_smallest_value_and_where_come_within_the_spacing(self) -> None:
        """The smallest lies at the far corner, (3, 3), which the rectangle leaves out: the field there is
        `10 - sqrt(18)`, and no point read is farther than half the spacing along each axis from it."""
        for spacing in (0.25, 0.05):
            field = SourceField(spacing=spacing)
            field.add(Circle((0.0, 0.0), 10.0), 10.0, full_within=0.0)
            area = Rectangle(1.0, 1.0, 2.0, 2.0)
            smallest_at_the_corner = 10.0 - math.sqrt(18.0)
            assert smallest_at_the_corner < field.min(within=area) <= smallest_at_the_corner + spacing / math.sqrt(2)
            assert field.argmin(within=area) == pytest.approx(Point((3.0 - spacing / 2, 3.0 - spacing / 2)))

    def test_overlapping_circles_add_up_where_both_reach(self) -> None:
        field = SourceField()
        field.add(Circle((0.0, 0.0), 1.0), 1.0)
        field.add(Circle((1.5, 0.0), 1.0), 1.0)
        area = Rectangle(-1.0, -1.0, 3.5, 2.0)
        assert field.max(within=area) == 2.0
        point = field.argmax(within=area)
        assert point.distance_to((0.0, 0.0)) <= 1.0
        assert point.distance_to((1.5, 0.0)) <= 1.0

    def test_points_that_tie_go_to_the_lowest_x_then_y(self) -> None:
        """Every point reads true, and the point nearest the circle's center lies left of every spaced one."""
        mask = SourceField(dtype=bool)
        mask.add(Circle((0.01, 0.5), 5.0), True)
        assert mask.argmax(within=Rectangle(0.0, 0.0, 1.0, 1.0)) == Point((0.01, 0.5))
        assert mask.argmin(within=Rectangle(0.0, 0.0, 1.0, 1.0)) == Point((0.01, 0.5))
        assert SourceField().argmin(within=Rectangle(2.0, 3.0, 1.0, 1.0)) == Point((2.125, 3.125))

    @pytest.mark.parametrize("area", [Tile(0, 0), Rectangle(0.0, 0.0, 1.0, 1.0)], ids=repr)
    def test_a_shape_touching_only_an_edge_the_area_leaves_out_reads_nothing(self, area: Area) -> None:
        """As a grid leaves out a tile an area touches only at an edge, and as `in` has it."""
        field = SourceField()
        field.add(Rectangle(1.0, 0.0, 1.0, 1.0), 5.0)
        assert field.max(within=area) == 0.0
        assert field.argmax(within=area) == Point((0.125, 0.125))
        assert field.max(within=Tile(1, 0)) == 5.0

    def test_a_shape_touching_a_circle_reads_where_they_touch(self) -> None:
        """A circle holds its edge, so the point the two share is the circle's."""
        field = SourceField()
        field.add(Circle((2.0, 0.5), 1.0), 7.0)
        assert field.max(within=Circle((0.0, 0.5), 1.0)) == 7.0
        assert field.argmax(within=Circle((0.0, 0.5), 1.0)) == Point((1.0, 0.5))

    def test_a_falling_circle_beyond_an_edge_the_area_leaves_out_reads_just_inside_it(self) -> None:
        field = SourceField()
        field.add(Circle((3.0, 0.5), 4.0), 8.0, full_within=0.0)
        area = Rectangle(0.0, 0.0, 1.0, 1.0)
        point = field.argmax(within=area)
        assert point == Point((math.nextafter(1.0, 0.0), 0.5))
        assert field.max(within=area) == pytest.approx(4.0)

    def test_a_tile_set_reads_its_tiles_alone(self) -> None:
        field = SourceField()
        field.add(Circle((5.5, 5.5), 2.0), 4.0, full_within=0.0)
        assert field.max(within=TileSet([Tile(0, 0), Tile(5, 5)])) == 4.0
        assert field.argmax(within=TileSet([Tile(0, 0), Tile(5, 5)])) == Point((5.5, 5.5))
        assert field.max(within=TileSet([Tile(0, 0), Tile(9, 9)])) == 0.0

    def test_a_spacing_wider_than_a_tile_still_reads_a_point_of_a_tile_set(self) -> None:
        """Split three tiles to a cell, the tile set's bounds hold no cell center on either tile."""
        tiles = TileSet([Tile(0, 0), Tile(5, 5)])
        assert SourceField(spacing=3.0).argmin(within=tiles) in tiles

    @pytest.mark.parametrize("area", [TileSet([]), Rectangle(1.0, 1.0, 0.0, 2.0)], ids=repr)
    def test_an_empty_area_raises(self, area: Area) -> None:
        field = _threat()
        for read in (field.min, field.max, field.argmin, field.argmax):
            with pytest.raises(ValueError, match="empty"):
                read(within=area)

    def test_the_answer_is_a_python_scalar(self) -> None:
        assert type(_threat().max(within=Tile(10, 10))) is float
        assert type(_threat().min(within=Tile(50, 50))) is float
        assert type(_mixed_mask().min(within=Tile(3, 3))) is bool
        assert type(_mixed_mask().max(within=Tile(50, 50))) is bool
        assert all(type(coordinate) is float for coordinate in _threat().argmax(within=Tile(10, 10)))
