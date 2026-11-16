"""`SourceField`: circles, each adding its value over the ground it covers, read as a `Field`."""

import math
import random
from collections.abc import Callable

import numpy
import pytest

from sc2nachos.geometry import (
    Area,
    Circle,
    CircleSource,
    Edge,
    Field,
    Grid,
    Point,
    Point3D,
    PointLike,
    Rectangle,
    SourceField,
    Tile,
    TileSet,
)
from test_geometry import OVERLAP_PROBES


def _threat() -> SourceField:
    """A cone of 8 at (10, 10), falling to 0 at 4 away, and 3 within 1.5 of (13, 10)."""
    field = SourceField()
    field.add_circle((10.0, 10.0), 4.0, 8.0, edge=Edge.LINEAR, edge_width=4.0)
    field.add_circle((13.0, 10.0), 1.5, 3.0)
    return field


def _mixed_field() -> SourceField:
    """Circles with a linear edge, a smooth one and none, one of them negative, over the shapes of `OVERLAP_PROBES`."""
    field = SourceField()
    field.add_circle((3.0, 3.0), 2.5, 4.0, edge=Edge.LINEAR, edge_width=1.5)
    field.add_circle((6.2, 1.1), 1.5, -2.0, edge=Edge.SMOOTH, edge_width=1.5)
    field.add_circle((1.0, 5.0), 1.2, 1.5)
    field.add_circle((2.75, 1.5), 1.0, 2.5)
    return field


def _mask() -> SourceField:
    """The circles of `_mixed_field`, each of 1.0."""
    field = SourceField()
    for center, radius in (((3.0, 3.0), 2.5), ((6.2, 1.1), 1.5), ((1.0, 5.0), 1.2), ((2.75, 1.5), 1.0)):
        field.add_circle(center, radius, 1.0)
    return field


