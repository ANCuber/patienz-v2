"""Pediatric / age-birthday consistency (util.demographics)."""
import datetime
from util.demographics import validate_demographics, parse_birthday, age_on

TODAY = datetime.date(2026, 6, 24)


def test_reported_bug_age53_birthday2013_gets_birthday_fixed():
    # The exact field report: 標註53歲、生日2013 (implies ~13y) → 生日 must be
    # regenerated to match 年齡=53 so the displayed identity is consistent.
    data = {"基本資訊": {"年齡": 53, "生日": "2013/05/10"}}
    r = validate_demographics(data, today=TODAY)
    assert r["age"] == 53 and r["is_pediatric"] is False
    assert r["fixed"] is True
    fixed_age = age_on(parse_birthday(data["基本資訊"]["生日"]), TODAY)
    assert abs(fixed_age - 53) <= 1


def test_true_pediatric_is_flagged():
    data = {"基本資訊": {"年齡": 11, "生日": "2015/03/01"}}
    r = validate_demographics(data, today=TODAY)
    assert r["age"] == 11 and r["is_pediatric"] is True
    # birthday already consistent (~11y) → not forcibly changed
    assert age_on(parse_birthday(data["基本資訊"]["生日"]), TODAY) in (11, 12)


def test_age_string_is_normalized_to_int():
    data = {"基本資訊": {"年齡": "53", "生日": ""}}
    validate_demographics(data, today=TODAY)
    assert data["基本資訊"]["年齡"] == 53


def test_age_clamped_into_range():
    data = {"基本資訊": {"年齡": 250, "生日": ""}}
    r = validate_demographics(data, today=TODAY)
    assert r["age"] == 120 and data["基本資訊"]["年齡"] == 120


def test_unparseable_birthday_is_regenerated():
    data = {"基本資訊": {"年齡": 40, "生日": "不知道"}}
    r = validate_demographics(data, today=TODAY)
    assert r["fixed"] is True
    assert age_on(parse_birthday(data["基本資訊"]["生日"]), TODAY) in (39, 40, 41)


def test_missing_basic_block_is_safe():
    assert validate_demographics({}, today=TODAY)["age"] is None
    assert validate_demographics({"基本資訊": "x"}, today=TODAY)["age"] is None
