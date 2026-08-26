"""
test_search_endpoint.py – Tests for POST /search (the main search route).

External HTTP calls are fully mocked so tests never hit real APIs.

Tags: api, search, mocking
"""
import json
import sys
import os
from unittest.mock import patch, MagicMock
import xml.etree.ElementTree as ET

import pytest

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Helpers – build mock API responses
# ---------------------------------------------------------------------------

def _hal_response(n=2):
    """Fake HAL JSON response."""
    docs = []
    for i in range(n):
        docs.append({
            "title_s": [f"HAL Paper {i}"],
            "authFullName_s": [f"Author {i}"],
            "producedDateY_i": 2022 + i,
            "journalTitle_s": "HAL Journal",
            "abstract_s": [f"Abstract for HAL paper {i}"],
            "uri_s": f"https://hal.science/hal-00{i}",
            "doiId_s": f"10.1234/hal{i}",
            "keyword_s": ["test"],
            "docType_s": "ART",
            "language_s": "en",
        })
    mock = MagicMock()
    mock.json.return_value = {"response": {"numFound": n, "docs": docs}}
    mock.raise_for_status = MagicMock()
    return mock


def _arxiv_response(n=2):
    """Fake arXiv Atom XML response."""
    entries = ""
    for i in range(n):
        entries += f"""
        <entry xmlns="http://www.w3.org/2005/Atom"
               xmlns:arxiv="http://arxiv.org/schemas/atom">
            <id>http://arxiv.org/abs/2401.0{i:04d}</id>
            <title>arXiv Paper {i}</title>
            <summary>Abstract for arXiv paper {i}</summary>
            <author><name>Author {i}</name></author>
            <published>2023-0{i+1:02d}-01T00:00:00Z</published>
            <arxiv:doi>10.1234/arxiv{i}</arxiv:doi>
            <category term="cs.SE"/>
        </entry>"""
    xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
        <opensearch:totalResults>{n}</opensearch:totalResults>
        {entries}
    </feed>"""
    mock = MagicMock()
    mock.text = xml_body
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Basic endpoint behaviour
# ---------------------------------------------------------------------------

def test_search_returns_400_on_empty_keywords(client):
    """
    GIVEN an empty keywords string
    WHEN  POST /search is called
    THEN  HTTP 400 with an error message is returned
    """
    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "", "databases": ["hal"]}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_search_returns_400_on_missing_keywords(client):
    """
    GIVEN no keywords key in the request body
    WHEN  POST /search is called
    THEN  HTTP 400 is returned
    """
    resp = client.post(
        "/search",
        data=json.dumps({"databases": ["hal"]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Mocked HAL search
# ---------------------------------------------------------------------------

@patch("requests.get")
def test_search_hal_returns_results(mock_get, client):
    """
    GIVEN a valid HAL mock response
    WHEN  POST /search is called with 'hal' database
    THEN  HTTP 200 and docs from HAL are returned
    """
    mock_get.return_value = _hal_response(3)

    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "machine learning", "databases": ["hal"], "rows": 10}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "docs" in data
    assert len(data["docs"]) == 3
    assert all(d["source"] == "HAL" for d in data["docs"])


@patch("requests.get")
def test_search_arxiv_returns_results(mock_get, client):
    """
    GIVEN a valid arXiv mock response
    WHEN  POST /search is called with 'arxiv' database
    THEN  HTTP 200 and docs from arXiv are returned
    """
    mock_get.return_value = _arxiv_response(2)

    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "deep learning", "databases": ["arxiv"], "rows": 10}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "docs" in data
    assert len(data["docs"]) == 2
    assert all(d["source"] == "arXiv" for d in data["docs"])


# ---------------------------------------------------------------------------
# PRISMA data in response
# ---------------------------------------------------------------------------

@patch("requests.get")
def test_search_response_includes_prisma_block(mock_get, client):
    """
    GIVEN a successful search
    WHEN  POST /search is called
    THEN  the response includes a 'prisma' block with 'identification'
    """
    mock_get.return_value = _hal_response(2)

    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "software testing", "databases": ["hal"]}),
        content_type="application/json",
    )
    data = resp.get_json()
    assert "prisma" in data
    assert "identification" in data["prisma"]


@patch("requests.get")
def test_prisma_included_count_equals_docs_length(mock_get, client):
    """
    GIVEN a search with no filters
    WHEN  POST /search is called
    THEN  prisma['included'] equals len(docs)
    """
    mock_get.return_value = _hal_response(4)

    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "neural network", "databases": ["hal"]}),
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["prisma"]["included"] == len(data["docs"])


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

@patch("requests.get")
def test_duplicate_dois_are_removed(mock_get, client):
    """
    GIVEN two databases return the same DOI
    WHEN  POST /search is called with both databases
    THEN  the duplicate is removed from the result set
    """
    # Both HAL and arXiv return one record with the same DOI
    hal_mock = MagicMock()
    hal_mock.raise_for_status = MagicMock()
    hal_mock.json.return_value = {
        "response": {
            "numFound": 1,
            "docs": [{
                "title_s": ["Same Paper"],
                "authFullName_s": ["Author A"],
                "producedDateY_i": 2023,
                "journalTitle_s": "Journal X",
                "abstract_s": ["Abstract"],
                "uri_s": "https://hal.science/hal-001",
                "doiId_s": "10.1234/dup",
                "keyword_s": [],
                "docType_s": "ART",
                "language_s": "en",
            }]
        }
    }

    arxiv_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
        <opensearch:totalResults>1</opensearch:totalResults>
        <entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
            <id>http://arxiv.org/abs/2401.99999</id>
            <title>Same Paper</title>
            <summary>Abstract</summary>
            <author><name>Author A</name></author>
            <published>2023-01-01T00:00:00Z</published>
            <arxiv:doi>10.1234/dup</arxiv:doi>
        </entry>
    </feed>"""
    arxiv_mock = MagicMock()
    arxiv_mock.raise_for_status = MagicMock()
    arxiv_mock.text = arxiv_xml

    # First call → HAL, second call → arXiv
    mock_get.side_effect = [hal_mock, arxiv_mock]

    resp = client.post(
        "/search",
        data=json.dumps({"keywords": "test", "databases": ["hal", "arxiv"]}),
        content_type="application/json",
    )
    data = resp.get_json()
    # Should only have 1 unique doc despite 2 sources returning it
    assert len(data["docs"]) == 1, "Duplicate DOI was not removed"


