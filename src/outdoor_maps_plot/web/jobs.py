"""Bounded, in-process render job execution."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from outdoor_maps_plot.options import PosterConfig
from outdoor_maps_plot.poster import PosterError
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.relief_service import ReliefError
from outdoor_maps_plot.service import (
    CancellationToken,
    ProgressEvent,
    RenderCancelled,
    RenderResult,
)
from outdoor_maps_plot.web.config import WebSettings
from outdoor_maps_plot.web.errors import ApiError
from outdoor_maps_plot.web.schemas import ErrorBody, JobStatus, ProductKind, RenderMode
from outdoor_maps_plot.web.storage import UploadRecord, WorkspaceStore, _identifier, utc_now

RenderCallable = Callable[..., RenderResult]
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "expired"}
LOGGER = logging.getLogger(__name__)


@dataclass
class RenderJob:
    job_id: str
    upload_id: str
    mode: RenderMode
    product_kind: ProductKind
    config: PosterConfig | ReliefConfig
    status: JobStatus
    progress: ProgressEvent
    created_at: datetime
    expires_at: datetime
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact_path: Path | None = None
    artifact_media_type: str | None = None
    artifact_size: int | None = None
    error: ErrorBody | None = None


class JobManager:
    def __init__(
        self,
        settings: WebSettings,
        storage: WorkspaceStore,
        poster_render_service: RenderCallable,
        relief_render_service: RenderCallable | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.poster_render_service = poster_render_service
        self.relief_render_service = relief_render_service
        self._lock = threading.RLock()
        self._capacity = threading.BoundedSemaphore(
            settings.max_concurrent_jobs + settings.max_queued_jobs
        )
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_jobs,
            thread_name_prefix="map-render",
        )
        self._jobs: dict[str, RenderJob] = {}
        self._futures: dict[str, Future[None]] = {}
        self._closed = False

    def submit(
        self,
        upload: UploadRecord,
        mode: RenderMode,
        product_kind: ProductKind,
        requested_config: PosterConfig | ReliefConfig,
    ) -> RenderJob:
        config = self._effective_config(mode, product_kind, requested_config)
        if self._closed or not self._capacity.acquire(blocking=False):
            raise ApiError(
                429,
                "render_queue_full",
                "The render queue is full. Try again after another job finishes.",
            )
        job_id = _identifier()
        now = utc_now()
        job = RenderJob(
            job_id=job_id,
            upload_id=upload.upload_id,
            mode=mode,
            product_kind=product_kind,
            config=config,
            status="queued",
            progress=ProgressEvent("validating", 0, "Waiting to render", now),
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.job_ttl_seconds),
        )
        with self._lock:
            self._jobs[job_id] = job
        try:
            # Keep the state lock until the future is registered. A very fast
            # renderer can otherwise finish before its Future is recorded.
            with self._lock:
                future = self._executor.submit(self._run, job, upload)
                self._futures[job_id] = future
        except Exception:
            self._capacity.release()
            with self._lock:
                self._jobs.pop(job_id, None)
            raise
        return job

    def _effective_config(
        self,
        mode: RenderMode,
        product_kind: ProductKind,
        config: PosterConfig | ReliefConfig,
    ) -> PosterConfig | ReliefConfig:
        if product_kind == "relief":
            if not isinstance(config, ReliefConfig):
                raise TypeError("relief jobs require ReliefConfig")
            return config
        if not isinstance(config, PosterConfig):
            raise TypeError("poster jobs require PosterConfig")
        max_tiles = min(config.max_tiles, self.settings.max_tiles)
        updates: dict[str, object] = {"max_tiles": max_tiles}
        if mode == "preview":
            updates.update(
                output_format="png",
                dpi=96,
                basemap_width=min(config.basemap_width, 1200),
                max_tiles=min(max_tiles, 100),
            )
        return config.model_copy(update=updates)

    def _run(self, job: RenderJob, upload: UploadRecord) -> None:
        try:
            if job.cancellation.cancelled:
                raise RenderCancelled("Render cancelled")
            with self._lock:
                job.status = "running"
                job.started_at = utc_now()
                job.progress = ProgressEvent("validating", 1, "Starting render", utc_now())
            render_dir = self.storage.render_directory(job.upload_id, job.job_id, job.mode)
            suffix = "jpg" if job.config.output_format == "jpeg" else job.config.output_format
            destination = render_dir / f"{job.product_kind}-{job.job_id}.{suffix}"
            renderer = (
                self.relief_render_service
                if job.product_kind == "relief"
                else self.poster_render_service
            )
            if renderer is None:
                raise RuntimeError("The 3D relief renderer is not installed.")
            result = renderer(
                upload.routes,
                destination,
                self.storage.cache,
                job.config,
                progress=lambda event: self._progress(job, event),
                cancellation=job.cancellation,
            )
            artifact = result.path.resolve()
            try:
                artifact.relative_to(render_dir.resolve())
            except ValueError as exc:
                raise RuntimeError("renderer returned an artifact outside its workspace") from exc
            if job.cancellation.cancelled:
                artifact.unlink(missing_ok=True)
                raise RenderCancelled("Render cancelled")
            with self._lock:
                job.artifact_path = artifact
                job.artifact_media_type = result.media_type
                job.artifact_size = result.size_bytes
                job.status = "succeeded"
                ready_message = (
                    "3D relief ready" if job.product_kind == "relief" else "Poster ready"
                )
                if result.warnings:
                    ready_message = f"{ready_message} — {' '.join(result.warnings)}"
                job.progress = ProgressEvent("finalizing", 100, ready_message, utc_now())
        except RenderCancelled:
            with self._lock:
                job.status = "cancelled"
                job.progress = ProgressEvent("finalizing", 100, "Render cancelled", utc_now())
        except (PosterError, ReliefError) as exc:
            LOGGER.warning(
                "Poster render rejected for job %s: %s",
                job.job_id,
                exc,
            )
            with self._lock:
                job.status = "failed"
                job.error = ErrorBody(
                    code="relief_error" if job.product_kind == "relief" else "poster_error",
                    message=str(exc),
                )
                job.progress = ProgressEvent("finalizing", 100, "Render failed", utc_now())
        except Exception as exc:
            LOGGER.exception("Map render failed for job %s", job.job_id, exc_info=exc)
            with self._lock:
                job.status = "failed"
                job.error = ErrorBody(
                    code="render_failed",
                    message=(
                        "The 3D relief could not be rendered."
                        if job.product_kind == "relief"
                        else "The poster could not be rendered."
                    ),
                )
                job.progress = ProgressEvent("finalizing", 100, "Render failed", utc_now())
        finally:
            with self._lock:
                job.finished_at = utc_now()
                job.expires_at = job.finished_at + timedelta(seconds=self.settings.job_ttl_seconds)
                self._futures.pop(job.job_id, None)
            self._capacity.release()
            self._delete_upload_if_idle(job.upload_id)

    def _progress(self, job: RenderJob, event: ProgressEvent) -> None:
        job.cancellation.raise_if_cancelled()
        poster_messages = {
            "validating": "Validating poster configuration",
            "parsing": "Preparing routes",
            "fetching_map": "Preparing topographic map",
            "drawing": "Drawing poster",
            "rasterizing": "Preparing image output",
            "finalizing": "Finalizing poster",
        }
        relief_messages = {
            "validating": "Validating 3D relief configuration",
            "parsing": "Preparing routes",
            "fetching_elevation": "Preparing elevation data",
            "fetching_water": "Loading lakes and rivers",
            "building_mesh": "Building printable terrain",
            "validating_mesh": "Checking printable geometry",
            "packaging": "Packaging 3MF model",
            "finalizing": "Finalizing 3D relief",
        }
        messages = relief_messages if job.product_kind == "relief" else poster_messages
        fallback = "Rendering 3D relief" if job.product_kind == "relief" else "Rendering poster"
        safe_message = messages.get(event.phase, fallback)
        with self._lock:
            previous = job.progress.percent
            percent = max(previous, min(100, max(0, event.percent)))
            job.progress = ProgressEvent(event.phase, percent, safe_message, utc_now())

    def get(self, job_id: str) -> RenderJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, "render_not_found", "The render was not found or has expired.")
        if job.expires_at <= utc_now() and job.status in TERMINAL_STATES:
            self.expire(job_id)
            raise ApiError(410, "render_expired", "The render has expired.")
        return job

    def cancel(self, job_id: str) -> RenderJob:
        job = self.get(job_id)
        job.cancellation.cancel()
        with self._lock:
            future = self._futures.get(job_id)
            if job.status == "queued" and future is not None and future.cancel():
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.progress = ProgressEvent("finalizing", 100, "Render cancelled", utc_now())
                self._futures.pop(job_id, None)
                self._capacity.release()
        self._delete_artifact(job)
        self._delete_upload_if_idle(job.upload_id)
        return job

    def cancel_upload(self, upload_id: str) -> bool:
        active = False
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.upload_id == upload_id]
        for job in jobs:
            if job.status not in TERMINAL_STATES:
                active = True
                self.cancel(job.job_id)
        return active

    def delete(self, job_id: str) -> bool:
        job = self.cancel(job_id)
        if job.status == "running":
            return False
        self._delete_artifact(job)
        with self._lock:
            self._jobs.pop(job_id, None)
        return True

    def _delete_artifact(self, job: RenderJob) -> None:
        if job.artifact_path is None:
            return
        try:
            artifact = job.artifact_path.resolve()
            artifact.relative_to(self.storage.root)
        except (OSError, ValueError):
            return
        artifact.unlink(missing_ok=True)
        job.artifact_path = None

    def expire(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            job.status = "expired"
            self._delete_artifact(job)

    def cleanup_expired(self) -> None:
        now = utc_now()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in TERMINAL_STATES and job.expires_at <= now
            ]
        for job_id in expired:
            self.expire(job_id)
        self.storage.cleanup_expired(self.active_upload_ids())

    def active_upload_ids(self) -> set[str]:
        with self._lock:
            return {
                job.upload_id for job in self._jobs.values() if job.status not in TERMINAL_STATES
            }

    def _delete_upload_if_idle(self, upload_id: str) -> None:
        with self._lock:
            active = any(
                job.upload_id == upload_id and job.status not in TERMINAL_STATES
                for job in self._jobs.values()
            )
        record = self.storage.uploads.get(upload_id)
        if not active and record is not None and record.deletion_requested:
            self.storage.delete(upload_id)

    def accepts_work(self) -> bool:
        if self._closed or not self._capacity.acquire(blocking=False):
            return False
        self._capacity.release()
        return True

    def shutdown(self) -> None:
        self._closed = True
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status not in TERMINAL_STATES:
                job.cancellation.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)


def event_json(job: RenderJob) -> str:
    return json.dumps(
        {
            "job_id": job.job_id,
            "product_kind": job.product_kind,
            "status": job.status,
            "progress": {
                "phase": job.progress.phase,
                "percent": job.progress.percent,
                "message": job.progress.message,
                "updated_at": job.progress.updated_at.isoformat(),
            },
            "error": job.error.model_dump(mode="json") if job.error is not None else None,
        }
    )
