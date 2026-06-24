"""Parallel grading orchestration + per-task failure isolation."""
import time
from util.grading_pipeline import run_parallel


def test_runs_all_tasks_and_returns_by_name():
    out = run_parallel({"a": lambda: 1, "b": lambda: 2})
    assert out == {"a": 1, "b": 2}


def test_failed_task_is_captured_not_raised():
    def boom():
        raise ValueError("nope")

    out = run_parallel({"ok": lambda: "fine", "bad": boom})
    assert out["ok"] == "fine"
    assert isinstance(out["bad"], ValueError)


def test_empty_tasks():
    assert run_parallel({}) == {}


def test_tasks_actually_overlap_in_time():
    # Two ~0.3s sleeps should finish in well under 0.6s if truly concurrent.
    def slow(v):
        def _():
            time.sleep(0.3)
            return v
        return _

    t0 = time.perf_counter()
    out = run_parallel({"x": slow("x"), "y": slow("y")})
    elapsed = time.perf_counter() - t0
    assert out == {"x": "x", "y": "y"}
    assert elapsed < 0.55, f"expected concurrency, took {elapsed:.2f}s"
