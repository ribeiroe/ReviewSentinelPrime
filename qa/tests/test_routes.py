"""
test_routes.py – Page route smoke tests.

Verifies that every HTML page returns HTTP 200 and contains
expected content markers.

Tags: routes, smoke
"""
import pytest


# ---------------------------------------------------------------------------
# Happy-path: all public pages must return 200
# ---------------------------------------------------------------------------

PAGES = [
    ("/",          "Search",         "keywords"),
    ("/charts",    "Charts",         "chart"),
    ("/prisma",    "PRISMA",         "prisma"),
    ("/config",    "Saved Searches", "saved"),
    ("/settings",  "Settings",       "api"),
    ("/manual",    "Manual",         "manual"),
    ("/about",     "About",          "Review Sentinel"),
    ("/survey",    "Survey",         "survey"),
    ("/feedback",  "Feedback",       "bug"),
]


@pytest.mark.parametrize("url, page_label, content_hint", PAGES)
def test_page_returns_200(client, url, page_label, content_hint):
    """
    GIVEN the Flask app is running
    WHEN  a GET request is made to <url>
    THEN  the response status code is 200
    """
    resp = client.get(url)
    assert resp.status_code == 200, (
        f"Page '{page_label}' at {url} returned {resp.status_code}, expected 200"
    )


@pytest.mark.parametrize("url, page_label, content_hint", PAGES)
def test_page_returns_html(client, url, page_label, content_hint):
    """
    GIVEN the Flask app is running
    WHEN  a GET request is made to <url>
    THEN  the response Content-Type is text/html
    """
    resp = client.get(url)
    assert "text/html" in resp.content_type, (
        f"Page '{page_label}' at {url} content type was '{resp.content_type}'"
    )


@pytest.mark.parametrize("url, page_label, content_hint", PAGES)
def test_page_contains_nav(client, url, page_label, content_hint):
    """
    GIVEN the Flask app is running
    WHEN  a page is loaded
    THEN  the navigation bar is present (contains href="/")
    """
    resp = client.get(url)
    body = resp.data.decode("utf-8", errors="replace")
    assert 'href="/"' in body, f"Nav bar missing on page '{page_label}' ({url})"


def test_unknown_route_returns_404(client):
    """
    GIVEN the Flask app is running
    WHEN  a GET request is made to a non-existent URL
    THEN  the response status code is 404
    """
    resp = client.get("/this-page-does-not-exist-xyz")
    assert resp.status_code == 404


def test_survey_page_contains_submit_button(client):
    """Survey page must include the Submit Survey button."""
    resp = client.get("/survey")
    body = resp.data.decode("utf-8", errors="replace")
    assert "Submit Survey" in body


def test_feedback_page_contains_submit_button(client):
    """Feedback page must include the Submit button."""
    resp = client.get("/feedback")
    body = resp.data.decode("utf-8", errors="replace")
    assert "Submit" in body


def test_manual_page_has_toc(client):
    """Manual page must contain Table of Contents."""
    resp = client.get("/manual")
    body = resp.data.decode("utf-8", errors="replace")
    assert "Table of Contents" in body


def test_settings_page_has_api_key_sections(client):
    """Settings page must reference all key databases."""
    resp = client.get("/settings")
    body = resp.data.decode("utf-8", errors="replace").lower()
    for db in ["ieee", "scopus", "springer", "pubmed"]:
        assert db in body, f"Settings page missing section for '{db}'"
