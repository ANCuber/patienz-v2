"""Deterministic demographic consistency checks for generated cases.

Pure functions (no Streamlit) so they are unit-testable. Fixes the reported
pediatric inconsistency ("標註53歲、生日2013、回答11歲") by making 年齡 and 生日
authoritative and mutually consistent before the case is shown, and exposes an
``is_pediatric`` flag the patient model uses to add a guardian/age-pinning note.
"""
import datetime

AGE_MIN = 0
AGE_MAX = 120


def parse_birthday(value):
    """Parse a 生日 string (expects YYYY/MM/DD; tolerates - or .) into a date."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("-", "/").replace(".", "/")
    parts = [p for p in text.split("/") if p]
    if len(parts) < 1:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        month = min(max(month, 1), 12)
        day = min(max(day, 1), 28)
        return datetime.date(year, month, day)
    except (ValueError, TypeError):
        return None


def age_on(birthday, today):
    """Age in completed years of ``birthday`` as of ``today``."""
    age = today.year - birthday.year
    if (today.month, today.day) < (birthday.month, birthday.day):
        age -= 1
    return age


def _coerce_age(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def validate_demographics(data, today=None):
    """Make 基本資訊.年齡 / 生日 authoritative and consistent.

    - Coerce 年齡 to int and clamp to [AGE_MIN, AGE_MAX].
    - If 生日 is unparseable or implies an age differing from 年齡 by > 1 year,
      regenerate 生日 deterministically from 年齡 (keeping month/day when possible).

    Returns ``{"age": int|None, "is_pediatric": bool, "fixed": bool}``.
    """
    if today is None:
        today = datetime.date.today()
    result = {"age": None, "is_pediatric": False, "fixed": False}

    basic = data.get("基本資訊") if isinstance(data, dict) else None
    if not isinstance(basic, dict):
        return result

    age = _coerce_age(basic.get("年齡"))
    if age is None:
        return result

    clamped = min(max(age, AGE_MIN), AGE_MAX)
    if clamped != age:
        age = clamped
        basic["年齡"] = age
        result["fixed"] = True
    elif basic.get("年齡") != age:
        basic["年齡"] = age  # normalize "53" -> 53

    result["age"] = age
    result["is_pediatric"] = age < 18

    birthday = parse_birthday(basic.get("生日"))
    needs_fix = birthday is None or abs(age_on(birthday, today) - age) > 1
    if needs_fix:
        month = birthday.month if birthday is not None else 6
        day = birthday.day if birthday is not None else 15
        new_birthday = datetime.date(today.year - age, month, min(day, 28))
        basic["生日"] = new_birthday.strftime("%Y/%m/%d")
        result["fixed"] = True

    return result
