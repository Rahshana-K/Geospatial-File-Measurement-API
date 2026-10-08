"""Pydantic response models (these also drive the OpenAPI docs)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["3f2b8c1e9a7d4c0f8e6b5a4d3c2b1a09"])
    filename: str = Field(examples=["survey.kml"])
    file_type: str = Field(description="'kml' or 'shapefile'")
    feature_count: int
    crs: str | None = Field(description="CRS of the source data, 'MIXED' if layers differ", examples=["EPSG:4326"])
    status: str = Field(description="PENDING | PROCESSING | COMPLETED | FAILED")
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops tzinfo on read; every timestamp we store is UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class FileList(BaseModel):
    total: int
    limit: int
    offset: int
    files: list[FileInfo]


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(validation_alias="index", description="0-based feature index within the file")
    layer: str | None
    geometry_type: str
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in the source CRS")
    crs: str | None
    properties: dict[str, Any]


class FeaturePage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_id: int = Field(validation_alias="index")
    layer: str | None
    geometry_type: str
    status: str = Field(validation_alias="measurement_status",
                        description="MEASURED | NOT_REQUIRED | UNSUPPORTED | ERROR")
    projected_crs: str | None = Field(description="CRS the measurement was computed in")
    area_m2: float | None = Field(description="Square metres (polygons)")
    perimeter_m: float | None = Field(description="Metres, polygon boundary length")
    length_m: float | None = Field(description="Metres (lines)")
    warnings: list[str]
    message: str | None


class MeasurementSummary(BaseModel):
    total_features: int
    measured: int
    not_required: int
    unsupported: int
    errors: int
    total_area_m2: float
    total_length_m: float


class MeasurementsResponse(BaseModel):
    file_id: str
    status: str
    summary: MeasurementSummary
    total: int = Field(description="Number of features matching the filter")
    limit: int
    offset: int
    measurements: list[MeasurementOut]


class ErrorResponse(BaseModel):
    detail: str
    file_id: str | None = Field(default=None, description="Set when a FAILED record was kept")
