"""CRS helpers: labelling, projected-CRS selection and cached transformers.

Strategy for measurements
-------------------------
Areas and lengths must never be computed on raw longitude/latitude degrees.
For each feature we pick a *local* projected CRS and measure there:

* the representative point is the centre of the feature's bounding box,
  expressed in WGS84 lon/lat;
* between 80°S and 84°N we use the matching UTM zone (EPSG:326xx north,
  EPSG:327xx south), which keeps distortion below ~0.1% within a zone;
* beyond that, UTM is undefined, so we fall back to the polar
  stereographic systems (EPSG:32661 north / EPSG:32761 south).
"""
from __future__ import annotations

import math
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from ..errors import InvalidFileError

WGS84 = CRS.from_epsg(4326)


def parse_crs(value: str) -> CRS:
    """Parse a user supplied CRS string such as ``EPSG:4326`` (or WKT)."""
    try:
        return CRS.from_user_input(value)
    except CRSError as exc:
        raise InvalidFileError(f"Unrecognised CRS {value!r}: {exc}") from exc


def crs_label(crs: CRS) -> str:
    """Short, stable label such as ``EPSG:4326``; falls back to the CRS name."""
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name or "UNKNOWN"


@lru_cache(maxsize=256)
def get_transformer(source: CRS, target: CRS) -> Transformer:
    """Building a Transformer is expensive; reuse them across features."""
    return Transformer.from_crs(source, target, always_xy=True)


def bounds_center_lonlat(bounds: tuple[float, float, float, float], source: CRS) -> tuple[float, float]:
    """Centre of a bounding box in the source CRS, converted to WGS84 lon/lat."""
    minx, miny, maxx, maxy = bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    if source == WGS84:
        return cx, cy
    lon, lat = get_transformer(source, WGS84).transform(cx, cy)
    return lon, lat


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone (or polar stereographic) covering lon/lat."""
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("Non-finite coordinate while selecting a projected CRS")
    if lat >= 84.0:
        return 32661  # WGS 84 / UPS North
    if lat < -80.0:
        return 32761  # WGS 84 / UPS South
    lon = ((lon + 180.0) % 360.0) - 180.0  # normalise to [-180, 180)
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def select_projected_crs(bounds: tuple[float, float, float, float], source: CRS) -> CRS:
    lon, lat = bounds_center_lonlat(bounds, source)
    return CRS.from_epsg(utm_epsg_for(lon, lat))
