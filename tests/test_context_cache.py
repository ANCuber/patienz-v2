"""Explicit context caching (§1-C): creation, fallback, and runtime self-heal."""
import types

import pytest
import streamlit as st  # the fake from conftest
from google.genai import errors as gerrors

import util.llm as llm
import util.context_cache as ccache


def _dead_cache_error():
    return gerrors.APIError(403, {"error": {"message": "CachedContent not found or expired",
                                            "status": "PERMISSION_DENIED"}})


@pytest.fixture(autouse=True)
def _clear_session():
    st.session_state.clear()
    yield
    st.session_state.clear()


@pytest.fixture
def cache_enabled(monkeypatch):
    monkeypatch.setenv("PATIENZ_DISABLE_CONTEXT_CACHE", "0")


class _FakeCaches:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.deleted = []

    def create(self, *, model, config):
        self.calls.append((model, config))
        if self.fail:
            raise RuntimeError("Cached content is too small: below minimum token count")
        return types.SimpleNamespace(name="cachedContents/abc123")

    def delete(self, *, name):
        self.deleted.append(name)


def _install_fake_client(monkeypatch, fail=False):
    caches = _FakeCaches(fail=fail)
    client = types.SimpleNamespace(caches=caches)
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return caches


# ---------- create_cache ----------
def test_disabled_by_env_skips_client_entirely(monkeypatch):
    monkeypatch.setenv("PATIENZ_DISABLE_CONTEXT_CACHE", "1")
    caches = _install_fake_client(monkeypatch)
    assert ccache.create_cache("gemini-2.5-flash", "x" * 5000) is None
    assert caches.calls == []


def test_create_cache_success_passes_instruction_and_ttl(monkeypatch, cache_enabled):
    caches = _install_fake_client(monkeypatch)
    name = ccache.create_cache("gemini-2.5-flash", "SYS", display_name="patienz-patient-1")
    assert name == "cachedContents/abc123"
    model, config = caches.calls[0]
    assert model == "gemini-2.5-flash"
    assert config.system_instruction == "SYS"
    assert config.display_name == "patienz-patient-1"
    assert config.ttl == f"{ccache.DEFAULT_TTL_SECONDS}s"
    # creation must fail FAST on restricted networks instead of stalling the spinner
    assert config.http_options.timeout == ccache.CACHE_CREATE_TIMEOUT_MS


def test_create_cache_any_failure_returns_none(monkeypatch, cache_enabled):
    _install_fake_client(monkeypatch, fail=True)
    assert ccache.create_cache("gemini-2.5-flash", "SYS") is None


def test_ttl_env_override(monkeypatch):
    monkeypatch.setenv("PATIENZ_CACHE_TTL_SECONDS", "900")
    assert ccache.ttl_seconds() == 900
    monkeypatch.setenv("PATIENZ_CACHE_TTL_SECONDS", "not-a-number")
    assert ccache.ttl_seconds() == ccache.DEFAULT_TTL_SECONDS


# ---------- build_config guard ----------
def test_build_config_rejects_cache_plus_system_instruction():
    with pytest.raises(ValueError):
        llm.build_config(system_instruction="SYS", cached_content="cachedContents/x")
    cfg = llm.build_config(cached_content="cachedContents/x")
    assert cfg.cached_content == "cachedContents/x"
    assert cfg.system_instruction is None


# ---------- start_cached_chat ----------
def test_start_cached_chat_uses_cache_when_available(monkeypatch, cache_enabled):
    _install_fake_client(monkeypatch)
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(model=model_name, config=config, history=history)
        return types.SimpleNamespace(send_message=lambda m: f"OK:{m}")

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    chat = ccache.start_cached_chat(
        "gemini-2.5-flash", system_instruction="SYS",
        history=[{"role": "user", "parts": [{"text": "hi"}]}],
        temperature=0.4, thinking_budget=0,
    )
    assert captured["config"].cached_content == "cachedContents/abc123"
    assert captured["config"].system_instruction is None
    assert captured["config"].temperature == 0.4
    assert captured["history"][0]["parts"][0]["text"] == "hi"
    # proxy in place, delegates sends
    assert isinstance(chat, ccache.ChatWithCacheFallback)
    assert chat.send_message("哈囉") == "OK:哈囉"


def test_start_cached_chat_falls_back_to_plain_chat(monkeypatch, cache_enabled):
    _install_fake_client(monkeypatch, fail=True)
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(config=config)
        return "PLAIN_CHAT"

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    chat = ccache.start_cached_chat("gemini-2.5-flash", system_instruction="SYS", thinking_budget=0)
    assert chat == "PLAIN_CHAT"  # no proxy on the uncached path
    assert captured["config"].system_instruction == "SYS"
    assert captured["config"].cached_content is None


# ---------- runtime self-heal ----------
class _ExpiredChat:
    """send_message always raises a dead-cache error, as after TTL expiry."""

    def __init__(self, exc=None):
        self.history = [{"role": "user", "parts": [{"text": "早安"}]}]
        self.exc = exc or _dead_cache_error()

    def send_message(self, message):
        raise self.exc

    def get_history(self, curated=False):
        assert curated is True
        return self.history


