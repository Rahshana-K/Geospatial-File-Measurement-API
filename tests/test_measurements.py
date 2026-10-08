import math

import pytest
from pyproj import CRS, Geod
from shapely.geometry import (
    GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon, box,
)

from app.models import MeasurementStatus
from app.services.measurements import measure_geometry

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


def test_polygon_area_matches_geodesic_reference():
    poly = box(76.96, 11.00, 76.97, 11.01)  # ~1.09 km x 1.09 km near Coimbatore
    m = measure_geometry(poly, WGS84)
    reference_area, reference_perimeter = GEOD.geometry_area_perimeter(poly)
    assert m.status is MeasurementStatus.MEASURED
    assert m.projected_crs == "EPSG:32643"
    assert m.area_m2 == pytest.approx(abs(reference_area), rel=1e-3)
    assert m.perimeter_m == pytest.approx(reference_perimeter, rel=1e-3)
    assert m.length_m is None


def test_area_is_not_computed_in_degrees():
    m = measure_geometry(box(76.96, 11.00, 76.97, 11.01), WGS84)
    assert m.area_m2 > 1e6  # ~1.2 million m2, never the 1e-4 "degrees squared"


def test_linestring_length_matches_geodesic_reference():
    line = LineString([(76.96, 11.0), (76.97, 11.0), (76.97, 11.02)])
    m = measure_geometry(line, WGS84)
    assert m.status is MeasurementStatus.MEASURED
    assert m.length_m == pytest.approx(GEOD.geometry_length(line), rel=1e-3)
    assert m.area_m2 is None


def test_polygon_with_hole_subtracts_hole():
    outer = [(76.96, 11.0), (76.97, 11.0), (76.97, 11.01), (76.96, 11.01)]
    hole = [(76.963, 11.003), (76.967, 11.003), (76.967, 11.007), (76.963, 11.007)]
    with_hole = measure_geometry(Polygon(outer, [hole]), WGS84)
    without = measure_geometry(Polygon(outer), WGS84)
    assert with_hole.area_m2 < without.area_m2
    assert with_hole.perimeter_m > without.perimeter_m


def test_multipolygon_and_multilinestring():
    mp = MultiPolygon([box(76.96, 11.0, 76.97, 11.01), box(76.98, 11.0, 76.99, 11.01)])
    single = measure_geometry(box(76.96, 11.0, 76.97, 11.01), WGS84).area_m2
    assert measure_geometry(mp, WGS84).area_m2 == pytest.approx(2 * single, rel=1e-3)

    ml = MultiLineString([[(76.96, 11.0), (76.97, 11.0)], [(76.96, 11.1), (76.97, 11.1)]])
    assert measure_geometry(ml, WGS84).length_m == pytest.approx(GEOD.geometry_length(ml), rel=1e-3)


@pytest.mark.parametrize("geom", [Point(76.96, 11.0), MultiPoint([(76.96, 11.0), (76.97, 11.0)])])
def test_points_need_no_measurement(geom):
    m = measure_geometry(geom, WGS84)
    assert m.status is MeasurementStatus.NOT_REQUIRED
    assert m.area_m2 is None and m.length_m is None


def test_geometry_collection_is_unsupported_not_a_crash():
    gc = GeometryCollection([Point(76.96, 11.0), LineString([(76.96, 11.0), (76.97, 11.0)])])
    m = measure_geometry(gc, WGS84)
    assert m.status is MeasurementStatus.UNSUPPORTED
    assert "GeometryCollection" in m.message


@pytest.mark.parametrize("geom", [None, Polygon()])
def test_missing_or_empty_geometry_is_unsupported(geom):
    assert measure_geometry(geom, WGS84).status is MeasurementStatus.UNSUPPORTED


def test_z_coordinates_are_ignored():
    flat = measure_geometry(box(76.96, 11.0, 76.97, 11.01), WGS84)
    z = Polygon([(76.96, 11.0, 500), (76.97, 11.0, 500), (76.97, 11.01, 900), (76.96, 11.01, 900)])
    assert measure_geometry(z, WGS84).area_m2 == pytest.approx(flat.area_m2)


def test_invalid_polygon_is_measured_with_warning():
    bowtie = Polygon([(76.96, 11.0), (76.97, 11.01), (76.97, 11.0), (76.96, 11.01)])
    m = measure_geometry(bowtie, WGS84)
    assert m.status is MeasurementStatus.MEASURED
    assert any("Invalid geometry" in w for w in m.warnings)


def test_projected_source_is_reprojected_not_measured_in_place():
    # 100 m x 100 m square in UTM 43N metres
    square = box(500000, 1217000, 500100, 1217100)
    m = measure_geometry(square, CRS.from_epsg(32643))
    assert m.area_m2 == pytest.approx(10_000, rel=1e-6)


def test_web_mercator_distortion_is_corrected():
    # A 100 m x 100 m square in EPSG:3857 at ~11N is only ~96 m wide in reality (scale ~1.02).
    square = box(8567000, 1235000, 8567100, 1235100)
    m = measure_geometry(square, CRS.from_epsg(3857))
    assert m.area_m2 < 10_000 * 0.97


def test_out_of_range_geographic_coordinates_report_error():
    m = measure_geometry(box(500000, 1217000, 500100, 1217100), WGS84)
    assert m.status is MeasurementStatus.ERROR
    assert "outside the valid range" in m.message


def test_results_are_finite():
    m = measure_geometry(box(76.96, 11.0, 76.97, 11.01), WGS84)
    assert all(math.isfinite(v) for v in (m.area_m2, m.perimeter_m))


def test_high_latitude_uses_polar_stereographic():
    m = measure_geometry(box(10, 86, 11, 86.5), WGS84)
    assert m.projected_crs == "EPSG:32661"
    assert m.area_m2 > 0
