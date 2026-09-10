# Review Sentinel Prime (RSP)

![Review Sentinel Prime logo](static/images/logo.png)

## A locally-run, browser-based tool for Systematic Literature Reviews — no cloud account required

v2.1.0 · Python 3.12 · Flask 3.1 · Docker ready

---

## What is RSP?

**Review Sentinel Prime** is a web application that runs entirely on your own computer. It lets researchers search up to **12 scientific databases simultaneously** using Boolean query syntax (`OR`, `AND`, parentheses), filter and validate results, generate PRISMA 2020 flow diagrams, and export complete SLR replication packages — all from a browser interface with no coding required.

---

## Features

| Feature | Description |
| --- | --- |
| ▸ **Multi-database search** | Search IEEE Xplore, ACM DL, Scopus, ScienceDirect, Springer, Inspec, PubMed, arXiv, HAL, OpenAlex, EBSCO, Web of Science simultaneously (12 databases) |
| ▸ **Boolean query syntax** | Full support for `OR`, `AND`, parentheses, and exact phrases — with an in-page Query tips guide |
| ▸ **Live progress** | Real-time per-database progress panel with elapsed timer |
| ▸ **Filters & criteria** | Year range, document type, language, and four color-coded filter cards (Include Terms, Exclude Terms, Include Venues, Exclude Venues) |
| ▸ **ALL / ANY match modes** | Each filter card has an inline `ALL (AND)` / `ANY (OR)` toggle — control whether all terms or any term must match |
| ▸ **Live / Offline filter mode** | Toggle between live network search and offline re-filtering against a local cache — iterate filter changes instantly without re-hitting APIs |
| ▸ **Validation papers** | Track known-relevant papers to verify search completeness |
| ▸ **Saved searches** | Save, schedule (hourly/daily/monthly), and re-run searches automatically |
| ▸ **Countdown timer** | Live countdown to the next scheduled run, anchored to `lastRun` timestamp — accurate after refreshes |
| ▸ **Email notifications** | Diff-based alerts showing new/removed papers after each scheduled run |
| ▸ **Bug reports & feature requests** | Built-in Feedback page that records submissions in the project's issue-collection forms — works even when no SMTP server is configured |
| ▸ **T&A Screening** | Manual Title & Abstract screening page — review papers one-by-one with Include / Exclude / Unsure decisions, navigate prev/next, and export decisions as CSV |
| ▸ **AI Screening Assistant** | Optional local-LLM suggestions (Ollama or any OpenAI-compatible endpoint) during T&A screening — the reviewer accepts or overrides each one, and accepted decisions carry model provenance that feeds the PRISMA item 8 automation-tool disclosure |
| ▸ **Results Hub** | Browse and download all local run snapshots (saved and ad-hoc) from a single page |
| ▸ **Charts** | Publications per year (with trend line), abstract keyword frequency (top terms bar chart), venue, and document-type charts — all with PNG export |
| ▸ **PRISMA 2020** | Auto-generated flow diagram; clickable stage cards **and** excluded/removed cards showing the actual papers removed at each stage; Snowballing / Other Sources panel; Review Protocol form (research questions, registration, reviewer team, synthesis/RoB methods); PRISMA 2020 Evidence Checklist (27 of 39 entries auto-assessed from run data + protocol, incl. auto-drafted abstract and process-limitations statements) |
| ▸ **PNG export** | DOM-capture export of the PRISMA diagram and evidence checklist at 2× resolution via html2canvas |
| ▸ **Replication package** | One-click ZIP export with CSVs, JSON config, protocol, PRISMA data, README |
| ▸ **Import / Export config** | Save and reload the complete search configuration — including filter modes — as a JSON file |
| ▸ **Cards & List view** | Toggle between rich card and compact list result layouts |
| ▸ **Persistent UI state** | Filter and validation panel visibility is remembered across refreshes and saved-search restores |
| ▸ **API key management** | Secure local storage with per-database test connection and last-checked timestamp |
| ▸ **Built-in manual** | Full 17-section user manual available at `/manual` |
| ▸ **Docker ready** | One-click Docker Compose deployment with start/stop scripts for Windows and macOS |

