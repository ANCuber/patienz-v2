"""Mark-scheme reuse cache (proposal §9-A)."""
import util.mark_scheme_cache as msc


def _data(disease_en="Rheumatoid arthritis", disease_zh="類風濕性關節炎"):
    return {"Problem": {"englishDiseaseName": disease_en, "疾病": disease_zh}}


def test_signature_is_deterministic():
    s1, _ = msc.signature(_data(), {"id": "pgy1"}, {"教學重點": ["病史詢問", "共病管理"], "難度": "中等"})
    s2, _ = msc.signature(_data(), {"id": "pgy1"}, {"教學重點": ["共病管理", "病史詢問"], "難度": "中等"})
    assert s1 == s2  # focus order-independent


def test_signature_sensitive_to_disease_role_focus_difficulty():
    base = msc.signature(_data(), {"id": "pgy1"}, {"教學重點": ["病史詢問"], "難度": "中等"})[0]
    assert msc.signature(_data("Gout"), {"id": "pgy1"}, {"教學重點": ["病史詢問"], "難度": "中等"})[0] != base
    assert msc.signature(_data(), {"id": "clerk"}, {"教學重點": ["病史詢問"], "難度": "中等"})[0] != base
    assert msc.signature(_data(), {"id": "pgy1"}, {"教學重點": ["共病管理"], "難度": "中等"})[0] != base
    assert msc.signature(_data(), {"id": "pgy1"}, {"教學重點": ["病史詢問"], "難度": "困難"})[0] != base


def test_signature_handles_missing_fields():
    s, raw = msc.signature({}, None, None)
    assert isinstance(s, str) and len(s) == 16
    assert "unknown" in raw


def test_get_or_create_misses_then_hits(tmp_path):
    cache_dir = str(tmp_path / "ms_cache")
    calls = {"n": 0}

    def gen():
        calls["n"] += 1
        return '[{"item_id": 1}]'

    sig, _ = msc.signature(_data(), {"id": "pgy1"}, {})
    text1, from_cache1 = msc.get_or_create(sig, gen, cache_dir=cache_dir)
    text2, from_cache2 = msc.get_or_create(sig, gen, cache_dir=cache_dir)

    assert text1 == text2 == '[{"item_id": 1}]'
    assert from_cache1 is False and from_cache2 is True
    assert calls["n"] == 1  # gen_fn invoked only on the miss


def test_version_mismatch_invalidates(tmp_path, monkeypatch):
    cache_dir = str(tmp_path / "c")
    sig = "deadbeefdeadbeef"
    msc.store(sig, '[{"item_id": 1}]', cache_dir=cache_dir)
    assert msc.load(sig, cache_dir=cache_dir) == '[{"item_id": 1}]'
    # a future version must treat the old-version file as a miss
    monkeypatch.setattr(msc, "SCHEME_CACHE_VERSION", msc.SCHEME_CACHE_VERSION + 1)
    assert msc.load(sig, cache_dir=cache_dir) is None
