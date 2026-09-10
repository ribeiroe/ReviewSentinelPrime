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


def _ebsco_record(i):
    """One EDS Record in the nested shape the EDS API returns."""
    return {
        "Header": {"DbId": "edstst", "PubTypeId": "academicJournal"},
        "PLink": f"http://search.ebscohost.com/login.aspx?direct=true&AN={i}",
        "Items": [{"Name": "Abstract", "Group": "Ab", "Data": f"Abstract <i>{i}</i> from EDS."}],
        "RecordInfo": {
            "BibRecord": {
                "BibEntity": {
                    "Titles": [{"TitleFull": f"EBSCO Paper {i}", "Type": "main"}],
                    "Identifiers": [{"Type": "doi", "Value": f"10.1234/ebsco{i}"}],
                    "Languages": [{"Text": "English", "Code": "eng"}],
                    "Subjects": [{"SubjectFull": "information retrieval"}],
                },
                "BibRelationships": {
                    "HasContributorRelationships": [
                        {"PersonEntity": {"Name": {"NameFull": f"Author {i}"}}}
                    ],
                    "IsPartOfRelationships": [
                        {
                            "BibEntity": {
                                "Titles": [{"TitleFull": "EBSCO Journal", "Type": "main"}],
                                "Dates": [{"Y": str(2020 + i), "Type": "published"}],
                            }
                        }
                    ],
                },
            }
        },
    }


def _ebsco_response(n=2):
    """Fake EDS /Search JSON response."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "SearchResult": {
            "Statistics": {"TotalHits": n},
            "Data": {"Records": [_ebsco_record(i) for i in range(n)]},
        }
    }
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


def test_openalex_language_metadata_is_preserved():
    """OpenAlex's authoritative language must survive normalization."""
    import app as rsp_app

    doc = rsp_app._normalize_openalex_doc(
        {"title": "English paper", "language": "en"},
        "OpenAlex",
    )

    assert doc["language"] == "en"


def test_openalex_english_record_survives_english_only_filter():
    """An OpenAlex English record must not be excluded as unknown language."""
    import app as rsp_app

    doc = rsp_app._normalize_openalex_doc(
        {"title": "English paper", "language": "en"},
        "OpenAlex",
    )
    prisma = {"screening": {}}

    filtered = rsp_app._apply_post_filters(
        [doc],
        {"languages": ["en"], "includeUnknownLanguage": False},
        prisma,
    )

    assert filtered == [doc]


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
# Mocked EBSCO (EDS) search
# ---------------------------------------------------------------------------

def test_normalize_ebsco_record_maps_all_fields():
    """The nested EDS record shape must flatten to the RSP doc schema."""
    import app as rsp_app

    doc = rsp_app._normalize_ebsco_record(_ebsco_record(1))

    assert doc["source"] == "EBSCO"
    assert doc["sources"] == ["EBSCO"]
    assert doc["title"] == "EBSCO Paper 1"
    assert doc["authors"] == ["Author 1"]
    assert doc["year"] == 2021
    assert doc["journal"] == "EBSCO Journal"
    assert doc["doi"] == "10.1234/ebsco1"
    assert doc["url"] == "https://doi.org/10.1234/ebsco1"
    assert doc["keywords"] == ["information retrieval"]
    assert doc["docType"] == "article"
    assert doc["language"] == "en"
    # EDS wraps field data in display markup that must not reach the UI
    assert doc["abstract"] == "Abstract 1 from EDS."
    assert "<i>" not in doc["abstract"]


def test_search_ebsco_without_credentials_returns_error():
    """EBSCO must fail with a clear message instead of calling the API unconfigured."""
    import app as rsp_app

    with patch.object(rsp_app, "get_ebsco_credentials",
                      return_value={"userId": "", "password": "", "profile": ""}):
        result = rsp_app.search_ebsco("machine learning", 10)

    assert result["docs"] == []
    assert result["numFound"] == 0
    assert "not configured" in result["error"].lower()


@patch("requests.get")
def test_search_ebsco_returns_results(mock_get, client):
    """
    GIVEN valid credentials and a mocked EDS search response
    WHEN  POST /search is called with the 'ebsco' database
    THEN  HTTP 200 and normalised EBSCO docs are returned
    """
    import app as rsp_app

    mock_get.return_value = _ebsco_response(3)

    with patch.object(rsp_app, "get_ebsco_credentials",
                      return_value={"userId": "u", "password": "p", "profile": "edsapi"}), \
         patch.object(rsp_app, "_ebsco_auth_token", return_value=("auth-tok", None)), \
         patch.object(rsp_app, "_ebsco_create_session", return_value=("sess-tok", None)), \
         patch.object(rsp_app, "_ebsco_end_session"):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "machine learning", "databases": ["ebsco"], "rows": 10}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["docs"]) == 3
    assert all(d["source"] == "EBSCO" for d in data["docs"])


