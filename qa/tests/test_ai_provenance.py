"""
Tests for AI-screening provenance, reviewer-AI agreement, and the
heterogeneity/sensitivity protocol fields (PRISMA items 8, 13e, 13f, 23c).
"""
import io
import json
import os
import uuid
import zipfile

import pytest


def _make_run(rsp_app):
    run_id = str(uuid.uuid4())
    os.makedirs(rsp_app.ADHOC_RESULTS_DIR, exist_ok=True)
    with open(os.path.join(rsp_app.ADHOC_RESULTS_DIR, f"{run_id}.json"), "w", encoding="utf-8") as fh:
        json.dump({"runId": run_id, "docs": []}, fh)
    return run_id


def _decisions(rsp_app, run_id):
    with open(os.path.join(rsp_app.ADHOC_RESULTS_DIR, f"{run_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)["screeningDecisions"]


def test_decision_stores_ai_suggestion_when_overridden(client):
    import app as rsp_app
    run_id = _make_run(rsp_app)
    resp = client.post(f"/api/results/run/adhoc/{run_id}/screening/decision", json={
        "paperId": "doi:10.1/x", "decision": "exclude", "reason": "off topic",
        "aiSuggested": "include", "aiConfidence": 140, "aiModel": "qwen2.5:7b", "aiVendor": "ollama",
    })
    assert resp.status_code == 200
    entry = _decisions(rsp_app, run_id)["doi:10.1/x"]
    assert entry["aiSuggested"] == "include"
    assert entry["aiConfidence"] == 100  # clamped
    assert entry["aiVendor"] == "ollama"
    assert entry["aiModel"] == "qwen2.5:7b"
    assert "aiAssisted" not in entry


def test_decision_accepted_from_ai(client):
    import app as rsp_app
    run_id = _make_run(rsp_app)
    client.post(f"/api/results/run/adhoc/{run_id}/screening/decision", json={
        "paperId": "doi:10.1/y", "decision": "include", "aiAssisted": True,
        "aiSuggested": "include", "aiConfidence": 80, "aiModel": "gpt-4o-mini", "aiVendor": "openai",
    })
    entry = _decisions(rsp_app, run_id)["doi:10.1/y"]
    assert entry["aiAssisted"] is True
    assert entry["aiSuggested"] == "include"


def test_decision_ignores_invalid_ai_suggestion(client):
    import app as rsp_app
    run_id = _make_run(rsp_app)
    client.post(f"/api/results/run/adhoc/{run_id}/screening/decision", json={
        "paperId": "doi:10.1/z", "decision": "include", "aiSuggested": "maybe", "aiModel": "m",
    })
    entry = _decisions(rsp_app, run_id)["doi:10.1/z"]
    assert "aiSuggested" not in entry and "aiModel" not in entry


def test_agreement_stats_perfect_and_partial():
    import app as rsp_app
    assert rsp_app._ai_agreement_stats([]) is None
    rows = [
        {"ai_suggested": "include", "decision": "include"},
        {"ai_suggested": "exclude", "decision": "exclude"},
    ]
    assert rsp_app._ai_agreement_stats(rows) == {"n": 2, "agree": 2, "pct": 100, "kappa": 1.0}

    rows = [
        {"ai_suggested": "include", "decision": "include"},
        {"ai_suggested": "include", "decision": "exclude"},
        {"ai_suggested": "exclude", "decision": "exclude"},
        {"ai_suggested": "exclude", "decision": "exclude"},
        {"ai_suggested": "", "decision": "include"},  # no suggestion: ignored
    ]
    stats = rsp_app._ai_agreement_stats(rows)
    assert stats["n"] == 4 and stats["agree"] == 3 and stats["pct"] == 75
    # po=0.75, pe=0.25*0.5+0.75*0.5=0.5 -> kappa=0.5
    assert stats["kappa"] == pytest.approx(0.5)


def test_export_includes_new_columns_and_agreement(client):
    resp = client.post("/api/export-replication-package", data=json.dumps({
        "searchName": "AI2", "docs": [], "excludedDocs": [], "query": "test",
        "screeningDecisions": [
            {"title": "P1", "decision": "include", "ai_assisted": "yes", "ai_model": "m1",
             "ai_vendor": "ollama", "ai_suggested": "include", "ai_confidence": 90},
            {"title": "P2", "decision": "exclude", "ai_assisted": "no", "ai_model": "m1",
             "ai_vendor": "ollama", "ai_suggested": "include", "ai_confidence": 55},
        ],
    }), content_type="application/json")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))

    csv_text = zf.read(next(n for n in zf.namelist() if n.endswith("screening_decisions.csv"))).decode("utf-8")
    header = csv_text.splitlines()[0]
    for col in ("ai_vendor", "ai_suggested", "ai_confidence"):
        assert col in header

    checklist = zf.read(next(n for n in zf.namelist() if n.endswith("PRISMA_evidence_checklist.txt"))).decode("utf-8")
    assert "Reviewer-AI agreement: 1/2 (50%)" in checklist

    rob = zf.read(next(n for n in zf.namelist() if n.endswith("risk_of_bias_template.csv"))).decode("utf-8")
    assert "P1" in rob and "P2" not in rob  # only included studies
    assert any(n.endswith("grade_certainty_template.csv") for n in zf.namelist())


def test_assessment_templates_use_protocol():
    import app as rsp_app
    rob, grade = rsp_app._assessment_templates(
        [{"title": "=cmd", "authors": ["A", "B"], "year": 2020}],
        {"robTool": "RoB 2", "outcomes": "Accuracy; Recall\nCost"})
    assert "D1 randomisation" in rob.splitlines()[0]
    assert "'=cmd" in rob and "A; B" in rob  # CSV-injection safe
    assert [l.split(",")[0] for l in grade.splitlines()[1:]] == ["Accuracy", "Recall", "Cost"]


def test_screen_suggest_retries_unparseable_reply(client, monkeypatch):
    import app as rsp_app
    replies = iter(["Sure! The paper looks relevant.", '{"decision": "include", "reason": "ok", "confidence": 70}'])
    monkeypatch.setattr(rsp_app, "_load_ai_config", lambda: {
        "enabled": True, "provider": "ollama", "endpoint": "http://localhost:11434",
        "model": "m", "apiKey": "", "vendor": "ollama", "profiles": {}})
    monkeypatch.setattr(rsp_app, "_ai_chat", lambda *a, **k: next(replies))
    data = client.post("/api/ai/screen-suggest", json={"papers": [{"paperId": "p1", "title": "T"}]}).get_json()
    assert data["suggestions"]["p1"]["decision"] == "include"
    assert data["errors"] == []


def test_config_test_retries_unparseable_reply(client, monkeypatch):
    import app as rsp_app
    replies = iter(["hello there", '{"ok": true}'])
    monkeypatch.setattr(rsp_app, "_ai_chat", lambda *a, **k: next(replies))
    assert client.post("/api/ai-config/test", json={}).get_json()["ok"] is True


def test_pull_rejects_bad_model_name(client):
    resp = client.post("/api/ai-config/pull", json={"model": "../../etc; rm -rf"})
    assert resp.status_code == 400


def test_protocol_saves_heterogeneity_and_sensitivity(client):
    resp = client.post("/api/protocol", json={
        "heterogeneityMethod": "Subgroup by setting",
        "sensitivityMethod": "None performed",
    })
    assert resp.status_code == 200
    data = client.get("/api/protocol").get_json()
    proto = data.get("protocol", data)
    assert proto["heterogeneityMethod"] == "Subgroup by setting"
    assert proto["sensitivityMethod"] == "None performed"
