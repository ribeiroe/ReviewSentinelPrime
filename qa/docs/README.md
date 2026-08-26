# Review Sentinel Prime – QA Automation Suite

## Overview
This folder contains the automated test suite for **Review Sentinel Prime (RSP)**, a locally-run Flask web app for systematic literature reviews.

The suite covers:
- Page-route smoke tests (all 11 HTML pages)
- REST API key CRUD (8 endpoints)
- Saved-search CRUD lifecycle
- POST /search with mocked external APIs + dedup/filter logic
- ZIP export correctness
- Helper function unit tests
- Negative / edge-case / security tests

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python      | ≥ 3.12  |
| pip         | latest  |

The application's **`venv/`** virtualenv must already be created in the repo root (it is created by the application setup).

---

## Installation

```powershell
# From the repo root
.\venv\Scripts\Activate.ps1

pip install pytest pytest-html
```

---

## Running the Tests

### Option A – PowerShell runner (recommended)

```powershell
# Run from the repo root
.\qa\run_tests.ps1
```

This script:
1. Activates the venv
2. Installs `pytest` and `pytest-html`
3. Runs the full suite
4. Writes timestamped HTML + JUnit XML reports to `results/reports/`

### Option B – Run pytest directly

```powershell
# From inside the qa/ folder
cd C:\...\ReviewSentinel\qa
pytest --html=results/reports/report.html --self-contained-html --junit-xml=results/reports/junit.xml -v
```

---

## Project Structure

```
qa/
├── tests/
│   ├── conftest.py              # Shared pytest fixtures
│   ├── test_routes.py           # Page-route smoke tests
│   ├── test_api_keys.py         # API key CRUD tests
│   ├── test_saved_searches.py   # Saved-search CRUD tests
│   ├── test_search_endpoint.py  # POST /search tests (mocked APIs)
│   ├── test_export.py           # ZIP export tests
│   ├── test_helpers.py          # build_query / build_arxiv_query unit tests
│   └── test_edge_cases.py       # Negative / boundary / security tests
├── test_data/
│   └── fixtures.py              # Reusable test data constants
├── results/
│   ├── raw/                     # Raw output artefacts (screenshots, etc.)
│   └── reports/                 # HTML + JUnit XML reports
├── bugs/
│   └── BUG_TEMPLATE.md          # Bug report template
├── docs/
│   └── README.md                # This file
├── pytest.ini                   # pytest configuration
└── run_tests.ps1                # Windows PowerShell runner
```

---

## Reports

After a test run two report files are generated inside `results/reports/`:

| File | Format | Purpose |
|------|--------|---------|
| `report_YYYYMMDD_HHmmss.html` | HTML | Human-readable, drill-down per test |
| `junit_YYYYMMDD_HHmmss.xml` | JUnit XML | CI/CD pipeline integration |

Open the HTML report in any browser:
```powershell
Start-Process results\reports\report_*.html
```

---

## Adding New Tests

1. Create `tests/test_<feature>.py`
2. Import `client` fixture from `conftest.py` (auto-discovered by pytest)
3. Follow the Given/When/Then docstring convention used in existing tests
4. Register any new markers in `pytest.ini`

---

## Bug Reporting

Copy `bugs/BUG_TEMPLATE.md`, rename to `bugs/BUG-NNN-short-title.md`, and fill in all sections including root cause analysis and fix verification.

---

## Design Decisions

- **No real API calls**: All external HTTP calls are intercepted with `unittest.mock.patch("requests.get", ...)` — tests are hermetic and fast.
- **Isolated data dir**: The `client` fixture redirects all file I/O to a `tmp_path` directory, so tests never mutate the real `data/` folder.
- **Session-scoped client**: The Flask test client is session-scoped (created once per pytest run) for speed; the temp directory is also session-scoped.
- **Given/When/Then docstrings**: Every test function has a three-part docstring for readability and traceability.
