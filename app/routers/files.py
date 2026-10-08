"""/api/files endpoints."""
from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Feature, FileStatus, MeasurementStatus, UploadedFile
from ..schemas import (
    ErrorResponse, FeaturePage, FileInfo, FileList, MeasurementOut,
    MeasurementsResponse, MeasurementSummary,
)
from ..services.processing import delete_file, process_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_file_or_404(session: Session, file_id: str) -> UploadedFile:
    record = session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"File '{file_id}' not found.")
    return record


@router.post(
    "/", status_code=201, response_model=FileInfo,
    summary="Upload and process a KML or zipped Shapefile",
    responses={413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
@router.post("", status_code=201, response_model=FileInfo, include_in_schema=False)
def upload_file(
    response: Response,
    file: UploadFile = File(..., description="A .kml file, or a .zip containing a Shapefile"),
    assume_crs: str | None = Form(
        default=None,
        description="CRS to assume when a Shapefile has no .prj, e.g. EPSG:4326",
    ),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    record = process_upload(session, settings, file, assume_crs)
    response.headers["Location"] = f"/api/files/{record.id}/"
    return record


@router.get("/", response_model=FileList, summary="List uploaded files (newest first)")
@router.get("", response_model=FileList, include_in_schema=False)
def list_files(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    total = session.scalar(select(func.count()).select_from(UploadedFile)) or 0
    rows = session.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return FileList(total=total, limit=limit, offset=offset, files=rows)


@router.get("/{file_id}/", response_model=FileInfo, summary="File information",
            responses={404: {"model": ErrorResponse}})
@router.get("/{file_id}", response_model=FileInfo, include_in_schema=False)
def get_file(file_id: str, session: Session = Depends(get_session)):
    return _get_file_or_404(session, file_id)


@router.get("/{file_id}/features/", response_model=FeaturePage,
            summary="Extracted features: id, geometry type, geometry, CRS, properties",
            responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def get_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    record = _require_completed(_get_file_or_404(session, file_id))
    rows = session.scalars(
        select(Feature).where(Feature.file_id == file_id)
        .order_by(Feature.index).limit(limit).offset(offset)
    ).all()
    return FeaturePage(file_id=file_id, total=record.feature_count, limit=limit,
                       offset=offset, features=rows)


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse,
            summary="Area / length measurements for each feature",
            responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def get_measurements(
    file_id: str,
    geometry_type: str | None = Query(None, description="Only features of this geometry type, e.g. Polygon"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    record = _require_completed(_get_file_or_404(session, file_id))

    base = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        base = base.where(func.lower(Feature.geometry_type) == geometry_type.lower())

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(Feature.index).limit(limit).offset(offset)).all()

    return MeasurementsResponse(
        file_id=file_id, status=record.status,
        summary=_summarise(session, file_id),
        total=total, limit=limit, offset=offset,
        measurements=[MeasurementOut.model_validate(r) for r in rows],
    )


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and its features",
               responses={404: {"model": ErrorResponse}})
def remove_file(file_id: str, session: Session = Depends(get_session),
                settings: Settings = Depends(get_settings)):
    delete_file(session, settings, _get_file_or_404(session, file_id))
    return Response(status_code=204)


def _require_completed(record: UploadedFile) -> UploadedFile:
    if record.status != FileStatus.COMPLETED.value:
        detail = f"File is {record.status}, results are not available."
        if record.error:
            detail += f" Reason: {record.error}"
        raise HTTPException(status_code=409, detail=detail)
    return record


def _summarise(session: Session, file_id: str) -> MeasurementSummary:
    counts = dict(session.execute(
        select(Feature.measurement_status, func.count())
        .where(Feature.file_id == file_id).group_by(Feature.measurement_status)
    ).all())
    total_area, total_length = session.execute(
        select(func.coalesce(func.sum(Feature.area_m2), 0.0),
               func.coalesce(func.sum(Feature.length_m), 0.0))
        .where(Feature.file_id == file_id)
    ).one()
    return MeasurementSummary(
        total_features=sum(counts.values()),
        measured=counts.get(MeasurementStatus.MEASURED.value, 0),
        not_required=counts.get(MeasurementStatus.NOT_REQUIRED.value, 0),
        unsupported=counts.get(MeasurementStatus.UNSUPPORTED.value, 0),
        errors=counts.get(MeasurementStatus.ERROR.value, 0),
        total_area_m2=float(total_area), total_length_m=float(total_length),
    )
