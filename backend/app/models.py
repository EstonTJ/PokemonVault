"""SQLAlchemy models. See ARCHITECTURE.md §3 for the rationale behind each table.

Three-state booleans (shiny, shadow, ...) use NULL for "unknown"; never coerce
NULL to False.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (
        # One canonical scan per recording (demo fixtures share a placeholder hash and are exempt).
        Index("uq_scans_sha256_real", "sha256", unique=True,
              sqlite_where=text("is_demo = 0"), postgresql_where=text("is_demo = false")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    original_filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="auto")  # collection | appraisal | auto
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|processing|review|done|failed|cancelled
    keep_video: Mapped[bool] = mapped_column(Boolean, default=False)
    video_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    video_deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frames_retained: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The processing attempt whose results are visible. NULL = results written before attempts existed.
    current_attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    frames: Mapped[list["Frame"]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    groups: Mapped[list["ObservationGroup"]] = relationship(back_populates="scan", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("uq_jobs_one_active_per_scan", "scan_id", unique=True,
              sqlite_where=text("status IN ('queued', 'running')"),
              postgresql_where=text("status IN ('queued', 'running')")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32), default="process_scan")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # New random token on every claim. Only the holder of the current token may report progress, publish
    # results or finish the job, so a worker whose lease was reassigned can't overwrite the new attempt.
    lease_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="jobs")


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    screen_type: Mapped[str] = mapped_column(String(16), default="other")
    segment: Mapped[int] = mapped_column(Integer, default=0)
    scroll_offset: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ocr: Mapped[list] = mapped_column(JSON, default=list)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    scan: Mapped[Scan] = relationship(back_populates="frames")


class ObservationGroup(Base):
    __tablename__ = "observation_groups"
    __table_args__ = (UniqueConstraint("scan_id", "attempt_id", "track_key", name="uq_group_attempt_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    track_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="needs_review")  # needs_review|ready|confirmed|rejected
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    candidate_pokemon_ids: Mapped[list] = mapped_column(JSON, default=list)
    pokemon_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pokemon.id", ondelete="SET NULL"), nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    first_ts_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_ts_ms: Mapped[int] = mapped_column(Integer, default=0)
    best_frame_id: Mapped[Optional[int]] = mapped_column(ForeignKey("frames.id", ondelete="SET NULL"), nullable=True)
    best_bbox: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # How the group was resolved: review | bulk | auto | auto-link | reimport
    link_method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    # Staged results of an in-progress attempt are invisible until the attempt is published.
    published: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    scan: Mapped[Scan] = relationship(back_populates="groups")
    observations: Mapped[list["Observation"]] = relationship(back_populates="group")


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("observation_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    screen_type: Mapped[str] = mapped_column(String(16))
    segment: Mapped[int] = mapped_column(Integer, default=0)
    grid_col: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    group: Mapped[Optional[ObservationGroup]] = relationship(back_populates="observations")


class Pokemon(Base):
    __tablename__ = "pokemon"
    # Basic invariants enforced by the database, whatever code path writes the row.
    __table_args__ = (
        CheckConstraint("cp IS NULL OR (cp BETWEEN 10 AND 6000)", name="ck_pokemon_cp"),
        CheckConstraint("hp IS NULL OR (hp BETWEEN 10 AND 1000)", name="ck_pokemon_hp"),
        CheckConstraint("iv_atk IS NULL OR (iv_atk BETWEEN 0 AND 15)", name="ck_pokemon_iv_atk"),
        CheckConstraint("iv_def IS NULL OR (iv_def BETWEEN 0 AND 15)", name="ck_pokemon_iv_def"),
        CheckConstraint("iv_sta IS NULL OR (iv_sta BETWEEN 0 AND 15)", name="ck_pokemon_iv_sta"),
        CheckConstraint("iv_status IN ('unknown', 'range', 'verified')", name="ck_pokemon_iv_status"),
        CheckConstraint("iv_status != 'verified' OR (iv_atk IS NOT NULL AND iv_def IS NOT NULL AND iv_sta IS NOT NULL)",
                        name="ck_pokemon_verified_ivs_present"),
        CheckConstraint("level IS NULL OR (level BETWEEN 1 AND 51)", name="ck_pokemon_level"),
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="ck_pokemon_weight"),
        CheckConstraint("height_m IS NULL OR height_m > 0", name="ck_pokemon_height"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    species_id: Mapped[str] = mapped_column(String(64), index=True)
    dex: Mapped[int] = mapped_column(Integer, index=True)
    form: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cp: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hp: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    iv_atk: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    iv_def: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    iv_sta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    iv_status: Mapped[str] = mapped_column(String(16), default="unknown")  # unknown|range|verified
    iv_candidates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Random per individual and unchanged by power-ups: a strong identity fingerprint.
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    height_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    shiny: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    shadow: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    purified: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    lucky: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    favorite: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_via: Mapped[str] = mapped_column(String(16), default="review")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    evidence: Mapped[list["PokemonEvidence"]] = relationship(back_populates="pokemon", cascade="all, delete-orphan")


class PokemonEvidence(Base):
    __tablename__ = "pokemon_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pokemon_id: Mapped[str] = mapped_column(ForeignKey("pokemon.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("observation_groups.id", ondelete="SET NULL"), nullable=True)
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"), nullable=True)
    frame_id: Mapped[Optional[int]] = mapped_column(ForeignKey("frames.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="observation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    pokemon: Mapped[Pokemon] = relationship(back_populates="evidence")


class Appraisal(Base):
    __tablename__ = "appraisals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[Optional[int]] = mapped_column(ForeignKey("frames.id", ondelete="SET NULL"), nullable=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("observation_groups.id", ondelete="SET NULL"), nullable=True)
    pokemon_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pokemon.id", ondelete="SET NULL"), nullable=True)
    bars: Mapped[dict] = mapped_column(JSON, default=dict)
    stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    iv_candidates: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="ambiguous")  # verified|ambiguous|failed
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(32))
    old: Mapped[Any] = mapped_column(JSON, nullable=True)
    new: Mapped[Any] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class GamedataVersion(Base):
    __tablename__ = "gamedata_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(255))
    source_timestamp: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UploadSession(Base):
    """A resumable, chunked upload in progress. The file is assembled in place under private/partial/."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    # name + size + last-modified of the browser's File: lets a reload pick the same session back up.
    fingerprint: Mapped[str] = mapped_column(String(320), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_size: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="auto")
    keep_video: Mapped[bool] = mapped_column(Boolean, default=False)
    reprocess: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | held | complete | aborted
    part_path: Mapped[str] = mapped_column(String(512))
    scan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"), nullable=True)

    chunks: Mapped[list["UploadChunk"]] = relationship(cascade="all, delete-orphan")


class UploadChunk(Base):
    """One received chunk. The (upload, index) primary key makes re-sending a chunk idempotent."""

    __tablename__ = "upload_chunks"

    upload_id: Mapped[str] = mapped_column(ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True)
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
