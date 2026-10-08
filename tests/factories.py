"""Builders for KML text and zipped-shapefile bytes, shared by tests and the sample-data script."""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import shapely
from pyogrio import raw as pyogrio_raw
from shapely.geometry.base import BaseGeometry


def _coords(points) -> str:
    return " ".join(",".join(str(c) for c in p) for p in points)


def kml_placemark(name: str, geom: BaseGeometry, description: str | None = None) -> str:
    """One <Placemark> for Point / LineString / Polygon (with holes) / MultiPolygon."""
    def polygon(p) -> str:
        xml = f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords(p.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs>"
        for hole in p.interiors:
            xml += f"<innerBoundaryIs><LinearRing><coordinates>{_coords(hole.coords)}</coordinates></LinearRing></innerBoundaryIs>"
        return xml + "</Polygon>"

    if geom.geom_type == "Point":
        body = f"<Point><coordinates>{_coords(geom.coords)}</coordinates></Point>"
    elif geom.geom_type == "LineString":
        body = f"<LineString><coordinates>{_coords(geom.coords)}</coordinates></LineString>"
    elif geom.geom_type == "Polygon":
        body = polygon(geom)
    elif geom.geom_type == "MultiPolygon":
        body = "<MultiGeometry>" + "".join(polygon(p) for p in geom.geoms) + "</MultiGeometry>"
    else:
        raise ValueError(f"Unsupported geometry for KML factory: {geom.geom_type}")
    desc = f"<description>{escape(description)}</description>" if description else ""
    return f"<Placemark><name>{escape(name)}</name>{desc}{body}</Placemark>"


def build_kml(placemarks: list[str], folder: str = "Survey") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>test</name>'
        f"<Folder><name>{escape(folder)}</name>{''.join(placemarks)}</Folder>"
        "</Document></kml>"
    )


def build_shapefile_zip(
    geometries: list[BaseGeometry],
    names: list[str] | None = None,
    crs: str | None = "EPSG:4326",
    geometry_type: str = "Polygon",
    stem: str = "survey",
) -> bytes:
    """Write a shapefile with one string attribute ('name') and return it zipped."""
    names = names or [f"feature-{i}" for i in range(len(geometries))]
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / f"{stem}.shp"
        pyogrio_raw.write(
            str(shp),
            geometry=shapely.to_wkb(np.array(geometries, dtype=object)),
            field_data=[np.array(names, dtype=object)],
            fields=["name"],
            crs=crs,
            driver="ESRI Shapefile",
            geometry_type=geometry_type,
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for part in sorted(Path(tmp).iterdir()):
                zf.write(part, arcname=part.name)
        return buffer.getvalue()
