import streamlit as st 
import util.constants as const

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
