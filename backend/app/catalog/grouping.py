"""Group per-frame observations into individuals for one recording.

Pure functions over plain data, with no database or image access, so the rules
are easy to unit-test.

Rules:
* Grid: within one scroll *segment*, an observation belongs to the slot at
  (column, content_y), where content_y = frame_y + scroll offset. One slot is
  one individual. A CP or species conflict inside a slot splits it and flags
  both parts for review instead of silently merging.
* Segments (tracking lost) are stitched only when a slot's signature matches
  exactly one earlier slot AND a neighbouring slot matches at the same relative
  position. Otherwise the repeat is flagged as a possible duplicate view.
* Detail: consecutive detail frames with the same CP and name are one
  individual. A detail track is folded into a grid slot only when exactly one
  grid slot is compatible.
* Appraisal frames attach to the closest preceding detail track with the same
  CP.
* Two groups seen in the same frame are distinct individuals by definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Hashable, List, Optional, Set, Tuple

TRUST = 0.5  # minimum confidence for a value to be used to separate or merge tracks


@dataclass
class ObsLite:
    key: Hashable
    frame_idx: int
    ts_ms: int
    screen_type: str
    segment: int = 0
    col: Optional[int] = None
    content_y: Optional[float] = None
    pitch: Optional[float] = None
    attrs: Dict[str, dict] = field(default_factory=dict)  # field -> {value, confidence, ...}
    notes: List[str] = field(default_factory=list)

    def val(self, f: str, min_conf: float = TRUST):
        a = self.attrs.get(f)
        if a is None or a.get("value") is None or a.get("confidence", 0) < min_conf:
            return None
        return a["value"]


@dataclass
class GroupDraft:
    track_key: str
    kind: str
    obs: List[ObsLite] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    possible_duplicates: Set[str] = field(default_factory=set)
    distinct_from: Set[str] = field(default_factory=set)
    segment: int = 0
    col: Optional[int] = None
    slot: Optional[int] = None
    content_y: Optional[float] = None
    attributes: Dict[str, dict] = field(default_factory=dict)

    @property
    def frames(self) -> Set[int]:
        return {o.frame_idx for o in self.obs}

    def consensus_value(self, f: str, min_conf: float = TRUST):
        a = self.attributes.get(f)
        if a is None or a.get("value") is None or a.get("confidence", 0) < min_conf:
            return None
        return a["value"]


# ------------------------------------------------------------------ consensus

def consensus(obs: List[ObsLite]) -> Tuple[Dict[str, dict], List[str]]:
    """Confidence-weighted vote per field. Agreement raises confidence; disagreement lowers it and is reported."""
    fields: Set[str] = set()
    for o in obs:
        fields.update(o.attrs)
    out: Dict[str, dict] = {}
    reasons: List[str] = []
    for f in sorted(fields):
        votes: Dict[str, List[dict]] = {}
        vote_w: Dict[str, float] = {}
        for o in obs:
            a = o.attrs.get(f)
            if a is None or a.get("value") is None:
                continue
            w = a.get("confidence", 0)
            if f in ("species_id", "dex"):
                # A reading whose form is certain outweighs one where the form was a default guess.
                w *= (o.attrs.get("form") or {}).get("confidence", 1.0)
            votes.setdefault(repr(a["value"]), []).append(a)
            vote_w[repr(a["value"])] = vote_w.get(repr(a["value"]), 0.0) + w
        if not votes:
            continue
        weights = vote_w
        total = sum(weights.values()) or 1.0
        best_k = max(weights, key=lambda k: (weights[k], len(votes[k])))
        supporting = votes[best_k]
        p_wrong = 1.0
        for a in supporting:
            p_wrong *= 1.0 - min(0.99, a.get("confidence", 0))
        agree = 1.0 - p_wrong
        share = weights[best_k] / total
        conf = agree * share
        best_obs = max(supporting, key=lambda a: a.get("confidence", 0))
        out[f] = {
            "value": best_obs["value"],
            "confidence": round(conf, 3),
            "method": best_obs.get("method", ""),
            "frame_id": best_obs.get("frame_id"),
            "support": len(supporting),
            "alternatives": [votes[k][0]["value"] for k in votes if k != best_k][:3],
        }
        same_species_forms = f in ("species_id", "form") and _same_dex([votes[k][0] for k in votes], obs)
        if len(votes) > 1 and share < 0.75 and f in ("cp", "species_id", "hp", "nickname", "form") and not same_species_forms:
            reasons.append(f"Conflicting {f} readings: " + ", ".join(str(votes[k][0]["value"]) for k in votes))
    return out, reasons


# ------------------------------------------------------------------ helpers

FORM_SURE = 0.75


def _same_dex(values: List[dict], obs: List[ObsLite]) -> bool:
    dexes = {(o.attrs.get("dex") or {}).get("value") for o in obs if o.attrs.get("dex")}
    return len(dexes) == 1 and None not in dexes


def _species_key(o: ObsLite):
    """(dex, species_id or None): species_id only counts when the form was read with confidence."""
    dex = o.val("dex")
    sid = o.val("species_id")
    if sid is None:
        return None
    form_sure = (o.attrs.get("form") or {}).get("confidence", 1.0) >= FORM_SURE
    return (dex, sid if form_sure else None)


def _species_compatible(a: ObsLite, b: ObsLite) -> bool:
    ka, kb = _species_key(a), _species_key(b)
    if ka is None or kb is None:
        return True
    if ka[0] is not None and kb[0] is not None and ka[0] != kb[0]:
        return False
    return ka[1] is None or kb[1] is None or ka[1] == kb[1]


def _compatible(o: ObsLite, g: GroupDraft) -> bool:
    ov = o.val("cp")
    if ov is not None:
        gv = [x.val("cp") for x in g.obs if x.val("cp") is not None]
        if gv and ov not in gv:
            return False
    if not all(_species_compatible(o, x) for x in g.obs):
        return False
    # Weight/height differ between individuals; a mismatch means a different Pokémon.
    for f in ("weight_kg", "height_m"):
        v = o.val(f)
        if v is not None and any(x.val(f) is not None and x.val(f) != v for x in g.obs):
            return False
    return True


def _fingerprint(g: GroupDraft):
    """Species + max HP + weight + height: effectively unique per individual within one recording.

    CP is deliberately left out: one misread digit ("CP135" for 1353) must not split an individual in two.
    If the CP readings disagree, consensus keeps the better-supported one and reports the conflict for review.
    """
    # A misread CP lowers confidence in the species reading too (the CP/HP no longer fit it), but the name itself
    # was read; the exact weight + height + HP match is what makes this safe.
    dex = g.consensus_value("dex", min_conf=0.25)
    hp = g.consensus_value("hp")
    w, h = g.consensus_value("weight_kg"), g.consensus_value("height_m")
    if None in (dex, hp, w, h):
        return None
    return (dex, hp, w, h)


def _signature(g: GroupDraft):
    cp = g.consensus_value("cp")
    who = g.consensus_value("species_id") or g.consensus_value("nickname")
    if cp is None or who is None:
        return None
    return (g.col, cp, who)


# ------------------------------------------------------------------ grid

def _group_grid(obs: List[ObsLite]) -> List[GroupDraft]:
    groups: List[GroupDraft] = []
    for o in sorted(obs, key=lambda o: (o.frame_idx, o.col or 0, o.content_y or 0)):
        pitch = o.pitch or 200.0
        tol = 0.35 * pitch
        match = None
        for g in groups:
            if g.segment != o.segment or g.col != o.col or g.content_y is None:
                continue
            if abs(g.content_y - (o.content_y or 0)) <= tol and o.frame_idx not in g.frames:
                match = g
                break
        if match is not None and _compatible(o, match):
            match.obs.append(o)
            n = len(match.obs)
            match.content_y = match.content_y + ((o.content_y or 0) - match.content_y) / n
            continue
        slot = int(round((o.content_y or 0) / pitch))
        key = f"g{o.segment}c{o.col}r{slot}"
        existing = {g.track_key for g in groups}
        suffix = 1
        base = key
        while key in existing:
            suffix += 1
            key = f"{base}-{suffix}"
        g = GroupDraft(track_key=key, kind="grid", obs=[o], segment=o.segment, col=o.col, slot=slot, content_y=o.content_y)
        if match is not None:
            msg = "Slot conflict: different CP/species seen at the same grid position (scroll tracking may have drifted)."
            g.reasons.append(msg)
            match.reasons.append(msg)
            g.possible_duplicates.add(match.track_key)
            match.possible_duplicates.add(g.track_key)
        groups.append(g)
    for g in groups:
        g.attributes, rs = consensus(g.obs)
        g.reasons.extend(rs)
    return groups


def _stitch_segments(groups: List[GroupDraft]) -> List[GroupDraft]:
    """Merge slots repeated across tracking segments only with neighbour corroboration."""
    by_seg: Dict[int, List[GroupDraft]] = {}
    for g in groups:
        by_seg.setdefault(g.segment, []).append(g)
    merged_into: Dict[str, GroupDraft] = {}
    segs = sorted(by_seg)
    for si, s in enumerate(segs):
        if si == 0:
            continue
        earlier = [g for g in groups if g.segment < s and g.track_key not in merged_into]
        sig_index: Dict[tuple, List[GroupDraft]] = {}
        for g in earlier:
            sig = _signature(g)
            if sig:
                sig_index.setdefault(sig, []).append(g)
        current = by_seg[s]
        for g in current:
            sig = _signature(g)
            if not sig or sig not in sig_index:
                continue
            matches = sig_index[sig]
            if len(matches) != 1:
                for m in matches:
                    g.possible_duplicates.add(m.track_key)
                g.reasons.append("Matches several earlier items after scroll tracking was lost.")
                continue
            m = matches[0]
            corroborated = False
            for n in current:
                if n is g or n.slot is None or g.slot is None or abs(n.slot - g.slot) > 1 or abs((n.col or 0) - (g.col or 0)) > 1:
                    continue
                nsig = _signature(n)
                for nm in sig_index.get(nsig, []) if nsig else []:
                    if nm.segment == m.segment and nm.slot is not None and m.slot is not None \
                            and nm.slot - m.slot == n.slot - g.slot and (nm.col or 0) - (m.col or 0) == (n.col or 0) - (g.col or 0):
                        corroborated = True
                        break
                if corroborated:
                    break
            if corroborated:
                merged_into[g.track_key] = m
            else:
                g.possible_duplicates.add(m.track_key)
                m.possible_duplicates.add(g.track_key)
                g.reasons.append("Looks like an item seen earlier, but scroll tracking was lost and no neighbour confirms it.")
    out: List[GroupDraft] = []
    for g in groups:
        target = merged_into.get(g.track_key)
        if target is None:
            out.append(g)
            continue
        while target.track_key in merged_into:
            target = merged_into[target.track_key]
        target.obs.extend(g.obs)
    for g in out:
        g.attributes, rs = consensus(g.obs)
        g.reasons = [r for r in g.reasons if not r.startswith("Conflicting")] + rs
        g.possible_duplicates = {k for k in g.possible_duplicates if k not in merged_into}
    return out


# ------------------------------------------------------------------ detail & appraisal

def _group_detail(obs: List[ObsLite]) -> List[GroupDraft]:
    groups: List[GroupDraft] = []
    cur: Optional[GroupDraft] = None
    for o in sorted(obs, key=lambda o: o.frame_idx):
        if cur is not None and _compatible(o, cur) and (o.val("nickname") is None or
                                                        o.val("nickname") in [x.val("nickname") for x in cur.obs] or
                                                        all(x.val("nickname") is None for x in cur.obs)):
            cur.obs.append(o)
            continue
        cur = GroupDraft(track_key=f"d{o.frame_idx}", kind="detail", obs=[o], segment=o.segment)
        groups.append(cur)
    for g in groups:
        g.attributes, rs = consensus(g.obs)
        g.reasons.extend(rs)
    return groups


def _attach_appraisals(appraisals: List[ObsLite], targets: List[GroupDraft]) -> List[GroupDraft]:
    """Attach each appraisal observation to the latest earlier group with the same CP; leftovers form groups."""
    leftovers: List[GroupDraft] = []
    for a in sorted(appraisals, key=lambda o: o.frame_idx):
        cp = a.val("cp")
        best = None
        for g in targets:
            first = min(g.frames)
            if first <= a.frame_idx and cp is not None and g.consensus_value("cp") == cp and _compatible(a, g):
                if best is None or first > min(best.frames):
                    best = g
        if best is not None:
            best.obs.append(a)
            continue
        if leftovers and cp is not None and leftovers[-1].consensus_value("cp") == cp and _compatible(a, leftovers[-1]):
            leftovers[-1].obs.append(a)
            leftovers[-1].attributes, _ = consensus(leftovers[-1].obs)
            continue
        g = GroupDraft(track_key=f"a{a.frame_idx}", kind="appraisal", obs=[a], segment=a.segment)
        g.attributes, _ = consensus(g.obs)
        if "species_id" not in g.attributes:
            g.reasons.append("Appraisal could not be linked to a Pokémon detail view.")
        leftovers.append(g)
    return leftovers


def _merge_by_fingerprint(groups: List[GroupDraft]) -> List[GroupDraft]:
    """Merge repeat views (e.g. an appraisal pass and a later detail pass) of the same individual.

    Requires an exact match on species, CP, HP, weight and height, and no shared frame.
    """
    for g in groups:
        g.attributes, _ = consensus(g.obs)
    out: List[GroupDraft] = []
    index: Dict[tuple, GroupDraft] = {}
    for g in sorted(groups, key=lambda g: min(g.frames)):
        fp = _fingerprint(g)
        target = index.get(fp) if fp else None
        # Two grid slots are positions, not views; never merge those here (stitching handles them).
        if target is not None and not (target.frames & g.frames) and not (target.kind == g.kind == "grid"):
            target.obs.extend(g.obs)
            target.attributes, _ = consensus(target.obs)
            continue
        if fp and fp not in index:
            index[fp] = g
        out.append(g)
    return out


def _group_compatible(view: GroupDraft, slot: GroupDraft) -> bool:
    if view.frames & slot.frames:
        return False  # seen at the same moment = two different Pokémon
    cp = view.consensus_value("cp")
    if cp is None or slot.consensus_value("cp") != cp:
        return False
    return all(_compatible(o, slot) for o in view.obs)


def _truncated_cp_match(view: GroupDraft, slot: GroupDraft, cp_feasible) -> bool:
    """A view whose CP is impossible, and a grid entry whose CP is that number plus one more digit and fits the HP.

    OCR sometimes loses a digit hidden behind the sprite ("CP135" for 1353); the grid shows the full number.
    """
    if cp_feasible is None or view.frames & slot.frames:
        return False
    sid, cp, hp = view.consensus_value("species_id", min_conf=0.25), view.consensus_value("cp"), view.consensus_value("hp")
    full = slot.consensus_value("cp")
    if None in (sid, cp, hp, full) or cp_feasible(sid, cp, hp):
        return False
    if not (str(full).startswith(str(cp)) and len(str(full)) == len(str(cp)) + 1):
        return False
    slot_dex, view_dex = slot.consensus_value("dex", min_conf=0.25), view.consensus_value("dex", min_conf=0.25)
    if slot_dex is None or slot_dex != view_dex:
        return False
    return cp_feasible(slot.consensus_value("species_id", min_conf=0.25) or sid, full, hp)


def _fold_views_into_grid(groups: List[GroupDraft], cp_feasible=None) -> List[GroupDraft]:
    """Merge a detail/appraisal view into its storage-grid entry, whichever came first in the recording.

    Only when the pairing is unambiguous both ways: the view matches exactly one grid entry, and that grid
    entry matches no other view. Identical twins (same species and CP) on the grid stay separate.
    """
    for g in groups:
        g.attributes, _ = consensus(g.obs)
    grid = [g for g in groups if g.kind == "grid"]
    views = [g for g in groups if g.kind != "grid"]
    matches = {}
    corrected: Set[int] = set()
    for v in views:
        exact = [s for s in grid if _group_compatible(v, s)]
        if not exact:
            fixed = [s for s in grid if _truncated_cp_match(v, s, cp_feasible)]
            if len(fixed) == 1:
                exact = fixed
                corrected.add(id(v))
        matches[id(v)] = exact
    merged: Set[int] = set()
    for v in views:
        slots = matches[id(v)]
        if len(slots) != 1:
            continue
        slot = slots[0]
        rivals = [w for w in views if w is not v and slot in matches[id(w)]]
        if rivals:
            continue
        if id(v) in corrected:
            full = slot.consensus_value("cp")
            for o in v.obs:
                a = o.attrs.get("cp")
                if a and a.get("value") != full:
                    # Keep the misread for the record, but let the grid's full CP win the vote.
                    o.attrs["cp"] = {**a, "confidence": 0.05, "method": f"{a.get('method', 'ocr')} (impossible; grid shows {full})"}
                o.notes = [n for n in o.notes if not n.startswith("CP/HP are not achievable")]
            for f in ("species_id", "dex", "form"):  # their low confidence came only from the impossible CP
                for o in v.obs:
                    if f in o.attrs and o.attrs[f].get("method", "").endswith(":infeasible"):
                        o.attrs[f] = {**o.attrs[f], "confidence": min(0.9, o.attrs[f]["confidence"] * 2)}
        slot.obs.extend(v.obs)
        slot.attributes, _ = consensus(slot.obs)
        merged.add(id(v))
    return [g for g in groups if id(g) not in merged]


def group_observations(obs: List[ObsLite], cp_feasible=None) -> List[GroupDraft]:
    """`cp_feasible(species_id, cp, hp) -> bool` (optional) enables repairing a CP misread by one digit."""
    grid = _stitch_segments(_group_grid([o for o in obs if o.screen_type == "grid"]))
    details = _group_detail([o for o in obs if o.screen_type == "detail"])

    # Detail and appraisal views are folded into grid slots later, by _fold_views_into_grid, which checks the
    # pairing is unambiguous in BOTH directions and works whichever came first in the recording.
    all_groups = grid + details
    leftovers = _attach_appraisals([o for o in obs if o.screen_type == "appraisal"], all_groups)
    all_groups += leftovers
    all_groups = _merge_by_fingerprint(all_groups)
    all_groups = _fold_views_into_grid(all_groups, cp_feasible)

    for g in all_groups:
        g.attributes, rs = consensus(g.obs)
        g.reasons = list(dict.fromkeys([r for r in g.reasons if not r.startswith("Conflicting")] + rs))
        known_species = g.consensus_value("species_id", min_conf=0.7) is not None
        # The regular form has no name (value None), so check the readings directly: any confident form
        # reading of the winning species settles it.
        winner = g.consensus_value("species_id", min_conf=0.7)
        known_form = known_species and any(
            (o.attrs.get("species_id") or {}).get("value") == winner
            and (o.attrs.get("form") or {}).get("confidence", 0) >= FORM_SURE for o in g.obs)
        for o in g.obs:
            for n in o.notes:
                if n in g.reasons or n.startswith(("Cell partially", "Name not visible")):
                    continue
                # Explanations of how something was decided, not problems.
                if n.startswith(("Form chosen by", "Species inferred from", "Form resolved by")):
                    continue
                # Notes from a frame that lacked evidence are moot once other frames supplied it.
                if known_species and (n.startswith("Species could not") or "may be a misread" in n
                                      or n.startswith("Several family members")):
                    continue
                if known_form and n.startswith("Form could not"):
                    continue
                g.reasons.append(n)

    # Co-visibility proves distinctness; same CP+species without it is a possible duplicate.
    for i, a in enumerate(all_groups):
        for b in all_groups[i + 1 :]:
            if a.frames & b.frames:
                a.distinct_from.add(b.track_key)
                b.distinct_from.add(a.track_key)
                continue
            same_cp = a.consensus_value("cp") is not None and a.consensus_value("cp") == b.consensus_value("cp")
            sa, sb = a.consensus_value("species_id"), b.consensus_value("species_id")
            if same_cp and sa is not None and sa == sb and not (a.kind == b.kind == "grid" and a.segment == b.segment):
                a.possible_duplicates.add(b.track_key)
                b.possible_duplicates.add(a.track_key)
    for g in all_groups:
        if g.possible_duplicates and not any("duplicate" in r.lower() or "seen earlier" in r for r in g.reasons):
            g.reasons.append("Possible duplicate view of another item in this recording: " + ", ".join(sorted(g.possible_duplicates)))
    return all_groups
