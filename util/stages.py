"""闖關模式 stage model (proposal §7-B).

Frames the consultation as an explicit staged reasoning process
(臆斷 → 篩檢 → 再鑑別 → 確診) so students are tested on the thinking *process*,
not just a one-shot final answer — answering the feedback that the system felt
like "一次用完整的個案模擬練習標準回答" rather than "闖關型式…考驗 thinking process".

Pure logic (no Streamlit) → unit-testable; the UI only renders ``stage_status``.
"""

QUEST_STAGES = [
    {"id": "history",   "title": "第一關：病史採集",
     "goal": "透過問診蒐集足夠的病史線索（建議至少進行數輪有意義的問診）。"},
    {"id": "tentative", "title": "第二關：初步臆斷",
     "goal": "根據病史與理學檢查，提出初步鑑別診斷與支持理由（先有臆斷，再去驗證）。"},
    {"id": "screening", "title": "第三關：檢驗篩檢",
     "goal": "開立能驗證或排除你臆斷的檢查，並對結果做出判讀。"},
    {"id": "confirm",   "title": "第四關：再鑑別與確診",
     "goal": "依檢查結果修正鑑別，提出最終主診斷與處置計畫。"},
]

HISTORY_TURNS_REQUIRED = 3


def _done_flags(snapshot):
    """Per-stage completion from a plain state snapshot.

    snapshot keys: history_turns(int), has_tentative(bool),
    exams_ordered(int), has_diagnosis(bool).
    """
    return [
        int(snapshot.get("history_turns", 0)) >= HISTORY_TURNS_REQUIRED,
        bool(snapshot.get("has_tentative", False)),
        int(snapshot.get("exams_ordered", 0)) >= 1,
        bool(snapshot.get("has_diagnosis", False)),
    ]


def stage_status(snapshot):
    """Return each stage annotated with done(bool) and state in
    {'done','current','locked'}. The 'current' stage is the first not-yet-done
    one; later not-done stages are 'locked'."""
    flags = _done_flags(snapshot)
    first_undone = next((i for i, d in enumerate(flags) if not d), None)
    out = []
    for i, stg in enumerate(QUEST_STAGES):
        if flags[i]:
            state = "done"
        elif i == first_undone:
            state = "current"
        else:
            state = "locked"
        out.append({**stg, "done": flags[i], "state": state})
    return out


def current_stage(snapshot):
    """The stage the student should work on now, or None if all cleared."""
    for s in stage_status(snapshot):
        if s["state"] == "current":
            return s
    return None


def all_cleared(snapshot):
    return all(_done_flags(snapshot))
