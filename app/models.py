"""ORM models: one row per uploaded file, one row per extracted feature."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class FileStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(str, enum.Enum):
    MEASURED = "MEASURED"  # area and/or length computed
    NOT_REQUIRED = "NOT_REQUIRED"  # points: valid, nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection, missing geometry
    ERROR = "ERROR"  # measurement attempted and failed for this feature


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "kml" | "shapefile"
    status: Mapped[str] = mapped_column(String(16), default=FileStatus.PENDING.value)
    crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # 0-based, unique within the file
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geometry_type: Mapped[str] = mapped_column(String(32))
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, source CRS
    crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[str] = mapped_column(String(16))
    projected_crs: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
