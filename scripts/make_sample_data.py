"""Generate sample input files in ./sample_data (run from the project root).

    python -m scripts.make_sample_data
"""
from __future__ import annotations

from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from tests.factories import build_kml, build_shapefile_zip, kml_placemark

OUT = Path("sample_data")


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # All coordinates are lon/lat (EPSG:4326) in southern India (UTM zone 43N).
    plot = box(76.960, 11.000, 76.970, 11.010)                       # ~1.09 km x 1.11 km
    plot_with_pond = Polygon(
        [(76.980, 11.000), (76.990, 11.000), (76.990, 11.010), (76.980, 11.010)],
        [[(76.983, 11.003), (76.987, 11.003), (76.987, 11.007), (76.983, 11.007)]],
    )
    parcels = MultiPolygon([box(77.000, 11.000, 77.005, 11.005), box(77.010, 11.000, 77.015, 11.005)])
    road = LineString([(76.960, 10.995), (76.970, 10.995), (76.970, 11.015)])
    well = Point(76.965, 11.005)

    kml = build_kml([
        kml_placemark("Plot A", plot, "Rice field"),
        kml_placemark("Plot B (with pond)", plot_with_pond),
        kml_placemark("Parcels", parcels),
        kml_placemark("Access road", road),
        kml_placemark("Borewell", well),
        # GeometryCollection (point + line) exercises the 'unsupported' path
        '<Placemark><name>Mixed</name><MultiGeometry>'
        '<Point><coordinates>76.96,11.0</coordinates></Point>'
        '<LineString><coordinates>76.96,11.0 76.97,11.0</coordinates></LineString>'
        '</MultiGeometry></Placemark>',
    ], folder="Survey")
    (OUT / "survey.kml").write_text(kml, encoding="utf-8")

    (OUT / "plots_wgs84.zip").write_bytes(
        build_shapefile_zip([plot, plot_with_pond], names=["Plot A", "Plot B"], crs="EPSG:4326")
    )
    # Same 100 m x 100 m square, stored in projected metres (UTM 43N)
    (OUT / "square_utm43n.zip").write_bytes(
        build_shapefile_zip([box(500000, 1217000, 500100, 1217100)], names=["Square"], crs="EPSG:32643")
    )
    print("Wrote:", *sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
