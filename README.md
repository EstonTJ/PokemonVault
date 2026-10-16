# PokéVault

A private, single-owner web app that turns iPhone screen recordings of your own
Pokémon GO storage into a reviewed and searchable collection. It also gives
reasoned Keep / Evolve / Power Up / Appraise / Review-for-Transfer suggestions.

It never connects to Pokémon GO, never asks for game credentials, and never
automates or modifies anything. It only reads pixels from videos you upload to
your own server.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the architecture, the database
schema and the milestones, and **[NOTICE.md](NOTICE.md)** for attributions.

> Unofficial fan project. Not affiliated with Niantic, Nintendo or The Pokémon Company.

## Status: what is and isn't verified

| Area | Status |
|---|---|
| Upload → FFmpeg → keyframes → OCR → grouping → review → confirm → catalog → CSV | Working end to end. Tested on **synthetic** recordings (`backend/tests/synthetic.py`). |
| Scroll tracking, dedup, identical-twin separation, re-import idempotency | Unit- and integration-tested |
| Appraisal bar → IV, with CP/HP cross-check | Tested on synthetic bars (exact for 0–15; boundary readings become ranges) |
| **Real recording #1** (iPhone, 1126×2436, 4 min 51 s, detail pages with appraisal open) | 84 individuals found, 81 ready, 3 genuinely ambiguous (Pikachu costumes, Grimer/Vulpix with the type line hidden). 82 exact IVs, each consistent with the bars, the star badge **and** the CP/HP math. Star tier matched the IV total on 83/84 (the miss was a mid-animation frame, handled as a minimum). Spot checks against the frames: all correct. |
| Real storage grid | Only briefly present in recording #1 (4 frames, about 15 Pokémon). Works, but needs a longer grid-scroll recording to judge properly. |
| Shiny / favorite icons | Not detected. Always "unknown" unless you set them. |
| Lucky / shadow | Text cues only (`LUCKY`, `PURIFY` button). Not yet seen in a real recording. |
| PostgreSQL backend | Supported by configuration but not yet exercised by the test suite |

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
```

```bash
cd frontend && npm install && npm run build
```

```bash
cd backend && POKEVAULT_PASSWORD=choose-a-password ../.venv/bin/python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000. For frontend hot reload, run `npm run dev` in
`frontend/`; it proxies `/api` to port 8000.

Tests (36: OCR parsing, grouping, dedup, identity, queue, appraisal, end-to-end, repeated upload):

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

## Deploying on Replit

1. Import the project into a Repl. `.replit` and `replit.nix` install Python 3.11, Node 20 and FFmpeg.
2. Add Secrets:
   * `POKEVAULT_PASSWORD`: your owner password (required).
   * `POKEVAULT_SECRET_KEY`: a long random string, so sessions survive restarts.
   * `POKEVAULT_DATABASE_URL`: **recommended.** Create a Replit PostgreSQL database and paste its URL here.
     Replit deployment filesystems are reset on every redeploy, so a SQLite file inside the
     deployment would lose your collection. SQLite is fine for local use or for a host with a persistent disk.
3. Deploy as a **Reserved VM**, not Autoscale. Processing runs in a background worker inside the app process,
   and Autoscale's scale-to-zero would interrupt it.

Evidence frames live on the deployment's disk and disappear on redeploy. That
fits the "minimize retained images" goal: the collection keeps every confirmed
value with its confidence, and only the screenshots go.

**Alternative host:** any small VM with a persistent disk (e.g. a Fly.io machine with a
volume, or a $5 VPS) can run `start.sh` directly with SQLite and no extra database.

## Privacy & security summary

* Single owner password. Signed HttpOnly SameSite=Strict cookie. Every write needs an `X-PokeVault` header (CSRF).
  Login attempts are rate-limited.
* Uploads are streamed with size, duration, hourly-count and concurrency limits, and checked for MOV/MP4 magic bytes.
* Raw videos are deleted after processing by default. A failed or cancelled scan keeps its video for Retry,
  and the video is swept after 24 hours.
* Only keyframes used as evidence are kept (downscaled). They are served only through authenticated endpoints with `no-store`.
* No third-party scripts, fonts, analytics or AI calls. The CSP blocks anything not same-origin.
* Settings has Export-all (JSON) and Delete-all (type `DELETE ALL`).

## Automation (iPhone Shortcut)

Settings → **Automation** can:

* **Create an upload key.** It's upload-only: it can't read, change or delete anything, and only its hash is stored.
  The Settings page then walks you through a Shortcut that sends your latest screen recording to
  `POST /api/scans?filename=recording.mp4` with `Authorization: Bearer pvu_…`. An iOS automation runs it
  whenever Pokémon GO **is closed**. Re-sending the same recording is detected and ignored.
* **Auto-confirm confident results** (off by default). Items with clearly read species and CP, no review
  reasons and no possible match to an existing Pokémon go straight into the vault, marked `auto`.
  Use "mark as checked" on a Pokémon to promote it. Everything uncertain still waits in Review.
* **Auto-link re-sightings** (part of the same setting). A re-scanned Pokémon is linked to its existing entry
  only when exactly one stored Pokémon matches species, form, CP and nickname (both blank counts as a match),
  HP agrees when visible, nothing else in the recording claims that Pokémon, and there are no other review
  reasons. Identical twins, unclear forms and nickname-only items still go to Review. Linking never
  downgrades a Pokémon you already checked.

iOS doesn't let apps or Shortcuts start screen recording, so starting the recording stays a manual tap.
The Shortcut steps have not been tested on a device yet. The server side was tested by sending the same
request the Shortcut makes.

## Recording tips

* Storage grid: scroll slowly and pause about half a second on each screen.
* Tap a Pokémon to open its detail page. That adds HP and candy family, which pin down species and form.
* Tap **Appraise** on the detail page to capture IV bars.

## Updating game data

```bash
cd backend && ../.venv/bin/python scripts/update_gamedata.py
```

Rankings show "provisional" when the data is older than `POKEVAULT_GAMEDATA_STALE_DAYS` (default 45).
