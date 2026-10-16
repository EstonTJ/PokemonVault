"""End-to-end processing of one uploaded recording.

The stages are streamed: frames are sampled → keyframes are selected → each
keyframe is OCR'd and read immediately, so only one frame's pixels sit in memory
at a time. Images are written to the private frame store and pruned afterwards
so only keyframes referenced by evidence remain.

Reprocessing is crash-safe. Each run is an *attempt* (identified by the job's lease token). Everything it writes
is tagged with that attempt and hidden (groups.published = false) until a single publish transaction:
  1. proves the attempt still owns the job (compare-and-set on the lease token),
  2. carries confirm/reject decisions over from the currently published results, read at that moment,
  3. deletes the previously published rows and makes the new ones visible.
Old image files are removed only after that commit. If anything fails earlier, the previous results stay intact.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
from sqlalchemy import delete, select

from . import db as dbmod
from .appraisal.bars import iv_candidates, locate_and_read, read_stars
from .catalog.grouping import ObsLite, group_observations
from .catalog.importer import auto_link_resightings, bulk_confirm_ready, decide_status, refresh_candidates
from .config import settings
from .jobs.queue import JobContext, PermanentJobError
from .models import AppSetting, Appraisal, Frame, Observation, ObservationGroup, Pokemon, PokemonEvidence, Scan, utcnow
from .ocr.engine import get_engine
from .recognition.readers import RawObservation, caption_tokens, classify, read_detail, read_grid, sanitize_group_attributes
from .recommend.gamedata import get_gamedata
from .video.frames import KeyframeSelector, VideoError, probe, sample_frames

log = logging.getLogger("pokevault.pipeline")


def frame_dir_for(scan_id: int) -> Path:
    return settings.frame_dir / str(scan_id)


def attempt_dir_for(scan_id: int, attempt_id: str) -> Path:
    return frame_dir_for(scan_id) / attempt_id


def _not_current(column, current: Optional[str]):
    """SQL condition: rows that don't belong to the published attempt."""
    return column.isnot(None) if current is None else ((column.is_(None)) | (column != current))


