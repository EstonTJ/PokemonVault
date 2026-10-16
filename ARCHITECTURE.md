# PokéVault — Architecture, Schema & Milestones

PokéVault is a private, single-owner web app. You record your own Pokémon GO
storage screens with the iPhone screen recorder and upload the video. The
backend extracts the useful frames, reads them with **local** OCR, matches what
it reads against a canonical species reference, groups repeated views of the
same Pokémon, and puts anything uncertain in a review queue. Only Pokémon you
confirm reach the collection.

PokéVault never talks to Pokémon GO. It does not log in to the game, call
unofficial APIs, intercept traffic, automate gameplay or modify the game. It
only reads pixels from a video you recorded yourself.

---

## 1. System overview

```
 iPhone Safari / desktop browser
        │  HTTPS, session cookie, X-PokeVault header on writes
        ▼
 ┌──────────────────────── FastAPI (backend/app) ─────────────────────────┐
 │ api/        auth · uploads · scans · review · collection · recommend   │
 │             dashboard · privacy (export-all / delete-all) · demo       │
 │                                                                        │
 │ jobs/       durable SQLite job queue: lease, heartbeat, cancel, retry  │
 │   └─ worker (in-process thread or `python -m app.jobs.worker`)         │
 │        │                                                               │
 │        ▼   pipeline (each stage is its own module)                     │
 │ video/        probe → sample (FFmpeg) → motion/scroll → keyframes      │
 │ ocr/          engine adapter (PaddleOCR | RapidOCR-ONNX) · text parse  │
 │ recognition/  screen classifier · grid reader · detail reader          │
 │               species matcher · CP/HP feasibility · attribute cues     │
 │ appraisal/    bar calibration → IV candidates → star cross-check       │
 │ catalog/      observation grouping · identity matching · importer      │
 │ recommend/    versioned game data · league math · scoring · actions    │
 │ export/       CSV / JSON                                               │
 └────────────────────────────────────────────────────────────────────────┘
        │                                    │
   SQLite (SQLAlchemy + Alembic)       data/private/  (never under a static route)
                                         uploads/  raw video, deleted after processing
                                         frames/   downscaled keyframes referenced by evidence
```

### Design principles

1. **Evidence first.** Every extracted attribute is stored as
   `{value, confidence, frame_id, method}`. If nothing supports a value, the
   value is `null` ("unknown"). The app never fills one in with a default.
2. **Observations ≠ Pokémon.** Raw per-frame *observations* are grouped into
   *observation groups*: one group is one individual seen in one recording.
   A group becomes a collection **Pokémon** only when you confirm it.
3. **Conservative identity.** Matching species and CP alone never merges two
   records. An automatic merge needs a deterministic fingerprint (the same
   recording re-imported) or a strong agreement of fields. Everything else goes
   to manual reconciliation.
4. **Swappable modules.** OCR engine, species matcher, sprite matcher, game data
   and scoring weights all sit behind small interfaces.
5. **Privacy by default.** Raw videos are deleted after processing. Only the
   keyframes that evidence points to are kept, downscaled. Media is served
   only through authenticated endpoints with `Cache-Control: no-store`.

---

## 2. Processing pipeline

