"""ACGME 子能力 Level 彙總到 6 大 Domain。

純函式、無 streamlit / Gemini 相依，可獨立測試（python util/acgme_aggregator.py）。
"""
import json
from collections import defaultdict

DOMAINS = ["PC", "MK", "PBLI", "ICS", "PROF", "SBP"]

# 當某 domain 已評估子能力數低於此門檻時，平均 Level 不具代表性，
# UI 應顯示「資料不足」而非實際數值。
INSUFFICIENT_DATA_THRESHOLD = 2


def _domain_totals_from_milestone(milestone_data: dict) -> dict:
    totals = defaultdict(int)
    for sub in milestone_data.get("subcompetencies", []):
        totals[sub["domain"]] += 1
    return dict(totals)


def aggregate_to_domains(grader_response: list, milestone_data: dict) -> dict:
    """彙總 LLM 子能力評級到 Domain。

    grader_response: list[dict]，每項含 subcompetency_id, domain, level (0-5)
    milestone_data: 過濾後 milestone JSON（已排除 OSCE 不適用子能力），
                    用於計算 coverage 分母

    回傳每個 domain 的彙總，含 `insufficient_data` 旗標：
    當 `assessed_count < INSUFFICIENT_DATA_THRESHOLD` 時為 True，
    UI 應視為資料不足、不顯示平均 Level。
    """
    totals_per_domain = _domain_totals_from_milestone(milestone_data)
    levels_per_domain = {d: [] for d in DOMAINS}
    items_per_domain = {d: [] for d in DOMAINS}

    for item in grader_response:
        d = item.get("domain")
        if d not in DOMAINS:
            continue
        items_per_domain[d].append(item)
        lvl = int(item.get("level", 0) or 0)
        if 1 <= lvl <= 5:
            levels_per_domain[d].append(lvl)

    result = {}
    for d in DOMAINS:
        levels = levels_per_domain[d]
        denom = totals_per_domain.get(d, 0)
        if levels:
            avg = round(sum(levels) / len(levels), 1)
            sorted_lvls = sorted(levels)
            median = sorted_lvls[len(sorted_lvls) // 2]
        else:
            avg = 0.0
            median = 0
        assessed = len(levels)
        result[d] = {
            "average_level": avg,
            "median_level": median,
            "assessed_count": assessed,
            "total_count": denom,
            "coverage_pct": round(assessed / denom * 100) if denom else 0,
            "insufficient_data": assessed < INSUFFICIENT_DATA_THRESHOLD,
            "items": items_per_domain[d],
        }
    return result


def overall_coverage(domain_summary: dict) -> tuple:
    assessed = sum(d["assessed_count"] for d in domain_summary.values())
    total = sum(d["total_count"] for d in domain_summary.values())
    return assessed, total


def reconcile_missing_subcompetencies(grader_response: list, milestone_data: dict) -> list:
    """補上 LLM 漏報的子能力（level=0）。"""
    seen = {item.get("subcompetency_id") for item in grader_response}
    reconciled = list(grader_response)
    for sub in milestone_data.get("subcompetencies", []):
        if sub["id"] not in seen:
            reconciled.append({
                "subcompetency_id": sub["id"],
                "subcompetency_name": sub.get("name_zh", sub["id"]),
                "domain": sub["domain"],
                "level": 0,
                "level_rationale": "（LLM 未回報此子能力）",
                "evidence": "",
                "improvement": "",
            })
    return reconciled


# ---------------- self-test ----------------
if __name__ == "__main__":
    import os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    milestone_path = os.path.join(here, "..", "config", "acgme_milestones", "internal_medicine.json")
    milestone = json.load(open(milestone_path, encoding="utf-8"))

    fake = [
        {"subcompetency_id": "PC1", "domain": "PC", "level": 4, "evidence": "..."},
        {"subcompetency_id": "PC2", "domain": "PC", "level": 3},
        {"subcompetency_id": "PC3", "domain": "PC", "level": 4},
        {"subcompetency_id": "PC4", "domain": "PC", "level": 3},
        {"subcompetency_id": "PC5", "domain": "PC", "level": 0},
        {"subcompetency_id": "MK1", "domain": "MK", "level": 4},
        {"subcompetency_id": "MK2", "domain": "MK", "level": 4},
        {"subcompetency_id": "PBLI1", "domain": "PBLI", "level": 2},
        {"subcompetency_id": "PBLI2", "domain": "PBLI", "level": 3},
        {"subcompetency_id": "ICS1", "domain": "ICS", "level": 4},
        {"subcompetency_id": "ICS2", "domain": "ICS", "level": 3},
        {"subcompetency_id": "ICS3", "domain": "ICS", "level": 3},
        {"subcompetency_id": "PROF1", "domain": "PROF", "level": 4},
        {"subcompetency_id": "PROF2", "domain": "PROF", "level": 4},
        {"subcompetency_id": "PROF3", "domain": "PROF", "level": 3},
        {"subcompetency_id": "PROF4", "domain": "PROF", "level": 0},
        {"subcompetency_id": "SBP1", "domain": "SBP", "level": 3},
        {"subcompetency_id": "SBP2", "domain": "SBP", "level": 3},
        {"subcompetency_id": "SBP3", "domain": "SBP", "level": 0},
    ]

    summary = aggregate_to_domains(fake, milestone)
    expected = {
        "PC":   (3.5, 4),   # [4,3,4,3] avg=3.5
        "MK":   (4.0, 2),
        "PBLI": (2.5, 2),
        "ICS":  (10/3, 3),
        "PROF": (11/3, 3),
        "SBP":  (3.0, 2),
    }
    failures = []
    for d, (exp_avg, exp_assessed) in expected.items():
        got = summary[d]
        if abs(got["average_level"] - round(exp_avg, 1)) > 0.05:
            failures.append(f"{d}: avg got {got['average_level']}, expected {exp_avg}")
        if got["assessed_count"] != exp_assessed:
            failures.append(f"{d}: assessed got {got['assessed_count']}, expected {exp_assessed}")

    print("=== aggregate_to_domains test ===")
    for d, info in summary.items():
        flag = " [INSUFFICIENT]" if info["insufficient_data"] else ""
        print(f"  {d}: avg={info['average_level']} median={info['median_level']} "
              f"coverage={info['assessed_count']}/{info['total_count']} "
              f"({info['coverage_pct']}%){flag}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\nPASS aggregate_to_domains")

    # === 資料不足門檻測試 ===
    sparse = [
        {"subcompetency_id": "PC1", "domain": "PC", "level": 3},
        {"subcompetency_id": "PC2", "domain": "PC", "level": 4},
        {"subcompetency_id": "PBLI1", "domain": "PBLI", "level": 2},  # 只有 1 項
    ]
    sparse_summary = aggregate_to_domains(sparse, milestone)
    assert sparse_summary["PC"]["insufficient_data"] is False, \
        f"PC 有 2 項應為足夠，但 got insufficient_data={sparse_summary['PC']['insufficient_data']}"
    assert sparse_summary["PBLI"]["insufficient_data"] is True, \
        f"PBLI 只 1 項應為資料不足，但 got insufficient_data={sparse_summary['PBLI']['insufficient_data']}"
    assert sparse_summary["MK"]["insufficient_data"] is True, \
        f"MK 0 項應為資料不足，但 got insufficient_data={sparse_summary['MK']['insufficient_data']}"
    print("PASS insufficient_data threshold (<2)")

    # reconcile test：過濾後的 milestone 應仍能 reconcile
    short = [{"subcompetency_id": "PC1", "domain": "PC", "level": 3}]
    fixed = reconcile_missing_subcompetencies(short, milestone)
    expected_total = len(milestone.get("subcompetencies", []))
    assert len(fixed) == expected_total, f"expected {expected_total}, got {len(fixed)}"
    print(f"PASS reconcile_missing_subcompetencies (filled to {expected_total})")