---

## What's New in v2.1.0

### ⚠️ Behaviour change

Include/exclude **term filters now also match author keywords**, in addition to title and abstract. Re-running an existing saved search may return a different set of papers than it did on v2.0.1 — re-run your searches before comparing PRISMA counts against earlier exports.

### New databases (10 → 12)

- **Web of Science** — Clarivate API key. Two editions share one key, selectable in Settings: *Starter* (free, self-service, **returns no abstracts**, as few as 50 requests/day) and *Expanded* (institutional subscription, full records with abstracts). Requests are throttled to stay inside the free-tier rate limit.
- **EBSCO Discovery Service** — institutional EDS subscription, authenticated with User ID + Password + Profile ID. EBSCO's licence restricts caching and redistribution of retrieved metadata.

### PRISMA 2020 evidence checklist — now largely automated

- Artefact rows are marked `[OK]` / `[--]` from the ZIP's actual contents, so the checklist no longer claims files that were not produced.
- Records excluded **with reasons** — all eight filter reasons, with a total.
- Title & abstract screening totals, including records not yet screened.
- **Identification by source** — databases/registers vs citation searching. Snowballed records were previously counted as database hits.
- **Validation (seed) paper recall** — reports which known papers the search actually retrieved, and flags a query that may be too narrow.
- **Automation disclosure** — AI-assisted decision count and model(s), as required when automation tools are used in study selection.
- **Items still needing manual attention** — registration, reviewer count, protocol items 11–15/25/26, and any database that errored or was truncated.
- Mapping extended to PRISMA items 5, 24a, 24b and 27.

### Data format (additive)

- `screening_decisions.csv` gains `ai_assisted` and `ai_model` columns.
- `prisma_data.json` gains `identification.other_methods`.

### Interface

- Search and Saved Searches: database checkboxes grouped into *Ready to use — no API key* and *Requires an API key*.
- Settings: database cards regrouped into *Requires an API key* / *Optional key* / *No key needed*, with the three Elsevier services (Scopus, ScienceDirect, Inspec) placed together since they share one key.

### Documentation

Manual, About page and release guides updated to 12 databases; manual Figure 2 redrawn to match the grouped database selector.

---

## What's New in v2.0.1 — Initial Release

This is the first public release of Review Sentinel Prime. Highlights include:

- **Multi-database search** across 12 scientific databases with Boolean query syntax, live progress tracking, and configurable filters/criteria
- **T&A Screening** with an optional local-LLM AI Screening Assistant (Ollama or any OpenAI-compatible endpoint) that suggests Include/Exclude/Unsure decisions for the reviewer to accept or override
- **PRISMA 2020 support** — auto-generated flow diagram, Review Protocol form, and an Evidence Checklist that auto-assesses up to 27 of 39 entries from run data
- **Saved searches** with scheduled re-runs and diff-based email notifications
- **Feedback, feature request, and survey collection** relayed server-side to Google Forms, working even without SMTP configured
- **Replication package export** — one-click ZIP with CSVs, JSON config, protocol, and PRISMA data
- **Results Hub and charts** for browsing local run snapshots and visualizing publications, keywords, venues, and document types
- **Security hardening** — atomic JSON writes with locking, restart-safe schedules, whole-word filter matching, and XSS/CSV-injection protection
- Released under the GNU General Public License v3.0

---

## Supported Databases

| Database | API Key Required |
| --- | --- |
| IEEE Xplore | Yes — free at `developer.ieee.org` |
| ACM Digital Library | No key — but ACM has no public API and blocks automated server-side access; RSP reports this explicitly during a search |
| Scopus | Yes — free at `dev.elsevier.com` |
| ScienceDirect | Yes — same Elsevier key as Scopus |
| Springer Nature | Yes — free at `dev.springernature.com` |
| Inspec (Engineering Village) | Yes — same Elsevier key as Scopus |
| PubMed | Optional (improves rate limits) |
| arXiv | No |
| HAL Open Science | No |
| OpenAlex | No |
| EBSCO Discovery Service | Yes — institutional EDS subscription (User ID, Password *and* Profile ID); EBSCO's licence restricts caching and redistribution of retrieved metadata |
| Web of Science | Yes — two editions share one key. **Starter**: free at `developer.clarivate.com`, but returns **no abstracts** and free plans allow as few as 50 requests/day. **Expanded**: paid institutional subscription, full records with abstracts |

