"""
test_connector_coverage.py – Connector paging, retries, truncation and the
per-database coverage log.

Every external call is served by an in-process fake API (no network). Each
fake understands the connector's real paging parameters, so the same server
drives multi-page runs, and individual calls can be replaced with faults
(429, timeout, empty page, 400) to check that nothing fails silently.
"""
import io
import json
import os
import sys
import zipfile
from unittest.mock import patch

import pytest
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import app as rsp_app  # noqa: E402


# ---------------------------------------------------------------------------
# Fake HTTP plumbing
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status=200, json_data=None, text=None, headers=None):
        self.status_code = status
        self._json = json_data
        if text is None:
            text = json.dumps(json_data) if json_data is not None else ""
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeServer:
    """Serves pages from `total` synthetic records; `faults` maps a 0-based
    call index to a FakeResp/exception returned instead of the normal page.
    `empty_from` makes every call from that index on return an empty page."""

    def __init__(self, page_fn, total, faults=None, empty_from=None):
        self.page_fn = page_fn
        self.total = total
        self.faults = faults or {}
        self.empty_from = empty_from
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        idx = len(self.calls)
        self.calls.append(dict(params or {}))
        fault = self.faults.get(idx)
        if isinstance(fault, BaseException) or (isinstance(fault, type) and issubclass(fault, BaseException)):
            raise fault if isinstance(fault, BaseException) else fault()
        if fault is not None:
            return fault
        empty = self.empty_from is not None and idx >= self.empty_from
        return self.page_fn(dict(params or {}), self.total, empty)


def _slice(total, offset, size, empty):
    if empty:
        return []
    return list(range(offset, min(total, offset + size)))


# ── Per-connector fake APIs ────────────────────────────────────────────────

def ieee_page(params, total, empty):
    ids = _slice(total, params["start_record"] - 1, params["max_records"], empty)
    return FakeResp(json_data={"total_records": total, "articles": [
        {"title": f"IEEE {i}", "doi": f"10.1/ieee{i}", "publication_year": 2023} for i in ids]})


def scopus_page(params, total, empty):
    offset = 0 if params.get("cursor", "*") == "*" else int(params.get("cursor") or params.get("start") or 0)
    if "start" in params:
        offset = params["start"]
    ids = _slice(total, offset, params["count"], empty)
    return FakeResp(json_data={"search-results": {
        "opensearch:totalResults": str(total),
        "cursor": {"@next": str(offset + len(ids))},
        "entry": [{"dc:title": f"Scopus {i}", "prism:doi": f"10.1/sc{i}",
                   "prism:coverDate": "2023-01-01"} for i in ids]}})


def springer_page(params, total, empty):
    ids = _slice(total, params["s"] - 1, params["p"], empty)
    return FakeResp(json_data={"result": [{"total": str(total)}], "records": [
        {"title": f"Springer {i}", "doi": f"10.1/sp{i}", "publicationDate": "2023-01-01"} for i in ids]})


def arxiv_page(params, total, empty):
    ids = _slice(total, params["start"], params["max_results"], empty)
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/2401.{i:05d}</id><title>arXiv {i}</title>"
        f"<summary>abstract {i}</summary><published>2023-01-01T00:00:00Z</published></entry>"
        for i in ids)
    xml = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
           'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
           'xmlns:arxiv="http://arxiv.org/schemas/atom">'
           f"<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>")
    return FakeResp(text=xml)


def openalex_page(params, total, empty):
    per = params["per_page"]
    if "cursor" in params:
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
    else:
        offset = (params["page"] - 1) * per
    ids = _slice(total, offset, per, empty)
    meta = {"count": total}
    if "cursor" in params:
        meta["next_cursor"] = str(offset + len(ids)) if ids else None
    return FakeResp(json_data={"meta": meta, "results": [
        {"id": f"https://openalex.org/W{i}", "title": f"OpenAlex {i}", "doi": f"https://doi.org/10.1/oa{i}",
         "publication_year": 2023, "type": "article"} for i in ids]},
        headers={"X-RateLimit-Remaining": "0.9", "X-RateLimit-Limit": "1"})


CONNECTORS = {
    #          search fn                 fake page     page size              cap patch
    "ieee":     (rsp_app.search_ieee,     ieee_page,     rsp_app.IEEE_MAX_PAGE),
    "scopus":   (rsp_app.search_scopus,   scopus_page,   rsp_app.SCOPUS_MAX_PAGE_COMPLETE),
    "springer": (rsp_app.search_springer, springer_page, rsp_app.SPRINGER_MAX_PAGE),
    "arxiv":    (rsp_app.search_arxiv,    arxiv_page,    rsp_app.ARXIV_PAGE_SIZE),
    "openalex": (rsp_app.search_openalex, openalex_page, rsp_app.OPENALEX_MAX_PAGE),
}

