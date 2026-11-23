"""The ten image-bank demonstration cases retrieve the film they were written for.

Each case in data/template_problem_set/影像示範 *.json carries
``_template_meta.image_targets``: the exact report wording the examiner is told
to produce (also placed in ``Problem.檢查預期所見``) and the manifest id it must
resolve to. These tests run that wording through the real retrieval with the
case's own age/sex — the same call ``page/examination.py`` makes — so a change
to the bank, the vocabulary or the gates that would break a case shows up here
rather than in front of a student.

They need the real ``image_bank/manifest.json`` and its files; without a built
bank they skip rather than fail, since the bank is git-ignored.
"""
import glob
import json
import os

import pytest

from util import image_bank as ib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "data", "template_problem_set")
BANK_DIR = os.path.join(ROOT, "image_bank")

ITEM_TERMS = {"ECG": ["Electrocardiography", "心電圖"],
              "CXR": ["Chest X-ray", "胸部X光"]}


def _templates():
    paths = sorted(glob.glob(os.path.join(TEMPLATE_DIR, "影像示範 *.json")))
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if "_template_meta" in data:
            out.append((os.path.basename(p), data))
    return out


TEMPLATES = _templates()


def _bank_ready():
    man = ib.load_manifest(BANK_DIR)
    return bool(man["entries"]) and any(
        ib.image_path(e, BANK_DIR) for e in man["entries"][:5])


pytestmark = pytest.mark.skipif(not _bank_ready(),
                                reason="image_bank/ not built on this machine")


def test_ten_templates_five_per_modality():
    assert len(TEMPLATES) == 10
    primary = [t["_template_meta"]["image_targets"][0]["modality"] for _, t in TEMPLATES]
    assert primary.count("ECG") == 5 and primary.count("CXR") == 5


@pytest.mark.parametrize("fname,data", TEMPLATES, ids=[f for f, _ in TEMPLATES])
def test_case_shape_is_what_the_app_reads(fname, data):
    basic = data["基本資訊"]
    for k in ("姓名", "年齡", "性別", "生日", "身高", "體重", "職業"):
        assert k in basic, "%s lacks 基本資訊.%s" % (fname, k)
    for k in ("主訴", "既往疾病", "目前病史", "過敏史", "藥物史"):
        assert k in data["MH"]
    prob = data["Problem"]
    for k in ("疾病", "排除可能疾病之診斷", "確認正確疾病之診斷", "處置方式",
              "englishDiseaseName", "檢查預期所見"):
        assert k in prob, "%s lacks Problem.%s" % (fname, k)
    # the wording the examiner is told to reproduce IS the wording the test uses
    for t in data["_template_meta"]["image_targets"]:
        assert prob["檢查預期所見"][t["exam"]] == t["expected_report"]


@pytest.mark.parametrize("fname,data", TEMPLATES, ids=[f for f, _ in TEMPLATES])
def test_expected_report_retrieves_the_target_film(fname, data):
    basic = data["基本資訊"]
    man = ib.load_manifest(BANK_DIR)
    for t in data["_template_meta"]["image_targets"]:
        hit = ib.find_image(t["modality"], t["has_abnormal"],
                            report_text=t["expected_report"],
                            item_terms=ITEM_TERMS[t["modality"]],
                            patient_age=basic["年齡"], patient_sex=basic["性別"],
                            directory=BANK_DIR, manifest=man)
        if t["target_id"] is None:
            # a secondary exam with no pinned film: it only has to be safe —
            # either nothing, or a film of the right modality/normality.
            if hit is not None:
                assert hit["modality"] == t["modality"]
                assert hit["normality"] == ("abnormal" if t["has_abnormal"] else "normal")
            continue
        assert hit is not None, "%s: %s report retrieved nothing" % (fname, t["exam"])
        assert hit["id"] == t["target_id"], (
            "%s: %s report retrieved %s (%s), expected %s"
            % (fname, t["exam"], hit["id"], hit["findings"], t["target_id"]))


@pytest.mark.parametrize("fname,data", TEMPLATES, ids=[f for f, _ in TEMPLATES])
def test_target_film_demographics_match_the_patient(fname, data):
    # the demographic tiebreak is what makes the pick deterministic, so the
    # case must be written for the film's own age and sex
    man = ib.load_manifest(BANK_DIR)
    by_id = {e["id"]: e for e in man["entries"]}
    basic = data["基本資訊"]
    # only the primary target (index 0) must share the patient's demographics;
    # a pinned secondary film is chosen deterministically but may be a close
    # rather than exact demographic match
    for t in data["_template_meta"]["image_targets"][:1]:
        if not t["target_id"]:
            continue
        film = by_id[t["target_id"]]
        assert film["age"] == basic["年齡"], fname
        assert ib._norm_sex(film["sex"]) == ib._norm_sex(basic["性別"]), fname
        assert film["verified"], "%s: target film is not published" % fname


def test_loader_strips_meta_but_keeps_expected_findings():
    # mirrors page/config.py: the prompt-facing JSON must not carry the meta
    # block but must keep Problem.檢查預期所見 for the examiner
    fname, data = TEMPLATES[0]
    data = json.loads(json.dumps(data))
    assert data.pop("_template_meta", None) is not None
    prompt_json = json.dumps(data, ensure_ascii=False)
    assert "_template_meta" not in prompt_json
    assert "檢查預期所見" in prompt_json
