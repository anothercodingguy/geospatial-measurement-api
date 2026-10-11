import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FileStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(enum.StrEnum):
    OK = "OK"  # measured successfully
    NOT_APPLICABLE = "NOT_APPLICABLE"  # points: nothing to measure
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"  # e.g. GeometryCollection, KML Model
    EMPTY_GEOMETRY = "EMPTY_GEOMETRY"  # feature has no (or an empty) geometry
    ERROR = "ERROR"  # measurement attempted but failed (e.g. unknown CRS)


class UploadedFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "shapefile" | "kml" | "kmz"
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus), default=FileStatus.PROCESSING)
    crs: Mapped[str | None] = mapped_column(String(64))  # e.g. "EPSG:4326"
    crs_name: Mapped[str | None] = mapped_column(String(255))
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # 0-based position in the source file
    source_id: Mapped[str | None] = mapped_column(String(255))  # KML id / shapefile record no.
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # GeoJSON, in the file's CRS
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    measurement_type: Mapped[str | None] = mapped_column(String(16))  # "area" | "length"
    measurement_value: Mapped[float | None] = mapped_column(Float)
    measurement_unit: Mapped[str | None] = mapped_column(String(8))  # "m2" | "m"
    measurement_crs: Mapped[str | None] = mapped_column(Text)  # projected CRS used
    measurement_status: Mapped[MeasurementStatus] = mapped_column(Enum(MeasurementStatus))
    measurement_note: Mapped[str | None] = mapped_column(Text)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
