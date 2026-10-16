"""Apply Alembic migrations programmatically (used at startup and in tests)."""
from __future__ import annotations

from alembic import command
from alembic.config import Config

from .config import BACKEND_DIR


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
