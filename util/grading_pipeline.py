"""Run independent grading stages concurrently, with a safe sequential fallback.

``page/grade.py`` has two independent network-bound chains at grading time:
  * OSCE chain  : mark_scheme_setter → grader_v2
  * ACGME chain : acgme_grader
Running them in parallel overlaps the two longest waits (a clear win once the
hidden-thinking latency is also capped).

Contract for tasks
------------------
Each task is a **zero-argument callable that performs ONLY LLM/network work and
returns a plain value**. Tasks MUST NOT touch Streamlit (``st.*``) or
``session_state`` — those are not thread-safe and have no ScriptRunContext in a
worker thread. The caller reads results back and writes session_state on the
main thread.

``run_parallel`` never raises: a task that errors yields its ``Exception`` as the
result value, and if the thread pool itself cannot be used the tasks are run
sequentially so grading always completes.
"""
from concurrent.futures import ThreadPoolExecutor


def run_parallel(tasks: dict) -> dict:
    """tasks: ``{name: callable}`` → ``{name: result_or_Exception}``.

    Check ``isinstance(result, Exception)`` per task to detect failures.
    """
    results = {}
    if not tasks:
        return results
    try:
        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as ex:
            futures = {name: ex.submit(fn) for name, fn in tasks.items()}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result()
                except Exception as e:  # noqa: BLE001 - capture per-task failure
                    results[name] = e
        return results
    except Exception:  # noqa: BLE001 - thread pool unusable → sequential fallback
        results = {}
        for name, fn in tasks.items():
            try:
                results[name] = fn()
            except Exception as e:  # noqa: BLE001
                results[name] = e
        return results
