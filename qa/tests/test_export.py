"""
test_export.py – Tests for POST /api/export-replication-package.

Tags: api, export, zip
"""
import io
import json
import zipfile
from unittest.mock import patch, MagicMock

import pytest


def test_export_returns_zip_content_type(client):
    """
    GIVEN an empty docs list
    WHEN  POST /api/export-replication-package is called
    THEN  HTTP 200 with application/zip content-type is returned
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({"docs": [], "excludedDocs": [], "query": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert "zip" in resp.content_type.lower() or "octet" in resp.content_type.lower()


def test_export_returns_valid_zip(client):
    """
    GIVEN any docs list (empty is fine)
    WHEN  POST /api/export-replication-package is called
    THEN  the response body is a valid ZIP archive
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({"docs": [], "excludedDocs": [], "query": "test"}),
        content_type="application/json",
    )
    assert zipfile.is_zipfile(io.BytesIO(resp.data)), "Response is not a valid ZIP file"


def test_export_zip_has_required_folders(client):
    """
    GIVEN any docs list
    WHEN  POST /api/export-replication-package is called
    THEN  the ZIP contains the 4 required top-level folders
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({"docs": [], "excludedDocs": [], "query": "test"}),
        content_type="application/json",
    )
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        names = zf.namelist()
    # Each required folder must appear in at least one entry
    required = ["01_search_strategy", "02_data", "03_protocol", "04_images", "05_evidence_checklist"]
    for folder in required:
        assert any(folder in name for name in names), (
            f"ZIP is missing required folder '{folder}'. Found: {names}"
        )


def test_export_zip_has_readme(client):
    """
    GIVEN any docs list
    WHEN  POST /api/export-replication-package is called
    THEN  the ZIP contains a README.md file
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({"docs": [], "excludedDocs": [], "query": "test"}),
        content_type="application/json",
    )
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        names = zf.namelist()
    assert any("README" in name.upper() for name in names), (
        f"ZIP is missing README.md. Found: {names}"
    )


def test_export_with_docs_includes_csv(client):
    """
    GIVEN a non-empty docs list
    WHEN  POST /api/export-replication-package is called
    THEN  the ZIP contains a CSV file in 02_data/
    """
    sample_docs = [
        {
            "source": "HAL", "title": "Test Paper", "authors": "Author A",
            "year": 2022, "journal": "Journal X", "abstract": "An abstract.",
            "url": "https://example.com", "doi": "10.1/test",
            "keywords": ["test"], "docType": "ART",
        }
    ]
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({
            "docs": sample_docs,
            "excludedDocs": [],
            "query": "machine learning",
        }),
        content_type="application/json",
    )
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        names = zf.namelist()
    assert any("02_data" in n and n.endswith(".csv") for n in names), (
        f"ZIP missing CSV in 02_data. Found: {names}"
    )


def test_export_with_empty_docs_does_not_crash(client):
    """
    GIVEN empty docs and excludedDocs
    WHEN  POST /api/export-replication-package is called
    THEN  HTTP 200 is returned without error
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({
            "docs": [],
            "excludedDocs": [],
            "query": "",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_export_filename_header_present(client):
    """
    GIVEN a successful export
    WHEN  POST /api/export-replication-package is called
    THEN  Content-Disposition header with a filename is present
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({"docs": [], "excludedDocs": [], "query": "test"}),
        content_type="application/json",
    )
    cd = resp.headers.get("Content-Disposition", "")
    assert "filename" in cd.lower() or "attachment" in cd.lower(), (
        f"Content-Disposition header missing or has no filename: '{cd}'"
    )


# ---------------------------------------------------------------------------
# PRISMA evidence checklist
# ---------------------------------------------------------------------------

def _checklist_from(client, payload):
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    name = next(n for n in zf.namelist() if n.endswith("PRISMA_evidence_checklist.txt"))
    return zf, zf.read(name).decode("utf-8")


