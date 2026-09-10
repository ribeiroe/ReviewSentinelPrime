"""
test_api_keys.py – API key CRUD endpoint tests.

Covers the 6 key-management endpoints (IEEE, Scopus, Inspec,
ScienceDirect, Springer, PubMed). The GitHub token endpoint was
removed from the app along with the GitHub upload feature.

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
    ("wos-key",           "Web of Science"),
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
    THEN  JSON body has keys: configured, key — and never the full secret
    """
    resp = client.get(f"/api/{endpoint}")
    data = resp.get_json()
    assert data is not None, f"{name}: response is not JSON"
    for field in ("configured", "key"):
        assert field in data, f"{name}: missing field '{field}' in response"
    assert "full" not in data, f"{name}: full key must never be returned"


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
    THEN  configured=True, the key is masked, and the full secret is absent
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
    assert "full" not in data, f"{name}: full key must never be returned"
    assert data["key"] != DUMMY_KEY and "..." in data["key"], f"{name}: key should be masked"


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


# ---------------------------------------------------------------------------
# EBSCO – three-value credentials rather than a single key
# ---------------------------------------------------------------------------

EBSCO_CREDS = {"userId": "rsp-test-user", "password": "s3cret-pass", "profile": "edsapi"}


def _clear_ebsco(client):
    client.post(
        "/api/ebsco-key",
        data=json.dumps({"userId": "", "password": "", "profile": ""}),
        content_type="application/json",
    )


def test_ebsco_get_unconfigured_shape(client):
    """
    GIVEN no EBSCO credentials are stored
    WHEN  GET /api/ebsco-key is called
    THEN  HTTP 200 with configured=False and no secret material
    """
    _clear_ebsco(client)
    resp = client.get("/api/ebsco-key")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["configured"] is False
    assert "password" not in data


def test_ebsco_save_and_get_is_configured(client):
    """
    GIVEN all three EBSCO values are posted
    WHEN  GET /api/ebsco-key is called afterwards
    THEN  configured=True, the user id is masked and the password is absent
    """
    resp = client.post(
        "/api/ebsco-key",
        data=json.dumps(EBSCO_CREDS),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True

    data = client.get("/api/ebsco-key").get_json()
    assert data["configured"] is True
    assert data["profile"] == "edsapi"
    assert data["userId"] != EBSCO_CREDS["userId"]
    assert "password" not in data
    assert EBSCO_CREDS["password"] not in json.dumps(data)
    _clear_ebsco(client)


def test_ebsco_password_is_never_returned_by_get(client):
    """The stored EDS password must never leave the server."""
    client.post(
        "/api/ebsco-key",
        data=json.dumps(EBSCO_CREDS),
        content_type="application/json",
    )
    body = client.get("/api/ebsco-key").get_data(as_text=True)
    assert EBSCO_CREDS["password"] not in body
    _clear_ebsco(client)


def test_ebsco_partial_credentials_are_rejected(client):
    """
    GIVEN a User ID but no Profile ID
    WHEN  POST /api/ebsco-key is called
    THEN  HTTP 400 is returned and nothing is stored
    """
    _clear_ebsco(client)
    resp = client.post(
        "/api/ebsco-key",
        data=json.dumps({"userId": "someone", "password": "pw", "profile": ""}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    assert client.get("/api/ebsco-key").get_json()["configured"] is False


def test_ebsco_blank_password_keeps_stored_one(client):
    """Re-saving with an empty password must not wipe the stored password."""
    import app as rsp_app

    client.post(
        "/api/ebsco-key",
        data=json.dumps(EBSCO_CREDS),
        content_type="application/json",
    )
    resp = client.post(
        "/api/ebsco-key",
        data=json.dumps({"userId": "rsp-test-user", "password": "", "profile": "newprofile"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    creds = rsp_app.get_ebsco_credentials()
    assert creds["password"] == EBSCO_CREDS["password"]
    assert creds["profile"] == "newprofile"
    _clear_ebsco(client)


def test_ebsco_clear_removes_credentials(client):
    """Posting all-empty values clears the stored credentials."""
    client.post(
        "/api/ebsco-key",
        data=json.dumps(EBSCO_CREDS),
        content_type="application/json",
    )
    _clear_ebsco(client)
    assert client.get("/api/ebsco-key").get_json()["configured"] is False
