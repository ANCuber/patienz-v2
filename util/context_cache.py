"""Explicit Gemini context caching for the interactive agents (proposal §1-C).

The patient / examiner chats resend a large **fixed prefix** — agent
instruction + full case JSON (several thousand tokens) — with every single
turn. Gemini 2.5 supports *explicit context caching*: upload that prefix once
(``client.caches.create``) and reference it by name on each request, which

- removes prefix re-processing from every turn's latency, and
- bills the cached tokens at a fraction of the normal input price
  (2.5 models also do best-effort *implicit* caching; an explicit cache makes
  the hit guaranteed instead of opportunistic).

Design constraints honoured here
--------------------------------
- **Graceful degradation everywhere.** Cache creation can fail for legitimate
  reasons: prefix below the model's minimum token count (1,024 for 2.5 Flash),
  quota, network, unsupported model. Every failure falls back to the plain
  "system_instruction on every request" path — behaviour identical to before
  §1-C, just without the speedup.
- **Runtime self-healing.** A cache can expire mid-session (TTL). The chat
  proxy retries a failed send ONCE on a rebuilt, uncached chat (history
  carried over via ``Chat.get_history``) and stays uncached afterwards, so a
  student is never stranded behind a dead cache.
- **Opt-out.** ``PATIENZ_DISABLE_CONTEXT_CACHE=1`` disables caching entirely;
  ``PATIENZ_CACHE_TTL_SECONDS`` overrides the cache lifetime.

This module is Streamlit-free so it stays unit-testable.
"""
import os

import util.llm as llm
from google.genai import errors as gerrors
from google.genai import types as gtypes

# Long enough to cover a full OSCE session (問診→評分 is well under 2 h in
# practice); expiry mid-session is still survivable via the send-time fallback.
DEFAULT_TTL_SECONDS = 2 * 60 * 60

# Cache creation is a synchronous network call inside the "正在建立…模型"
# spinner. On a firewalled/captive network it must fail FAST into the uncached
# path rather than stall model creation for the httpx default timeout.
CACHE_CREATE_TIMEOUT_MS = 10_000


def enabled() -> bool:
    return os.environ.get("PATIENZ_DISABLE_CONTEXT_CACHE", "").lower() not in ("1", "true", "yes")


def ttl_seconds() -> int:
    try:
        return int(os.environ["PATIENZ_CACHE_TTL_SECONDS"])
    except (KeyError, ValueError):
        return DEFAULT_TTL_SECONDS


def create_cache(model_name: str, system_instruction: str, display_name: str = None):
    """Upload ``system_instruction`` as an explicit context cache.

    Returns the cache resource name (``cachedContents/…``), or ``None`` on ANY
    failure — the caller then uses the ordinary uncached path.
    """
    if not enabled():
        return None
    try:
        cache = llm.get_client().caches.create(
            model=model_name,
            config=gtypes.CreateCachedContentConfig(
                system_instruction=system_instruction,
                display_name=display_name,
                ttl=f"{ttl_seconds()}s",
                http_options=gtypes.HttpOptions(timeout=CACHE_CREATE_TIMEOUT_MS),
            ),
        )
        print(f"[CACHE] created {cache.name} ({display_name})")
        return cache.name
    except Exception as e:
        print(f"[CACHE] create failed ({display_name}): {e} — using uncached path")
        return None


def delete_cache(cache_name):
    """Best-effort server-side deletion of a cache that will no longer be used.

    Explicit caches bill storage per token-hour until TTL expiry; deleting on
    handle drop (session load, cache fallback) stops the billing early. Never
    raises — a 404 after natural expiry is expected and fine.
    """
    if not cache_name:
        return
    try:
        llm.get_client().caches.delete(name=cache_name)
        print(f"[CACHE] deleted {cache_name}")
    except Exception as e:
        print(f"[CACHE] delete failed ({cache_name}): {e}")


def _is_dead_cache_error(e) -> bool:
    """Only these plausibly mean "the cached_content reference is unusable":
    an expired/deleted cache surfaces as 400 INVALID_ARGUMENT, 403
    PERMISSION_DENIED or 404 NOT_FOUND. Transient 429/408/5xx and non-API
    errors must NOT abandon a healthy cache (and retrying them immediately
    with the full prefix inlined would fire a strictly larger request into an
    already-throttled quota)."""
    return isinstance(e, gerrors.APIError) and getattr(e, "code", None) in (400, 403, 404)


class ChatWithCacheFallback:
    """Proxy around a cache-backed chat.

    If a send fails with a dead-cache error (typically: the cache expired
    mid-session), rebuild the chat WITHOUT the cache — same generation config
    but with the system instruction inlined, history carried over — and retry
    the send once. After falling back the chat stays uncached (no
    flip-flopping) and the abandoned cache is deleted server-side. A failed
    send is not recorded in the SDK chat history, so the retry does not
    duplicate the student's message.

    Transient errors (429/5xx/network) are re-raised untouched: the existing
    page-level handlers already turn them into a "請再試一次" warning, and the
    healthy cache keeps serving the next attempt.
    """

    def __init__(self, chat, model_name, uncached_config, cache_name=None):
        self._chat = chat
        self._model_name = model_name
        self._uncached_config = uncached_config
        self.cache_name = cache_name
        self._fell_back = False

    def send_message(self, message):
        try:
            return self._chat.send_message(message)
        except Exception as e:
            if self._fell_back or not _is_dead_cache_error(e):
                raise
            print(f"[CACHE] send failed ({e}) — rebuilding chat without cache and retrying once")
            history = self._chat.get_history(curated=True)
            self._chat = llm.start_chat(self._model_name, self._uncached_config, history=history)
            self._fell_back = True
            delete_cache(self.cache_name)  # dead/abandoned — stop paying for storage
            self.cache_name = None
            return self._chat.send_message(message)

    def __getattr__(self, name):
        # Anything we don't override (get_history, …) → the underlying chat.
        return getattr(self._chat, name)


def start_cached_chat(model_name: str, *, system_instruction: str, display_name: str = None,
                      history=None, **config_kwargs):
    """Create a chat whose fixed system instruction is served from an explicit
    context cache when possible, falling back to a plain chat otherwise.

    ``config_kwargs`` are forwarded to :func:`util.llm.build_config`
    (temperature, thinking_budget, response_schema, safety_settings, …).
    """
    cache_name = create_cache(model_name, system_instruction, display_name)
    if cache_name is None:
        return llm.start_chat(
            model_name,
            llm.build_config(system_instruction=system_instruction, **config_kwargs),
            history=history,
        )
    cached_config = llm.build_config(cached_content=cache_name, **config_kwargs)
    uncached_config = llm.build_config(system_instruction=system_instruction, **config_kwargs)
    chat = llm.start_chat(model_name, cached_config, history=history)
    return ChatWithCacheFallback(chat, model_name, uncached_config, cache_name=cache_name)