def test_checklist_marks_absent_artefacts_as_not_applicable(client):
    """A run with no screening must not claim screening_decisions.csv exists."""
    zf, text = _checklist_from(client, {
        "searchName": "NoScreening", "docs": [], "excludedDocs": [], "query": "test",
    })

    assert not any(n.endswith("screening_decisions.csv") for n in zf.namelist())
    line = next(l for l in text.splitlines() if "screening_decisions.csv" in l)
    assert line.strip().startswith("[--]"), f"absent artefact not flagged: {line}"


def test_checklist_marks_present_artefacts_as_ok(client):
    """Artefacts that are written must be marked present."""
    zf, text = _checklist_from(client, {
        "searchName": "Present", "docs": [], "excludedDocs": [], "query": "test",
    })

    assert any(n.endswith("included_studies.csv") for n in zf.namelist())
    line = next(l for l in text.splitlines() if "included_studies.csv" in l)
    assert line.strip().startswith("[OK]"), f"present artefact not marked: {line}"


def test_checklist_does_not_treat_artifacts_as_reporting_confirmation(client):
    _, text = _checklist_from(client, {
        "searchName": "Evidence", "docs": [], "excludedDocs": [], "query": "test",
    })

    assert "File presence does not establish that an item is reported" in text
    assert "6: Information sources" in text
    assert "7: Search strategies" in text
    assert "8: Selection process" in text
    assert "16c:" not in text
    assert "16b: verify citations and reasons" in text
    assert "verify reporting in the manuscript" in text
    assert "PRISMA recommends >= 2" not in text


@pytest.fixture
def checklist_runs(tmp_path, monkeypatch):
    import app as rsp_app

    monkeypatch.setattr(rsp_app, "ADHOC_RESULTS_DIR", str(tmp_path / "adhoc"))
    monkeypatch.setattr(rsp_app, "PROTOCOL_FILE", str(tmp_path / "protocol.json"))
    run = {"query": "test", "databases": ["hal"], "filters": {}, "docs": [{"title": "A"}],
           "prisma": {"identification": {"total_found": 1, "retrieved": 1, "databases": {}},
                      "screening": {}, "eligibility": {}, "included": 1}}
    first = rsp_app.save_ad_hoc_run(run)
    second = rsp_app.save_ad_hoc_run(run)

    def context(run_id):
        return {"prisma": {**run["prisma"], "runId": run_id}, "formState": {"keywords": "test"},
                "docs": run["docs"], "excludedDocs": {}, "manualSteps": [], "snowballing": None}

    return first, second, context


def _checklist_eval(client, run_id, context):
    return client.post(f"/api/results/run/adhoc/{run_id}/checklist/evaluate", json={"context": context})


def _checklist_save(client, run_id, context, version, items):
    return client.put(f"/api/results/run/adhoc/{run_id}/checklist",
                      json={"context": context, "version": version, "items": items})


def test_run_checklist_is_isolated_and_exported_with_notes(client, checklist_runs):
    first, second, make_context = checklist_runs
    context = make_context(first)
    loaded = _checklist_eval(client, first, context).get_json()
    items = {"9": {"checked": True, "text": "Verified data extraction", "confirmedRevision": loaded["revision"]}}
    saved = _checklist_save(client, first, context, loaded["version"], items)
    assert saved.status_code == 200
    assert _checklist_eval(client, second, make_context(second)).get_json()["items"] == {}
    assert _checklist_eval(client, first, context).get_json()["items"] == items

    zf, text = _checklist_from(client, {
        "searchName": "Run", "keywords": "test", "databases": ["hal"], "filters": {},
        "runId": first, "docs": context["docs"], "prisma": context["prisma"],
        "checklistContext": context,
    })
    name = next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))
    record = json.loads(zf.read(name))
    entry = next(item for item in record["items"] if item["id"] == "9")
    assert entry["status"] == "confirmed" and entry["note"] == "Verified data extraction"
    assert "Verified data extraction" in text
    assert len(record["items"]) == 39