class TestBuilding:
    def test_a_new_field_reads_nothing_everywhere(self) -> None:
        field = SourceField()
        assert field.value_at((3.0, 4.0)) == 0.0
        assert field.max(within=Circle((3.0, 4.0), 2.0)) == 0.0
        assert field.min(within=Circle((3.0, 4.0), 2.0)) == 0.0
        assert field.gradient_at((3.0, 4.0)) == (0.0, 0.0)

    @pytest.mark.parametrize("spacing", [0.0, -0.25])
    def test_the_spacing_is_positive(self, spacing: float) -> None:
        with pytest.raises(ValueError, match="positive spacing"):
            SourceField(spacing=spacing)

    def test_the_spacing_reads_back(self) -> None:
        assert SourceField().spacing == 0.25
        assert SourceField(spacing=0.5).spacing == 0.5

    @pytest.mark.parametrize("radius", [0.0, -1.0, math.nan])
    def test_a_circles_radius_lies_above_0(self, radius: float) -> None:
        with pytest.raises(ValueError, match="radius lies above 0"):
            SourceField().add_circle((0.0, 0.0), radius, 1.0)

    def test_a_circle_is_held_by_its_center_and_radius(self) -> None:
        """A `Point` or a tuple as the center alike, and the reads see the circle `Circle` draws."""
        field = SourceField()
        field.add_circle(Point((3.0, 4.0)), 2.0, 5.0)
        field.add_circle((3.0, 4.0), 2.0, 1.0)
        assert field.value_at((5.0, 4.0)) == 6.0
        assert field.value_at((5.001, 4.0)) == 0.0
        assert field.max(within=Circle((6.0, 4.0), 1.0)) == 6.0

    @pytest.mark.parametrize("edge", [Edge.LINEAR, Edge.SMOOTH])
    @pytest.mark.parametrize("edge_width", [-0.5, 0.0, 2.5])
    def test_a_sloping_edges_width_lies_above_0_and_within_the_radius(self, edge: Edge, edge_width: float) -> None:
        with pytest.raises(ValueError, match="above 0 and within the radius"):
            SourceField().add_circle((0, 0), 2, 1.0, edge=edge, edge_width=edge_width)

    def test_a_sharp_edge_takes_no_width(self) -> None:
        with pytest.raises(ValueError, match="sharp edge takes no width"):
            SourceField().add_circle((0, 0), 2, 1.0, edge_width=1.0)

    def test_len_counts_the_circles_and_clear_removes_them_keeping_the_spacing(self) -> None:
        field = SourceField(spacing=0.5)
        field.add_circle((0.0, 0.0), 2.0, 1.0)
        field.add_circle((5.0, 0.0), 1.0, -2.0, edge=Edge.SMOOTH, edge_width=1.0)
        assert len(field) == 2
        field.clear()
        assert len(field) == 0
        assert field.spacing == 0.5
        assert field.value_at((0.0, 0.0)) == 0.0
        assert field.max(within=Circle((0.0, 0.0), 1.0)) == 0.0
        field.add_circle((0.0, 0.0), 2.0, 3.0)
        assert len(field) == 1
        assert field.value_at((0.0, 0.0)) == 3.0

    @pytest.mark.parametrize("make_field", [_threat, SourceField], ids=["circles", "no circle"])
    def test_a_field_has_no_truth_value(self, make_field: Callable[[], SourceField]) -> None:
        with pytest.raises(TypeError, match="no truth value"):
            bool(make_field())

    def test_iterating_gives_each_circle_as_it_was_added_in_order(self) -> None:
        field = SourceField()
        field.add_circle((10, 10), 4, 8, edge=Edge.LINEAR, edge_width=4.0)
        field.add_circle(Point((13.0, 10.0)), 1.5, 3.0)
        assert list(field) == [
            CircleSource(Point((10.0, 10.0)), 4.0, 8.0, Edge.LINEAR, 4.0),
            CircleSource(Point((13.0, 10.0)), 1.5, 3.0, Edge.SHARP, 0.0),
        ]
        assert all(type(source.center) is Point for source in field)
        assert list(SourceField()) == []

    def test_the_circles_iterated_build_a_field_that_reads_the_same(self) -> None:
        field = _mixed_field()
        copied = SourceField()
        for source in field:
            copied.add_circle(
                source.center, source.radius, source.value, edge=source.edge, edge_width=source.edge_width
            )
        rectangle = Rectangle(-2.0, -1.0, 12.0, 10.0)
        assert copied.to_grid(rectangle).values.tolist() == field.to_grid(rectangle).values.tolist()
        assert copied.gradient_at((3.1, 4.2)) == field.gradient_at((3.1, 4.2))

    def test_a_circle_source_is_frozen(self) -> None:
        source = next(iter(_threat()))
        with pytest.raises(AttributeError):
            source.radius = 1.0  # pyright: ignore[reportAttributeAccessIssue]

    def test_a_field_is_built_from_circle_sources_each_checked_as_add_circle_checks_it(self) -> None:
        sources = [
            CircleSource(Point((10.0, 10.0)), 4.0, 8.0, Edge.LINEAR, 4.0),
            CircleSource(Point((13.0, 10.0)), 1.5, 3.0, Edge.SHARP, 0.0),
        ]
        field = SourceField(source for source in sources)
        assert list(field) == sources
        assert field.spacing == 0.25
        assert SourceField(sources, spacing=0.5).spacing == 0.5
        with pytest.raises(ValueError, match="radius lies above 0"):
            SourceField([CircleSource(Point((0.0, 0.0)), 0.0, 1.0, Edge.SHARP, 0.0)])
        with pytest.raises(ValueError, match="sharp edge takes no width"):
            SourceField([CircleSource(Point((0.0, 0.0)), 2.0, 1.0, Edge.SHARP, 1.0)])

    def test_a_field_is_built_from_another_fields_circles(self) -> None:
        field = _mixed_field()
        rebuilt = SourceField(field)
        assert list(rebuilt) == list(field)
        assert rebuilt.value_at((3.1, 4.2)) == field.value_at((3.1, 4.2))

    @pytest.mark.parametrize("source", [Circle((0.0, 0.0), 1.0), ((0.0, 0.0), 1.0, 1.0)], ids=repr)
    def test_a_field_is_built_from_circle_sources_alone(self, source: object) -> None:
        with pytest.raises(TypeError, match="built from CircleSources, not a"):
            SourceField([source])  # pyright: ignore[reportArgumentType]

    def test_a_copy_holds_the_same_circles_and_spacing_apart_from_the_original(self) -> None:
        field = _threat()
        copied = field.copy()
        assert list(copied) == list(field)
        assert SourceField(spacing=0.5).copy().spacing == 0.5
        copied.add_circle((0.0, 0.0), 1.0, 1.0)
        field.clear()
        assert len(copied) == 3
        assert len(field) == 0

    def test_repr(self) -> None:
        assert repr(_threat()) == "SourceField(2 circles)"
        assert repr(SourceField()) == "SourceField(0 circles)"


