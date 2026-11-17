import csv
import io
import json
import uuid
import zipfile

import pytest


@pytest.fixture
def runs(client, tmp_path, monkeypatch):
    import app as rsp

    monkeypatch.setattr(rsp, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(rsp, "ADHOC_RESULTS_DIR", str(tmp_path / "results" / "adhoc"))
    monkeypatch.setattr(rsp, "PROTOCOL_FILE", str(tmp_path / "protocol.json"))
    docs = [{"title": title, "doi": "10.1/" + title.lower()} for title in ("A", "B", "C", "D", "E")]
    data = {"query": "test", "databases": ["hal"], "filters": {}, "docs": docs,
            "prisma": {"identification": {"total_found": 5, "retrieved": 5, "databases": {}},
                       "screening": {}, "eligibility": {}, "included": 5}}
    adhoc = rsp.save_ad_hoc_run(data)
    search_id = str(uuid.uuid4())
    saved = rsp.save_search_run(search_id, data, keep_last=10)["runId"]
    return data, adhoc, search_id, saved


def _ta(client, base, title, decision):
    return client.post(base + "/screening/decision",
                       json={"paperId": "doi:10.1/" + title.lower(), "decision": decision})


def _ft(client, base, version, title, decision, reason=""):
    return client.put(base + "/full-text",
                      json={"version": version, "paperId": "doi:10.1/" + title.lower(),
                            "decision": decision, "reason": reason})


def test_full_text_candidates_are_run_scoped_and_require_reasons(client, runs):
    _, adhoc, search_id, saved = runs
    base = f"/api/results/run/adhoc/{adhoc}"
    saved_base = f"/api/results/run/saved/{search_id}/{saved}"
    assert _ta(client, base, "A", "include").status_code == 200
    assert _ta(client, base, "B", "exclude").status_code == 200
    assert _ta(client, saved_base, "C", "include").status_code == 200
    assert client.get(f"/api/results/run/saved/{search_id}/by-id/{saved}").status_code == 200
    assert [r["paper"]["title"] for r in client.get(base + "/full-text").get_json()["candidates"]] == ["A"]
    assert [r["paper"]["title"] for r in client.get(saved_base + "/full-text").get_json()["candidates"]] == ["C"]
    assert _ft(client, base, 0, "B", "include").status_code == 409
    assert _ft(client, base, 0, "A", "exclude").status_code == 400
    assert _ft(client, base, 0, "A", "not_retrieved").status_code == 400
    assert _ft(client, saved_base, 0, "C", "include").status_code == 200
    before_full_text = client.post("/api/export-replication-package", json={
        "keywords": "test", "databases": ["hal"], "filters": {}, "runId": adhoc})
    with zipfile.ZipFile(io.BytesIO(before_full_text.data)) as zf:
        contents = zf.read(next(n for n in zf.namelist() if n.endswith("included_studies.csv"))).decode("utf-8-sig")
        assert [row["title"] for row in csv.DictReader(io.StringIO(contents))] == ["A"]
    assert _ft(client, base, 0, "A", "exclude", "Wrong population").status_code == 200
    assert _ft(client, base, 0, "A", "include").status_code == 409
    assert client.delete(saved_base + "/screening/decision", json={"paperId": "doi:10.1/c"}).status_code == 200
    saved_state = client.get(saved_base + "/full-text").get_json()
    assert not saved_state["candidates"] and len(saved_state["inactive"]) == 1
    assert client.delete(saved_base + "/screening").status_code == 200


def test_full_text_export_and_checklist_preserve_pending_and_reasons(client, runs):
    data, adhoc, _, _ = runs
    base = f"/api/results/run/adhoc/{adhoc}"
    for title in "ABCD":
        assert _ta(client, base, title, "include").status_code == 200
    assert _ta(client, base, "E", "exclude").status_code == 200
    context = {"prisma": {**data["prisma"], "runId": adhoc}, "formState": {"keywords": "test"},
               "docs": data["docs"], "excludedDocs": {}, "manualSteps": [], "snowballing": None}
    loaded = client.post(base + "/checklist/evaluate", json={"context": context}).get_json()
    item = {"checked": True, "text": "Reviewed", "confirmedRevision": loaded["revision"]}
    assert client.put(base + "/checklist", json={"version": 0, "items": {"16b": item},
                                                "context": context}).status_code == 200
    assert _ft(client, base, 0, "A", "include").status_code == 200
    assert _ft(client, base, 1, "B", "exclude", "Wrong population").status_code == 200
    assert _ft(client, base, 2, "C", "not_retrieved", "Unavailable").status_code == 200
    refreshed = client.post(base + "/checklist/evaluate", json={"context": context}).get_json()
    assert refreshed["revision"] != loaded["revision"]
    assert {k: refreshed["fullText"][k] for k in ("included", "excluded", "notRetrieved", "pending")} == {
        "included": 1, "excluded": 1, "notRetrieved": 1, "pending": 1}
    response = client.post("/api/export-replication-package", json={
        "keywords": "test", "databases": ["hal"], "filters": {}, "runId": adhoc,
        "docs": data["docs"], "prisma": context["prisma"], "checklistContext": context})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        def read(suffix):
            return zf.read(next(n for n in zf.namelist() if n.endswith(suffix))).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(read("full_text_screening.csv"))))
        assert [(r["title"], r["decision"], r["reason"]) for r in rows] == [
            ("A", "include", ""), ("B", "exclude", "Wrong population"),
            ("C", "not_retrieved", "Unavailable"), ("D", "pending", "")]
        included = list(csv.DictReader(io.StringIO(read("included_studies.csv"))))
        assert [r["title"] for r in included] == ["A"]
        svg = read("prisma_flow_diagram.svg")
        assert "FULL TEXT" in svg and "Not retrieved: 1" in svg and "Included so far: 1" in svg
        checklist = read("PRISMA_evidence_checklist.txt")
        assert "not retrieved: 1" in checklist and "pending: 1" in checklist
        saved_checklist = json.loads(read("researcher_checklist.json"))
        assert next(row for row in saved_checklist["items"] if row["id"] == "16b")["status"] == "stale"

    assert _ta(client, base, "B", "exclude").status_code == 200
    state = client.get(base + "/full-text").get_json()
    assert len(state["candidates"]) == 3 and state["inactive"][0]["decision"]["reason"] == "Wrong population"
    assert _ft(client, base, state["version"], "B", "include").status_code == 409
    response = client.post("/api/export-replication-package", json={
        "keywords": "test", "databases": ["hal"], "filters": {}, "runId": adhoc})
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        contents = zf.read(next(n for n in zf.namelist() if n.endswith("full_text_screening.csv"))).decode()
        rows = list(csv.DictReader(io.StringIO(contents)))
        inactive = next(row for row in rows if row["title"] == "B")
        assert inactive["eligibility"] == "no longer eligible" and inactive["reason"] == "Wrong population"


