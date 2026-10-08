"""Upload -> validate -> store -> read -> measure -> persist pipeline."""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import shapely
from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import FileTooLargeError, GeoFileError, InvalidFileError, UnsupportedFileTypeError
from ..models import Feature, FileStatus, UploadedFile
from .crs import crs_label
from .measurements import measure_geometry
from .readers import read_kml, read_shapefile_zip

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".kml":
        return "kml"
    if suffix == ".zip":
        return "shapefile"
    raise UnsupportedFileTypeError(
        "Unsupported file type. Upload a .kml file or a .zip containing a Shapefile."
    )


def sanitize_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")
    return name[:200] or "upload"


def _store_upload(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream the upload to disk, aborting once it exceeds ``max_bytes``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(dest, "wb") as out:
        while chunk := upload.file.read(CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise FileTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
            out.write(chunk)
    if size == 0:
        raise InvalidFileError("The uploaded file is empty.")
    return size


def _validate_content(path: Path, file_type: str) -> None:
    with open(path, "rb") as fh:
        head = fh.read(512)
    if file_type == "shapefile" and not head.startswith(b"PK"):
        raise InvalidFileError("The uploaded .zip file is not a valid zip archive.")
    if file_type == "kml" and b"<" not in head:
        raise InvalidFileError("The uploaded .kml file does not look like XML.")


def process_upload(session: Session, settings: Settings, upload: UploadFile,
                   assume_crs: str | None = None) -> UploadedFile:
    """Run the whole pipeline. Raises GeoFileError (with ``file_id`` when a record exists)."""
    original_name = upload.filename or "upload"
    file_type = detect_file_type(original_name)  # 415 before touching disk or DB

    file_id = uuid.uuid4().hex
    stored_name = sanitize_filename(original_name)
    file_dir = settings.upload_dir / file_id
    stored_path = file_dir / stored_name

    try:
        _store_upload(upload, stored_path, settings.max_upload_mb * 1024 * 1024)
        _validate_content(stored_path, file_type)
    except GeoFileError:
        shutil.rmtree(file_dir, ignore_errors=True)  # nothing worth keeping, no DB row yet
        raise

    record = UploadedFile(
        id=file_id, filename=original_name, file_type=file_type,
        status=FileStatus.PROCESSING.value,
    )
    session.add(record)
    session.commit()

    try:
        _extract_and_measure(session, settings, record, stored_path, assume_crs)
    except GeoFileError as exc:
        _mark_failed(session, record, exc.message)
        exc.file_id = file_id
        raise
    except Exception as exc:  # unexpected: record it, tell the client generically
        log.exception("Unexpected error while processing file %s", file_id)
        _mark_failed(session, record, "Unexpected error while processing the file.")
        raise GeoFileError("Unexpected error while processing the file.", file_id) from exc
    return record


def _mark_failed(session: Session, record: UploadedFile, message: str) -> None:
    session.rollback()
    record.status = FileStatus.FAILED.value
    record.error = message
    record.completed_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()


def _extract_and_measure(session: Session, settings: Settings, record: UploadedFile,
                         stored_path: Path, assume_crs: str | None) -> None:
    if record.file_type == "kml":
        result = read_kml(stored_path, settings.max_features)
    else:
        with tempfile.TemporaryDirectory(prefix="geo-") as tmp:
            result = read_shapefile_zip(
                stored_path, Path(tmp), assume_crs=assume_crs,
                max_features=settings.max_features,
                max_members=settings.max_zip_members,
                max_unzipped_bytes=settings.max_unzipped_mb * 1024 * 1024,
            )

    rows: list[Feature] = []
    for raw in result.features:
        m = measure_geometry(raw.geometry, raw.crs)
        geometry_json = json.loads(shapely.to_geojson(raw.geometry)) if raw.geometry is not None else None
        rows.append(Feature(
            file_id=record.id,
            index=raw.index,
            layer=raw.layer,
            geometry_type=raw.geometry.geom_type if raw.geometry is not None else "None",
            geometry=geometry_json,
            crs=crs_label(raw.crs),
            properties=raw.properties,
            measurement_status=m.status.value,
            projected_crs=m.projected_crs,
            area_m2=m.area_m2,
            perimeter_m=m.perimeter_m,
            length_m=m.length_m,
            warnings=m.warnings,
            message=m.message,
        ))

    session.add_all(rows)
    record.feature_count = len(rows)
    record.crs = result.crs_label
    record.status = FileStatus.COMPLETED.value
    record.completed_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()


def delete_file(session: Session, settings: Settings, record: UploadedFile) -> None:
    file_id = record.id
    session.delete(record)
    session.commit()
    shutil.rmtree(settings.upload_dir / file_id, ignore_errors=True)