class TestValueAt:
    def test_a_circle_covers_its_edge(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 2.0, 1.0)
        assert field.value_at((2.0, 0.0)) == 1.0
        assert field.value_at((2.0, 0.01)) == 0.0

    def test_a_linear_edge_holds_the_value_to_where_it_begins_then_falls_evenly_to_zero(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 6.0, edge=Edge.LINEAR, edge_width=2.0)
        distances_and_values = [(0.0, 6.0), (1.5, 6.0), (2.0, 6.0), (3.0, 3.0), (3.5, 1.5), (4.0, 0.0), (5.0, 0.0)]
        assert [field.value_at((distance, 0.0)) for distance, _ in distances_and_values] == [
            value for _, value in distances_and_values
        ]
        assert field.value_at((0.0, -3.0)) == 3.0
        assert field.value_at((1.8, 2.4)) == pytest.approx(3.0)

    def test_an_edge_as_wide_as_the_radius_makes_a_cone(self) -> None:
        field = SourceField()
        field.add_circle((10.0, 10.0), 4.0, 8.0, edge=Edge.LINEAR, edge_width=4.0)
        assert field.value_at((10.0, 10.0)) == 8.0
        assert field.value_at((12.0, 10.0)) == 4.0

    def test_a_smooth_edge_eases_from_the_value_to_zero(self) -> None:
        """A point a quarter of the way across the edge has three quarters still to cross: (3/4)²(3 − 3/2) of the
        value."""
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 8.0, edge=Edge.SMOOTH, edge_width=2.0)
        distances_and_values = [
            (1.0, 8.0),
            (2.0, 8.0),
            (2.5, 8.0 * 0.84375),
            (3.0, 4.0),
            (3.5, 8.0 * 0.15625),
            (4.0, 0.0),
            (4.5, 0.0),
        ]
        assert [field.value_at((distance, 0.0)) for distance, _ in distances_and_values] == [
            value for _, value in distances_and_values
        ]

    def test_a_smooth_edge_as_wide_as_the_radius_makes_a_bump(self) -> None:
        field = SourceField()
        field.add_circle((10.0, 10.0), 4.0, 8.0, edge=Edge.SMOOTH, edge_width=4.0)
        assert field.value_at((10.0, 10.0)) == 8.0
        assert field.value_at((11.0, 10.0)) == 8.0 * 0.84375
        assert field.value_at((12.0, 10.0)) == 4.0

    def test_overlapping_circles_add_up(self) -> None:
        threat = _threat()
        assert threat.value_at((12.0, 10.0)) == 4.0 + 3.0
        assert threat.value_at((10.0, 10.0)) == 8.0
        assert threat.value_at((13.0, 11.0)) == pytest.approx(8.0 * (4.0 - math.sqrt(10.0)) / 4.0 + 3.0)
        threat.add_circle((12.0, 10.0), 1.0, -5.0)
        assert threat.value_at((12.0, 10.0)) == 2.0

    def test_a_mask_of_ones_reads_truthy_wherever_a_circle_covers_the_point(self) -> None:
        mask = SourceField()
        mask.add_circle((0.0, 0.0), 2.0, 1.0)
        mask.add_circle((3.0, 0.0), 2.0, 1.0)
        assert mask.value_at((1.5, 0.0)) == 2.0
        assert mask.value_at((4.5, 0.0)) == mask.value_at((-1.5, 0.0)) == 1.0
        assert not mask.value_at((10.0, 10.0))

    def test_the_value_is_a_python_scalar(self) -> None:
        assert type(_threat().value_at((12.0, 10.0))) is float
        assert type(_threat().value_at((50.0, 50.0))) is float