def process_scan(ctx: JobContext, scan_id: int) -> None:
    gd = get_gamedata()
    with dbmod.SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is None:
            raise PermanentJobError("Scan no longer exists.")
        video_path = Path(scan.video_path) if scan.video_path else None
        scan.status = "processing"
        scan.error = None
        db.commit()
    if video_path is None or not video_path.exists():
        raise PermanentJobError("The raw video is no longer stored. Upload the recording again to reprocess it.")

    ctx.update(0.01, "probing video")
    try:
        info = probe(video_path)
    except VideoError as exc:
        raise PermanentJobError(str(exc)) from exc
    if info.duration_s > settings.max_duration_s:
        raise PermanentJobError(f"Video is {info.duration_s:.0f}s long; the limit is {settings.max_duration_s}s.")
    with dbmod.SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        scan.duration_s, scan.width, scan.height = info.duration_s, info.width, info.height
        db.commit()

    aid = ctx.attempt_id
    # Remove leftovers of crashed or abandoned attempts. Published results are never touched here.
    with dbmod.SessionLocal() as db:
        current = db.get(Scan, scan_id).current_attempt_id
        leftover_paths = _delete_rows(db, scan_id, _not_current_filter(current))
        db.commit()
    _remove_files(leftover_paths)
    fdir = attempt_dir_for(scan_id, aid)
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True, exist_ok=True)

    engine = get_engine()
    selector = KeyframeSelector()
    total_samples = max(1, int(info.duration_s * settings.sample_fps))
    stats = {"sampled": 0, "keyframes": 0, "grid": 0, "detail": 0, "appraisal": 0, "other": 0, "observations": 0}
    lite: List[ObsLite] = []
    appraisal_readings: Dict[int, dict] = {}  # observation id -> bar readings

    for sf in sample_frames(video_path, info, settings.sample_fps, settings.normalized_width):
        stats["sampled"] += 1
        if sf.index % 4 == 0:
            ctx.update(0.03 + 0.85 * min(1.0, sf.index / total_samples), f"scanning video ({stats['keyframes']} key frames)")
        kf = selector.push(sf)
        if kf is None:
            continue
        if stats["keyframes"] >= settings.max_keyframes:
            break
        stats["keyframes"] += 1
        h, w = kf.image.shape[:2]
        tokens = engine.read(kf.image)
        screen = classify(tokens, w, h)
        stats[screen] += 1
        raws: List[RawObservation] = []
        bars = None
        stars = None
        if screen == "grid":
            raws = read_grid(tokens, w, h, gd)
        elif screen == "detail":
            raws = read_detail(tokens, w, h, gd)
        elif screen == "appraisal":
            raws = read_detail(tokens, w, h, gd) or []
            bars = locate_and_read(kf.image, tokens)
            stars, _ = read_stars(kf.image, tokens)
            if not raws:
                raws = [RawObservation("appraisal", (0, 0, w, h))]
            for r in raws:
                r.screen_type = "appraisal"
        if not raws:
            continue
        # Privacy: the appraisal caption says where the Pokémon was caught. Blur it in the kept image
        # and drop it from the stored OCR. Its species name was already used above.
        caps = caption_tokens(tokens, h)
        if caps:
            cy0 = int(max(0, min(t.y0 for t in caps) - 8))
            cy1 = int(min(h, max(t.y1 for t in caps) + 8))
            kf.image[cy0:cy1] = cv2.GaussianBlur(kf.image[cy0:cy1], (0, 0), 14)
            cap_ids = {id(t) for t in caps}
            tokens = [t for t in tokens if id(t) not in cap_ids]
        img_path = fdir / f"{kf.index:06d}.jpg"
        cv2.imwrite(str(img_path), kf.image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with dbmod.SessionLocal() as db:
            fr = Frame(scan_id=scan_id, idx=kf.index, timestamp_ms=kf.timestamp_ms, screen_type=screen, segment=kf.segment,
                       scroll_offset=kf.scroll_offset, width=w, height=h, image_path=str(img_path),
                       ocr=[t.to_json() for t in tokens], attempt_id=aid)
            db.add(fr)
            db.flush()
            for r in raws:
                attrs = {k: a.to_json(fr.id) for k, a in r.attrs.items()}
                content_y = (r.frame_y + kf.scroll_offset) if r.frame_y is not None else None
                ob = Observation(scan_id=scan_id, frame_id=fr.id, screen_type=r.screen_type, segment=kf.segment,
                                 grid_col=r.grid_col, content_y=content_y, bbox=list(r.bbox), attributes=attrs,
                                 attempt_id=aid)
                db.add(ob)
                db.flush()
                notes = list(r.notes)
                if bars is not None:
                    appraisal_readings[ob.id] = {"frame_id": fr.id, "bars": {k: v.to_json() for k, v in bars.items()},
                                                  "stars": stars}
                lite.append(ObsLite(key=ob.id, frame_idx=kf.index, ts_ms=kf.timestamp_ms, screen_type=r.screen_type,
                                    segment=kf.segment, col=r.grid_col, content_y=content_y, pitch=r.cell_pitch,
                                    attrs=attrs, notes=notes))
                stats["observations"] += 1
            db.commit()

    ctx.update(0.9, "grouping observations")
    drafts = group_observations(lite)

    # Stage the groups (invisible until published).
    with dbmod.SessionLocal() as db:
        obs_by_id = {o.id: o for o in db.scalars(select(Observation).where(Observation.scan_id == scan_id,
                                                                           Observation.attempt_id == aid)).all()}
        for d in drafts:
            attrs = sanitize_group_attributes(dict(d.attributes), gd)
            reasons = [r for r in d.reasons if not (r.startswith("Conflicting nickname") and "nickname" not in attrs)]
            best = max(d.obs, key=lambda o: (o.screen_type != "grid", sum(a.get("confidence", 0) for a in o.attrs.values())))
            best_ob = obs_by_id[best.key]
            g = ObservationGroup(scan_id=scan_id, track_key=d.track_key, attributes=attrs, review_reasons=reasons,
                                 observation_count=len(d.obs), first_ts_ms=min(o.ts_ms for o in d.obs),
                                 last_ts_ms=max(o.ts_ms for o in d.obs), best_frame_id=best_ob.frame_id,
                                 best_bbox=best_ob.bbox, attempt_id=aid, published=False)
            db.add(g)
            db.flush()
            for o in d.obs:
                obs_by_id[o.key].group_id = g.id
            readings = [(appraisal_readings[o.key], o) for o in d.obs if o.key in appraisal_readings]
            if readings:
                _apply_appraisal(db, g, readings, gd, aid)
        db.commit()

    ctx.update(0.95, "publishing results")
    stats.update({"ocr_engine": engine.name, "gamedata": gd.timestamp})
    old_paths = publish_attempt(ctx, scan_id, stats)
    _remove_files(old_paths)
    _remove_stale_attempt_dirs(scan_id, keep=aid)
    discard_scan_media(scan_id, keep_video_if_requested=True, frames_too=False)


def publish_attempt(ctx: JobContext, scan_id: int, stats: dict) -> List[str]:
    """Atomically replace the scan's published results with this attempt's staged results.

    Returns image paths of the replaced attempt, to delete only after the commit succeeded.
    """
    aid = ctx.attempt_id
    with dbmod.SessionLocal() as db:
        ctx.assert_owner(db)  # takes the write lock; a stale attempt stops here and writes nothing
        scan = db.get(Scan, scan_id)
        staged = db.scalars(select(ObservationGroup).where(ObservationGroup.scan_id == scan_id,
                                                          ObservationGroup.attempt_id == aid)).all()

        # Decisions on the currently published results, read now, so a confirm/reject made while this
        # attempt was processing is carried over too.
        decisions: Dict[str, Tuple[Optional[str], str]] = {}
        for g in db.scalars(select(ObservationGroup).where(ObservationGroup.scan_id == scan_id,
                                                          ObservationGroup.published.is_(True))).all():
            if g.status == "confirmed" and g.pokemon_id:
                decisions[g.track_key] = (g.pokemon_id, "confirmed")
            elif g.status == "rejected":
                decisions[g.track_key] = (None, "rejected")

        for g in staged:
            prior = decisions.get(g.track_key)
            if prior and prior[1] == "confirmed" and db.get(Pokemon, prior[0]) is not None:
                g.pokemon_id, g.status, g.review_reasons, g.link_method = prior[0], "confirmed", [], "reimport"
                db.add(PokemonEvidence(pokemon_id=prior[0], group_id=g.id, scan_id=scan_id, frame_id=g.best_frame_id,
                                       kind="reimport"))
            elif prior and prior[1] == "rejected":
                g.status = "rejected"
            else:
                refresh_candidates(db, g)
                g.status = decide_status(g)
        db.flush()

        auto_confirmed = auto_linked = 0
        auto = db.get(AppSetting, "auto_confirm_ready")
        if auto is not None and auto.value:
            # Re-sightings first, so a confident new item can't be mistaken for an existing one.
            auto_linked = len(auto_link_resightings(db, scan_id, attempt_id=aid))
            auto_confirmed = len(bulk_confirm_ready(db, scan_id, via="auto", attempt_id=aid))

        # Swap: drop the previous published rows, reveal the staged ones.
        old_paths = _delete_rows(db, scan_id, _not_current_filter(aid))
        for g in staged:
            g.published = True
        scan.current_attempt_id = aid
        pruned = _prune_unreferenced_frames(db, scan_id, aid)

        pending = sum(1 for g in staged if g.status in ("needs_review", "ready"))
        stats.update({"groups": len(staged), "pending": pending,
                      "needs_review": sum(1 for g in staged if g.status == "needs_review"),
                      "relinked": sum(1 for g in staged if g.link_method == "reimport"),
                      "auto_confirmed": auto_confirmed, "auto_linked": auto_linked})
        if stats.get("observations", 0) == 0:
            stats["message"] = ("No Pokémon storage, detail or appraisal screens were recognised. "
                                "Record the Pokémon storage grid or detail pages with the iPhone screen recorder.")
        scan.summary = stats
        scan.status = "review" if pending else "done"
        scan.error = None
        scan.frames_retained = db.query(Frame).filter(Frame.scan_id == scan_id, Frame.attempt_id == aid,
                                                       Frame.image_path.isnot(None)).count()
        db.commit()
    return old_paths + pruned


def _apply_appraisal(db, g: ObservationGroup, readings, gd, attempt_id: Optional[str] = None) -> None:
    from .appraisal.bars import BarReading

    def strength(r):
        bars = r[0]["bars"]
        return min((bars[k].get("confidence") or 0) for k in ("atk", "def", "sta"))

    best, _ = max(readings, key=strength)
    # Stars animate in; across this Pokémon's frames the highest count is the most complete.
    star_vals = [r[0].get("stars") for r in readings if r[0].get("stars") is not None]
    stars = max(star_vals) if star_vals else None
    br = {k: BarReading(**v) for k, v in best["bars"].items()}
    attrs = dict(g.attributes or {})
    sp = gd.get((attrs.get("species_id") or {}).get("value"))
    cp = (attrs.get("cp") or {}).get("value")
    hp = (attrs.get("hp") or {}).get("value")
    cands, status, conf, reasons = iv_candidates(br, sp, cp, hp, stars)

    # If the form was uncertain (or the bars don't fit it), let the appraisal decide between forms:
    # keep the form only if it is the single one whose CP/HP math agrees with the bars.
    form_conf = (attrs.get("form") or {}).get("confidence", 1.0)
    if sp is not None and cp is not None and (form_conf < 0.75 or status != "verified"):
        fits = []
        for alt in gd.by_base_name.get(sp.base_name.upper(), []):
            c2, s2, conf2, r2 = iv_candidates(br, alt, cp, hp, stars)
            if s2 == "verified":
                fits.append((alt, c2, s2, conf2, r2))
        if len(fits) == 1 and (fits[0][0].species_id != sp.species_id or form_conf < 0.75):
            alt, cands, status, conf, reasons = fits[0]
            m = {"confidence": 0.9, "method": "appraisal+cp/hp", "frame_id": best["frame_id"]}
            attrs["species_id"] = {**m, "value": alt.species_id}
            attrs["dex"] = {**m, "value": alt.dex}
            attrs["form"] = {**m, "value": alt.form}
            reasons = [f"Form resolved by appraisal: only {alt.display_name} fits the bars, CP and HP."] + list(reasons)
            g.review_reasons = [r for r in (g.review_reasons or []) if not r.startswith(("Form could not", "CP/HP are not"))]

    db.add(Appraisal(scan_id=g.scan_id, frame_id=best["frame_id"], group_id=g.id, bars=best["bars"], stars=stars,
                     iv_candidates=[list(c) for c in cands[:200]], status=status, confidence=conf,
                     attempt_id=attempt_id))
    if stars is not None:
        attrs["stars"] = {"value": stars, "confidence": 0.9, "method": "appraisal:badge", "frame_id": best["frame_id"]}
    if status == "verified":
        attrs["ivs"] = {"value": list(cands[0]), "confidence": conf, "method": "appraisal:verified", "frame_id": best["frame_id"]}
    elif cands:
        attrs["iv_candidates"] = {"value": [list(c) for c in cands[:64]], "confidence": conf, "method": "appraisal:ambiguous",
                                  "frame_id": best["frame_id"]}
        g.review_reasons = list(g.review_reasons or []) + ["Appraisal ambiguous: " + " ".join(reasons)]
    else:
        g.review_reasons = list(g.review_reasons or []) + ["Appraisal failed: " + " ".join(reasons)]
    g.attributes = attrs


def _not_current_filter(current: Optional[str]):
    return lambda model: _not_current(model.attempt_id, current)


def _only_attempt_filter(attempt_id: str):
    return lambda model: model.attempt_id == attempt_id


def _delete_rows(db, scan_id: int, where) -> List[str]:
    """Delete one or more attempts' rows (selected by `where(model)`). Returns their image paths."""
    paths = [p for (p,) in db.execute(select(Frame.image_path).where(Frame.scan_id == scan_id, where(Frame))) if p]
    group_ids = select(ObservationGroup.id).where(ObservationGroup.scan_id == scan_id, where(ObservationGroup))
    db.execute(delete(Appraisal).where(Appraisal.scan_id == scan_id, where(Appraisal)))
    db.execute(delete(Observation).where(Observation.scan_id == scan_id, where(Observation)))
    db.execute(delete(PokemonEvidence).where(PokemonEvidence.group_id.in_(group_ids)))
    db.execute(delete(ObservationGroup).where(ObservationGroup.scan_id == scan_id, where(ObservationGroup)))
    db.execute(delete(Frame).where(Frame.scan_id == scan_id, where(Frame)))
    return paths


def _remove_files(paths: List[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _remove_stale_attempt_dirs(scan_id: int, keep: str) -> None:
    root = frame_dir_for(scan_id)
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name != keep:
            shutil.rmtree(child, ignore_errors=True)
        elif child.is_file():  # images from before per-attempt folders
            child.unlink(missing_ok=True)


def _prune_unreferenced_frames(db, scan_id: int, attempt_id: str) -> List[str]:
    """Keep only this attempt's frames used as best evidence or holding appraisal readings.

    Returns image paths to delete after commit.
    """
    keep = {fid for (fid,) in db.execute(select(ObservationGroup.best_frame_id).where(
        ObservationGroup.scan_id == scan_id, ObservationGroup.attempt_id == attempt_id)) if fid}
    keep |= {fid for (fid,) in db.execute(select(Appraisal.frame_id).where(
        Appraisal.scan_id == scan_id, Appraisal.attempt_id == attempt_id)) if fid}
    paths = []
    for fr in db.scalars(select(Frame).where(Frame.scan_id == scan_id, Frame.attempt_id == attempt_id)).all():
        if fr.id not in keep:
            if fr.image_path:
                paths.append(fr.image_path)
            fr.image_path = None
            fr.ocr = []  # drop text of frames nobody references (may include nicknames)
    return paths


def discard_attempt(scan_id: int, attempt_id: Optional[str]) -> None:
    """Throw away an unpublished attempt (failed, cancelled or stale). Never touches published results."""
    if not attempt_id:
        return
    with dbmod.SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is None or scan.current_attempt_id == attempt_id:
            return
        paths = _delete_rows(db, scan_id, _only_attempt_filter(attempt_id))
        db.commit()
    _remove_files(paths)
    shutil.rmtree(attempt_dir_for(scan_id, attempt_id), ignore_errors=True)


def discard_scan_media(scan_id: int, keep_video_if_requested: bool = True, frames_too: bool = False) -> None:
    with dbmod.SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        if scan is None:
            return
        if scan.video_path and not (keep_video_if_requested and scan.keep_video):
            try:
                os.remove(scan.video_path)
            except FileNotFoundError:
                pass
            scan.video_path = None
            scan.video_deleted_at = utcnow()
        if frames_too:
            shutil.rmtree(frame_dir_for(scan_id), ignore_errors=True)
            for fr in db.scalars(select(Frame).where(Frame.scan_id == scan_id)).all():
                fr.image_path = None
            scan.frames_retained = 0
        db.commit()


def sweep_stale_media(max_age_h: int = 24) -> int:
    from datetime import timedelta

    cutoff = utcnow() - timedelta(hours=max_age_h)
    n = 0
    with dbmod.SessionLocal() as db:
        stale = db.scalars(select(Scan).where(Scan.status.in_(("failed", "cancelled")), Scan.video_path.isnot(None),
                                              Scan.keep_video.is_(False), Scan.created_at < cutoff)).all()
        ids = [s.id for s in stale]
    for sid in ids:
        discard_scan_media(sid, keep_video_if_requested=True)
        n += 1
    return n
