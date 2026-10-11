from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.schemas import (
    FeatureList,
    FeatureOut,
    FileInfo,
    FileList,
    MeasurementList,
    MeasurementOut,
    MeasurementSummary,
)
from app.services.processing import UnsupportedFileType, detect_file_type, process_upload

router = APIRouter(prefix="/api/files", tags=["files"])

SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0)]
CHUNK = 1024 * 1024


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileInfo,
    responses={
        400: {"description": "Empty upload"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported file extension"},
        422: {"model": FileInfo, "description": "File was stored but could not be processed"},
    },
)
async def upload_file(request: Request, session: SessionDep, file: Annotated[UploadFile, File()]):
    """Upload a `.zip` (Shapefile), `.kml` or `.kmz` file. It is processed synchronously."""
    settings = request.app.state.settings
    filename = file.filename or "upload"
    try:
        detect_file_type(filename)
    except UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    data = await _read_limited(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty.")

    # Parsing, reprojection and DB writes are blocking (CPU + I/O). Running them in the
    # threadpool keeps the event loop free to serve other requests meanwhile.
    record, info = await run_in_threadpool(_process, session, settings, filename, data)
    if record.status == FileStatus.FAILED:
        return JSONResponse(status_code=422, content=info.model_dump(mode="json"))
    return info


@router.get("/", response_model=FileList)
def list_files(session: SessionDep, limit: Limit = 50, offset: Offset = 0):
    total = session.scalar(select(func.count()).select_from(UploadedFile))
    rows = session.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    # One grouped query for the whole page instead of one query per file.
    counts = _geometry_type_counts(session, [r.id for r in rows])
    return FileList(
        count=total,
        limit=limit,
        offset=offset,
        results=[_file_info(session, r, counts.get(r.id, {})) for r in rows],
    )


@router.get("/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, session: SessionDep):
    return _file_info(session, _get_file_or_404(session, file_id))


@router.get("/{file_id}/features/", response_model=FeatureList)
def list_features(file_id: str, session: SessionDep, limit: Limit = 100, offset: Offset = 0):
    record = _get_completed_or_409(session, file_id)
    rows = _features(session, file_id, limit, offset)
    return FeatureList(
        file_id=file_id,
        count=record.feature_count,
        limit=limit,
        offset=offset,
        results=[
            FeatureOut(
                feature_id=f.index,
                source_id=f.source_id,
                geometry_type=f.geometry_type,
                crs=record.crs,
                geometry=f.geometry,
                properties=f.properties,
            )
            for f in rows
        ],
    )


@router.get("/{file_id}/measurements/", response_model=MeasurementList)
def list_measurements(file_id: str, session: SessionDep, limit: Limit = 100, offset: Offset = 0):
    record = _get_completed_or_409(session, file_id)
    rows = _features(session, file_id, limit, offset)
    return MeasurementList(
        file_id=file_id,
        crs=record.crs,
        summary=_summary(session, file_id),
        count=record.feature_count,
        limit=limit,
        offset=offset,
        results=[
            MeasurementOut(
                feature_id=f.index,
                source_id=f.source_id,
                geometry_type=f.geometry_type,
                status=f.measurement_status,
                measurement=f.measurement_type,
                value=f.measurement_value,
                unit=f.measurement_unit,
                measured_in_crs=f.measurement_crs,
                note=f.measurement_note,
            )
            for f in rows
        ],
    )


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    chunks, size = [], 0
    while chunk := await file.read(CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {max_bytes} byte limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _get_file_or_404(session: Session, file_id: str) -> UploadedFile:
    record = session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found.")
    return record


def _get_completed_or_409(session: Session, file_id: str) -> UploadedFile:
    record = _get_file_or_404(session, file_id)
    if record.status != FileStatus.COMPLETED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File is {record.status.value}: {record.error or 'not processed yet'}",
        )
    return record


def _features(session: Session, file_id: str, limit: int, offset: int) -> list[Feature]:
    return session.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.index)
        .limit(limit)
        .offset(offset)
    ).all()


def _process(
    session: Session, settings: Settings, filename: str, data: bytes
) -> tuple[UploadedFile, FileInfo]:
    record = process_upload(session, settings, filename, data)
    return record, _file_info(session, record)


def _geometry_type_counts(session: Session, file_ids: list[str]) -> dict[str, dict[str, int]]:
    if not file_ids:
        return {}
    rows = session.execute(
        select(Feature.file_id, Feature.geometry_type, func.count())
        .where(Feature.file_id.in_(file_ids))
        .group_by(Feature.file_id, Feature.geometry_type)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for file_id, geometry_type, n in rows:
        counts.setdefault(file_id, {})[geometry_type or "None"] = n
    return counts


def _file_info(
    session: Session, record: UploadedFile, geometry_types: dict[str, int] | None = None
) -> FileInfo:
    info = FileInfo.model_validate(record)
    if geometry_types is None:
        geometry_types = _geometry_type_counts(session, [record.id]).get(record.id, {})
    info.geometry_types = geometry_types
    return info


def _summary(session: Session, file_id: str) -> MeasurementSummary:
    totals = dict(
        session.execute(
            select(Feature.measurement_type, func.coalesce(func.sum(Feature.measurement_value), 0))
            .where(Feature.file_id == file_id, Feature.measurement_status == MeasurementStatus.OK)
            .group_by(Feature.measurement_type)
        ).all()
    )
    by_status = session.execute(
        select(Feature.measurement_status, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.measurement_status)
    ).all()
    return MeasurementSummary(
        total_area_m2=round(totals.get("area", 0.0), 3),
        total_length_m=round(totals.get("length", 0.0), 3),
        by_status={s.value: n for s, n in by_status},
    )