class TestGradient:
    def test_a_falling_circle_slopes_up_toward_its_center(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 6.0, edge=Edge.LINEAR, edge_width=2.0)
        assert field.gradient_at((3.0, 0.0)) == (-3.0, 0.0)
        assert field.gradient_at((0.0, -3.0)) == (0.0, 3.0)
        slope_x, slope_y = field.gradient_at((2.1, 2.1))
        assert math.hypot(slope_x, slope_y) == pytest.approx(3.0)
        assert slope_x == pytest.approx(slope_y)

    def test_a_negative_circle_slopes_up_away_from_its_center(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, -6.0, edge=Edge.LINEAR, edge_width=2.0)
        assert field.gradient_at((3.0, 0.0)) == (3.0, 0.0)

    @pytest.mark.parametrize(
        ("point", "slope"),
        [
            ((1.0, 0.0), (0.0, 0.0)),  # holding its whole value
            ((2.0, 0.0), (0.0, 0.0)),  # where the edge begins
            ((4.0, 0.0), (-3.0, 0.0)),  # on the circle's edge, which slopes
            ((4.5, 0.0), (0.0, 0.0)),  # beyond the circle
        ],
    )
    def test_a_linear_edge_slopes_from_where_it_begins_out_to_the_circles_edge(
        self, point: PointLike, slope: tuple[float, float]
    ) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 6.0, edge=Edge.LINEAR, edge_width=2.0)
        assert field.gradient_at(point) == slope

    @pytest.mark.parametrize(
        ("point", "slope"),
        [
            ((1.0, 0.0), (0.0, 0.0)),  # holding its whole value
            ((2.0, 0.0), (0.0, 0.0)),  # where the edge begins, its slope eased to 0
            ((3.0, 0.0), (-6.0, 0.0)),  # halfway across, steepest: 8 × 6 × ½ × ½ / 2
            ((4.0, 0.0), (0.0, 0.0)),  # on the circle's edge, its slope eased to 0
            ((4.5, 0.0), (0.0, 0.0)),  # beyond the circle
        ],
    )
    def test_a_smooth_edge_slopes_most_halfway_across_and_not_at_its_ends(
        self, point: PointLike, slope: tuple[float, float]
    ) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 8.0, edge=Edge.SMOOTH, edge_width=2.0)
        assert field.gradient_at(point) == pytest.approx(slope)

    def test_a_cone_and_a_constant_circle_are_flat_at_their_center(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 4.0, 8.0, edge=Edge.LINEAR, edge_width=4.0)
        field.add_circle((10.0, 0.0), 4.0, 8.0)
        assert field.gradient_at((0.0, 0.0)) == (0.0, 0.0)
        assert field.gradient_at((10.0, 0.0)) == (0.0, 0.0)

    def test_a_constant_circle_is_flat_even_at_its_edge(self) -> None:
        field = SourceField()
        field.add_circle((10.0, 0.0), 4.0, 8.0)
        assert field.gradient_at((14.0, 0.0)) == field.gradient_at((13.0, 0.0)) == (0.0, 0.0)

    @pytest.mark.parametrize("point", [(2.2, 3.7), (4.6, 2.1), (5.9, 0.4), (3.1, 1.9), (2.0, 4.5)], ids=repr)
    def test_the_gradient_is_the_slope_of_the_values_around_it(self, point: tuple[float, float]) -> None:
        field = _mixed_field()
        step = 1e-6
        x, y = point
        finite_difference_x = (field.value_at((x + step, y)) - field.value_at((x - step, y))) / (2 * step)
        finite_difference_y = (field.value_at((x, y + step)) - field.value_at((x, y - step))) / (2 * step)
        assert field.gradient_at(point) == pytest.approx((finite_difference_x, finite_difference_y), abs=1e-6)


