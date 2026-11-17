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


import time  # noqa: E402

import pytest  # noqa: E402


class _Env:
    """Handle returned by the ``env`` fixture."""

    def __init__(self, tmp_path):
        from util import auth, db_store
        self.tmp_path = tmp_path
        self.auth = auth
        self.db = db_store
        self.root = auth.DEFAULT_ADMIN_USERNAME
        self.default_group = db_store.default_group_id()

    def act_as(self, username):
        """Make ``username`` the logged-in user of the fake session."""
        if username is None:
            self.auth.ss.auth_user = None
            return None
        user = self.db.get_user_by_username(username)
        self.auth.ss.auth_user = self.auth._session_user_dict(user)
        self.auth.ss.auth_checked_at = time.time()
        return user

    def user(self, username):
        return self.db.get_user_by_username(username)

    def make_group(self, name, admin=None, members=()):
        """Create a group (as root) with an optional admin and ordinary members."""
        self.act_as(self.root)
        assert self.auth.create_group(name)[0]
        gid = self.db.get_group_by_name(name)["id"]
        if admin:
            assert self.auth.create_user(admin, "password123", role="group_admin", group_id=gid)[0]
        for member in members:
            assert self.auth.create_user(member, "password123", group_id=gid)[0]
        return gid


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh system DB + group DB directory under tmp_path, with the root
    admin bootstrapped and nobody logged in."""
    from util import auth, db_store
    monkeypatch.setattr(db_store, "MAIN_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(db_store, "GROUP_DB_DIR", None)
    monkeypatch.setattr(db_store, "LOG_DB_DIR", str(tmp_path / "log_db"))
    monkeypatch.setattr(db_store, "LOG_DB_PREFIX", "session_logs")
    monkeypatch.setattr(auth.st, "query_params", {}, raising=False)
    auth._failed_logins.clear()
    auth.ss.auth_user = None
    auth.ss.auth_checked_at = 0
    auth.init_auth()
    return _Env(tmp_path)