@patch("requests.get")
def test_search_ebsco_always_ends_its_session(mock_get, client):
    """A failed EDS search must still release the session token."""
    import app as rsp_app

    failing = MagicMock()
    failing.status_code = 500
    mock_get.return_value = failing

    with patch.object(rsp_app, "get_ebsco_credentials",
                      return_value={"userId": "u", "password": "p", "profile": "edsapi"}), \
         patch.object(rsp_app, "_ebsco_auth_token", return_value=("auth-tok", None)), \
         patch.object(rsp_app, "_ebsco_create_session", return_value=("sess-tok", None)), \
         patch.object(rsp_app, "_ebsco_end_session") as mock_end:
        result = rsp_app.search_ebsco("machine learning", 10)

    mock_end.assert_called_once_with("auth-tok", "sess-tok")
    assert result["docs"] == []
    assert "500" in result["error"]


@patch("requests.get")
def test_search_ebsco_deduplicates_against_other_sources(mock_get, client):
    """An EBSCO record sharing a DOI with HAL must not be counted twice."""
    import app as rsp_app

    ebsco = _ebsco_response(1)
    # Force a DOI collision with the HAL fixture's first document
    record = ebsco.json.return_value["SearchResult"]["Data"]["Records"][0]
    record["RecordInfo"]["BibRecord"]["BibEntity"]["Identifiers"] = [
        {"Type": "doi", "Value": "10.1234/hal0"}
    ]

    with patch.object(rsp_app, "get_ebsco_credentials",
                      return_value={"userId": "u", "password": "p", "profile": "edsapi"}), \
         patch.object(rsp_app, "_ebsco_auth_token", return_value=("auth-tok", None)), \
         patch.object(rsp_app, "_ebsco_create_session", return_value=("sess-tok", None)), \
         patch.object(rsp_app, "_ebsco_end_session"):
        def _route(url, *a, **kw):
            return ebsco if "ebscohost.com" in url else _hal_response(1)

        mock_get.side_effect = _route
        resp = client.post(
            "/search",
            data=json.dumps({
                "keywords": "machine learning",
                "databases": ["hal", "ebsco"],
                "rows": 10,
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    dois = [d.get("doi") for d in resp.get_json()["docs"]]
    assert dois.count("10.1234/hal0") == 1


# ---------------------------------------------------------------------------
# Mocked Web of Science (Starter API) search
# ---------------------------------------------------------------------------

def _wos_hit(i):
    """One Web of Science Starter API hit."""
    return {
        "uid": f"WOS:00000000000000{i}",
        "title": f"WoS Paper {i}",
        "types": ["Article"],
        "source": {"sourceTitle": "WoS Journal", "publishYear": 2020 + i},
        "names": {"authors": [{"displayName": f"Author {i}", "wosStandard": f"Author, {i}"}]},
        "links": {"record": f"https://www.webofscience.com/wos/woscc/full-record/WOS:{i}"},
        "identifiers": {"doi": f"10.1234/wos{i}", "issn": "1234-5678"},
        "keywords": {"authorKeywords": ["bibliometrics"]},
    }


def _wos_response(n=2, total=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "metadata": {"total": n if total is None else total, "page": 1, "limit": 50},
        "hits": [_wos_hit(i) for i in range(n)],
    }
    mock.raise_for_status = MagicMock()
    return mock


def test_normalize_wos_doc_maps_all_fields():
    """The Starter API hit shape must flatten to the RSP doc schema."""
    import app as rsp_app

    doc = rsp_app._normalize_wos_doc(_wos_hit(1))

    assert doc["source"] == "Web of Science"
    assert doc["sources"] == ["Web of Science"]
    assert doc["title"] == "WoS Paper 1"
    assert doc["authors"] == ["Author 1"]
    assert doc["year"] == 2021
    assert doc["journal"] == "WoS Journal"
    assert doc["doi"] == "10.1234/wos1"
    assert doc["url"] == "https://doi.org/10.1234/wos1"
    assert doc["keywords"] == ["bibliometrics"]
    assert doc["docType"] == "article"
    # The Starter API has no abstract field at all
    assert doc["abstract"] == ""


def test_search_wos_without_key_returns_error():
    """WoS must fail with a clear message instead of calling the API unauthenticated."""
    import app as rsp_app

    with patch.object(rsp_app, "get_wos_api_key", return_value=""):
        result = rsp_app.search_wos("machine learning", 10)

    assert result["docs"] == []
    assert result["numFound"] == 0
    assert "not configured" in result["error"].lower()


@patch("requests.get")
def test_search_wos_returns_results(mock_get, client):
    """
    GIVEN a valid key and a mocked Starter API response
    WHEN  POST /search is called with the 'wos' database
    THEN  HTTP 200 and normalised Web of Science docs are returned
    """
    import app as rsp_app

    mock_get.return_value = _wos_response(3)

    with patch.object(rsp_app, "get_wos_api_key", return_value="TESTKEY"), \
         patch.object(rsp_app, "_wos_throttle"):
        resp = client.post(
            "/search",
            data=json.dumps({"keywords": "machine learning", "databases": ["wos"], "rows": 10}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["docs"]) == 3
    assert all(d["source"] == "Web of Science" for d in data["docs"])


@patch("requests.get")
def test_search_wos_reports_rate_limit(mock_get):
    """A 429 must surface the daily-quota explanation rather than a bare status."""
    import app as rsp_app

    limited = MagicMock()
    limited.status_code = 429
    mock_get.return_value = limited

    with patch.object(rsp_app, "get_wos_api_key", return_value="TESTKEY"), \
         patch.object(rsp_app, "_wos_throttle"):
        result = rsp_app.search_wos("machine learning", 10)

    assert result["docs"] == []
    assert "429" in result["error"]
    assert "per day" in result["error"]


@patch("requests.get")
def test_search_wos_stops_paging_at_requested_rows(mock_get):
    """WoS must not keep burning daily quota once enough records are collected."""
    import app as rsp_app

    mock_get.return_value = _wos_response(50, total=10000)

    with patch.object(rsp_app, "get_wos_api_key", return_value="TESTKEY"), \
         patch.object(rsp_app, "_wos_throttle"):
        result = rsp_app.search_wos("machine learning", 50)

    assert len(result["docs"]) == 50
    assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Web of Science Expanded edition
# ---------------------------------------------------------------------------

def _wos_expanded_rec(i, collapse=False):
    """One Expanded API record. WoS collapses single-element arrays to objects."""
    names = {"role": "author", "display_name": f"Author {i}"}
    identifier = {"type": "doi", "value": f"10.1234/wosx{i}"}
    return {
        "UID": f"WOS:00000000000000{i}",
        "static_data": {
            "summary": {
                "titles": {"title": [
                    {"type": "source", "content": "WoS Expanded Journal"},
                    {"type": "item", "content": f"Expanded Paper {i}"},
                ]},
                "pub_info": {"pubyear": 2020 + i},
                "names": {"name": names if collapse else [names]},
                "doctypes": {"doctype": "Article" if collapse else ["Article"]},
            },
            "fullrecord_metadata": {
                "abstracts": {"abstract": {
                    "abstract_text": {"p": "Full abstract text." if collapse
                                      else ["First half.", "Second half."]}
                }},
                "keywords": {"keyword": ["citation analysis"]},
                "languages": {"language": {"type": "primary", "content": "English"}},
            },
        },
        "dynamic_data": {
            "cluster_related": {"identifiers": {"identifier": identifier if collapse else [identifier]}}
        },
    }


def _wos_expanded_response(n=2):
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "QueryResult": {"RecordsFound": n, "RecordsSearched": 100},
        "Data": {"Records": {"records": {"REC": [_wos_expanded_rec(i) for i in range(n)]}}},
    }
    mock.raise_for_status = MagicMock()
    return mock


def test_normalize_wos_expanded_doc_includes_abstract():
    """The Expanded record shape must yield a real abstract."""
    import app as rsp_app

    doc = rsp_app._normalize_wos_expanded_doc(_wos_expanded_rec(1))

    assert doc["source"] == "Web of Science"
    assert doc["title"] == "Expanded Paper 1"
    assert doc["journal"] == "WoS Expanded Journal"
    assert doc["authors"] == ["Author 1"]
    assert doc["year"] == 2021
    assert doc["doi"] == "10.1234/wosx1"
    assert doc["abstract"] == "First half. Second half."
    assert doc["keywords"] == ["citation analysis"]
    assert doc["docType"] == "article"
    assert doc["language"] == "en"


def test_normalize_wos_expanded_doc_handles_collapsed_arrays():
    """Single-element arrays arrive as bare objects and must not crash parsing."""
    import app as rsp_app

    doc = rsp_app._normalize_wos_expanded_doc(_wos_expanded_rec(2, collapse=True))

    assert doc["authors"] == ["Author 2"]
    assert doc["doi"] == "10.1234/wosx2"
    assert doc["abstract"] == "Full abstract text."
    assert doc["docType"] == "article"


@patch("requests.get")
def test_search_wos_expanded_edition_uses_expanded_endpoint(mock_get, client):
    """With edition=expanded the Expanded host must be called, not Starter."""
    import app as rsp_app

    mock_get.return_value = _wos_expanded_response(2)

    with patch.object(rsp_app, "get_wos_api_key", return_value="TESTKEY"), \
         patch.object(rsp_app, "get_wos_edition", return_value="expanded"), \
         patch.object(rsp_app, "_wos_throttle"):
        result = rsp_app.search_wos("machine learning", 10)

    called_url = mock_get.call_args[0][0]
    assert called_url == rsp_app.WOS_EXPANDED_API_URL
    assert len(result["docs"]) == 2
    assert all(d["abstract"] for d in result["docs"])


@patch("requests.get")
def test_search_wos_expanded_403_explains_subscription(mock_get):
    """A free key used against Expanded must get an actionable message."""
    import app as rsp_app

    denied = MagicMock()
    denied.status_code = 403
    mock_get.return_value = denied

    with patch.object(rsp_app, "get_wos_api_key", return_value="TESTKEY"), \
         patch.object(rsp_app, "get_wos_edition", return_value="expanded"), \
         patch.object(rsp_app, "_wos_throttle"):
        result = rsp_app.search_wos("machine learning", 10)

    assert result["docs"] == []
    assert "Expanded" in result["error"]
    assert "Starter" in result["error"]


def test_wos_edition_defaults_to_starter_and_round_trips(client):
    """The edition setting must persist and default safely."""
    import app as rsp_app

    client.post("/api/wos-key", data=json.dumps({"key": "K123456789"}),
                content_type="application/json")
    assert rsp_app.get_wos_edition() == "starter"

    client.post("/api/wos-key", data=json.dumps({"edition": "expanded"}),
                content_type="application/json")
    assert rsp_app.get_wos_edition() == "expanded"
    # Omitting "key" must not wipe the stored key
    assert client.get("/api/wos-key").get_json()["configured"] is True

    client.post("/api/wos-key", data=json.dumps({"edition": "nonsense"}),
                content_type="application/json")
    assert rsp_app.get_wos_edition() == "starter"

    client.post("/api/wos-key", data=json.dumps({"key": ""}),
                content_type="application/json")


# ---------------------------------------------------------------------------
# Term filters match keywords as well as title/abstract
# ---------------------------------------------------------------------------

def _blank_prisma():
    return {"screening": {}, "eligibility": {}, "identification": {"databases": {}}}


def _doc(title="A paper", abstract="", keywords=None, doi="10.1/x"):
    return {
        "source": "Web of Science", "title": title, "authors": ["A"], "year": 2021,
        "journal": "J", "doi": doi, "url": "", "abstract": abstract,
        "keywords": keywords or [], "docType": "article", "language": "en",
    }


def test_include_terms_match_author_keywords():
    """An abstract-less record must still be matchable via its keywords."""
    import app as rsp_app

    doc = _doc(title="Deep learning study", keywords=["anomaly detection"])
    kept = rsp_app._apply_post_filters(
        [doc], {"includeTerms": ["anomaly"], "includeTermsMode": "all"}, _blank_prisma()
    )
    assert len(kept) == 1


def test_exclude_terms_match_author_keywords():
    """Exclude terms must also see keywords, or unwanted records slip through."""
    import app as rsp_app

    doc = _doc(title="Deep learning study", keywords=["cryptography"])
    prisma = _blank_prisma()
    kept = rsp_app._apply_post_filters(
        [doc], {"excludeTerms": ["cryptography"], "excludeTermsMode": "any"}, prisma
    )
    assert kept == []
    assert prisma["eligibility"]["exclude_terms"] == 1


def test_term_haystack_tolerates_string_keywords():
    """Some connectors return keywords as a bare string rather than a list."""
    import app as rsp_app

    text = rsp_app._term_haystack(_doc(title="T", keywords="single keyword"))
    assert "single keyword" in text


def test_term_matching_still_respects_word_boundaries():
    """Widening the haystack must not turn terms into substring matches."""
    import app as rsp_app

    doc = _doc(title="Artificial intelligence", keywords=["artistic style"])
    kept = rsp_app._apply_post_filters(
        [doc], {"includeTerms": ["art"], "includeTermsMode": "all"}, _blank_prisma()
    )
    assert kept == []


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
