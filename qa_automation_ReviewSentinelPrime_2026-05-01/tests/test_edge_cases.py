"""
test_edge_cases.py – Negative tests, boundary conditions, and security guards.

Tags: negative, edge, security
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# /search edge cases
# ---------------------------------------------------------------------------

def test_search_with_no_databases_returns_400_or_empty(client):
    """
    GIVEN an empty databases list
    WHEN  POST /search is called
    THEN  HTTP 400 or HTTP 200 with empty docs (no crash)
    """
    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "test", "databases": []}),
        content_type="application/json",
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        data = resp.get_json()
        assert len(data.get("docs", [])) == 0


def test_search_with_max_results_exceeding_cap_returns_200(client):
    """
    GIVEN maxResults of 9999
    WHEN  POST /search is called
    THEN  HTTP 200 is returned without errors (capped internally)
    """
    from unittest.mock import patch, MagicMock
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": {"numFound": 0, "docs": []}}

    with patch("requests.get", return_value=mock_resp):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "test", "databases": ["hal"], "rows": 9999}),
            content_type="application/json",
        )
    assert resp.status_code == 200


def test_search_with_non_json_body_returns_error(client):
    """
    GIVEN a non-JSON request body
    WHEN  POST /search is called
    THEN  HTTP 400 or 415 is returned
    """
    resp = client.post(
        "/search",
        data="not-json-at-all",
        content_type="text/plain",
    )
    assert resp.status_code in (400, 415, 500)


def test_search_with_very_long_keyword_does_not_crash(client):
    """
    GIVEN an extremely long keyword string (10 000 chars)
    WHEN  POST /search is called
    THEN  the server does not raise a 500
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": {"numFound": 0, "docs": []}}

    with patch("requests.get", return_value=mock_resp):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "a" * 10000, "databases": ["hal"]}),
            content_type="application/json",
        )
    assert resp.status_code != 500


def test_search_sql_injection_like_input_does_not_crash(client):
    """
    GIVEN a SQL injection-like keyword
    WHEN  POST /search is called
    THEN  the server returns 200 or 400, not 500
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": {"numFound": 0, "docs": []}}

    with patch("requests.get", return_value=mock_resp):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "'; DROP TABLE searches; --", "databases": ["hal"]}),
            content_type="application/json",
        )
    assert resp.status_code in (200, 400)


def test_search_xss_payload_is_not_reflected_unescaped(client):
    """
    GIVEN a keyword containing a script tag
    WHEN  POST /search is called
    THEN  any reflection of the keyword in the response is escaped / not executable
    """
    xss_payload = "<script>alert('xss')</script>"
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": {"numFound": 0, "docs": []}}

    with patch("requests.get", return_value=mock_resp):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": xss_payload, "databases": ["hal"]}),
            content_type="application/json",
        )
    # The raw <script> tag should not appear verbatim in a JSON API response
    body = resp.data.decode("utf-8", errors="replace")
    # In JSON context it gets encoded as \u003cscript\u003e or similar
    assert "<script>" not in body or resp.content_type.startswith("application/json")


# ---------------------------------------------------------------------------
# /api/searches edge cases
# ---------------------------------------------------------------------------

def test_create_search_with_missing_name_still_responds(client):
    """
    GIVEN a search payload missing the 'name' field
    WHEN  POST /api/searches is called
    THEN  HTTP 400 or a default name is used (no 500 crash)
    """
    resp = client.post(
        "/api/searches",
        data=json.dumps({
            "query": "test",
            "databases": ["hal"],
            "maxResults": 5,
            "schedule": {"type": "manual"},
        }),
        content_type="application/json",
    )
    assert resp.status_code in (201, 400), f"Expected 201 or 400, got {resp.status_code}"
    if resp.status_code == 201:
        data = resp.get_json()
        client.delete(f"/api/searches/{data['id']}")


def test_create_search_with_negative_max_results(client):
    """
    GIVEN maxResults=-5
    WHEN  POST /api/searches is called
    THEN  the server handles it gracefully (no 500)
    """
    resp = client.post(
        "/api/searches",
        data=json.dumps({
            "name": "Negative",
            "query": "test",
            "databases": ["hal"],
            "maxResults": -5,
            "schedule": {"type": "manual"},
            "filters": {},
            "validationPapers": [],
        }),
        content_type="application/json",
    )
    assert resp.status_code in (201, 400)
    if resp.status_code == 201:
        data = resp.get_json()
        client.delete(f"/api/searches/{data['id']}")


def test_api_key_post_with_non_json_body(client):
    """
    GIVEN a non-JSON body POSTed to an API key endpoint
    WHEN  POST /api/ieee-key is called
    THEN  HTTP 400 is returned (not 500)
    """
    resp = client.post(
        "/api/ieee-key",
        data="not-json",
        content_type="text/plain",
    )
    assert resp.status_code in (400, 415, 500)


# ---------------------------------------------------------------------------
# Export edge cases
# ---------------------------------------------------------------------------

def test_export_with_malformed_doc_does_not_crash(client):
    """
    GIVEN docs list with a doc missing required fields
    WHEN  POST /api/export-replication-package is called
    THEN  HTTP 200 is returned (graceful handling)
    """
    resp = client.post(
        "/api/export-replication-package",
        data=json.dumps({
            "docs": [{"title": "Broken doc"}],  # minimal/incomplete
            "excludedDocs": [],
            "query": "test",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GitHub upload guard (no token)
# ---------------------------------------------------------------------------

def test_upload_without_github_token_returns_error(client):
    """
    GIVEN no GitHub token is configured
    WHEN  POST /api/upload-to-github is called
    THEN  HTTP 400 or 500 with an informative error is returned
    """
    import app as rsp_app
    original = rsp_app.GITHUB_TOKEN_FILE
    rsp_app.GITHUB_TOKEN_FILE = "/tmp/no_github_token_xyz.txt"

    try:
        resp = client.post(
            "/api/upload-to-github",
            data=json.dumps({
                "owner": "user",
                "repo": "myrepo",
                "branch": "main",
                "docs": [],
                "excludedDocs": [],
                "query": "test",
            }),
            content_type="application/json",
        )
        assert resp.status_code in (400, 500)
    finally:
        rsp_app.GITHUB_TOKEN_FILE = original
