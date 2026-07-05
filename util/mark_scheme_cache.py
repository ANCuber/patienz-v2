"""Reuse OSCE mark schemes across similar cases (proposal §9-A).

`mark_scheme_setter` previously regenerated a rubric every grading run. Because a
rubric depends only on the case signature (disease + learner role + teaching
focus + difficulty), we can cache it keyed by that signature and reuse it for the
next similar case. This both **speeds up grading** (skips one LLM call) and
improves **scheme consistency** (same disease → same rubric), addressing the
meeting note "存一個 grading scheme 的資料庫，相似就從裡面拿".

Pure file-backed cache (no Streamlit); safe to call from a worker thread.
"""
import os
import json
import hashlib

CACHE_DIR = os.path.join("data", "mark_scheme_cache")

# Bump whenever the mark_scheme_setter instruction or the grader_v2 schema
# contract changes, so previously cached schemes are invalidated automatically.
SCHEME_CACHE_VERSION = 1


def signature(data, learner_role=None, user_config=None):
    """Return (digest, raw) describing the case for cache keying.

    Coarse enough to reuse across different patients of the same disease, but
    sensitive to learner role / teaching focus / difficulty so the rubric stays
    appropriate.

    DELIBERATELY excludes patient-specific case content: the OSCE rubric is
    disease-level (e.g. "是否詢問晨僵" for RA), not patient-specific, and §9-A's
    goal is exactly to reuse one rubric across similar patients for consistency
    and speed. Folding the full case JSON into the key would make every patient
    unique and defeat reuse. Re-grading the same session (after a backtrack) is
    also correct to reuse: the rubric depends on the case, not on the student's
    (edited) performance. Use SCHEME_CACHE_VERSION to invalidate on prompt/contract
    changes.
    """
    problem = data.get("Problem", {}) if isinstance(data, dict) else {}
    disease = problem.get("englishDiseaseName") or problem.get("疾病") or "unknown"
    disease = str(disease).strip().lower()

    role = ""
    if isinstance(learner_role, dict):
        role = str(learner_role.get("id", ""))

    uc = user_config or {}
    focus = uc.get("教學重點") or []
    if isinstance(focus, (list, tuple)):
        focus_key = ",".join(sorted(str(f) for f in focus))
    else:
        focus_key = str(focus)
    difficulty = str(uc.get("難度") or "")

    raw = f"{disease}|{role}|{focus_key}|{difficulty}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return digest, raw


def _path(sig, cache_dir=CACHE_DIR):
    return os.path.join(cache_dir, f"{sig}.json")


def load(sig, cache_dir=CACHE_DIR):
    """Return cached mark-scheme text for ``sig``, or None.

    Returns None (cache miss) if the file is missing, corrupt, or was written by
    a different SCHEME_CACHE_VERSION — so a prompt/contract change transparently
    forces regeneration instead of reusing an incompatible scheme.
    """
    p = _path(sig, cache_dir)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("version") != SCHEME_CACHE_VERSION:
            return None
        return payload.get("mark_scheme")
    except Exception as e:
        print(f"[mark_scheme_cache] ignoring unreadable cache {p}: {e}")
        return None


def store(sig, mark_scheme_text, meta=None, cache_dir=CACHE_DIR):
    """Persist a mark scheme atomically. Returns True on success (non-fatal)."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        final = _path(sig, cache_dir)
        tmp = f"{final}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"version": SCHEME_CACHE_VERSION, "mark_scheme": mark_scheme_text, "meta": meta or {}},
                f, ensure_ascii=False, indent=2,
            )
        os.replace(tmp, final)  # atomic on POSIX and Windows
        return True
    except Exception:
        return False


def get_or_create(sig, gen_fn, cache_dir=CACHE_DIR):
    """Return (mark_scheme_text, from_cache).

    ``gen_fn`` is called only on a cache miss; whatever it returns is stored.
    ``gen_fn`` should validate its own output (e.g. json.loads) and raise on bad
    output so a malformed scheme is never cached.
    """
    cached = load(sig, cache_dir)
    if cached:
        return cached, True
    text = gen_fn()
    store(sig, text, cache_dir=cache_dir)
    return text, False
