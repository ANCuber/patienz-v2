"""驗證 acgme_selector 對使用者指定的所有症狀與疾病的對應結果。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from util.acgme_selector import select_milestone

SYMPTOMS = [
    "發燒", "心悸", "呼吸困難", "頭痛", "頭暈", "咳嗽", "胸痛", "食慾不振",
    "腹痛", "腹瀉", "便秘", "黃疸", "寡尿", "水腫", "體重減輕", "關節痛",
    "腰背痛", "貧血", "全身倦怠", "皮疹", "腫塊", "焦慮", "憂鬱", "睡眠障礙",
]
DISEASES = [
    "糖尿病", "高血壓", "敗血症", "肺炎", "尿路感染", "結核病", "蜂窩性組織炎",
    "意識障礙", "譫妄症", "腦血管疾病", "慢性阻塞肺病", "冠狀動脈心臟病",
    "瓣膜性心臟病", "心臟衰竭", "肝炎", "肝硬化", "消化道出血", "血尿",
    "呼吸衰竭", "氣喘", "腎衰竭", "褥瘡", "安寧照護",
]


def report(label: str, items, kind: str):
    print(f"\n{'='*88}\n  {label}\n{'='*88}")
    fb_count = 0
    print(f"{'#':<4}{'name':<14}{'milestone':<24}{'reason':<18}{'matched_key':<14}fallback")
    print("-" * 88)
    for i, name in enumerate(items, 1):
        if kind == "disease":
            r = select_milestone(name, None)
        else:
            r = select_milestone("", name)
        fb = (r["fallback_reason"] or "")[:30]
        if fb:
            fb_count += 1
        print(f"{i:<4}{name:<14}{r['milestone_name']:<24}{r['selection_reason']:<18}"
              f"{(r['matched_key'] or ''):<14}{fb}")
    print(f"\n→ fallback count: {fb_count}/{len(items)}")


report("症狀（24 項）", SYMPTOMS, "symptom")
report("疾病（23 項）", DISEASES, "disease")
