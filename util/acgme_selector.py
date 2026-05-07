"""疾病/症狀 → ACGME milestone JSON 載入。

執行邏輯：依疾病關鍵字、再依症狀關鍵字決定 milestone 檔；對應檔不存在則 fallback 到 default。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MILESTONE_DIR = os.path.normpath(os.path.join(_HERE, "..", "config", "acgme_milestones"))
_MAPPING_FILE = os.path.join(_MILESTONE_DIR, "_disease_mapping.json")


def _load_mapping() -> dict:
    with open(_MAPPING_FILE, encoding="utf-8") as f:
        return json.load(f)


def _milestone_path(name: str) -> str:
    return os.path.join(_MILESTONE_DIR, f"{name}.json")


def _normalize_symptoms(symptoms) -> list:
    if not symptoms:
        return []
    if isinstance(symptoms, str):
        parts = symptoms.replace("、", ",").replace("，", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(symptoms, list):
        return [str(s).strip() for s in symptoms if str(s).strip()]
    return [str(symptoms).strip()]


def _match(text: str, keys) -> str:
    """回傳第一個出現於 text 內的 key，否則 None。"""
    if not text:
        return None
    for k in keys:
        if k and k in text:
            return k
    return None


def _is_osce_inapplicable_by_name(domain: str, name_en: str) -> bool:
    """後備規則：當 milestone JSON 缺 `applicable_in_osce` 旗標時，
    依 name_en 關鍵字判斷是否為 OSCE 單次模擬不適用之子能力。

    與 tools/flag_osce_applicability.py 同步維護。
    """
    name = (name_en or "").lower()
    domain = (domain or "").upper()
    if "digital health" in name:
        return True
    if "physician role" in name and "health care system" in name:
        return True
    if "interprofessional" in name and "team communication" in name:
        return True
    if "reflective practice" in name:
        return True
    if domain == "PROF":
        wellness_kw = [
            "well-being", "well being", "wellness", "resiliency",
            "help-seeking", "help seeking", "self-awareness",
        ]
        if any(kw in name for kw in wellness_kw):
            return True
    return False


def filter_applicable(milestone_data: dict) -> tuple:
    """過濾掉 OSCE 不適用之子能力。

    優先讀 JSON 內的 `applicable_in_osce` 旗標；未提供時 fallback 到 name_en 規則。

    回傳：(filtered_data, excluded_list)
      filtered_data: 與原 milestone_data 同結構，但 subcompetencies 只剩適用項目
      excluded_list: 被排除的子能力簡要清單，用於 log/UI 提示
    """
    filtered = {k: v for k, v in milestone_data.items() if k != "subcompetencies"}
    kept = []
    excluded = []
    for sub in milestone_data.get("subcompetencies", []):
        flag = sub.get("applicable_in_osce")
        if flag is None:
            applicable = not _is_osce_inapplicable_by_name(
                sub.get("domain", ""), sub.get("name_en", "")
            )
        else:
            applicable = bool(flag)
        if applicable:
            kept.append(sub)
        else:
            excluded.append({
                "id": sub.get("id"),
                "domain": sub.get("domain"),
                "name_en": sub.get("name_en", ""),
                "name_zh": sub.get("name_zh", ""),
            })
    filtered["subcompetencies"] = kept
    return filtered, excluded


def select_milestone(disease: str, symptoms=None, apply_osce_filter: bool = True) -> dict:
    """依疾病/症狀選擇 milestone 並載入；含 fallback。

    apply_osce_filter=True 時自動排除 OSCE 不適用子能力，並回傳排除清單。

    回傳：
      {
        "milestone_data": <載入的 JSON，已過濾>,
        "milestone_data_full": <未過濾原始 JSON，供 debug>,
        "milestone_name": <最終採用檔名（如 "internal_medicine"）>,
        "selection_reason": "disease match" | "symptom match" | "default",
        "matched_key": <命中的 key 或 None>,
        "fallback_reason": <若有 fallback 的說明，否則 None>,
        "excluded_subcompetencies": <被排除的子能力清單>,
      }
    """
    mapping = _load_mapping()
    default_name = mapping.get("default", "internal_medicine")
    disease = (disease or "").strip()
    symptom_list = _normalize_symptoms(symptoms)

    candidate = None
    selection_reason = "default"
    matched_key = None

    # 1. 疾病優先
    disease_map = mapping.get("disease_to_milestone", {})
    matched = _match(disease, list(disease_map.keys()))
    if matched:
        candidate = disease_map[matched]
        selection_reason = "disease match"
        matched_key = matched
    else:
        # 2. 症狀次之（取第一個命中的）
        symptom_map = mapping.get("symptom_to_milestone", {})
        for s in symptom_list:
            matched = _match(s, list(symptom_map.keys()))
            if matched:
                candidate = symptom_map[matched]
                selection_reason = "symptom match"
                matched_key = matched
                break

    if candidate is None:
        candidate = default_name

    fallback_reason = None
    final_name = candidate
    if not os.path.exists(_milestone_path(candidate)):
        fallback_reason = f"檔案 {candidate}.json 不存在，fallback 至 {default_name}"
        final_name = default_name

    final_path = _milestone_path(final_name)
    if not os.path.exists(final_path):
        raise FileNotFoundError(f"連預設 milestone 檔 {final_name}.json 也不存在；請檢查 {_MILESTONE_DIR}")

    with open(final_path, encoding="utf-8") as f:
        full_data = json.load(f)

    if apply_osce_filter:
        filtered_data, excluded = filter_applicable(full_data)
    else:
        filtered_data, excluded = full_data, []

    return {
        "milestone_data": filtered_data,
        "milestone_data_full": full_data,
        "milestone_name": final_name,
        "selection_reason": selection_reason,
        "matched_key": matched_key,
        "fallback_reason": fallback_reason,
        "excluded_subcompetencies": excluded,
    }


# ---------------- self-test ----------------
if __name__ == "__main__":
    cases = [
        ("肺炎", None),
        ("糖尿病", None),
        ("心臟衰竭", None),                    # 期待 cardiology → fallback internal_medicine
        ("焦慮", None),                         # 期待 psychiatry → fallback
        ("不存在的疾病", "胸痛、頭暈"),         # 期待 symptom 命中 cardiology → fallback
        ("不存在的疾病", ["頭暈", "腹瀉"]),     # 期待 default
        ("", None),
    ]
    for disease, symptoms in cases:
        try:
            r = select_milestone(disease, symptoms)
            print(f"[{disease!r}, {symptoms!r}] → {r['milestone_name']} "
                  f"(reason={r['selection_reason']}, matched={r['matched_key']!r}, "
                  f"fallback={r['fallback_reason']})")
        except Exception as e:
            print(f"[{disease!r}, {symptoms!r}] → ERROR {e}")
