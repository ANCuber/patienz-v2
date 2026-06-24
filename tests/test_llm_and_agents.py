"""util.llm wrapper + per-agent thinking-budget policy + Selenium-free patient."""
import pytest
import streamlit as st  # the fake from conftest

import util.llm as llm


@pytest.fixture(autouse=True)
def _clear_session():
    st.session_state.clear()
    yield
    st.session_state.clear()


# ---------- wrapper ----------
def test_build_config_thinking_budget():
    c0 = llm.build_config(thinking_budget=0)
    assert c0.thinking_config.thinking_budget == 0
    cN = llm.build_config(thinking_budget=None)
    assert cN.thinking_config is None
    cG = llm.build_config(thinking_budget=llm.THINK_GRADER)
    assert cG.thinking_config.thinking_budget == 2048


def test_safety_block_only_high_is_list_of_four():
    s = llm.safety_block_only_high()
    assert isinstance(s, list) and len(s) == 4
    assert all(x.threshold == llm.gtypes.HarmBlockThreshold.BLOCK_ONLY_HIGH for x in s)


def test_model_handle_starts_chat_with_its_config(monkeypatch):
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured["model"] = model_name
        captured["config"] = config
        captured["history"] = history
        return "CHAT"

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    cfg = llm.build_config(temperature=0.3, thinking_budget=2048)
    h = llm.ModelHandle("gemini-2.5-flash", cfg)
    assert h.start_chat() == "CHAT"
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["config"] is cfg


# ---------- agent thinking budgets ----------
def test_grader_agents_thinking_budgets():
    from model.grader_v2 import create_grader_v2_model
    from model.mark_scheme_setter import create_mark_scheme_setter_model
    from model.acgme_grader import create_acgme_grader_model
    from model.lab_advisor import create_lab_advisor_model

    assert create_grader_v2_model("[]").config.thinking_config.thinking_budget == llm.THINK_GRADER
    assert create_mark_scheme_setter_model().config.thinking_config.thinking_budget == llm.THINK_LIGHT
    ms = {"subcompetencies": [{"id": "PC1", "domain": "PC",
                               "levels": {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}}]}
    assert create_acgme_grader_model(ms, None).config.thinking_config.thinking_budget == llm.THINK_GRADER
    assert create_lab_advisor_model("x").config.thinking_config.thinking_budget == llm.THINK_OFF


def test_patient_is_thinking_off_and_has_no_scrape(monkeypatch):
    import model.patient as patient
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(model=model_name, config=config, history=history)
        return "PATIENT_CHAT"

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    st.session_state.data = {"基本資訊": {"年齡": 40}}
    patient.create_patient_model("{case json}", prior_messages=[
        {"role": "doctor", "content": "你好"},
        {"role": "patient", "content": "醫生好"},
    ])
    assert captured["config"].thinking_config.thinking_budget == 0
    assert captured["config"].response_mime_type == "text/plain"
    assert len(captured["config"].safety_settings) == 4
    # history converted doctor->user, patient->model, text-only (no File parts)
    assert captured["history"][0]["role"] == "user"
    assert captured["history"][1]["role"] == "model"
    assert captured["history"][0]["parts"][0]["text"] == "你好"
    assert st.session_state.patient == "PATIENT_CHAT"
    # patient module must NOT import the Selenium scraper anymore
    assert not hasattr(patient, "getPDF")


def test_pediatric_note_added_for_minor(monkeypatch):
    import model.patient as patient
    st.session_state.data = {"基本資訊": {"年齡": 11}}
    note = patient._pediatric_note()
    assert "11 歲" in note and "家屬" in note
    st.session_state.data = {"基本資訊": {"年齡": 40}}
    assert patient._pediatric_note() == ""


def test_value_examiner_thinking_off_and_schema(monkeypatch):
    import model.examiner as examiner
    captured = {}

    def fake_start_chat(model_name, config, history=None):
        captured.update(model=model_name, config=config)
        return "VAL_EXAMINER"

    monkeypatch.setattr(llm, "start_chat", fake_start_chat)
    st.session_state.data = {"基本資訊": {"性別": "男"}}
    examiner.create_value_examiner_model("{case}")
    assert captured["config"].thinking_config.thinking_budget == 0
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is not None
    assert st.session_state.value_examiner == "VAL_EXAMINER"
