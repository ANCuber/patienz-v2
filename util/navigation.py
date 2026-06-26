"""Phase navigation gating (proposal §7 流程彈性).

Two modes:

- **Standard (default)**: forward-only to the current frontier, free backtrack to
  completed phases. Preserves the deliberate OSCE ordering — committing a
  pre-DDx *before* seeing investigations is part of what the grader assesses.
- **Free-exploration (opt-in)**: any clinical/grading phase is reachable in any
  order, so a student can order screening tests early, return to history after
  results, etc. — matching the feedback ("先做檢查幫助鑑別", "檢查完後繼續問診").

Pure functions (no Streamlit) so they are unit-testable.
"""

CONFIG_PHASE = 0  # 病患設定 — always required first; not part of free interleave


def can_visit(target, current_progress, free_navigation=False):
    """May the student act on / navigate to phase ``target``?

    In free mode any phase from 1 onward is allowed (config is handled
    separately). In standard mode you may only be at or behind the frontier.
    """
    if free_navigation:
        return target >= 1
    return target <= current_progress


def is_unlocked(i, current_progress, free_navigation=False):
    """Sidebar: should phase ``i`` render as a navigable button (vs 🔒 locked)?"""
    if free_navigation:
        return True
    return i <= current_progress