# ---------------------------------------------------------------------------
# Post-filters
# ---------------------------------------------------------------------------

@patch("requests.get")
def test_year_filter_removes_out_of_range_docs(mock_get, client):
    """
    GIVEN results include papers from 2018, 2020, 2023
    WHEN  a yearFrom=2020, yearTo=2022 filter is applied
    THEN  only the 2020 paper remains
    """
    hal_mock = MagicMock()
    hal_mock.raise_for_status = MagicMock()
    hal_mock.json.return_value = {
        "response": {
            "numFound": 3,
            "docs": [
                {"title_s": ["Old Paper"], "producedDateY_i": 2018,
                 "authFullName_s": [], "abstract_s": [], "doiId_s": "10.1/a",
                 "uri_s": "", "keyword_s": [], "docType_s": "ART", "language_s": "en"},
                {"title_s": ["In Range Paper"], "producedDateY_i": 2020,
                 "authFullName_s": [], "abstract_s": [], "doiId_s": "10.1/b",
                 "uri_s": "", "keyword_s": [], "docType_s": "ART", "language_s": "en"},
                {"title_s": ["Future Paper"], "producedDateY_i": 2023,
                 "authFullName_s": [], "abstract_s": [], "doiId_s": "10.1/c",
                 "uri_s": "", "keyword_s": [], "docType_s": "ART", "language_s": "en"},
            ]
        }
    }
    mock_get.return_value = hal_mock

    resp = client.post(
        "/search",
        data=json.dumps({
            "keywords": "test",
            "databases": ["hal"],
            "filters": {"yearFrom": 2020, "yearTo": 2022},
        }),
        content_type="application/json",
    )
    data = resp.get_json()
    years = [d["year"] for d in data["docs"]]
    assert all(2020 <= y <= 2022 for y in years), f"Year filter failed, got years: {years}"