def test_checklist_confirmation_becomes_stale_when_evidence_changes(client, checklist_runs):
    first, _, make_context = checklist_runs
    context = make_context(first)
    initial = _checklist_eval(client, first, context).get_json()
    item = {"checked": True, "text": "Review", "confirmedRevision": initial["revision"]}
    assert _checklist_save(client, first, context, 0, {"8": item}).status_code == 200

    assert client.post(f"/api/results/run/adhoc/{first}/screening/decision",
                       json={"paperId": "title:a", "decision": "include"}).status_code == 200
    changed = _checklist_eval(client, first, context).get_json()
    assert changed["revision"] != initial["revision"]
    assert changed["items"]["8"] == item
    steps = [{"name": "Full text", "added": 0, "removed": 1, "desc": ""}]
    assert client.put(f"/api/results/run/adhoc/{first}/manual-evidence",
                      json={"version": 0, "manualSteps": steps, "snowballing": None}).status_code == 200
    changed_context = {**context, "manualSteps": steps}
    assert _checklist_eval(client, first, changed_context).get_json()["revision"] != changed["revision"]
    assert client.post("/api/protocol", json={"researchQuestions": "New question"}).status_code == 200
    assert _checklist_eval(client, first, changed_context).get_json()["revision"] != changed["revision"]

    zf, _ = _checklist_from(client, {
        "searchName": "Stale", "keywords": "test", "databases": ["hal"], "filters": {},
        "runId": first, "docs": context["docs"], "prisma": context["prisma"],
        "checklistContext": changed_context,
    })
    record = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))))
    assert next(item for item in record["items"] if item["id"] == "8")["status"] == "stale"


def test_checklist_rejects_cross_run_and_concurrent_writes(client, checklist_runs):
    first, second, make_context = checklist_runs
    context = make_context(first)
    initial = _checklist_eval(client, first, context).get_json()
    assert _checklist_eval(client, second, context).status_code == 400
    assert _checklist_save(client, first, context, 0, {}).status_code == 200
    assert _checklist_save(client, first, context, 0, {}).status_code == 409
    invalid = {"9": {"checked": True, "text": "", "confirmedRevision": initial["revision"]}}
    context["manualSteps"].append({"name": "Changed", "added": 0, "removed": 0})
    assert _checklist_save(client, first, context, 1, invalid).status_code == 409


def test_manual_evidence_is_run_scoped_versioned_and_exported(client, checklist_runs):
    first, second, make_context = checklist_runs
    endpoint = f"/api/results/run/adhoc/{first}/manual-evidence"
    first_context = make_context(first)
    original = _checklist_eval(client, first, first_context).get_json()
    item = {"checked": True, "text": "Reviewed", "confirmedRevision": original["revision"]}
    assert _checklist_save(client, first, first_context, 0, {"9": item}).status_code == 200
    steps = [{"name": "Full text", "added": 0, "removed": 1, "desc": "Reviewed"}]
    snow = {"backward": 3, "forward": 0, "other": 0, "excluded": 1, "desc": "Sources"}
    body = {"version": 0, "manualSteps": steps, "snowballing": snow}
    saved = client.put(endpoint, json=body)
    assert saved.status_code == 200 and saved.get_json()["version"] == 1
    assert client.get(endpoint).get_json()["manualSteps"] == steps
    assert client.get(f"/api/results/run/adhoc/{second}/manual-evidence").get_json()["manualSteps"] == []
    assert client.put(endpoint, json=body).status_code == 409
    assert _checklist_eval(client, first, first_context).status_code == 409
    assert _checklist_save(client, first, first_context, 1, {"9": item}).status_code == 409
    context = {**first_context, "manualSteps": steps, "snowballing": snow}
    assert _checklist_eval(client, first, context).get_json()["revision"] != original["revision"]
    zf, _ = _checklist_from(client, {
        "keywords": "test", "databases": ["hal"], "filters": {}, "runId": first,
        "docs": context["docs"], "prisma": context["prisma"], "checklistContext": context,
    })
    evidence = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("prisma_manual_evidence.json"))))
    assert evidence["manualSteps"] == steps and evidence["snowballing"] == snow
    svg = zf.read(next(n for n in zf.namelist() if n.endswith("prisma_flow_diagram.svg"))).decode()
    assert "Other-source records: 3" in svg and "Full text" in svg
    assert "Final studies for synthesis: 2" in svg
    record = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))))
    assert next(item for item in record["items"] if item["id"] == "9")["status"] == "stale"