def test_proxy_rebuilds_uncached_and_retries_once(monkeypatch):
    caches = _install_fake_client(monkeypatch)
    expired = _ExpiredChat()
    uncached_config = llm.build_config(system_instruction="SYS", thinking_budget=0)
    rebuilt = {}

    def fake_start_chat(model_name, config, history=None):
        rebuilt.update(model=model_name, config=config, history=history)
        return types.SimpleNamespace(send_message=lambda m: f"HEALED:{m}")

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    proxy = ccache.ChatWithCacheFallback(expired, "gemini-2.5-flash", uncached_config,
                                         cache_name="cachedContents/abc123")
    assert proxy.send_message("還好嗎？") == "HEALED:還好嗎？"
    # rebuilt without cache, with the original system instruction + history
    assert rebuilt["config"].system_instruction == "SYS"
    assert rebuilt["config"].cached_content is None
    assert rebuilt["history"] == expired.history
    assert proxy._fell_back is True
    # abandoned cache deleted server-side, name cleared
    assert caches.deleted == ["cachedContents/abc123"]
    assert proxy.cache_name is None


def test_proxy_does_not_retry_twice(monkeypatch):
    """After a fallback, a second failure propagates (page code handles it)."""
    def fake_start_chat(model_name, config, history=None):
        return _ExpiredChat()  # the rebuilt chat also fails

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    proxy = ccache.ChatWithCacheFallback(
        _ExpiredChat(), "gemini-2.5-flash",
        llm.build_config(system_instruction="SYS"),
    )
    with pytest.raises(gerrors.APIError):
        proxy.send_message("x")
    with pytest.raises(gerrors.APIError):
        proxy.send_message("x")  # stays failed, no infinite rebuild loop


def test_transient_errors_do_not_kill_cache(monkeypatch):
    """429/5xx/network blips re-raise untouched: the page shows its retry
    warning and the (healthy) cache keeps serving the next attempt."""
    def fail_if_called(*a, **k):
        raise AssertionError("must not rebuild the chat on a transient error")

    monkeypatch.setattr(llm, "start_chat", fail_if_called)
    for exc in (
        gerrors.APIError(429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}}),
        gerrors.APIError(503, {"error": {"message": "unavailable", "status": "UNAVAILABLE"}}),
        RuntimeError("connection reset"),  # non-API error, e.g. network layer
    ):
        proxy = ccache.ChatWithCacheFallback(_ExpiredChat(exc=exc), "gemini-2.5-flash",
                                             llm.build_config(system_instruction="SYS"),
                                             cache_name="cachedContents/keepme")
        with pytest.raises(type(exc)):
            proxy.send_message("x")
        assert proxy._fell_back is False
        assert proxy.cache_name == "cachedContents/keepme"


# ---------- delete_cache ----------
def test_delete_cache_none_is_noop(monkeypatch):
    def fail_if_called():
        raise AssertionError("no client call for empty cache name")

    monkeypatch.setattr(llm, "get_client", fail_if_called)
    ccache.delete_cache(None)
    ccache.delete_cache("")


def test_delete_cache_swallows_errors(monkeypatch):
    class _Boom:
        def delete(self, *, name):
            raise RuntimeError("404 already expired")

    monkeypatch.setattr(llm, "get_client", lambda: types.SimpleNamespace(caches=_Boom()))
    ccache.delete_cache("cachedContents/gone")  # must not raise


def test_proxy_delegates_other_attributes():
    inner = types.SimpleNamespace(send_message=lambda m: m, custom_attr="VALUE")
    proxy = ccache.ChatWithCacheFallback(inner, "m", None)
    assert proxy.custom_attr == "VALUE"


# ---------- agent integration ----------
def test_patient_uses_cached_chat_and_keeps_generation_config(monkeypatch, cache_enabled):
    import model.patient as patient
    _install_fake_client(monkeypatch)
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(model=model_name, config=config, history=history)
        return types.SimpleNamespace(send_message=lambda m: m)

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    st.session_state.data = {"基本資訊": {"年齡": 40}}
    patient.create_patient_model("{case json}", prior_messages=[{"role": "doctor", "content": "你好"}])
    cfg = captured["config"]
    assert cfg.cached_content == "cachedContents/abc123"
    assert cfg.system_instruction is None
    # generation-level settings still applied at request time
    assert cfg.thinking_config.thinking_budget == 0
    assert len(cfg.safety_settings) == 4
    assert captured["history"][0]["role"] == "user"
    assert isinstance(st.session_state.patient, ccache.ChatWithCacheFallback)
    # the fallback config restores the full system instruction
    assert "{case json}" in st.session_state.patient._uncached_config.system_instruction


def test_value_examiner_cached_keeps_response_schema(monkeypatch, cache_enabled):
    import model.examiner as examiner
    _install_fake_client(monkeypatch)
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(config=config)
        return types.SimpleNamespace(send_message=lambda m: m)

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    st.session_state.data = {"基本資訊": {"性別": "男"}}
    examiner.create_value_examiner_model("{case}")
    cfg = captured["config"]
    assert cfg.cached_content == "cachedContents/abc123"
    assert cfg.response_schema is not None
    assert cfg.response_mime_type == "application/json"
