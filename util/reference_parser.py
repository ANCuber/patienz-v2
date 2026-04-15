"""
參考值解析模組：解析 examination.csv 中的參考值字串，
判定檢測值是否異常或達到危急值。
"""

import re


# 危急值定義（需立即通知醫師的極端異常值）
CRITICAL_VALUES = {
    "Potassium, K": {"low": 2.5, "high": 6.5},
    "Sodium, Na": {"low": 120, "high": 160},
    "Glucose (A.C.)": {"low": 40, "high": 500},
    "Hb": {"low": 7.0},
    "Platelet": {"low": 50},
    "pH": {"low": 7.2, "high": 7.6},
    "Calcium, Ca": {"low": 1.6, "high": 3.2},
    "pCO2": {"low": 20, "high": 70},
    "pO2": {"low": 40},
    "HCO3-": {"low": 10, "high": 40},
    "WBC": {"low": 2.0, "high": 30.0},
    "INR": {"high": 5.0},
    "aPTT": {"high": 70},
    "Creatinine (Serum)": {"high": 10.0},
    "BUN": {"high": 100},
    "Total bilirubin": {"high": 15.0},
    "Ammonia": {"high": 100},
    "Lactic acid (Lactate)": {"high": 4.0},
    "D-dimer": {"high": 5.0},
    "Fibrinogen": {"low": 100},
    "CSF Cell Count (WBC)": {"high": 100},
}


def _try_float(s: str):
    """嘗試將字串轉為浮點數，失敗返回 None。"""
    try:
        return float(s.strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_reference(ref_string: str) -> dict:
    """
    解析參考值字串為結構化格式。

    回傳格式:
    - {"type": "range", "low": float, "high": float}
    - {"type": "gender", "男": {"low": ..., "high": ...}, "女": {"low": ..., "high": ...}}
    - {"type": "upper", "high": float}
    - {"type": "lower", "low": float}
    - {"type": "qualitative", "normal": str}
    - {"type": "descriptive", "text": str}
    """
    if not ref_string or not ref_string.strip():
        return {"type": "descriptive", "text": ""}

    ref = ref_string.strip()

    # 性別分類: "男：X~Y；女：X~Y" 或 "男：X~Y ; 女：X~Y" 或 "男：X~Y女：X~Y"
    gender_pattern = r'男[：:][\s]*([0-9.]+)\s*[~\-]\s*([0-9.]+)\s*[；;]?\s*女[：:][\s]*([0-9.]+)\s*[~\-]\s*([0-9.]+)'
    m = re.search(gender_pattern, ref)
    if m:
        return {
            "type": "gender",
            "男": {"low": float(m.group(1)), "high": float(m.group(2))},
            "女": {"low": float(m.group(3)), "high": float(m.group(4))},
        }

    # 性別分類（僅上限）: "男：< X ; 女：< Y"
    gender_upper = r'男[：:][\s]*<\s*([0-9.]+)\s*[；;]\s*女[：:][\s]*<\s*([0-9.]+)'
    m = re.search(gender_upper, ref)
    if m:
        return {
            "type": "gender",
            "男": {"low": None, "high": float(m.group(1))},
            "女": {"low": None, "high": float(m.group(2))},
        }

    # 定性結果: （-）, (-)
    if re.search(r'[（(]\s*[-\-]\s*[）)]', ref):
        return {"type": "qualitative", "normal": "(-)"}

    # 上限: "< X" 或 "≤ X"
    m = re.match(r'^[<≤]\s*([0-9.]+)', ref)
    if m:
        return {"type": "upper", "high": float(m.group(1))}

    # 下限: "> X" 或 "≥ X"
    m = re.match(r'^[>≥]\s*([0-9.]+)', ref)
    if m:
        return {"type": "lower", "low": float(m.group(1))}

    # 範圍: "X~Y" 或 "X-Y" 或 "X ~ Y"（排除已經被性別匹配的）
    range_pattern = r'^([0-9.]+)\s*[~\-]\s*([0-9.]+)$'
    m = re.match(range_pattern, ref)
    if m:
        return {"type": "range", "low": float(m.group(1)), "high": float(m.group(2))}

    # 帶有括號的範圍（如 "Normal（< 30）"）
    m = re.search(r'[<≤]\s*([0-9.]+)', ref)
    if m:
        return {"type": "upper", "high": float(m.group(1))}

    m = re.search(r'[>≥]\s*([0-9.]+)', ref)
    if m:
        return {"type": "lower", "low": float(m.group(1))}

    # 嘗試最後的範圍匹配（可能有前後文字）
    m = re.search(r'([0-9.]+)\s*[~\-]\s*([0-9.]+)', ref)
    if m:
        return {"type": "range", "low": float(m.group(1)), "high": float(m.group(2))}

    # 無法解析：標記為描述型
    return {"type": "descriptive", "text": ref}


def is_abnormal(value: str, reference: dict, gender: str = None) -> tuple:
    """
    判斷檢測值是否異常。

    Args:
        value: 檢測值字串
        reference: parse_reference() 的回傳結果
        gender: 病人性別（"男" 或 "女"），用於性別分類的參考值

    Returns:
        (is_abnormal: bool, direction: str)
        direction: "↑"（偏高）, "↓"（偏低）, ""（正常或無法判斷）
    """
    if not value or not reference:
        return (False, "")

    ref_type = reference.get("type", "descriptive")

    # 定性結果
    if ref_type == "qualitative":
        val_clean = value.strip().replace("（", "(").replace("）", ")")
        if "(+)" in val_clean or "Positive" in val_clean.lower():
            return (True, "↑")
        return (False, "")

    # 描述型（無法程式化判斷）
    if ref_type == "descriptive":
        return (False, "")

    # 嘗試提取數值
    num_val = _extract_numeric(value)
    if num_val is None:
        return (False, "")

    # 取得適用的範圍
    if ref_type == "gender":
        if gender and gender in reference:
            ref_range = reference[gender]
        else:
            # 預設用男性範圍
            ref_range = reference.get("男", reference.get("女", {}))
        low = ref_range.get("low")
        high = ref_range.get("high")
    elif ref_type == "range":
        low = reference.get("low")
        high = reference.get("high")
    elif ref_type == "upper":
        low = None
        high = reference.get("high")
    elif ref_type == "lower":
        low = reference.get("low")
        high = None
    else:
        return (False, "")

    if high is not None and num_val > high:
        return (True, "↑")
    if low is not None and num_val < low:
        return (True, "↓")

    return (False, "")


def is_critical(value: str, exam_name: str) -> bool:
    """
    判斷檢測值是否達到危急值。

    Args:
        value: 檢測值字串
        exam_name: 檢查項目英文名

    Returns:
        True 如果達到危急值
    """
    if exam_name not in CRITICAL_VALUES:
        return False

    num_val = _extract_numeric(value)
    if num_val is None:
        return False

    crit = CRITICAL_VALUES[exam_name]
    if "low" in crit and num_val < crit["low"]:
        return True
    if "high" in crit and num_val > crit["high"]:
        return True

    return False


def _extract_numeric(value: str):
    """從檢測值字串中提取數值。"""
    if not value:
        return None
    # 移除常見的非數字前綴/後綴
    val = value.strip()
    # 處理 ↑ ↓ ⚠ 等符號
    val = re.sub(r'[↑↓⚠\s]', '', val)
    # 嘗試直接轉換
    result = _try_float(val)
    if result is not None:
        return result
    # 嘗試提取第一個數字
    m = re.search(r'[-]?[0-9]+\.?[0-9]*', val)
    if m:
        return float(m.group())
    return None
