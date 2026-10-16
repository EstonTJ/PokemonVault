"""FastAPI application: API routes, worker lifecycle, and the built SPA (same origin, no CORS)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from .api import collection, scans, system, uploads
from .auth import init_auth
from .config import settings
from .jobs.worker import WorkerThread
from .migrate import upgrade

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


log = logging.getLogger("pokevault.startup")


class StartupError(RuntimeError):
    pass


def check_database() -> None:
    """Fail fast, with a readable reason, if the database can't be reached (instead of hanging)."""
    from sqlalchemy import create_engine, text

    from .db import connect_args_for

    eng = create_engine(settings.database_url, connect_args=connect_args_for(settings.database_url))
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report any driver error the same way
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise StartupError(
            f"Can't connect to the {settings.database_kind} database at {settings.database_url_redacted}: {first_line}. "
            "Check that the database exists and is running, and that DATABASE_URL (or POKEVAULT_DATABASE_URL) "
            "is set for this deployment.") from None
    finally:
        eng.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting PokéVault: database %s (%s), data folder %s",
             settings.database_kind, settings.database_url_redacted, settings.data_dir)
    settings.ensure_dirs()
    if not settings.storage_persistent:
        logging.getLogger("pokevault").error(
            "STORAGE IS NOT PERSISTENT: this is a Replit deployment (or POKEVAULT_REQUIRE_PERSISTENT_DB is set) "
            "using a SQLite file, which is erased on every redeploy. Attach PostgreSQL and set DATABASE_URL "
            "(or POKEVAULT_DATABASE_URL). Uploads are refused until then%s.",
            "" if not settings.allow_ephemeral_storage else " (overridden by POKEVAULT_ALLOW_EPHEMERAL_STORAGE)")
    try:
        log.info("Connecting to the database (timeout %ss)...", settings.db_connect_timeout_s)
        check_database()
        log.info("Database reachable. Applying migrations...")
        upgrade(settings.database_url)
        log.info("Database schema up to date.")
    except StartupError as exc:
        log.error("STARTUP FAILED: %s", exc)
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("STARTUP FAILED while migrating the database: %s: %s", type(exc).__name__,
                  str(exc).strip().splitlines()[0] if str(exc).strip() else "")
        raise
    init_auth()
    worker = None
    if settings.run_worker_in_process:
        worker = WorkerThread()
        worker.start()
    log.info("PokéVault is ready.")
    yield
    if worker:
        worker.stop()


app = FastAPI(title="PokéVault", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(system.public)
app.include_router(system.router)
app.include_router(scans.upload_router)
app.include_router(uploads.router)
app.include_router(scans.router)
app.include_router(collection.router)


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    # The database rejected a write that broke a data rule (constraint backstop). Don't leak SQL details.
    return JSONResponse({"detail": "That change conflicts with existing data or breaks a data rule."}, status_code=409)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


dist = settings.frontend_dist
if (dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    candidate = (dist / path).resolve()
    if path and candidate.is_file() and dist.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.exists():
        # Always revalidate the app shell so a new deploy is picked up; hashed assets can cache forever.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=503)
