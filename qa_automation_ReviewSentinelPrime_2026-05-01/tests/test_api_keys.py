"""
test_api_keys.py – API key CRUD endpoint tests.

Covers all 7 key-management endpoints (IEEE, Scopus, Inspec,
ScienceDirect, Springer, PubMed, GitHub).

Tags: api, keys
"""
import json
import pytest


# ---------------------------------------------------------------------------
# Parameterised: each tuple is (endpoint_suffix, readable_name)
# ---------------------------------------------------------------------------
KEY_ENDPOINTS = [
    ("ieee-key",          "IEEE"),
    ("scopus-key",        "Scopus"),
    ("inspec-key",        "Inspec"),
    ("sciencedirect-key", "ScienceDirect"),
    ("springer-key",      "Springer"),
    ("pubmed-key",        "PubMed"),
    ("github-token",      "GitHub"),
]

DUMMY_KEY = "TEST_KEY_ABCDEFGH"


# ---------------------------------------------------------------------------
# GET – unconfigured state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_get_key_returns_200(client, endpoint, name):
    """
    GIVEN no key is stored
    WHEN  GET /api/<endpoint> is called
    THEN  HTTP 200 is returned
    """
    resp = client.get(f"/api/{endpoint}")
    assert resp.status_code == 200, f"{name}: GET returned {resp.status_code}"


@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_get_unconfigured_key_has_correct_shape(client, endpoint, name):
    """
    GIVEN no key is stored
    WHEN  GET /api/<endpoint> is called
    THEN  JSON body has keys: configured, key, full
    """
    resp = client.get(f"/api/{endpoint}")
    data = resp.get_json()
    assert data is not None, f"{name}: response is not JSON"
    for field in ("configured", "key", "full"):
        assert field in data, f"{name}: missing field '{field}' in response"


# ---------------------------------------------------------------------------
# POST – save a key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_save_key_returns_200(client, endpoint, name):
    """
    GIVEN a key value in the request body
    WHEN  POST /api/<endpoint> is called
    THEN  HTTP 200 is returned with {"ok": true}
    """
    resp = client.post(
        f"/api/{endpoint}",
        data=json.dumps({"key": DUMMY_KEY}),
        content_type="application/json",
    )
    assert resp.status_code == 200, f"{name}: POST returned {resp.status_code}"
    data = resp.get_json()
    assert data.get("ok") is True, f"{name}: POST did not return ok=True"


@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_get_key_after_save_is_configured(client, endpoint, name):
    """
    GIVEN a key was previously saved
    WHEN  GET /api/<endpoint> is called
    THEN  configured=True and full key matches
    """
    # Save first
    client.post(
        f"/api/{endpoint}",
        data=json.dumps({"key": DUMMY_KEY}),
        content_type="application/json",
    )
    resp = client.get(f"/api/{endpoint}")
    data = resp.get_json()
    assert data["configured"] is True, f"{name}: configured should be True after save"
    assert data["full"] == DUMMY_KEY, f"{name}: full key mismatch after save"


@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_save_empty_key_clears_configuration(client, endpoint, name):
    """
    GIVEN a key was saved
    WHEN  POST /api/<endpoint> with an empty string is called
    THEN  configured=False on the next GET
    """
    # Save a key first
    client.post(
        f"/api/{endpoint}",
        data=json.dumps({"key": DUMMY_KEY}),
        content_type="application/json",
    )
    # Clear it
    client.post(
        f"/api/{endpoint}",
        data=json.dumps({"key": ""}),
        content_type="application/json",
    )
    resp = client.get(f"/api/{endpoint}")
    data = resp.get_json()
    assert data["configured"] is False, f"{name}: configured should be False after clearing"


@pytest.mark.parametrize("endpoint, name", KEY_ENDPOINTS)
def test_masked_key_format_when_key_longer_than_8(client, endpoint, name):
    """
    GIVEN a key longer than 8 characters is stored
    WHEN  GET /api/<endpoint> is called
    THEN  the 'key' field is masked (contains '...')
    """
    long_key = "ABCDEFGHIJKLMNOP"  # 16 chars
    client.post(
        f"/api/{endpoint}",
        data=json.dumps({"key": long_key}),
        content_type="application/json",
    )
    resp = client.get(f"/api/{endpoint}")
    data = resp.get_json()
    assert "..." in data["key"], (
        f"{name}: key longer than 8 chars should be masked with '...'"
    )
