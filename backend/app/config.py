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

    # Optional user-supplied sprite references (none are bundled)
    sprite_dir: str = field(default_factory=lambda: os.environ.get("POKEVAULT_SPRITE_DIR", ""))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = os.environ.get("POKEVAULT_DATABASE_URL", f"sqlite:///{self.data_dir / 'pokevault.db'}")
        # Replit/Heroku-style URLs -> SQLAlchemy psycopg3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefix):
                self.database_url = "postgresql+psycopg://" + self.database_url[len(prefix):]

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
