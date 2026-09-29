import streamlit as st
import util.constants as const
import util.tools as util

ss = st.session_state

def update(chat_area, msgs, height, show_all=True):
    """Render the conversation inside a fixed-height scrollable container.

    By default (`show_all=True`) the full transcript is rendered so the student
    can scroll back through the whole consultation; the container's own scroll
    keeps it visually contained and a long transcript re-renders cheaply each
    rerun. `show_all=False` falls back to just the last two turns.
    """
    chat_area.empty()
    with chat_area.container(height=height):
        rendered = msgs if show_all else msgs[-2:]
        for msg in rendered:
            try:
                with st.chat_message(msg["role"], avatar=const.avatar_map[msg["role"]]):
                    st.markdown(msg["content"])
            except Exception:
                pass

def append(msgs, role, content):
    msgs.append({"role": role, "content": content})


def _extract_reply(response):
    """Safely pull the text out of a Gemini response.

    A capped/blocked candidate must never reach ``response.text`` (that raises
    and crashes the page), so inspect finish_reason / prompt_feedback first.
    Returns ``(text, finish_reason, blocked)``; ``text`` is "" when unusable.
    """
    finish_reason = None
    candidate = None
    try:
        if response.candidates:
            candidate = response.candidates[0]
            finish_reason = candidate.finish_reason
    except Exception:
        candidate = None

    blocked = False
    try:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            blocked = True
    except Exception:
        blocked = False

    text = ""
    if candidate is not None and not blocked:
        try:
            text = response.text
        except Exception as e:
            util.record(ss.log, f"[PATIENT] response.text unavailable (finish_reason={finish_reason}): {e}")
            text = ""
    return text, finish_reason, blocked


def send_to_patient(prompt: str, chat_area, height: int, suffix: str = ""):
    """Record the doctor's message, get a hardened patient reply, and append it
    to ``ss.diagnostic_messages``. Returns True if a rerun should follow.

    ``suffix`` is appended to the prompt sent to the model (not to the log or
    the transcript), e.g. a reminder to answer as the patient.
    """
    prompt = prompt.rstrip("\n")
    if prompt == "":
        return False

    util.record(ss.log, f"Doctor: {prompt}")
    append(ss.diagnostic_messages, "doctor", prompt)
    update(chat_area, msgs=ss.diagnostic_messages, height=height, show_all=ss.show_all)

    try:
        response = ss.patient.send_message(f"醫學生：{prompt}{suffix}")
    except Exception as e:
        util.record(ss.log, f"[PATIENT] send_message error: {e}")
        st.warning("病人沒聽清楚，請再說一次")
        return False

    reply_text, finish_reason, blocked = _extract_reply(response)
    formatted_response = reply_text.replace("(", "（").replace(")", "）").strip()

    if blocked or formatted_response == "":
        util.record(ss.log, f"[PATIENT] empty/blocked response (finish_reason={finish_reason}, blocked={blocked})")
        st.warning("病人沒聽清楚，請再說一次")
        return False

    util.record(ss.log, f"Patient: {reply_text}")
    append(ss.diagnostic_messages, "patient", formatted_response)
    return True


def new_voice_transcript(key: str):
    """Render a voice-input widget and return the transcript of a *new*
    recording, or None.

    st.audio_input keeps its recording across reruns, so the audio is hashed
    and remembered in session state to avoid re-sending the same clip.
    """
    audio = st.audio_input("語音輸入", key=key)
    if not audio:
        return None
    audio_id = hash(audio.getvalue())
    state_key = f"{key}__last_id"
    if ss.get(state_key) == audio_id or not util.check_progress():
        return None
    ss[state_key] = audio_id
    from util.process import process_audio  # speech_recognition is only needed here
    return process_audio(audio)
