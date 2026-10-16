"""Pure text parsing for OCR output. No image access, so it is fully unit-testable.

Every parser returns a value and a confidence, or None when the text does not
support a value. Parsers never guess.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Digits OCR commonly confuses with letters in the Pokémon GO font.
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "!": "1",
                               "Z": "2", "S": "5", "s": "5", "B": "8", "G": "6", "T": "7"})

MAX_PLAUSIBLE_CP = 6000  # comfortably above the highest current CP; values above this are rejected
MIN_CP = 10


@dataclass
class Parsed:
    value: object
    confidence: float
    method: str


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def _digits(text: str) -> Tuple[str, int]:
    """Return digits after fixing common confusions, plus the number of substitutions made."""
    fixed = text.translate(_DIGIT_FIXES)
    subs = sum(1 for a, b in zip(text, fixed) if a != b)
    return re.sub(r"[^0-9]", "", fixed), subs


_CP_RE = re.compile(r"^\s*[Cc][Pp]\s*[:.]?\s*([0-9OoDQIl|!ZSsBGT]{2,5})\s*$")
_CP_PREFIX_ONLY = re.compile(r"^\s*[Cc][Pp]\s*$")


def parse_cp(text: str, ocr_conf: float = 1.0) -> Optional[Parsed]:
    """Parse 'CP 1523' / 'CP1523' / 'CP 15Z3' / '. CP601' -> number."""
    # OCR sometimes attaches stray punctuation from the sprite or background in front of "CP".
    m = _CP_RE.match(re.sub(r"^[^A-Za-z0-9]+", "", normalize(text)))
    if not m:
        return None
    digits, subs = _digits(m.group(1))
    if not digits or len(digits) > 4 and int(digits) > MAX_PLAUSIBLE_CP:
        return None
    value = int(digits)
    if not (MIN_CP <= value <= MAX_PLAUSIBLE_CP):
        return None
    conf = ocr_conf * (0.8 ** subs)
    return Parsed(value, round(conf, 4), "ocr:cp" + (f"+{subs}fix" if subs else ""))


def parse_cp_number(text: str, ocr_conf: float = 1.0) -> Optional[Parsed]:
    """Parse a bare number token that sits right after a separate 'CP' token."""
    t = normalize(text)
    if not re.fullmatch(r"[0-9OoDQIl|!ZSsBGT]{2,4}", t):
        return None
    digits, subs = _digits(t)
    if not digits:
        return None
    value = int(digits)
    if not (MIN_CP <= value <= MAX_PLAUSIBLE_CP):
        return None
    # Pure digits are trusted more than letter-fixed ones.
    return Parsed(value, round(ocr_conf * (0.8 ** subs) * 0.95, 4), "ocr:cp-split" + (f"+{subs}fix" if subs else ""))


def is_cp_label(text: str) -> bool:
    # Tolerate stray punctuation the OCR attaches to the small "CP" glyphs (e.g. "CP（").
    return re.sub(r"[^A-Za-z0-9]", "", normalize(text)).upper() == "CP"


_HP_RE = re.compile(r"([0-9OoIlSZB]{1,4})\s*/\s*([0-9OoIlSZB]{1,4})\s*H\s*P", re.IGNORECASE)


def parse_hp(text: str, ocr_conf: float = 1.0) -> Optional[Parsed]:
    """Parse '123 / 145 HP'. Returns the *maximum* HP (current HP can be reduced by damage)."""
    m = _HP_RE.search(normalize(text))
    if not m:
        return None
    cur, s1 = _digits(m.group(1))
    mx, s2 = _digits(m.group(2))
    if not cur or not mx:
        return None
    cur_v, max_v = int(cur), int(mx)
    if max_v < 10 or max_v > 1000 or cur_v > max_v:
        return None
    return Parsed(max_v, round(ocr_conf * (0.8 ** (s1 + s2)), 4), "ocr:hp")


_CANDY_RE = re.compile(r"^(.+?)\s*CANDY(?:\s+XL)?$", re.IGNORECASE)


def parse_candy_family(text: str, ocr_conf: float = 1.0) -> Optional[Parsed]:
    """'BULBASAUR CANDY' -> 'BULBASAUR' (the family's base species name)."""
    t = normalize(text).upper()
    m = _CANDY_RE.match(t)
    if not m:
        return None
    name = m.group(1).strip()
    if not re.search(r"[A-Z]", name) or name in {"RARE", "XL"}:
        return None
    return Parsed(name, round(ocr_conf, 4), "ocr:candy")


def contains_word(tokens: Sequence[str], word: str) -> bool:
    w = word.upper()
    return any(w in normalize(t).upper() for t in tokens)


# Fuzzy name matching -----------------------------------------------------------

def _clean_name(s: str) -> str:
    s = normalize(s).upper()
    s = s.replace("♀", " F").replace("♂", " M")
    return re.sub(r"[^A-Z0-9 ]", "", s).strip()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_similarity(a: str, b: str) -> float:
    a, b = _clean_name(a), _clean_name(b)
    if not a or not b:
        return 0.0
    d = levenshtein(a, b)
    return max(0.0, 1.0 - d / max(len(a), len(b)))


def best_name_matches(text: str, names: Sequence[str], limit: int = 3) -> List[Tuple[str, float]]:
    scored = [(n, name_similarity(text, n)) for n in names]
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def looks_like_name(text: str) -> bool:
    """Heuristic filter for name-like tokens (Pokémon GO nicknames are at most 12 characters)."""
    t = normalize(text)
    if not (1 <= len(t) <= 14):
        return False
    if re.fullmatch(r"[0-9 /]+", t):
        return False
    if is_cp_label(t) or _CP_RE.match(t):
        return False
    if re.fullmatch(r"[0-9.,]+\s*(kg|m|cm|hp)", t, re.I):
        return False
    return bool(re.search(r"[A-Za-z]", t))