def test_manual_evidence_rejects_invalid_counts_without_writing(client, checklist_runs):
    first, _, _ = checklist_runs
    endpoint = f"/api/results/run/adhoc/{first}/manual-evidence"
    snow = {"backward": 1, "forward": 0, "other": 0, "excluded": 2, "desc": ""}
    assert client.put(endpoint, json={"version": 0, "manualSteps": [], "snowballing": snow}).status_code == 400
    assert client.get(endpoint).get_json()["version"] == 0


def test_export_without_matching_evidence_never_claims_confirmation(client, checklist_runs):
    first, _, make_context = checklist_runs
    context = make_context(first)
    revision = _checklist_eval(client, first, context).get_json()["revision"]
    item = {"checked": True, "text": "Saved note", "confirmedRevision": revision}
    assert _checklist_save(client, first, context, 0, {"9": item}).status_code == 200
    zf, text = _checklist_from(client, {
        "searchName": "Mismatched", "keywords": "different", "databases": ["hal"], "filters": {},
        "runId": first, "docs": context["docs"], "prisma": context["prisma"],
        "checklistContext": context,
    })
    record = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))))
    assert next(item for item in record["items"] if item["id"] == "9")["status"] == "unverified"
    assert "confirmations cannot be verified" in text


def test_export_uses_one_protocol_snapshot_if_protocol_changes_mid_export(client, checklist_runs, monkeypatch):
    import app as rsp_app

    first, _, make_context = checklist_runs
    context = make_context(first)
    assert client.post("/api/protocol", json={"researchQuestions": "Original"}).status_code == 200
    revision = _checklist_eval(client, first, context).get_json()["revision"]
    item = {"checked": True, "text": "Reviewed", "confirmedRevision": revision}
    assert _checklist_save(client, first, context, 0, {"9": item}).status_code == 200
    original = rsp_app._assessment_templates

    def update_during_export(studies, protocol):
        rsp_app._write_json_atomic(rsp_app.PROTOCOL_FILE, {"researchQuestions": "Changed"})
        return original(studies, protocol)

    monkeypatch.setattr(rsp_app, "_assessment_templates", update_during_export)
    zf, _ = _checklist_from(client, {
        "keywords": "test", "databases": ["hal"], "filters": {}, "runId": first,
        "docs": context["docs"], "prisma": context["prisma"], "checklistContext": context,
    })
    protocol = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("review_protocol.json"))))
    record = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))))
    assert protocol["researchQuestions"] == "Original"
    assert next(item for item in record["items"] if item["id"] == "9")["status"] == "confirmed"
    assert _checklist_eval(client, first, context).get_json()["revision"] != revision