| Stage | Module | What it does |
|---|---|---|
| Validate | `api/uploads.py` | Streams the upload to disk in chunks and aborts once it passes `MAX_UPLOAD_MB`. Checks the extension and the ISO-BMFF `ftyp` magic bytes. Computes SHA-256 for idempotent re-import. |
| Probe | `video/probe.py` | Reads duration, resolution and fps through the bundled FFmpeg. Enforces `MAX_DURATION_S`. |
| Sample | `video/frames.py` | FFmpeg decodes at `SAMPLE_FPS` (default 4) and scales to a 720 px normalized width, so the app never decodes every frame into Python. |
| Motion & scroll | `video/motion.py` | Grayscale diff → motion score. Phase correlation on the content band → vertical scroll delta. A response below threshold means tracking is lost and a new *segment* starts. |
| Keyframes | `video/frames.py` | Keeps a frame once motion settles (stable after a change) and it differs from the last keyframe, plus a periodic keyframe while scrolling slowly. |
| Classify | `recognition/screens.py` | Classifies each keyframe from its OCR layout: `grid` (≥3 `CP` tokens in columns), `detail` (CP header plus STARDUST/CANDY/POWER UP), `appraisal` (Attack/Defense/HP labels plus bars), or `other`. |
| Read | `recognition/grid.py`, `recognition/detail.py` | Turns OCR boxes into structured observations: grid cell (column, row, content-y), CP, name text, HP, candy family and text cues. |
| Identify | `recognition/species.py` | Fuzzy-matches the name against the species and form reference. If the name is a nickname, it uses candy family candidates and keeps those for which some level and IV combination can produce the observed CP **and** HP (`recognition/feasibility.py`). |
| Appraise | `appraisal/bars.py` | Finds the three bar tracks, measures fill against the calibrated track length and maps it to 0–15. Ambiguous readings stay ranges. The star tier and CP/HP feasibility narrow the candidates further. |
| Group | `catalog/grouping.py` | Grid: same segment, same column and content-y within tolerance means one slot, so one individual. Detail: consecutive frames with consistent CP and name. Conflicts split the group and flag it. |
| Match | `catalog/identity.py` | Compares each group with existing Pokémon. The result is `auto_link` (same recording fingerprint), `candidates` (needs manual reconciliation) or `new`. |
| Retain | `catalog/importer.py` | Deletes the raw video (unless keep was chosen) and deletes keyframes that no evidence references. |

Cancellation is checked between frames and stages. A worker that dies leaves
its job leased; when the lease expires the job is re-queued, up to
`max_attempts`.

---

## 3. Database schema (SQLite, Alembic-managed)

```
scans                       one uploaded recording
  id PK, created_at, original_filename, sha256 (indexed), size_bytes,
  kind ('collection'|'appraisal'|'auto'), status, keep_video, video_path?,
  video_deleted_at?, duration_s?, width?, height?, frames_retained,
  is_demo, summary JSON, error?

jobs                        durable work queue
  id PK, scan_id FK, type, status ('queued'|'running'|'succeeded'|'failed'|
  'cancelled'), cancel_requested, progress 0..1, stage, attempts,
  max_attempts, lease_until?, worker_id?, error?, created_at, started_at?,
  finished_at?

frames                      retained keyframes (evidence)
  id PK, scan_id FK, idx, timestamp_ms, screen_type, segment,
  scroll_offset, width, height, image_path?, ocr JSON

observations                one Pokémon seen in one frame
  id PK, scan_id FK, frame_id FK, group_id FK?, screen_type,
  segment, grid_col?, grid_row?, content_y?, bbox JSON,
  attributes JSON   {field: {value, confidence, frame_id, method}}

observation_groups          one individual in one recording
  id PK, scan_id FK, track_key (deterministic, unique per scan),
  status ('needs_review'|'ready'|'confirmed'|'rejected'),
  attributes JSON (consensus + confidence), review_reasons JSON,
  candidate_pokemon_ids JSON, pokemon_id FK?, observation_count,
  first_ts_ms, last_ts_ms

pokemon                     confirmed individuals (the collection)
  id PK (uuid4), species_id, dex, form?, nickname?, cp?, hp?,
  iv_atk?, iv_def?, iv_sta?, iv_status ('unknown'|'range'|'verified'),
  iv_candidates JSON?, level?, shiny?, shadow?, purified?, lucky?, favorite?
  (nullable = unknown), confidence, provenance JSON, user_confirmed,
  confirmed_via ('review'|'bulk'|'manual'), notes, is_demo,
  first_seen_at, last_seen_at, created_at, updated_at

pokemon_evidence            links a Pokémon to the groups and frames that support it
  id PK, pokemon_id FK, group_id FK?, scan_id FK?, frame_id FK?, kind

appraisals                  one appraisal reading
  id PK, scan_id FK, frame_id FK, group_id FK?, pokemon_id FK?,
  bars JSON (fill ratio, track px, confidence per stat), stars?,
  iv_candidates JSON, status ('verified'|'ambiguous'|'failed'), confidence

corrections                 audit log of user edits
  id PK, target ('group'|'pokemon'), target_id, field, old JSON, new JSON, at

app_settings                key/value (retention, thresholds, scoring weights)
gamedata_versions           id, source, source_timestamp, loaded_at, sha256
```

Booleans such as `shiny` are three-state: `true`, `false` or `null`
(unknown). The UI shows unknown as "—", never as "No".

