import pytest
from app.catalog.grouping import ObsLite, consensus, group_observations

_n = [0]


def obs(frame, col, y, cp, species=None, nickname=None, segment=0, screen="grid", conf=0.95, pitch=300.0, hp=None):
    _n[0] += 1
    attrs = {"cp": {"value": cp, "confidence": conf, "frame_id": frame}}
    if species:
        attrs["species_id"] = {"value": species, "confidence": conf, "frame_id": frame}
    if nickname:
        attrs["nickname"] = {"value": nickname, "confidence": conf, "frame_id": frame}
    if hp:
        attrs["hp"] = {"value": hp, "confidence": conf, "frame_id": frame}
    return ObsLite(key=_n[0], frame_idx=frame, ts_ms=frame * 250, screen_type=screen, segment=segment, col=col,
                   content_y=y, pitch=pitch, attrs=attrs)


def test_repeated_views_across_scroll_are_one_individual():
    # The same slot (col 0, content_y ~600) is seen in three frames while scrolling.
    o = [obs(1, 0, 600, 500, "pidgey"), obs(5, 0, 603, 500, "pidgey"), obs(9, 0, 598, 500, "pidgey")]
    groups = group_observations(o)
    assert len(groups) == 1
    assert len(groups[0].obs) == 3
    assert groups[0].attributes["cp"]["value"] == 500
    assert groups[0].attributes["cp"]["support"] == 3


def test_identical_species_and_cp_in_different_slots_are_distinct():
    # Two Bidoof with identical CP visible in the same frame: must stay two individuals.
    o = [obs(1, 1, 300, 252, "bidoof"), obs(1, 2, 300, 252, "bidoof"),
         obs(4, 1, 302, 252, "bidoof"), obs(4, 2, 301, 252, "bidoof")]
    groups = group_observations(o)
    assert len(groups) == 2
    a, b = groups
    assert b.track_key in a.distinct_from
    assert not a.possible_duplicates and not b.possible_duplicates


def test_slot_conflict_splits_and_flags():
    o = [obs(1, 0, 600, 500, "pidgey"), obs(5, 0, 605, 812, "eevee")]
    groups = group_observations(o)
    assert len(groups) == 2
    assert all(any("Slot conflict" in r for r in g.reasons) for g in groups)


def test_segment_stitch_requires_neighbour():
    # Tracking lost (segment 1). Two adjacent items reappear at the same relative layout -> merged.
    seg0 = [obs(1, 0, 600, 500, "pidgey"), obs(1, 1, 600, 812, "eevee")]
    seg1 = [obs(20, 0, 100, 500, "pidgey", segment=1), obs(20, 1, 100, 812, "eevee", segment=1)]
    groups = group_observations(seg0 + seg1)
    assert len(groups) == 2
    assert all(len(g.obs) == 2 for g in groups)


def test_no_stitch_without_corroboration_is_flagged_not_merged():
    seg0 = [obs(1, 0, 600, 500, "pidgey")]
    seg1 = [obs(20, 0, 100, 500, "pidgey", segment=1)]
    groups = group_observations(seg0 + seg1)
    assert len(groups) == 2  # conservative: not merged
    assert all(g.possible_duplicates for g in groups)


def test_detail_folds_into_unique_grid_slot():
    o = [obs(1, 0, 600, 603, "bulbasaur"), obs(30, None, None, 603, "bulbasaur", screen="detail", hp=84, segment=1),
         obs(31, None, None, 603, "bulbasaur", screen="detail", hp=84, segment=1)]
    groups = group_observations(o)
    assert len(groups) == 1
    assert groups[0].attributes["hp"]["value"] == 84


def test_detail_not_folded_when_ambiguous():
    o = [obs(1, 1, 300, 252, "bidoof"), obs(1, 2, 300, 252, "bidoof"),
         obs(30, None, None, 252, "bidoof", screen="detail", segment=1)]
    groups = group_observations(o)
    assert len(groups) == 3
    detail = [g for g in groups if g.kind == "detail"][0]
    assert len(detail.possible_duplicates) == 2


