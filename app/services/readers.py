"""Read KML / zipped-Shapefile uploads into plain Python feature records.

We use ``pyogrio`` (GDAL bundled in its wheels) through its low-level ``raw``
API, which returns WKB geometries and numpy attribute arrays without needing
geopandas.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
import shapely
from pyogrio import list_layers
from pyogrio import raw as pyogrio_raw
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from ..errors import InvalidFileError, MissingCRSError
from .crs import WGS84, crs_label, parse_crs

# Only these members are ever extracted from an uploaded zip.
SHAPEFILE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}

# Styling / time bookkeeping columns that GDAL's KML driver adds to every layer.
KML_NOISE_FIELDS = {
    "timestamp", "begin", "end", "altitudeMode", "tessellate",
    "extrude", "visibility", "drawOrder", "icon",
}


@dataclass
class RawFeature:
    index: int
    layer: str
    geometry: BaseGeometry | None
    properties: dict
    crs: CRS


@dataclass
class ReadResult:
    features: list[RawFeature] = field(default_factory=list)

    @property
    def crs_label(self) -> str | None:
        labels = {crs_label(f.crs) for f in self.features}
        if not labels:
            return None
        return labels.pop() if len(labels) == 1 else "MIXED"


def _to_python(value):
    """numpy scalars / NaN / NaT -> JSON friendly python values (None for nulls)."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _read_layer(path: Path, layer: str, start_index: int, assume_crs: CRS | None,
                is_kml: bool, label: str, limit: int) -> list[RawFeature]:
    try:
        meta, _fids, geometries, field_data = pyogrio_raw.read(str(path), layer=layer)
    except Exception as exc:
        raise InvalidFileError(f"Could not read layer '{label}': {exc}") from exc

    if len(geometries) > limit:
        raise InvalidFileError(f"File has more than the allowed {limit} features.")

    crs_text = meta.get("crs")
    if crs_text:
        crs = parse_crs(crs_text)
    elif is_kml:
        crs = WGS84  # the KML specification mandates WGS84 lon/lat
    elif assume_crs is not None:
        crs = assume_crs
    else:
        raise MissingCRSError(
            f"Layer '{label}' declares no coordinate reference system (missing .prj?). "
            "Re-upload with the 'assume_crs' form field, e.g. assume_crs=EPSG:4326."
        )

    fields = [str(f) for f in meta["fields"]]
    features: list[RawFeature] = []
    for i, wkb in enumerate(geometries):
        props: dict = {}
        for name, column in zip(fields, field_data):
            if is_kml and name in KML_NOISE_FIELDS:
                continue
            value = _to_python(column[i])
            if value is not None:
                props[name] = value
        geom = shapely.from_wkb(wkb) if wkb is not None else None
        features.append(RawFeature(start_index + i, label, geom, props, crs))
    return features


def _read_path(path: Path, assume_crs: CRS | None, is_kml: bool, max_features: int,
               label_prefix: str = "") -> list[RawFeature]:
    try:
        layers = [str(name) for name, _ in list_layers(str(path))]
    except Exception as exc:
        raise InvalidFileError(f"Could not open file as a geospatial dataset: {exc}") from exc

    out: list[RawFeature] = []
    for layer in layers:
        label = f"{label_prefix}{layer}" if label_prefix else layer
        out.extend(_read_layer(path, layer, len(out), assume_crs, is_kml, label,
                               max_features - len(out)))
    return out


def read_kml(path: Path, max_features: int) -> ReadResult:
    features = _read_path(path, None, True, max_features)
    return ReadResult(features)


def safe_extract_shapefile_zip(zip_path: Path, dest: Path, *, max_members: int,
                               max_unzipped_bytes: int) -> list[Path]:
    """Extract shapefile components with zip-slip and zip-bomb protection."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidFileError("The uploaded file is not a valid zip archive.") from exc

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > max_members:
            raise InvalidFileError(f"Zip contains more than {max_members} files.")
        if sum(m.file_size for m in members) > max_unzipped_bytes:
            raise InvalidFileError("Zip expands beyond the allowed uncompressed size.")

        written = 0
        shp_files: list[Path] = []
        for member in members:
            name = PurePosixPath(member.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                raise InvalidFileError(f"Zip contains an unsafe path: {member.filename!r}")
            if name.parts[0] == "__MACOSX" or name.name.startswith("._"):
                continue  # macOS resource-fork junk
            if name.suffix.lower() not in SHAPEFILE_PARTS:
                continue

            target = dest.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_unzipped_bytes:  # headers can lie; count real bytes
                        raise InvalidFileError("Zip expands beyond the allowed uncompressed size.")
                    dst.write(chunk)
            if name.suffix.lower() == ".shp":
                shp_files.append(target)

    if not shp_files:
        raise InvalidFileError("The zip archive does not contain any .shp file.")
    return sorted(shp_files)


def read_shapefile_zip(zip_path: Path, workdir: Path, *, assume_crs: str | None,
                       max_features: int, max_members: int, max_unzipped_bytes: int) -> ReadResult:
    shp_files = safe_extract_shapefile_zip(
        zip_path, workdir, max_members=max_members, max_unzipped_bytes=max_unzipped_bytes
    )
    fallback = parse_crs(assume_crs) if assume_crs else None

    features: list[RawFeature] = []
    for shp in shp_files:
        label = shp.relative_to(workdir).with_suffix("").as_posix()
        for f in _read_path(shp, fallback, False, max_features - len(features)):
            features.append(RawFeature(len(features), label, f.geometry, f.properties, f.crs))
    return ReadResult(features)