def test_saved_search_run_checklist_is_accessible_and_exported(client, tmp_path, monkeypatch):
    import app as rsp_app
    import uuid

    monkeypatch.setattr(rsp_app, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(rsp_app, "PROTOCOL_FILE", str(tmp_path / "protocol.json"))
    search_id = str(uuid.uuid4())
    run = rsp_app.save_search_run(search_id, {
        "query": "test", "databases": ["hal"], "filters": {}, "docs": [{"title": "A"}],
        "prisma": {"identification": {"total_found": 1, "retrieved": 1, "databases": {}},
                   "screening": {}, "eligibility": {}, "included": 1},
    })
    run_id = run["runId"]
    manual_endpoint = f"/api/results/run/saved/{search_id}/{run_id}/manual-evidence"
    steps = [{"name": "Full text", "added": 0, "removed": 0, "desc": "Checked"}]
    assert client.put(manual_endpoint, json={"version": 0, "manualSteps": steps, "snowballing": None}).status_code == 200
    assert client.get(manual_endpoint).get_json()["manualSteps"] == steps
    context = {"prisma": {**run["prisma"], "runId": run_id}, "formState": {"keywords": "test"},
               "docs": run["docs"], "excludedDocs": {}, "manualSteps": steps, "snowballing": None}
    endpoint = f"/api/results/run/saved/{search_id}/{run_id}/checklist"
    loaded = client.post(endpoint + "/evaluate", json={"context": context}).get_json()
    item = {"checked": True, "text": "Saved search note", "confirmedRevision": loaded["revision"]}
    assert client.put(endpoint, json={"context": context, "version": 0, "items": {"9": item}}).status_code == 200
    assert client.get(f"/prisma?runId={run_id}&searchId={search_id}").status_code == 200
    zf, _ = _checklist_from(client, {
        "searchName": "Saved", "keywords": "test", "databases": ["hal"], "filters": {},
        "runId": run_id, "searchId": search_id, "docs": run["docs"], "includedDocs": run["docs"],
        "prisma": context["prisma"], "checklistContext": context,
    })
    record = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("researcher_checklist.json"))))
    assert next(item for item in record["items"] if item["id"] == "9")["status"] == "confirmed"
    evidence = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("prisma_manual_evidence.json"))))
    assert evidence["manualSteps"] == steps


def test_checklist_reports_every_exclusion_reason(client):
    """PRISMA 16b needs per-reason counts, not just the duplicate total."""
    _, text = _checklist_from(client, {
        "searchName": "Reasons", "docs": [], "excludedDocs": [], "query": "test",
        "prisma": {
            "identification": {"total_found": 10, "retrieved": 10, "databases": {}},
            "screening": {"duplicates": 2, "year_range": 3, "language": 1},
            "eligibility": {"exclude_terms": 4},
            "included": 0,
        },
    })

    assert "Outside year range" in text
    assert "Language excluded" in text
    assert "Matched an exclude term" in text
    # Every reason must render a human label, never a raw dict key
    assert "Duplicate records" in text
    assert "\n  duplicates" not in text
    # 2 + 3 + 1 + 4
    assert "10" in text.split("Total excluded before screening")[1].splitlines()[0]


def test_checklist_flags_outstanding_manual_items(client):
    """The checklist must say what still needs human input."""
    _, text = _checklist_from(client, {
        "searchName": "Todo", "docs": [], "excludedDocs": [], "query": "test",
        "prisma": {
            "identification": {
                "total_found": 1, "retrieved": 1,
                "databases": {"wos": {"found": 1, "retrieved": 1, "error": "rate limited"}},
            },
            "screening": {}, "eligibility": {}, "included": 1,
        },
    })

    todo = text.split("ITEMS STILL NEEDING MANUAL ATTENTION")[1]
    assert "not registered" in todo
    assert "no title/abstract screening decisions" in todo
    assert "'wos' returned an error" in todo


def test_checklist_reports_screening_counts(client):
    """T&A counts are computed for the diagram and must appear in the checklist too."""
    decisions = [
        {"id": "doi:10.1/a", "decision": "include", "reason": "", "timestamp": ""},
        {"id": "doi:10.1/b", "decision": "exclude", "reason": "off topic", "timestamp": ""},
        {"id": "doi:10.1/c", "decision": "unsure", "reason": "", "timestamp": ""},
    ]
    _, text = _checklist_from(client, {
        "searchName": "Screened", "docs": [], "excludedDocs": [], "query": "test",
        "screeningDecisions": decisions,
    })

    section = text.split("TITLE & ABSTRACT SCREENING")[1]
    assert "Included:" in section
    assert "Excluded:" in section
    assert "Unsure:" in section


