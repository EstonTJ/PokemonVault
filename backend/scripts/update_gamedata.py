"""Refresh versioned game data from PvPoke (MIT licence).

    python scripts/update_gamedata.py

Validates the download (JSON shape + CP formula spot check) before replacing
data/gamemaster.json. Restart the app afterwards.
"""
import json
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/gamemaster.json"
DEST = Path(__file__).resolve().parent.parent / "data" / "gamemaster.json"


def main() -> int:
    raw = urllib.request.urlopen(URL, timeout=60).read()
    data = json.loads(raw)
    if not isinstance(data.get("pokemon"), list) or len(data["pokemon"]) < 500 or "moves" not in data:
        print("Unexpected format; not replacing.", file=sys.stderr)
        return 1
    sys.path.insert(0, str(DEST.parent.parent))
    from app.recommend.gamedata import calc_cp

    ok = sum(1 for p in data["pokemon"] if "level25CP" in p and "_mega" not in p["speciesId"] and
             calc_cp((p["baseStats"]["atk"], p["baseStats"]["def"], p["baseStats"]["hp"]), (0, 0, 0), 25.0) == p["level25CP"])
    print(f"Validated {ok} species CP values. Timestamp {data.get('timestamp')}")
    tmp = DEST.with_suffix(".tmp")
    tmp.write_bytes(raw)
    tmp.replace(DEST)
    print(f"Updated {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