class TestAreaReads:
    def test_a_source_field_is_a_field(self) -> None:
        def largest_near(field: Field[float], point: PointLike) -> tuple[float, Point]:
            return field.max(within=Circle(point, 1.0)), field.argmax(within=Circle(point, 1.0))

        assert largest_near(_threat(), (10.0, 10.0)) == (8.0, Point((10.0, 10.0)))

    @pytest.mark.parametrize("area", OVERLAP_PROBES, ids=repr)
    @pytest.mark.parametrize("field", [_mixed_field(), _mask()], ids=["mixed", "mask"])
    def test_every_read_is_the_value_at_a_point_the_area_holds(self, field: SourceField, area: Area) -> None:
        """An area's reads work out many values at once, `value_at` one, and the two must agree to the last bit."""
        for extreme, where in ((field.min, field.argmin), (field.max, field.argmax)):
            point = where(within=area)
            assert point in area
            assert field.value_at(point) == extreme(within=area)

    @pytest.mark.parametrize("area", OVERLAP_PROBES, ids=repr)
    @pytest.mark.parametrize(
        ("value", "edge", "edge_width"),
        [
            (3.0, Edge.SHARP, 0.0),
            (3.0, Edge.LINEAR, 1.5),
            (-3.0, Edge.LINEAR, 2.0),
            (3.0, Edge.SMOOTH, 1.5),
            (-3.0, Edge.SMOOTH, 2.0),
        ],
        ids=repr,
    )
    def test_a_circle_reaching_an_area_alone_reads_as_every_point_read_does(
        self, area: Area, value: float, edge: Edge, edge_width: float
    ) -> None:
        """`max` of a positive circle reaching an area alone, and `min` of a negative one, read only the area's point
        nearest its center, where `argmax` and `argmin` read every point. A second circle is near no probe."""
        field = SourceField()
        field.add_circle((3.5, 3.0), 2.0, value, edge=edge, edge_width=edge_width)
        field.add_circle((40.0, 40.0), 1.0, 5.0)
        for extreme, where in ((field.min, field.argmin), (field.max, field.argmax)):
            assert field.value_at(where(within=area)) == extreme(within=area)

    def test_one_circles_largest_value_is_exact_where_the_spaced_points_miss_it(self) -> None:
        field = SourceField()
        field.add_circle((5.0, 0.0), 4.0, 8.0, edge=Edge.LINEAR, edge_width=4.0)
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
    def test_a_circle_reaching_the_area_is_found_however_little_it_reaches_in(self, area: Area, circle: Circle) -> None:
        mask = SourceField()
        mask.add_circle(circle.center, circle.radius, 1.0)
        assert mask.max(within=area) == 1.0
        point = mask.argmax(within=area)
        assert point in area
        assert point in circle

    def test_a_circle_reaching_a_units_circle_from_any_side_is_found(self) -> None:
        """200 seeded circles, each reaching 0.001 into a unit's circle from its own angle. The point of the unit's
        circle nearest each one's center rounded outside the unit's about half the time, and was dropped."""
        rng = random.Random(5)
        for _ in range(200):
            unit = Circle((rng.uniform(0, 200), rng.uniform(0, 200)), rng.uniform(0.375, 1.0))
            radius = rng.uniform(0.5, 3.0)
            angle = rng.uniform(0, 2 * math.pi)
            reach = unit.radius + radius - 0.001
            splash = Circle((unit.center.x + reach * math.cos(angle), unit.center.y + reach * math.sin(angle)), radius)
            mask = SourceField()
            mask.add_circle(splash.center, splash.radius, 1.0)
            assert mask.max(within=unit) == 1.0, (unit, splash)

    def test_the_smallest_value_and_where_come_within_the_spacing(self) -> None:
        """The smallest lies at the far corner, (3, 3), which the rectangle leaves out: the field there is
        `10 - sqrt(18)`, and no point read is farther than half the spacing along each axis from it."""
        for spacing in (0.25, 0.05):
            field = SourceField(spacing=spacing)
            field.add_circle((0.0, 0.0), 10.0, 10.0, edge=Edge.LINEAR, edge_width=10.0)
            area = Rectangle(1.0, 1.0, 2.0, 2.0)
            smallest_at_the_corner = 10.0 - math.sqrt(18.0)
            assert smallest_at_the_corner < field.min(within=area) <= smallest_at_the_corner + spacing / math.sqrt(2)
            assert field.argmin(within=area) == pytest.approx(Point((3.0 - spacing / 2, 3.0 - spacing / 2)))

    def test_overlapping_circles_add_up_where_both_reach(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 1.0, 1.0)
        field.add_circle((1.5, 0.0), 1.0, 1.0)
        area = Rectangle(-1.0, -1.0, 3.5, 2.0)
        assert field.max(within=area) == 2.0
        point = field.argmax(within=area)
        assert point.distance_to((0.0, 0.0)) <= 1.0
        assert point.distance_to((1.5, 0.0)) <= 1.0

    def test_points_that_tie_go_to_the_lowest_x_then_y(self) -> None:
        """Every point reads 1, and the point nearest the circle's center lies left of every spaced one."""
        mask = SourceField()
        mask.add_circle((0.01, 0.5), 5.0, 1.0)
        assert mask.argmax(within=Rectangle(0.0, 0.0, 1.0, 1.0)) == Point((0.01, 0.5))
        assert mask.argmin(within=Rectangle(0.0, 0.0, 1.0, 1.0)) == Point((0.01, 0.5))
        assert SourceField().argmin(within=Rectangle(2.0, 3.0, 1.0, 1.0)) == Point((2.125, 3.125))

    @pytest.mark.parametrize("area", [Tile(0, 0), Rectangle(0.0, 0.0, 1.0, 1.0)], ids=repr)
    def test_a_circle_touching_only_an_edge_the_area_leaves_out_reads_nothing(self, area: Area) -> None:
        """As a grid leaves out a tile an area touches only at an edge, and as `in` has it: the circle's leftmost
        point, (1, 0.5), is the next tile's."""
        field = SourceField()
        field.add_circle((1.75, 0.5), 0.75, 5.0)
        assert field.max(within=area) == 0.0
        assert field.max(within=Tile(1, 0)) == 5.0

    def test_a_circle_touching_a_circle_reads_where_they_touch(self) -> None:
        """A circle holds its edge, so the point the two share is both circles'."""
        field = SourceField()
        field.add_circle((2.0, 0.5), 1.0, 7.0)
        assert field.max(within=Circle((0.0, 0.5), 1.0)) == 7.0
        assert field.argmax(within=Circle((0.0, 0.5), 1.0)) == Point((1.0, 0.5))

    def test_a_falling_circle_beyond_an_edge_the_area_leaves_out_reads_just_inside_it(self) -> None:
        field = SourceField()
        field.add_circle((3.0, 0.5), 4.0, 8.0, edge=Edge.LINEAR, edge_width=4.0)
        area = Rectangle(0.0, 0.0, 1.0, 1.0)
        point = field.argmax(within=area)
        assert point == Point((math.nextafter(1.0, 0.0), 0.5))
        assert field.max(within=area) == pytest.approx(4.0)

    def test_a_tile_set_reads_its_tiles_alone(self) -> None:
        field = SourceField()
        field.add_circle((5.5, 5.5), 2.0, 4.0, edge=Edge.LINEAR, edge_width=2.0)
        assert field.max(within=TileSet([Tile(0, 0), Tile(5, 5)])) == 4.0
        assert field.argmax(within=TileSet([Tile(0, 0), Tile(5, 5)])) == Point((5.5, 5.5))
        assert field.max(within=TileSet([Tile(0, 0), Tile(9, 9)])) == 0.0

    def test_a_spacing_wider_than_a_tile_still_reads_a_point_of_a_tile_set(self) -> None:
        """Split three tiles to a cell, the tile set's bounds hold no cell center on either tile."""
        tiles = TileSet([Tile(0, 0), Tile(5, 5)])
        assert SourceField(spacing=3.0).argmin(within=tiles) in tiles

    @pytest.mark.parametrize("area", [TileSet([]), Rectangle(1.0, 1.0, 0.0, 2.0)], ids=repr)
    @pytest.mark.parametrize("make_field", [_threat, SourceField], ids=["circles", "no circle"])
    def test_an_empty_area_raises(self, area: Area, make_field: Callable[[], SourceField]) -> None:
        field = make_field()
        for read in (field.min, field.max, field.argmin, field.argmax):
            with pytest.raises(ValueError, match="empty"):
                read(within=area)

    def test_the_answer_is_a_python_scalar(self) -> None:
        assert type(_threat().max(within=Tile(10, 10))) is float
        assert type(_threat().min(within=Tile(50, 50))) is float
        assert all(type(coordinate) is float for coordinate in _threat().argmax(within=Tile(10, 10)))


