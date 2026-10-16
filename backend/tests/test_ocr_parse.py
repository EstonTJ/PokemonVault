from app.ocr.parse import (is_cp_label, looks_like_name, name_similarity, parse_candy_family, parse_cp, parse_cp_number,
                           parse_hp)


def test_cp_basic_variants():
    assert parse_cp("CP 1523").value == 1523
    assert parse_cp("CP1523").value == 1523
    assert parse_cp("cp 87").value == 87
    assert parse_cp("CP: 2001").value == 2001


def test_cp_letter_confusions_lower_confidence():
    clean = parse_cp("CP 1503", 0.99)
    fixed = parse_cp("CP 15O3", 0.99)
    assert fixed.value == 1503
    assert fixed.confidence < clean.confidence
    assert "fix" in fixed.method


def test_cp_rejects_implausible_and_garbage():
    assert parse_cp("CP 9") is None          # below minimum CP
    assert parse_cp("CP 99999") is None      # too long / implausible
    assert parse_cp("HP 1500") is None
    assert parse_cp("1500") is None          # bare number needs the split path
    assert parse_cp("CP") is None


def test_split_cp_label_and_number():
    assert is_cp_label("CP")
    assert is_cp_label(" cp ")
    assert parse_cp_number("1234").value == 1234
    assert parse_cp_number("12a4") is None
    assert parse_cp_number("7") is None


def test_hp_returns_max_hp_and_validates():
    assert parse_hp("88 / 145 HP").value == 145
    assert parse_hp("145/145HP").value == 145
    assert parse_hp("146 / 145 HP") is None  # current cannot exceed max
    assert parse_hp("hello") is None


def test_candy_family():
    assert parse_candy_family("BULBASAUR CANDY").value == "BULBASAUR"
    assert parse_candy_family("Mr. Mime Candy").value == "MR. MIME"
    assert parse_candy_family("RARE CANDY") is None
    assert parse_candy_family("CANDY") is None


def test_name_similarity_and_filter():
    assert name_similarity("Bulbasaur", "BULBASAUR") == 1.0
    assert name_similarity("Bulbasaur", "Bu1basaur") > 0.85
    assert name_similarity("Sparky", "Pikachu") < 0.5
    assert looks_like_name("Sparky")
    assert not looks_like_name("CP 1500")
    assert not looks_like_name("123 / 145")
    assert not looks_like_name("")
