"""Per-geometry measurement logic.

All results are in SI units: square metres for area, metres for lengths.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

from ..models import MeasurementStatus
from .crs import crs_label, get_transformer, select_projected_crs

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "LinearRing", "MultiLineString"}
POINTLIKE = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: MeasurementStatus
    projected_crs: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    warnings: list[str] = field(default_factory=list)
    message: str | None = None


def _project(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """Vectorised reprojection of a 2D geometry."""
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    return shapely.transform(geom, apply)


def _geographic_range_problem(geom: BaseGeometry, source: CRS) -> str | None:
    """Catch projected coordinates mislabelled as lon/lat before they hit PROJ."""
    if not source.is_geographic:
        return None
    minx, miny, maxx, maxy = geom.bounds
    if miny < -90 or maxy > 90 or minx < -360 or maxx > 360:
        return (
            f"Coordinates fall outside the valid range for geographic CRS "
            f"{crs_label(source)}; the file's CRS declaration may be wrong."
        )
    return None


def measure_geometry(geom: BaseGeometry | None, source: CRS) -> Measurement:
    """Measure one geometry that is expressed in ``source`` CRS. Never raises."""
    if geom is None or geom.is_empty:
        return Measurement(MeasurementStatus.UNSUPPORTED, message="Feature has no geometry.")

    gtype = geom.geom_type

    if gtype in POINTLIKE:
        return Measurement(MeasurementStatus.NOT_REQUIRED, message="Points have no area or length.")

    if gtype not in POLYGONAL and gtype not in LINEAR:
        return Measurement(
            MeasurementStatus.UNSUPPORTED,
            message=f"Geometry type '{gtype}' is not supported for measurement.",
        )

    try:
        problem = _geographic_range_problem(geom, source)
        if problem:
            return Measurement(MeasurementStatus.ERROR, message=problem)

        warnings: list[str] = []
        if not shapely.is_valid(geom):
            warnings.append(f"Invalid geometry: {shapely.is_valid_reason(geom)}. Results may be unreliable.")

        flat = shapely.force_2d(geom)  # elevation plays no part in planar measures
        target = select_projected_crs(flat.bounds, source)
        projected = _project(flat, get_transformer(source, target))

        if not all(math.isfinite(v) for v in projected.bounds):
            return Measurement(
                MeasurementStatus.ERROR,
                message="Reprojection produced non-finite coordinates.",
            )

        result = Measurement(
            MeasurementStatus.MEASURED,
            projected_crs=crs_label(target),
            warnings=warnings,
        )
        if gtype in POLYGONAL:
            result.area_m2 = float(projected.area)
            result.perimeter_m = float(projected.length)  # exterior + hole boundaries
        else:
            result.length_m = float(projected.length)
        return result
    except Exception as exc:  # one bad feature must not sink the whole file
        return Measurement(MeasurementStatus.ERROR, message=f"Measurement failed: {exc}")
