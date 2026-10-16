"""Demo fixtures. Every record is flagged is_demo=True and labelled as an EXAMPLE.

They are not scan results and are never mixed into exports unless explicitly requested.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import ObservationGroup, Pokemon, Scan
from .recommend.gamedata import get_gamedata

DEMO_NOTE = "DEMO EXAMPLE: fixture data, not an actual scan result."

# species_id, cp, hp, ivs (or None), flags
_FIXTURES = [
    ("charizard", 2889, 149, (15, 14, 15), {"shiny": False, "lucky": True}),
    ("medicham", 1497, 125, (0, 15, 15), {"shiny": False}),
    ("swampert", 1500, 140, None, {"shadow": True, "shiny": False}),
    ("rattata_alolan", 412, 58, None, {"shiny": True}),
    ("dragonite", 3792, 167, (15, 15, 14), {"shiny": False}),
    ("mewtwo", 4178, 180, (14, 15, 15), {"shiny": False, "purified": True}),
    ("bidoof", 289, 64, (3, 7, 2), {"shiny": False}),
    ("bidoof", 301, 66, None, {}),
    ("pidgey", 154, 37, (5, 1, 9), {"shiny": False}),
    ("eevee", 812, 92, None, {"shiny": False}),
    ("gible", 640, 72, (15, 14, 15), {"shiny": False}),
    ("vulpix_alolan", 588, 61, None, {"shiny": True, "favorite": True}),
]


def load_demo(db: Session) -> int:
    clear_demo(db)
    gd = get_gamedata()
    n = 0
    for sid, cp, hp, ivs, flags in _FIXTURES:
        sp = gd.get(sid)
        if sp is None:
            continue
        p = Pokemon(species_id=sid, dex=sp.dex, form=sp.form, cp=cp, hp=hp, nickname=None, is_demo=True,
                    user_confirmed=False, confirmed_via="demo", confidence=1.0, notes=DEMO_NOTE,
                    provenance={"_": {"method": "demo fixture", "confidence": 1.0}})
        if ivs:
            p.iv_atk, p.iv_def, p.iv_sta = ivs
            p.iv_status = "verified"
        for k, v in flags.items():
            setattr(p, k, v)
        db.add(p)
        n += 1
    scan = Scan(original_filename="DEMO-example-recording.mov", sha256="demo", size_bytes=0, kind="collection",
                status="review", is_demo=True, summary={"message": DEMO_NOTE, "keyframes": 0, "observations": 2})
    db.add(scan)
    db.flush()
    db.add(ObservationGroup(scan_id=scan.id, track_key="demo-1", status="needs_review",
                            attributes={"cp": {"value": 1032, "confidence": 0.92, "method": "demo", "frame_id": None},
                                        "nickname": {"value": "Sparky", "confidence": 0.81, "method": "demo", "frame_id": None}},
                            review_reasons=["DEMO: species could not be identified from a nickname alone."]))
    db.add(ObservationGroup(scan_id=scan.id, track_key="demo-2", status="needs_review",
                            attributes={"cp": {"value": 1500, "confidence": 0.95, "method": "demo", "frame_id": None},
                                        "species_id": {"value": "medicham", "confidence": 0.93, "method": "demo", "frame_id": None},
                                        "dex": {"value": 308, "confidence": 0.93, "method": "demo", "frame_id": None}},
                            review_reasons=["DEMO: possible duplicate of a Medicham already in the collection."]))
    db.commit()
    return n


def clear_demo(db: Session) -> int:
    n = db.query(Pokemon).filter(Pokemon.is_demo.is_(True)).count()
    for s in db.scalars(select(Scan).where(Scan.is_demo.is_(True))).all():
        db.delete(s)
    db.execute(delete(Pokemon).where(Pokemon.is_demo.is_(True)))
    db.commit()
    return n
