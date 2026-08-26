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
    # Each of the 4 required folders must appear in at least one entry
    required = ["01_search_strategy", "02_data", "03_images", "04_evidence_checklist"]
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