INVALID_QUERY = {
    "ieee": FakeResp(400, {"error": "Invalid query syntax"}),
    "scopus": FakeResp(400, {"service-error": {"status": {"statusCode": "INVALID_INPUT",
                                                         "statusText": "Error translating query"}}}),
    "springer": FakeResp(400, {"message": "Invalid query"}),
    "arxiv": FakeResp(400, text=(
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        "<opensearch:totalResults>1</opensearch:totalResults>"
        "<entry><id>http://arxiv.org/api/errors#incorrect_query</id><title>Error</title>"
        "<summary>malformed query</summary></entry></feed>")),
    "openalex": FakeResp(400, {"error": "Invalid query parameters error.", "message": "bad search"}),
}


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    monkeypatch.setattr(rsp_app, "get_ieee_api_key", lambda: "IEEEKEY")
    monkeypatch.setattr(rsp_app, "get_scopus_api_key", lambda: "SCOPUSKEY")
    monkeypatch.setattr(rsp_app, "get_springer_api_key", lambda: "SPRINGERKEY")
    monkeypatch.setattr(rsp_app, "get_elsevier_insttoken", lambda: "")
    monkeypatch.setattr(rsp_app, "get_openalex_api_key", lambda: "")


def _run(db, server, rows=10000):
    fn = CONNECTORS[db][0]
    with patch.object(rsp_app.requests, "get", side_effect=server):
        result = fn('"large language model" AND ("DO-178C" OR "ISO 26262")', rows, {})
    return result, rsp_app._db_status_entry(db, result)


ALL_DBS = list(CONNECTORS)


# ---------------------------------------------------------------------------
# Per-connector behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("db", ALL_DBS)
def test_multi_page_results_are_complete(db):
    page = CONNECTORS[db][2]
    total = page * 2 + 3  # forces three pages
    server = FakeServer(CONNECTORS[db][1], total)
    result, entry = _run(db, server)
    assert result["numFound"] == total
    assert len(result["docs"]) == total
    assert entry["status"] == "complete"
    assert result["log"]["pages"] == 3
    assert result["log"]["querySent"]


@pytest.mark.parametrize("db", ALL_DBS)
def test_429_is_retried_honouring_retry_after(db, no_real_sleeps):
    total = CONNECTORS[db][2] + 5
    server = FakeServer(CONNECTORS[db][1], total,
                        faults={1: FakeResp(429, {"error": "slow down"}, headers={"Retry-After": "7"})})
    result, entry = _run(db, server)
    assert len(result["docs"]) == total
    assert entry["status"] == "complete"
    assert result["log"]["retries"] == 1
    assert 7 in no_real_sleeps


@pytest.mark.parametrize("db", ALL_DBS)
def test_persistent_timeout_fails_visibly(db):
    faults = {i: requests.exceptions.Timeout() for i in range(10)}
    server = FakeServer(CONNECTORS[db][1], 50, faults=faults)
    result, entry = _run(db, server)
    assert result["docs"] == []
    assert entry["status"] == "failed"
    assert "did not respond" in entry["message"]
    assert result["log"]["retries"] >= 1
    assert result["log"]["errors"]


@pytest.mark.parametrize("db", ALL_DBS)
def test_timeout_mid_paging_keeps_partial_results(db):
    page = CONNECTORS[db][2]
    faults = {i: requests.exceptions.Timeout() for i in range(1, 12)}
    server = FakeServer(CONNECTORS[db][1], page * 3, faults=faults)
    result, entry = _run(db, server)
    assert len(result["docs"]) == page
    assert entry["status"] == "partial"
    assert entry["error"]


@pytest.mark.parametrize("db", ALL_DBS)
def test_empty_page_in_the_middle_is_reported(db):
    page = CONNECTORS[db][2]
    server = FakeServer(CONNECTORS[db][1], page * 3, empty_from=1)
    result, entry = _run(db, server)
    assert len(result["docs"]) == page
    assert entry["status"] == "partial"
    assert "empty page" in entry["message"]


def test_arxiv_recovers_from_a_transient_empty_page():
    page = rsp_app.ARXIV_PAGE_SIZE
    calls = {"n": 0}

    def flaky(params, total, empty):
        calls["n"] += 1
        return arxiv_page(params, total, calls["n"] == 2)  # only the 2nd call is empty

    server = FakeServer(flaky, page * 2)
    result, entry = _run("arxiv", server)
    assert len(result["docs"]) == page * 2
    assert entry["status"] == "complete"
    assert result["log"]["retries"] == 1


