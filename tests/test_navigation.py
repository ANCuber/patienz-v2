"""Phase navigation gating (proposal §7)."""
from util.navigation import can_visit, is_unlocked


def test_standard_mode_is_forward_only_to_frontier():
    cp = 2  # frontier at 理學檢查
    assert can_visit(1, cp, False) is True   # backtrack ok
    assert can_visit(2, cp, False) is True   # current ok
    assert can_visit(3, cp, False) is False  # cannot jump ahead
    assert can_visit(4, cp, False) is False


def test_standard_mode_sidebar_locks_future_phases():
    cp = 2
    assert is_unlocked(1, cp, False) is True
    assert is_unlocked(2, cp, False) is True
    assert is_unlocked(3, cp, False) is False
    assert is_unlocked(4, cp, False) is False


def test_free_mode_allows_any_clinical_phase_in_any_order():
    cp = 1  # only 問診 reached
    for target in (1, 2, 3, 4, 5, 6):
        assert can_visit(target, cp, True) is True
        assert is_unlocked(target, cp, True) is True


def test_free_mode_still_excludes_below_phase_1():
    # config (0) is handled separately; can_visit guards target>=1
    assert can_visit(0, 1, True) is False
