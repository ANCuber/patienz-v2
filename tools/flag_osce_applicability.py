"""一次性腳本：依 name_en 關鍵字為每個 milestone JSON 的子能力加上
`applicable_in_osce` 旗標。不適用 OSCE 單次模擬情境的子能力標為 false。

排除規則（domain + name_en 關鍵字 case-insensitive contains）：
1. 任何 domain：name 含 "digital health" → 不適用（無 EHR/Telehealth）
2. 任何 domain：name 同時含 "physician role" 與 "health care system" → 不適用（政策層面）
3. domain == "PROF" 且 name 含「身心健康/自我覺察類」關鍵字 → 不適用
4. 任何 domain：name 含 "interprofessional" 與 "team communication" → 不適用（無團隊）
5. 任何 domain：name 含 "reflective practice" → 不適用（單次練習無法評）

執行：python tools/flag_osce_applicability.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MS_DIR = os.path.normpath(os.path.join(HERE, "..", "config", "acgme_milestones"))


def is_osce_inapplicable(domain: str, name_en: str) -> tuple[bool, str]:
    """回傳 (是否不適用, 說明)。"""
    name = (name_en or "").lower()
    domain = (domain or "").upper()

    if "digital health" in name:
        return True, "Digital Health/Telehealth/EHR"
    if "physician role" in name and "health care system" in name:
        return True, "Physician Role in Health Care Systems"
    if "interprofessional" in name and "team communication" in name:
        return True, "Interprofessional and Team Communication"
    if "reflective practice" in name:
        return True, "Reflective Practice"
    if domain == "PROF":
        wellness_kw = [
            "well-being", "well being", "wellness", "resiliency",
            "help-seeking", "help seeking", "self-awareness",
        ]
        if any(kw in name for kw in wellness_kw):
            return True, "Wellness/Self-Awareness"
    return False, ""


def update_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    counts = {"applicable": 0, "inapplicable": 0, "tagged_reasons": []}
    for sub in data.get("subcompetencies", []):
        name_en = sub.get("name_en", "")
        domain = sub.get("domain", "")
        bad, reason = is_osce_inapplicable(domain, name_en)
        sub["applicable_in_osce"] = not bad
        if bad:
            counts["inapplicable"] += 1
            counts["tagged_reasons"].append(f"  - {sub['id']} ({domain}) {name_en} → {reason}")
        else:
            counts["applicable"] += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return counts


def main():
    files = sorted(
        f for f in os.listdir(MS_DIR)
        if f.endswith(".json") and not f.startswith("_")
    )
    grand_total_applicable = 0
    grand_total_inapplicable = 0
    for fn in files:
        path = os.path.join(MS_DIR, fn)
        counts = update_file(path)
        grand_total_applicable += counts["applicable"]
        grand_total_inapplicable += counts["inapplicable"]
        print(f"=== {fn} ===")
        print(f"  applicable={counts['applicable']}, inapplicable={counts['inapplicable']}")
        for line in counts["tagged_reasons"]:
            print(line)
    print()
    print(f"TOTAL applicable={grand_total_applicable}, inapplicable={grand_total_inapplicable}")


if __name__ == "__main__":
    main()