@pytest.mark.parametrize("db,cap_attr", [
    ("ieee", "RSP_RETRIEVAL_CAP"), ("scopus", "RSP_RETRIEVAL_CAP"),
    ("springer", "RSP_RETRIEVAL_CAP"), ("arxiv", "ARXIV_MAX_TOTAL"),
    ("openalex", "OPENALEX_BASIC_PAGING_LIMIT"),
])
def test_total_larger_than_retrievable_cap_is_flagged(db, cap_attr, monkeypatch):
    page = CONNECTORS[db][2]
    cap = page * 2
    if cap_attr == "RSP_RETRIEVAL_CAP":
        monkeypatch.setitem(rsp_app.RSP_RETRIEVAL_CAP, db, cap)
    else:
        monkeypatch.setattr(rsp_app, cap_attr, cap)
    server = FakeServer(CONNECTORS[db][1], cap * 10)
    result, entry = _run(db, server)
    assert len(result["docs"]) == cap
    assert result["numFound"] == cap * 10
    assert entry["status"] == "partial"
    assert result["log"]["truncated"]
    assert f"{cap:,}" in result["log"]["truncated"]


@pytest.mark.parametrize("db", ALL_DBS)
def test_invalid_query_error_is_reported(db):
    faults = {i: INVALID_QUERY[db] for i in range(5)}
    server = FakeServer(CONNECTORS[db][1], 10, faults=faults)
    result, entry = _run(db, server)
    assert result["docs"] == []
    assert entry["status"] == "failed"
    assert "400" in entry["message"]


@pytest.mark.parametrize("db", ALL_DBS)
def test_page_size_never_exceeds_documented_maximum(db):
    server = FakeServer(CONNECTORS[db][1], 5000)
    _run(db, server, rows=10000)
    size_param = {"ieee": "max_records", "scopus": "count", "springer": "p",
                  "arxiv": "max_results", "openalex": "per_page"}[db]
    assert max(c[size_param] for c in server.calls) <= CONNECTORS[db][2]


def test_api_keys_are_not_written_to_the_log():
    server = FakeServer(springer_page, 3)
    result, _ = _run("springer", server)
    assert "SPRINGERKEY" not in json.dumps(result["log"])


def test_openalex_quota_exhaustion_fails_fast_with_reset_time(no_real_sleeps):
    quota = FakeResp(429, {"error": "Rate limit exceeded"},
                     headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "7200"})
    server = FakeServer(openalex_page, 500, faults={0: quota})
    result, entry = _run("openalex", server)
    assert entry["status"] == "failed"
    assert "quota exhausted" in entry["message"]
    assert "2h" in entry["message"]
    assert "API key" in entry["message"]
    assert len(server.calls) == 1  # no pointless retries
    assert no_real_sleeps == [] or max(no_real_sleeps) < 2


def test_openalex_uses_cursor_paging_with_an_api_key(monkeypatch):
    monkeypatch.setattr(rsp_app, "get_openalex_api_key", lambda: "OAKEY")
    server = FakeServer(openalex_page, 250)
    result, entry = _run("openalex", server)
    assert len(result["docs"]) == 250 and entry["status"] == "complete"
    assert all("cursor" in c and "page" not in c for c in server.calls)
    assert all(c["api_key"] == "OAKEY" for c in server.calls)
    assert "OAKEY" not in json.dumps(result["log"])


def test_complex_openalex_query_is_throttled_to_one_request_per_second(monkeypatch):
    seen = []
    monkeypatch.setattr(rsp_app, "_throttle_host", lambda host, interval: seen.append(interval))
    server = FakeServer(openalex_page, 10)
    with patch.object(rsp_app.requests, "get", side_effect=server):
        rsp_app.search_openalex('"a" OR "b" OR "c" OR "d" OR "e" OR "f" OR "g"', 100, {})
    assert seen and min(seen) >= 1.0


def test_scopus_quota_header_stops_retries():
    quota = FakeResp(429, {}, headers={"X-ELS-Status": "QUOTA_EXCEEDED - Quota Exceeded",
                                       "X-RateLimit-Reset": "9999999999"})
    server = FakeServer(scopus_page, 100, faults={0: quota})
    result, entry = _run("scopus", server)
    assert entry["status"] == "failed" and "quota exhausted" in entry["message"]
    assert len(server.calls) == 1


def test_dns_failure_is_retried_then_explained():
    err = requests.exceptions.ConnectionError(
        "Failed to resolve 'api.elsevier.com' ([Errno 8] nodename nor servname provided)")
    server = FakeServer(scopus_page, 10, faults={i: err for i in range(10)})
    result, entry = _run("scopus", server)
    assert entry["status"] == "failed"
    assert "DNS lookup failed" in entry["message"]
    assert result["log"]["retries"] == 4


