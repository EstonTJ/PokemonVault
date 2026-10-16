"""Job worker. Runs in-process (a daemon thread) by default, or standalone:

    python -m app.jobs.worker
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback

from .. import db as dbmod
from . import queue

log = logging.getLogger("pokevault.worker")


def _handlers():
    from ..pipeline import process_scan

    return {"process_scan": process_scan}


def run_one(worker_id: str) -> bool:
    """Claim and run a single job. Returns True when a job was processed."""
    with dbmod.SessionLocal() as db:
        job = queue.claim_next(db, worker_id)
        if job is None:
            return False
        job_id, scan_id, job_type, token = job.id, job.scan_id, job.type, job.lease_token
    ctx = queue.JobContext(dbmod.SessionLocal, job_id, token)
    handler = _handlers().get(job_type)
    try:
        if handler is None:
            raise queue.PermanentJobError(f"Unknown job type {job_type!r}")
        handler(ctx, scan_id)
        with dbmod.SessionLocal() as db:
            queue.finish(db, job_id, token, "succeeded")
    except queue.StaleAttempt as exc:
        # Another attempt owns the job now: write nothing about it, just drop this attempt's staged work.
        log.warning("job %s: abandoning stale attempt (%s)", job_id, exc)
        _discard_attempt(scan_id, token)
    except queue.JobCancelled as exc:
        log.info("job %s cancelled: %s", job_id, exc)
        with dbmod.SessionLocal() as db:
            queue.finish(db, job_id, token, "cancelled", str(exc), retryable=False)
        _discard_attempt(scan_id, token)
    except queue.PermanentJobError as exc:
        log.warning("job %s failed permanently: %s", job_id, exc)
        with dbmod.SessionLocal() as db:
            queue.finish(db, job_id, token, "failed", str(exc), retryable=False)
        _discard_attempt(scan_id, token)
    except Exception as exc:  # noqa: BLE001 - surface any pipeline error to the user
        log.error("job %s crashed: %s", job_id, traceback.format_exc())
        with dbmod.SessionLocal() as db:
            queue.finish(db, job_id, token, "failed", f"{type(exc).__name__}: {exc}", retryable=True)
        _discard_attempt(scan_id, token)
    return True


def _discard_attempt(scan_id: int, attempt_id: str) -> None:
    from ..pipeline import discard_attempt

    try:
        discard_attempt(scan_id, attempt_id)
    except Exception:  # noqa: BLE001 - cleanup must never mask the original outcome
        log.error("cleanup of attempt %s failed: %s", attempt_id, traceback.format_exc())


def sweep() -> None:
    """Delete raw videos left behind by failed/cancelled scans (kept briefly so Retry works)."""
    from ..pipeline import sweep_stale_media

    sweep_stale_media()


class WorkerThread(threading.Thread):
    def __init__(self, poll_s: float = 1.0):
        super().__init__(daemon=True, name="pokevault-worker")
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        self.poll_s = poll_s
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        log.info("worker %s started", self.worker_id)
        last_sweep = 0.0
        while not self._halt.is_set():
            if time.time() - last_sweep > 600:
                last_sweep = time.time()
                try:
                    sweep()
                except Exception:  # noqa: BLE001
                    log.error("sweep failed: %s", traceback.format_exc())
            try:
                worked = run_one(self.worker_id)
            except Exception:  # noqa: BLE001
                log.error("worker loop error: %s", traceback.format_exc())
                worked = False
            if not worked:
                self._halt.wait(self.poll_s)


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    from ..config import settings
    from ..migrate import upgrade

    settings.ensure_dirs()
    upgrade(settings.database_url)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        if not run_one(worker_id):
            time.sleep(1.0)


if __name__ == "__main__":  # pragma: no cover
    main()