def test_full_text_rejects_ambiguous_paper_ids(client, runs):
    import app as rsp

    data, _, _, _ = runs
    data["docs"] = [{"title": "Duplicate"}, {"title": "Duplicate"}]
    run_id = rsp.save_ad_hoc_run(data)
    base = f"/api/results/run/adhoc/{run_id}"
    assert client.post(base + "/screening/decision",
                       json={"paperId": "title:duplicate", "decision": "include"}).status_code == 200
    state = client.get(base + "/full-text").get_json()
    assert len(state["candidates"]) == 2 and all(r["ambiguous"] for r in state["candidates"])
    assert client.put(base + "/full-text", json={"version": 0, "paperId": "title:duplicate",
                                                  "decision": "include", "reason": ""}).status_code == 409


@pytest.mark.parametrize("saved_run", [False, True])
def test_two_reviewers_resolve_and_invalidate_decisions(client, runs, saved_run):
    data, adhoc, search_id, saved = runs
    run_id = saved if saved_run else adhoc
    base = f"/api/results/run/saved/{search_id}/{saved}" if saved_run else f"/api/results/run/adhoc/{adhoc}"
    ta = base + "/dual-screening/title-abstract"
    ft = base + "/dual-screening/full-text"

    assert client.post(ta, json={"reviewers": ["Same", "same"]}).status_code == 400
    context = {"prisma": {**data["prisma"], "runId": run_id}, "formState": {"keywords": "test"},
               "docs": data["docs"], "excludedDocs": {}, "manualSteps": [], "snowballing": None}
    original_revision = client.post(base + "/checklist/evaluate", json={"context": context}).get_json()["revision"]
    assert client.post(ta, json={"reviewers": ["Alice", "Bob"]}).status_code == 200
    assert _ta(client, base, "A", "include").status_code == 409
    assert client.delete(base + "/screening").status_code == 409

    def review(url, version, title, reviewer, decision, reason=""):
        return client.put(url, json={"version": version, "paperId": "doi:10.1/" + title.lower(),
                                     "action": "review", "reviewer": reviewer, "decision": decision,
                                     "reason": reason})

    assert review(ta, 0, "A", 0, "include").status_code == 200
    premature = {"version": 1, "paperId": "doi:10.1/a", "action": "adjudicate",
                 "decision": "include", "reason": "Premature", "adjudicator": "Casey"}
    assert client.put(ta, json=premature).status_code == 409
    assert client.post(base + "/checklist/evaluate", json={"context": context}).get_json()["revision"] != original_revision
    initial_export = {"keywords": "test", "databases": ["hal"], "filters": {}, "runId": run_id}
    if saved_run:
        initial_export["searchId"] = search_id
    response = client.post("/api/export-replication-package", json=initial_export)
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        included = zf.read(next(n for n in zf.namelist() if n.endswith("included_studies.csv"))).decode("utf-8-sig")
        svg = zf.read(next(n for n in zf.namelist() if n.endswith("prisma_flow_diagram.svg"))).decode()
        assert list(csv.DictReader(io.StringIO(included))) == []
        assert "T&amp;A SCREENING" in svg and "Included so far: 0" in svg
    assert review(ta, 0, "A", 1, "include").status_code == 409
    assert client.get(base + "/full-text").get_json()["candidates"] == []
    assert review(ta, 1, "A", 1, "include").status_code == 200
    assert [r["paper"]["title"] for r in client.get(base + "/full-text").get_json()["candidates"]] == ["A"]
    assert review(ta, 2, "B", 0, "include").status_code == 200
    assert review(ta, 3, "B", 1, "exclude", "Wrong design").status_code == 200
    adjudication = {"version": 4, "paperId": "doi:10.1/b", "action": "adjudicate",
                    "decision": "include", "reason": "Agreed after discussion", "adjudicator": "Casey"}
    assert client.put(ta, json=adjudication).status_code == 200
    assert len(client.get(base + "/full-text").get_json()["candidates"]) == 2
    assert review(ta, 5, "B", 0, "unsure").status_code == 200
    assert [r["paper"]["title"] for r in client.get(base + "/full-text").get_json()["candidates"]] == ["A"]
    assert client.get(ta).get_json()["stage"]["records"]["doi:10.1/b"]["adjudication"] is None

    assert client.post(ft, json={"reviewers": ["Alice", "Bob"]}).status_code == 200
    assert _ft(client, base, 0, "A", "include").status_code == 409
    assert review(ft, 0, "A", 0, "include").status_code == 200
    payload = {"keywords": "test", "databases": ["hal"], "filters": {}, "runId": run_id}
    if saved_run:
        payload["searchId"] = search_id
    response = client.post("/api/export-replication-package", json=payload)
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        def read(suffix):
            return zf.read(next(n for n in zf.namelist() if n.endswith(suffix))).decode("utf-8-sig")
        assert list(csv.DictReader(io.StringIO(read("included_studies.csv")))) == []
        assert "Included so far: 0" in read("prisma_flow_diagram.svg")
        audit = json.loads(read("two_reviewer_screening.json"))
        assert audit["title-abstract"]["history"][-1]["decision"] == "unsure"
        assert audit["full-text"]["records"]["doi:10.1/a"]["reviews"]["0"]["decision"] == "include"
    assert review(ft, 1, "A", 1, "exclude", "Wrong population").status_code == 200
    adjudication = {"version": 2, "paperId": "doi:10.1/a", "action": "adjudicate",
                    "decision": "exclude", "reason": "Wrong population after discussion", "adjudicator": "Casey"}
    assert client.put(ft, json=adjudication).status_code == 200
    result = client.get(base + "/full-text").get_json()
    assert result["candidates"][0]["decision"]["reason"] == "Wrong population after discussion"
    assert review(ft, 3, "A", 0, "include").status_code == 200
    assert client.get(base + "/full-text").get_json()["candidates"][0]["decision"] is None
    assert review(ft, 4, "A", 1, "include").status_code == 200
    assert client.get(base + "/full-text").get_json()["candidates"][0]["decision"]["decision"] == "include"
    assert review(ta, 6, "A", 0, "exclude", "Wrong design").status_code == 200
    assert client.get(ft).get_json()["inactive"] == 1