---

## 4. Recognition: what is reliable and what is not

| Attribute | Method | Status in MVP |
|---|---|---|
| CP | OCR of `CP ####` tokens, with plausibility bounds | Implemented |
| Species and form | OCR name → fuzzy match → candy-family and CP/HP feasibility | Implemented |
| Nickname | Name text that is not a species name but sits where a name belongs | Implemented |
| HP | OCR `## / ## HP` (detail screen) | Implemented |
| Lucky | OCR text "LUCKY" on the detail screen | Implemented as a low-confidence cue |
| Shadow | `PURIFY` button text on the detail screen | Implemented as a cue |
| Purified | No reliable text cue | Unknown unless set by the user |
| Shiny, Favorite | Needs icon templates calibrated on real recordings | **Not detected**; stays unknown |
| Sprite matching | Optional ORB matcher against a user-supplied reference sprite folder | Off by default; the app ships no Nintendo artwork |
| Appraisal IVs | Calibrated bar fill plus star tier plus CP/HP feasibility | Implemented. Exact IVs only when one candidate remains. |

> **Validation status:** the pipeline is tested against synthetic recordings
> generated by `backend/tests/synthetic.py`. These are clearly not game footage.
> No accuracy claims about real Pokémon GO recordings are made until real sample
> recordings have been run and the layout thresholds calibrated
> (`recognition/layout.py`).

---

## 5. Recommendations

Game data is the PvPoke `gamemaster.json` (MIT license), stored with its source
timestamp in `gamedata_versions`. Rankings are marked **provisional** when the
data is older than `GAMEDATA_STALE_DAYS`. PvE scoring is always provisional,
because it uses PvP move stats as a proxy.

| Score | Basis |
|---|---|
| Collection rarity | Legendary, mythical and ultra-beast tags, regional and special forms, shiny/lucky/shadow, how many of the species you own |
| PvE usefulness | Level-40 attack stat × best fast and charged move damage per time (proxy) |
| Great/Ultra/Master League | Stat product at the best level under the CP cap. Uses the IV rank out of 4096 when IVs are verified; otherwise species potential, labeled "IVs unknown". |
| Evolution priority | Better evolution scores, a new dex entry, special-form evolutions |
| Stardust priority | Best use-case score × IV quality, with lucky and shadow cost modifiers |

Actions: **Keep / Evolve / Power Up / Appraise / Review for Transfer**. Each
action carries plain-language reasons. Nothing is transferred or modified, and
"Review for Transfer" is only a suggestion to look.

---

## 6. Security & privacy

* Single owner. The password comes from `POKEVAULT_PASSWORD` (a Replit Secret).
  If it is unset, one is generated into `data/private/.owner_password` (0600)
  and its location is logged.
* Signed, HttpOnly, SameSite=Strict session cookie. All writes require the
  `X-PokeVault` header, which blocks CSRF because no CORS is configured.
  Login attempts are rate-limited.
* No third-party scripts, analytics, fonts or AI services. The
  `external_ai_opt_in` setting exists but defaults to off, and no code path
  sends data anywhere.
* Upload size, duration, concurrent-job and hourly-upload limits are all
  configurable.
* `GET /api/privacy/export` exports everything as JSON.
  `POST /api/privacy/delete-all` deletes all data (it requires typing a
  confirmation phrase).

---

## 7. Milestones

| # | Milestone | Status |
|---|---|---|
| M1 | Vertical slice: upload → FFmpeg → keyframes → OCR → review → confirm → catalog → CSV | **This delivery** |
| M2 | Dedup and identity: scroll tracking, grouping, reconciliation, idempotent re-import | **This delivery** (unit-tested) |
| M3 | Appraisal recording: bar calibration, IV candidates, ambiguity flags | **This delivery** (synthetic-tested) |
| M4 | Recommendations v1 with reasons and provisional labels | **This delivery** |
| M5 | Calibrate on real recordings: thresholds, iOS status-bar crop, Dynamic Island devices, scaling | Next. Needs real sample recordings. |
| M6 | Icon cues: shiny sparkle, favorite star, lucky background, shadow flame | After M5 (needs labeled samples) |
| M7 | Optional sprite matcher with a user-provided reference set; PvE move data from a PvE source | Later |
