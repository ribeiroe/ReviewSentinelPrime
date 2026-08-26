"""
conftest.py – Shared fixtures for the RSP test suite.

Provides:
  - `client`       Flask test client (isolated temp data dir)
  - `tmp_data_dir` path to the temporary data directory
  - `sample_search_payload` standard saved-search creation body
"""
import json
import os
import shutil
import sys
import tempfile

import pytest

# Make the project root importable from within the QA folder
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(scope="session")
def tmp_data_dir(tmp_path_factory):
    """A temporary data directory shared across the session."""
    tmp_dir = tmp_path_factory.mktemp("rsp_data")
    results_dir = tmp_dir / "results"
    results_dir.mkdir()
    return tmp_dir


@pytest.fixture(scope="session")
def client(tmp_data_dir):
    """
    Flask test client with the DATA_DIR redirected to a temp folder
    so tests never touch the real data/ directory.
    """
    # Patch constants BEFORE importing app
    import app as rsp_app

    original_data_dir    = rsp_app.DATA_DIR
    original_config_file = rsp_app.CONFIG_FILE
    original_results_dir = rsp_app.RESULTS_DIR
    original_ieee        = rsp_app.IEEE_API_KEY_FILE
    original_scopus      = rsp_app.SCOPUS_API_KEY_FILE
    original_inspec      = rsp_app.INSPEC_API_KEY_FILE
    original_sd          = rsp_app.SCIENCEDIRECT_API_KEY_FILE
    original_springer    = rsp_app.SPRINGER_API_KEY_FILE
    original_pubmed      = rsp_app.PUBMED_API_KEY_FILE
    original_github      = rsp_app.GITHUB_TOKEN_FILE

    rsp_app.DATA_DIR         = str(tmp_data_dir)
    rsp_app.CONFIG_FILE      = str(tmp_data_dir / "searches.json")
    rsp_app.RESULTS_DIR      = str(tmp_data_dir / "results")
    rsp_app.IEEE_API_KEY_FILE        = str(tmp_data_dir / "ieee_api_key.txt")
    rsp_app.SCOPUS_API_KEY_FILE      = str(tmp_data_dir / "scopus_api_key.txt")
    rsp_app.INSPEC_API_KEY_FILE      = str(tmp_data_dir / "inspec_api_key.txt")
    rsp_app.SCIENCEDIRECT_API_KEY_FILE = str(tmp_data_dir / "sciencedirect_api_key.txt")
    rsp_app.SPRINGER_API_KEY_FILE    = str(tmp_data_dir / "springer_api_key.txt")
    rsp_app.PUBMED_API_KEY_FILE      = str(tmp_data_dir / "pubmed_api_key.txt")
    rsp_app.GITHUB_TOKEN_FILE        = str(tmp_data_dir / "github_token.txt")

    rsp_app.app.config["TESTING"] = True
    with rsp_app.app.test_client() as test_client:
        yield test_client

    # Restore
    rsp_app.DATA_DIR         = original_data_dir
    rsp_app.CONFIG_FILE      = original_config_file
    rsp_app.RESULTS_DIR      = original_results_dir
    rsp_app.IEEE_API_KEY_FILE        = original_ieee
    rsp_app.SCOPUS_API_KEY_FILE      = original_scopus
    rsp_app.INSPEC_API_KEY_FILE      = original_inspec
    rsp_app.SCIENCEDIRECT_API_KEY_FILE = original_sd
    rsp_app.SPRINGER_API_KEY_FILE    = original_springer
    rsp_app.PUBMED_API_KEY_FILE      = original_pubmed
    rsp_app.GITHUB_TOKEN_FILE        = original_github


@pytest.fixture
def sample_search_payload():
    """A minimal valid saved-search creation payload."""
    return {
        "name": "Test SLR",
        "query": "machine learning",
        "databases": ["hal", "arxiv"],
        "maxResults": 10,
        "schedule": {"type": "manual"},
        "filters": {},
        "validationPapers": [],
    }


@pytest.fixture
def created_search(client, sample_search_payload):
    """Creates a saved search and returns the response JSON. Cleans up after."""
    resp = client.post(
        "/api/searches",
        data=json.dumps(sample_search_payload),
        content_type="application/json",
    )
    search = resp.get_json()
    yield search
    # Cleanup: delete after test
    if search and search.get("id"):
        client.delete(f"/api/searches/{search['id']}")