@patch("requests.get")
def test_include_terms_filter(mock_get, client):
    """
    GIVEN results with different abstracts
    WHEN  includeTerms filter requires 'blockchain'
    THEN  only papers mentioning 'blockchain' are returned
    """
    hal_mock = MagicMock()
    hal_mock.raise_for_status = MagicMock()
    hal_mock.json.return_value = {
        "response": {
            "numFound": 2,
            "docs": [
                {"title_s": ["Blockchain Paper"], "producedDateY_i": 2022,
                 "authFullName_s": [], "abstract_s": ["About blockchain technology"],
                 "doiId_s": "10.1/bc", "uri_s": "", "keyword_s": [],
                 "docType_s": "ART", "language_s": "en"},
                {"title_s": ["Other Paper"], "producedDateY_i": 2022,
                 "authFullName_s": [], "abstract_s": ["About something else entirely"],
                 "doiId_s": "10.1/other", "uri_s": "", "keyword_s": [],
                 "docType_s": "ART", "language_s": "en"},
            ]
        }
    }
    mock_get.return_value = hal_mock

    resp = client.post(
        "/search",
        data=json.dumps({
            "keywords": "test",
            "databases": ["hal"],
            "filters": {"includeTerms": ["blockchain"]},
        }),
        content_type="application/json",
    )
    data = resp.get_json()
    assert len(data["docs"]) == 1
    assert "Blockchain" in data["docs"][0]["title"]


@patch("requests.get")
def test_exclude_venues_filter(mock_get, client):
    """
    GIVEN results from 'arXiv' and 'IEEE TSE' venues
    WHEN  excludeVenues=['arxiv'] is applied
    THEN  arXiv venue is excluded
    """
    hal_mock = MagicMock()
    hal_mock.raise_for_status = MagicMock()
    hal_mock.json.return_value = {
        "response": {
            "numFound": 2,
            "docs": [
                {"title_s": ["Keep this"], "producedDateY_i": 2022,
                 "authFullName_s": [], "abstract_s": [],
                 "doiId_s": "10.1/keep", "uri_s": "", "keyword_s": [],
                 "docType_s": "ART", "language_s": "en",
                 "journalTitle_s": "IEEE Transactions on Software Engineering"},
                {"title_s": ["Exclude this"], "producedDateY_i": 2022,
                 "authFullName_s": [], "abstract_s": [],
                 "doiId_s": "10.1/excl", "uri_s": "", "keyword_s": [],
                 "docType_s": "ART", "language_s": "en",
                 "journalTitle_s": "arXiv preprint"},
            ]
        }
    }
    mock_get.return_value = hal_mock

    resp = client.post(
        "/search",
        data=json.dumps({
            "keywords": "test",
            "databases": ["hal"],
            "filters": {"excludeVenues": ["arxiv"]},
        }),
        content_type="application/json",
    )
    data = resp.get_json()
    journals = [d.get("journal", "").lower() for d in data["docs"]]
    assert not any("arxiv" in j for j in journals), f"Venue filter failed, journals: {journals}"


# ---------------------------------------------------------------------------
# API key missing guard
# ---------------------------------------------------------------------------

def test_ieee_search_without_key_returns_empty(client):
    """
    GIVEN no IEEE API key is configured
    WHEN  POST /search is called with 'ieee' database only
    THEN  zero docs are returned (graceful degradation)
    """
    import app as rsp_app
    original = rsp_app.IEEE_API_KEY_FILE
    # Point to a non-existent file so key is always missing
    rsp_app.IEEE_API_KEY_FILE = "/tmp/definitely_no_key.txt"

    try:
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "test", "databases": ["ieee"]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data.get("docs", [])) == 0
    finally:
        rsp_app.IEEE_API_KEY_FILE = original
