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
