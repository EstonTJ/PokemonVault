from datetime import timedelta

from app.jobs import queue
from app.models import Job, Scan, utcnow


_n = [0]


def _scan(db):
    _n[0] += 1
    s = Scan(original_filename="x.mov", sha256=f"sha-{_n[0]}", size_bytes=1)
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
    ctx = queue.JobContext(fresh_db.SessionLocal, running.id, running.lease_token)
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
        queue.finish(db, j.id, j.lease_token, "failed", "boom", retryable=True)
        assert db.get(Job, j.id).status == "queued"
        j = queue.claim_next(db, "w1")
        queue.finish(db, j.id, j.lease_token, "failed", "bad video", retryable=False)
        assert db.get(Job, j.id).status == "failed"


def test_stale_attempt_cannot_touch_reassigned_job(fresh_db):
    """Review finding 3: the old worker's cancel/finish must not affect the new worker's attempt."""
    with fresh_db.SessionLocal() as db:
        s = _scan(db)
        queue.enqueue(db, s)
        db.commit()
        first = queue.claim_next(db, "old-worker")
        old_token = first.lease_token
        first.lease_until = utcnow() - timedelta(seconds=1)  # old worker stalls; lease expires
        db.commit()
        second = queue.claim_next(db, "new-worker")
        assert second.id == first.id and second.lease_token != old_token

        # The stale worker now hits its error/cancel path and tries to write the outcome.
        assert queue.finish(db, first.id, old_token, "cancelled", "stale", retryable=False) is False
        assert queue.finish(db, first.id, old_token, "succeeded") is False
        db.expire_all()
        job = db.get(Job, first.id)
        assert job.status == "running" and job.lease_token == second.lease_token

        stale_ctx = queue.JobContext(fresh_db.SessionLocal, first.id, old_token)
        import pytest as _pytest
        with _pytest.raises(queue.StaleAttempt):
            stale_ctx.update(0.5)
        with _pytest.raises(queue.StaleAttempt):
            stale_ctx.assert_owner(db)

        # The current owner still completes normally.
        assert queue.finish(db, second.id, second.lease_token, "succeeded") is True
        assert db.get(Job, second.id).status == "succeeded"
