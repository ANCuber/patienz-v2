"""Test bootstrap: a minimal fake ``streamlit`` so model/util modules import and
run their session-state / spinner code without a real Streamlit context, plus a
dummy API key so the google-genai client can be constructed (no network calls
are made in these tests).
"""
import os
import sys
import types
import contextlib

os.environ.setdefault("GEMINI_API_KEY", "DUMMY")
# Explicit context caching (§1-C) does a real network call at cache creation;
# force it off in tests (hard assignment — an enabling value inherited from the
# developer's shell must not turn the no-network suite into real HTTPS calls).
# Cache-specific tests re-enable per-test with a monkeypatched client.
os.environ["PATIENZ_DISABLE_CONTEXT_CACHE"] = "1"

# Ensure the project root is importable as the package root (util.*, model.*).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class _SessionState(dict):
    """dict that also supports attribute access, like st.session_state."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError as e:
            raise AttributeError(key) from e


def _make_fake_streamlit():
    st = types.ModuleType("streamlit")
    st.session_state = _SessionState()

    @contextlib.contextmanager
    def _spinner(*_a, **_k):
        yield

    st.spinner = _spinner

    def _noop(*_a, **_k):
        return None

    for name in ("write", "warning", "error", "info", "success", "stop",
                 "markdown", "caption", "rerun"):
        setattr(st, name, _noop)
    return st


# Install before any model/util module imports streamlit.
if "streamlit" not in sys.modules or not hasattr(sys.modules["streamlit"], "session_state"):
    sys.modules["streamlit"] = _make_fake_streamlit()
