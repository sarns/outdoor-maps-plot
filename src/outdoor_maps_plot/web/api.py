"""FastAPI routes for uploads, rendering, downloads, and operations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, File, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from outdoor_maps_plot.gpx import PointBudget
from outdoor_maps_plot.options import NAMED_PAPER_SIZES, PosterConfig
from outdoor_maps_plot.styles import PROVIDERS, STYLES
from outdoor_maps_plot.web.config import HARD_MAX_FILES, WebSettings
from outdoor_maps_plot.web.errors import ApiError
from outdoor_maps_plot.web.jobs import TERMINAL_STATES, JobManager, RenderJob, event_json
from outdoor_maps_plot.web.schemas import (
    ArtifactResponse,
    DeleteResponse,
    FileSummary,
    HealthResponse,
    ProgressResponse,
    RenderAccepted,
    RenderRequest,
    RenderStatusResponse,
    RouteSummary,
    UploadResponse,
    UploadTotals,
)
from outdoor_maps_plot.web.storage import UploadRecord, WorkspaceStore

router = APIRouter()


def _state(request: Request) -> tuple[WebSettings, WorkspaceStore, JobManager]:
    return request.app.state.settings, request.app.state.storage, request.app.state.jobs


def upload_response(record: UploadRecord) -> UploadResponse:
    routes = [
        RouteSummary(
            name=route.name,
            point_count=len(route.points),
            distance_km=round(route.distance_km, 3),
            ascent_m=round(route.ascent_m, 1),
        )
        for route in record.routes
    ]
    return UploadResponse(
        upload_id=record.upload_id,
        files=[
            FileSummary(
                display_name=item.display_name,
                size_bytes=item.size_bytes,
                route_count=item.route_count,
            )
            for item in record.files
        ],
        summary=UploadTotals(
            route_count=len(routes),
            point_count=sum(route.point_count for route in routes),
            distance_km=round(sum(route.distance_km for route in routes), 3),
            ascent_m=round(sum(route.ascent_m for route in routes), 1),
        ),
        routes=routes,
        expires_at=record.expires_at,
    )


def render_response(job: RenderJob) -> RenderStatusResponse:
    artifact = None
    if job.status == "succeeded" and job.artifact_path is not None:
        artifact = ArtifactResponse(
            filename=job.artifact_path.name,
            media_type=cast(str, job.artifact_media_type),
            size_bytes=cast(int, job.artifact_size),
            download_url=f"/api/renders/{job.job_id}/download",
        )
    return RenderStatusResponse(
        job_id=job.job_id,
        upload_id=job.upload_id,
        mode=job.mode,
        status=job.status,
        progress=ProgressResponse(
            phase=job.progress.phase,
            percent=job.progress.percent,
            message=job.progress.message,
            updated_at=job.progress.updated_at,
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        expires_at=job.expires_at,
        artifact=artifact,
        error=job.error,
    )


@router.get("/api/config")
def get_config(request: Request) -> dict[str, object]:
    settings, _, _ = _state(request)
    schema = PosterConfig.model_json_schema()
    return {
        "defaults": PosterConfig().model_dump(mode="json"),
        "poster_schema": schema,
        "paper_sizes": list(NAMED_PAPER_SIZES),
        "styles": [
            {
                "id": name,
                "label": style.label,
                "paper": style.paper,
                "ink": style.ink,
                "muted": style.muted,
                "route": style.route,
                "halo": style.halo,
                "default_provider": style.provider,
            }
            for name, style in STYLES.items()
        ],
        "providers": [
            {
                "id": provider,
                "configured": settings.provider_credentials[provider],
            }
            for provider in PROVIDERS
        ],
        "limits": {
            "max_files": settings.max_files,
            "hard_max_files": HARD_MAX_FILES,
            "max_file_bytes": settings.max_file_bytes,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_points": settings.max_points_total,
            "max_tiles": settings.max_tiles,
        },
        "output_formats": ["pdf", "png", "jpeg"],
    }


@router.post("/api/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def create_upload(
    request: Request,
    files: Annotated[list[UploadFile], File(description="One to fifteen GPX files")],
) -> UploadResponse:
    settings, storage, _ = _state(request)
    if not files:
        raise ApiError(422, "missing_files", "Select at least one GPX file.")
    if len(files) > settings.max_files:
        raise ApiError(
            413,
            "too_many_files",
            f"A maximum of {settings.max_files} GPX files may be uploaded.",
            [{"limit": settings.max_files}],
        )
    upload_id, workspace = storage.create_workspace()
    stored = []
    routes = []
    aggregate_bytes = 0
    point_budget = PointBudget(settings.max_points_total)
    try:
        for index, upload in enumerate(files, start=1):
            item, parsed, aggregate_bytes = storage.store_file(
                workspace,
                upload.file,
                upload.filename,
                index,
                aggregate_bytes,
                point_budget,
            )
            stored.append(item)
            routes.extend(parsed)
        return upload_response(storage.register(upload_id, workspace, stored, routes))
    except Exception:
        # A partially accepted upload must never leave an accessible workspace.
        from shutil import rmtree

        rmtree(workspace, ignore_errors=True)
        raise
    finally:
        for upload in files:
            upload.file.close()


@router.get("/api/uploads/{upload_id}", response_model=UploadResponse)
def get_upload(request: Request, upload_id: str) -> UploadResponse:
    _, storage, _ = _state(request)
    return upload_response(storage.get(upload_id))


@router.delete("/api/uploads/{upload_id}", response_model=DeleteResponse)
def delete_upload(request: Request, upload_id: str) -> DeleteResponse:
    _, storage, jobs = _state(request)
    storage.get(upload_id)
    storage.request_deletion(upload_id)
    had_active = jobs.cancel_upload(upload_id)
    if upload_id not in jobs.active_upload_ids():
        storage.delete(upload_id)
        return DeleteResponse(id=upload_id, status="deleted")
    return DeleteResponse(
        id=upload_id,
        status="cancellation_requested" if had_active else "deleted",
    )


@router.post("/api/renders", response_model=RenderAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_render(request: Request, payload: RenderRequest) -> RenderAccepted:
    settings, storage, jobs = _state(request)
    upload = storage.get(payload.upload_id)
    if payload.config.max_tiles > settings.max_tiles:
        # Server limit is an authoritative cap, not a caller-visible failure.
        config = payload.config.model_copy(update={"max_tiles": settings.max_tiles})
    else:
        config = payload.config
    job = jobs.submit(upload, payload.mode, config)
    return RenderAccepted(
        job_id=job.job_id,
        status="queued",
        status_url=f"/api/renders/{job.job_id}",
        events_url=f"/api/renders/{job.job_id}/events",
    )


@router.get("/api/renders/{job_id}", response_model=RenderStatusResponse)
def get_render(request: Request, job_id: str) -> RenderStatusResponse:
    _, _, jobs = _state(request)
    return render_response(jobs.get(job_id))


@router.get("/api/renders/{job_id}/events")
async def render_events(request: Request, job_id: str) -> StreamingResponse:
    _, _, jobs = _state(request)
    jobs.get(job_id)

    async def events() -> AsyncIterator[str]:
        last = ""
        while True:
            if await request.is_disconnected():
                break
            try:
                job = jobs.get(job_id)
            except ApiError as exc:
                yield f"event: error\ndata: {json.dumps({'code': exc.code})}\n\n"
                break
            payload = event_json(job)
            if payload != last:
                yield f"event: progress\ndata: {payload}\n\n"
                last = payload
            if job.status in TERMINAL_STATES:
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/renders/{job_id}/download")
def download_render(request: Request, job_id: str) -> FileResponse:
    _, storage, jobs = _state(request)
    job = jobs.get(job_id)
    if job.status != "succeeded" or job.artifact_path is None:
        raise ApiError(409, "render_not_ready", "The render is not ready for download.")
    try:
        artifact = job.artifact_path.resolve()
        artifact.relative_to(storage.root)
    except (OSError, ValueError) as exc:
        raise ApiError(500, "storage_error", "The render artifact is unavailable.") from exc
    if not artifact.is_file():
        raise ApiError(410, "artifact_expired", "The render artifact has expired.")
    return FileResponse(
        artifact,
        media_type=job.artifact_media_type,
        filename=artifact.name,
    )


@router.delete("/api/renders/{job_id}", response_model=DeleteResponse)
def delete_render(request: Request, job_id: str) -> DeleteResponse:
    _, _, jobs = _state(request)
    deleted = jobs.delete(job_id)
    return DeleteResponse(
        id=job_id,
        status="deleted" if deleted else "cancellation_requested",
    )


@router.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
def readiness(request: Request) -> HealthResponse:
    _, storage, jobs = _state(request)
    if storage.is_ready() and jobs.accepts_work():
        return HealthResponse(status="ready")
    raise ApiError(503, "not_ready", "The application is not ready.")