def test_checklist_separates_citation_searching_from_databases(client):
    """PRISMA 16a: snowballed records are a distinct identification arm."""
    docs = [
        {"source": "HAL", "sources": ["HAL"], "title": "Db paper", "doi": "10.1/a"},
        {"source": "Snowball\u2191Forward", "sources": ["Snowball\u2191Forward"],
         "title": "Cited paper", "doi": "10.1/b"},
        {"source": "Snowball\u2193Backward", "sources": ["Snowball\u2193Backward"],
         "title": "Reference paper", "doi": "10.1/c"},
    ]
    _, text = _checklist_from(client, {
        "searchName": "Snowball", "docs": docs, "excludedDocs": [], "query": "test",
        "prisma": {
            "identification": {"total_found": 3, "retrieved": 3, "databases": {}},
            "screening": {}, "eligibility": {}, "included": 3,
        },
    })

    section = text.split("IDENTIFICATION BY SOURCE")[1]
    assert "Via citation searching:    2" in section
    assert "Via databases/registers:   1" in section


def test_doc_found_by_both_counts_as_database_identified():
    """A record corroborated by a database must not be counted as citation-only."""
    import app as rsp_app

    docs = [
        {"source": "HAL", "sources": ["HAL", "Snowball\u2191Forward"]},
        {"source": "Snowball\u2191Forward", "sources": ["Snowball\u2191Forward"]},
    ]
    assert rsp_app._count_other_methods(docs) == 1


def test_checklist_reports_validation_recall(client):
    """Seed papers must be matched against results and reported."""
    docs = [{"source": "HAL", "sources": ["HAL"],
             "title": "Deep learning for anomaly detection in networks", "doi": "10.1/a"}]
    _, text = _checklist_from(client, {
        "searchName": "Recall", "docs": docs, "excludedDocs": [], "query": "test",
        "validationPapers": [
            "Deep learning for anomaly detection in networks",
            "A paper that was never retrieved anywhere",
        ],
    })

    section = text.split("VALIDATION (SEED) PAPER RECALL")[1]
    assert "Retrieved 1 of 2 known papers." in section
    assert "A paper that was never retrieved anywhere" in section
    assert "may be too narrow" in section


def test_validation_recall_ignores_degenerate_titles():
    """A one-word doc title must not spuriously match every seed."""
    import app as rsp_app

    result = rsp_app._validation_recall(
        ["Deep learning for anomaly detection in networks"],
        [{"title": "S", "source": "OpenAlex"}],
    )
    assert result["found"] == []
    assert len(result["missing"]) == 1


def test_screening_csv_records_ai_provenance(client):
    """PRISMA requires disclosing automation used in selection."""
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({
            "searchName": "AI", "docs": [], "excludedDocs": [], "query": "test",
            "screeningDecisions": [
                {"title": "P1", "decision": "include", "reason": "",
                 "screened_at": "", "ai_assisted": "yes", "ai_model": "llama3"},
                {"title": "P2", "decision": "exclude", "reason": "off topic",
                 "screened_at": "", "ai_assisted": "no", "ai_model": ""},
            ],
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))

    csv_name = next(n for n in zf.namelist() if n.endswith("screening_decisions.csv"))
    csv_text = zf.read(csv_name).decode("utf-8")
    assert "ai_assisted" in csv_text and "ai_model" in csv_text
    assert "llama3" in csv_text

    chk = next(n for n in zf.namelist() if n.endswith("PRISMA_evidence_checklist.txt"))
    checklist = zf.read(chk).decode("utf-8")
    section = checklist.split("AUTOMATION TOOLS USED IN SELECTION")[1]
    assert "AI-assisted decisions: 1 of 2" in section
    assert "llama3" in section
