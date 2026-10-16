"""Shared input validation for every path that writes Pokémon data.

Collection edits, manual creation, review-queue corrections and confirmations all go through
`clean_pokemon_input` (shape and ranges) and `check_pokemon_invariants` (cross-field rules). The database
CHECK constraints in models.Pokemon are the final backstop for anything that slips past.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from .recommend.gamedata import get_gamedata

CP_RANGE = (10, 6000)
HP_RANGE = (10, 1000)
IV_RANGE = (0, 15)
NICKNAME_MAX = 24  # the game allows 12; allow slack for OCR'd text the user is correcting
NOTES_MAX = 2000
IV_FIELDS = ("iv_atk", "iv_def", "iv_sta")


class PokemonInput(BaseModel):
    """Every user-editable Pokémon field. Unknown fields and wrong types are rejected (no "500" for 500)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    species_id: Optional[StrictStr] = None
    nickname: Optional[StrictStr] = Field(default=None, max_length=NICKNAME_MAX)
    cp: Optional[StrictInt] = Field(default=None, ge=CP_RANGE[0], le=CP_RANGE[1])
    hp: Optional[StrictInt] = Field(default=None, ge=HP_RANGE[0], le=HP_RANGE[1])
    iv_atk: Optional[StrictInt] = Field(default=None, ge=IV_RANGE[0], le=IV_RANGE[1])
    iv_def: Optional[StrictInt] = Field(default=None, ge=IV_RANGE[0], le=IV_RANGE[1])
    iv_sta: Optional[StrictInt] = Field(default=None, ge=IV_RANGE[0], le=IV_RANGE[1])
    shiny: Optional[StrictBool] = None
    shadow: Optional[StrictBool] = None
    purified: Optional[StrictBool] = None
    lucky: Optional[StrictBool] = None
    favorite: Optional[StrictBool] = None
    notes: Optional[StrictStr] = Field(default=None, max_length=NOTES_MAX)

    @field_validator("species_id")
    @classmethod
    def _known_species(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and get_gamedata().get(v) is None:
            raise ValueError(f"unknown species {v!r}")
        return v

    @field_validator("nickname")
    @classmethod
    def _tidy_nickname(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class InvalidInput(ValueError):
    """User input that breaks a validation rule. The message is safe to show to the user."""


def clean_pokemon_input(data: Any, *, require_species: bool = False) -> Dict[str, Any]:
    """Validate a partial update/create payload. Returns only the fields that were provided, normalised."""
    if not isinstance(data, dict):
        raise InvalidInput("Expected a JSON object.")
    try:
        model = PokemonInput.model_validate(data)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err.get("loc", ())) or "input"
            problems.append(f"{field}: {err.get('msg', 'invalid')}")
        raise InvalidInput("; ".join(problems)) from None
    cleaned = model.model_dump(exclude_unset=True)
    if require_species and not cleaned.get("species_id"):
        raise InvalidInput("species_id: required")
    return cleaned


def check_pokemon_invariants(values: Dict[str, Any]) -> None:
    """Cross-field rules on the FINAL state of a Pokémon (after merging an edit)."""
    ivs = [values.get(f) for f in IV_FIELDS]
    if any(v is not None for v in ivs) and not all(v is not None for v in ivs):
        raise InvalidInput("IVs must be given for all three stats (attack, defense, HP) or none.")
    for f, (lo, hi) in (("cp", CP_RANGE), ("hp", HP_RANGE)):
        v = values.get(f)
        if v is not None and not (lo <= v <= hi):
            raise InvalidInput(f"{f}: must be between {lo} and {hi}")
    for f in IV_FIELDS:
        v = values.get(f)
        if v is not None and not (IV_RANGE[0] <= v <= IV_RANGE[1]):
            raise InvalidInput(f"{f}: must be between {IV_RANGE[0]} and {IV_RANGE[1]}")
    if values.get("shadow") and values.get("purified"):
        raise InvalidInput("A Pokémon can't be both shadow and purified.")
    if values.get("iv_status") == "verified" and not all(v is not None for v in ivs):
        raise InvalidInput("IVs can only be 'verified' when all three are known.")
