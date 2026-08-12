"""Entry point: initialise the database, mount the JSON API, and (in
production) serve the built SvelteKit frontend as static files.

Run dev:   uv run uvicorn main:app --reload --port 8080   (from backend/)
Run prod:  build the frontend first (see frontend/), then `uv run main.py`.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import api
import db
from settings import Settings, setup_logging

# built SvelteKit output (adapter-static) lives here after `npm run build`
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "build"

app = FastAPI(title="Stronghold API")
app.include_router(api.router)

# dev: the SvelteKit dev server runs on :5173 and calls the API on :8080
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mount_frontend() -> None:
    """Serve the built SPA if present. Unknown non-/api paths fall back to
    index.html so client-side routing (deep links, refresh) works."""
    if not _FRONTEND_DIST.exists():
        return
    app.mount("/app", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
    index = _FRONTEND_DIST / "index.html"

    @app.get("/")
    def _root() -> FileResponse:
        return FileResponse(index)

    @app.get("/{path:path}")
    def _spa_fallback(path: str) -> FileResponse:
        candidate = _FRONTEND_DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(settings: Settings) -> FastAPI:
    db.init(Path(settings.get("db", "db_path")), settings.get("db", "export_sql"))
    # prices are kept current by the writes that change them; recomputing at
    # startup covers rows written outside the app (an import, a restored .sql)
    db.refresh_all_prices()
    _mount_frontend()
    return app


def main() -> None:
    import uvicorn

    settings = Settings(Path("settings.toml"))
    setup_logging(settings)
    logging.getLogger(__name__).info("starting up")
    create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.get("gui", "port"))  # noqa: S104


if __name__ == "__main__":
    main()