def test_two_unsure_reviews_require_adjudication(client, runs):
    _, adhoc, _, _ = runs
    base = f"/api/results/run/adhoc/{adhoc}"
    url = base + "/dual-screening/title-abstract"
    assert client.post(url, json={"reviewers": ["Alice", "Bob"]}).status_code == 200
    body = {"paperId": "doi:10.1/a", "action": "review", "decision": "unsure", "reason": ""}
    for version in (0, 1):
        assert client.put(url, json={**body, "version": version, "reviewer": version}).status_code == 200
    assert client.get(base + "/full-text").get_json()["candidates"] == []
    adjudication = {"paperId": "doi:10.1/a", "action": "adjudicate", "version": 2,
                    "decision": "include", "reason": "Discussed eligibility", "adjudicator": "Alice"}
    assert client.put(url, json=adjudication).status_code == 200
    assert len(client.get(base + "/full-text").get_json()["candidates"]) == 1


def test_two_reviewer_setup_rejects_existing_decisions_and_ambiguous_records(client, runs):
    import app as rsp

    data, adhoc, _, _ = runs
    base = f"/api/results/run/adhoc/{adhoc}"
    assert _ta(client, base, "A", "include").status_code == 200
    assert client.post(base + "/dual-screening/title-abstract",
                       json={"reviewers": ["Alice", "Bob"]}).status_code == 409
    assert _ft(client, base, 0, "A", "include").status_code == 200
    assert client.post(base + "/dual-screening/full-text",
                       json={"reviewers": ["Alice", "Bob"]}).status_code == 409
    data["docs"] = [{"title": "Duplicate"}, {"title": "Duplicate"}]
    run = rsp.save_ad_hoc_run(data)
    url = f"/api/results/run/adhoc/{run}/dual-screening/title-abstract"
    assert client.post(url, json={"reviewers": ["Alice", "Bob"]}).status_code == 200
    assert client.put(url, json={"version": 0, "paperId": "title:duplicate", "action": "review",
                                 "reviewer": 0, "decision": "include", "reason": ""}).status_code == 409
