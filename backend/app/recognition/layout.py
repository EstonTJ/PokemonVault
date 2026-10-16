"""Layout constants for Pokémon GO screens, as fractions of the normalized frame.

These are initial values from the public game UI. They MUST be calibrated
against real sample recordings (milestone M5) before accuracy claims are made.
Everything is relative, so the values survive device resolution and display
scaling once frames are normalized to a fixed width.
"""

GRID_COLUMNS = 3
GRID_MIN_CP_TOKENS = 3          # need at least this many CP labels to call a frame a grid
GRID_CONTENT_TOP = 0.14         # header (title, search, tabs) sits above this
GRID_CONTENT_BOTTOM = 0.90      # bottom bar / close button below this
GRID_NAME_MAX_BELOW = 1.35      # name must be within this many column-widths below its CP label

DETAIL_CP_MAX_Y = 0.22          # big "CP ####" header on detail screens
DETAIL_NAME_BAND = (0.30, 0.62)  # vertical band where the species / nickname appears
DETAIL_KEYWORDS = ("STARDUST", "POWER UP", "CANDY", "EVOLVE", "WEIGHT", "HEIGHT")
DETAIL_MIN_KEYWORDS = 2

APPRAISAL_KEYWORDS = ("ATTACK", "DEFENSE", "HP")
APPRAISAL_BAND = (0.55, 0.98)

# Words that appear in the name band but are never names.
NON_NAME_WORDS = {
    "WEIGHT", "HEIGHT", "STARDUST", "CANDY", "POWER UP", "EVOLVE", "PURIFY", "LUCKY", "LUCKY POKEMON",
    "NORMAL", "FIRE", "WATER", "GRASS", "ELECTRIC", "ICE", "FIGHTING", "POISON", "GROUND", "FLYING", "PSYCHIC",
    "BUG", "ROCK", "GHOST", "DRAGON", "DARK", "STEEL", "FAIRY", "HP", "KG", "M", "ATTACK", "DEFENSE", "XL",
    "SHADOW", "PURIFIED", "NEW ATTACK", "APPRAISE", "TRANSFER",
}
