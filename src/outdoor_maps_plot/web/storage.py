"""Filesystem workspaces and in-memory upload metadata."""

from __future__ import annotations

import re
import secrets
import shutil
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import BinaryIO

from outdoor_maps_plot.gpx import (
    SUPPORTED_ROUTE_EXTENSIONS,
    GpxError,
    PointBudget,
    Route,
    parse_route_file,
)
from outdoor_maps_plot.web.config import WebSettings
from outdoor_maps_plot.web.errors import ApiError

CHUNK_SIZE = 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier() -> str:
    return secrets.token_urlsafe(24)


def _display_name(filename: str | None, index: int) -> str:
    raw = (filename or f"route-{index}.gpx").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
    return (cleaned or f"route-{index}.gpx")[:180]


def contained_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ApiError(500, "storage_error", "Stored artifact path is invalid.") from exc
    return resolved_candidate


@dataclass(frozen=True)
class StoredFile:
    display_name: str
    path: Path
    size_bytes: int
    route_count: int


@dataclass
class UploadRecord:
    upload_id: str
    workspace: Path
    files: list[StoredFile]
    routes: list[Route]
    created_at: datetime
    expires_at: datetime
    deletion_requested: bool = False

    @property
    def point_count(self) -> int:
        return sum(len(route.points) for route in self.routes)


@dataclass
class WorkspaceStore:
    settings: WebSettings
    uploads: dict[str, UploadRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.root = self.settings.job_root.resolve()
        self.cache = self.settings.cache_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)

    def create_workspace(self) -> tuple[str, Path]:
        for _ in range(10):
            upload_id = _identifier()
            workspace = self.root / upload_id
            try:
                (workspace / "input").mkdir(parents=True, exist_ok=False)
                (workspace / "preview").mkdir()
                (workspace / "renders").mkdir()
            except FileExistsError:
                continue
            return upload_id, workspace
        raise ApiError(500, "storage_error", "Could not allocate an upload workspace.")

    def store_file(
        self,
        workspace: Path,
        stream: BinaryIO,
        filename: str | None,
        index: int,
        aggregate_bytes: int,
        point_budget: PointBudget,
    ) -> tuple[StoredFile, list[Route], int]:
        display_name = _display_name(filename, index)
        suffix = PurePath(display_name).suffix.lower()
        if suffix not in SUPPORTED_ROUTE_EXTENSIONS:
            raise ApiError(
                422,
                "invalid_file_type",
                "Only files with a .gpx or .fit extension are accepted.",
                [{"file": display_name}],
            )
        format_name = suffix[1:].upper()
        destination = contained_path(self.root, workspace / "input" / f"{index:03d}{suffix}")
        size = 0
        try:
            with destination.open("xb") as target:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    aggregate_bytes += len(chunk)
                    if size > self.settings.max_file_bytes:
                        raise ApiError(
                            413,
                            "file_too_large",
                            f"One {format_name} file exceeds the allowed size.",
                            [{"file": display_name, "limit_bytes": self.settings.max_file_bytes}],
                        )
                    if aggregate_bytes > self.settings.max_upload_bytes:
                        raise ApiError(
                            413,
                            "upload_too_large",
                            "The combined upload exceeds the allowed size.",
                            [{"limit_bytes": self.settings.max_upload_bytes}],
                        )
                    target.write(chunk)
            try:
                routes = parse_route_file(
                    destination,
                    max_points=self.settings.max_points_total,
                    point_budget=point_budget,
                    default_name=PurePath(display_name).stem,
                )
            except GpxError as exc:
                if point_budget.remaining == 0:
                    raise ApiError(
                        413,
                        "too_many_points",
                        f"The uploaded {format_name} data contains too many points.",
                        [{"limit": self.settings.max_points_total}],
                    ) from exc
                raise ApiError(
                    422,
                    f"invalid_{format_name.lower()}",
                    f"One {format_name} file could not be parsed.",
                    [{"file": display_name}],
                ) from exc
            if not routes:
                raise ApiError(
                    422,
                    f"invalid_{format_name.lower()}",
                    f"One {format_name} file contains no usable positioned tracks or routes.",
                    [{"file": display_name}],
                )
            return StoredFile(display_name, destination, size, len(routes)), routes, aggregate_bytes
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def register(
        self,
        upload_id: str,
        workspace: Path,
        files: list[StoredFile],
        routes: list[Route],
    ) -> UploadRecord:
        point_count = sum(len(route.points) for route in routes)
        if point_count > self.settings.max_points_total:
            raise ApiError(
                413,
                "too_many_points",
                "The uploaded route data contains too many points.",
                [{"limit": self.settings.max_points_total}],
            )
        now = utc_now()
        record = UploadRecord(
            upload_id=upload_id,
            workspace=workspace,
            files=files,
            routes=routes,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.job_ttl_seconds),
        )
        with self._lock:
            self.uploads[upload_id] = record
        return record

    def get(self, upload_id: str) -> UploadRecord:
        with self._lock:
            record = self.uploads.get(upload_id)
        if record is None:
            raise ApiError(404, "upload_not_found", "The upload was not found or has expired.")
        if record.expires_at <= utc_now():
            self.delete(upload_id)
            raise ApiError(410, "upload_expired", "The upload has expired.")
        return record

    def render_directory(self, upload_id: str, job_id: str, mode: str) -> Path:
        record = self.get(upload_id)
        base = record.workspace / ("preview" if mode == "preview" else f"renders/{job_id}")
        path = contained_path(self.root, base)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def request_deletion(self, upload_id: str) -> None:
        record = self.get(upload_id)
        record.deletion_requested = True

    def delete(self, upload_id: str) -> bool:
        with self._lock:
            record = self.uploads.pop(upload_id, None)
        if record is None:
            return False
        workspace = contained_path(self.root, record.workspace)
        if workspace != self.root:
            shutil.rmtree(workspace, ignore_errors=True)
        return True

    def cleanup_expired(self, active_upload_ids: set[str]) -> list[str]:
        now = utc_now()
        with self._lock:
            expired = [
                upload_id
                for upload_id, record in self.uploads.items()
                if upload_id not in active_upload_ids
                and (record.expires_at <= now or record.deletion_requested)
            ]
        for upload_id in expired:
            self.delete(upload_id)
        return expired

    def is_ready(self) -> bool:
        try:
            for directory in (self.root, self.cache):
                probe = contained_path(directory, directory / f".ready-{_identifier()}")
                probe.write_bytes(b"")
                probe.unlink()
            return True
        except OSError:
            return False
