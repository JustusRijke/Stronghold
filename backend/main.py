"""Entry point: initialise the database, mount the JSON API, and (in
production) serve the built SvelteKit frontend as static files.

Run dev:   uv run uvicorn main:app --reload --port 8080   (from backend/)
Run prod:  build the frontend first (see frontend/), then `uv run main.py`.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import api
import db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from settings import Settings, setup_logging
from version import APP_VERSION, SCHEMA_VERSION

# built SvelteKit output (adapter-static) lives here after `npm run build`
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "build"

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Write the data file out once more on the way down.

    Normally a no-op: every write exports (and commits) as it happens, so a
    clean stop -- and even a SIGKILL -- loses nothing. It is here so that a
    stop is always a known-good write, whatever left the database dirty."""
    yield
    if db._engine is None:  # startup failed before db.init
        return
    _log.info("shutting down: writing %s", db._export_path)
    db.export()


app = FastAPI(title="Stronghold API", version=APP_VERSION, lifespan=_lifespan)
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


def _report_startup(settings: Settings, data_file: Path) -> None:
    """Say which version, settings file, data file and log file are in use."""
    log = logging.getLogger(__name__)
    log.info("Stronghold %s (data schema version %s)", APP_VERSION, SCHEMA_VERSION)
    log.info("settings: %s", settings.path)
    log.info("data file: %s", data_file.resolve())
    log.info("log file: %s", settings.path_of("logging", "file").resolve())


def create_app(settings: Settings) -> FastAPI:
    api.settings = settings
    data_file = settings.path_of("db", "data_file")
    _report_startup(settings, data_file)
    db.init(data_file, settings.get("db", "auto_commit"))
    # prices are kept current by the writes that change them; recomputing at
    # startup covers rows written outside the app (an import, a restored .sql)
    db.refresh_all_prices()
    _mount_frontend()
    return app


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Stronghold server.")
    parser.add_argument(
        "settings",
        type=Path,
        help="settings.toml to use; paths inside it are relative to its directory",
    )
    args = parser.parse_args()
    settings = Settings(args.settings)
    setup_logging(settings)
    logging.getLogger(__name__).info("starting up")
    try:
        create_app(settings)
    except db.DataVersionError as error:
        # a wrong Stronghold for this data: the reason is the whole message,
        # and a traceback would only bury it
        logging.getLogger(__name__).error("%s", error)
        raise SystemExit(1) from None
    uvicorn.run(app, host="0.0.0.0", port=settings.get("gui", "port"))  # noqa: S104


if __name__ == "__main__":
    main()
