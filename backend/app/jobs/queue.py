"""Durable job queue stored in the database.

Jobs survive restarts. A worker claims a job by taking a time-limited lease and
renews it through heartbeats. If a worker dies, the lease expires and the job
is re-queued, up to `max_attempts`. Cancellation is cooperative: the API sets
`cancel_requested` and the pipeline checks it between units of work.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Job, Scan, utcnow


class JobCancelled(Exception):
    pass


def enqueue(db: Session, scan: Scan, job_type: str = "process_scan") -> Job:
    job = Job(scan_id=scan.id, type=job_type, max_attempts=settings.job_max_attempts)
    db.add(job)
    scan.status = "queued"
    db.flush()
    return job


def recover_expired(db: Session) -> int:
    """Re-queue running jobs whose lease expired. Jobs out of attempts are marked failed."""
    now = utcnow()
    stale = db.scalars(select(Job).where(Job.status == "running", Job.lease_until < now)).all()
    for job in stale:
        if job.cancel_requested:
            job.status, job.finished_at = "cancelled", now
            _set_scan_status(db, job.scan_id, "cancelled")
        elif job.attempts >= job.max_attempts:
            job.status, job.finished_at = "failed", now
            job.error = (job.error or "") + "\nWorker stopped responding; attempts exhausted."
            _set_scan_status(db, job.scan_id, "failed", job.error)
        else:
            job.status, job.worker_id, job.lease_until = "queued", None, None
            job.stage = "requeued after worker loss"
    db.commit()
    return len(stale)


def claim_next(db: Session, worker_id: str) -> Optional[Job]:
    """Atomically claim the oldest queued job.

    The compare-and-set UPDATE guarantees that only one worker wins a job,
    even with several worker processes on one SQLite file.
    """
    recover_expired(db)
    candidate = db.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(1)).first()
    if candidate is None:
        return None
    now = utcnow()
    res = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == "queued")
        .values(
            status="running",
            worker_id=worker_id,
            attempts=Job.attempts + 1,
            lease_until=now + timedelta(seconds=settings.job_lease_s),
            started_at=now,
            stage="starting",
        )
    )
    db.commit()
    if res.rowcount != 1:
        return None
    db.expire_all()
    return db.get(Job, candidate.id)


class JobContext:
    """Handed to the pipeline: reports progress, renews the lease, checks for cancellation."""

    def __init__(self, session_factory, job_id: int, worker_id: str):
        self._sf = session_factory
        self.job_id = job_id
        self.worker_id = worker_id

    def update(self, progress: Optional[float] = None, stage: Optional[str] = None) -> None:
        with self._sf() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                raise JobCancelled("job deleted")
            if job.cancel_requested:
                raise JobCancelled("cancel requested")
            if job.worker_id != self.worker_id:
                raise JobCancelled("lease lost to another worker")
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))
            if stage is not None:
                job.stage = stage
            job.lease_until = utcnow() + timedelta(seconds=settings.job_lease_s)
            db.commit()

    check = update


class PermanentJobError(Exception):
    """A failure that retrying will not fix (bad video, limits exceeded)."""


def finish(db: Session, job_id: int, status: str, error: Optional[str] = None, retryable: bool = True) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = status
    job.error = error
    job.finished_at = utcnow()
    job.lease_until = None
    if status == "succeeded":
        job.progress = 1.0
        job.stage = "done"
    elif status == "failed" and retryable and job.attempts < job.max_attempts and not job.cancel_requested:
        # Transient failure: retry automatically.
        job.status = "queued"
        job.worker_id = None
        job.stage = "retrying"
        job.finished_at = None
    if job.status in ("failed", "cancelled"):
        _set_scan_status(db, job.scan_id, job.status, error)
    db.commit()


def request_cancel(db: Session, job: Job) -> None:
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = utcnow()
        _set_scan_status(db, job.scan_id, "cancelled")
    elif job.status == "running":
        job.cancel_requested = True
    db.commit()


def _set_scan_status(db: Session, scan_id: int, status: str, error: Optional[str] = None) -> None:
    scan = db.get(Scan, scan_id)
    if scan is not None:
        scan.status = status
        if error:
            scan.error = error


def sqlite_checkpoint(db: Session) -> None:  # pragma: no cover - maintenance helper
    db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
