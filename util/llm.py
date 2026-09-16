"""Central Gemini client wrapper (google-genai SDK).

Replaces the **deprecated** ``google-generativeai`` package (whose support has
ended and which exposed no ``thinking_config``). Centralizing here gives the app
a single place to:

- hold one shared :class:`google.genai.Client`,
- build a :class:`GenerateContentConfig` including **thinking-budget control**,
- preserve the legacy ``model.start_chat().send_message()`` call shape that the
  grading agents use (via :class:`ModelHandle`),
- expose thin ``start_chat()`` / ``generate()`` helpers.

Why thinking-budget control matters
-----------------------------------
``gemini-3.6-flash`` *thinks* by default. Those hidden thinking tokens (a) add
latency to every short interactive turn and (b) share the output budget, which
previously forced very large ``max_output_tokens`` to avoid the MAX_TOKENS
empty-candidate crash ("回覆很慢，也沒有回答完" in user feedback). With the new
SDK we disable thinking (``budget=0``) on the latency-sensitive conversational /
examiner paths and allow only a small budget on the one-shot grading paths where
reasoning quality matters and the latency is hidden behind a single spinner.
"""
import os
from functools import lru_cache

from google import genai
from google.genai import types as gtypes

# Re-export so agent modules can do ``from util.llm import gtypes`` for schemas.
Type = gtypes.Type
Schema = gtypes.Schema

# --- thinking budgets (output tokens reserved for hidden reasoning) ---
# 0 disables thinking entirely → fastest. Used for all interactive turns.
THINK_OFF = 0
# Small budget for one-shot generation where some reasoning helps quality.
THINK_LIGHT = 512
# Larger budget for the graders (reasoning-heavy, latency hidden, one-shot).
THINK_GRADER = 2048

_client = None

DEFAULT_GEMINI_MODEL = os.getenv("PATIENZ_GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT_GEMINI_LITE_MODEL = os.getenv("PATIENZ_GEMINI_LITE_MODEL", "gemini-3.5-flash-lite")

GEMINI_MODEL_FALLBACKS = (
    DEFAULT_GEMINI_MODEL,
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


def _is_model_unavailable_error(exc):
    """Return True when the Google API says a model is unavailable or invalid."""
    text = str(exc).lower()
    return (
        "not_found" in text
        or "invalid_argument" in text
        or "unsupported" in text
        or "no longer available" in text
        or "model" in text and "not found" in text
        or "model" in text and "unavailable" in text
    )


class RetryChat:
    """Proxy chat that retries with a supported fallback model on API validation errors."""

    def __init__(self, model_name, config, history=None):
        self._model_name = model_name
        self._config = config
        self._history = list(history or [])
        self._chat = self._create_chat(model_name)

    def _create_chat(self, model_name):
        return get_client().chats.create(model=model_name, config=self._config, history=self._history)

    def _candidate_models(self):
        seen = set()
        for model_name in (self._model_name, *GEMINI_MODEL_FALLBACKS):
            if model_name not in seen:
                seen.add(model_name)
                yield model_name

    def send_message(self, *args, **kwargs):
        current_model = self._model_name
        tried = {current_model}
        for candidate in self._candidate_models():
            try:
                if candidate != current_model:
                    self._model_name = candidate
                    self._chat = self._create_chat(candidate)
                    current_model = candidate
                return self._chat.send_message(*args, **kwargs)
            except Exception as exc:
                if not _is_model_unavailable_error(exc):
                    raise
                tried.add(candidate)
                remaining = [m for m in self._candidate_models() if m not in tried]
                if not remaining:
                    raise
                self._model_name = remaining[0]
                self._chat = self._create_chat(self._model_name)
        raise RuntimeError("No Gemini model fallback succeeded")

    def __getattr__(self, name):
        return getattr(self._chat, name)


@lru_cache(maxsize=256)
def read_text_file(path):
    """Read instruction/config text once and reuse it across reruns."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def get_client():
    """Return the process-wide google-genai client (lazily created)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


def safety_block_only_high():
    """Loosen safety thresholds so ordinary clinical/symptom talk (pain, blood,
    self-harm ideation in a psychiatric case, …) is not spuriously blocked,
    which would otherwise return an empty candidate and stall the patient."""
    cats = [
        gtypes.HarmCategory.HARM_CATEGORY_HARASSMENT,
        gtypes.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        gtypes.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        gtypes.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    ]
    return [
        gtypes.SafetySetting(category=c, threshold=gtypes.HarmBlockThreshold.BLOCK_ONLY_HIGH)
        for c in cats
    ]


def build_config(*, system_instruction=None, temperature=None, top_p=None, top_k=None,
                 max_output_tokens=None, response_schema=None, response_mime_type=None,
                 safety_settings=None, thinking_budget=None):
    """Build a GenerateContentConfig. ``thinking_budget`` is in output tokens;
    pass ``THINK_OFF`` (0) to disable thinking. ``None`` leaves a field unset."""
    thinking = None
    if thinking_budget is not None:
        thinking = gtypes.ThinkingConfig(thinking_budget=thinking_budget)
    return gtypes.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        max_output_tokens=max_output_tokens,
        response_schema=response_schema,
        response_mime_type=response_mime_type,
        safety_settings=safety_settings,
        thinking_config=thinking,
    )


def start_chat(model_name, config, history=None):
    """Create a chat session bound to ``config``. ``history`` is a list of
    ``{"role": "user"|"model", "parts": [{"text": ...}]}`` dicts (or Content)."""
    if model_name is None:
        model_name = DEFAULT_GEMINI_MODEL
    return RetryChat(model_name, config, history=history)


def generate(model_name, contents, config=None):
    """One-shot (stateless) generation."""
    return get_client().models.generate_content(model=model_name, contents=contents, config=config)


class ModelHandle:
    """Preserve the legacy ``model.start_chat().send_message(...)`` shape.

    The new SDK binds config at chat-creation time, so a "model" is just a
    (model_name, config) pair that mints chats on demand. Grading agents return
    one of these so ``page/grade.py`` keeps working with minimal changes.
    """

    def __init__(self, model_name, config):
        self.model_name = model_name
        self.config = config

    def start_chat(self, history=None):
        return start_chat(self.model_name, self.config, history)
