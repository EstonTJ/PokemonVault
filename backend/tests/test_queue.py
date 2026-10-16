from datetime import timedelta

from app.jobs import queue
from app.models import Job, Scan, utcnow


def _scan(db):
    s = Scan(original_filename="x.mov", sha256="abc", size_bytes=1)
    db.add(s)
    db.flush()
    return s


def test_claim_is_exclusive_and_lease_recovery_requeues(fresh_db):
    with fresh_db.SessionLocal() as db:
        s = _scan(db)
        queue.enqueue(db, s)
        db.commit()
        j = queue.claim_next(db, "w1")
        assert j is not None and j.status == "running" and j.attempts == 1
        assert queue.claim_next(db, "w2") is None  # nothing else queued
        # Simulate a crashed worker: lease in the past.
        j.lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
        j2 = queue.claim_next(db, "w2")
        assert j2 is not None and j2.id == j.id and j2.worker_id == "w2" and j2.attempts == 2


def test_attempts_exhausted_marks_failed(fresh_db):
    with fresh_db.SessionLocal() as db:
        s = _scan(db)
        j = queue.enqueue(db, s)
        j.max_attempts = 1
        db.commit()
        j = queue.claim_next(db, "w1")
        j.lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
        assert queue.claim_next(db, "w2") is None
        db.refresh(j)
        assert j.status == "failed"
        assert db.get(Scan, s.id).status == "failed"


def test_cancel_queued_and_running(fresh_db):
    with fresh_db.SessionLocal() as db:
        s = _scan(db)
        j = queue.enqueue(db, s)
        db.commit()
        queue.request_cancel(db, j)
        assert j.status == "cancelled"

        s2 = _scan(db)
        queue.enqueue(db, s2)
        db.commit()
        running = queue.claim_next(db, "w1")
        queue.request_cancel(db, running)
        assert running.cancel_requested
    ctx = queue.JobContext(fresh_db.SessionLocal, running.id, "w1")
    try:
        ctx.update(0.5, "x")
        raised = False
    except queue.JobCancelled:
        raised = True
    assert raised


def test_transient_failure_retries_permanent_does_not(fresh_db):
    with fresh_db.SessionLocal() as db:
        s = _scan(db)
        queue.enqueue(db, s)
        db.commit()
        j = queue.claim_next(db, "w1")
        queue.finish(db, j.id, "failed", "boom", retryable=True)
        assert db.get(Job, j.id).status == "queued"
        j = queue.claim_next(db, "w1")
        queue.finish(db, j.id, "failed", "bad video", retryable=False)
        assert db.get(Job, j.id).status == "failed"
