"""End-to-end processing of one uploaded recording.

The stages are streamed: frames are sampled → keyframes are selected → each
keyframe is OCR'd and read immediately, so only one frame's pixels sit in memory
at a time. Images are written to the private frame store and pruned afterwards
so only keyframes referenced by evidence remain.
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

    # Snapshot confirmed links so reprocessing the same recording is idempotent.
    prior_links: Dict[str, Tuple[str, str]] = {}
    with dbmod.SessionLocal() as db:
        for g in db.scalars(select(ObservationGroup).where(ObservationGroup.scan_id == scan_id)).all():
            if g.pokemon_id and g.status == "confirmed":
                prior_links[g.track_key] = (g.pokemon_id, g.status)
            elif g.status == "rejected":
                prior_links[g.track_key] = ("", "rejected")
        _clear_scan_results(db, scan_id)
        db.commit()

    fdir = frame_dir_for(scan_id)
    if fdir.exists():
        shutil.rmtree(fdir)
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
                       ocr=[t.to_json() for t in tokens])
            db.add(fr)
            db.flush()
            for r in raws:
                attrs = {k: a.to_json(fr.id) for k, a in r.attrs.items()}
                content_y = (r.frame_y + kf.scroll_offset) if r.frame_y is not None else None
                ob = Observation(scan_id=scan_id, frame_id=fr.id, screen_type=r.screen_type, segment=kf.segment,
                                 grid_col=r.grid_col, content_y=content_y, bbox=list(r.bbox), attributes=attrs)
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

    with dbmod.SessionLocal() as db:
        obs_by_id = {o.id: o for o in db.scalars(select(Observation).where(Observation.scan_id == scan_id)).all()}
        key_to_group: Dict[str, ObservationGroup] = {}
        for d in drafts:
            attrs = sanitize_group_attributes(dict(d.attributes), gd)
            reasons = [r for r in d.reasons if not (r.startswith("Conflicting nickname") and "nickname" not in attrs)]
            best = max(d.obs, key=lambda o: (o.screen_type != "grid", sum(a.get("confidence", 0) for a in o.attrs.values())))
            best_ob = obs_by_id[best.key]
            g = ObservationGroup(scan_id=scan_id, track_key=d.track_key, attributes=attrs, review_reasons=reasons,
                                 observation_count=len(d.obs), first_ts_ms=min(o.ts_ms for o in d.obs),
                                 last_ts_ms=max(o.ts_ms for o in d.obs), best_frame_id=best_ob.frame_id, best_bbox=best_ob.bbox)
            db.add(g)
            db.flush()
            key_to_group[d.track_key] = g
            for o in d.obs:
                obs_by_id[o.key].group_id = g.id
            # Appraisal readings for this individual.
            readings = [(appraisal_readings[o.key], o) for o in d.obs if o.key in appraisal_readings]
            if readings:
                _apply_appraisal(db, g, readings, gd)

        for d in drafts:
            g = key_to_group[d.track_key]
            prior = prior_links.get(d.track_key)
            if prior and prior[1] == "confirmed" and db.get(Pokemon, prior[0]) is not None:
                g.pokemon_id, g.status = prior[0], "confirmed"
                g.review_reasons = []
                g.link_method = "reimport"
                db.add(PokemonEvidence(pokemon_id=prior[0], group_id=g.id, scan_id=scan_id, frame_id=g.best_frame_id, kind="reimport"))
                continue
            if prior and prior[1] == "rejected":
                g.status = "rejected"
                continue
            refresh_candidates(db, g)
            g.status = decide_status(g)

        db.flush()
        auto = db.get(AppSetting, "auto_confirm_ready")
        auto_confirmed = 0
        auto_linked = 0
        if auto is not None and auto.value:
            # Re-sightings first, so a confident new item can't be mistaken for an existing one.
            auto_linked = len(auto_link_resightings(db, scan_id))
            # Only "ready" groups: high confidence on species and CP, no review reasons, no identity candidates.
            auto_confirmed = len(bulk_confirm_ready(db, scan_id, via="auto"))

        _prune_unreferenced_frames(db, scan_id)
        scan = db.get(Scan, scan_id)
        db.flush()
        pending = sum(1 for g in key_to_group.values() if g.status in ("needs_review", "ready"))
        stats.update({"groups": len(drafts), "pending": pending,
                      "needs_review": sum(1 for g in key_to_group.values() if g.status == "needs_review"),
                      "relinked": sum(1 for g in key_to_group.values() if g.link_method == "reimport"),
                      "auto_confirmed": auto_confirmed, "auto_linked": auto_linked,
                      "ocr_engine": engine.name, "gamedata": gd.timestamp})
        if stats["observations"] == 0:
            stats["message"] = ("No Pokémon storage, detail or appraisal screens were recognised. "
                                "Record the Pokémon storage grid or detail pages with the iPhone screen recorder.")
        scan.summary = stats
        scan.status = "review" if pending else "done"
        scan.frames_retained = db.query(Frame).filter(Frame.scan_id == scan_id).count()
        db.commit()

    ctx.update(0.98, "cleaning up")
    discard_scan_media(scan_id, keep_video_if_requested=True, frames_too=False)


def _apply_appraisal(db, g: ObservationGroup, readings, gd) -> None:
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
                     iv_candidates=[list(c) for c in cands[:200]], status=status, confidence=conf))
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


def _clear_scan_results(db, scan_id: int) -> None:
    db.execute(delete(Appraisal).where(Appraisal.scan_id == scan_id))
    db.execute(delete(Observation).where(Observation.scan_id == scan_id))
    group_ids = select(ObservationGroup.id).where(ObservationGroup.scan_id == scan_id)
    db.execute(delete(PokemonEvidence).where(PokemonEvidence.group_id.in_(group_ids)))
    db.execute(delete(ObservationGroup).where(ObservationGroup.scan_id == scan_id))
    db.execute(delete(Frame).where(Frame.scan_id == scan_id))


def _prune_unreferenced_frames(db, scan_id: int) -> None:
    """Keep only frames that some group uses as its best evidence or that hold appraisal readings."""
    keep = {fid for (fid,) in db.execute(select(ObservationGroup.best_frame_id).where(ObservationGroup.scan_id == scan_id)) if fid}
    keep |= {fid for (fid,) in db.execute(select(Appraisal.frame_id).where(Appraisal.scan_id == scan_id)) if fid}
    for fr in db.scalars(select(Frame).where(Frame.scan_id == scan_id)).all():
        if fr.id not in keep:
            if fr.image_path and os.path.exists(fr.image_path):
                os.remove(fr.image_path)
            fr.image_path = None
            fr.ocr = []  # drop text of frames nobody references (may include nicknames)


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