def test_retry_after_http_date_is_parsed():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    assert 25 <= rsp_app._parse_retry_after(when) <= 31
    assert rsp_app._parse_retry_after("12") == 12
    assert rsp_app._parse_retry_after("garbage") is None


def test_reported_matches_without_records_is_never_complete():
    entry = rsp_app._db_status_entry("openalex", {"numFound": 637, "docs": []})
    assert entry["status"] == "failed"
    assert "637" in entry["message"]


# ---------------------------------------------------------------------------
# Run level: visible status and exported log
# ---------------------------------------------------------------------------

def _mixed_server(url, params=None, headers=None, timeout=None):
    """IEEE answers normally; OpenAlex has exhausted its daily budget."""
    if "openalex" in url:
        return FakeResp(429, {"error": "Rate limit exceeded"},
                        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3600"})
    return ieee_page(dict(params or {}), 3, False)


def test_failed_connector_is_visible_in_run_and_exported_log(client):
    with patch.object(rsp_app.requests, "get", side_effect=_mixed_server):
        resp = client.post("/search", data=json.dumps({
            "keywords": "llm", "databases": ["ieee", "openalex"], "rows": 100, "filters": {}}),
            content_type="application/json")
    data = resp.get_json()
    dbs = data["prisma"]["identification"]["databases"]
    assert dbs["ieee"]["status"] == "complete"
    assert dbs["openalex"]["status"] == "failed"
    assert dbs["openalex"]["retrieved"] == 0
    assert "quota exhausted" in dbs["openalex"]["message"]

    zresp = client.get(f"/api/results/download-zip/adhoc/{data['runId']}")
    zf = zipfile.ZipFile(io.BytesIO(zresp.data))
    log_rows = json.loads(zf.read("search_log.json"))
    by_db = {r["database_id"]: r for r in log_rows}
    assert by_db["openalex"]["status"] == "failed"
    assert by_db["openalex"]["errors"]
    assert by_db["ieee"]["retrieved"] == 3 and by_db["ieee"]["pages"] == 1
    assert by_db["ieee"]["query_sent"] == "llm"
    csv_text = zf.read("search_log.csv").decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("database,database_id,timestamp,status,query_sent,total_reported,retrieved")


def test_stream_progress_events_carry_status(client):
    with patch.object(rsp_app.requests, "get", side_effect=_mixed_server):
        resp = client.post("/search/stream", data=json.dumps({
            "keywords": "llm", "databases": ["ieee", "openalex"], "rows": 100, "filters": {}}),
            content_type="application/json")
        body = resp.get_data(as_text=True)
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    progress = {e["db"]: e for e in events if e["type"] == "progress"}
    assert progress["openalex"]["status"] == "failed" and progress["openalex"]["error"]
    assert progress["ieee"]["status"] == "complete"
    done = [e for e in events if e["type"] == "done"][0]
    assert done["prisma"]["identification"]["databases"]["openalex"]["status"] == "failed"


def test_unknown_database_is_reported_not_skipped(client):
    resp = client.post("/search", data=json.dumps({
        "keywords": "llm", "databases": ["notadb"], "rows": 10, "filters": {}}),
        content_type="application/json")
    dbs = resp.get_json()["prisma"]["identification"]["databases"]
    assert dbs["notadb"]["status"] == "failed"


def test_per_database_filter_removals_are_logged(client):
    def server(url, params=None, headers=None, timeout=None):
        return FakeResp(json_data={"total_records": 3, "articles": [
            {"title": "Keep me", "doi": "10.1/a", "publication_year": 2023},
            {"title": "Too old", "doi": "10.1/b", "publication_year": 2010},
            {"title": "A survey of things", "doi": "10.1/c", "publication_year": 2024},
        ]})
    with patch.object(rsp_app.requests, "get", side_effect=server):
        resp = client.post("/search", data=json.dumps({
            "keywords": "llm", "databases": ["ieee"], "rows": 10,
            "filters": {"yearFrom": 2022, "excludeTerms": ["survey"], "excludeTermsMode": "any"}}),
            content_type="application/json")
    ieee = resp.get_json()["prisma"]["identification"]["databases"]["ieee"]
    assert ieee["removed"]["year_range"] == 1
    assert ieee["removed"]["exclude_terms"] == 1
    assert ieee["included"] == 1


def test_duplicate_attribution_does_not_depend_on_completion_order():
    a = {"source": "OpenAlex", "title": "Same Paper", "doi": "10.1/x", "year": 2023}
    b = {"source": "IEEE", "title": "Same Paper", "doi": "10.1/x", "year": 2023}
    for order in (("openalex", "ieee"), ("ieee", "openalex")):
        results = {db: {"docs": [dict(a) if db == "openalex" else dict(b)]} for db in order}
        docs, origin = rsp_app._assemble_docs(results)
        prisma = {"screening": {}, "eligibility": {}}
        kept = rsp_app._apply_post_filters(docs, {}, prisma, origin=origin)
        assert [d["source"] for d in kept] == ["IEEE"]
        assert prisma["removed_by_db"]["duplicates"] == {"openalex": 1}


# ---------------------------------------------------------------------------
# Exclude terms: any / all modes, phrases, fields
# ---------------------------------------------------------------------------

def _doc(title="", abstract="", keywords=None, journal=""):
    return {"title": title, "abstract": abstract, "keywords": keywords or [], "journal": journal,
            "year": 2023, "doi": "", "source": "IEEE", "docType": "article"}


def _exclude(docs, terms, mode):
    prisma = {"screening": {}, "eligibility": {}}
    return rsp_app._apply_post_filters(docs, {"excludeTerms": terms, "excludeTermsMode": mode}, prisma)


def test_exclude_any_removes_when_one_term_matches():
    docs = [_doc("A safety case for LLMs"), _doc("Unrelated paper")]
    kept = _exclude(docs, ['"safety case"', '"medical device"'], "any")
    assert [d["title"] for d in kept] == ["Unrelated paper"]


def test_exclude_all_removes_only_when_every_term_matches():
    both = _doc("A safety case", abstract="for a medical device")
    one = _doc("A safety case only")
    kept = _exclude([both, one], ['"safety case"', '"medical device"'], "all")
    assert [d["title"] for d in kept] == ["A safety case only"]


def test_quoted_phrase_matches_as_phrase_not_as_separate_words():
    words_apart = _doc("Safety requirements for the case study")
    phrase = _doc("Building a safety case")
    kept = _exclude([words_apart, phrase], ['"safety case"'], "any")
    assert kept == [words_apart]


def test_phrase_matches_across_line_breaks_in_abstracts():
    doc = _doc("T", abstract="We build a safety\n  case for avionics")
    assert _exclude([doc], ['"safety case"'], "any") == []


def test_exclude_terms_match_title_abstract_and_keywords_but_not_venue():
    in_kw = _doc("T1", keywords=["systematic review"])
    in_abs = _doc("T2", abstract="this systematic review covers")
    in_venue = _doc("T3", journal="Systematic Review Letters")
    kept = _exclude([in_kw, in_abs, in_venue], ['"systematic review"'], "any")
    assert [d["title"] for d in kept] == ["T3"]


def test_newline_separated_terms_are_split():
    assert rsp_app._split_filter_terms(['"safety case"\n"medical device"']) == ['"safety case"', '"medical device"']
    assert rsp_app._split_filter_terms(['"safety, critical", review']) == ['"safety, critical"', "review"]
    doc = _doc("A medical device study")
    assert _exclude([doc], ['"safety case"\n"medical device"'], "any") == []


def test_api_side_exclusion_respects_all_mode_and_quotes_phrases(monkeypatch):
    monkeypatch.setenv("RSP_ENABLE_API_FILTERS", "1")
    any_mode = rsp_app._api_filters({"excludeTerms": ["safety case", "review"], "excludeTermsMode": "any"})
    assert any_mode["excludeTerms"] == ['"safety case"', "review"]
    all_mode = rsp_app._api_filters({"excludeTerms": ["safety case", "review"], "excludeTermsMode": "all"})
    assert all_mode["excludeTerms"] == []


# ---------------------------------------------------------------------------
# Year range at the database (Settings → Retrieval)
# ---------------------------------------------------------------------------

def test_springer_sends_documented_date_constraints():
    server = FakeServer(springer_page, 3)
    with patch.object(rsp_app.requests, "get", side_effect=server):
        result = rsp_app.search_springer('"llm" OR "gpt"', 100, {"yearFrom": 2022})
    q = server.calls[0]["q"]
    assert q.startswith('("llm" OR "gpt") datefrom:2022-01-01 dateto:')
    assert not any(k.startswith("date-facet") for k in server.calls[0])
    assert "datefrom:2022-01-01" in result["log"]["querySent"]


def _ieee_by_year(all_years, in_range, honour=True):
    """Fake IEEE: all_years records exist; in_range of them are 2022+.
    honour=False simulates an API that ignores start_year."""
    def handler(url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        limited = honour and "start_year" in params
        total = in_range if limited else all_years
        offset, size = params["start_record"] - 1, params["max_records"]
        ids = range(offset, min(total, offset + size))
        year = (lambda i: 2023) if limited else (lambda i: 2023 if i < in_range else 2010)
        return FakeResp(json_data={"total_records": total, "articles": [
            {"title": f"IEEE {i}", "doi": f"10.1/ieee{i}", "publication_year": year(i)} for i in ids]})
    calls = []

    def spy(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return handler(url, params, headers, timeout)
    return spy, calls


def _search(client, filters):
    resp = client.post("/search", data=json.dumps({
        "keywords": "llm", "databases": ["ieee"], "rows": 10000, "filters": filters}),
        content_type="application/json")
    return resp.get_json()


def test_year_at_database_counts_all_years_and_downloads_only_in_range(client):
    client.post("/api/retrieval-settings", data=json.dumps({"yearAtDatabase": True}),
                content_type="application/json")
    spy, calls = _ieee_by_year(all_years=50, in_range=20)
    with patch.object(rsp_app.requests, "get", side_effect=spy):
        data = _search(client, {"yearFrom": 2022})
    ident = data["prisma"]["identification"]
    ieee = ident["databases"]["ieee"]
    assert ieee["found"] == 50 and ieee["foundInRange"] == 20
    assert ieee["removedDateAtDatabase"] == 30
    assert ieee["retrieved"] == 20 and ieee["status"] == "complete"
    assert ident["total_found"] == 50
    assert ident["removed_date_at_database"] == 30 and ident["not_retrieved"] == 0
    assert ident["date_limit_at_database"]["yearFrom"] == 2022
    count_calls = [c for c in calls if "start_year" not in c]
    assert len(count_calls) == 1 and count_calls[0]["max_records"] == 1
    assert all(c["start_year"] == 2022 for c in calls if "start_year" in c)
    assert "dateLimitWarning" not in ieee

    zresp = client.get(f"/api/results/download-zip/adhoc/{data['runId']}")
    row = json.loads(zipfile.ZipFile(io.BytesIO(zresp.data)).read("search_log.json"))[0]
    assert row["date_limit_at_database"] == "yes"
    assert row["total_reported"] == 50 and row["reported_in_date_range"] == 20
    assert row["removed_date_at_database"] == 30 and row["not_retrieved"] == 0


def test_without_the_setting_no_count_call_is_made(client):
    spy, calls = _ieee_by_year(all_years=50, in_range=20)
    with patch.object(rsp_app.requests, "get", side_effect=spy):
        data = _search(client, {"yearFrom": 2022})
    ident = data["prisma"]["identification"]
    assert all("start_year" not in c for c in calls) and len(calls) == 1
    assert ident["databases"]["ieee"]["found"] == 50
    assert ident["removed_date_at_database"] == 0
    assert data["prisma"]["screening"]["year_range"] == 30  # removed locally, with a list


def test_cap_shortfall_is_reported_as_not_retrieved(client, monkeypatch):
    client.post("/api/retrieval-settings", data=json.dumps({"yearAtDatabase": True, "caps": {"ieee": 100}}),
                content_type="application/json")
    spy, _ = _ieee_by_year(all_years=900, in_range=300)
    with patch.object(rsp_app.requests, "get", side_effect=spy):
        data = _search(client, {"yearFrom": 2022})
    ident = data["prisma"]["identification"]
    ieee = ident["databases"]["ieee"]
    assert ieee["retrieved"] == 100 and ieee["status"] == "partial"
    assert ident["removed_date_at_database"] == 600
    assert ident["not_retrieved"] == 200
    assert ident["total_found"] - 600 - 200 == ident["retrieved"]


def test_failed_count_call_is_noted_not_hidden(client):
    client.post("/api/retrieval-settings", data=json.dumps({"yearAtDatabase": True}),
                content_type="application/json")
    spy, _ = _ieee_by_year(all_years=50, in_range=20)

    def handler(url, params=None, headers=None, timeout=None):
        if "start_year" not in (params or {}):
            return FakeResp(400, {"error": "boom"})
        return spy(url, params, headers, timeout)
    with patch.object(rsp_app.requests, "get", side_effect=handler):
        data = _search(client, {"yearFrom": 2022})
    ieee = data["prisma"]["identification"]["databases"]["ieee"]
    assert ieee["removedDateAtDatabase"] is None
    assert ieee["found"] == 20 and ieee["retrieved"] == 20
    assert "All-years count unavailable" in ieee["dateLimitNote"]


def test_database_ignoring_the_date_limit_is_flagged(client):
    client.post("/api/retrieval-settings", data=json.dumps({"yearAtDatabase": True}),
                content_type="application/json")
    spy, _ = _ieee_by_year(all_years=50, in_range=20, honour=False)
    with patch.object(rsp_app.requests, "get", side_effect=spy):
        data = _search(client, {"yearFrom": 2022})
    ieee = data["prisma"]["identification"]["databases"]["ieee"]
    assert "may have ignored the date limit" in ieee["dateLimitWarning"]
    assert "may have ignored the date limit" in ieee["message"]


def test_retrieval_settings_round_trip_and_clamp(client):
    resp = client.post("/api/retrieval-settings", data=json.dumps(
        {"yearAtDatabase": True, "caps": {"ieee": 999999, "scopus": 5, "springer": 2000,
                                          "pubmed": 5000, "hal": 500}}),
        content_type="application/json")
    saved = resp.get_json()["settings"]
    assert set(saved["caps"]) == set(rsp_app._connector_table())
    assert {db: saved["caps"][db] for db in ("ieee", "scopus", "springer", "pubmed", "hal")} == {
        "ieee": 10000, "scopus": 100, "springer": 2000, "pubmed": 5000, "hal": 500}
    got = client.get("/api/retrieval-settings").get_json()["settings"]
    assert got["yearAtDatabase"] is True and got["caps"] == saved["caps"]
    server = FakeServer(springer_page, 5000)
    with patch.object(rsp_app.requests, "get", side_effect=server):
        result = rsp_app.search_springer("x", 10000, {})
    assert len(result["docs"]) == 2000 and "2,000" in result["log"]["truncated"]


def test_old_retrieval_settings_keep_existing_caps_and_new_defaults():
    with open(rsp_app.RETRIEVAL_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"yearAtDatabase": True, "caps": {"ieee": 5000, "scopus": 2000, "springer": 500}}, f)
    settings = rsp_app.get_retrieval_settings()
    assert settings["yearAtDatabase"] is True
    assert settings["caps"] == dict(rsp_app.RSP_RETRIEVAL_CAP, ieee=5000, scopus=2000, springer=500)


@pytest.mark.parametrize("db", ["inspec", "sciencedirect", "pubmed", "acm"])
def test_previously_fixed_caps_can_be_lowered_and_raised(db, monkeypatch):
    monkeypatch.setattr(rsp_app, "get_inspec_api_key", lambda: "key")
    monkeypatch.setattr(rsp_app, "get_sciencedirect_api_key", lambda: "key")
    monkeypatch.setattr(rsp_app, "get_pubmed_api_key", lambda: "")
    monkeypatch.setattr(rsp_app, "get_acm_api_key", lambda: "")
    total = 1100

    def get_page(url, params=None, headers=None, timeout=None):
        if db == "inspec":
            ids = _slice(total, params["offset"] - 1, params["pageSize"], False)
            return FakeResp(json_data={"PAGE": {"RESULTS-COUNT": total, "PAGE-RESULTS": {
                "PAGE-ENTRY": [{"EI-DOCUMENT": {"DOCUMENTPROPERTIES": {"TI": str(i)}}} for i in ids]}}})
        if db == "sciencedirect":
            ids = _slice(total, params["start"], params["count"], False)
            return FakeResp(json_data={"search-results": {"opensearch:totalResults": str(total),
                                                           "entry": [{"dc:title": str(i)} for i in ids]}})
        if db == "acm":
            ids = _slice(total, params["startPage"] * 50, params["pageSize"], False)
            return FakeResp(json_data={"totalHits": total, "items": [{"title": str(i)} for i in ids]})
        if url.endswith("esearch.fcgi"):
            ids = _slice(total, 0, params["retmax"], False)
            return FakeResp(json_data={"esearchresult": {"count": str(total), "idlist": [str(i) for i in ids]}})
        articles = "".join(f"<PubmedArticle><MedlineCitation><PMID>{i}</PMID>"
                           f"<Article><ArticleTitle>{i}</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
                           for i in params["id"].split(","))
        return FakeResp(text=f"<PubmedArticleSet>{articles}</PubmedArticleSet>")

    with patch.object(rsp_app.requests, "get", side_effect=get_page):
        rsp_app.save_retrieval_settings({"caps": {db: 100}})
        _, result, _ = next(rsp_app._run_connectors([db], "test", 10000, {}))
        entry = rsp_app._db_status_entry(db, result)
        assert len(result["docs"]) == 100
        assert entry["status"] == "partial" and "RSP retrieval cap" in entry["message"]

        rsp_app.save_retrieval_settings({"caps": {db: 2000}})
        _, result, _ = next(rsp_app._run_connectors([db], "test", 10000, {}))
        assert len(result["docs"]) == total
        assert rsp_app._db_status_entry(db, result)["status"] == "complete"


@pytest.mark.parametrize("db", ["hal", "arxiv", "openalex", "ebsco", "wos"])
def test_new_database_cap_limits_retrieval(db, monkeypatch):
    total = 250
    rsp_app.save_retrieval_settings({"caps": {db: 100}})
    monkeypatch.setattr(rsp_app, "get_ebsco_credentials",
                        lambda: {"userId": "u", "password": "p", "profile": "p"})
    monkeypatch.setattr(rsp_app, "_ebsco_auth_token", lambda *args, **kwargs: ("auth", None))
    monkeypatch.setattr(rsp_app, "_ebsco_create_session", lambda *args: ("session", None))
    monkeypatch.setattr(rsp_app, "_ebsco_end_session", lambda *args: None)
    monkeypatch.setattr(rsp_app, "get_wos_api_key", lambda: "key")
    monkeypatch.setattr(rsp_app, "get_wos_edition", lambda: "starter")
    monkeypatch.setattr(rsp_app, "_wos_throttle", lambda: None)

    def get_page(url, params=None, headers=None, timeout=None):
        if db == "arxiv":
            return arxiv_page(params, total, False)
        if db == "openalex":
            return openalex_page(params, total, False)
        if db == "hal":
            ids = _slice(total, params["start"], params["rows"], False)
            return FakeResp(json_data={"response": {"numFound": total,
                                                     "docs": [{"title_s": str(i)} for i in ids]}})
        if db == "ebsco":
            ids = _slice(total, (params["pagenumber"] - 1) * 100, params["resultsperpage"], False)
            return FakeResp(json_data={"SearchResult": {"Statistics": {"TotalHits": total},
                                                         "Data": {"Records": [{} for _ in ids]}}})
        ids = _slice(total, (params["page"] - 1) * 50, params["limit"], False)
        return FakeResp(json_data={"metadata": {"total": total}, "hits": [{} for _ in ids]})

    with patch.object(rsp_app.requests, "get", side_effect=get_page):
        _, result, _ = next(rsp_app._run_connectors([db], "test", 10000, {}))
    entry = rsp_app._db_status_entry(db, result)
    assert len(result["docs"]) == 100
    assert entry["status"] == "partial"
    assert "retrieval cap" in entry["message"]


def test_sciencedirect_paging_safeguard_remains_when_cap_is_raised(monkeypatch):
    rsp_app.save_retrieval_settings({"caps": {"sciencedirect": 10000}})
    monkeypatch.setattr(rsp_app, "get_sciencedirect_api_key", lambda: "key")

    def get_page(url, params=None, headers=None, timeout=None):
        assert params["start"] + params["count"] <= 5000
        ids = _slice(5100, params["start"], params["count"], False)
        return FakeResp(json_data={"search-results": {"opensearch:totalResults": "5100",
                                                       "entry": [{"dc:title": str(i)} for i in ids]}})

    with patch.object(rsp_app.requests, "get", side_effect=get_page):
        _, result, _ = next(rsp_app._run_connectors(["sciencedirect"], "test", 10000, {}))
    assert len(result["docs"]) == 5000
    assert rsp_app._db_status_entry("sciencedirect", result)["status"] == "partial"


def test_prisma_svg_shows_records_removed_before_retrieval():
    prisma = {"identification": {"total_found": 50, "retrieved": 20, "databases": {},
                                 "removed_date_at_database": 30, "not_retrieved": 0},
              "screening": {}, "eligibility": {}, "included": 20}
    svg = rsp_app._build_prisma_svg(prisma)
    assert "Removed before retrieval (n=30)" in svg and "Records retrieved: 20" in svg
    prisma["identification"]["removed_date_at_database"] = 0
    assert "Removed before retrieval" not in rsp_app._build_prisma_svg(prisma)


def test_replication_package_documents_the_date_limit_and_log(client):
    prisma = {"identification": {
        "total_found": 50, "retrieved": 20, "removed_date_at_database": 30, "not_retrieved": 0,
        "date_limit_at_database": {"yearFrom": 2022, "yearTo": None},
        "databases": {"springer": {"found": 50, "foundInRange": 20, "retrieved": 20, "status": "complete",
                                   "dateLimitAtDatabase": True, "removedDateAtDatabase": 30,
                                   "notRetrieved": 0, "message": "ok", "log": {"querySent": "x"}}}},
        "screening": {}, "eligibility": {}, "included": 20}
    resp = client.post("/api/export-replication-package", data=json.dumps({
        "docs": [], "keywords": '"llm"', "databases": ["springer"],
        "filters": {"yearFrom": 2022}, "prisma": prisma}), content_type="application/json")
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    strings = zf.read(next(n for n in names if n.endswith("search_strings_per_database.txt"))).decode()
    assert "datefrom:2022-01-01" in strings and "applied AT THE DATABASE" in strings
    log = json.loads(zf.read(next(n for n in names if n.endswith("01_search_strategy/search_log.json"))))
    assert log[0]["removed_date_at_database"] == 30
    svg = zf.read(next(n for n in names if n.endswith("prisma_flow_diagram.svg"))).decode()
    assert "Removed before retrieval (n=30)" in svg
