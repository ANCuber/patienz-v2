"""闖關模式 stage model (proposal §7-B)."""
from util.stages import stage_status, current_stage, all_cleared, QUEST_STAGES


def _states(snapshot):
    return [s["state"] for s in stage_status(snapshot)]


def test_fresh_start_only_first_stage_current():
    snap = {"history_turns": 0, "has_tentative": False, "exams_ordered": 0, "has_diagnosis": False}
    assert _states(snap) == ["current", "locked", "locked", "locked"]
    assert current_stage(snap)["id"] == "history"
    assert all_cleared(snap) is False


def test_history_done_advances_to_tentative():
    snap = {"history_turns": 4, "has_tentative": False, "exams_ordered": 0, "has_diagnosis": False}
    assert _states(snap) == ["done", "current", "locked", "locked"]
    assert current_stage(snap)["id"] == "tentative"


def test_all_cleared():
    snap = {"history_turns": 6, "has_tentative": True, "exams_ordered": 2, "has_diagnosis": True}
    assert all_cleared(snap) is True
    assert current_stage(snap) is None
    assert _states(snap) == ["done", "done", "done", "done"]


def test_noncontiguous_progress_marks_first_undone_as_current():
    # history not done, but tentative committed: stage1 is current, stage2 done.
    snap = {"history_turns": 1, "has_tentative": True, "exams_ordered": 0, "has_diagnosis": False}
    statuses = stage_status(snap)
    assert statuses[0]["state"] == "current"
    assert statuses[0]["done"] is False
    assert statuses[1]["state"] == "done"
    assert statuses[2]["state"] == "locked"


def test_history_threshold_boundary():
    assert stage_status({"history_turns": 2})[0]["done"] is False
    assert stage_status({"history_turns": 3})[0]["done"] is True


def test_stage_count_matches_definition():
    assert len(stage_status({})) == len(QUEST_STAGES) == 4
