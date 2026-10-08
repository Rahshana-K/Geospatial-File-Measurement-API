import pytest
from pyproj import CRS

from app.services.crs import crs_label, select_projected_crs, utm_epsg_for


@pytest.mark.parametrize("lon,lat,expected", [
    (76.96, 11.0, 32643),      # Coimbatore -> UTM 43N
    (-0.1, 51.5, 32630),       # London -> UTM 30N
    (151.2, -33.9, 32756),     # Sydney -> UTM 56S
    (179.99, 10, 32660),       # edge of last zone
    (-180.0, 10, 32601),       # antimeridian maps to zone 1
    (10, 88, 32661),           # polar north
    (10, -85, 32761),          # polar south
])
def test_utm_epsg_for(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_select_projected_crs_from_geographic():
    crs = select_projected_crs((76.96, 11.0, 76.97, 11.01), CRS.from_epsg(4326))
    assert crs.to_epsg() == 32643


def test_select_projected_crs_from_projected_source():
    # Web Mercator coordinates of roughly (76.96E, 11N)
    crs = select_projected_crs((8567000, 1235000, 8567100, 1235100), CRS.from_epsg(3857))
    assert crs.to_epsg() == 32643


def test_crs_label():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"
    assert crs_label(CRS.from_epsg(32643)) == "EPSG:32643"