def _values_at_tile_centers(field: SourceField, grid: Grid[float]) -> list[list[float]]:
    """`value_at` at the center of each of `grid`'s tiles, laid out as its values."""
    return [[field.value_at(grid.tile_at((x, y)).center) for y in range(grid.height)] for x in range(grid.width)]


class TestToGrid:
    @pytest.mark.parametrize(
        "rectangle",
        [Rectangle(-2.0, -1.0, 12.0, 10.0), Rectangle(0.3, 0.6, 5.2, 4.1), Rectangle(2.0, 2.0, 1.0, 1.0)],
        ids=repr,
    )
    def test_each_tile_holds_the_value_at_its_center_to_the_last_bit(self, rectangle: Rectangle) -> None:
        """Over the tiles `Grid.cropped` takes of the same rectangle, circles of every edge and a negative one."""
        field = _mixed_field()
        grid = field.to_grid(rectangle)
        assert grid.bounds == Grid.zeros(30, 30, origin=Tile(-10, -10)).cropped(rectangle).bounds
        assert grid.values.tolist() == _values_at_tile_centers(field, grid)

    def test_seeded_circles_agree_with_value_at_to_the_last_bit(self) -> None:
        """60 seeded circles of every edge and sign, overlapping, at centers and radii a float holds only roughly."""
        rng = random.Random(7)
        field = SourceField()
        for _ in range(60):
            radius = rng.uniform(0.3, 4.0)
            edge = rng.choice(list(Edge))
            width = 0.0 if edge is Edge.SHARP else rng.uniform(0.05, radius)
            field.add_circle(
                (rng.uniform(0, 30), rng.uniform(0, 30)), radius, rng.uniform(-3, 5), edge=edge, edge_width=width
            )
        grid = field.to_grid(Rectangle(-1.0, -1.0, 32.0, 32.0))
        assert grid.values.tolist() == _values_at_tile_centers(field, grid)

    def test_a_circle_crossing_the_grids_edge_marks_the_tiles_inside_alone(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 3.0, 2.0, edge=Edge.LINEAR, edge_width=3.0)
        grid = field.to_grid(Rectangle(-1.0, 0.0, 6.0, 2.0))
        assert grid.origin == Tile(-1, 0)
        assert grid.values.tolist() == _values_at_tile_centers(field, grid)
        assert grid[(0.5, 0.5)] > 0.0
        assert grid[(3.5, 0.5)] == 0.0

    def test_a_tile_whose_center_lies_on_a_circles_edge_holds_its_value(self) -> None:
        field = SourceField()
        field.add_circle((0.5, 0.5), 2.0, 7.0)
        grid = field.to_grid(Rectangle(-3.0, -3.0, 7.0, 7.0))
        assert grid[Tile(2, 0)] == 7.0
        assert grid[Tile(-2, 0)] == 7.0
        assert grid[Tile(0, 2)] == 7.0
        assert grid[Tile(2, 1)] == 0.0

    def test_a_circle_covering_no_tiles_center_leaves_no_mark(self) -> None:
        """A bile's circle of 0.5 on a tile's corner, which every read over an area still finds."""
        field = SourceField()
        field.add_circle((1.0, 1.0), 0.5, 4.0)
        grid = field.to_grid(Rectangle(0.0, 0.0, 2.0, 2.0))
        assert not grid.values.any()
        assert field.max(within=Tile(0, 0)) == 4.0

    def test_a_circle_beyond_the_rectangle_and_an_empty_field_leave_it_zero(self) -> None:
        field = SourceField()
        field.add_circle((20.0, 20.0), 3.0, 5.0)
        for source in (field, SourceField()):
            grid = source.to_grid(Rectangle(0.0, 0.0, 4.0, 3.0))
            assert (grid.width, grid.height) == (4, 3)
            assert not grid.values.any()

    def test_the_grid_reads_outside_beyond_its_edge_as_asked(self) -> None:
        grid = _threat().to_grid(Rectangle(8.0, 8.0, 4.0, 4.0), outside=0.0)
        assert grid.outside == 0.0
        assert grid[(50.0, 50.0)] == 0.0
        with pytest.raises(IndexError):
            _threat().to_grid(Rectangle(8.0, 8.0, 4.0, 4.0))[(50.0, 50.0)]

    def test_the_grid_holds_floats(self) -> None:
        assert _threat().to_grid(Rectangle(8.0, 8.0, 4.0, 4.0)).values.dtype == float