def test_appraisal_attaches_to_preceding_detail_with_same_cp():
    o = [obs(30, None, None, 603, "bulbasaur", screen="detail", segment=1),
         obs(36, None, None, 603, None, screen="appraisal", segment=2)]
    groups = group_observations(o)
    assert len(groups) == 1
    assert {x.screen_type for x in groups[0].obs} == {"detail", "appraisal"}


def test_consensus_reports_conflicts_and_lowers_confidence():
    a = obs(1, 0, 600, 500, "pidgey", conf=0.9)
    b = obs(2, 0, 600, 508, "pidgey", conf=0.85)
    attrs, reasons = consensus([a, b])
    assert attrs["cp"]["confidence"] < 0.9
    assert any("Conflicting cp" in r for r in reasons)


def test_never_invents_missing_values():
    o = [obs(1, 0, 600, 500)]  # no species, no nickname
    g = group_observations(o)[0]
    assert "species_id" not in g.attributes
    assert "nickname" not in g.attributes


def test_confident_regular_form_overrides_grid_uncertainty():
    # Grid can't tell Tauros forms apart (form conf 0.3); the detail type line says regular form (0.9).
    def tauros(frame, screen, form_conf, note=None):
        o = obs(frame, 0 if screen == "grid" else None, 600 if screen == "grid" else None, 1460, "tauros", screen=screen)
        o.attrs["dex"] = {"value": 128, "confidence": 0.95}
        o.attrs["form"] = {"value": None, "confidence": form_conf}
        if note:
            o.notes.append(note)
        return o
    g = group_observations([tauros(1, "grid", 0.3, "Form could not be determined: Tauros, Tauros (Aqua)"),
                            tauros(9, "appraisal", 0.9, "Form chosen by type line among 4 forms.")])
    assert len(g) == 1
    assert g[0].reasons == []


def test_same_cp_different_species_never_merge():
    o = [obs(10, None, None, 524, "squirtle", screen="appraisal"), obs(11, None, None, 524, "doduo", screen="appraisal")]
    assert len(group_observations(o)) == 2


def test_weight_height_fingerprint_merges_repeat_views():
    def view(frame, screen, w):
        o = obs(frame, None, None, 298, "jigglypuff", screen=screen, hp=135)
        o.attrs["dex"] = {"value": 39, "confidence": 0.95}
        o.attrs["weight_kg"] = {"value": w, "confidence": 0.99}
        o.attrs["height_m"] = {"value": 0.4, "confidence": 0.99}
        return o
    # Appraisal pass early, detail pass much later: same individual.
    assert len(group_observations([view(244, "appraisal", 4.07), view(1047, "detail", 4.07)])) == 1
    # Same species/CP/HP but different weight: two individuals.
    assert len(group_observations([view(244, "appraisal", 4.07), view(1047, "detail", 5.12)])) == 2


def _view(frame, screen, cp, species, w, h, hp):
    o = obs(frame, None, None, cp, species, screen=screen, hp=hp)
    o.attrs["dex"] = {"value": 1, "confidence": 0.95}
    o.attrs["weight_kg"] = {"value": w, "confidence": 0.99}
    o.attrs["height_m"] = {"value": h, "confidence": 0.99}
    return o


def test_grid_seen_last_still_merges_with_earlier_views():
    """Appraisal pass, then detail pass, then the storage grid at the end: one Pokémon, not two."""
    o = [_view(71, "appraisal", 2024, "cinderace", 50.51, 1.65, 134),
         _view(700, "detail", 2024, "cinderace", 50.51, 1.65, 134),
         obs(990, 1, 300, 2024, "cinderace", segment=9)]
    groups = group_observations(o)
    assert len(groups) == 1
    assert {x.screen_type for x in groups[0].obs} == {"appraisal", "detail", "grid"}
    assert not groups[0].possible_duplicates


