"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from outdoor_maps_plot.service import render_poster
from outdoor_maps_plot.web.api import router
from outdoor_maps_plot.web.config import WebSettings
from outdoor_maps_plot.web.errors import error_payload, install_error_handlers
from outdoor_maps_plot.web.jobs import JobManager, RenderCallable
from outdoor_maps_plot.web.storage import WorkspaceStore


def _render_relief(*args, **kwargs):
    """Import the optional relief pipeline only when a relief job is run."""
    from outdoor_maps_plot.relief_service import render_relief

    return render_relief(*args, **kwargs)


def _static_version(static_root: Path) -> str:
    """Return a stable content fingerprint for browser cache busting."""
    digest = hashlib.sha256()
    for path in sorted(item for item in static_root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(static_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def create_app(
    settings: WebSettings | None = None,
    render_service: RenderCallable = render_poster,
    relief_render_service: RenderCallable | None = _render_relief,
) -> FastAPI:
    resolved_settings = settings or WebSettings.from_env()
    web_root = Path(__file__).resolve().parent
    template_root = web_root / "templates"
    static_root = web_root / "static"
    static_version = _static_version(static_root)
    storage = WorkspaceStore(resolved_settings)
    jobs = JobManager(
        resolved_settings,
        storage,
        render_service,
        relief_render_service,
    )

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(resolved_settings.cleanup_interval_seconds)
            await asyncio.to_thread(jobs.cleanup_expired)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            await asyncio.to_thread(jobs.shutdown)

    app = FastAPI(
        title="Outdoor Maps Plot",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.storage = storage
    app.state.jobs = jobs
    app.state.templates = Jinja2Templates(directory=template_root)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin.rstrip("/") != expected.rstrip("/"):
                return JSONResponse(
                    status_code=403,
                    content=error_payload(
                        "origin_rejected", "Cross-origin requests are not allowed."
                    ),
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'"
        )
        if request.url.path == "/":
            # Revalidate the document so deployments can advertise new asset URLs.
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/static/"):
            if request.query_params.get("v") == static_version:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                # Legacy, unversioned asset URLs must not become stale again.
                response.headers["Cache-Control"] = "no-cache"
        return response

    install_error_handlers(app)
    app.include_router(router)
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request):
        index_path = template_root / "index.html"
        if index_path.is_file():
            return app.state.templates.TemplateResponse(
                request,
                "index.html",
                {"static_version": static_version},
            )
        return HTMLResponse(
            "<!doctype html><html><head><title>Outdoor Maps Plot</title></head>"
            "<body><main><h1>Outdoor Maps Plot</h1>"
            "<p>The web interface is being installed. The API is ready.</p></main></body></html>"
        )

    return app


app = create_app()


def main() -> None:
    """Run the single-process web application."""
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("OMP_HOST", "127.0.0.1"),
        port=int(os.getenv("OMP_PORT", "8000")),
    )