class TestValuesAt:
    def test_each_value_is_the_value_at_its_point_to_the_last_bit(self) -> None:
        field = _mixed_field()
        rng = random.Random(3)
        points = [Point((rng.uniform(-1, 9), rng.uniform(-1, 8))) for _ in range(200)]
        values = field.values_at(points)
        assert values.dtype == float
        assert values.tolist() == [field.value_at(point) for point in points]

    def test_points_come_as_tuples_points_with_a_height_or_an_array(self) -> None:
        field = _threat()
        expected = [8.0, 7.0, 0.0]
        assert field.values_at([(10.0, 10.0), (12.0, 10.0), (50.0, 50.0)]).tolist() == expected
        assert field.values_at(numpy.array([[10.0, 10.0], [12.0, 10.0], [50.0, 50.0]])).tolist() == expected
        assert field.values_at([Point3D((10.0, 10.0, 3.0)), Point3D((12.0, 10.0, 3.0))]).tolist() == [8.0, 7.0]

    def test_no_points_read_no_values(self) -> None:
        assert _threat().values_at([]).tolist() == []
        assert SourceField().values_at([(1.0, 2.0)]).tolist() == [0.0]

    def test_anything_but_rows_of_two_or_three_coordinates_raises(self) -> None:
        with pytest.raises(ValueError, match="rows of an x and a y"):
            _threat().values_at(numpy.zeros((3, 4)))