---

## Getting Started

### Option A — Docker (recommended, no Python required)

> Requires [Docker Desktop](https://www.docker.com/products/docker-desktop) (free).

1. Download and extract the release ZIP.
2. **Windows:** double-click `start-app.bat`
3. **macOS:** double-click `start-app.command`
4. Open your browser at **[http://localhost:8090](http://localhost:8090)**

See [`releases/docker/README.md`](releases/docker/README.md) for the full step-by-step guide.

### Option B — Python (development / direct run)

> Requires Python 3.12+.

```bash
# Clone the repository
git clone <repo-url>
cd ReviewSentinelPrime

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Open **[http://localhost:5001](http://localhost:5001)** in your browser.

On macOS/Linux you can also double-click `releases/python/start.command`.  
On Windows, double-click `releases/python/START.bat`.
To stop launcher-based runs, use `releases/python/stop.command` (macOS/Linux) or `releases/python/STOP.bat` (Windows).

macOS note for release ZIP users: if Gatekeeper blocks a launcher on first run,
open Terminal in the extracted folder and run:

```bash
find . -name "*.command" -exec chmod 755 {} \;
xattr -dr com.apple.quarantine .
```

Then right-click the launcher and choose Open once.

---

## Project Structure

```text
ReviewSentinelPrime/
├── app.py                  # Flask application & all API routes
├── requirements.txt        # Python dependencies
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS, images
├── data/                   # Saved searches, API keys, results (created at runtime)
│   └── results/            # Per-search result history (JSON)
└── releases/
    ├── docker/             # Docker Compose files + user guide README
    └── python/             # One-click Python launcher scripts
```

---

## Filter System

The four filter fields are displayed as color-coded cards on both the **Search** page and the **Saved Searches** page:

| Card | Color | Behaviour |
| --- | --- | --- |
| **Include Terms** | 🟢 Green | Only papers whose title/abstract contain these terms are kept (case-insensitive, whole-word/phrase matching; quoted terms supported) |
| **Exclude Terms** | 🔴 Red | Papers whose title/abstract contain these terms are removed (same whole-word matching) |
| **Include Venues** | 🟢 Green | Only papers whose journal/conference name matches are kept (partial match against the venue name only, never the source database; leave empty for all) |
| **Exclude Venues** | 🔴 Red | Papers whose journal/conference name matches are removed — to exclude an entire database, uncheck it instead |

Each card has an inline **`ALL (AND)`** / **`ANY (OR)`** match mode dropdown:

- **ALL (AND)** — the paper must contain *every* listed term/venue (default for Include Terms)
- **ANY (OR)** — the paper must contain *at least one* of the listed terms/venues (default for Exclude Terms and both venue cards)

> **Tip:** Keep Include Venues on **ANY (OR)** when listing multiple distinct journals (e.g. `TSE, ICSE`) — no paper can belong to all venues simultaneously.

---

## Tech Stack

- **Backend:** Python 3.12, Flask 3.1, APScheduler 3.10, requests 2.32
- **Frontend:** Vanilla JS, Chart.js 4.4, html2canvas 1.4.1, Jinja2, Inter (Google Fonts)
- **Deployment:** Docker & Compose, or direct Python
- **Storage:** Local JSON files — no database server required

---

## Origins & Credits

Review Sentinel Prime (RSP) builds upon the [Replication Package Builder](https://github.com/zemacedo99/Replication-Package-Builder), a tool designed to compile scientific publications from multiple academic databases (IEEE Xplore, ACM Digital Library, Scopus, ScienceDirect, HAL Open Science, Springer Nature, Inspec, PubMed, arXiv, and more) to support Systematic Literature Reviews and Mappings. The original tool was used to produce a published replication package for a systematic literature mapping in the aerospace domain. RSP (2026) extends this vision with a lightweight, locally-run web interface that lets researchers — regardless of coding experience — search databases, schedule automated runs, and manage results entirely in the browser.

RSP brings together work from three distinct sources. Each is credited and cited separately below to avoid confusion.

### 1. This application — Review Sentinel Prime (RSP)

- **Author:** J. Eduardo Ferreira Ribeiro
- **Contributor:** Nuno Pessoa Soares

If RSP helped produce a paper, thesis, or report, please cite it as:

```bibtex
@software{ribeiro2026rsp,
  author  = {Ferreira Ribeiro, J. Eduardo and Pessoa Soares, Nuno},
  title   = {Review Sentinel Prime (RSP)},
  year    = {2026},
  url.    = {https://github.com/ribeiroe/ReviewSentinelPrime}
}
```

### 2. Original tool RSP is built upon — Replication Package Builder

- **Authors:** J. Eduardo Ferreira Ribeiro, J. Antonio Dantas Macedo

> "We want to thank J. Eduardo Ferreira Ribeiro and J. Antonio Dantas Macedo for their substantial contributions in developing and providing the Replication Package Builder, upon which Review Sentinel Prime (RSP) is based."

Please reference the original repository:

```bibtex
@misc{ribeiro2024replicationpackagebuilder,
  author = {Ferreira Ribeiro, J. Eduardo and Dantas Macedo, J. Antonio},
  title  = {Replication Package Builder},
  year   = {2024},
  url    = {https://github.com/zemacedo99/Replication-Package-Builder}
}
```

### 3. Example dataset produced using the original tool

This is a published research dataset created *using* the Replication Package Builder — its authors are the researchers of that study, not the authors of the tool itself.

- **Authors:** J. Eduardo Ferreira Ribeiro, J. Gabriel Silva, A. Aguiar
- **DOI:** [10.5281/zenodo.13763171](https://doi.org/10.5281/zenodo.13763171)

```bibtex
@dataset{ribeiro2024replicationpackage,
  author    = {Ferreira Ribeiro, J. Eduardo and Gabriel Silva, J. and Aguiar, A.},
  title     = {Replication Package for a Systematic Literature Mapping of Agility in
               Safety-Critical Software Development within the Aerospace Industry},
  year      = {2024},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.13763171}
}
```

**Full citation (APA) for the dataset above:**
> Ferreira Ribeiro, J. E., Gabriel Silva, J., & Aguiar, A. (2024). *Replication Package for a Systematic Literature Mapping of Agility in Safety-Critical Software Development within the Aerospace Industry* [Data set]. Zenodo. [https://doi.org/10.5281/zenodo.13763171](https://doi.org/10.5281/zenodo.13763171)

---

## Community Support (Optional)

RSP is free and will remain free under the GNU General Public License v3.0. If it helps your work, you can optionally support RibeiroLabs to help cover development, maintenance, and hosting costs. Donating unlocks no features, no early access, no priority, and comes with no support commitment — it is a voluntary donation only.

- Ko-fi: [https://ko-fi.com/ribeirolabs](https://ko-fi.com/ribeirolabs)
- In-app locations: global footer and the About page

---

## Note on AI Use

RSP's only stage that uses a language model is the optional **AI Screening Assistant** (Settings → AI Screening Assistant), which suggests Include / Exclude / Unsure decisions during Title & Abstract screening. It is disabled by default and, when enabled, targets a local Ollama instance or any OpenAI-compatible endpoint you configure. No other stage (search, filtering, PRISMA generation, export) uses a language model.

Language model output is probabilistic and can be wrong. Every suggestion is a starting point only: the reviewer must accept, override, or reject each one, and all AI-assisted decisions must be verified by the reviewer before being used in any publication or report. RSP is a research support tool — it is not intended for clinical, legal, employment, or other regulated decision-making. RSP is provided as-is under the GNU General Public License v3.0, without warranty of any kind (see [LICENSE](LICENSE), Sections 15 and 16).

---

## License

**Review Sentinel Prime (RSP) itself is released under the [GNU General Public License v3.0](LICENSE).**

The upstream Replication Package Builder it builds upon is released under the [MIT License](https://github.com/zemacedo99/Replication-Package-Builder/blob/main/LICENSE).  
The associated Zenodo dataset is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