def test_grid_fold_refuses_ambiguous_pairings():
    # Two identical Bidoof on the grid, one detail view: can't know which -> stays separate and flagged.
    o = [_view(10, "detail", 252, "bidoof", 4.0, 0.5, 60),
         obs(990, 1, 300, 252, "bidoof", segment=9), obs(990, 2, 300, 252, "bidoof", segment=9)]
    groups = group_observations(o)
    assert len(groups) == 3
    # One grid slot but two different detail views (different weights) -> also refused.
    o = [_view(10, "detail", 500, "pidgey", 1.0, 0.3, 60), _view(20, "detail", 500, "pidgey", 2.0, 0.4, 61),
         obs(990, 0, 300, 500, "pidgey", segment=9)]
    assert len(group_observations(o)) == 3


def test_misread_cp_does_not_split_an_individual():
    """Same species, HP, weight and height; one frame read 'CP135' instead of 1353 -> one Pokémon, flagged."""
    misread = _view(122, "appraisal", 135, "exeggutor_alolan", 628.26, 12.57, 121)
    for k in ("species_id", "dex"):  # an impossible CP halves the species confidence, as the reader does
        misread.attrs[k]["confidence"] = 0.45
    o = [misread,
         _view(140, "appraisal", 1353, "exeggutor_alolan", 628.26, 12.57, 121),
         _view(700, "detail", 1353, "exeggutor_alolan", 628.26, 12.57, 121)]
    groups = group_observations(o)
    assert len(groups) == 1
    assert groups[0].consensus_value("cp") == 1353
    assert any("Conflicting cp" in r for r in groups[0].reasons)


def test_impossible_cp_is_repaired_from_unique_grid_entry():
    feasible = lambda sid, cp, hp: cp != 135  # stub: CP 135 is impossible for this HP, 1353 is fine
    misread = _view(122, "appraisal", 135, "exeggutor", 628.26, 12.57, 121)
    misread.attrs["species_id"]["method"] = "name:infeasible"
    misread.attrs["dex"]["value"] = 103
    grid = obs(990, 1, 300, 1353, "exeggutor", segment=9)
    grid.attrs["dex"] = {"value": 103, "confidence": 0.95}
    groups = group_observations([misread, grid], cp_feasible=feasible)
    assert len(groups) == 1 and groups[0].consensus_value("cp") == 1353
    assert not any("not achievable" in r for r in groups[0].reasons)


@pytest.mark.parametrize("grid_cp, feasible", [
    (1353, lambda sid, cp, hp: True),          # misread CP is actually possible -> no repair
    (1363, lambda sid, cp, hp: cp != 135),     # doesn't start with the misread digits -> no repair
    (13530, lambda sid, cp, hp: cp != 135),    # two extra digits -> no repair
])
def test_cp_repair_stays_narrow(grid_cp, feasible):
    misread = _view(122, "appraisal", 135, "exeggutor", 628.26, 12.57, 121)
    misread.attrs["dex"]["value"] = 103
    grid = obs(990, 1, 300, grid_cp, "exeggutor", segment=9)
    grid.attrs["dex"] = {"value": 103, "confidence": 0.95}
    assert len(group_observations([misread, grid], cp_feasible=feasible)) == 2


def test_cp_repair_refused_when_two_grid_entries_fit():
    feasible = lambda sid, cp, hp: cp != 135
    misread = _view(122, "appraisal", 135, "exeggutor", 628.26, 12.57, 121)
    misread.attrs["dex"]["value"] = 103
    grids = [obs(990, c, 300, cp, "exeggutor", segment=9) for c, cp in ((0, 1353), (1, 1357))]
    for g in grids:
        g.attrs["dex"] = {"value": 103, "confidence": 0.95}
    assert len(group_observations([misread] + grids, cp_feasible=feasible)) == 3