class TestArithmetic:
    def test_a_sum_holds_both_fields_circles_and_reads_their_sum(self) -> None:
        threat = _threat()
        mixed = _mixed_field()
        total = threat + mixed
        assert list(total) == list(threat) + list(mixed)
        for point in ((3.0, 3.0), (6.0, 1.5), (11.0, 10.0), (20.0, 20.0)):
            assert total.value_at(point) == pytest.approx(threat.value_at(point) + mixed.value_at(point))
            assert total.gradient_at(point) == pytest.approx(
                tuple(a + b for a, b in zip(threat.gradient_at(point), mixed.gradient_at(point), strict=True))
            )

    def test_a_difference_reads_the_difference(self) -> None:
        threat = _threat()
        mixed = _mixed_field()
        for point in ((3.0, 3.0), (6.0, 1.5), (11.0, 10.0)):
            assert (threat - mixed).value_at(point) == pytest.approx(threat.value_at(point) - mixed.value_at(point))
        assert list(threat - mixed) == list(threat + -mixed)

    def test_a_number_scales_every_circles_value(self) -> None:
        field = _mixed_field()
        point = (3.1, 4.2)
        assert [source.value for source in -field] == [-source.value for source in field]
        assert [source.value for source in 2 * field] == [2 * source.value for source in field]
        assert [source.value for source in field * 0.5] == [0.5 * source.value for source in field]
        assert [source.value for source in field / 4] == [source.value / 4 for source in field]
        assert (-field).value_at(point) == -field.value_at(point)
        assert (field * 3.0).max(within=Circle(point, 1.0)) == pytest.approx(3.0 * field.max(within=Circle(point, 1.0)))
        with pytest.raises(ZeroDivisionError):
            _ = field / 0

    def test_each_result_is_a_new_field_leaving_the_operands_as_they_were(self) -> None:
        threat = _threat()
        mixed = _mixed_field()
        before = (list(threat), list(mixed))
        for result in (threat + mixed, threat - mixed, -threat, 2 * threat, threat / 2):
            result.add_circle((0.0, 0.0), 1.0, 1.0)
        assert (list(threat), list(mixed)) == before

    def test_a_sum_reads_at_the_finer_spacing_and_scaling_keeps_it(self) -> None:
        coarse = SourceField(spacing=0.5)
        fine = SourceField(spacing=0.1)
        assert (coarse + fine).spacing == 0.1
        assert (fine - coarse).spacing == 0.1
        assert (-coarse).spacing == (coarse * 2).spacing == (coarse / 2).spacing == 0.5

    @pytest.mark.parametrize("other", [1.0, Grid.zeros(2, 2), "field"], ids=repr)
    def test_a_field_adds_only_a_field(self, other: object) -> None:
        with pytest.raises(TypeError):
            _ = _threat() + other  # pyright: ignore[reportOperatorIssue]
        with pytest.raises(TypeError):
            _ = _threat() - other  # pyright: ignore[reportOperatorIssue]

    def test_a_field_is_scaled_only_by_a_number(self) -> None:
        with pytest.raises(TypeError):
            _ = _threat() * _threat()  # pyright: ignore[reportOperatorIssue]
        with pytest.raises(TypeError):
            _ = _threat() / "2"  # pyright: ignore[reportOperatorIssue]


class TestSourcesReaching:
    def test_the_circles_reaching_an_area_in_the_order_added(self) -> None:
        field = SourceField()
        field.add_circle((0.0, 0.0), 2.0, 1.0)
        field.add_circle((10.0, 0.0), 1.0, 2.0)
        field.add_circle((3.0, 0.0), 1.5, 3.0, edge=Edge.SMOOTH, edge_width=1.5)
        assert field.sources_reaching(Circle((2.0, 0.0), 0.5)) == [
            CircleSource(Point((0.0, 0.0)), 2.0, 1.0, Edge.SHARP, 0.0),
            CircleSource(Point((3.0, 0.0)), 1.5, 3.0, Edge.SMOOTH, 1.5),
        ]
        assert field.sources_reaching(Circle((6.0, 5.0), 1.0)) == []

    @pytest.mark.parametrize("area", [Tile(0, 0), Rectangle(0.0, 0.0, 1.0, 1.0)], ids=repr)
    def test_a_circle_touching_only_an_edge_the_area_leaves_out_does_not_reach_it(self, area: Area) -> None:
        field = SourceField()
        field.add_circle((1.75, 0.5), 0.75, 5.0)
        assert field.sources_reaching(area) == []
        assert len(field.sources_reaching(Tile(1, 0))) == 1

    @pytest.mark.parametrize("area", OVERLAP_PROBES, ids=repr)
    def test_a_mask_reads_above_0_exactly_where_a_circle_reaches(self, area: Area) -> None:
        assert (len(_mask().sources_reaching(area)) > 0) == (_mask().max(within=area) > 0)

    @pytest.mark.parametrize("area", [TileSet([]), Rectangle(1.0, 1.0, 0.0, 2.0)], ids=repr)
    def test_an_empty_area_is_reached_by_none(self, area: Area) -> None:
        assert _threat().sources_reaching(area) == []
