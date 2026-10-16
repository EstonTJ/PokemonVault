"""Runtime configuration. Every limit is overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("POKEVAULT_DATA_DIR", BACKEND_DIR / "var")))
    database_url: str = ""
    gamemaster_path: Path = field(default_factory=lambda: Path(os.environ.get("POKEVAULT_GAMEMASTER", BACKEND_DIR / "data" / "gamemaster.json")))
    frontend_dist: Path = field(default_factory=lambda: Path(os.environ.get("POKEVAULT_FRONTEND_DIST", BACKEND_DIR.parent / "frontend" / "dist")))

    # Auth
    password: str = field(default_factory=lambda: os.environ.get("POKEVAULT_PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("POKEVAULT_SECRET_KEY", ""))
    secure_cookies: bool = field(default_factory=lambda: _env_bool("POKEVAULT_SECURE_COOKIES", False))
    session_max_age_s: int = field(default_factory=lambda: _env_int("POKEVAULT_SESSION_MAX_AGE_S", 14 * 24 * 3600))

    # Upload / processing limits
    max_upload_mb: int = field(default_factory=lambda: _env_int("POKEVAULT_MAX_UPLOAD_MB", 1024))
    upload_chunk_mb: int = field(default_factory=lambda: _env_int("POKEVAULT_UPLOAD_CHUNK_MB", 8))
    upload_session_ttl_h: int = field(default_factory=lambda: _env_int("POKEVAULT_UPLOAD_SESSION_TTL_H", 24))
    max_open_upload_sessions: int = field(default_factory=lambda: _env_int("POKEVAULT_MAX_OPEN_UPLOADS", 3))
    # A just-uploaded recording that turns out to be a duplicate is kept this long so "Rescan" needn't re-upload it.
    upload_hold_minutes: int = field(default_factory=lambda: _env_int("POKEVAULT_UPLOAD_HOLD_MINUTES", 60))
    max_duration_s: int = field(default_factory=lambda: _env_int("POKEVAULT_MAX_DURATION_S", 900))
    max_uploads_per_hour: int = field(default_factory=lambda: _env_int("POKEVAULT_MAX_UPLOADS_PER_HOUR", 20))
    max_keyframes: int = field(default_factory=lambda: _env_int("POKEVAULT_MAX_KEYFRAMES", 400))
    sample_fps: float = field(default_factory=lambda: _env_float("POKEVAULT_SAMPLE_FPS", 4.0))
    normalized_width: int = field(default_factory=lambda: _env_int("POKEVAULT_NORMALIZED_WIDTH", 720))

    # Jobs
    run_worker_in_process: bool = field(default_factory=lambda: _env_bool("POKEVAULT_INPROCESS_WORKER", True))
    job_lease_s: int = field(default_factory=lambda: _env_int("POKEVAULT_JOB_LEASE_S", 120))
    job_max_attempts: int = field(default_factory=lambda: _env_int("POKEVAULT_JOB_MAX_ATTEMPTS", 2))

    # Recognition thresholds
    ready_confidence: float = field(default_factory=lambda: _env_float("POKEVAULT_READY_CONFIDENCE", 0.85))

    # Game data older than this many days makes rankings provisional
    gamedata_stale_days: int = field(default_factory=lambda: _env_int("POKEVAULT_GAMEDATA_STALE_DAYS", 45))

    db_connect_timeout_s: int = field(default_factory=lambda: _env_int("POKEVAULT_DB_CONNECT_TIMEOUT_S", 10))
    db_lock_timeout_s: int = field(default_factory=lambda: _env_int("POKEVAULT_DB_LOCK_TIMEOUT_S", 60))

    # Storage safety. Replit sets REPLIT_DEPLOYMENT=1 in deployments, whose disk is reset on every redeploy,
    # so a SQLite file there silently loses everything. Other ephemeral hosts can opt in with
    # POKEVAULT_REQUIRE_PERSISTENT_DB=1. POKEVAULT_ALLOW_EPHEMERAL_STORAGE=1 overrides (for throwaway demos).
    on_replit_deployment: bool = field(default_factory=lambda: os.environ.get("REPLIT_DEPLOYMENT") == "1")
    require_persistent_db: bool = field(default_factory=lambda: _env_bool("POKEVAULT_REQUIRE_PERSISTENT_DB", False))
    allow_ephemeral_storage: bool = field(default_factory=lambda: _env_bool("POKEVAULT_ALLOW_EPHEMERAL_STORAGE", False))

    # Optional user-supplied sprite references (none are bundled)
    sprite_dir: str = field(default_factory=lambda: os.environ.get("POKEVAULT_SPRITE_DIR", ""))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            # POKEVAULT_DATABASE_URL wins; otherwise use DATABASE_URL, which Replit (and most hosts) set
            # automatically when a PostgreSQL database is attached; otherwise a local SQLite file.
            self.database_url = (os.environ.get("POKEVAULT_DATABASE_URL") or os.environ.get("DATABASE_URL")
                                 or f"sqlite:///{self.data_dir / 'pokevault.db'}")
        # Replit/Heroku-style URLs -> SQLAlchemy psycopg3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefix):
                self.database_url = "postgresql+psycopg://" + self.database_url[len(prefix):]

    @property
    def database_url_redacted(self) -> str:
        from sqlalchemy.engine import make_url

        try:
            return make_url(self.database_url).render_as_string(hide_password=True)
        except Exception:  # noqa: BLE001 - never leak a malformed URL that might contain a password
            return self.database_kind + "://(unparseable URL)"

    @property
    def database_kind(self) -> str:
        return "sqlite" if self.database_url.startswith("sqlite") else self.database_url.split(":", 1)[0].split("+")[0]

    @property
    def storage_persistent(self) -> bool:
        """False when the database would be erased by the host (e.g. SQLite inside a Replit deployment)."""
        ephemeral_host = self.on_replit_deployment or self.require_persistent_db
        return not (ephemeral_host and self.database_kind == "sqlite")

    @property
    def storage_writable(self) -> bool:
        """Whether new recordings may be accepted. Refuse when they would be silently lost."""
        return self.storage_persistent or self.allow_ephemeral_storage

    def storage_status(self) -> dict:
        ephemeral_host = self.on_replit_deployment or self.require_persistent_db
        return {
            "persistent": self.storage_persistent,
            "accepting_uploads": self.storage_writable,
            "database": self.database_kind,
            "ephemeral_host": ephemeral_host,
            # Without a fixed secret, a redeploy signs everyone out (harmless, but surprising).
            "secret_key_configured": bool(self.secret_key) or not ephemeral_host,
            # Evidence images and partial uploads live on disk; on an ephemeral host they don't survive redeploys.
            "files_survive_redeploy": not ephemeral_host,
        }

    @property
    def private_dir(self) -> Path:
        return self.data_dir / "private"

    @property
    def upload_dir(self) -> Path:
        return self.private_dir / "uploads"

    @property
    def frame_dir(self) -> Path:
        return self.private_dir / "frames"

    @property
    def upload_chunk_bytes(self) -> int:
        return self.upload_chunk_mb * 1024 * 1024

    @property
    def partial_dir(self) -> Path:
        return self.private_dir / "partial"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.private_dir, self.upload_dir, self.frame_dir, self.partial_dir):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass


settings = Settings()
