"""Tests for the ETL cleaning logic in scripts/clean_data.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from clean_data import norm_key, parse_bool, title_case_category, to_nullable_int


def test_norm_key_folds_case_and_whitespace():
    assert norm_key("  NovaPay ") == norm_key("novapay")
    assert norm_key("Elena Rossi") == "elena rossi"


def test_parse_bool_accepts_multiple_truthy_spellings():
    assert parse_bool("yes") is True
    assert parse_bool("Y") is True
    assert parse_bool("TRUE") is True
    assert parse_bool("FALSE") is False
    assert parse_bool("no") is False


def test_title_case_category_normalizes_known_acronym():
    assert title_case_category("ai") == "AI"


def test_to_nullable_int_coerces_float_strings():
    assert to_nullable_int("2016.0") == 2016
