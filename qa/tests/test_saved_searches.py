"""
test_saved_searches.py – CRUD tests for the /api/searches endpoints.

Tags: api, saved-searches, crud
"""
import json
import pytest


# ---------------------------------------------------------------------------
# GET /api/searches – list
# ---------------------------------------------------------------------------

def test_list_searches_returns_200(client):
    """
    GIVEN the application is running
    WHEN  GET /api/searches is called
    THEN  HTTP 200 with a JSON array is returned
    """
    resp = client.get("/api/searches")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# POST /api/searches – create
# ---------------------------------------------------------------------------

def test_create_search_returns_201(client, sample_search_payload):
    """
    GIVEN a valid search payload
    WHEN  POST /api/searches is called
    THEN  HTTP 201 is returned with the new search object
    """
    resp = client.post(
        "/api/searches",
        data=json.dumps(sample_search_payload),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert "id" in data
    # Cleanup
    client.delete(f"/api/searches/{data['id']}")


def test_created_search_fields(client, sample_search_payload):
    """
    GIVEN a valid search payload
    WHEN  POST /api/searches is called
    THEN  the returned object contains all expected fields
    """
    resp = client.post(
        "/api/searches",
        data=json.dumps(sample_search_payload),
        content_type="application/json",
    )
    data = resp.get_json()
    for field in ("id", "name", "query", "databases", "maxResults",
                  "schedule", "filters", "createdAt", "lastRun"):
        assert field in data, f"Field '{field}' missing from created search"
    client.delete(f"/api/searches/{data['id']}")


def test_created_search_appears_in_list(client, created_search):
    """
    GIVEN a search was created
    WHEN  GET /api/searches is called
    THEN  the new search appears in the list
    """
    resp = client.get("/api/searches")
    ids = [s["id"] for s in resp.get_json()]
    assert created_search["id"] in ids


def test_create_search_caps_max_results(client):
    """
    GIVEN a maxResults value exceeding 200
    WHEN  POST /api/searches is called
    THEN  maxResults is capped at 200
    """
    payload = {
        "name": "Capped",
        "query": "test",
        "databases": ["hal"],
        "maxResults": 999,
        "schedule": {"type": "manual"},
        "filters": {},
        "validationPapers": [],
    }
    resp = client.post(
        "/api/searches",
        data=json.dumps(payload),
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["maxResults"] <= 200, "maxResults was not capped at 200"
    client.delete(f"/api/searches/{data['id']}")


# ---------------------------------------------------------------------------
# PUT /api/searches/<id> – update
# ---------------------------------------------------------------------------

def test_update_search_name(client, created_search):
    """
    GIVEN an existing search
    WHEN  PUT /api/searches/<id> is called with a new name
    THEN  HTTP 200 is returned and the name is updated
    """
    new_name = "Updated SLR Name"
    resp = client.put(
        f"/api/searches/{created_search['id']}",
        data=json.dumps({"name": new_name}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == new_name


def test_update_nonexistent_search_returns_404(client):
    """
    GIVEN a well-formed but non-existent search ID
    WHEN  PUT /api/searches/<id> is called
    THEN  HTTP 404 is returned
    """
    resp = client.put(
        "/api/searches/00000000-0000-4000-8000-000000000000",
        data=json.dumps({"name": "ghost"}),
        content_type="application/json",
    )
    assert resp.status_code == 404


def test_update_malformed_search_id_returns_400(client):
    """
    GIVEN a malformed (non-UUID) search ID
    WHEN  PUT /api/searches/<id> is called
    THEN  HTTP 400 is returned (UUID validation guard)
    """
    resp = client.put(
        "/api/searches/nonexistent-id-xyz",
        data=json.dumps({"name": "ghost"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/searches/<id>
# ---------------------------------------------------------------------------

def test_delete_search(client, sample_search_payload):
    """
    GIVEN an existing search
    WHEN  DELETE /api/searches/<id> is called
    THEN  HTTP 200 is returned and the search is removed from the list
    """
    # Create
    create_resp = client.post(
        "/api/searches",
        data=json.dumps(sample_search_payload),
        content_type="application/json",
    )
    search_id = create_resp.get_json()["id"]

    # Delete
    del_resp = client.delete(f"/api/searches/{search_id}")
    assert del_resp.status_code == 200
    assert del_resp.get_json().get("ok") is True

    # Verify gone
    list_resp = client.get("/api/searches")
    ids = [s["id"] for s in list_resp.get_json()]
    assert search_id not in ids


def test_delete_nonexistent_search_still_returns_ok(client):
    """
    GIVEN a well-formed search ID that does not exist
    WHEN  DELETE /api/searches/<id> is called
    THEN  HTTP 200 is returned (idempotent deletion)
    """
    resp = client.delete("/api/searches/00000000-0000-4000-8000-000000000000")
    assert resp.status_code == 200


def test_delete_malformed_search_id_returns_400(client):
    """
    GIVEN a malformed (non-UUID) search ID
    WHEN  DELETE /api/searches/<id> is called
    THEN  HTTP 400 is returned (UUID validation guard)
    """
    resp = client.delete("/api/searches/does-not-exist-abc")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/searches/<id>/results
# ---------------------------------------------------------------------------

def test_get_results_for_search_without_runs_returns_empty(client, created_search):
    """
    GIVEN a search that has never been run
    WHEN  GET /api/searches/<id>/results is called
    THEN  HTTP 200 with an empty list is returned
    """
    resp = client.get(f"/api/searches/{created_search['id']}/results")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 0
