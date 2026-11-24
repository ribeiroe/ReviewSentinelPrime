import base64
import csv
import copy
import hashlib
import html
import io
import json
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

RSP_VERSION = "v2.4.0"

@app.context_processor
def inject_rsp_version():
    return {"rsp_version": RSP_VERSION}

HAL_API_URL = "https://api.archives-ouvertes.fr/search/"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
IEEE_API_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
SCOPUS_API_URL = "https://api.elsevier.com/content/search/scopus"
INSPEC_API_URL = "https://api.elsevier.com/content/ev/results"
SCIENCEDIRECT_API_URL = "https://api.elsevier.com/content/search/sciencedirect"
SPRINGER_API_URL = "https://api.springernature.com/meta/v2/json"
PUBMED_API_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ACM_API_URL = "https://dl.acm.org/action/doSearch"
EBSCO_AUTH_URL = "https://eds-api.ebscohost.com/authservice/rest/uidauth"
EBSCO_API_URL = "https://eds-api.ebscohost.com/edsapi/rest"
WOS_API_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"
WOS_EXPANDED_API_URL = "https://wos-api.clarivate.com/api/wos"
IEEE_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ieee_api_key.txt")
SCOPUS_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scopus_api_key.txt")
INSPEC_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "inspec_api_key.txt")
SCIENCEDIRECT_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sciencedirect_api_key.txt")
# Shared by Scopus and ScienceDirect: Elsevier requires this in addition to
# the API key when a request originates outside the institution's IP range.
ELSEVIER_INSTTOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "elsevier_insttoken.txt")
SPRINGER_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "springer_api_key.txt")
PUBMED_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pubmed_api_key.txt")
OPENALEX_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "openalex_api_key.txt")
ACM_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "acm_api_key.txt")
WOS_API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wos_api_key.txt")
# Both WoS editions authenticate with the same X-ApiKey; only the endpoint and
# response shape differ, so the edition is stored alongside the key.
WOS_EDITION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wos_edition.txt")
# EDS needs three values (user id, password, profile id), so it uses a JSON
# credential file rather than the single-key .txt files above.
EBSCO_CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ebsco_credentials.json")
GITHUB_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "github_token.txt")
SMTP_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "smtp_config.json")
GOOGLE_FORM_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "google_form_config.json")
FEEDBACK_GOOGLE_FORM_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "google_form_feedback_config.json")
FEATURE_GOOGLE_FORM_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "google_form_feature_config.json")
FEEDBACK_UPLOAD_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feedback_upload_config.json")
FEEDBACK_ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feedback_attachments")
RUN_RETENTION_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "run_retention_settings.json")
RETRIEVAL_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "retrieval_settings.json")
PROTOCOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "protocol.json")
AI_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ai_config.json")

# UUID validation — prevents path traversal via search_id URL parameter
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Lock to serialise concurrent reads/writes to searches.json
_searches_lock = threading.Lock()

# Lock serialising read-modify-write cycles on run/result JSON files, which
# are shared between Flask request threads and the APScheduler thread.
_data_io_lock = threading.RLock()


def _to_int(value, default):
    """int() that returns a default instead of raising on bad user input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _write_json_atomic(path, data, indent=2):
    """Write JSON via a temp file + os.replace.

    A crash or concurrent read mid-write can never observe a truncated file —
    run snapshots hold irreplaceable manual screening work.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "searches.json")
RESULTS_DIR = os.path.join(DATA_DIR, "results")
ADHOC_RESULTS_DIR = os.path.join(RESULTS_DIR, "adhoc")
RAW_CACHE_FILE = os.path.join(DATA_DIR, "raw_cache.json")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(ADHOC_RESULTS_DIR, exist_ok=True)

DEFAULT_RUN_RETENTION_SETTINGS = {
    "savedKeepLast": 10,
    "adhocKeepLast": 10,
}

# --- Scheduler setup ---
# In Flask debug mode the werkzeug reloader spawns a child process
# (WERKZEUG_RUN_MAIN=true) that is the real server; the parent is just the
# file-watcher. We start the scheduler unconditionally here so it is always
# available, but load_all_schedules() below is guarded to only register jobs
# once (in the child), preventing every scheduled job from firing twice.
scheduler = BackgroundScheduler()
scheduler.start()


def _clean_api_key(key):
    """Strip whitespace/BOM and unwrap stray quotes from a pasted API key.

    Catches keys copied from JSON/.env sources (e.g. "abc123") or saved with
    a UTF-8 BOM, which .strip() alone would leave intact.
    """
    key = (key or "").strip()
    if len(key) >= 2 and key[0] in ("'", '"') and key[-1] == key[0]:
        key = key[1:-1].strip()
    return key


def _load_api_key(file_path, env_var):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8-sig") as f:
            key = _clean_api_key(f.read())
            if key:
                return key
    # Env var fallback was previously returned raw; trailing newlines/quotes
    # from shell exports or .env files would then reach the API unmasked.
    return _clean_api_key(os.environ.get(env_var, ""))


def _save_api_key(file_path, key):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    temp_path = file_path + ".tmp"
    try:
        with open(temp_path, "w") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_ieee_api_key():         return _load_api_key(IEEE_API_KEY_FILE,         "IEEE_API_KEY")
def get_scopus_api_key():       return _load_api_key(SCOPUS_API_KEY_FILE,       "SCOPUS_API_KEY")
def get_inspec_api_key():       return _load_api_key(INSPEC_API_KEY_FILE,       "INSPEC_API_KEY")
def get_sciencedirect_api_key(): return _load_api_key(SCIENCEDIRECT_API_KEY_FILE, "SCIENCEDIRECT_API_KEY")
def get_elsevier_insttoken():    return _load_api_key(ELSEVIER_INSTTOKEN_FILE,    "ELSEVIER_INSTTOKEN")
def get_springer_api_key():     return _load_api_key(SPRINGER_API_KEY_FILE,     "SPRINGER_API_KEY")
def get_pubmed_api_key():       return _load_api_key(PUBMED_API_KEY_FILE,       "PUBMED_API_KEY")
def get_openalex_api_key():     return _load_api_key(OPENALEX_API_KEY_FILE,     "OPENALEX_API_KEY")
def get_acm_api_key():          return _load_api_key(ACM_API_KEY_FILE,          "ACM_API_KEY")
def get_wos_api_key():          return _load_api_key(WOS_API_KEY_FILE,          "WOS_API_KEY")


def get_wos_edition():
    """Return the active Web of Science edition: 'starter' or 'expanded'."""
    value = _load_api_key(WOS_EDITION_FILE, "WOS_EDITION").strip().lower()
    return "expanded" if value == "expanded" else "starter"


def get_ebsco_credentials():
    """Load EBSCO EDS credentials from file, with env vars as override."""
    cfg = {}
    if os.path.exists(EBSCO_CREDENTIALS_FILE):
        try:
            with open(EBSCO_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            cfg = {}
    return {
        "userId":   os.environ.get("EBSCO_USER_ID",  cfg.get("userId", "")).strip(),
        "password": os.environ.get("EBSCO_PASSWORD", cfg.get("password", "")).strip(),
        "profile":  os.environ.get("EBSCO_PROFILE",  cfg.get("profile", "")).strip(),
    }


def _save_ebsco_credentials(creds):
    """Persist EDS credentials owner-readable only — the file holds a password."""
    _write_json_atomic(EBSCO_CREDENTIALS_FILE, creds)
    try:
        os.chmod(EBSCO_CREDENTIALS_FILE, 0o600)
    except OSError:
        pass


def get_smtp_config():
    """Load SMTP config from file, with env vars as override."""
    cfg = {}
    if os.path.exists(SMTP_CONFIG_FILE):
        with open(SMTP_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    return {
        "host":  os.environ.get("SMTP_HOST",  cfg.get("host",  "")),
        "port":  int(os.environ.get("SMTP_PORT",  cfg.get("port",  587))),
        "user":  os.environ.get("SMTP_USER",  cfg.get("user",  "")),
        "password": os.environ.get("SMTP_PASS", cfg.get("password", "")),
        "from_addr": os.environ.get("SMTP_FROM", cfg.get("from_addr", "")),
    }


def _load_google_form_config(config_file, url_env, entries_env, page_history_env):
    """Load a Google Forms relay config (form URL + field -> entry.ID map).

    Never hardcoded: read from a git-ignored JSON file under data/, overridable
    with env vars. Returns {} when no form is configured, disabling the relay.
    """
    cfg = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}

    form_url = os.environ.get(url_env, cfg.get("form_url", "")).strip()
    entries = cfg.get("entries", {})
    env_entries = os.environ.get(entries_env, "")
    if env_entries:
        try:
            parsed = json.loads(env_entries)
            if isinstance(parsed, dict):
                entries = parsed
        except ValueError:
            pass

    if not form_url or not isinstance(entries, dict) or not entries:
        return {}
    value_map = cfg.get("value_map", {})
    if not isinstance(value_map, dict):
        value_map = {}
    page_history = str(os.environ.get(page_history_env, cfg.get("page_history", "")) or "")
    return {"form_url": form_url, "entries": entries, "value_map": value_map, "page_history": page_history}


def get_google_form_config():
    return _load_google_form_config(
        GOOGLE_FORM_CONFIG_FILE,
        "GOOGLE_FORM_URL",
        "GOOGLE_FORM_ENTRY_MAP",
        "GOOGLE_FORM_PAGE_HISTORY",
    )


def get_feedback_google_form_config():
    return _load_google_form_config(
        FEEDBACK_GOOGLE_FORM_CONFIG_FILE,
        "FEEDBACK_GOOGLE_FORM_URL",
        "FEEDBACK_GOOGLE_FORM_ENTRY_MAP",
        "FEEDBACK_GOOGLE_FORM_PAGE_HISTORY",
    )


def get_feature_google_form_config():
    return _load_google_form_config(
        FEATURE_GOOGLE_FORM_CONFIG_FILE,
        "FEATURE_GOOGLE_FORM_URL",
        "FEATURE_GOOGLE_FORM_ENTRY_MAP",
        "FEATURE_GOOGLE_FORM_PAGE_HISTORY",
    )


_google_form_page_cache = {}


def _google_form_page_history(view_url):
    """Return the pageHistory value ('0,1,..,n') for a multi-section form.

    Google discards answers on pages the submitter never "visited", so a relayed
    response must list every page. Page count is read from the live form and
    cached; None means the count could not be determined.
    """
    if view_url in _google_form_page_cache:
        return _google_form_page_cache[view_url]

    pages = None
    try:
        resp = requests.get(view_url, timeout=15)
        if resp.status_code < 400:
            match = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\])\s*;\s*</script>", resp.text, re.S)
            if match:
                items = json.loads(match.group(1))[1][1] or []
                page_breaks = sum(1 for item in items if item[3] == 8)
                pages = page_breaks + 1
    except (requests.RequestException, ValueError, IndexError, TypeError):
        pages = None

    history = ",".join(str(i) for i in range(pages)) if pages else None
    _google_form_page_cache[view_url] = history
    return history


def _post_to_google_form(cfg, payload):
    """POST a prepared entry payload to the form's formResponse endpoint."""
    form_url = cfg["form_url"]
    if "docs.google.com" not in form_url:
        return False, "Configured form URL is not a Google Forms URL."
    post_url = re.sub(r"/(viewform|formResponse)(\?.*)?$", "/formResponse", form_url)

    view_url = re.sub(r"/(viewform|formResponse)(\?.*)?$", "/viewform", form_url)
    page_history = cfg.get("page_history") or _google_form_page_history(view_url)
    if page_history:
        payload["pageHistory"] = page_history
        payload["fvv"] = "1"

    try:
        resp = requests.post(post_url, data=payload, timeout=15)
    except requests.RequestException as exc:
        return False, f"Google Forms relay failed: {exc}"

    if resp.status_code >= 400:
        return False, f"Google Forms rejected the response (HTTP {resp.status_code})."
    # Forms answers 200 with the form re-rendered when a value fails validation.
    if "formResponse" in resp.text and "required question" in resp.text.lower():
        return False, "Google Forms rejected the response (validation error)."
    return True, None


def _relay_survey_to_google_form(data):
    """POST the survey response to the configured Google Form.

    Runs independently of SMTP. Returns (relayed: bool, error: str|None);
    a missing configuration is reported as (False, None).
    """
    cfg = get_google_form_config()
    if not cfg:
        return False, None

    background = data.get("background", {}) or {}
    likert = data.get("likert", {}) or {}
    open_fb = data.get("open", {}) or {}

    payload = {}
    value_map = cfg.get("value_map", {})
    for field, entry_id in cfg["entries"].items():
        if field in background:
            value = background.get(field)
        elif field in likert:
            value = likert.get(field)
        else:
            value = open_fb.get(field)
        if value in (None, ""):
            continue
        # Dropdown codes must be sent as the form's own option labels.
        value = value_map.get(field, {}).get(str(value), value)
        payload[str(entry_id)] = str(value)

    if not payload:
        return False, "No survey answers matched the configured form entries."

    return _post_to_google_form(cfg, payload)


def _relay_feedback_to_google_form(data):
    """POST a bug report or feature request to its configured Google Form.

    Runs independently of SMTP. Returns (relayed: bool, error: str|None);
    a missing configuration is reported as (False, None).
    """
    if data.get("type") == "feature":
        cfg = get_feature_google_form_config()
    else:
        cfg = get_feedback_google_form_config()
    if not cfg:
        return False, None

    payload = {}
    value_map = cfg.get("value_map", {})
    for field, entry_id in cfg["entries"].items():
        value = data.get(field)
        if value in (None, ""):
            continue
        # Dropdown codes must be sent as the form's own option labels.
        value = value_map.get(field, {}).get(str(value), value)
        payload[str(entry_id)] = str(value)

    if not payload:
        return False, "No feedback answers matched the configured form entries."

    return _post_to_google_form(cfg, payload)


# ── Feedback attachments ─────────────────────────────────────────────────────
FEEDBACK_MAX_FILES = 5
FEEDBACK_MAX_FILE_BYTES = 5 * 1024 * 1024
FEEDBACK_MAX_TOTAL_BYTES = 10 * 1024 * 1024
_FEEDBACK_IMAGE_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "pdf": (b"%PDF-",),
}
_FEEDBACK_TEXT_EXTS = {"txt", "log", "csv", "json", "ris", "bib"}
FEEDBACK_ALLOWED_EXTS = set(_FEEDBACK_IMAGE_SIGNATURES) | {"webp"} | _FEEDBACK_TEXT_EXTS
_FEEDBACK_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "pdf": "application/pdf", "json": "application/json",
    "csv": "text/csv",
}


def get_feedback_upload_config():
    """Apps Script endpoint that stores feedback attachments in Google Drive.

    Read from data/feedback_upload_config.json (shipped from releases/relay/),
    overridable with FEEDBACK_UPLOAD_URL / FEEDBACK_UPLOAD_TOKEN. Returns {}
    when not configured, which disables the Drive upload.
    """
    cfg = {}
    if os.path.exists(FEEDBACK_UPLOAD_CONFIG_FILE):
        try:
            with open(FEEDBACK_UPLOAD_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}
    url = str(os.environ.get("FEEDBACK_UPLOAD_URL", cfg.get("upload_url", "")) or "").strip()
    token = str(os.environ.get("FEEDBACK_UPLOAD_TOKEN", cfg.get("token", "")) or "").strip()
    if not url.startswith("https://script.google.com/"):
        return {}
    return {"upload_url": url, "token": token}


def _safe_attachment_name(name):
    name = unicodedata.normalize("NFKC", os.path.basename(str(name or "").replace("\\", "/")))
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .")
    return (name or "attachment")[:120]


def _collect_feedback_attachments(file_storages):
    """Validate uploaded files. Returns a list of {name, ext, mime, data}.

    Raises ValueError with a user-facing message when a limit or type check fails.
    """
    files = [f for f in file_storages if f and f.filename]
    if len(files) > FEEDBACK_MAX_FILES:
        raise ValueError(f"You can attach at most {FEEDBACK_MAX_FILES} files.")
    result, total = [], 0
    for storage in files:
        name = _safe_attachment_name(storage.filename)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in FEEDBACK_ALLOWED_EXTS:
            raise ValueError(f"'{name}' is not an allowed file type.")
        data = storage.read(FEEDBACK_MAX_FILE_BYTES + 1)
        if len(data) > FEEDBACK_MAX_FILE_BYTES:
            raise ValueError(f"'{name}' is larger than {FEEDBACK_MAX_FILE_BYTES // (1024 * 1024)} MB.")
        if not data:
            raise ValueError(f"'{name}' is empty.")
        total += len(data)
        if total > FEEDBACK_MAX_TOTAL_BYTES:
            raise ValueError(f"Attachments exceed {FEEDBACK_MAX_TOTAL_BYTES // (1024 * 1024)} MB in total.")
        # Check content, not just the extension or browser-reported type.
        if ext == "webp":
            valid = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        elif ext in _FEEDBACK_IMAGE_SIGNATURES:
            valid = data.startswith(_FEEDBACK_IMAGE_SIGNATURES[ext])
        else:
            valid = b"\x00" not in data[:8192]
        if not valid:
            raise ValueError(f"'{name}' does not look like a valid .{ext} file.")
        mime = _FEEDBACK_MIME.get(ext, "text/plain")
        if any(r["name"].lower() == name.lower() for r in result):
            name = f"{len(result) + 1}_{name}"
        result.append({"name": name, "ext": ext, "mime": mime, "data": data})
    return result


def _upload_feedback_attachments(attachments, submission_id, fb_type, title):
    """Send attachments to the Apps Script endpoint. Returns (links_text|None, error|None)."""
    cfg = get_feedback_upload_config()
    if not cfg:
        return None, None
    payload = {
        "token": cfg["token"],
        "submissionId": submission_id,
        "type": fb_type,
        "title": title,
        "files": [
            {"name": a["name"], "mimeType": a["mime"],
             "data": base64.b64encode(a["data"]).decode("ascii")}
            for a in attachments
        ],
    }
    try:
        # Apps Script answers with a 302 to googleusercontent.com; requests
        # follows it as a GET, which is how the script's result is fetched.
        resp = requests.post(cfg["upload_url"], json=payload, timeout=60)
        result = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return None, f"Attachment upload failed: {exc}"
    if not result.get("ok"):
        return None, f"Attachment upload rejected: {result.get('error', 'unknown error')}"
    lines = [f"Folder: {result.get('folderUrl', '')}"]
    lines += [f"{l.get('name', '')}: {l.get('url', '')}" for l in result.get("links", [])]
    return "\n".join(lines), None


def _save_feedback_attachments_locally(attachments, submission_id):
    """Keep a local copy when the Drive upload is unavailable. Returns the folder path."""
    folder = os.path.join(FEEDBACK_ATTACHMENTS_DIR, submission_id)
    os.makedirs(folder, exist_ok=True)
    for a in attachments:
        with open(os.path.join(folder, a["name"]), "wb") as f:
            f.write(a["data"])
    return folder


def _clamp_keep_last(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 10
    return min(max(v, 1), 100)


def get_run_retention_settings():
    settings = dict(DEFAULT_RUN_RETENTION_SETTINGS)
    settings["lastCleanupAt"] = ""
    if os.path.exists(RUN_RETENTION_SETTINGS_FILE):
        try:
            with open(RUN_RETENTION_SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                settings["savedKeepLast"] = _clamp_keep_last(cfg.get("savedKeepLast", settings["savedKeepLast"]))
                settings["adhocKeepLast"] = _clamp_keep_last(cfg.get("adhocKeepLast", settings["adhocKeepLast"]))
                settings["lastCleanupAt"] = str(cfg.get("lastCleanupAt") or "")
        except Exception:
            pass
    return settings


def save_run_retention_settings(settings):
    existing = {}
    if os.path.exists(RUN_RETENTION_SETTINGS_FILE):
        try:
            with open(RUN_RETENTION_SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f) or {}
        except Exception:
            existing = {}

    cfg = {
        "savedKeepLast": _clamp_keep_last(settings.get("savedKeepLast", DEFAULT_RUN_RETENTION_SETTINGS["savedKeepLast"])),
        "adhocKeepLast": _clamp_keep_last(settings.get("adhocKeepLast", DEFAULT_RUN_RETENTION_SETTINGS["adhocKeepLast"])),
        "lastCleanupAt": str(settings.get("lastCleanupAt", existing.get("lastCleanupAt", "")) or ""),
    }
    _write_json_atomic(RUN_RETENTION_SETTINGS_FILE, cfg)
    return cfg


RETRIEVAL_CAP_MIN = 100
RETRIEVAL_CAP_MAX = 10000


def _clamp_cap(value, default):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, RETRIEVAL_CAP_MIN), RETRIEVAL_CAP_MAX)


def get_retrieval_settings():
    """Retrieval options set in Settings → Retrieval.

    yearAtDatabase: send the year range to each API (records outside it are
    counted with one extra count-only request, not downloaded).
    caps: per-database retrieval ceilings.
    """
    settings = {"yearAtDatabase": False, "caps": dict(RSP_RETRIEVAL_CAP)}
    if os.path.exists(RETRIEVAL_SETTINGS_FILE):
        try:
            with open(RETRIEVAL_SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            if isinstance(cfg, dict):
                settings["yearAtDatabase"] = bool(cfg.get("yearAtDatabase", False))
                caps = cfg.get("caps") or {}
                for db, default in RSP_RETRIEVAL_CAP.items():
                    settings["caps"][db] = _clamp_cap(caps.get(db, default), default)
        except Exception:
            pass
    return settings


def save_retrieval_settings(data):
    current = get_retrieval_settings()
    caps = (data or {}).get("caps") or {}
    cfg = {
        "yearAtDatabase": bool((data or {}).get("yearAtDatabase", current["yearAtDatabase"])),
        "caps": {db: _clamp_cap(caps.get(db, current["caps"][db]), current["caps"][db])
                 for db in RSP_RETRIEVAL_CAP},
    }
    _write_json_atomic(RETRIEVAL_SETTINGS_FILE, cfg)
    return cfg


def _retrieval_cap(db):
    return get_retrieval_settings()["caps"].get(db, RSP_RETRIEVAL_CAP[db])


def load_searches():
    with _searches_lock:
        if not os.path.exists(CONFIG_FILE):
            return []
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)


def save_searches(searches):
    with _searches_lock:
        _write_json_atomic(CONFIG_FILE, searches)


def load_previous_results(search_id):
    """Return the docs list from the most recent saved run, or [] if none."""
    filepath = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            history = json.load(f)
        if history:
            return history[0].get("docs", [])
    except Exception:
        pass
    return []


def _doc_key(doc):
    """Return a stable identifier for a document (DOI preferred, else normalised title)."""
    doi = (doc.get("doi") or "").strip().lower()
    if doi:
        return doi
    return "".join(c for c in (doc.get("title") or "").lower() if c.isalnum())


def save_results(search_id, results):
    filepath = os.path.join(RESULTS_DIR, f"{search_id}.json")
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "numFound": results["numFound"],
        "docs": results["docs"],
    }
    # Append to history (keep last 10 runs)
    with _data_io_lock:
        history = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.insert(0, entry)
        history = history[:10]
        _write_json_atomic(filepath, history)


def save_search_run(search_id, run_data, keep_last=None):
    """Save a search run with full local metadata while keeping backward-compatible keys."""
    if keep_last is None:
        keep_last = get_run_retention_settings()["savedKeepLast"]
    keep_last = _clamp_keep_last(keep_last)

    filepath = os.path.join(RESULTS_DIR, f"{search_id}.json")
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    entry = {
        "runId": str(uuid.uuid4()),
        "timestamp": timestamp,
        "numFound": run_data.get("numFound", 0),
        "docs": run_data.get("docs", []),
        "query": run_data.get("query", ""),
        "databases": run_data.get("databases", []),
        "filters": run_data.get("filters", {}),
        "rawDocs": run_data.get("rawDocs", []),
        "excluded_docs": run_data.get("excluded_docs", {}),
        "prisma": run_data.get("prisma", {}),
        "dbCounts": run_data.get("dbCounts", {}),
    }

    with _data_io_lock:
        history = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.insert(0, entry)
        history = history[:keep_last]
        _write_json_atomic(filepath, history)

    return entry


def save_ad_hoc_run(run_data):
    """Persist one ad-hoc search execution in its own file under data/results/adhoc."""
    run_id = str(uuid.uuid4())
    payload = {
        "runId": run_id,
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "numFound": run_data.get("numFound", 0),
        "docs": run_data.get("docs", []),
        "query": run_data.get("query", ""),
        "databases": run_data.get("databases", []),
        "filters": run_data.get("filters", {}),
        "rawDocs": run_data.get("rawDocs", []),
        "excluded_docs": run_data.get("excluded_docs", {}),
        "prisma": run_data.get("prisma", {}),
        "dbCounts": run_data.get("dbCounts", {}),
        "screeningDecisions": {},
    }
    filepath = os.path.join(ADHOC_RESULTS_DIR, f"{run_id}.json")
    with _data_io_lock:
        _write_json_atomic(filepath, payload)

    # Keep ad-hoc history bounded, aligned with saved-search history retention.
    keep_last = get_run_retention_settings()["adhocKeepLast"]
    _prune_adhoc_runs(keep_last=keep_last)
    return run_id


def _prune_adhoc_runs(keep_last=10):
    """Keep only the newest ad-hoc run files."""
    if keep_last < 1:
        keep_last = 1
    if not os.path.isdir(ADHOC_RESULTS_DIR):
        return

    files = []
    for name in os.listdir(ADHOC_RESULTS_DIR):
        if not name.endswith(".json"):
            continue
        run_id = name[:-5]
        if not _UUID_RE.fullmatch(run_id):
            continue
        path = os.path.join(ADHOC_RESULTS_DIR, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        files.append((mtime, path))

    files.sort(reverse=True)  # newest first
    deleted = 0
    for _, path in files[keep_last:]:
        try:
            os.remove(path)
            deleted += 1
        except OSError:
            pass
    return deleted


def _find_previous_adhoc_run(query, databases):
    """Return the most recent ad-hoc run that used the same query+databases fingerprint.

    Returns the parsed run dict, or None if no matching previous run exists.
    The caller must invoke this BEFORE saving the new run so we don't match it.
    """
    if not os.path.isdir(ADHOC_RESULTS_DIR):
        return None

    query_norm = (query or "").strip().lower()
    db_set = frozenset(d.lower() for d in (databases or []))

    # Collect all adhoc run files sorted newest-first
    files = []
    for name in os.listdir(ADHOC_RESULTS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(ADHOC_RESULTS_DIR, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        files.append((mtime, path))
    files.sort(reverse=True)

    for _, path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                run = json.load(f)
            run_query = (run.get("query") or "").strip().lower()
            run_dbs   = frozenset(d.lower() for d in (run.get("databases") or []))
            if run_query == query_norm and run_dbs == db_set:
                return run
        except Exception:
            continue
    return None


def _prune_saved_runs(keep_last=10):
    """Trim each saved-search history file to keep only the latest N runs."""
    keep_last = _clamp_keep_last(keep_last)
    if not os.path.isdir(RESULTS_DIR):
        return 0

    deleted = 0
    for name in os.listdir(RESULTS_DIR):
        if name == "adhoc" or not name.endswith(".json"):
            continue
        search_id = name[:-5]
        if not _UUID_RE.fullmatch(search_id):
            continue
        path = os.path.join(RESULTS_DIR, name)
        history = _read_json_file(path, None)
        if not isinstance(history, list):
            continue
        if len(history) <= keep_last:
            continue
        deleted += len(history) - keep_last
        history = history[:keep_last]
        try:
            with _data_io_lock:
                _write_json_atomic(path, history)
        except OSError:
            pass
    return deleted


def _run_retention_stats():
    """Return counts and disk usage for saved/ad-hoc run files."""
    saved_runs = 0
    adhoc_runs = 0
    saved_bytes = 0
    adhoc_bytes = 0

    if os.path.isdir(RESULTS_DIR):
        for name in os.listdir(RESULTS_DIR):
            path = os.path.join(RESULTS_DIR, name)
            if name == "adhoc":
                continue
            if not name.endswith(".json"):
                continue
            search_id = name[:-5]
            if not _UUID_RE.fullmatch(search_id):
                continue
            history = _read_json_file(path, [])
            if isinstance(history, list):
                saved_runs += len(history)
            try:
                saved_bytes += os.path.getsize(path)
            except OSError:
                pass

    if os.path.isdir(ADHOC_RESULTS_DIR):
        for name in os.listdir(ADHOC_RESULTS_DIR):
            if not name.endswith(".json"):
                continue
            run_id = name[:-5]
            if not _UUID_RE.fullmatch(run_id):
                continue
            adhoc_runs += 1
            path = os.path.join(ADHOC_RESULTS_DIR, name)
            try:
                adhoc_bytes += os.path.getsize(path)
            except OSError:
                pass

    return {
        "savedRuns": saved_runs,
        "adhocRuns": adhoc_runs,
        "savedBytes": saved_bytes,
        "adhocBytes": adhoc_bytes,
        "totalRuns": saved_runs + adhoc_runs,
        "totalBytes": saved_bytes + adhoc_bytes,
    }


def _read_json_file(file_path, default_value):
    """Read JSON from file and return default on failure."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value


def _file_iso_mtime(file_path):
    """Return file mtime as UTC ISO string."""
    try:
        return datetime.utcfromtimestamp(os.path.getmtime(file_path)).isoformat() + "Z"
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _run_summary(entry, storage, detail_url, download_url, search_id="", history_index=None):
    """Build a compact, UI-friendly run summary."""
    docs = entry.get("docs") or []
    decisions = entry.get("screeningDecisions") or {}
    included_sc = sum(1 for d in decisions.values() if isinstance(d, dict) and d.get("decision") == "include")
    excluded_sc = sum(1 for d in decisions.values() if isinstance(d, dict) and d.get("decision") == "exclude")
    unsure_sc   = sum(1 for d in decisions.values() if isinstance(d, dict) and d.get("decision") == "unsure")
    screened    = included_sc + excluded_sc + unsure_sc
    return {
        "storage": storage,
        "searchId": search_id,
        "historyIndex": history_index,
        "runId": entry.get("runId", ""),
        "timestamp": entry.get("timestamp", ""),
        "query": entry.get("query", ""),
        "databases": entry.get("databases", []),
        "numFound": int(entry.get("numFound", 0) or 0),
        "included": len(docs),
        "detailUrl": detail_url,
        "downloadUrl": download_url,
        "zipDownloadUrl": download_url.replace("/download/", "/download-zip/", 1),
        "screeningProgress": {
            "total": len(docs),
            "screened": screened,
            "included": included_sc,
            "excluded": excluded_sc,
            "unsure": unsure_sc,
        },
    }


def _csv_safe(value):
    """Neutralize spreadsheet formula injection.

    Paper metadata comes from external APIs; a title like
    =HYPERLINK("http://evil","x") would execute when the researcher opens
    the CSV in Excel. Prefix cells starting with = + - @ with a quote.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _csv_safe_row(row):
    return {k: _csv_safe(v) for k, v in row.items()}


def _docs_to_csv_string(docs):
    """Serialize a docs array to CSV string."""
    fieldnames = [
        "title", "authors", "year", "source", "journal", "doi", "url",
        "docType", "language", "keywords", "abstract",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for d in docs or []:
        writer.writerow(_csv_safe_row({
            "title": d.get("title", ""),
            "authors": "; ".join(d.get("authors") or []),
            "year": d.get("year", ""),
            "source": d.get("source", ""),
            "journal": d.get("journal", ""),
            "doi": d.get("doi", ""),
            "url": d.get("url", ""),
            "docType": d.get("docType", ""),
            "language": d.get("language", ""),
            "keywords": "; ".join(d.get("keywords") or []),
            "abstract": d.get("abstract", ""),
        }))
    # BOM so Excel opens the file as UTF-8 (accented author names, etc.)
    return "\ufeff" + buf.getvalue()


def _build_run_zip_buffer(payload):
    """Build ZIP bytes for one run payload."""
    zip_buf = io.BytesIO()
    docs = payload.get("docs") or []
    raw_docs = payload.get("rawDocs") or []
    excluded_docs = payload.get("excluded_docs") or {}

    summary = {
        "runId": payload.get("runId", ""),
        "timestamp": payload.get("timestamp", ""),
        "query": payload.get("query", ""),
        "databases": payload.get("databases", []),
        "numFound": payload.get("numFound", 0),
        "includedCount": len(docs),
        "rawCount": len(raw_docs),
        "excludedReasons": list(excluded_docs.keys()),
    }

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("run.json", json.dumps(payload, indent=2, ensure_ascii=False))
        zf.writestr("included_docs.csv", _docs_to_csv_string(docs))
        zf.writestr("raw_docs.csv", _docs_to_csv_string(raw_docs))
        zf.writestr("excluded_docs.json", json.dumps(excluded_docs, indent=2, ensure_ascii=False))
        zf.writestr("summary.json", json.dumps(summary, indent=2, ensure_ascii=False))
        db_counts = payload.get("dbCounts") or (payload.get("prisma") or {}).get("identification", {}).get("databases") or {}
        if db_counts:
            zf.writestr("search_log.csv", _search_log_csv(db_counts))
            zf.writestr("search_log.json", json.dumps(_search_log_rows(db_counts), indent=2, ensure_ascii=False))

        # Screening decisions CSV (only written when decisions exist)
        screening_decisions = payload.get("screeningDecisions") or {}
        if screening_decisions:
            sd_buf = io.StringIO()
            sd_fields = ["title", "authors", "year", "source", "doi", "decision", "reason", "screened_at"]
            sd_writer = csv.DictWriter(sd_buf, fieldnames=sd_fields, extrasaction="ignore")
            sd_writer.writeheader()
            docs_by_id = {}
            for d in docs:
                pid = ("doi:" + d["doi"].lower().strip()) if d.get("doi") else ("title:" + (d.get("title") or "").lower().strip())
                docs_by_id[pid] = d
            for pid, dec in screening_decisions.items():
                if not isinstance(dec, dict):
                    continue
                doc = docs_by_id.get(pid, {})
                sd_writer.writerow({
                    "title":       doc.get("title", ""),
                    "authors":     "; ".join(doc.get("authors") or []) if isinstance(doc.get("authors"), list) else (doc.get("authors") or ""),
                    "year":        doc.get("year", ""),
                    "source":      doc.get("source", ""),
                    "doi":         doc.get("doi", ""),
                    "decision":    dec.get("decision", ""),
                    "reason":      dec.get("reason", ""),
                    "screened_at": dec.get("timestamp", dec.get("ts", "")),
                })
            zf.writestr("screening_decisions.csv", sd_buf.getvalue())

    zip_buf.seek(0)
    return zip_buf


def _smtp_send(to_address, msg):
    """Shared SMTP send helper. Accepts a single address or comma-separated list.
    Sends one copy per recipient (addresses are not exposed to each other).
    Returns True on success, raises on failure."""
    cfg = get_smtp_config()
    smtp_host = cfg["host"]
    if not smtp_host:
        return False  # No SMTP configured — silently skip
    smtp_port = cfg["port"]
    smtp_user = cfg["user"]
    smtp_pass = cfg["password"]
    recipients = [a.strip() for a in str(to_address).split(",") if a.strip()]
    if not recipients:
        return False
    ssl_context = _create_smtp_ssl_context()
    if int(smtp_port) == 465:
        # Implicit TLS (e.g. Gmail's suggested port) — plain SMTP + STARTTLS
        # against this port hangs until timeout with a confusing error.
        smtp_conn = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10, context=ssl_context)
    else:
        smtp_conn = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
    with smtp_conn as server:
        server.ehlo()
        if int(smtp_port) not in (25, 465):
            server.starttls(context=ssl_context)
            server.ehlo()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        for recipient in recipients:
            msg.replace_header("To", recipient)
            server.sendmail(msg["From"], [recipient], msg.as_string())
    return True


def _create_smtp_ssl_context():
    import ssl
    ssl_context = ssl.create_default_context()
    try:
        # Some macOS Python builds do not inherit a usable system CA store.
        ssl_context.load_verify_locations(cafile=requests.certs.where())
    except Exception:
        pass
    return ssl_context


def _safe_email_link(link):
    """Allow only http(s) URLs in email hrefs; API metadata is untrusted."""
    link = (link or "").strip()
    return link if link.startswith(("http://", "https://")) else ""


def send_notification_email(to_address, search_name, num_found, docs,
                            new_doc_keys=None, removed_docs=None, is_first_run=False,
                            coverage_warning=""):
    """Send a scheduled-run success summary email. Requires SMTP_HOST env var at minimum."""
    cfg = get_smtp_config()
    if not cfg["host"]:
        return
    from_addr = cfg["from_addr"] or cfg["user"] or "reviewsentinel@localhost"

    run_timestamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')
    new_doc_keys = new_doc_keys or set()
    removed_docs = removed_docs or []

    # Build a short HTML summary (top 10 results with year and link)
    sample = docs[:10]
    rows_html = ""
    for i, doc in enumerate(sample):
        title = html.escape(doc.get("title", "(no title)"))
        year = html.escape(str(doc.get("year") or ""))
        source = html.escape(doc.get("source", ""))
        url = doc.get("url") or ""
        doi = doc.get("doi") or ""
        link = html.escape(_safe_email_link(f"https://doi.org/{doi}" if doi else url), quote=True)
        is_new = (not is_first_run) and (_doc_key(doc) in new_doc_keys)
        title_cell = (
            f'<a href="{link}" style="color:#1a2a4a;text-decoration:none;">{title}</a>'
            if link else title
        )
        new_badge = '&nbsp;<span style="background:#d4edda;color:#155724;font-size:0.78em;padding:1px 5px;border-radius:3px;font-weight:600;">NEW</span>' if is_new else ""
        rows_html += (
            f'<tr{"  style=background:#f0fff4;" if is_new else ""}>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;">{i+1}.</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;">{title_cell}'
            f'{"&nbsp;&nbsp;<span style=\'color:#888;font-size:0.9em;\'>(" + str(year) + ")</span>" if year else ""}'
            f'{new_badge}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;color:#555;white-space:nowrap;">{source}</td>'
            f'</tr>'
        )
    more_note = f'<p style="color:#888;">… and {len(docs) - len(sample)} more results in the attached CSV.</p>' if len(docs) > len(sample) else ""

    # Summary line about changes
    if is_first_run:
        change_summary = '<p style="color:#555;">This is the <strong>first run</strong> for this search — no previous results to compare.</p>'
    else:
        n_new = len(new_doc_keys)
        n_removed = len(removed_docs)
        parts = []
        if n_new:
            parts.append(f'<span style="color:#155724;font-weight:600;">+{n_new} new</span>')
        if n_removed:
            parts.append(f'<span style="color:#721c24;font-weight:600;">−{n_removed} removed</span>')
        if parts:
            change_summary = f'<p style="margin:4px 0 12px 0;">Changes since last run: {", ".join(parts)}</p>'
        else:
            change_summary = '<p style="margin:4px 0 12px 0;color:#555;">No changes since last run.</p>'
    if coverage_warning:
        # A failed database makes its papers look "removed"; say so up front.
        change_summary = (
            '<p style="margin:4px 0 12px 0;padding:8px 10px;background:#fff7ed;border:1px solid #fdba74;'
            'border-radius:4px;color:#9a3412;"><strong>Incomplete coverage:</strong> '
            f'{html.escape(coverage_warning)}. Papers from these databases may appear as removed.</p>'
            + change_summary
        )

    # Removed papers section
    removed_html = ""
    if removed_docs and not is_first_run:
        removed_rows = ""
        for doc in removed_docs:
            title = html.escape(doc.get("title", "(no title)"))
            year = html.escape(str(doc.get("year") or ""))
            url = doc.get("url") or ""
            doi = doc.get("doi") or ""
            link = html.escape(_safe_email_link(f"https://doi.org/{doi}" if doi else url), quote=True)
            title_cell = f'<a href="{link}" style="color:#721c24;text-decoration:none;">{title}</a>' if link else title
            removed_rows += (
                f'<tr><td style="padding:5px 8px;border-bottom:1px solid #f5c6cb;">{title_cell}'
                f'{"&nbsp;&nbsp;<span style=\'color:#888;font-size:0.9em;\'>(" + year + ")</span>" if year else ""}'
                f'</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f5c6cb;color:#721c24;white-space:nowrap;">{html.escape(doc.get("source",""))}</td></tr>'
            )
        removed_html = f"""
        <h3 style="color:#721c24;margin-top:24px;">Removed since last run ({len(removed_docs)})</h3>
        <table style="border-collapse:collapse;width:100%;background:#fff3f3;">
          <thead><tr>
            <th style="text-align:left;padding:5px 8px;background:#f8d7da;">Title (Year)</th>
            <th style="text-align:left;padding:5px 8px;background:#f8d7da;">Source</th>
          </tr></thead>
          <tbody>{removed_rows}</tbody>
        </table>"""

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;">
    <h2 style="color:#1a2a4a;">Review Sentinel Prime &mdash; Scheduled Run: {html.escape(search_name)}</h2>
    <p style="margin:4px 0;"><strong>Run timestamp:</strong> {run_timestamp}</p>
    <p style="margin:4px 0;">Your saved search found <strong>{num_found}</strong> results. Top {len(sample)} shown below:</p>
    {change_summary}
    <table style="border-collapse:collapse;width:100%;">
      <thead><tr>
        <th style="text-align:left;padding:6px 8px;background:#f5f5f5;">#</th>
        <th style="text-align:left;padding:6px 8px;background:#f5f5f5;">Title (Year)</th>
        <th style="text-align:left;padding:6px 8px;background:#f5f5f5;">Source</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    {more_note}
    {removed_html}
    <p style="color:#aaa;font-size:0.85em;margin-top:16px;">Sent by Review Sentinel Prime &bull; {run_timestamp}</p>
    </body></html>
    """
    # Build CSV attachment with full results list
    csv_fields = ["#", "Status", "Title", "Year", "Authors", "Source", "Journal", "DOI", "URL", "Abstract", "Keywords", "DocType"]
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=csv_fields, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for idx, doc in enumerate(docs, start=1):
        key = _doc_key(doc)
        if is_first_run:
            status = "first run"
        elif key in new_doc_keys:
            status = "new"
        else:
            status = "existing"
        writer.writerow({
            "#": idx,
            "Status": status,
            "Title": _csv_safe(doc.get("title", "")),
            "Year": doc.get("year", ""),
            "Authors": _csv_safe("; ".join(doc.get("authors") or [])),
            "Source": _csv_safe(doc.get("source", "")),
            "Journal": _csv_safe(doc.get("journal", "")),
            "DOI": _csv_safe(doc.get("doi", "")),
            "URL": _csv_safe(doc.get("url", "")),
            "Abstract": _csv_safe(doc.get("abstract", "")),
            "Keywords": _csv_safe("; ".join(doc.get("keywords") or [])),
            "DocType": _csv_safe(doc.get("docType", "")),
        })
    # Also append removed papers at the bottom
    for doc in removed_docs:
        writer.writerow({
            "#": "",
            "Status": "removed",
            "Title": _csv_safe(doc.get("title", "")),
            "Year": doc.get("year", ""),
            "Authors": _csv_safe("; ".join(doc.get("authors") or [])),
            "Source": _csv_safe(doc.get("source", "")),
            "Journal": _csv_safe(doc.get("journal", "")),
            "DOI": _csv_safe(doc.get("doi", "")),
            "URL": _csv_safe(doc.get("url", "")),
            "Abstract": _csv_safe(doc.get("abstract", "")),
            "Keywords": _csv_safe("; ".join(doc.get("keywords") or [])),
            "DocType": _csv_safe(doc.get("docType", "")),
        })
    csv_bytes = csv_buf.getvalue().encode("utf-8-sig")  # utf-8-sig adds BOM for Excel compatibility
    csv_filename = f"{search_name.replace(' ', '_')}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M')}.csv"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[Review Sentinel Prime] Scheduled run: {search_name} — {num_found} results"
    msg["From"] = from_addr
    msg["To"] = to_address
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    attachment = MIMEBase("text", "csv", name=csv_filename)
    attachment.set_payload(csv_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=csv_filename)
    msg.attach(attachment)

    try:
        _smtp_send(to_address, msg)
    except Exception as exc:
        app.logger.warning("Failed to send success notification email to %s: %s", to_address, exc)


def send_failure_email(to_address, search_config, error_msg):
    """Send a scheduled-run failure alert email."""
    cfg = get_smtp_config()
    if not cfg["host"]:
        return
    from_addr = cfg["from_addr"] or cfg["user"] or "reviewsentinel@localhost"

    search_name = html.escape(search_config.get("name", search_config["id"]))
    sched = search_config.get("schedule", {})
    sched_text = "Manual" if sched.get("type") == "manual" else f"Every {sched.get('value', 1)} {sched.get('type', '')}"
    databases = html.escape(", ".join(search_config.get("databases", [])) or "—")
    query = html.escape(search_config.get("query", "—"))
    error_msg = html.escape(str(error_msg))
    run_time = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:640px;">
    <h2 style="color:#c0392b;">&#9888; Review Sentinel Prime &mdash; Scheduled Run Failed</h2>
    <table style="border-collapse:collapse;width:100%;margin-bottom:1rem;">
      <tr><td style="padding:6px 10px;background:#fdf2f2;font-weight:600;width:160px;">Search name</td>
          <td style="padding:6px 10px;background:#fdf2f2;">{search_name}</td></tr>
      <tr><td style="padding:6px 10px;">Schedule</td>
          <td style="padding:6px 10px;">{sched_text}</td></tr>
      <tr><td style="padding:6px 10px;background:#fdf2f2;">Databases</td>
          <td style="padding:6px 10px;background:#fdf2f2;">{databases}</td></tr>
      <tr><td style="padding:6px 10px;">Query</td>
          <td style="padding:6px 10px;"><code>{query}</code></td></tr>
      <tr><td style="padding:6px 10px;background:#fdf2f2;">Failed at</td>
          <td style="padding:6px 10px;background:#fdf2f2;">{run_time}</td></tr>
      <tr><td style="padding:6px 10px;color:#c0392b;font-weight:600;">Error</td>
          <td style="padding:6px 10px;color:#c0392b;font-family:monospace;">{error_msg}</td></tr>
    </table>
    <p style="color:#555;">The search is still active and will retry automatically at the next scheduled interval.</p>
    <p><a href="{os.environ.get('APP_BASE_URL', 'http://localhost:5001')}/config" style="color:#2471a3;">&#128279; Open Saved Searches</a></p>
    <p style="color:#aaa;font-size:0.85em;">Sent by Review Sentinel Prime at {run_time}</p>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Review Sentinel Prime] Scheduled run FAILED: {search_name}"
    msg["From"] = from_addr
    msg["To"] = to_address
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        _smtp_send(to_address, msg)
    except Exception as exc:
        app.logger.warning("Failed to send failure notification email to %s: %s", to_address, exc)


def run_saved_search(search_id):
    """Execute a saved search and store results."""
    try:
        _run_saved_search_inner(search_id)
    except Exception as exc:
        app.logger.error("Scheduled run failed for search %s: %s", search_id, exc, exc_info=True)
        try:
            searches = load_searches()
            search_config = next((s for s in searches if s["id"] == search_id), None)
            if search_config:
                search_config["lastRun"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
                search_config["lastRunError"] = str(exc)
                save_searches(searches)
                notify_email = search_config.get("notifyEmail", "")
                if notify_email:
                    send_failure_email(notify_email, search_config, str(exc))
        except Exception:
            app.logger.error("Failed to record lastRunError for search %s", search_id, exc_info=True)
    finally:
        # Monthly schedules use one-shot "date" triggers; re-arm the next
        # occurrence anchored to the lastRun timestamp just written.
        try:
            search_config = next((s for s in load_searches() if s["id"] == search_id), None)
            if search_config and search_config.get("schedule", {}).get("type") == "months":
                schedule_search(search_config)
        except Exception:
            app.logger.warning("Failed to re-arm monthly schedule for search %s", search_id, exc_info=True)


def _run_saved_search_inner(search_id):
    """Inner implementation — called by run_saved_search with error handling."""
    searches = load_searches()
    search_config = next((s for s in searches if s["id"] == search_id), None)
    if not search_config:
        return

    keywords = normalize_boolean_operators(search_config["query"])
    rows = 10000  # same retrieval ceiling as ad-hoc searches, so scheduled re-runs are comparable
    databases = search_config.get("databases", ["hal", "arxiv"])
    filters = search_config.get("filters", {})

    run = _execute_search(keywords, databases, rows, filters)
    total_found = run["total_found"]
    raw_docs = run["raw_docs"]
    filtered_docs = run["docs"]
    prisma = run["prisma"]
    excluded_docs = run["excluded_docs"]
    db_counts = run["db_counts"]

    # Compute diff against previous run before overwriting results
    prev_docs = load_previous_results(search_id)
    prev_keys = {_doc_key(d) for d in prev_docs if _doc_key(d)}
    curr_keys = {_doc_key(d) for d in filtered_docs if _doc_key(d)}
    new_doc_keys = curr_keys - prev_keys
    removed_docs = [d for d in prev_docs if _doc_key(d) and _doc_key(d) not in curr_keys]
    is_first_run = len(prev_docs) == 0
    coverage_warning = _coverage_warning(db_counts)

    save_search_run(search_id, {
        "numFound": total_found,
        "docs": filtered_docs,
        "query": keywords,
        "databases": databases,
        "filters": filters,
        "rawDocs": raw_docs,
        "excluded_docs": excluded_docs,
        "prisma": prisma,
        "dbCounts": db_counts,
    })

    # Send notification email if configured
    notify_email = search_config.get("notifyEmail", "")
    if notify_email:
        send_notification_email(
            notify_email,
            search_config.get("name", search_id),
            total_found,
            filtered_docs,
            new_doc_keys=new_doc_keys,
            removed_docs=removed_docs,
            is_first_run=is_first_run,
            coverage_warning=coverage_warning,
        )

    # Update lastRun timestamp and clear any previous error
    searches = load_searches()
    for s in searches:
        if s["id"] == search_id:
            s["lastRun"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
            s["lastRunCount"] = len(filtered_docs)
            s.pop("lastRunError", None)  # clear error from any previous failed run
            if coverage_warning:
                s["lastRunWarning"] = coverage_warning
            else:
                s.pop("lastRunWarning", None)
            break
    save_searches(searches)



def _add_months(dt, months):
    """Return dt shifted forward by N calendar months, clamping the day."""
    import calendar
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _interval_next_run(search_config, delta):
    """First fire time for an interval schedule, anchored to lastRun.

    Without an anchor, APScheduler's in-memory interval trigger first fires a
    full interval after *registration* — so every app restart resets the
    countdown and a daily job on a nightly-rebooted laptop never fires. If
    the anchored time is already past (the app was down when the run was
    due), fire shortly after startup instead of waiting a full interval.
    """
    last_run = search_config.get("lastRun")
    if not last_run:
        return None  # never run yet: default first fire (now + interval) is fine
    try:
        anchor = datetime.fromisoformat(str(last_run).rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    next_run = anchor + delta
    now = datetime.now(timezone.utc)
    if next_run <= now:
        next_run = now + timedelta(seconds=30)
    return next_run


def schedule_search(search_config):
    """Schedule or unschedule a search based on its config."""
    job_id = f"search_{search_config['id']}"
    # Remove existing job if any
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    schedule = search_config.get("schedule", {})
    if schedule.get("type") == "manual":
        return

    interval_type = schedule.get("type")  # hours, days, months
    interval_value = schedule.get("value", 1)

    if interval_type in ("hours", "days"):
        delta = timedelta(hours=interval_value) if interval_type == "hours" else timedelta(days=interval_value)
        kwargs = {"coalesce": True, "misfire_grace_time": 3600}
        next_run = _interval_next_run(search_config, delta)
        if next_run:
            kwargs["next_run_time"] = next_run
        scheduler.add_job(run_saved_search, "interval",
                          hours=interval_value if interval_type == "hours" else 0,
                          days=interval_value if interval_type == "days" else 0,
                          id=job_id, args=[search_config["id"]], **kwargs)
    elif interval_type == "months":
        # APScheduler has no "every N calendar months" interval, and a cron
        # month=*/N trigger fires on calendar months 1, 1+N, ... at 00:00 on
        # day 1 — not "N months after the last run". Anchor to lastRun (or
        # now) instead; run_saved_search re-arms the next occurrence.
        anchor = datetime.now(timezone.utc)
        last_run = search_config.get("lastRun")
        if last_run:
            try:
                anchor = datetime.fromisoformat(last_run.rstrip("Z")).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        next_run = _add_months(anchor, interval_value)
        now = datetime.now(timezone.utc)
        while next_run <= now:
            next_run = _add_months(next_run, interval_value)
        scheduler.add_job(run_saved_search, "date", run_date=next_run,
                          id=job_id, args=[search_config["id"]])


def load_all_schedules():
    """Load all saved searches and schedule them."""
    for s in load_searches():
        if s.get("schedule", {}).get("type") != "manual":
            schedule_search(s)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/config")
def config_page():
    return render_template("config.html")


@app.route("/about")
def about_page():
    return render_template("about.html")


@app.route("/survey")
def survey_page():
    return render_template("survey.html")


@app.route("/api/submit-survey", methods=["POST"])
def submit_survey():
    """Receive survey JSON, relay it to Google Forms, and email a CSV copy."""
    SURVEY_RECIPIENT = os.environ.get("SURVEY_RECIPIENT_EMAIL", "rsp.researchfeedback@gmail.com")

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid payload"}), 400

    # Relay first so responses are captured even when SMTP is unconfigured.
    relayed, relay_error = _relay_survey_to_google_form(data)

    if not SURVEY_RECIPIENT:
        if relayed:
            return jsonify({"ok": True, "googleForm": True, "emailSent": False})
        return jsonify({"error": relay_error or "Survey recipient not configured on the server."}), 503

    submitted_at = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")

    # ── Build CSV in memory ──────────────────────────────────────────────────
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)

    # Participant background
    writer.writerow(["Section", "Question ID", "Question", "Response"])
    bg = data.get("background", {})
    writer.writerow(["A – Participant Background", "A1", "Role", bg.get("role", "")])
    writer.writerow(["A – Participant Background", "A2", "Years of experience", bg.get("experience", "")])
    writer.writerow(["A – Participant Background", "A3", "SLRs conducted", bg.get("slrCount", "")])
    writer.writerow(["A – Participant Background", "A4", "Research field", bg.get("field", "")])

    # Likert questions
    likert_meta = {
        "sus1":  ("B – SUS", "B1",  "Would use RSP frequently"),
        "sus2":  ("B – SUS", "B2",  "Unnecessarily complex"),
        "sus3":  ("B – SUS", "B3",  "Easy to use"),
        "sus4":  ("B – SUS", "B4",  "Needs tech support"),
        "sus5":  ("B – SUS", "B5",  "Well integrated"),
        "sus6":  ("B – SUS", "B6",  "Too much inconsistency"),
        "sus7":  ("B – SUS", "B7",  "Quick to learn"),
        "sus8":  ("B – SUS", "B8",  "Cumbersome"),
        "sus9":  ("B – SUS", "B9",  "Felt confident"),
        "sus10": ("B – SUS", "B10", "Needed to learn a lot"),
        "tam1":  ("C – TAM", "C1",  "Improves performance"),
        "tam2":  ("C – TAM", "C2",  "Increases productivity"),
        "tam3":  ("C – TAM", "C3",  "Makes SLR easier"),
        "tam4":  ("C – TAM", "C4",  "Useful for research"),
        "tam5":  ("C – TAM", "C5",  "Easy to learn"),
        "tam6":  ("C – TAM", "C6",  "Easy to control"),
        "tam7":  ("C – TAM", "C7",  "Clear interaction"),
        "tam8":  ("C – TAM", "C8",  "Intend to continue"),
        "imp1":  ("D – Impact", "D1", "Improves transparency"),
        "imp2":  ("D – Impact", "D2", "Helps reproducibility"),
        "imp3":  ("D – Impact", "D3", "PRISMA improves reporting"),
        "imp4":  ("D – Impact", "D4", "Replication meets open science"),
        "imp5":  ("D – Impact", "D5", "Validation verifies completeness"),
        "imp6":  ("D – Impact", "D6", "Reduces selection bias"),
        "imp7":  ("D – Impact", "D7", "Multi-DB improves coverage"),
        "feat1": ("E – Features", "E1", "Multi-database search"),
        "feat2": ("E – Features", "E2", "Inclusion/Exclusion filters"),
        "feat3": ("E – Features", "E3", "Validation papers"),
        "feat4": ("E – Features", "E4", "PRISMA auto-generation"),
        "feat5": ("E – Features", "E5", "Replication export"),
    }
    likert = data.get("likert", {})
    for key, (section, qid, label) in likert_meta.items():
        writer.writerow([section, qid, label, likert.get(key, "")])

    # Open questions
    open_meta = {
        "open1": ("F – Open Feedback", "F1", "Most useful aspects"),
        "open2": ("F – Open Feedback", "F2", "Difficulties encountered"),
        "open3": ("F – Open Feedback", "F3", "Comparison to previous approach"),
        "open4": ("F – Open Feedback", "F4", "Suggested improvements"),
        "open5": ("F – Open Feedback", "F5", "Additional comments"),
    }
    open_fb = data.get("open", {})
    for key, (section, qid, label) in open_meta.items():
        writer.writerow([section, qid, label, open_fb.get(key, "")])

    csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility

    # ── Build HTML email body ─────────────────────────────────────────────────
    def _row(label, value):
        return (
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;'
            f'font-weight:600;color:#555;width:220px;white-space:nowrap;">{label}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #eee;color:#222;">{value or "<em style=\'color:#aaa\'>not provided</em>"}</td></tr>'
        )

    def _section_header(title):
        return (
            f'<tr><td colspan="2" style="background:#1a2a4a;color:#fff;padding:8px 12px;'
            f'font-size:0.9rem;font-weight:700;letter-spacing:0.04em;">{title}</td></tr>'
        )

    likert_rows_html = ""
    section_order = ["B – SUS", "C – TAM", "D – Impact", "E – Features"]
    section_labels = {
        "B – SUS":      "Section B – System Usability Scale (SUS, 1–5)",
        "C – TAM":      "Section C – Perceived Usefulness &amp; Ease of Use (TAM, 1–7)",
        "D – Impact":   "Section D – Impact on Research Quality (1–7)",
        "E – Features": "Section E – Feature-Specific Evaluation (1–5)",
    }
    grouped = {}
    for key, (section, qid, label) in likert_meta.items():
        grouped.setdefault(section, []).append((qid, label, likert.get(key, "—")))

    for sec in section_order:
        if sec not in grouped:
            continue
        likert_rows_html += _section_header(section_labels[sec])
        for qid, label, val in grouped[sec]:
            likert_rows_html += _row(f"{qid} &nbsp;{label}", val)

    open_rows_html = _section_header("Section F – Open Feedback")
    for key, (_, qid, label) in open_meta.items():
        val = open_fb.get(key, "") or "<em style='color:#aaa'>no answer</em>"
        # preserve newlines in open text
        val_fmt = val.replace("\n", "<br>") if val else val
        open_rows_html += _row(f"{qid} &nbsp;{label}", val_fmt)

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fa;padding:32px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:#1a2a4a;padding:28px 32px;">
            <p style="margin:0;font-size:1.5rem;font-weight:700;color:#fff;letter-spacing:0.02em;">
              Review Sentinel Prime
            </p>
            <p style="margin:4px 0 0;font-size:0.9rem;color:#a8bcd4;">
              Research Evaluation Survey &mdash; New Response Received
            </p>
          </td>
        </tr>

        <!-- Intro banner -->
        <tr>
          <td style="background:#eaf4fb;padding:14px 32px;border-bottom:1px solid #d0e8f5;">
            <p style="margin:0;font-size:0.88rem;color:#2c6e8a;">
              A participant has completed the RSP usability &amp; usefulness survey.
              The full response is attached as <strong>survey_response.csv</strong>.
              Submitted on <strong>{submitted_at}</strong>.
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #eee;border-radius:6px;border-collapse:collapse;font-size:0.88rem;">

              {_section_header("Section A – Participant Background")}
              {_row("Role",                bg.get("role",""))}
              {_row("Years of experience", bg.get("experience",""))}
              {_row("SLRs conducted",      bg.get("slrCount",""))}
              {_row("Research field",      bg.get("field",""))}

              {likert_rows_html}
              {open_rows_html}

            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8f9fa;padding:16px 32px;border-top:1px solid #eee;text-align:center;">
            <p style="margin:0;font-size:0.78rem;color:#aaa;">
              This message was automatically generated by Review Sentinel Prime &mdash;
              do not reply directly to this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    # ── Compose MIME message ──────────────────────────────────────────────────
    cfg = get_smtp_config()
    from_addr = cfg.get("from_addr") or cfg.get("user") or "reviewsentinel@localhost"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[Review Sentinel Prime] Evaluation Survey – New Response [{submitted_at}]"
    msg["From"]    = from_addr
    msg["To"]      = SURVEY_RECIPIENT

    # HTML part
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    # CSV attachment
    part = MIMEBase("text", "csv", charset="utf-8")
    part.set_payload(csv_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", 'attachment; filename="survey_response.csv"')
    msg.attach(part)

    try:
        sent = _smtp_send(SURVEY_RECIPIENT, msg)
    except Exception as exc:
        if relayed:
            return jsonify({"ok": True, "googleForm": True, "emailSent": False, "emailError": str(exc)})
        return jsonify({"error": str(exc)}), 500

    if not sent:
        if relayed:
            return jsonify({"ok": True, "googleForm": True, "emailSent": False})
        return jsonify({"error": relay_error or "SMTP not configured on the server."}), 503

    return jsonify({"ok": True, "googleForm": relayed, "emailSent": True})


@app.route("/feedback")
def feedback_page():
    return render_template("feedback.html")


@app.route("/api/submit-feedback", methods=["POST"])
def submit_feedback():
    """Receive feedback JSON, relay it to Google Forms, and email a copy."""
    FEEDBACK_RECIPIENT = os.environ.get("SURVEY_RECIPIENT_EMAIL", "rsp.researchfeedback@gmail.com")

    # Multipart (with attachments) from the form; JSON kept for older clients.
    if request.content_length and request.content_length > FEEDBACK_MAX_TOTAL_BYTES + 1024 * 1024:
        return jsonify({"error": "Attachments are too large."}), 413
    attachments = []
    if request.mimetype == "multipart/form-data":
        data = request.form.to_dict()
        try:
            attachments = _collect_feedback_attachments(request.files.getlist("attachments"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid payload"}), 400

    submitted_at = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")
    fb_type    = data.get("type", "bug")
    title      = data.get("title", "(no title)")
    description= data.get("description", "")
    steps      = data.get("steps", "")
    expected   = data.get("expected", "")
    actual     = data.get("actual", "")
    priority   = data.get("priority", "medium").upper()
    contact    = data.get("contactEmail", "")

    # Attachments: upload to Drive (links go into the Google Form); keep a
    # local copy if that is unavailable so nothing is lost.
    attachment_info = None
    if attachments:
        submission_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        names = [a["name"] for a in attachments]
        links_text, upload_error = _upload_feedback_attachments(attachments, submission_id, fb_type, title)
        attachment_info = {"count": len(attachments), "names": names, "uploaded": bool(links_text)}
        if links_text:
            data["attachments"] = links_text
        else:
            data["attachments"] = (f"{len(names)} attachment(s), not uploaded: " + ", ".join(names))
            attachment_info["localFolder"] = _save_feedback_attachments_locally(attachments, submission_id)
            if upload_error:
                attachment_info["uploadError"] = upload_error

    # Relay first so submissions are captured even when SMTP is unconfigured.
    relayed, relay_error = _relay_feedback_to_google_form(data)

    # Everything below is HTML email content: escape user input.
    esc = lambda s: html.escape(str(s or ""))
    title_html, description, steps, expected, actual, contact = (
        esc(title), esc(description), esc(steps), esc(expected), esc(actual), esc(contact))
    priority = esc(priority)

    type_label = "Bug Report" if fb_type == "bug" else "Feature Request"
    type_color = "#c0392b" if fb_type == "bug" else "#27ae60"

    def _row(label, value, pre=False):
        val_html = (value or "<em style='color:#aaa'>not provided</em>")
        if pre and value:
            val_html = f"<pre style='margin:0;white-space:pre-wrap;font-family:inherit;font-size:0.88rem;'>{value}</pre>"
        return (
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee;font-weight:600;'
            f'color:#555;width:180px;vertical-align:top;white-space:nowrap;">{label}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #eee;color:#222;">{val_html}</td></tr>'
        )

    bug_rows = ""
    if fb_type == "bug":
        bug_rows = (
            f'<tr><td colspan="2" style="background:#1a2a4a;color:#fff;padding:8px 12px;'
            f'font-size:0.9rem;font-weight:700;">Bug Details</td></tr>'
            + _row("Steps to Reproduce", steps, pre=True)
            + _row("Expected Behaviour", expected)
            + _row("Actual Behaviour", actual)
        )

    attachments_row = ""
    if attachment_info:
        attachments_row = _row("Attachments", esc(data.get("attachments", "")), pre=True)

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fa;padding:32px 0;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:#1a2a4a;padding:24px 32px;">
            <p style="margin:0;font-size:1.4rem;font-weight:700;color:#fff;">Review Sentinel Prime</p>
            <p style="margin:4px 0 0;font-size:0.88rem;color:#a8bcd4;">
              New <span style="color:{type_color};font-weight:700;">{type_label}</span> received &mdash; {submitted_at}
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #eee;border-radius:6px;border-collapse:collapse;font-size:0.88rem;">

              <tr><td colspan="2" style="background:#1a2a4a;color:#fff;padding:8px 12px;font-size:0.9rem;font-weight:700;">Submission</td></tr>
              {_row("Type", f'<span style="background:{type_color};color:#fff;padding:2px 8px;border-radius:3px;font-size:0.8rem;font-weight:700;">{type_label}</span>')}
              {_row("Priority", priority)}
              {_row("Title", title_html)}
              {_row("Description", description, pre=True)}
              {bug_rows}
              {attachments_row}
              <tr><td colspan="2" style="background:#1a2a4a;color:#fff;padding:8px 12px;font-size:0.9rem;font-weight:700;">Contact</td></tr>
              {_row("Reporter email", contact or "anonymous")}

            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8f9fa;padding:14px 32px;border-top:1px solid #eee;text-align:center;">
            <p style="margin:0;font-size:0.78rem;color:#aaa;">
              Automatically generated by Review Sentinel Prime &mdash; do not reply directly to this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    cfg = get_smtp_config()
    from_addr = cfg.get("from_addr") or cfg.get("user") or "reviewsentinel@localhost"

    # mixed = the HTML body plus one part per attached file.
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[Review Sentinel Prime] {type_label}: {title}"
    msg["From"]    = from_addr
    msg["To"]      = FEEDBACK_RECIPIENT
    body_part = MIMEMultipart("alternative")
    body_part.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(body_part)
    for a in attachments:
        maintype, subtype = a["mime"].split("/", 1)
        part = MIMEBase(maintype, subtype)
        part.set_payload(a["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=a["name"])
        msg.attach(part)

    def _reply(payload, status=200):
        if attachment_info:
            payload["attachments"] = attachment_info
        return jsonify(payload), status

    if not FEEDBACK_RECIPIENT:
        if relayed:
            return _reply({"ok": True, "googleForm": True, "emailSent": False})
        return _reply({"error": relay_error or "Feedback recipient not configured on the server."}, 503)

    try:
        sent = _smtp_send(FEEDBACK_RECIPIENT, msg)
    except Exception as exc:
        if relayed:
            return _reply({"ok": True, "googleForm": True, "emailSent": False, "emailError": str(exc)})
        return _reply({"error": str(exc)}, 500)

    if not sent:
        if relayed:
            return _reply({"ok": True, "googleForm": True, "emailSent": False})
        return _reply({"error": relay_error or "SMTP not configured on the server."}, 503)

    return _reply({"ok": True, "googleForm": relayed, "emailSent": True})


@app.route("/charts")
def charts_page():
    return render_template("charts.html")


@app.route("/results")
def results_page():
    return render_template("results.html")


@app.route("/manual")
def manual_page():
    return render_template("manual.html")


@app.route("/screening")
def screening_page():
    return render_template("screening.html")


@app.route("/full-text")
def full_text_page():
    return render_template("full_text.html")


@app.route("/dual-screening")
def dual_screening_page():
    return render_template("dual_screening.html")


@app.route("/prisma")
def prisma_page():
    run_id = request.args.get("runId")
    search_id = request.args.get("searchId")
    if not run_id and not search_id:
        return render_template("prisma.html", selected_run=None, selected_search_id=None)
    if not run_id or not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return "Invalid run or search ID", 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return error, status
        run = target[1]
    return render_template("prisma.html", selected_run=run, selected_search_id=search_id)


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


# --- API: IEEE API Key ---

@app.route("/api/ieee-key", methods=["GET"])
def get_ieee_key():
    key = get_ieee_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/ieee-key", methods=["POST"])
def set_ieee_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(IEEE_API_KEY_FILE), exist_ok=True)
    with open(IEEE_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


# --- API: Scopus API Key ---

@app.route("/api/scopus-key", methods=["GET"])
def get_scopus_key():
    key = get_scopus_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/scopus-key", methods=["POST"])
def set_scopus_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(SCOPUS_API_KEY_FILE), exist_ok=True)
    with open(SCOPUS_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


# --- API: Inspec (Engineering Village) API Key ---

@app.route("/api/inspec-key", methods=["GET"])
def get_inspec_key():
    key = get_inspec_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/inspec-key", methods=["POST"])
def set_inspec_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(INSPEC_API_KEY_FILE), exist_ok=True)
    with open(INSPEC_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


# --- API: ScienceDirect API Key ---

@app.route("/api/sciencedirect-key", methods=["GET"])
def get_sciencedirect_key():
    key = get_sciencedirect_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/sciencedirect-key", methods=["POST"])
def set_sciencedirect_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    _save_api_key(SCIENCEDIRECT_API_KEY_FILE, key)
    return jsonify({"ok": True})


# --- API: Elsevier Institutional Token (shared by Scopus & ScienceDirect) ---
# Optional: only required when requests originate outside the institution's
# IP range. Added to requests alongside the API key, never in place of it.

@app.route("/api/elsevier-insttoken", methods=["GET"])
def get_elsevier_insttoken_route():
    token = get_elsevier_insttoken()
    if token:
        return jsonify({"configured": True, "key": token[:4] + "..." + token[-4:] if len(token) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/elsevier-insttoken", methods=["POST"])
def set_elsevier_insttoken():
    data = request.get_json()
    token = data.get("key", "").strip()
    _save_api_key(ELSEVIER_INSTTOKEN_FILE, token)
    return jsonify({"ok": True})


@app.route("/api/springer-key", methods=["GET"])
def get_springer_key():
    key = get_springer_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/springer-key", methods=["POST"])
def set_springer_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(SPRINGER_API_KEY_FILE), exist_ok=True)
    with open(SPRINGER_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


@app.route("/api/pubmed-key", methods=["GET"])
def get_pubmed_key():
    key = get_pubmed_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/pubmed-key", methods=["POST"])
def set_pubmed_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(PUBMED_API_KEY_FILE), exist_ok=True)
    with open(PUBMED_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


@app.route("/api/openalex-key", methods=["GET"])
def get_openalex_key():
    key = get_openalex_api_key()
    if key:
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/openalex-key", methods=["POST"])
def set_openalex_key():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    _save_api_key(OPENALEX_API_KEY_FILE, key)
    return jsonify({"ok": True})


# --- API: ACM Digital Library API Key ---

@app.route("/api/acm-key", methods=["GET"])
def get_acm_key():
    key = get_acm_api_key()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "key": ""})


@app.route("/api/acm-key", methods=["POST"])
def set_acm_key():
    data = request.get_json()
    key = data.get("key", "").strip()
    os.makedirs(os.path.dirname(ACM_API_KEY_FILE), exist_ok=True)
    with open(ACM_API_KEY_FILE, "w") as f:
        f.write(key)
    return jsonify({"ok": True})


# --- API: EBSCO (EDS) Credentials ---
@app.route("/api/ebsco-key", methods=["GET"])
def get_ebsco_key():
    creds = get_ebsco_credentials()
    configured = bool(creds["userId"] and creds["password"] and creds["profile"])
    user_id = creds["userId"]
    return jsonify({
        "configured": configured,
        # Profile is not a secret and the UI needs it; the password is never returned.
        "userId":  user_id[:2] + "..." + user_id[-2:] if len(user_id) > 6 else ("****" if user_id else ""),
        "profile": creds["profile"],
        "key":     "****" if configured else "",
    })


@app.route("/api/ebsco-key", methods=["POST"])
def set_ebsco_key():
    data = request.get_json() or {}
    user_id  = (data.get("userId") or "").strip()
    password = (data.get("password") or "").strip()
    profile  = (data.get("profile") or "").strip()

    if not user_id and not password and not profile:
        if os.path.exists(EBSCO_CREDENTIALS_FILE):
            os.remove(EBSCO_CREDENTIALS_FILE)
        return jsonify({"ok": True})

    # Blank password on an update means "keep the stored one" — the UI never
    # receives the real password back, so it cannot resubmit it.
    if not password:
        password = get_ebsco_credentials()["password"]

    if not (user_id and password and profile):
        return jsonify({
            "ok": False,
            "error": "EBSCO requires all three values: User ID, Password and Profile ID.",
        }), 400

    _save_ebsco_credentials({"userId": user_id, "password": password, "profile": profile})
    return jsonify({"ok": True})


# --- API: Web of Science API Key ---

@app.route("/api/wos-key", methods=["GET"])
def get_wos_key():
    key = get_wos_api_key()
    edition = get_wos_edition()
    if key:
        # Never return the full key — a masked preview is enough for the UI,
        # and any page/process that can reach the port must not get secrets.
        return jsonify({"configured": True, "edition": edition,
                        "key": key[:4] + "..." + key[-4:] if len(key) > 8 else "****"})
    return jsonify({"configured": False, "edition": edition, "key": ""})


@app.route("/api/wos-key", methods=["POST"])
def set_wos_key():
    data = request.get_json()
    # An absent "key" field means "edition change only" — the stored key is
    # never sent to the browser, so the UI cannot resubmit it.
    if "key" in data:
        _save_api_key(WOS_API_KEY_FILE, data.get("key", "").strip())
    if "edition" in data:
        edition = "expanded" if str(data.get("edition", "")).strip().lower() == "expanded" else "starter"
        _save_api_key(WOS_EDITION_FILE, edition)
    return jsonify({"ok": True})


# --- API: SMTP Configuration ---

@app.route("/api/smtp-config", methods=["GET"])
def get_smtp_config_route():
    cfg = get_smtp_config()
    return jsonify({
        "configured": bool(cfg["host"]),
        "host":      cfg["host"],
        "port":      cfg["port"],
        "user":      cfg["user"],
        "from_addr": cfg["from_addr"],
        # never return the password to the frontend
    })


@app.route("/api/smtp-config", methods=["POST"])
def set_smtp_config_route():
    data = request.get_json()
    existing = {}
    if os.path.exists(SMTP_CONFIG_FILE):
        with open(SMTP_CONFIG_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    # An empty password normally means "keep the stored one" (the UI never
    # echoes it back), but clear=true must genuinely erase the credential —
    # the Clear button promises exactly that.
    clear = bool(data.get("clear"))
    new_password = data.get("password", "").strip()
    if clear:
        password = ""
    else:
        password = new_password if new_password else existing.get("password", "")
    try:
        port = int(data.get("port", 587) or 587)
    except (TypeError, ValueError):
        port = 587
    cfg = {
        "host":      data.get("host", "").strip(),
        "port":      port,
        "user":      data.get("user", "").strip(),
        "password":  password,
        "from_addr": data.get("from_addr", "").strip(),
    }
    _write_json_atomic(SMTP_CONFIG_FILE, cfg)
    return jsonify({"ok": True})


@app.route("/api/smtp-config/test", methods=["POST"])
def test_smtp_config_route():
    data = request.get_json()
    to_address = data.get("to", "").strip()
    if not to_address:
        return jsonify({"ok": False, "error": "No recipient address provided."}), 400
    cfg = get_smtp_config()
    if not cfg["host"]:
        return jsonify({"ok": False, "error": "SMTP host is not configured."}), 400
    from_addr = cfg["from_addr"] or cfg["user"] or "reviewsentinel@localhost"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[Review Sentinel Prime] Test Email"
    msg["From"] = from_addr
    msg["To"] = to_address
    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;">
    <h2 style="color:#27ae60;">&#10004; SMTP Test Successful</h2>
    <p>Your Review Sentinel Prime email configuration is working correctly.</p>
    <p style="color:#aaa;font-size:0.85em;">Sent at {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</p>
    </body></html>
    """
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        _smtp_send(to_address, msg)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/run-retention-settings", methods=["GET"])
def get_run_retention_settings_route():
    settings = get_run_retention_settings()
    return jsonify({
        "ok": True,
        "settings": settings,
        "stats": _run_retention_stats(),
    })


@app.route("/api/run-retention-settings", methods=["POST"])
def set_run_retention_settings_route():
    data = request.get_json() or {}
    settings = save_run_retention_settings(data)
    return jsonify({
        "ok": True,
        "settings": settings,
        "stats": _run_retention_stats(),
    })


@app.route("/api/retrieval-settings", methods=["GET"])
def get_retrieval_settings_route():
    return jsonify({"ok": True, "settings": get_retrieval_settings(),
                    "limits": {"min": RETRIEVAL_CAP_MIN, "max": RETRIEVAL_CAP_MAX}})


@app.route("/api/retrieval-settings", methods=["POST"])
def set_retrieval_settings_route():
    return jsonify({"ok": True, "settings": save_retrieval_settings(request.get_json(silent=True) or {})})


@app.route("/api/run-retention-settings/cleanup", methods=["POST"])
def cleanup_run_retention_route():
    settings = get_run_retention_settings()
    removed_saved = _prune_saved_runs(settings.get("savedKeepLast", 10))
    removed_adhoc = _prune_adhoc_runs(settings.get("adhocKeepLast", 10))
    settings["lastCleanupAt"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    settings = save_run_retention_settings(settings)
    return jsonify({
        "ok": True,
        "removed": {
            "saved": removed_saved,
            "adhoc": removed_adhoc,
            "total": removed_saved + removed_adhoc,
        },
        "settings": settings,
        "stats": _run_retention_stats(),
    })


# --- API: Review Protocol ---
# Captures the review-level facts PRISMA requires but no search can infer:
# research questions, registration, reviewer team, synthesis/RoB methods,
# funding, and competing interests. Feeds the PRISMA checklist and the
# replication-package export.

def _ai_agreement_stats(rows):
    """Reviewer-vs-AI agreement and 3-class Cohen's kappa from export rows."""
    classes = ("include", "exclude", "unsure")
    pairs = [(r.get("ai_suggested"), r.get("decision")) for r in rows
             if r.get("ai_suggested") in classes and r.get("decision") in classes]
    n = len(pairs)
    if not n:
        return None
    agree = sum(1 for a, d in pairs if a == d)
    po = agree / n
    pe = sum((sum(1 for _, d in pairs if d == c) / n) * (sum(1 for a, _ in pairs if a == c) / n)
             for c in classes)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {"n": n, "agree": agree, "pct": round(po * 100), "kappa": round(kappa, 2)}


_ROB_DOMAINS = {
    "RoB 2": ["D1 randomisation", "D2 deviations from intended interventions", "D3 missing outcome data",
              "D4 measurement of the outcome", "D5 selection of the reported result"],
    "ROBINS-I": ["D1 confounding", "D2 selection of participants", "D3 classification of interventions",
                 "D4 deviations from intended interventions", "D5 missing data",
                 "D6 measurement of outcomes", "D7 selection of the reported result"],
    "CASP": ["Q1 clear aims", "Q2 appropriate methodology", "Q3 appropriate design", "Q4 recruitment",
             "Q5 data collection", "Q6 researcher relationship", "Q7 ethics", "Q8 rigorous analysis",
             "Q9 clear findings", "Q10 value of research"],
}


def _assessment_templates(studies, protocol):
    """Blank risk-of-bias (one row per study) and GRADE (one row per outcome) CSVs."""
    tool = (protocol or {}).get("robTool") or ""
    domains = _ROB_DOMAINS.get(tool, ["Domain 1", "Domain 2", "Domain 3", "Domain 4", "Domain 5"])
    rob_fields = ["study_id", "title", "authors", "year", "doi", "tool"] + domains + [
        "overall_judgement", "support_for_judgement", "assessor"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=rob_fields, extrasaction="ignore")
    writer.writeheader()
    for i, s in enumerate(studies or [], 1):
        authors = s.get("authors", "")
        if isinstance(authors, list):
            authors = "; ".join(authors)
        writer.writerow(_csv_safe_row({
            "study_id": f"S{i:03d}", "title": s.get("title", ""), "authors": authors,
            "year": s.get("year", ""), "doi": s.get("doi", ""), "tool": tool or "(specify tool)",
        }))
    rob_csv = out.getvalue()

    outcomes = [o.strip(" -•\t") for o in re.split(r"[\n;]+", (protocol or {}).get("outcomes") or "")]
    outcomes = [o for o in outcomes if o] or ["(outcome 1)"]
    grade_fields = ["outcome", "n_studies", "study_design", "risk_of_bias", "inconsistency",
                    "indirectness", "imprecision", "publication_bias", "certainty", "comments"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=grade_fields)
    writer.writeheader()
    for o in outcomes:
        writer.writerow(_csv_safe_row({f: "" for f in grade_fields} | {"outcome": o}))
    return rob_csv, out.getvalue()


_PROTOCOL_FIELDS = [
    "researchQuestions", "rationale", "outcomes",
    "reviewers", "screeningProcess",
    "registered", "registerName", "registrationNumber", "registrationDate",
    "synthesisMethod", "synthesisNotes",
    "robTool", "robNotes",
    "effectMeasures", "reportingBiasMethod", "certaintyMethod",
    "funding", "competingInterests",
    "heterogeneityMethod", "sensitivityMethod",
]


def _load_protocol():
    protocol = _read_json_file(PROTOCOL_FILE, {})
    return protocol if isinstance(protocol, dict) else {}


@app.route("/api/protocol", methods=["GET"])
def get_protocol_route():
    return jsonify(_load_protocol())


@app.route("/api/protocol", methods=["POST"])
def set_protocol_route():
    data = request.get_json(silent=True) or {}
    protocol = {}
    for field in _PROTOCOL_FIELDS:
        value = data.get(field)
        if field == "reviewers":
            protocol[field] = max(1, _to_int(value, 1))
        elif field == "registered":
            protocol[field] = bool(value)
        else:
            protocol[field] = str(value or "").strip()
    protocol["updatedAt"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    with _data_io_lock:
        _write_json_atomic(PROTOCOL_FILE, protocol)
    return jsonify({"ok": True, "protocol": protocol})


# --- API: AI Screening Assistant (local LLM) ---
# Proposes include/exclude/unsure decisions for title & abstract screening.
# The human reviewer remains the decider: suggestions are never applied
# automatically, and accepted decisions record aiAssisted provenance so
# PRISMA item 8 ("details of automation tools used") can report it.
# Targets a local Ollama instance by default (confidentiality-preserving);
# any OpenAI-compatible chat endpoint works as an alternative. Anthropic
# (Claude) uses its native Messages API via the official SDK instead.

_AI_DEFAULTS = {
    "enabled": False,
    "provider": "ollama",              # "ollama" | "openai"
    "endpoint": "http://localhost:11434",
    "model": "llama3.1",
    "apiKey": "",                       # only for openai-compatible endpoints
    "vendor": "",                       # UI preset id, "" = custom
    "profiles": {},                     # per-vendor {provider, endpoint, model, apiKey}
}


def _load_ai_config():
    cfg = _read_json_file(AI_CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = dict(_AI_DEFAULTS)
    merged.update({k: cfg[k] for k in _AI_DEFAULTS if k in cfg})
    if not isinstance(merged["profiles"], dict):
        merged["profiles"] = {}
    return merged


def _ai_vendor_id(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[a-z0-9_-]{1,32}", value) else "custom"


def _apply_ai_overrides(cfg, overrides):
    """Overlay unsaved form values; reuse the stored key for the chosen vendor."""
    vendor = _ai_vendor_id(overrides.get("vendor", cfg["vendor"]))
    for field in ("provider", "endpoint", "model"):
        if overrides.get(field):
            cfg[field] = str(overrides[field]).strip()
    typed = str(overrides.get("apiKey") or "").strip()
    if typed:
        cfg["apiKey"] = typed
    elif vendor != _ai_vendor_id(cfg["vendor"]):
        cfg["apiKey"] = cfg["profiles"].get(vendor, {}).get("apiKey", "")
    return cfg


@app.route("/api/ai-config", methods=["GET"])
def get_ai_config_route():
    cfg = _load_ai_config()
    key = cfg.pop("apiKey", "")
    cfg["apiKeyConfigured"] = bool(key)  # never return the key itself
    cfg["profiles"] = {
        name: {"endpoint": p.get("endpoint", ""), "model": p.get("model", ""),
               "apiKeyConfigured": bool(p.get("apiKey"))}
        for name, p in cfg["profiles"].items() if isinstance(p, dict)
    }
    return jsonify(cfg)


@app.route("/api/ai-config", methods=["POST"])
def set_ai_config_route():
    data = request.get_json(silent=True) or {}
    existing = _load_ai_config()
    provider = str(data.get("provider", existing["provider"])).strip().lower()
    if provider not in ("ollama", "openai"):
        provider = "ollama"
    vendor = _ai_vendor_id(data.get("vendor", existing["vendor"]))
    profiles = dict(existing["profiles"])
    stored_key = profiles.get(vendor, {}).get("apiKey", "")
    if not stored_key and vendor == _ai_vendor_id(existing["vendor"]):
        stored_key = existing["apiKey"]
    new_key = str(data.get("apiKey", "")).strip()
    cfg = {
        "enabled": bool(data.get("enabled", existing["enabled"])),
        "provider": provider,
        "endpoint": str(data.get("endpoint", existing["endpoint"])).strip().rstrip("/"),
        "model": str(data.get("model", existing["model"])).strip(),
        # empty key means "keep stored"; clear=true erases it
        "apiKey": "" if data.get("clear") else (new_key or stored_key),
        "vendor": vendor,
    }
    profiles[vendor] = {k: cfg[k] for k in ("provider", "endpoint", "model", "apiKey")}
    cfg["profiles"] = profiles
    _write_json_atomic(AI_CONFIG_FILE, cfg)
    safe = {k: v for k, v in cfg.items() if k not in ("apiKey", "profiles")}
    safe["apiKeyConfigured"] = bool(cfg["apiKey"])
    return jsonify({"ok": True, "config": safe})


def _openai_chat_url(endpoint):
    """Resolve the chat-completions URL for an OpenAI-compatible endpoint.

    Vendors mount the API under different prefixes — OpenAI/Anthropic at
    /v1, Gemini at /v1beta/openai, Groq at /openai/v1, LM Studio at /v1 —
    so only bare hosts get the default /v1 prefix appended; an endpoint
    that already carries a path just gets /chat/completions.
    """
    from urllib.parse import urlparse

    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if urlparse(endpoint).path.strip("/"):
        return f"{endpoint}/chat/completions"
    return f"{endpoint}/v1/chat/completions"


def _is_anthropic(cfg):
    """True when the configured service is Anthropic's own API (Claude)."""
    return (_ai_vendor_id(cfg.get("vendor")) == "anthropic"
            or "api.anthropic.com" in str(cfg.get("endpoint", "")))


def _anthropic_client(cfg, timeout):
    """Official Anthropic SDK client. Imported lazily so the rest of the app
    keeps working if the package is missing from an older install."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' Python package is not installed. Run "
            "'pip install -r requirements.txt' and restart the app.") from exc
    return anthropic, anthropic.Anthropic(api_key=cfg.get("apiKey") or None,
                                          timeout=timeout, max_retries=2)


# JSON shape the screening prompt asks for; with Claude it is enforced by
# structured outputs instead of being parsed out of free text.
SCREENING_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["include", "exclude", "unsure"]},
        "reason": {"type": "string"},
        "confidence": {"type": "integer"},
    },
    "required": ["decision", "reason", "confidence"],
    "additionalProperties": False,
}


def _anthropic_chat(cfg, system_prompt, user_prompt, timeout, schema=None):
    """One request to Claude through the native Messages API.

    No temperature: current Claude models reject sampling parameters. Low
    effort keeps include/exclude screening fast and cheap; the JSON schema
    guarantees a parseable answer. Older models that don't support effort or
    structured outputs get the same request without them (400 -> next attempt).
    """
    anthropic, client = _anthropic_client(cfg, timeout)
    if not cfg.get("model"):
        raise ValueError("No Claude model selected. Click 'Refresh list' and choose a model.")
    base = {
        "model": cfg["model"],
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    output_config = {"effort": "low"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    attempts = [dict(base, output_config=output_config)]
    if schema:
        attempts.append(dict(base, output_config={"format": output_config["format"]}))
    attempts.append(base)

    for i, params in enumerate(attempts):
        try:
            response = client.messages.create(**params)
            break
        except anthropic.BadRequestError:
            if i == len(attempts) - 1:
                raise
    if response.stop_reason == "refusal":
        raise ValueError("Claude declined to assess this paper. Screen it manually.")
    if response.stop_reason == "max_tokens":
        raise ValueError("Claude's answer was cut off before it finished. Try again.")
    return "".join(block.text for block in response.content if block.type == "text")


def _ai_chat(cfg, system_prompt, user_prompt, timeout=60, schema=None):
    """One chat completion against the configured backend. Returns text.

    `schema` (a JSON schema) is enforced where the backend supports it
    (Claude); other backends rely on the prompt and on JSON extraction.
    """
    if _is_anthropic(cfg):
        return _anthropic_chat(cfg, system_prompt, user_prompt, timeout, schema)
    endpoint = cfg["endpoint"].rstrip("/")
    if cfg["provider"] == "openai":
        headers = {"Content-Type": "application/json"}
        if cfg.get("apiKey"):
            headers["Authorization"] = f"Bearer {cfg['apiKey']}"
        resp = requests.post(
            _openai_chat_url(endpoint),
            headers=headers,
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    # Ollama native chat API
    resp = requests.post(
        f"{endpoint}/api/chat",
        json={
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0},
            "format": "json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _extract_json_object(text):
    """Pull the first JSON object out of an LLM reply (models often wrap it)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            pass
    return None


def _build_screening_prompt(protocol, filters, query):
    parts = [
        "You are assisting title-and-abstract screening for a systematic literature review.",
        "Assess whether the paper should be INCLUDED in the review based only on its title and abstract.",
    ]
    if query:
        parts.append(f"Search query of the review: {query}")
    if protocol.get("researchQuestions"):
        parts.append(f"Research questions: {protocol['researchQuestions']}")
    if protocol.get("outcomes"):
        parts.append(f"Data/outcomes sought: {protocol['outcomes']}")
    criteria = []
    if filters.get("includeTerms"):
        criteria.append(f"papers should relate to: {', '.join(filters['includeTerms'])}")
    if filters.get("excludeTerms"):
        criteria.append(f"papers about the following are excluded: {', '.join(filters['excludeTerms'])}")
    if filters.get("yearFrom") or filters.get("yearTo"):
        criteria.append(f"publication year {filters.get('yearFrom') or '*'}-{filters.get('yearTo') or '*'}")
    if criteria:
        parts.append("Eligibility criteria: " + "; ".join(criteria) + ".")
    parts.append(
        'Respond with ONLY a JSON object, no other text: '
        '{"decision": "include" | "exclude" | "unsure", '
        '"reason": "<one short sentence>", "confidence": <integer 0-100>}. '
        'Use "unsure" when the abstract gives too little information.'
    )
    return "\n".join(parts)


@app.route("/api/ai/screen-suggest", methods=["POST"])
def ai_screen_suggest():
    """Suggest screening decisions for a batch of papers. Never writes decisions."""
    cfg = _load_ai_config()
    if not cfg["enabled"]:
        return jsonify({"error": "AI assistant is disabled. Enable it in Settings → AI Assistant."}), 400
    data = request.get_json(silent=True) or {}
    papers = data.get("papers") or []
    if not isinstance(papers, list) or not papers:
        return jsonify({"error": "No papers provided."}), 400
    papers = papers[:20]  # bound request latency; the frontend batches

    system_prompt = _build_screening_prompt(_load_protocol(), data.get("filters") or {}, data.get("query") or "")
    suggestions = {}
    errors = []
    for paper in papers:
        pid = str(paper.get("paperId") or "").strip()
        if not pid:
            continue
        title = (paper.get("title") or "").strip()
        abstract = (paper.get("abstract") or "").strip()
        user_prompt = f"Title: {title}\n"
        if paper.get("year"):
            user_prompt += f"Year: {paper['year']}\n"
        if paper.get("journal"):
            user_prompt += f"Venue: {paper['journal']}\n"
        user_prompt += f"Abstract: {abstract or '(no abstract available)'}"
        try:
            reply = _ai_chat(cfg, system_prompt, user_prompt, schema=SCREENING_SCHEMA)
            parsed = _extract_json_object(reply)
            decision = str((parsed or {}).get("decision", "")).lower()
            if decision not in ("include", "exclude", "unsure"):
                # One retry with a stricter reminder; small models often add prose around the JSON.
                reply = _ai_chat(cfg, system_prompt, user_prompt +
                                 "\n\nYour previous answer could not be read. Reply with ONLY the JSON object.",
                                 schema=SCREENING_SCHEMA)
                parsed = _extract_json_object(reply)
                decision = str((parsed or {}).get("decision", "")).lower()
            if decision not in ("include", "exclude", "unsure"):
                raise ValueError(f"model returned no valid decision: {reply[:120]!r}")
            suggestions[pid] = {
                "decision": decision,
                "reason": str((parsed or {}).get("reason", ""))[:500],
                "confidence": max(0, min(100, _to_int((parsed or {}).get("confidence"), 50))),
                "model": cfg["model"],
                "vendor": cfg.get("vendor") or cfg["provider"],
            }
        except Exception as exc:
            errors.append({"paperId": pid, "error": _friendly_ai_error(exc, cfg)})
    return jsonify({"suggestions": suggestions, "errors": errors, "model": cfg["model"]})


@app.route("/api/ai-config/test", methods=["POST"])
def test_ai_config_route():
    """Round-trip a trivial prompt to verify the endpoint/model works."""
    cfg = _load_ai_config()
    cfg = _apply_ai_overrides(cfg, request.get_json(silent=True) or {})
    try:
        reply = _ai_chat(cfg, "Reply with ONLY this JSON object: {\"ok\": true}", "ping", timeout=30)
        parsed = _extract_json_object(reply)
        if parsed is None:
            reply = _ai_chat(cfg, "Reply with ONLY this JSON object: {\"ok\": true}",
                             "ping\n\nYour previous answer could not be read. Reply with ONLY the JSON object.", timeout=30)
            parsed = _extract_json_object(reply)
        if parsed is not None:
            return jsonify({"ok": True, "message": f"Model '{cfg['model']}' responded."})
        return jsonify({"ok": False, "message": f"Connected, but the model's reply was not usable: {str(reply)[:120]}. Try a different (larger or instruction-tuned) model."})
    except Exception as exc:
        return jsonify({"ok": False, "message": _friendly_ai_error(exc, cfg)})


def _friendly_anthropic_error(exc):
    """Messages for errors raised by the Anthropic SDK; None if not one of them."""
    try:
        import anthropic
    except ImportError:
        return None
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude took too long to answer. Try again in a moment."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Can't reach api.anthropic.com. Check your internet connection, proxy or VPN."
    if not isinstance(exc, anthropic.APIStatusError):
        return None
    code = exc.status_code
    body = getattr(exc, "body", None)
    detail = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        detail = str(body["error"].get("message") or "")
    detail = (detail or str(getattr(exc, "message", "") or exc))[:200]
    if code == 401:
        return "Anthropic rejected the API key. Check that you pasted the full key (it starts with 'sk-ant-')."
    if code == 402:
        return "Your Anthropic account has a billing problem or no credit left. Check console.anthropic.com → Billing."
    if code == 403:
        return f"This Anthropic API key is not allowed to do this ({detail})."
    if code == 404:
        return "Claude model not found. Click 'Refresh list' and choose a model from the list."
    if code == 429:
        return ("Anthropic is rate-limiting your account, or its spend limit was reached. "
                "Wait a minute and retry, or check console.anthropic.com → Limits.")
    if code >= 500:
        return f"Anthropic's service is busy or had an error (HTTP {code}). Try again in a moment."
    return f"Anthropic answered with HTTP {code}: {detail}"


def _friendly_ai_error(exc, cfg):
    """Translate network/HTTP errors into guidance a non-technical user can act on."""
    endpoint = cfg.get("endpoint", "")
    if _is_anthropic(cfg):
        claude_msg = _friendly_anthropic_error(exc)
        if claude_msg:
            return claude_msg
    is_local = cfg.get("provider") == "ollama" or any(
        h in endpoint for h in ("localhost", "127.0.0.1", "192.168.", "10.", ":1234", ":11434"))
    if isinstance(exc, requests.exceptions.ConnectionError):
        if is_local:
            return (f"Can't reach the AI server at {endpoint}. Make sure Ollama / LM Studio is open and its "
                    "server is running; if it's on another computer, check the IP address and that it "
                    "accepts network connections.")
        return f"Can't reach {endpoint}. Check your internet connection."
    if isinstance(exc, requests.exceptions.Timeout):
        return ("The AI server took too long to answer. Large models on a computer without a strong "
                "GPU can be slow — try again or choose a smaller model.")
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in (401, 403):
            return "The API key was rejected. Check that you pasted the full key for this service."
        if code == 404:
            return ("Model or address not found. Click 'Refresh list' and choose a model from the list, "
                    "or check the server address.")
        if code == 429:
            return "The service is rate-limiting you or your account has no credit left. Check your billing/usage page."
        if code >= 500:
            return f"The AI service had an internal error (HTTP {code}). Try again in a moment."
        return f"The AI service answered with HTTP {code}: {exc.response.text[:200]}"
    return str(exc)[:300]


@app.route("/api/ai-config/models", methods=["POST"])
def list_ai_models_route():
    """List models available at the configured (or overridden) endpoint."""
    cfg = _load_ai_config()
    overrides = request.get_json(silent=True) or {}
    overrides.pop("model", None)
    cfg = _apply_ai_overrides(cfg, overrides)
    endpoint = cfg["endpoint"].rstrip("/")
    try:
        if _is_anthropic(cfg):
            _, client = _anthropic_client(cfg, timeout=15)
            models = [m.id for m in client.models.list(limit=100)]
        elif cfg["provider"] == "ollama":
            resp = requests.get(f"{endpoint}/api/tags", timeout=15)
            resp.raise_for_status()
            models = [m.get("name") for m in resp.json().get("models", [])]
        else:
            headers = {}
            if cfg.get("apiKey"):
                headers["Authorization"] = f"Bearer {cfg['apiKey']}"
                # Anthropic's /v1/models authenticates via x-api-key instead of Bearer
                headers["x-api-key"] = cfg["apiKey"]
                headers["anthropic-version"] = "2023-06-01"
            models_url = _openai_chat_url(endpoint)[: -len("/chat/completions")] + "/models"
            resp = requests.get(models_url, headers=headers, timeout=15)
            resp.raise_for_status()
            models = [m.get("id") for m in resp.json().get("data", [])]
        models = sorted({str(m).removeprefix("models/") for m in models if m})
        return jsonify({"ok": True, "models": models})
    except Exception as exc:
        return jsonify({"ok": False, "models": [], "message": _friendly_ai_error(exc, cfg)})


_OLLAMA_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,99}(:[A-Za-z0-9._\-]{1,50})?$")


@app.route("/api/ai-config/pull", methods=["POST"])
def pull_ai_model_route():
    """Ask a local Ollama server to download a model (blocking until done)."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("model", "")).strip()
    if not _OLLAMA_MODEL_RE.match(name):
        return jsonify({"ok": False, "message": "Invalid model name."}), 400
    cfg = _apply_ai_overrides(_load_ai_config(), {k: v for k, v in data.items() if k != "model"})
    if cfg["provider"] != "ollama":
        return jsonify({"ok": False, "message": "Model download is only available for Ollama."}), 400
    try:
        resp = requests.post(f"{cfg['endpoint'].rstrip('/')}/api/pull",
                             json={"model": name, "stream": False}, timeout=3600)
        resp.raise_for_status()
        status = (resp.json() or {}).get("status", "")
        if status and status != "success":
            return jsonify({"ok": False, "message": f"Ollama reported: {status[:200]}"})
        return jsonify({"ok": True, "message": f"Model '{name}' downloaded."})
    except Exception as exc:
        return jsonify({"ok": False, "message": _friendly_ai_error(exc, cfg)})


# --- API: Test Connection (proxy to avoid browser CORS restrictions) ---

@app.route("/api/test-connection", methods=["POST"])
def test_connection():
    data = request.get_json() or {}
    api = data.get("api", "").lower()
    key = data.get("key", "").strip()

    if not key:
        # The UI no longer holds the stored key in its inputs (masked-only
        # responses); an empty key means "test the stored key".
        stored_getter = {
            "ieee": get_ieee_api_key,
            "scopus": get_scopus_api_key,
            "inspec": get_inspec_api_key,
            "sciencedirect": get_sciencedirect_api_key,
            "springer": get_springer_api_key,
            "pubmed": get_pubmed_api_key,
            "acm": get_acm_api_key,
            "wos": get_wos_api_key,
        }.get(api)
        if stored_getter:
            key = (stored_getter() or "").strip()

    try:
        if api == "ieee":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            r = requests.get(
                "https://ieeexploreapi.ieee.org/api/v1/search/articles",
                params={"apikey": key, "querytext": "test", "max_records": 1},
                timeout=10,
            )
            if r.status_code == 200:
                total = r.json().get("total_records", "?")
                return jsonify({"ok": True, "message": f"Connection successful! IEEE Xplore returned {total} results for test query."})
            elif r.status_code == 403:
                return jsonify({"ok": False, "message": "API key rejected (403 Forbidden). Your key may be invalid, inactive, or not yet approved. Visit developer.ieee.org to check your key status or request a new one."})
            elif r.status_code == 401:
                return jsonify({"ok": False, "message": "API key rejected (401 Unauthorized). Please verify your key at developer.ieee.org or request a new one."})
            else:
                return jsonify({"ok": False, "message": f"IEEE API returned an unexpected status {r.status_code}. Try again later or check developer.ieee.org."})

        elif api == "scopus":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            headers = {"Accept": "application/json", "X-ELS-APIKey": key}
            insttoken = get_elsevier_insttoken()
            if insttoken:
                headers["X-ELS-Insttoken"] = insttoken
            # Use a proper TITLE-ABS-KEY query structure that Scopus accepts
            # The test query uses parentheses which Scopus requires for proper syntax
            r = requests.get(
                "https://api.elsevier.com/content/search/scopus",
                params={
                    "query": "TITLE-ABS-KEY(\"machine learning\")",
                    "count": 1,
                    "sort": "-coverDate",
                    "view": "STANDARD",
                },
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                total = r.json().get("search-results", {}).get("opensearch:totalResults", "?")
                return jsonify({"ok": True, "message": f"Connection successful! Scopus returned {total} results for test query."})
            elif r.status_code == 400:
                error_detail = ""
                try:
                    error_detail = r.json().get("service-error", {}).get("status", {}).get("statusText", r.text[:200])
                except:
                    error_detail = r.text[:200]
                return jsonify({"ok": False, "message": f"Bad request (400). Scopus API error: {error_detail}. Verify your API key at dev.elsevier.com."})
            elif r.status_code in (401, 403):
                return jsonify({"ok": False, "message": f"API key rejected ({r.status_code}). Your Scopus key may be invalid or expired. Visit dev.elsevier.com to verify your key or generate a new one."})
            elif r.status_code == 429:
                return jsonify({"ok": False, "message": "Rate limit exceeded (429). Too many requests — wait a moment and try again."})
            else:
                return jsonify({"ok": False, "message": f"Scopus API returned an unexpected status {r.status_code}. Try again later or check dev.elsevier.com."})

        elif api == "inspec":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            headers = {"Accept": "application/json", "X-ELS-APIKey": key}
            insttoken = get_elsevier_insttoken()
            if insttoken:
                headers["X-ELS-Insttoken"] = insttoken
            r = requests.get(
                "https://api.elsevier.com/content/ev/results",
                params={"query": "test", "database": "i", "pageSize": 1},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                return jsonify({"ok": True, "message": "Connection successful! Inspec (Engineering Village) API key is valid."})
            elif r.status_code == 403:
                return jsonify({"ok": False, "message": "Access denied (403): Your Elsevier API key does not have Engineering Village / Inspec access. Inspec requires a separate institutional subscription — contact your library or visit dev.elsevier.com to request EV access for your key."})
            elif r.status_code == 401:
                return jsonify({"ok": False, "message": "API key rejected (401). Your Inspec key may be invalid or expired. Visit dev.elsevier.com to verify your key."})
            elif r.status_code == 429:
                return jsonify({"ok": False, "message": "Rate limit exceeded (429). Too many requests — wait a moment and try again."})
            else:
                return jsonify({"ok": False, "message": f"Inspec API returned an unexpected status {r.status_code}. Try again later or check dev.elsevier.com."})

        elif api == "sciencedirect":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            headers = {"Accept": "application/json", "X-ELS-APIKey": key}
            insttoken = get_elsevier_insttoken()
            if insttoken:
                headers["X-ELS-Insttoken"] = insttoken
            r = requests.get(
                "https://api.elsevier.com/content/search/sciencedirect",
                params={"query": "test", "count": 1},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                total = r.json().get("search-results", {}).get("opensearch:totalResults", "?")
                return jsonify({"ok": True, "message": f"Connection successful! ScienceDirect returned {total} results for test query."})
            elif r.status_code == 401:
                return jsonify({"ok": False, "message": "Access denied (401): Your Elsevier API key does not have ScienceDirect search access. The ScienceDirect API requires an institutional subscription — your key is valid for Scopus but ScienceDirect access must be separately enabled. Contact your library or visit dev.elsevier.com to request ScienceDirect access for your key."})
            elif r.status_code == 403:
                return jsonify({"ok": False, "message": "Access denied (403): Your Elsevier API key may be invalid or expired. Visit dev.elsevier.com to verify your key or generate a new one."})
            elif r.status_code == 429:
                return jsonify({"ok": False, "message": "Rate limit exceeded (429). Too many requests — wait a moment and try again."})
            else:
                return jsonify({"ok": False, "message": f"ScienceDirect API returned an unexpected status {r.status_code}. Try again later or check dev.elsevier.com."})

        elif api == "springer":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            # Use a specific field query (keyword:) — broad queries like 'test' hang the server.
            # A keyword search is fast (~1s) regardless of corpus size.
            try:
                r = requests.get(
                    SPRINGER_API_URL,
                    params={"q": "keyword:machine+learning", "p": 1, "s": 1, "api_key": key},
                    timeout=15,
                )
                if r.status_code == 200:
                    result = r.json()
                    total = (result.get("result") or [{}])[0].get("total", "?")
                    return jsonify({"ok": True, "message": f"Connection successful! Springer Nature API is active ({total} results for test query)."})
                elif r.status_code in (401, 403):
                    return jsonify({"ok": False, "message": f"API key rejected ({r.status_code}). Your Springer Nature key is invalid or inactive. Visit dev.springernature.com to verify your key."})
                elif r.status_code == 429:
                    return jsonify({"ok": False, "message": "Rate limit exceeded (429). Too many requests — wait a moment and try again."})
                else:
                    return jsonify({"ok": False, "message": f"Springer API returned status {r.status_code}. Try again later or check dev.springernature.com."})
            except requests.exceptions.Timeout:
                return jsonify({"ok": False, "message": "Springer API timed out. Check your internet connection or try again later."})
            except Exception as e:
                return jsonify({"ok": False, "message": f"Springer error: {e}"})

        elif api == "pubmed":
            params = {"db": "pubmed", "term": "test", "retmax": 1, "retmode": "json"}
            if key:
                params["api_key"] = key
            r = requests.get(PUBMED_API_URL + "esearch.fcgi", params=params, timeout=10)
            if r.status_code == 200:
                count = r.json().get("esearchresult", {}).get("count", "?")
                return jsonify({"ok": True, "message": f"Connection successful! PubMed is reachable ({count} results for test query)."})
            elif r.status_code == 401:
                return jsonify({"ok": False, "message": "API key rejected (401). Your NCBI API key is invalid. Visit ncbi.nlm.nih.gov/account/settings to verify or generate a new key. Note: PubMed works without a key at lower rate limits."})
            else:
                return jsonify({"ok": False, "message": f"PubMed API returned status {r.status_code}. Try again later or check ncbi.nlm.nih.gov."})

        elif api == "hal":
            r = requests.get(HAL_API_URL, params={"q": "test", "rows": 1, "wt": "json"}, timeout=10)
            if r.status_code == 200:
                total = r.json().get("response", {}).get("numFound", "?")
                return jsonify({"ok": True, "message": f"Connection successful! HAL Open Science is reachable ({total} results for test query)."})
            else:
                return jsonify({"ok": False, "message": f"HAL API returned status {r.status_code}."})

        elif api == "arxiv":
            r = requests.get(ARXIV_API_URL, params={"search_query": "all:test", "max_results": 1}, timeout=10)
            if r.status_code == 200:
                return jsonify({"ok": True, "message": "Connection successful! arXiv API is reachable."})
            else:
                return jsonify({"ok": False, "message": f"arXiv API returned status {r.status_code}."})

        elif api == "acm":
            # ACM DL does not offer a public REST API. The doSearch endpoint is an internal
            # AJAX endpoint used by their website and is protected by Cloudflare bot mitigation,
            # which blocks all server-side programmatic requests with a 403.
            # We test basic connectivity by doing a HEAD request to the ACM homepage instead.
            headers_test = {
                "User-Agent": "Mozilla/5.0 (compatible; ReviewSentinelPrime/1.0; research tool)",
                "Accept": "text/html,application/xhtml+xml",
            }
            try:
                r = requests.head("https://dl.acm.org/", headers=headers_test, timeout=10, allow_redirects=True)
                if r.status_code < 500:
                    return jsonify({
                        "ok": False,
                        "message": (
                            "ACM Digital Library is reachable via browser, but its search endpoint "
                            "is protected by Cloudflare bot mitigation, which blocks all server-side "
                            "programmatic requests. ACM does not offer a public REST API. "
                            "ACM searches in Review Sentinel Prime may return no results due to this restriction."
                        )
                    })
                else:
                    return jsonify({"ok": False, "message": f"ACM DL returned status {r.status_code}. The site may be temporarily unavailable."})
            except requests.exceptions.Timeout:
                return jsonify({"ok": False, "message": "ACM Digital Library timed out. Check your internet connection or try again later."})

        elif api == "openalex":
            # Single-entity lookups are free, so this check never spends the
            # (small, keyless) daily search budget that real searches need.
            oa_key = (key or "").strip() or get_openalex_api_key()
            oa_params = {"select": "id"}
            if oa_key:
                oa_params["api_key"] = oa_key
            r = requests.get("https://api.openalex.org/works/W2741809807", params=oa_params,
                             headers={"User-Agent": "ReviewSentinelPrime"}, timeout=10)
            remaining = r.headers.get("X-RateLimit-Remaining")
            limit = r.headers.get("X-RateLimit-Limit")
            budget = (f" Daily budget remaining: {remaining} of {limit} credits (each search page of "
                      f"up to 100 records costs 10)." if remaining and limit else "")
            keyed = "with your API key" if oa_key else "without an API key (keyless budget is about 100 searches/day)"
            if r.status_code == 200:
                return jsonify({"ok": True, "message": f"Connection successful — OpenAlex is reachable {keyed}.{budget}"})
            if r.status_code == 429:
                wait = _quota_exhausted_wait(r)
                when = f" It resets in about {_fmt_wait(wait)} (midnight UTC)." if wait is not None else " Please wait a moment and try again."
                return jsonify({"ok": False, "message": f"OpenAlex rate limit or daily budget exceeded (429).{when}"})
            if r.status_code in (401, 403):
                return jsonify({"ok": False, "message": f"OpenAlex rejected the API key (HTTP {r.status_code}) — check it, or activate it from the email OpenAlex sent."})
            return jsonify({"ok": False, "message": f"OpenAlex API returned status {r.status_code}."})

        elif api == "ebsco":
            creds = get_ebsco_credentials()
            missing = [n for n, v in (("User ID", creds["userId"]),
                                      ("Password", creds["password"]),
                                      ("Profile ID", creds["profile"])) if not v]
            if missing:
                return jsonify({"ok": False, "message": f"Missing EBSCO {', '.join(missing)}."})

            auth_token, err = _ebsco_auth_token(creds, force_refresh=True)
            if err:
                return jsonify({"ok": False, "message": err})
            session_token, err = _ebsco_create_session(auth_token, creds["profile"])
            if err:
                return jsonify({"ok": False, "message": err})
            try:
                r = requests.get(
                    f"{EBSCO_API_URL}/Search",
                    params={"query": "test", "resultsperpage": 1, "pagenumber": 1,
                            "highlight": "n", "includefacets": "n"},
                    headers={"x-authenticationToken": auth_token,
                             "x-sessionToken": session_token,
                             "Accept": "application/json"},
                    timeout=20,
                )
            finally:
                _ebsco_end_session(auth_token, session_token)
            if r.status_code == 200:
                total = ((r.json().get("SearchResult") or {}).get("Statistics") or {}).get("TotalHits", "?")
                return jsonify({"ok": True, "message": f"Connection successful! EBSCO Discovery Service returned {total} results for test query."})
            elif r.status_code in (401, 403):
                return jsonify({"ok": False, "message": f"EBSCO rejected the search ({r.status_code}). Your profile may not be entitled to EDS API access — contact your library administrator."})
            elif r.status_code == 429:
                return jsonify({"ok": False, "message": "Rate limit exceeded (429). Too many requests to EBSCO — wait a moment and try again."})
            else:
                return jsonify({"ok": False, "message": f"EBSCO API returned status {r.status_code}."})

        elif api == "wos":
            if not key:
                return jsonify({"ok": False, "message": "No API key provided."})
            edition = (data.get("edition") or get_wos_edition()).strip().lower()
            edition = "expanded" if edition == "expanded" else "starter"
            if edition == "expanded":
                r = requests.get(
                    WOS_EXPANDED_API_URL,
                    params={"databaseId": "WOS", "usrQuery": "TS=(test)", "count": 1,
                            "firstRecord": 1, "optionView": "FR"},
                    headers={"X-ApiKey": key, "Accept": "application/json"},
                    timeout=30,
                )
                if r.status_code == 200:
                    total = (r.json().get("QueryResult") or {}).get("RecordsFound", "?")
                    return jsonify({"ok": True, "message": f"Connection successful! Web of Science Expanded returned {total} results for test query (abstracts included)."})
            else:
                r = requests.get(
                    WOS_API_URL,
                    params={"q": "TS=(test)", "db": "WOS", "limit": 1, "page": 1},
                    headers={"X-ApiKey": key, "Accept": "application/json"},
                    timeout=20,
                )
                if r.status_code == 200:
                    total = (r.json().get("metadata") or {}).get("total", "?")
                    return jsonify({"ok": True, "message": f"Connection successful! Web of Science Starter returned {total} results for test query. Note: the Starter API does not return abstracts."})
            return jsonify({"ok": False, "message": _wos_http_error(r, [], expanded=(edition == "expanded"))})

        else:
            return jsonify({"ok": False, "message": f"Unknown API: {api}"}), 400

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "message": "Request timed out. The API server may be slow — try again in a moment."})
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Error: {exc}"}), 500


# --- API: Saved Searches CRUD ---

@app.route("/api/searches", methods=["GET"])
def get_searches():
    searches = load_searches()
    for s in searches:
        job_id = f"search_{s['id']}"
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            s["schedulerNextRun"] = job.next_run_time.isoformat()
        else:
            s["schedulerNextRun"] = None
    return jsonify(searches)


@app.route("/api/searches", methods=["POST"])
def create_search():
    data = request.get_json()
    search_config = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Untitled Search"),
        "query": data.get("query", ""),
        "databases": data.get("databases", ["hal", "arxiv"]),
        "maxResults": 200,
        "pageSize": min(_to_int(data.get("pageSize", 25), 25), 200),
        "schedule": data.get("schedule", {"type": "manual"}),
        "filters": data.get("filters", {}),
        "validationPapers": data.get("validationPapers", []),
        "snowballSeeds": data.get("snowballSeeds", []),
        "notifyEmail": data.get("notifyEmail", ""),
        "createdAt": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "lastRun": None,
    }
    searches = load_searches()
    searches.append(search_config)
    save_searches(searches)
    schedule_search(search_config)
    return jsonify(search_config), 201


@app.route("/api/searches/<search_id>", methods=["PUT"])
def update_search(search_id):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    data = request.get_json()
    searches = load_searches()
    for i, s in enumerate(searches):
        if s["id"] == search_id:
            s["name"] = data.get("name", s["name"])
            s["query"] = data.get("query", s["query"])
            s["databases"] = data.get("databases", s["databases"])
            s["maxResults"] = 200
            s["pageSize"] = min(_to_int(data.get("pageSize", s.get("pageSize", 25)), 25), 200)
            s["schedule"] = data.get("schedule", s["schedule"])
            s["filters"] = data.get("filters", s.get("filters", {}))
            s["validationPapers"] = data.get("validationPapers", s.get("validationPapers", []))
            s["snowballSeeds"] = data.get("snowballSeeds", s.get("snowballSeeds", []))
            s["notifyEmail"] = data.get("notifyEmail", s.get("notifyEmail", ""))
            searches[i] = s
            save_searches(searches)
            schedule_search(s)
            return jsonify(s)
    return jsonify({"error": "Search not found"}), 404


@app.route("/api/searches/<search_id>", methods=["DELETE"])
def delete_search(search_id):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    searches = load_searches()
    searches = [s for s in searches if s["id"] != search_id]
    save_searches(searches)
    job_id = f"search_{search_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    # Remove results file
    results_file = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if os.path.exists(results_file):
        os.remove(results_file)
    return jsonify({"ok": True})


@app.route("/api/searches/<search_id>/run", methods=["POST"])
def run_search_now(search_id):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    searches = load_searches()
    search_config = next((s for s in searches if s["id"] == search_id), None)
    if not search_config:
        return jsonify({"error": "Search not found"}), 404
    run_saved_search(search_id)
    # run_saved_search swallows exceptions by design (scheduler safety), so
    # report the recorded outcome instead of an unconditional ok:true.
    refreshed = next((s for s in load_searches() if s["id"] == search_id), None)
    last_error = (refreshed or {}).get("lastRunError")
    if last_error:
        return jsonify({"ok": False, "error": last_error}), 500
    return jsonify({"ok": True})


@app.route("/api/searches/<search_id>/results", methods=["GET"])
def get_search_results(search_id):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    filepath = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(filepath):
        return jsonify([])
    with open(filepath, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


def normalize_boolean_operators(keywords):
    """Uppercase and/or/not outside quoted phrases.

    Databases like PubMed and IEEE treat lowercase `or` as a literal search
    term, so the same query would silently mean different things per database
    unless operators are normalized once, before dispatch to any connector.
    Text inside double-quoted phrases is left untouched.
    """
    query = (keywords or "").strip()
    if not query:
        return ""
    parts = re.split(r'("[^"]*")', query)
    for i, part in enumerate(parts):
        if not part.startswith('"'):
            parts[i] = re.sub(
                r"\b(and|or|not)\b",
                lambda m: m.group(0).upper(),
                part,
                flags=re.IGNORECASE,
            )
    return "".join(parts)


_QUERY_FOREIGN_SYNTAX = [
    (re.compile(r"&&|\|\|"), "'&&' / '||' are not supported; use AND / OR"),
    (re.compile(r'"\s*~\s*\d+'), "'~N' proximity after a phrase is not supported; use NEAR/N"),
    (re.compile(r"(?:^|[\s(])[+-](?=[A-Za-z\"(])"), "'+' / '-' before a term is not supported; use AND / NOT"),
    (re.compile(r"\b(?:TS|TI|AB|AK|KP|SO|AU|PY)\s*=", re.IGNORECASE),
     "Web of Science field tags (e.g. TI=, TS=) are not supported in the search box"),
]


def validate_search_query(keywords, exclude_terms=None, databases=None):
    """Return problems that would make the databases reject the query.

    Checks structure (quotes, parentheses, dangling operators) for every
    search, and syntax from other search engines when Scopus is selected —
    Scopus answers those with HTTP 400 ("Error translating query") or 500.
    Returns a list of user-facing messages; empty means the query is fine.
    """
    problems = []

    def _structure(text, label):
        if text.count('"') % 2:
            problems.append(f"{label}: a quotation mark (\") is not closed.")
            return
        # Ignore everything inside quoted phrases for the remaining checks.
        bare = re.sub(r'"[^"]*"', ' "PHRASE" ', text)
        depth = 0
        for ch in bare:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    problems.append(f"{label}: there is a closing parenthesis ')' without a matching '('.")
                    return
        if depth > 0:
            problems.append(f"{label}: {depth} opening parenthes{'is' if depth == 1 else 'es'} '(' "
                            f"{'is' if depth == 1 else 'are'} not closed.")
            return
        if re.search(r"\(\s*\)", bare):
            problems.append(f"{label}: there are empty parentheses '()'.")
        tokens = re.findall(r"\(|\)|[^\s()]+", bare)
        ops = {"AND", "OR", "NOT"}
        for i, tok in enumerate(tokens):
            if tok not in ops:
                continue
            prev = tokens[i - 1] if i > 0 else "("
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ")"
            if nxt == ")" or nxt in ("AND", "OR"):
                problems.append(f"{label}: '{tok}' is not followed by a search term.")
                return
            if tok != "NOT" and (prev == "(" or prev in ops):
                problems.append(f"{label}: '{tok}' is not preceded by a search term.")
                return

    _structure(keywords, "Search query")
    for term in exclude_terms or []:
        if str(term).strip():
            _structure(str(term), f"Exclude term '{term}'")

    if "scopus" in (databases or []):
        for pattern, message in _QUERY_FOREIGN_SYNTAX:
            if pattern.search(keywords):
                problems.append(f"Search query: {message} (Scopus rejects this syntax).")
    return problems


def build_query(keywords):
    """Build a HAL-compatible query string from user input.

    HAL/Solr expects boolean operators in uppercase; normalize common lowercase input.
    """
    return normalize_boolean_operators(keywords)


def strip_wildcards_in_quotes(text):
    """Remove * and ? wildcards from inside double-quoted phrases.

    arXiv and OpenAlex's default (stemmed) search both reject wildcards used
    inside a multi-word quoted phrase, so this is applied before dispatch to
    either connector.
    """
    return re.sub(r'"[^"]*"', lambda m: m.group(0).replace('*', '').replace('?', ''), text)


def build_arxiv_query(keywords):
    """Build an arXiv-compatible query from user input."""
    keywords = normalize_boolean_operators(keywords)
    if not keywords:
        return ""

    normalized = " ".join(keywords.split())

    # arXiv does not support wildcards (* or ?) inside quoted phrases — strip them.
    normalized = strip_wildcards_in_quotes(normalized)

    # Preserve explicit Boolean expressions and fielded/quoted queries.
    if re.search(r"\b(AND|OR|NOT|ANDNOT)\b", normalized) or '"' in normalized or "'" in normalized:
        return f"all:({normalized})"

    # Convert plain multi-word input into an AND query for arXiv.
    terms = normalized.split()
    if not terms:
        return f"all:{normalized}"
    if len(terms) == 1:
        return f"all:{terms[0]}"
    return f"all:({' AND '.join(terms)})"


def build_db_queries(keywords, filters=None):
    """Build the exact query string (and key URL parameters) sent to each API.

    Returns a dict keyed by database ID:
      { db_id: { "query": <str>, "query_param": <str>, "extra_params": <dict> } }
    Only replication-relevant parameters are captured; API keys are excluded.
    """
    keywords = normalize_boolean_operators(keywords)
    filters = filters or {}
    exclude_terms = filters.get("excludeTerms", [])
    year_from     = filters.get("yearFrom")
    year_to       = filters.get("yearTo")
    languages     = filters.get("languages", [])
    doc_types     = filters.get("docTypes", [])

    _lang_map = {
        "en": "English", "fr": "French", "de": "German", "es": "Spanish",
        "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese",
    }

    queries = {}

    # ── HAL Open Science (Solr) ───────────────────────────────────────
    hal_q = build_query(keywords)
    for term in exclude_terms:
        hal_q += f" NOT {term}"
    hal_fq = []
    if year_from or year_to:
        hal_fq.append(f"producedDateY_i:[{year_from or '*'} TO {year_to or '*'}]")
    if doc_types:
        hal_fq.append("docType_s:(" + " OR ".join(doc_types) + ")")
    if languages:
        hal_fq.append("language_s:(" + " OR ".join(languages) + ")")
    queries["hal"] = {
        "query_param": "q",
        "query": hal_q,
        "extra_params": {"fq": hal_fq} if hal_fq else {},
    }

    # ── arXiv ─────────────────────────────────────────────────────────
    arxiv_q = build_arxiv_query(keywords)
    for term in exclude_terms:
        arxiv_q += f" ANDNOT all:{term}"
    if year_from or year_to:
        date_from = f"{year_from}0101" if year_from else "19000101"
        date_to   = f"{year_to}1231"   if year_to   else "20991231"
        arxiv_q  += f" AND submittedDate:[{date_from} TO {date_to}]"
    queries["arxiv"] = {
        "query_param": "search_query",
        "query": arxiv_q,
        "extra_params": {},
    }

    # ── IEEE Xplore ───────────────────────────────────────────────────
    ieee_q = keywords
    for term in exclude_terms:
        ieee_q += f" NOT {term}"
    ieee_extra = {}
    if year_from:
        ieee_extra["start_year"] = year_from
    if year_to:
        ieee_extra["end_year"] = year_to
    queries["ieee"] = {
        "query_param": "querytext",
        "query": ieee_q,
        "extra_params": ieee_extra,
    }

    # ── Scopus ────────────────────────────────────────────────────────
    scopus_q = f"TITLE-ABS-KEY({keywords})"
    for term in exclude_terms:
        scopus_q += f" AND NOT TITLE-ABS-KEY({term})"
    if year_from and year_to:
        scopus_q += f" AND PUBYEAR > {int(year_from) - 1} AND PUBYEAR < {int(year_to) + 1}"
    elif year_from:
        scopus_q += f" AND PUBYEAR > {int(year_from) - 1}"
    elif year_to:
        scopus_q += f" AND PUBYEAR < {int(year_to) + 1}"
    _scopus_doc_map = {
        "ART": "ar", "COMM": "cp", "THESE": "th",
        "COUV": "ch", "REPORT": "rp", "PREPRINT": "ar",
    }
    if doc_types:
        scopus_types = [_scopus_doc_map.get(t, t) for t in doc_types]
        scopus_q += " AND DOCTYPE(" + " OR ".join(scopus_types) + ")"
    if languages:
        lang_names = [_lang_map.get(l, l) for l in languages]
        scopus_q += " AND LANGUAGE(" + " OR ".join(lang_names) + ")"
    queries["scopus"] = {
        "query_param": "query",
        "query": scopus_q,
        "extra_params": {},
    }

    # ── Inspec (Engineering Village) ─────────────────────────────────
    inspec_q = keywords
    for term in exclude_terms:
        inspec_q += f" NOT {term}"
    inspec_extra = {}
    if year_from:
        inspec_extra["startYear"] = year_from
    if year_to:
        inspec_extra["endYear"] = year_to
    if languages:
        inspec_extra["language"] = [_lang_map[l] for l in languages if l in _lang_map]
    queries["inspec"] = {
        "query_param": "query",
        "query": inspec_q,
        "extra_params": inspec_extra,
    }

    # ── ScienceDirect ─────────────────────────────────────────────────
    sd_q = keywords
    for term in exclude_terms:
        sd_q += f" AND NOT {term}"
    sd_extra = {}
    if year_from:
        sd_extra["date"] = f"{year_from}-{year_to or '2099'}"
    elif year_to:
        sd_extra["date"] = f"1900-{year_to}"
    queries["sciencedirect"] = {
        "query_param": "query",
        "query": sd_q,
        "extra_params": sd_extra,
    }

    # ── Springer Nature ───────────────────────────────────────────────
    springer_q = keywords
    for term in exclude_terms:
        springer_q += f" NOT {term}"
    springer_date = _springer_date_clause(year_from, year_to)
    if springer_date:
        springer_q = f"({springer_q}) {springer_date}"
    queries["springer"] = {
        "query_param": "q",
        "query": springer_q,
        "extra_params": {},
    }

    # ── PubMed (E-utilities) ──────────────────────────────────────────
    pubmed_q = keywords
    for term in exclude_terms:
        pubmed_q += f" NOT {term}"
    if languages:
        lang_terms = [f"{_lang_map.get(lang, lang)}[Language]" for lang in languages]
        if len(lang_terms) == 1:
            pubmed_q += f" AND {lang_terms[0]}"
        else:
            pubmed_q += " AND (" + " OR ".join(lang_terms) + ")"
    if year_from and year_to:
        pubmed_q += f" AND {year_from}:{year_to}[dp]"
    elif year_from:
        pubmed_q += f" AND {year_from}:3000[dp]"
    elif year_to:
        pubmed_q += f" AND 1900:{year_to}[dp]"
    queries["pubmed"] = {
        "query_param": "term",
        "query": pubmed_q,
        "extra_params": {},
    }

    # ── ACM Digital Library ───────────────────────────────────────────
    acm_q = keywords
    for term in exclude_terms:
        acm_q += f" NOT {term}"
    queries["acm"] = {
        "query_param": "query",
        "query": acm_q,
        "extra_params": {},
    }

    # ── OpenAlex ──────────────────────────────────────────────────────
    oa_filter_parts = []
    if year_from:
        oa_filter_parts.append(f"publication_year:>{int(year_from) - 1}")
    if year_to:
        oa_filter_parts.append(f"publication_year:<{int(year_to) + 1}")
    if languages:
        oa_filter_parts.append(f"language:{languages[0]}")
    queries["openalex"] = {
        "query_param": "search",
        # OpenAlex's default search is stemmed and returns 400 for wildcards inside quotes.
        "query": strip_wildcards_in_quotes(keywords),
        "extra_params": {"filter": ",".join(oa_filter_parts)} if oa_filter_parts else {},
    }

    # ── EBSCO (EDS) ───────────────────────────────────────────────────
    # EDS query syntax: AND/OR/NOT with field codes (TI, AB, SU); limiters and
    # facets travel separately in the querystring rather than inside `query`.
    ebsco_q = keywords
    for term in exclude_terms:
        ebsco_q += f" NOT {term}"
    ebsco_extra = {}
    if year_from or year_to:
        ebsco_extra["limiter"] = f"DT1:{year_from or '1900'}-01/{year_to or '3000'}-12"
    if languages:
        lang_names = [_lang_map.get(l, l) for l in languages]
        ebsco_extra["facetfilter"] = "1," + ",".join(f"Language:{n}" for n in lang_names)
    queries["ebsco"] = {
        "query_param": "query",
        "query": ebsco_q,
        "extra_params": ebsco_extra,
    }

    # ── Web of Science (Starter API) ──────────────────────────────────
    # WoS uses field tags: TS=topic, PY=year, DT=doc type, LA=language.
    wos_q = f"TS=({keywords})"
    for term in exclude_terms:
        wos_q += f" NOT TS=({term})"
    if year_from and year_to:
        wos_q += f" AND PY=({year_from}-{year_to})"
    elif year_from:
        wos_q += f" AND PY=({year_from}-3000)"
    elif year_to:
        wos_q += f" AND PY=(1900-{year_to})"
    _wos_doc_map = {
        "ART": "Article", "COMM": "Proceedings Paper", "COUV": "Book Chapter",
        "OUV": "Book", "REPORT": "Article", "THESE": "Article", "PREPRINT": "Preprint",
    }
    if doc_types:
        wos_types = sorted({_wos_doc_map[t] for t in doc_types if t in _wos_doc_map})
        if wos_types:
            wos_q += " AND DT=(" + " OR ".join(wos_types) + ")"
    if languages:
        lang_names = [_lang_map.get(l, l) for l in languages]
        wos_q += " AND LA=(" + " OR ".join(lang_names) + ")"
    queries["wos"] = {
        "query_param": "usrQuery" if get_wos_edition() == "expanded" else "q",
        "query": wos_q,
        "extra_params": ({"databaseId": "WOS", "optionView": "FR"}
                         if get_wos_edition() == "expanded" else {"db": "WOS"}),
    }

    return queries


def _api_filters(filters):
    """Return filters to send to upstream APIs.

    By default, filtering is applied only in local inclusion/exclusion processing.
    Set RSP_ENABLE_API_FILTERS=1 to enable API-side filtering.
    """
    val = (os.environ.get("RSP_ENABLE_API_FILTERS", "0") or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        api = dict(filters or {})
        terms = _split_filter_terms(api.get("excludeTerms", []))
        if api.get("excludeTermsMode", "any") == "all":
            # Connectors append one NOT per term, which is "any" semantics;
            # "all" can only be applied by the local filter.
            terms = []
        # Quote multi-word terms so `NOT safety case` cannot become
        # `NOT safety` AND `case` in the remote query.
        api["excludeTerms"] = [
            t if t.startswith('"') or " " not in t.strip() else f'"{t.strip()}"'
            for t in terms
        ]
        return api
    return {}


_LANG_NAME_TO_CODE = {
    "english": "en", "en": "en",
    "french": "fr", "fr": "fr",
    "german": "de", "de": "de",
    "spanish": "es", "es": "es",
    "portuguese": "pt", "pt": "pt",
    "italian": "it", "it": "it",
    "chinese": "zh", "zh": "zh", "zh-cn": "zh", "zh-tw": "zh",
    "japanese": "ja", "ja": "ja",
}


def _normalize_language(value):
    """Normalize language labels/codes to ISO-639-1 where possible."""
    if not value:
        return ""
    v = str(value).strip().lower().replace("_", "-")
    if not v:
        return ""
    if v in _LANG_NAME_TO_CODE:
        return _LANG_NAME_TO_CODE[v]
    # Common patterns like en-US, pt-BR, fr-FR
    if len(v) >= 2 and v[:2].isalpha():
        prefix = v[:2]
        if prefix in _LANG_NAME_TO_CODE:
            return _LANG_NAME_TO_CODE[prefix]
    return ""


def _detect_language(text):
    """Best-effort local language detection from text (no external deps)."""
    text = (text or "").strip()
    if not text:
        return "", 0.0

    # Collapse non-breaking spaces and other unicode whitespace (common in
    # OpenAlex/Springer/ScienceDirect titles) to plain spaces — otherwise the
    # " of ", " for " etc. marker matches below silently fail and an English
    # title with no abstract gets misclassified as "unknown" language.
    lower = re.sub(r"\s+", " ", text.lower())

    # Fast CJK heuristics
    if any("\u3040" <= c <= "\u30ff" for c in text):
        return "ja", 0.95
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "zh", 0.9

    score_map = {
        "en": {" the ", " and ", " of ", " in ", " for ", " with ", "this ", " that "},
        "fr": {" le ", " la ", " les ", " des ", " et ", " une ", " pour ", " dans "},
        "de": {" der ", " die ", " das ", " und ", " mit ", " ein ", " ist ", " auf "},
        "es": {" el ", " la ", " los ", " las ", " y ", " una ", " para ", " con "},
        "pt": {" o ", " a ", " os ", " as ", " e ", " uma ", " para ", " com "},
        "it": {" il ", " la ", " gli ", " le ", " e ", " una ", " per ", " con "},
    }
    padded = f" {lower} "
    scores = {code: 0 for code in score_map}
    for code, markers in score_map.items():
        for marker in markers:
            if marker in padded:
                scores[code] += 1

    best_code = max(scores, key=scores.get)
    best_score = scores[best_code]
    second_score = sorted(scores.values(), reverse=True)[1]
    if best_score == 0:
        return "", 0.0
    confidence = min(0.95, 0.55 + 0.1 * (best_score - second_score))
    return best_code, round(confidence, 2)


def _enrich_docs_language(docs):
    """Ensure every doc has normalized language metadata for local filtering."""
    for doc in docs:
        raw = doc.get("language")
        normalized = _normalize_language(raw)
        if normalized:
            doc["language"] = normalized
            doc["languageSource"] = "api"
            doc["languageConfidence"] = 1.0
            continue

        text = ((doc.get("title") or "") + " " + (doc.get("abstract") or "")).strip()
        detected, conf = _detect_language(text)
        if detected:
            doc["language"] = detected
            doc["languageSource"] = "detected"
            doc["languageConfidence"] = conf
        else:
            doc["language"] = ""
            doc["languageSource"] = "unknown"
            doc["languageConfidence"] = 0.0


# ── Connector instrumentation: retries, throttling, per-database log ────────
# Every instrumented connector records what it actually did (query as sent,
# pages, retries, errors, truncation) so a run can never report a silent 0.

_sleep = time.sleep  # indirection so tests can skip real waits

_host_throttle_lock = threading.Lock()
_host_locks = {}
_host_last_request = {}


def _throttle_host(host, min_interval):
    """Serialise requests to one host and keep at least min_interval seconds
    between them. Shared across threads, so parallel runs (or a scheduled run
    overlapping an ad-hoc one) cannot burst past an API's rate limit."""
    if not min_interval:
        return
    with _host_throttle_lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = _host_last_request.get(host, 0.0) + min_interval - time.monotonic()
        if wait > 0:
            _sleep(wait)
        _host_last_request[host] = time.monotonic()


def _parse_retry_after(value):
    """Retry-After is either delta-seconds or an HTTP-date (RFC 9110)."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _fmt_wait(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _quota_exhausted_wait(resp):
    """Seconds until a daily/weekly quota resets, or None for a plain throttle.

    OpenAlex: X-RateLimit-Remaining 0 and X-RateLimit-Reset = seconds to
    midnight UTC. Elsevier: X-ELS-Status QUOTA_EXCEEDED and X-RateLimit-Reset
    as an epoch timestamp.
    """
    h = resp.headers
    els_quota = "QUOTA_EXCEEDED" in (h.get("X-ELS-Status") or "").upper()
    remaining = (h.get("X-RateLimit-Remaining") or "").strip()
    try:
        body_text = (resp.text or "")[:500].lower()
    except Exception:
        body_text = ""
    budget_msg = any(w in body_text for w in ("not_enough_credits", "daily budget", "budget exceeded"))
    if not (els_quota or remaining in ("0", "0.0") or budget_msg):
        return None
    try:
        reset = float(h.get("X-RateLimit-Reset") or "")
    except ValueError:
        return 3600.0
    if reset > 1e9:  # epoch timestamp
        reset -= time.time()
    return max(0.0, reset)


class ConnectorRequestError(Exception):
    """A request that still failed after all retries (or must not be retried)."""

    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ConnectorLog:
    """What one connector did during one run — exported with the results."""

    def __init__(self, db, page_size=None, cap=None):
        self.db = db
        self.started = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
        self.query_sent = ""
        self.params = {}
        self.page_size = page_size
        self.cap = cap
        self.pages = 0
        self.retries = 0
        self.errors = []
        self.notes = []  # degradations/fallbacks that did not stop retrieval
        self.truncated = ""  # human-readable reason when paging stopped short

    def set_request(self, query_sent, params=None):
        self.query_sent = query_sent
        # Never persist credentials into the exported log.
        secret = {"apikey", "api_key", "apiKey", "key"}
        self.params = {k: v for k, v in (params or {}).items() if k not in secret}

    def error(self, message):
        if message and message not in self.errors:
            self.errors.append(message)

    def note(self, message):
        if message and message not in self.notes:
            self.notes.append(message)

    def to_dict(self):
        return {
            "started": self.started,
            "querySent": self.query_sent,
            "params": self.params,
            "pageSize": self.page_size,
            "cap": self.cap,
            "pages": self.pages,
            "retries": self.retries,
            "errors": list(self.errors),
            "notes": list(self.notes),
            "truncated": self.truncated,
        }


_RETRY_STATUSES = (429, 500, 502, 503, 504)


def _http_get(url, *, log, params=None, headers=None, timeout=30, max_retries=4,
              base_delay=2.0, max_wait=90.0, min_interval=0.0, label="", retry_statuses=_RETRY_STATUSES):
    """GET with exponential backoff on 429/5xx, timeouts and connection errors.

    Honours Retry-After. A wait longer than max_wait is not slept through —
    it fails fast with a message saying how long the API asked us to wait.
    Returns the final response for any other status so the caller can map
    4xx errors (bad query, bad key) to a specific message.
    """
    host = requests.utils.urlparse(url).hostname or url
    label = label or host
    last_problem = ""
    for attempt in range(max_retries + 1):
        _throttle_host(host, min_interval)
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            last_problem = f"{label} did not respond within {timeout}s"
            resp = None
        except requests.exceptions.ConnectionError as e:
            text = str(e)
            if "NameResolution" in text or "Failed to resolve" in text or "nodename nor servname" in text:
                last_problem = f"Could not reach {host} (DNS lookup failed — check your internet connection or VPN)"
            else:
                last_problem = f"Could not connect to {host} (network error)"
            resp = None
        except requests.RequestException as e:
            raise ConnectorRequestError(f"{label} request failed: {e}")

        if resp is not None and resp.status_code not in retry_statuses:
            return resp

        retry_after = None
        if resp is not None:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            if resp.status_code == 429:
                last_problem = f"{label} rate-limited the request (HTTP 429)"
                quota = _quota_exhausted_wait(resp)
                if quota is not None:
                    # Daily/weekly budget gone: retrying only burns time.
                    raise ConnectorRequestError(
                        f"{label} quota exhausted (HTTP 429); it resets in about "
                        f"{_fmt_wait(quota)}. Retrieval stopped.",
                        status=429, retry_after=quota,
                    )
            else:
                last_problem = f"{label} server error (HTTP {resp.status_code})"

        if attempt >= max_retries:
            break
        wait = retry_after if retry_after is not None else min(max_wait, base_delay * (2 ** attempt))
        if wait > max_wait:
            raise ConnectorRequestError(
                f"{last_problem}; the API asked to wait {_fmt_wait(wait)} before retrying. Try again later.",
                status=resp.status_code if resp is not None else None,
                retry_after=wait,
            )
        log.retries += 1
        _sleep(wait)

    raise ConnectorRequestError(
        f"{last_problem} after {max_retries + 1} attempts",
        status=resp.status_code if resp is not None else None,
    )


def _http_error_message(label, resp):
    """Short, specific message for a non-retryable HTTP error response."""
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error") or body.get("service-error") or body.get("message") or ""
            if isinstance(err, dict):
                err = err.get("status", {}).get("statusText") or err.get("message") or json.dumps(err)[:200]
            detail = str(err)[:300]
    except ValueError:
        detail = (resp.text or "")[:300].strip()
    code = resp.status_code
    if code in (401, 403):
        hint = "the API key/entitlement was rejected or the quota is exhausted"
    elif code == 400:
        hint = "the API rejected the query (syntax, length or unsupported operator)"
    elif code == 414:
        hint = "the query is too long for the API"
    else:
        hint = "request failed"
    return f"{label}: HTTP {code} — {hint}" + (f" ({detail})" if detail else "")


def search_hal(query, rows, filters=None):
    """Search HAL and return normalized results."""
    filters = filters or {}
    rows = min(rows, _retrieval_cap("hal"))
    page_size = 1000  # HAL Solr handles up to 1000 rows per request cleanly

    base_params = {
        "q": query,
        "wt": "json",
        "fl": ",".join([
            "docid",
            "title_s",
            "authFullName_s",
            "producedDateY_i",
            "journalTitle_s",
            "abstract_s",
            "uri_s",
            "doiId_s",
            "keyword_s",
            "docType_s",
            "language_s",
        ]),
        "sort": "producedDateY_i desc",
    }

    # Apply HAL-specific filters
    fq_parts = []
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from or year_to:
        y_from = year_from if year_from else "*"
        y_to = year_to if year_to else "*"
        fq_parts.append(f"producedDateY_i:[{y_from} TO {y_to}]")

    doc_types = filters.get("docTypes", [])
    if doc_types:
        fq_parts.append("docType_s:(" + " OR ".join(doc_types) + ")")

    languages = filters.get("languages", [])
    if languages:
        fq_parts.append("language_s:(" + " OR ".join(languages) + ")")

    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        base_params["q"] += f" NOT {term}"

    if fq_parts:
        base_params["fq"] = fq_parts

    normalized = []
    total_records = 0
    error_msg = None
    while True:
        params = dict(base_params)
        params["rows"] = min(page_size, rows - len(normalized)) if rows else page_size
        params["start"] = len(normalized)
        try:
            response = requests.get(HAL_API_URL, params=params, timeout=20)
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as e:
            error_msg = f"Request failed: {e}"
            break

        response_body = result.get("response", {})
        if total_records == 0:
            total_records = response_body.get("numFound", 0)
        docs = response_body.get("docs", [])
        if not docs:
            break

        _hal_type_map = {
                "ART": "article", "COMM": "conference", "THESE": "thesis",
                "COUV": "book chapter", "OUV": "book chapter", "DOUV": "book chapter",
                "REPORT": "report", "PREPRINT": "preprint",
            }
        for doc in docs:
            title_raw    = doc.get("title_s", "Untitled")
            abstract_raw = doc.get("abstract_s", "")
            language_raw = doc.get("language_s", "")
            _hal_raw_dt  = doc.get("docType_s", "") or ""
            normalized.append({
                "source": "HAL",
                "title": title_raw[0] if isinstance(title_raw, list) and title_raw else (title_raw if isinstance(title_raw, str) else "Untitled"),
                "authors": doc.get("authFullName_s", []),
                "year": doc.get("producedDateY_i"),
                "journal": doc.get("journalTitle_s", ""),
                "abstract": abstract_raw[0] if isinstance(abstract_raw, list) and abstract_raw else (abstract_raw if isinstance(abstract_raw, str) else ""),
                "url": doc.get("uri_s", ""),
                "doi": doc.get("doiId_s", ""),
                "keywords": doc.get("keyword_s", []),
                "docType": _hal_type_map.get(_hal_raw_dt, ""),
                "language": language_raw[0] if isinstance(language_raw, list) and language_raw else (language_raw if isinstance(language_raw, str) else ""),
            })
        if len(normalized) >= total_records or (rows and len(normalized) >= rows):
            break

    result = {"numFound": total_records, "docs": normalized}
    if error_msg:
        result["error"] = error_msg
    return result


# ── Documented API limits (checked against the providers' docs, Nov 2026) ──
# IEEE Xplore: max_records ≤ 200, start_record offset paging.
# Scopus: count ≤ 25 (COMPLETE view) / ≤ 200 (STANDARD); start+count ≤ 5000
#   without cursor paging; 9 req/s throttle, 20,000 requests/week.
# Springer Nature Meta API v2: p ≤ 25 (basic plan; 100 premium); basic plan
#   100 requests/min and 500/day.
# arXiv: max_results ≤ 2000 per call, 30,000 per query; ≥ 3 s between
#   requests on a single connection (Terms of Use).
# OpenAlex: per_page ≤ 100; page paging stops at 10,000 results (cursor
#   paging beyond that needs an API key); keyless budget ≈ 100 searches/day,
#   searches with > 5 boolean operators limited to 1 req/s without a key.
IEEE_MAX_PAGE = 200
SCOPUS_MAX_PAGE_COMPLETE = 25
SCOPUS_MAX_PAGE_STANDARD = 200
SCOPUS_START_LIMIT = 5000
SPRINGER_MAX_PAGE = 25
ARXIV_PAGE_SIZE = 200
ARXIV_MAX_TOTAL = 30000
ARXIV_MIN_INTERVAL = 3.0
OPENALEX_MAX_PAGE = 100
OPENALEX_BASIC_PAGING_LIMIT = 10000

# Defaults preserve the existing interactive search limits. Provider and
# connector-specific paging limits can still stop retrieval earlier.
RSP_RETRIEVAL_CAP = {
    "hal": 10000, "arxiv": 10000, "ieee": 1000, "scopus": 1000,
    "inspec": 1000, "sciencedirect": 1000, "springer": 1000,
    "pubmed": 1000, "acm": 1000, "openalex": 10000,
    "ebsco": 10000, "wos": 10000,
}


def _connector_result(log, total, docs, error=None):
    """Standard connector return value; records truncation when paging
    stopped short of the reported total and no specific reason was logged."""
    total = int(total or 0)
    if error:
        log.error(error)
    if total > len(docs) and not log.truncated:
        log.truncated = f"Retrieved {len(docs)} of {total} records reported by the API"
    result = {"numFound": total, "docs": docs, "log": log.to_dict()}
    if error:
        result["error"] = error
    return result


def _cap_reason(cap, total, label):
    return f"{label} retrieval cap of {cap:,} records reached; the API reported {total:,}"


_ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _normalize_arxiv_entry(entry):
    ns = _ARXIV_NS
    title_el = entry.find("atom:title", ns)
    title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled"

    summary_el = entry.find("atom:summary", ns)
    abstract = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

    authors = []
    for author_el in entry.findall("atom:author", ns):
        name_el = author_el.find("atom:name", ns)
        if name_el is not None and name_el.text:
            authors.append(name_el.text)

    published_el = entry.find("atom:published", ns)
    year = None
    if published_el is not None and published_el.text:
        try:
            year = int(published_el.text[:4])
        except (ValueError, TypeError):
            year = None

    url = ""
    id_el = entry.find("atom:id", ns)
    if id_el is not None and id_el.text:
        url = id_el.text

    doi_el = entry.find("arxiv:doi", ns)
    doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""

    journal_el = entry.find("arxiv:journal_ref", ns)
    journal = journal_el.text.strip() if journal_el is not None and journal_el.text else ""

    categories = []
    for cat_el in entry.findall("atom:category", ns):
        term_val = cat_el.get("term", "")
        if term_val:
            categories.append(term_val)

    return {
        "source": "arXiv",
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "abstract": abstract,
        "url": url,
        "doi": doi,
        "keywords": categories,
        "docType": "Preprint",
    }


def _arxiv_error_entry(entries):
    """arXiv reports bad queries as HTTP 200/400 with one Atom entry whose id
    points at /api/errors — that entry must never be treated as a paper."""
    for entry in entries:
        id_el = entry.find("atom:id", _ARXIV_NS)
        if id_el is not None and id_el.text and "/api/errors" in id_el.text:
            summary_el = entry.find("atom:summary", _ARXIV_NS)
            return (summary_el.text or "").strip() if summary_el is not None else "unknown error"
    return None


def search_arxiv(keywords, rows, filters=None):
    """Search arXiv and return normalized results."""
    filters = filters or {}
    cap = min(rows, _retrieval_cap("arxiv"), ARXIV_MAX_TOTAL)
    log = ConnectorLog("arxiv", page_size=ARXIV_PAGE_SIZE, cap=cap)
    search_query = build_arxiv_query(keywords)

    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        search_query += f" ANDNOT all:{term}"

    # Year filter — arXiv supports submittedDate:[YYYYMMDD TO YYYYMMDD] in search_query
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from or year_to:
        date_from = f"{year_from}0101" if year_from else "19000101"
        date_to = f"{year_to}1231" if year_to else "20991231"
        search_query += f" AND submittedDate:[{date_from} TO {date_to}]"

    page_size = ARXIV_PAGE_SIZE
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    log.set_request(search_query, params)

    normalized = []
    total_records = None
    error_msg = None
    empty_retries = 0
    while len(normalized) < cap:
        params["start"] = len(normalized)
        params["max_results"] = min(page_size, cap - len(normalized))
        try:
            response = _http_get(ARXIV_API_URL, log=log, params=params, timeout=60,
                                 base_delay=5.0, max_wait=120.0,
                                 min_interval=ARXIV_MIN_INTERVAL, label="arXiv",
                                 retry_statuses=(429, 502, 503, 504))
        except ConnectorRequestError as e:
            error_msg = str(e)
            break
        if response.status_code == 500:
            # arXiv's backend intermittently 500s on complex Boolean queries
            # with large pages; a smaller batch often succeeds.
            if page_size > 25:
                page_size = max(25, page_size // 2)
                log.page_size = page_size
                log.retries += 1
                log.note(f"arXiv HTTP 500 — page size reduced to {page_size}")
                continue
            error_msg = ("arXiv returned HTTP 500 even with 25-record pages — the query is likely "
                         "too complex for arXiv (e.g. wildcards inside quoted phrases)")
            break

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            error_msg = f"arXiv returned an unreadable response (HTTP {response.status_code}) at offset {params['start']}"
            break
        entries = root.findall("atom:entry", _ARXIV_NS)
        api_error = _arxiv_error_entry(entries)
        if api_error or response.status_code != 200:
            error_msg = f"arXiv rejected the query (HTTP {response.status_code}): {api_error or 'no details'}"
            break
        log.pages += 1

        if total_records is None:
            total_el = root.find("opensearch:totalResults", _ARXIV_NS)
            try:
                total_records = int(total_el.text) if total_el is not None else 0
            except (TypeError, ValueError):
                total_records = 0

        if not entries:
            if len(normalized) >= total_records:
                break
            # arXiv intermittently serves an empty page mid-result-set; retry
            # the same offset before giving up, then report the truncation.
            if empty_retries < 3:
                empty_retries += 1
                log.retries += 1
                _sleep(5 * empty_retries)
                continue
            error_msg = (f"arXiv returned empty pages at offset {params['start']} of "
                         f"{total_records} after {empty_retries} retries; retrieval stopped early")
            break
        empty_retries = 0
        normalized.extend(_normalize_arxiv_entry(e) for e in entries)
        if len(normalized) >= total_records:
            break

    total_records = total_records or 0
    if not error_msg and total_records > len(normalized) and len(normalized) >= cap:
        log.truncated = (f"arXiv allows at most {ARXIV_MAX_TOTAL:,} results per query"
                         if cap == ARXIV_MAX_TOTAL else _cap_reason(cap, total_records, "Requested"))
    return _connector_result(log, total_records, normalized, error_msg)


def _normalize_ieee_article(article):
    title = article.get("title", "Untitled")
    authors_list = []
    if "authors" in article and "authors" in article["authors"]:
        for author in article["authors"]["authors"]:
            full_name = author.get("full_name", "")
            if full_name:
                authors_list.append(full_name)

    year = None
    pub_year = article.get("publication_year")
    if pub_year:
        try:
            year = int(pub_year)
        except (ValueError, TypeError):
            pass

    journal = article.get("publication_title", "")
    abstract = article.get("abstract", "")
    doi = article.get("doi", "")
    url = article.get("html_url", "")
    if not url and doi:
        url = f"https://doi.org/{doi}"

    content_type = article.get("content_type", "")
    _ieee_type_map = {
        "Conferences": "conference",
        "Journals": "article",
        "Early Access": "article",
        "Books": "book chapter",
        "Standards": "article",
        "Courses": "article",
    }
    doc_type = _ieee_type_map.get(content_type, "article")

    keywords_list = []
    if "index_terms" in article:
        idx_terms = article["index_terms"]
        if "ieee_terms" in idx_terms and "terms" in idx_terms["ieee_terms"]:
            keywords_list.extend(idx_terms["ieee_terms"]["terms"])
        if "author_terms" in idx_terms and "terms" in idx_terms["author_terms"]:
            keywords_list.extend(idx_terms["author_terms"]["terms"])

    language = article.get("language", "") or ""

    return {
        "source": "IEEE",
        "title": title,
        "authors": authors_list,
        "year": year,
        "journal": journal,
        "abstract": abstract,
        "url": url,
        "doi": doi,
        "keywords": keywords_list[:10],
        "docType": doc_type,
        "language": language,
    }


def search_ieee(keywords, rows, filters=None):
    """Search IEEE Xplore and return normalized results."""
    filters = filters or {}
    cap = min(rows, _retrieval_cap("ieee"))
    log = ConnectorLog("ieee", page_size=IEEE_MAX_PAGE, cap=cap)
    api_key = get_ieee_api_key()
    if not api_key:
        return _connector_result(log, 0, [], "IEEE API key not configured")

    # The IEEE REST API's dedicated 'abstract' parameter does not support
    # parentheses, so complex boolean queries (which always have parens) return 0.
    # Use 'querytext' which fully supports boolean operators and parentheses.
    query_text = keywords

    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" NOT {term}"

    # NOTE: IEEE Xplore REST API does NOT support Language:"..." in querytext —
    # appending it causes the API to return 0 results. Language filtering is not
    # available as a separate API parameter either, so we skip it entirely.

    params = {
        "apikey": api_key,
        "querytext": query_text,
        "max_records": IEEE_MAX_PAGE,
        "start_record": 1,
        "sort_order": "desc",
        "sort_field": "publication_year",
    }
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from:
        params["start_year"] = year_from
    if year_to:
        params["end_year"] = year_to
    log.set_request(query_text, params)

    normalized = []
    total_records = None
    error_msg = None
    while len(normalized) < cap:
        batch = min(IEEE_MAX_PAGE, cap - len(normalized))
        if total_records is not None:
            batch = min(batch, total_records - len(normalized))
        if batch <= 0:
            break
        params["max_records"] = batch
        params["start_record"] = len(normalized) + 1
        try:
            response = _http_get(IEEE_API_URL, log=log, params=params, timeout=30,
                                 min_interval=0.2, label="IEEE Xplore")
        except ConnectorRequestError as e:
            error_msg = str(e)
            break
        if response.status_code != 200:
            error_msg = _http_error_message("IEEE Xplore", response)
            break
        try:
            data = response.json()
        except ValueError:
            error_msg = f"IEEE Xplore returned a non-JSON response at record {params['start_record']}"
            break
        log.pages += 1
        if total_records is None:
            total_records = int(data.get("total_records") or 0)
        articles = data.get("articles") or []
        if not articles:
            if len(normalized) < total_records:
                error_msg = (f"IEEE Xplore returned an empty page at record {params['start_record']} "
                             f"of {total_records}; retrieval stopped early")
            break
        normalized.extend(_normalize_ieee_article(a) for a in articles)

    total_records = total_records or 0
    if not error_msg and total_records > len(normalized) and len(normalized) >= cap:
        log.truncated = _cap_reason(cap, total_records, "RSP")
    return _connector_result(log, total_records, normalized, error_msg)


def _normalize_scopus_entry(entry):
    title = entry.get("dc:title", "Untitled")
    authors_list = []
    for a in entry.get("author") or []:
        name = a.get("authname") or " ".join(
            x for x in [a.get("given-name"), a.get("surname")] if x
        )
        if name:
            authors_list.append(name)
    if not authors_list:
        creator = entry.get("dc:creator", "")
        if creator:
            authors_list.append(creator)

    year = None
    cover_date = entry.get("prism:coverDate", "")
    if cover_date:
        try:
            year = int(cover_date[:4])
        except (ValueError, TypeError):
            pass

    journal = entry.get("prism:publicationName", "")
    abstract = entry.get("dc:description", "") or ""
    doi = entry.get("prism:doi", "") or ""

    url = ""
    eid = entry.get("eid", "")
    if doi:
        url = f"https://doi.org/{doi}"
    elif eid:
        url = f"https://www.scopus.com/record/display.uri?eid={eid}&origin=resultslist"

    _scopus_raw = (entry.get("subtypeDescription", "") or "").lower()
    _scopus_type_map = {
        "article": "article", "review": "article", "short survey": "article",
        "conference paper": "conference", "conference review": "conference",
        "book chapter": "book chapter", "book": "book chapter",
        "editorial": "article", "letter": "article", "note": "article",
        "report": "report",
    }
    doc_type = _scopus_type_map.get(_scopus_raw, "article" if _scopus_raw else "")
    keywords_list = []
    authkeywords = entry.get("authkeywords", "")
    if authkeywords:
        keywords_list = [k.strip() for k in authkeywords.split("|") if k.strip()]

    language = entry.get("prism:language", "") or ""

    return {
        "source": "Scopus",
        "title": title,
        "authors": authors_list,
        "year": year,
        "journal": journal,
        "abstract": abstract,
        "url": url,
        "doi": doi,
        "keywords": keywords_list[:10],
        "docType": doc_type,
        "language": language,
    }


def search_scopus(keywords, rows, filters=None):
    """Search Scopus and return normalized results."""
    filters = filters or {}
    cap = min(rows, _retrieval_cap("scopus"))
    log = ConnectorLog("scopus", page_size=SCOPUS_MAX_PAGE_COMPLETE, cap=cap)
    api_key = get_scopus_api_key()
    if not api_key:
        return _connector_result(log, 0, [], "Scopus API key not configured")

    query = f"TITLE-ABS-KEY({keywords})"

    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query += f" AND NOT TITLE-ABS-KEY({term})"

    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from and year_to:
        query += f" AND PUBYEAR > {int(year_from) - 1} AND PUBYEAR < {int(year_to) + 1}"
    elif year_from:
        query += f" AND PUBYEAR > {int(year_from) - 1}"
    elif year_to:
        query += f" AND PUBYEAR < {int(year_to) + 1}"

    doc_types = filters.get("docTypes", [])
    scopus_doc_map = {
        "ART": "ar", "COMM": "cp", "THESE": "th",
        "COUV": "ch", "REPORT": "rp", "PREPRINT": "ar"
    }
    if doc_types:
        scopus_types = [scopus_doc_map.get(t, t) for t in doc_types]
        query += " AND DOCTYPE(" + " OR ".join(scopus_types) + ")"

    languages = filters.get("languages", [])
    lang_map = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese"}
    if languages:
        lang_names = [lang_map.get(l, l) for l in languages]
        query += " AND LANGUAGE(" + " OR ".join(lang_names) + ")"

    params = {
        "query": query,
        "sort": "-coverDate",
        "field": "dc:title,dc:creator,author,prism:coverDate,prism:publicationName,dc:description,prism:doi,citedby-count,subtypeDescription,authkeywords,prism:url,eid,prism:language",
    }
    log.set_request(query, params)

    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json",
    }
    insttoken = get_elsevier_insttoken()
    if insttoken:
        headers["X-ELS-Insttoken"] = insttoken

    normalized = []
    total_records = None
    error_msg = None
    # Entitlement-dependent features, each dropped on a 4xx before any data:
    # COMPLETE view returns the full author list (STANDARD has only the first
    # author) but caps count at 25; cursor paging removes the 5,000-record
    # ceiling of start-based paging.
    view_complete = True
    use_cursor = True
    cursor = "*"
    while len(normalized) < cap:
        page_max = SCOPUS_MAX_PAGE_COMPLETE if view_complete else SCOPUS_MAX_PAGE_STANDARD
        log.page_size = page_max
        req = dict(params)
        req["count"] = min(page_max, cap - len(normalized))
        if view_complete:
            req["view"] = "COMPLETE"
        if use_cursor:
            req["cursor"] = cursor
        else:
            start = len(normalized)
            if start >= SCOPUS_START_LIMIT:
                log.truncated = (f"Scopus start-based paging stops at {SCOPUS_START_LIMIT:,} records "
                                 "(cursor paging is not enabled for this API key)")
                break
            req["start"] = start
            req["count"] = min(req["count"], SCOPUS_START_LIMIT - start)
        try:
            response = _http_get(SCOPUS_API_URL, log=log, params=req, headers=headers,
                                 timeout=30, min_interval=0.12, label="Scopus")
        except ConnectorRequestError as e:
            error_msg = str(e)
            break
        if response.status_code in (400, 401, 403) and not normalized and (view_complete or use_cursor):
            if view_complete:
                view_complete = False
                log.note(f"Scopus HTTP {response.status_code} with view=COMPLETE — retried with the STANDARD view (first author only)")
            else:
                use_cursor = False
                log.note(f"Scopus HTTP {response.status_code} with cursor paging — retried with start-based paging (max {SCOPUS_START_LIMIT:,} records)")
            continue
        if response.status_code == 400:
            # All fallbacks already tried, so the query text itself is the problem.
            error_msg = ("Scopus could not read the search query (HTTP 400). Check for unbalanced "
                         "parentheses or quotation marks, AND/OR/NOT at the start or end, or syntax "
                         "from another search engine, then search again.")
            break
        if response.status_code != 200:
            error_msg = _http_error_message("Scopus", response)
            break
        try:
            data = response.json()
        except ValueError:
            error_msg = "Scopus returned a non-JSON response"
            break
        log.pages += 1

        search_results = data.get("search-results", {})
        if total_records is None:
            total_records = int(search_results.get("opensearch:totalResults") or 0)
        next_cursor = (search_results.get("cursor") or {}).get("@next") or ""
        # An empty result set comes back as a single {"error": "Result set was empty"} entry.
        entries = [e for e in search_results.get("entry", []) if "error" not in e]
        if not entries:
            if len(normalized) < total_records:
                error_msg = (f"Scopus returned an empty page after {len(normalized)} of "
                             f"{total_records} records; retrieval stopped early")
            break
        normalized.extend(_normalize_scopus_entry(e) for e in entries)
        if len(normalized) >= total_records:
            break
        if use_cursor:
            if not next_cursor or next_cursor == cursor:
                error_msg = (f"Scopus stopped returning a next cursor after {len(normalized)} of "
                             f"{total_records} records")
                break
            cursor = next_cursor

    total_records = total_records or 0
    if not error_msg and not log.truncated and total_records > len(normalized) and len(normalized) >= cap:
        log.truncated = _cap_reason(cap, total_records, "RSP")
    return _connector_result(log, total_records, normalized, error_msg)


def search_inspec(keywords, rows, filters=None):
    """Search Inspec on Engineering Village and return normalized results."""
    filters = filters or {}
    rows = min(rows, _retrieval_cap("inspec"))
    api_key = get_inspec_api_key()
    if not api_key:
        return {"numFound": 0, "docs": [], "error": "Inspec API key not configured"}

    # Build query
    query_text = keywords

    # Add exclusion terms
    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" NOT {term}"

    # Year filter
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    date_range = ""
    if year_from and year_to:
        date_range = f"{year_from}-{year_to}"
    elif year_from:
        date_range = f"{year_from}-2099"
    elif year_to:
        date_range = f"1900-{year_to}"

    params = {
        "query": query_text,
        "database": "i",  # 'i' = Inspec
        "pageSize": 25,
        "offset": 1,
        "sortField": "yr",
        "sortDirection": "dw",  # descending
    }

    if date_range:
        params["startYear"] = year_from or "1900"
        params["endYear"] = year_to or "2099"

    # Language filter — Engineering Village supports language[] parameter
    languages = filters.get("languages", [])
    inspec_lang_map = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese"}
    if languages:
        lang_names = [inspec_lang_map[l] for l in languages if l in inspec_lang_map]
        if lang_names:
            params["language"] = lang_names  # EV accepts repeated or list params

    headers = {
        "Accept": "application/json",
        "X-ELS-APIKey": api_key,
    }
    insttoken = get_elsevier_insttoken()
    if insttoken:
        headers["X-ELS-Insttoken"] = insttoken

    normalized = []
    total_records = 0
    error_msg = None
    while len(normalized) < rows:
        params["pageSize"] = min(25, rows - len(normalized))
        params["offset"] = len(normalized) + 1
        try:
            response = requests.get(INSPEC_API_URL, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            error_msg = f"Request failed: {e}"
            break

        page = data.get("PAGE", {})
        if total_records == 0:
            total_records = int(page.get("RESULTS-COUNT", 0))
        results = page.get("PAGE-RESULTS", {}).get("PAGE-ENTRY", [])
        if isinstance(results, dict):
            results = [results]
        if not results:
            break

        for entry in results:
            ei_doc = entry.get("EI-DOCUMENT", {})

            title = ""
            doc_title = ei_doc.get("DOCUMENTPROPERTIES", {}).get("TI", "")
            if isinstance(doc_title, list):
                title = doc_title[0] if doc_title else "Untitled"
            else:
                title = doc_title or "Untitled"

            authors_list = []
            aus = ei_doc.get("DOCUMENTPROPERTIES", {}).get("AUS", {}).get("AU", [])
            if isinstance(aus, str):
                authors_list = [aus]
            elif isinstance(aus, list):
                authors_list = aus

            year = None
            yr = ei_doc.get("DOCUMENTPROPERTIES", {}).get("YR", "")
            if yr:
                try:
                    year = int(yr)
                except (ValueError, TypeError):
                    pass

            journal = ei_doc.get("DOCUMENTPROPERTIES", {}).get("SO", "") or ""
            abstract = ei_doc.get("DOCUMENTPROPERTIES", {}).get("AB", "") or ""
            doi = ei_doc.get("DOCUMENTPROPERTIES", {}).get("DO", "") or ""

            url = ""
            if doi:
                url = f"https://doi.org/{doi}"

            _inspec_raw = (ei_doc.get("DOCUMENTPROPERTIES", {}).get("DT", "") or "").lower()
            _inspec_type_map = {
                "journal article": "article", "journal paper": "article",
                "conference paper": "conference", "conference article": "conference",
                "conference proceeding": "conference",
                "book": "book chapter", "book chapter": "book chapter",
                "report": "report", "technical report": "report",
                "thesis": "thesis", "dissertation": "thesis",
                "preprint": "preprint",
            }
            doc_type = _inspec_type_map.get(_inspec_raw, "article" if _inspec_raw else "")

            keywords_list = []
            cvs = ei_doc.get("DOCUMENTPROPERTIES", {}).get("CVS", {}).get("CV", [])
            if isinstance(cvs, str):
                keywords_list = [cvs]
            elif isinstance(cvs, list):
                keywords_list = cvs[:10]

            language = ei_doc.get("DOCUMENTPROPERTIES", {}).get("LA", "") or ""
            if isinstance(language, list):
                language = language[0] if language else ""

            normalized.append({
                "source": "Inspec",
                "title": title,
                "authors": authors_list,
                "year": year,
                "journal": journal,
                "abstract": abstract,
                "url": url,
                "doi": doi,
                "keywords": keywords_list,
                "docType": doc_type,
                "language": language,
            })
        if len(normalized) >= total_records:
            break

    result = {"numFound": total_records, "docs": normalized}
    if error_msg:
        result["error"] = error_msg
    return result


def search_sciencedirect(keywords, rows, filters=None):
    """Search ScienceDirect and return normalized results."""
    filters = filters or {}
    rows = min(rows, _retrieval_cap("sciencedirect"))
    log = ConnectorLog("sciencedirect", page_size=25, cap=rows)
    api_key = get_sciencedirect_api_key()
    if not api_key:
        return _connector_result(log, 0, [], "ScienceDirect API key not configured")

    # Build query
    query_text = keywords

    # Add exclusion terms
    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" AND NOT {term}"

    params = {
        "query": query_text,
        "count": 25,
        "start": 0,
        "sort": "-date",
    }

    # Year filter via date range
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from:
        params["date"] = f"{year_from}-{year_to or '2099'}"
    elif year_to:
        params["date"] = f"1900-{year_to}"
    log.set_request(query_text, params)

    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json",
    }
    insttoken = get_elsevier_insttoken()
    if insttoken:
        headers["X-ELS-Insttoken"] = insttoken

    normalized = []
    total_records = 0
    error_msg = None
    while len(normalized) < rows:
        params["count"] = min(25, rows - len(normalized))
        params["start"] = len(normalized)
        # Elsevier API hard limit: start + count must be ≤ 5000
        if params["start"] >= 5000:
            log.truncated = "ScienceDirect start-based paging stops at 5,000 records"
            break
        params["count"] = min(params["count"], 5000 - params["start"])
        # Shared helper: throttles, retries 429/5xx/timeouts/connection drops
        # with backoff, and words failures for the user (same as Scopus).
        try:
            response = _http_get(SCIENCEDIRECT_API_URL, log=log, params=params, headers=headers,
                                 timeout=30, min_interval=0.5, label="ScienceDirect")
        except ConnectorRequestError as e:
            error_msg = str(e)
            break
        if response.status_code != 200:
            error_msg = _http_error_message("ScienceDirect", response)
            break
        try:
            data = response.json()
        except ValueError:
            error_msg = "ScienceDirect returned a non-JSON response"
            break
        log.pages += 1

        search_results = data.get("search-results", {})
        if total_records == 0:
            # .get(..., 0) only covers a missing key — Elsevier sometimes sends
            # the key with an explicit null, which still needs the `or 0` fallback.
            total_records = int(search_results.get("opensearch:totalResults") or 0)
        entries = search_results.get("entry", [])
        if not isinstance(entries, list):
            entries = [entries] if entries else []
        if not entries:
            break

        for entry in entries:
            if "error" in entry:
                continue

            title = entry.get("dc:title", "Untitled") or "Untitled"

            authors_list = []
            creator = entry.get("dc:creator", "")
            if creator:
                authors_list.append(creator)
            authors_field = entry.get("authors", {})
            if isinstance(authors_field, dict):
                author_entries = authors_field.get("author", [])
                if isinstance(author_entries, list):
                    for a in author_entries:
                        name = a.get("$", "") or a.get("given-name", "") + " " + a.get("surname", "")
                        if name.strip() and name.strip() not in authors_list:
                            authors_list.append(name.strip())

            year = None
            cover_date = entry.get("prism:coverDate", "") or ""
            if cover_date:
                try:
                    year = int(cover_date[:4])
                except (ValueError, TypeError):
                    pass

            journal = entry.get("prism:publicationName", "") or ""
            doi = entry.get("prism:doi", "") or ""
            abstract = entry.get("dc:description", "") or ""

            url = ""
            if doi:
                url = f"https://doi.org/{doi}"
            else:
                links = entry.get("link", [])
                if isinstance(links, list):
                    for link in links:
                        if link.get("@ref") == "scidir":
                            url = link.get("@href", "")
                            break

            _sd_raw = (entry.get("prism:aggregationType", "") or "").lower()
            _sd_type_map = {
                "journal": "article", "serial": "article",
                "conference proceeding": "conference", "conference paper": "conference",
                "book series": "book chapter", "book": "book chapter",
                "report": "report",
            }
            doc_type = _sd_type_map.get(_sd_raw, "article" if _sd_raw else "")

            keywords_list = []
            authkeywords = entry.get("authkeywords", "")
            if authkeywords:
                keywords_list = [k.strip() for k in authkeywords.split("|") if k.strip()]

            language = entry.get("prism:language", "") or ""

            normalized.append({
                "source": "ScienceDirect",
                "title": title,
                "authors": authors_list,
                "year": year,
                "journal": journal,
                "abstract": abstract,
                "url": url,
                "doi": doi,
                "keywords": keywords_list[:10],
                "docType": doc_type,
                "language": language,
            })
        if len(normalized) >= total_records:
            break

    if not error_msg and not log.truncated and total_records > len(normalized) and len(normalized) >= rows:
        log.truncated = _cap_reason(rows, total_records, "RSP")
    return _connector_result(log, total_records, normalized, error_msg)


def _normalize_springer_record(item):
    title = item.get("title", "Untitled") or "Untitled"

    authors_list = []
    creators = item.get("creators", [])
    if isinstance(creators, list):
        for c in creators:
            name = c.get("creator", "")
            if name:
                authors_list.append(name)

    year = None
    pub_date = item.get("publicationDate", "") or item.get("onlineDate", "")
    if pub_date:
        try:
            year = int(str(pub_date)[:4])
        except (ValueError, TypeError):
            pass

    journal = item.get("publicationName", "") or ""
    abstract = item.get("abstract", "") or ""
    doi = item.get("doi", "") or ""

    url = ""
    urls = item.get("url", [])
    if isinstance(urls, list):
        for u in urls:
            if u.get("format") == "html":
                url = u.get("value", "")
                break
        if not url and urls:
            url = urls[0].get("value", "")
    if not url and doi:
        url = f"https://doi.org/{doi}"

    _sp_raw = (item.get("contentType", "") or "").lower().replace("-", " ")
    _sp_type_map = {
        "article": "article", "review article": "article", "brief communication": "article",
        "chapter": "book chapter", "book chapter": "book chapter",
        "conference paper": "conference", "conferencepaper": "conference",
        "report": "report",
    }
    doc_type = _sp_type_map.get(_sp_raw, "article" if _sp_raw else "")

    keywords_list = []
    kw_data = item.get("keyword", [])
    if isinstance(kw_data, list):
        keywords_list = kw_data[:10]

    language = item.get("language", "") or ""

    return {
        "source": "Springer Nature",
        "title": title,
        "authors": authors_list,
        "year": year,
        "journal": journal,
        "abstract": abstract,
        "url": url,
        "doi": doi,
        "keywords": keywords_list,
        "docType": doc_type,
        "language": language,
    }


def _springer_date_clause(year_from, year_to):
    if not (year_from or year_to):
        return ""
    start = f"{int(year_from)}-01-01" if year_from else "1900-01-01"
    end = f"{int(year_to)}-12-31" if year_to else f"{datetime.now().year + 1}-12-31"
    return f"datefrom:{start} dateto:{end}"


def search_springer(keywords, rows, filters=None):
    """Search Springer Nature and return normalized results."""
    filters = filters or {}
    cap = min(rows, _retrieval_cap("springer"))
    log = ConnectorLog("springer", page_size=SPRINGER_MAX_PAGE, cap=cap)
    api_key = get_springer_api_key()
    if not api_key:
        return _connector_result(log, 0, [], "Springer Nature API key not configured")

    query_text = keywords
    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" NOT {term}"

    # Publication-date limit: the documented datefrom:/dateto: constraints
    # (Basic and Premium plans). The date-facet-* parameters used previously
    # only shape facet counts and never filtered the results.
    date_clause = _springer_date_clause(filters.get("yearFrom"), filters.get("yearTo"))
    if date_clause:
        query_text = f"({query_text}) {date_clause}"

    params = {
        "q": query_text,
        "api_key": api_key,
        "p": SPRINGER_MAX_PAGE,
        "s": 1,
    }

    # NOTE: Springer Nature API does NOT support language:en in the query string —
    # appending it causes the API to return 0 results. Language filtering is not
    # available as a separate API parameter either, so we skip it entirely.
    log.set_request(query_text, params)

    normalized = []
    total_records = None
    error_msg = None
    while len(normalized) < cap:
        params["p"] = min(SPRINGER_MAX_PAGE, cap - len(normalized))
        params["s"] = len(normalized) + 1
        try:
            # Basic plan: 100 requests/minute → keep ≥ 0.6 s between calls.
            response = _http_get(SPRINGER_API_URL, log=log, params=params, timeout=30,
                                 min_interval=0.6, label="Springer Nature")
        except ConnectorRequestError as e:
            error_msg = str(e)
            break
        if response.status_code != 200:
            error_msg = _http_error_message("Springer Nature", response)
            break
        try:
            data = response.json()
        except ValueError:
            error_msg = f"Springer Nature returned a non-JSON response at record {params['s']}"
            break
        log.pages += 1

        if total_records is None:
            result_info = data.get("result") or [{}]
            try:
                total_records = int((result_info[0] or {}).get("total") or 0)
            except (TypeError, ValueError, IndexError, AttributeError):
                total_records = 0
        records = data.get("records") or []
        if not records:
            if len(normalized) < total_records:
                error_msg = (f"Springer Nature returned an empty page at record {params['s']} of "
                             f"{total_records}; retrieval stopped early")
            break
        normalized.extend(_normalize_springer_record(r) for r in records)
        if len(normalized) >= total_records:
            break

    total_records = total_records or 0
    if not error_msg and total_records > len(normalized) and len(normalized) >= cap:
        log.truncated = _cap_reason(cap, total_records, "RSP")
    return _connector_result(log, total_records, normalized, error_msg)


def search_pubmed(keywords, rows, filters=None):
    """Search PubMed via NCBI E-utilities and return normalized results."""
    import xml.etree.ElementTree as ET
    filters = filters or {}
    rows = min(rows, _retrieval_cap("pubmed"))
    api_key = get_pubmed_api_key()

    # Build query
    query_text = keywords

    # Add exclusion terms
    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" NOT {term}"

    # Language filter
    languages = filters.get("languages", [])
    if languages:
        lang_map = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese"}
        lang_terms = [f"{lang_map[lang]}[Language]" for lang in languages if lang in lang_map]
        if lang_terms:
            if len(lang_terms) == 1:
                query_text += f" AND {lang_terms[0]}"
            else:
                query_text += " AND (" + " OR ".join(lang_terms) + ")"

    # Year filter
    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from and year_to:
        query_text += f" AND {year_from}:{year_to}[dp]"
    elif year_from:
        query_text += f" AND {year_from}:3000[dp]"
    elif year_to:
        query_text += f" AND 1900:{year_to}[dp]"

    # Step 1: ESearch to get all PMIDs up to `rows`
    search_params = {
        "db": "pubmed",
        "term": query_text,
        "retmax": min(rows, 10000),
        "retmode": "json",
        "sort": "relevance",
    }
    if api_key:
        search_params["api_key"] = api_key

    try:
        resp = requests.get(PUBMED_API_URL + "esearch.fcgi", params=search_params, timeout=15)
        resp.raise_for_status()
        search_data = resp.json()
    except requests.RequestException as e:
        return {"numFound": 0, "docs": [], "error": f"Request failed: {e}"}

    esearch_result = search_data.get("esearchresult", {})
    total_records = int(esearch_result.get("count", 0))
    id_list = esearch_result.get("idlist", [])

    if not id_list:
        return {"numFound": total_records, "docs": []}

    # Step 2: EFetch in batches of 200 (XML gets unwieldy above that)
    normalized = []
    error_msg = None
    batch_size = 200
    for i in range(0, len(id_list), batch_size):
        batch_ids = id_list[i:i + batch_size]
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(batch_ids),
            "retmode": "xml",
        }
        if api_key:
            fetch_params["api_key"] = api_key

        try:
            resp = requests.get(PUBMED_API_URL + "efetch.fcgi", params=fetch_params, timeout=30)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except (requests.RequestException, ET.ParseError) as e:
            error_msg = f"Request failed while fetching article details: {e}"
            break

        for article in root.findall(".//PubmedArticle"):
            medline = article.find("MedlineCitation")
            if medline is None:
                continue
            art = medline.find("Article")
            if art is None:
                continue

            title = ""
            title_el = art.find("ArticleTitle")
            if title_el is not None and title_el.text:
                title = title_el.text

            authors_list = []
            author_list_el = art.find("AuthorList")
            if author_list_el is not None:
                for author in author_list_el.findall("Author"):
                    last = author.findtext("LastName", "")
                    fore = author.findtext("ForeName", "")
                    if last:
                        authors_list.append(f"{last} {fore}".strip())

            year = None
            pub_date = art.find(".//PubDate")
            if pub_date is not None:
                year_el = pub_date.find("Year")
                if year_el is not None and year_el.text:
                    try:
                        year = int(year_el.text)
                    except ValueError:
                        pass
                if year is None:
                    medline_date = pub_date.findtext("MedlineDate", "")
                    if medline_date:
                        try:
                            year = int(medline_date[:4])
                        except ValueError:
                            pass

            journal = ""
            journal_el = art.find("Journal")
            if journal_el is not None:
                journal = journal_el.findtext("Title", "") or journal_el.findtext("ISOAbbreviation", "")

            abstract = ""
            abstract_el = art.find(".//Abstract")
            if abstract_el is not None:
                parts = []
                for text_el in abstract_el.findall("AbstractText"):
                    if text_el.text:
                        parts.append(text_el.text)
                abstract = " ".join(parts)

            pmid = medline.findtext("PMID", "")
            doi = ""
            article_ids = article.find(".//ArticleIdList")
            if article_ids is not None:
                for aid in article_ids.findall("ArticleId"):
                    if aid.get("IdType") == "doi" and aid.text:
                        doi = aid.text
                        break

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
            if not url and doi:
                url = f"https://doi.org/{doi}"

            keywords_list = []
            mesh_list = medline.find("MeshHeadingList")
            if mesh_list is not None:
                for mesh in mesh_list.findall("MeshHeading"):
                    desc = mesh.findtext("DescriptorName", "")
                    if desc:
                        keywords_list.append(desc)
                keywords_list = keywords_list[:10]

            pub_type = ""
            pub_type_list = art.find("PublicationTypeList")
            if pub_type_list is not None:
                pt = pub_type_list.find("PublicationType")
                if pt is not None and pt.text:
                    pub_type = pt.text

            language = ""
            lang_list = art.find("Language")
            if lang_list is not None and lang_list.text:
                language = lang_list.text

            _pm_raw = (pub_type or "").lower()
            _pm_type_map = {
                "journal article": "article", "review": "article", "systematic review": "article",
                "meta-analysis": "article", "clinical trial": "article", "letter": "article",
                "editorial": "article", "comment": "article", "case reports": "article",
                "conference paper": "conference",
                "book": "book chapter", "book chapter": "book chapter",
                "preprint": "preprint",
            }
            doc_type = _pm_type_map.get(_pm_raw, "article" if _pm_raw else "")

            normalized.append({
                "source": "PubMed",
                "title": title or "Untitled",
                "authors": authors_list,
                "year": year,
                "journal": journal,
                "abstract": abstract,
                "url": url,
                "doi": doi,
                "keywords": keywords_list,
                "docType": doc_type,
                "language": language,
            })

    result = {"numFound": total_records, "docs": normalized}
    if error_msg:
        result["error"] = error_msg
    return result


def search_acm(keywords, rows, filters=None):
    """Search ACM Digital Library and return normalized results."""
    filters = filters or {}
    rows = min(rows, _retrieval_cap("acm"))

    query_text = keywords

    # Add exclusion terms
    exclude_terms = filters.get("excludeTerms", [])
    for term in exclude_terms:
        query_text += f" NOT {term}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "ReviewSentinelPrime/1.0 (research tool)",
    }

    # Optional API key for institutional access
    api_key = get_acm_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    page_size = 50  # ACM DL page size
    normalized = []
    total_records = 0
    error_msg = None
    page = 0

    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")

    while len(normalized) < rows:
        params = {
            "AllField": query_text,
            "pageSize": min(page_size, rows - len(normalized)),
            "startPage": page,
            "sortBy": "EpubDate",
        }
        if year_from:
            params["AfterYear"] = year_from
        if year_to:
            params["BeforeYear"] = year_to

        try:
            response = requests.get(ACM_API_URL, params=params, headers=headers, timeout=20)
            if response.status_code == 403:
                # Cloudflare bot mitigation blocks all server-side requests to dl.acm.org
                app.logger.warning(
                    "ACM DL search blocked by Cloudflare (403). "
                    "ACM does not offer a public API; server-side access is not supported."
                )
                error_msg = (
                    "ACM DL blocked the request (Cloudflare 403). ACM has no public "
                    "API; results from ACM must be collected manually."
                )
                break
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            error_msg = f"Request failed: {e}"
            break

        if total_records == 0:
            total_records = data.get("totalHits", 0)

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            title = item.get("title", "Untitled") or "Untitled"

            authors_list = []
            for author in (item.get("authors") or {}).get("allAuthorsNames", []):
                name = author.get("name", "")
                if name:
                    authors_list.append(name)

            year = None
            pub_date = item.get("publicationDate", "") or item.get("datePublished", "")
            if pub_date:
                try:
                    year = int(str(pub_date)[:4])
                except (ValueError, TypeError):
                    pass

            doi = item.get("doi", "") or ""
            url = f"https://doi.org/{doi}" if doi else ""
            if not url:
                acm_id = item.get("acmId", "")
                if acm_id:
                    url = f"https://dl.acm.org/doi/{acm_id}"

            parent = item.get("parent") or {}
            journal = parent.get("title", "") or ""
            abstract = item.get("abstract", "") or ""

            keywords_list = []
            kw_groups = (item.get("keywords") or {}).get("keywordGroups", [])
            for group in kw_groups:
                kws = group.get("keywords", [])
                if isinstance(kws, list):
                    keywords_list.extend(kws)
            keywords_list = [k for k in keywords_list if isinstance(k, str)][:10]

            language = item.get("language", "") or ""

            _acm_raw = (item.get("type", "") or "").lower()
            _acm_type_map = {
                "research-article": "article", "article": "article", "review-article": "article",
                "short-paper": "article",
                "extended-abstract": "conference", "proceeding": "conference", "conference-paper": "conference",
                "book-chapter": "book chapter", "chapter": "book chapter",
                "report": "report",
            }
            doc_type = _acm_type_map.get(_acm_raw, "article" if _acm_raw else "")

            normalized.append({
                "source": "ACM",
                "title": title,
                "authors": authors_list,
                "year": year,
                "journal": journal,
                "abstract": abstract,
                "url": url,
                "doi": doi,
                "keywords": keywords_list,
                "docType": doc_type,
                "language": language,
            })

        page += 1
        if len(normalized) >= total_records:
            break

    result = {"numFound": total_records, "docs": normalized}
    if error_msg:
        result["error"] = error_msg
    return result


def search_openalex(keywords, rows, filters=None):
    """Search OpenAlex and return normalised results.

    Without an API key, OpenAlex allows ~100 searches/day and page paging up
    to 10,000 results; with a free key (Settings or OPENALEX_API_KEY) the
    budget is 10× larger and cursor paging can go deeper.
    """
    filters = filters or {}
    api_key = get_openalex_api_key()
    rows = min(rows, _retrieval_cap("openalex"))
    cap = rows if api_key else min(rows, OPENALEX_BASIC_PAGING_LIMIT)
    log = ConnectorLog("openalex", page_size=OPENALEX_MAX_PAGE, cap=cap)

    filter_parts = []
    year_from = filters.get("yearFrom")
    year_to   = filters.get("yearTo")
    if year_from:
        filter_parts.append(f"publication_year:>{int(year_from) - 1}")
    if year_to:
        filter_parts.append(f"publication_year:<{int(year_to) + 1}")

    languages = filters.get("languages", [])
    if len(languages) == 1:
        filter_parts.append(f"language:{languages[0]}")

    doc_types = filters.get("docTypes", [])
    _oa_type_map = {
        "ART": "article", "COMM": "proceedings-article", "THESE": "dissertation",
        "COUV": "book-chapter", "REPORT": "report", "PREPRINT": "preprint", "OUV": "book",
    }
    oa_types = [_oa_type_map[t] for t in doc_types if t in _oa_type_map]
    if len(oa_types) == 1:
        filter_parts.append(f"type:{oa_types[0]}")

    search_text = strip_wildcards_in_quotes(keywords)
    params = {
        # OpenAlex's default search is stemmed and returns 400 for wildcards inside quotes.
        "search":   search_text,
        "per_page": OPENALEX_MAX_PAGE,
        "select":   ("id,doi,title,publication_year,primary_location,"
                     "authorships,abstract_inverted_index,keywords,type,language"),
    }
    if filter_parts:
        params["filter"] = ",".join(filter_parts)
    log.set_request(search_text, params)
    if api_key:
        params["api_key"] = api_key
    else:
        log.note("No OpenAlex API key: the keyless budget is about 100 searches per day "
                 "and each 100 records costs one search. Add a free key in Settings.")

    # Searches with more than 5 boolean operators: 1 req/s keyless, 5 req/s with a key.
    n_ops = len(re.findall(r"\b(?:AND|OR|NOT)\b", re.sub(r'"[^"]*"', " ", search_text)))
    if n_ops > 5:
        min_interval = 0.25 if api_key else 1.05
    else:
        min_interval = 0.05 if api_key else 0.12

    _hdrs = {"User-Agent": "ReviewSentinelPrime"}
    _base = "https://api.openalex.org/works"
    use_cursor = bool(api_key)
    cursor = "*"
    page = 1
    results = []
    total = None
    error_msg = None
    while len(results) < cap:
        req = dict(params)
        req["per_page"] = min(OPENALEX_MAX_PAGE, cap - len(results))
        if use_cursor:
            req["cursor"] = cursor
        else:
            if (page - 1) * OPENALEX_MAX_PAGE >= OPENALEX_BASIC_PAGING_LIMIT:
                break
            req["page"] = page
            req["per_page"] = OPENALEX_MAX_PAGE
        try:
            resp = _http_get(_base, log=log, params=req, headers=_hdrs, timeout=30,
                             min_interval=min_interval, label="OpenAlex")
        except ConnectorRequestError as e:
            error_msg = str(e)
            if e.status == 429 and not api_key:
                error_msg += " Add a free OpenAlex API key in Settings to raise the daily budget 10×."
            break
        if resp.status_code != 200:
            error_msg = _http_error_message("OpenAlex", resp)
            break
        try:
            data = resp.json()
        except ValueError:
            error_msg = f"OpenAlex returned a non-JSON response on page {page}"
            break
        log.pages += 1
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            log.params["creditsRemaining"] = remaining

        meta = data.get("meta") or {}
        if total is None:
            total = int(meta.get("count") or 0)
        batch = data.get("results") or []
        if not batch:
            if len(results) < min(total, cap):
                error_msg = (f"OpenAlex returned an empty page after {len(results)} of {total} "
                             "records; retrieval stopped early")
            break
        results.extend(batch)
        if len(results) >= total:
            break
        if use_cursor:
            cursor = meta.get("next_cursor") or ""
            if not cursor:
                break
        page += 1

    total = total or 0
    results = results[:cap]
    if not error_msg and total > len(results):
        if not api_key and len(results) >= OPENALEX_BASIC_PAGING_LIMIT:
            log.truncated = (f"OpenAlex page paging stops at {OPENALEX_BASIC_PAGING_LIMIT:,} results; "
                             f"the API reported {total:,}. An API key enables cursor paging.")
        elif len(results) >= cap:
            log.truncated = _cap_reason(cap, total, "Requested")
    normalized = [_normalize_openalex_doc(w, "OpenAlex") for w in results]
    return _connector_result(log, total, normalized, error_msg)


# ── EBSCO Discovery Service (EDS) ────────────────────────────────────────
# EDS needs a two-step handshake before any search: uidauth returns an
# AuthToken (valid ~30 min, cached below) and CreateSession returns a
# per-search SessionToken that must be released again with EndSession.

_ebsco_auth_lock = threading.Lock()
_ebsco_auth_cache = {"token": "", "expires": 0.0, "fingerprint": ""}

_EBSCO_TAG_RE = re.compile(r"<[^>]+>")


def _ebsco_text(value):
    """Flatten an EDS field, which carries display markup such as <i>/<relatesTo>."""
    if not value:
        return ""
    text = _EBSCO_TAG_RE.sub(" ", html.unescape(str(value)))
    return re.sub(r"\s+", " ", text).strip()


def _ebsco_auth_token(creds, force_refresh=False):
    """Return a cached EDS AuthToken, re-authenticating when it expires."""
    fingerprint = f"{creds['userId']}\x00{creds['profile']}"
    with _ebsco_auth_lock:
        if (not force_refresh
                and _ebsco_auth_cache["token"]
                and _ebsco_auth_cache["fingerprint"] == fingerprint
                and _ebsco_auth_cache["expires"] > time.time()):
            return _ebsco_auth_cache["token"], None
        try:
            r = requests.post(
                EBSCO_AUTH_URL,
                json={"UserId": creds["userId"], "Password": creds["password"]},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=20,
            )
        except requests.exceptions.Timeout:
            return "", "EBSCO authentication timed out. Please try again in a minute."
        except requests.RequestException as e:
            return "", f"EBSCO authentication failed: {e}"

        if r.status_code in (401, 403):
            return "", (
                f"EBSCO rejected the stored credentials (HTTP {r.status_code}). "
                "Check the User ID and Password in Settings — EDS access requires an "
                "active institutional subscription."
            )
        if r.status_code != 200:
            return "", f"EBSCO authentication returned status {r.status_code}."
        try:
            data = r.json()
        except ValueError:
            return "", "EBSCO authentication returned a malformed response."

        token = data.get("AuthToken") or ""
        if not token:
            return "", "EBSCO authentication did not return an AuthToken."
        # Refresh a minute early so a long-running search cannot expire mid-flight.
        timeout = _to_int(data.get("AuthTimeout"), 1800)
        _ebsco_auth_cache.update({
            "token": token,
            "expires": time.time() + max(60, timeout - 60),
            "fingerprint": fingerprint,
        })
        return token, None


def _ebsco_create_session(auth_token, profile):
    """Open an EDS session and return (session_token, error)."""
    try:
        r = requests.get(
            f"{EBSCO_API_URL}/CreateSession",
            params={"profile": profile, "guest": "n"},
            headers={"x-authenticationToken": auth_token, "Accept": "application/json"},
            timeout=20,
        )
    except requests.exceptions.Timeout:
        return "", "EBSCO session request timed out. Please try again in a minute."
    except requests.RequestException as e:
        return "", f"EBSCO session request failed: {e}"

    if r.status_code == 400:
        return "", (
            f"EBSCO rejected profile '{profile}'. Confirm the Profile ID with your "
            "library administrator (it is often 'edsapi' or similar)."
        )
    if r.status_code in (401, 403):
        return "", "EBSCO session was refused. The authentication token may have expired."
    if r.status_code != 200:
        return "", f"EBSCO CreateSession returned status {r.status_code}."
    try:
        token = (r.json() or {}).get("SessionToken") or ""
    except ValueError:
        return "", "EBSCO CreateSession returned a malformed response."
    if not token:
        return "", "EBSCO CreateSession did not return a SessionToken."
    return token, None


def _ebsco_end_session(auth_token, session_token):
    """Release an EDS session; failures are non-fatal and intentionally ignored."""
    try:
        requests.get(
            f"{EBSCO_API_URL}/EndSession",
            params={"sessiontoken": session_token},
            headers={"x-authenticationToken": auth_token, "Accept": "application/json"},
            timeout=10,
        )
    except requests.RequestException:
        pass


_EBSCO_PUBTYPE_MAP = {
    "academicjournal": "article",
    "periodical": "article",
    "magazine": "article",
    "newspaperarticle": "article",
    "newspaper": "article",
    "conferencematerial": "conference",
    "conferenceproceeding": "conference",
    "book": "book chapter",
    "ebook": "book chapter",
    "bookseries": "book chapter",
    "dissertation": "thesis",
    "report": "report",
}


def _ebsco_items_by_group(record, group):
    """Collect the display Items EDS tags with a given group code (Ti, Ab, Au...)."""
    out = []
    for item in record.get("Items") or []:
        if (item.get("Group") or "") == group:
            text = _ebsco_text(item.get("Data"))
            if text:
                out.append(text)
    return out


def _normalize_ebsco_record(record, source_label="EBSCO"):
    """Normalize one EDS Record to RSP doc format."""
    header = record.get("Header") or {}
    bib_record = ((record.get("RecordInfo") or {}).get("BibRecord") or {})
    bib_entity = bib_record.get("BibEntity") or {}
    relationships = bib_record.get("BibRelationships") or {}

    titles = bib_entity.get("Titles") or []
    title = _ebsco_text(titles[0].get("TitleFull")) if titles else ""
    if not title:
        item_titles = _ebsco_items_by_group(record, "Ti")
        title = item_titles[0] if item_titles else ""

    authors = []
    for rel in relationships.get("HasContributorRelationships") or []:
        name = (((rel.get("PersonEntity") or {}).get("Name") or {}).get("NameFull"))
        name = _ebsco_text(name)
        if name:
            authors.append(name)
    if not authors:
        authors = _ebsco_items_by_group(record, "Au")

    journal, year = "", None
    for rel in relationships.get("IsPartOfRelationships") or []:
        parent = rel.get("BibEntity") or {}
        parent_titles = parent.get("Titles") or []
        if not journal and parent_titles:
            journal = _ebsco_text(parent_titles[0].get("TitleFull"))
        for date in parent.get("Dates") or []:
            if year is None:
                year = _to_int(date.get("Y"), None)
        if journal and year is not None:
            break

    doi = ""
    for ident in bib_entity.get("Identifiers") or []:
        if (ident.get("Type") or "").lower() == "doi":
            doi = _ebsco_text(ident.get("Value"))
            break

    abstracts = _ebsco_items_by_group(record, "Ab")
    keywords = []
    for subject in bib_entity.get("Subjects") or []:
        text = _ebsco_text(subject.get("SubjectFull"))
        if text:
            keywords.append(text)

    language = ""
    for lang in bib_entity.get("Languages") or []:
        language = _normalize_language(_ebsco_text(lang.get("Text"))) or ""
        if language:
            break

    raw_type = (header.get("PubTypeId") or header.get("PubType") or "").lower().replace(" ", "")
    doc_type = _EBSCO_PUBTYPE_MAP.get(raw_type, "article" if raw_type else "")

    return {
        "title":    title,
        "authors":  authors,
        "year":     year,
        "journal":  journal,
        "doi":      doi,
        "url":      f"https://doi.org/{doi}" if doi else _ebsco_text(record.get("PLink")),
        "abstract": abstracts[0] if abstracts else "",
        "keywords": keywords,
        "docType":  doc_type,
        "source":   source_label,
        "sources":  [source_label],
        "language": language,
    }


def search_ebsco(keywords, rows, filters=None):
    """Search EBSCO Discovery Service and return normalised results.

    Requires an institutional EDS subscription: User ID, Password and Profile ID
    are configured in Settings. Results are capped at 100 records per page.
    """
    filters = filters or {}
    rows = min(rows, _retrieval_cap("ebsco"))
    creds = get_ebsco_credentials()
    if not (creds["userId"] and creds["password"] and creds["profile"]):
        return {
            "numFound": 0,
            "docs": [],
            "error": ("EBSCO credentials are not configured. Add your EDS User ID, "
                      "Password and Profile ID in Settings."),
        }

    auth_token, err = _ebsco_auth_token(creds)
    if err:
        return {"numFound": 0, "docs": [], "error": err}

    session_token, err = _ebsco_create_session(auth_token, creds["profile"])
    if err:
        # A cached token that the server has already dropped looks like a 401 here.
        auth_token, auth_err = _ebsco_auth_token(creds, force_refresh=True)
        if auth_err:
            return {"numFound": 0, "docs": [], "error": auth_err}
        session_token, err = _ebsco_create_session(auth_token, creds["profile"])
        if err:
            return {"numFound": 0, "docs": [], "error": err}

    params = {
        "query":          keywords,
        "searchmode":     "all",
        "view":           "detailed",
        "highlight":      "n",
        "includefacets":  "n",
        "resultsperpage": min(rows, 100),
        "pagenumber":     1,
        "sort":           "date",
    }

    year_from = filters.get("yearFrom")
    year_to   = filters.get("yearTo")
    if year_from or year_to:
        params["limiter"] = f"DT1:{year_from or '1900'}-01/{year_to or '3000'}-12"

    languages = filters.get("languages", [])
    if languages:
        _ebsco_lang_map = {
            "en": "English", "fr": "French", "de": "German", "es": "Spanish",
            "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese",
        }
        names = [_ebsco_lang_map.get(l, l) for l in languages]
        params["facetfilter"] = "1," + ",".join(f"Language:{n}" for n in names)

    headers = {
        "x-authenticationToken": auth_token,
        "x-sessionToken": session_token,
        "Accept": "application/json",
    }

    normalized = []
    total = 0
    error_msg = None
    # EDS caps a result set at 100 per page; stop well before the server's own
    # deep-paging ceiling rather than hammering it.
    max_pages = 50

    try:
        for page in range(1, max_pages + 1):
            params["pagenumber"] = page
            params["resultsperpage"] = min(rows - len(normalized), 100)
            if params["resultsperpage"] <= 0:
                break
            try:
                r = requests.get(
                    f"{EBSCO_API_URL}/Search", params=params, headers=headers, timeout=30
                )
            except requests.exceptions.Timeout:
                error_msg = (
                    "EBSCO timed out while retrieving results. "
                    + ("Partial results were kept." if normalized else "Please try again in a minute.")
                )
                break
            except requests.RequestException as e:
                error_msg = f"Request failed: {e}"
                break

            if r.status_code in (401, 403):
                error_msg = "EBSCO session expired or was refused. Please run the search again."
                break
            if r.status_code == 429:
                error_msg = (
                    "EBSCO rate-limited this search (HTTP 429). Wait a moment and try again. "
                    "Tip: reduce Max records per database to lower burst traffic."
                )
                break
            if r.status_code != 200:
                error_msg = f"EBSCO API returned status {r.status_code}."
                break

            try:
                payload = r.json() or {}
            except ValueError:
                error_msg = "EBSCO returned a malformed response."
                break

            search_result = payload.get("SearchResult") or {}
            if page == 1:
                total = _to_int((search_result.get("Statistics") or {}).get("TotalHits"), 0)

            records = ((search_result.get("Data") or {}).get("Records")) or []
            if not records:
                break
            normalized.extend(_normalize_ebsco_record(rec) for rec in records)
            if len(normalized) >= min(rows, total or rows):
                break
    finally:
        _ebsco_end_session(auth_token, session_token)

    result = {"numFound": total, "docs": normalized[:rows]}
    if error_msg:
        result["error"] = error_msg
    return result


# ── Web of Science (Starter API) ─────────────────────────────────────────

_WOS_TYPE_MAP = {
    "article": "article",
    "review": "article",
    "early access": "article",
    "editorial material": "article",
    "letter": "article",
    "news item": "article",
    "correction": "article",
    "meeting abstract": "conference",
    "proceedings paper": "conference",
    "book chapter": "book chapter",
    "book": "book chapter",
    "book review": "book chapter",
    "preprint": "preprint",
}

# The free Starter plan allows 1 request/second; stay under it globally.
_wos_throttle_lock = threading.Lock()
_wos_last_call = [0.0]


def _wos_throttle():
    with _wos_throttle_lock:
        elapsed = time.time() - _wos_last_call[0]
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        _wos_last_call[0] = time.time()


def _normalize_wos_doc(hit, source_label="Web of Science"):
    """Normalize one Web of Science Starter API hit to RSP doc format."""
    source_info = hit.get("source") or {}
    identifiers = hit.get("identifiers") or {}
    names = hit.get("names") or {}
    links = hit.get("links") or {}

    authors = []
    for author in names.get("authors") or []:
        name = (author.get("displayName") or author.get("wosStandard") or "").strip()
        if name:
            authors.append(name)

    doi = (identifiers.get("doi") or "").strip()
    raw_types = [str(t).strip().lower() for t in (hit.get("types") or []) if t]
    doc_type = ""
    for raw in raw_types:
        if raw in _WOS_TYPE_MAP:
            doc_type = _WOS_TYPE_MAP[raw]
            break

    keywords = list((hit.get("keywords") or {}).get("authorKeywords") or [])

    return {
        "title":    (hit.get("title") or "").strip(),
        "authors":  authors,
        "year":     _to_int(source_info.get("publishYear"), None),
        "journal":  (source_info.get("sourceTitle") or "").strip(),
        "doi":      doi,
        "url":      f"https://doi.org/{doi}" if doi else (links.get("record") or ""),
        # The Starter API does not expose abstracts; only Expanded does.
        "abstract": "",
        "keywords": keywords,
        "docType":  doc_type,
        "source":   source_label,
        "sources":  [source_label],
        "language": "",
    }


def _wos_listify(value):
    """WoS Expanded collapses single-element arrays to bare objects."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_wos_expanded_doc(rec, source_label="Web of Science"):
    """Normalize one Web of Science Expanded API record to RSP doc format."""
    static_data = rec.get("static_data") or {}
    summary = static_data.get("summary") or {}
    metadata = static_data.get("fullrecord_metadata") or {}

    title, journal = "", ""
    for entry in _wos_listify((summary.get("titles") or {}).get("title")):
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("type") or "").lower()
        content = str(entry.get("content") or "").strip()
        if kind == "item" and not title:
            title = content
        elif kind == "source" and not journal:
            journal = content

    authors = []
    for name in _wos_listify((summary.get("names") or {}).get("name")):
        if not isinstance(name, dict) or (name.get("role") or "").lower() != "author":
            continue
        display = str(name.get("display_name") or name.get("full_name") or "").strip()
        if display:
            authors.append(display)

    doi = ""
    identifiers = (((rec.get("dynamic_data") or {}).get("cluster_related") or {}).get("identifiers") or {})
    for ident in _wos_listify(identifiers.get("identifier")):
        if isinstance(ident, dict) and (ident.get("type") or "").lower() == "doi":
            doi = str(ident.get("value") or "").strip()
            break

    paragraphs = []
    for abstract in _wos_listify((metadata.get("abstracts") or {}).get("abstract")):
        if not isinstance(abstract, dict):
            continue
        for para in _wos_listify((abstract.get("abstract_text") or {}).get("p")):
            text = str(para or "").strip()
            if text:
                paragraphs.append(text)

    keywords = [str(k).strip() for k in _wos_listify((metadata.get("keywords") or {}).get("keyword")) if k]

    doc_type = ""
    for raw in _wos_listify((summary.get("doctypes") or {}).get("doctype")):
        mapped = _WOS_TYPE_MAP.get(str(raw).strip().lower())
        if mapped:
            doc_type = mapped
            break

    language = ""
    for lang in _wos_listify((metadata.get("languages") or {}).get("language")):
        raw = lang.get("content") if isinstance(lang, dict) else lang
        language = _normalize_language(str(raw or "")) or ""
        if language:
            break

    uid = str(rec.get("UID") or "").strip()
    return {
        "title":    title,
        "authors":  authors,
        "year":     _to_int((summary.get("pub_info") or {}).get("pubyear"), None),
        "journal":  journal,
        "doi":      doi,
        "url":      (f"https://doi.org/{doi}" if doi
                     else (f"https://www.webofscience.com/wos/woscc/full-record/{uid}" if uid else "")),
        "abstract": " ".join(paragraphs),
        "keywords": keywords,
        "docType":  doc_type,
        "source":   source_label,
        "sources":  [source_label],
        "language": language,
    }


def search_wos(keywords, rows, filters=None):
    """Search Web of Science and return normalised results.

    Two editions share the same X-ApiKey: the free Starter API (no abstracts)
    and the subscription Expanded API (full records including abstracts).
    """
    filters = filters or {}
    rows = min(rows, _retrieval_cap("wos"))
    key = get_wos_api_key()
    if not key:
        return {
            "numFound": 0,
            "docs": [],
            "error": ("Web of Science API key is not configured. "
                      "Add a free Starter API key in Settings."),
        }

    query = _build_wos_query(keywords, filters)
    if get_wos_edition() == "expanded":
        return _search_wos_expanded(key, query, rows)
    return _search_wos_starter(key, query, rows)


def _build_wos_query(keywords, filters):
    """Build a WoS field-tagged query; identical syntax for both editions."""
    query = f"TS=({keywords})"
    year_from = filters.get("yearFrom")
    year_to   = filters.get("yearTo")
    if year_from and year_to:
        query += f" AND PY=({year_from}-{year_to})"
    elif year_from:
        query += f" AND PY=({year_from}-3000)"
    elif year_to:
        query += f" AND PY=(1900-{year_to})"

    languages = filters.get("languages", [])
    _wos_lang_map = {
        "en": "English", "fr": "French", "de": "German", "es": "Spanish",
        "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese",
    }
    if languages:
        names = [_wos_lang_map.get(l, l) for l in languages]
        query += " AND LA=(" + " OR ".join(names) + ")"
    return query


def _search_wos_starter(key, query, rows):
    """Starter API: free key, basic metadata, no abstracts."""
    headers = {"X-ApiKey": key, "Accept": "application/json"}
    per_page = 50  # Starter API hard limit

    normalized = []
    total = 0
    error_msg = None
    # Free plans allow as few as 50 requests per DAY, so never burn more than
    # this many on a single search regardless of how many records exist.
    max_pages = 20

    for page in range(1, max_pages + 1):
        params = {
            "q": query,
            "db": "WOS",
            "limit": min(per_page, max(1, rows - len(normalized))),
            "page": page,
        }
        _wos_throttle()
        try:
            r = requests.get(WOS_API_URL, params=params, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            error_msg = (
                "Web of Science timed out. "
                + ("Partial results were kept." if normalized else "Please try again in a minute.")
            )
            break
        except requests.RequestException as e:
            error_msg = f"Request failed: {e}"
            break

        error_msg = _wos_http_error(r, normalized)
        if error_msg or r.status_code != 200:
            break

        try:
            payload = r.json() or {}
        except ValueError:
            error_msg = "Web of Science returned a malformed response."
            break

        if page == 1:
            total = _to_int((payload.get("metadata") or {}).get("total"), 0)

        hits = payload.get("hits") or []
        if not hits:
            break
        normalized.extend(_normalize_wos_doc(h) for h in hits)
        if len(normalized) >= min(rows, total or rows):
            break

    result = {"numFound": total, "docs": normalized[:rows]}
    if error_msg:
        result["error"] = error_msg
    return result


def _search_wos_expanded(key, query, rows):
    """Expanded API: institutional subscription, full records with abstracts."""
    headers = {"X-ApiKey": key, "Accept": "application/json"}
    per_page = 100  # Expanded API hard limit

    normalized = []
    total = 0
    error_msg = None
    max_pages = 50

    for page in range(max_pages):
        first_record = page * per_page + 1
        params = {
            "databaseId": "WOS",
            "usrQuery": query,
            "count": min(per_page, max(1, rows - len(normalized))),
            "firstRecord": first_record,
            "optionView": "FR",
            "lang": "en",
        }
        _wos_throttle()
        try:
            r = requests.get(WOS_EXPANDED_API_URL, params=params, headers=headers, timeout=45)
        except requests.exceptions.Timeout:
            error_msg = (
                "Web of Science timed out. "
                + ("Partial results were kept." if normalized else "Please try again in a minute.")
            )
            break
        except requests.RequestException as e:
            error_msg = f"Request failed: {e}"
            break

        error_msg = _wos_http_error(r, normalized, expanded=True)
        if error_msg or r.status_code != 200:
            break

        try:
            payload = r.json() or {}
        except ValueError:
            error_msg = "Web of Science returned a malformed response."
            break

        if page == 0:
            total = _to_int((payload.get("QueryResult") or {}).get("RecordsFound"), 0)

        records = _wos_listify(
            ((((payload.get("Data") or {}).get("Records") or {}).get("records") or {}).get("REC"))
        )
        if not records:
            break
        normalized.extend(_normalize_wos_expanded_doc(rec) for rec in records)
        if len(normalized) >= min(rows, total or rows):
            break

    result = {"numFound": total, "docs": normalized[:rows]}
    if error_msg:
        result["error"] = error_msg
    return result


def _wos_http_error(r, collected, expanded=False):
    """Map a WoS HTTP status to a user-facing message, or None when OK."""
    if r.status_code == 200:
        return None
    partial = " Partial results were kept." if collected else ""
    if r.status_code in (401, 403):
        if expanded:
            return (
                f"Web of Science rejected the key for the Expanded API ({r.status_code}). "
                "Expanded requires a paid institutional subscription — if you only have a free "
                "key, switch the edition back to Starter in Settings."
            )
        return (
            f"Web of Science rejected the API key ({r.status_code}). Verify it at "
            "developer.clarivate.com — Starter API keys must be tied to a registered application."
        )
    if r.status_code == 429:
        return (
            "Web of Science rate limit reached (HTTP 429). Free Starter plans allow as few as "
            f"50 requests per day.{partial}"
        )
    return f"Web of Science API returned status {r.status_code}.{partial}"


# ── Multi-database fan-out shared by ad-hoc, streamed and scheduled runs ────

# Fixed order used to assemble results before deduplication, so which
# database "keeps" a duplicate no longer depends on which API answered first.
# Curated indexes come first; broad aggregators (whose records duplicate
# almost everything else) come last.
_DB_ORDER = ["ieee", "scopus", "sciencedirect", "inspec", "springer", "wos",
             "ebsco", "acm", "pubmed", "hal", "arxiv", "openalex"]

_DB_LABELS = {
    "hal": "HAL", "arxiv": "arXiv", "ieee": "IEEE Xplore", "scopus": "Scopus",
    "inspec": "Inspec", "sciencedirect": "ScienceDirect", "springer": "Springer Nature",
    "pubmed": "PubMed", "acm": "ACM DL", "openalex": "OpenAlex", "ebsco": "EBSCO",
    "wos": "Web of Science",
}

# doc["source"] label written by each connector -> database key
_SOURCE_TO_DB = {
    "HAL": "hal", "arXiv": "arxiv", "IEEE": "ieee", "Scopus": "scopus", "Inspec": "inspec",
    "ScienceDirect": "sciencedirect", "Springer Nature": "springer", "PubMed": "pubmed",
    "ACM": "acm", "OpenAlex": "openalex", "EBSCO": "ebsco", "Web of Science": "wos",
}

_FILTER_REASONS = ["duplicates", "year_range", "doc_type", "language", "exclude_terms",
                   "must_include_terms", "include_venues", "exclude_venues"]


def _connector_table():
    return {
        "hal": search_hal, "arxiv": search_arxiv, "ieee": search_ieee,
        "scopus": search_scopus, "inspec": search_inspec,
        "sciencedirect": search_sciencedirect, "springer": search_springer,
        "pubmed": search_pubmed, "acm": search_acm, "openalex": search_openalex,
        "ebsco": search_ebsco, "wos": search_wos,
    }


def _run_connectors(databases, keywords, rows, api_filters, count_filters=None):
    """Run the selected connectors in parallel; yield (db_key, result, count)
    as each database finishes.

    When count_filters is given, a count-only request (rows=1) runs alongside
    each search so the all-years total is known while only in-range records
    are downloaded; `count` is that result, otherwise None. Unknown database
    IDs and crashed connectors yield an error result instead of disappearing.
    """
    table = _connector_table()
    hal_query = build_query(keywords)
    unknown = [db for db in databases if db not in table]
    for db in unknown:
        yield db, {"numFound": 0, "docs": [], "error": f"Unknown database '{db}' — not searched"}, None
    jobs = [db for db in dict.fromkeys(databases) if db in table]
    if not jobs:
        return

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            app.logger.exception("Connector %s crashed", getattr(fn, "__name__", fn))
            return {"numFound": 0, "docs": [], "error": f"Connector crashed: {exc}"}

    want_count = count_filters is not None
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_job = {}
        for db in jobs:
            query = hal_query if db == "hal" else keywords
            future_to_job[executor.submit(_safe, table[db], query, rows, api_filters)] = (db, "main")
            if want_count:
                future_to_job[executor.submit(_safe, table[db], query, 1, count_filters)] = (db, "count")
        done = {db: {} for db in jobs}
        for future in as_completed(future_to_job):
            db, part = future_to_job[future]
            result = future.result()
            if part == "main" and not result.get("error"):
                cap = _retrieval_cap(db)
                retrieved = len(result.get("docs") or [])
                total = int(result.get("numFound") or 0)
                if cap <= rows and retrieved >= cap and total > retrieved:
                    log = result.get("log") or {}
                    result["log"] = log
                    if not log.get("truncated"):
                        log["truncated"] = _cap_reason(cap, total, "RSP")
            done[db][part] = result
            if "main" in done[db] and (not want_count or "count" in done[db]):
                yield db, done[db]["main"], done[db].get("count")


def _db_status_entry(db, result):
    """Per-database identification record with an explicit status.

    complete — every record the API reported was retrieved;
    partial  — records were retrieved but some are missing (error mid-way,
               paging limit, RSP cap or a short page);
    failed   — nothing was retrieved because of an error.
    A database that reports matches but returns none is never "complete".
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    docs = result.get("docs") or []
    found = int(result.get("numFound") or 0)
    retrieved = len(docs)
    error = result.get("error") or ""
    log = result.get("log") or {}
    truncated = log.get("truncated") or ""

    if error and retrieved == 0:
        status = "failed"
    elif error:
        status = "partial"
    elif found > retrieved:
        status = "failed" if retrieved == 0 else "partial"
        if not truncated:
            truncated = (f"API reported {found:,} records but returned none" if retrieved == 0
                         else f"Only {retrieved:,} of {found:,} records retrieved (retrieval stopped early)")
    else:
        status = "complete"

    entry = {
        "found": found,
        "retrieved": retrieved,
        # PRISMA item 6 reports the date each database was searched
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(timespec="seconds") + "Z",
        "status": status,
        "log": log,
    }
    if error:
        entry["error"] = error
    if truncated:
        entry["warning"] = truncated
    entry["message"] = error or truncated or (
        "The API reported no matching records" if found == 0 else "All reported records retrieved")
    return entry


def _apply_date_limit(entry, count_result):
    """Turn an in-range search plus its all-years count into identification
    numbers: found = all years, removedDateAtDatabase = found − in range."""
    log = entry.setdefault("log", {})
    entry["dateLimitAtDatabase"] = True
    in_range_known = not (entry["status"] == "failed" and entry["found"] == 0)
    entry["foundInRange"] = entry["found"] if in_range_known else None
    count_ok = count_result is not None and not count_result.get("error")
    if count_ok:
        all_years = int(count_result.get("numFound") or 0)
        if in_range_known:
            all_years = max(all_years, entry["found"])
            entry["removedDateAtDatabase"] = all_years - entry["found"]
        else:
            entry["removedDateAtDatabase"] = None
        entry["found"] = all_years
        log["allYearsQuery"] = (count_result.get("log") or {}).get("querySent", "")
    else:
        entry["removedDateAtDatabase"] = None
        why = (count_result or {}).get("error") or "not run"
        note = (f"All-years count unavailable ({why}); records identified for this database "
                "is the in-range total only")
        log.setdefault("notes", []).append(note)
        entry["dateLimitNote"] = note
    return entry


def _make_db_entry(db, result, count_result, date_limited):
    entry = _db_status_entry(db, result)
    if date_limited:
        _apply_date_limit(entry, count_result)
    return entry


def _identification_breakdown(prisma, db_counts):
    """Per-database and total records removed by the date limit at the
    database and records not retrieved (cap, error, short paging), so the
    PRISMA flow adds up: identified − date-limited − not retrieved = retrieved."""
    removed_total = not_retrieved_total = 0
    for e in db_counts.values():
        removed = e.get("removedDateAtDatabase") or 0
        missing = max(0, int(e.get("found") or 0) - removed - int(e.get("retrieved") or 0))
        e["notRetrieved"] = missing
        removed_total += removed
        not_retrieved_total += missing
    ident = prisma.setdefault("identification", {})
    ident["removed_date_at_database"] = removed_total
    ident["not_retrieved"] = not_retrieved_total


def _check_date_limit_applied(db_counts):
    """Safety net: if a database was sent the year range but many of the
    records it returned fall outside it, the API probably ignored the limit."""
    for db, e in db_counts.items():
        if not e.get("dateLimitAtDatabase"):
            continue
        retrieved = int(e.get("retrieved") or 0)
        outside = int((e.get("removed") or {}).get("year_range") or 0)
        if retrieved and outside > max(2, 0.05 * retrieved):
            warn = (f"{_DB_LABELS.get(db, db)} may have ignored the date limit: {outside:,} of "
                    f"{retrieved:,} retrieved records were outside the year range (removed by RSP)")
            if e.get("dateLimitWarning") == warn:
                continue  # already recorded (cache re-filter)
            e["dateLimitWarning"] = warn
            e["message"] = f"{e.get('message', '')} — {warn}".strip(" —")
            e.setdefault("log", {}).setdefault("notes", []).append(warn)


def _search_filters_for_apis(filters):
    """(api_filters, count_filters): filters sent to the APIs, and — when a
    year range goes to the APIs — the same filters without it for the
    all-years count-only request."""
    api_filters = dict(_api_filters(filters))
    if get_retrieval_settings()["yearAtDatabase"]:
        for key in ("yearFrom", "yearTo"):
            if filters.get(key):
                api_filters[key] = filters[key]
    if api_filters.get("yearFrom") or api_filters.get("yearTo"):
        count_filters = {k: v for k, v in api_filters.items() if k not in ("yearFrom", "yearTo")}
        return api_filters, count_filters
    return api_filters, None


def _progress_event(db, entry):
    event = {
        "type": "progress", "db": db, "found": entry["found"],
        "retrieved": entry["retrieved"], "status": entry["status"],
        "message": entry["message"],
    }
    if entry.get("error"):
        event["error"] = entry["error"]
    if entry.get("warning"):
        event["warning"] = entry["warning"]
    return event


def _assemble_docs(results_by_db):
    """Concatenate connector docs in the fixed database order and remember
    which database each record came from (for per-database filter counts)."""
    order = [db for db in _DB_ORDER if db in results_by_db]
    order += [db for db in results_by_db if db not in order]
    all_docs, origin = [], {}
    for db in order:
        for doc in results_by_db[db].get("docs") or []:
            all_docs.append(doc)
            origin[id(doc)] = db
    return all_docs, origin


def _attach_filter_breakdown(db_counts, prisma, included_docs, origin):
    """Move prisma["removed_by_db"] into each database's identification entry."""
    removed_by_db = prisma.pop("removed_by_db", {}) or {}
    included = {}
    for doc in included_docs:
        db = origin.get(id(doc))
        if db:
            included[db] = included.get(db, 0) + 1
    for db, entry in db_counts.items():
        entry["removed"] = {reason: (removed_by_db.get(reason) or {}).get(db, 0)
                            for reason in _FILTER_REASONS}
        entry["included"] = included.get(db, 0)


def _coverage_warning(db_counts):
    """One line naming every database that failed or was only partly retrieved."""
    parts = []
    for db, e in (db_counts or {}).items():
        status = e.get("status")
        if status in ("failed", "partial"):
            parts.append(f"{_DB_LABELS.get(db, db)} {status}: {e.get('message', '')}")
    return "; ".join(parts)


def _search_log_rows(db_counts):
    """Flatten identification entries into the exported per-database log."""
    rows = []
    for db, e in (db_counts or {}).items():
        log = e.get("log") or {}
        removed = e.get("removed") or {}
        row = {
            "database": _DB_LABELS.get(db, db),
            "database_id": db,
            "timestamp": e.get("timestamp") or log.get("started") or e.get("date", ""),
            "status": e.get("status") or ("failed" if e.get("error") and not e.get("retrieved") else
                                          "partial" if e.get("error") or e.get("warning") else "complete"),
            "query_sent": log.get("querySent", ""),
            "total_reported": e.get("found", 0),
            "retrieved": e.get("retrieved", 0),
            "date_limit_at_database": "yes" if e.get("dateLimitAtDatabase") else "no",
            "reported_in_date_range": e.get("foundInRange", "") if e.get("dateLimitAtDatabase") else "",
            "removed_date_at_database": (e.get("removedDateAtDatabase") if e.get("removedDateAtDatabase") is not None
                                         else ("unknown" if e.get("dateLimitAtDatabase") else 0)),
            "not_retrieved": e.get("notRetrieved", max(0, int(e.get("found") or 0) - int(e.get("retrieved") or 0))),
        }
        for reason in _FILTER_REASONS:
            row[f"removed_{reason}"] = removed.get(reason, 0)
        row.update({
            "included": e.get("included", ""),
            "pages": log.get("pages", ""),
            "page_size": log.get("pageSize", ""),
            "retrieval_cap": log.get("cap", ""),
            "retries": log.get("retries", ""),
            "errors": " | ".join(log.get("errors") or ([e["error"]] if e.get("error") else [])),
            "truncation": log.get("truncated") or e.get("warning", ""),
            "notes": " | ".join(log.get("notes") or []),
            "all_years_count_query": log.get("allYearsQuery", ""),
            "request_params": json.dumps(log.get("params") or {}, ensure_ascii=False, sort_keys=True),
        })
        rows.append(row)
    return rows


def _search_log_csv(db_counts):
    rows = _search_log_rows(db_counts)
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(_csv_safe_row(row))
    return "\ufeff" + buf.getvalue()


def _execute_search(keywords, databases, rows, filters, on_progress=None):
    """Run every selected connector, filter, and return the pieces a run stores.

    on_progress(db, entry) is called as each database finishes.
    """
    api_filters, count_filters = _search_filters_for_apis(filters)
    date_limited = count_filters is not None
    results_by_db, db_counts = {}, {}
    for db, result, count in _run_connectors(databases, keywords, rows, api_filters, count_filters):
        results_by_db[db] = result
        db_counts[db] = _make_db_entry(db, result, count, date_limited)
        if on_progress:
            on_progress(db, db_counts[db])
    all_docs, origin = _assemble_docs(results_by_db)
    total_found = sum(e["found"] for e in db_counts.values())
    _enrich_docs_language(all_docs)
    raw_docs = list(all_docs)
    prisma = {
        "identification": {"total_found": total_found, "retrieved": len(raw_docs), "databases": db_counts},
        "screening": {},
        "eligibility": {},
        "search_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
    }
    if date_limited:
        prisma["identification"]["date_limit_at_database"] = {
            "yearFrom": filters.get("yearFrom"), "yearTo": filters.get("yearTo")}
    filtered = _apply_post_filters(all_docs, filters, prisma, origin=origin)
    prisma["included"] = len(filtered)
    excluded_docs = prisma.pop("excluded_docs", {})
    _attach_filter_breakdown(db_counts, prisma, filtered, origin)
    _check_date_limit_applied(db_counts)
    _identification_breakdown(prisma, db_counts)
    return {
        "total_found": total_found,
        "raw_docs": raw_docs,
        "docs": filtered,
        "prisma": prisma,
        "excluded_docs": excluded_docs,
        "db_counts": db_counts,
    }


@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    keywords = normalize_boolean_operators(data.get("keywords", ""))
    rows = max(1, _to_int(data.get("rows"), 10000))  # fetch all; each search fn stops when API is exhausted
    databases = data.get("databases", ["hal", "arxiv"])
    filters = data.get("filters", {})

    if not keywords:
        return jsonify({"error": "Please provide at least one keyword."}), 400

    run = _execute_search(keywords, databases, rows, filters)
    run_id = save_ad_hoc_run({
        "numFound": run["total_found"],
        "docs": run["docs"],
        "query": keywords,
        "databases": databases,
        "filters": filters,
        "rawDocs": run["raw_docs"],
        "excluded_docs": run["excluded_docs"],
        "prisma": run["prisma"],
        "dbCounts": run["db_counts"],
    })

    return jsonify({"numFound": run["total_found"], "docs": run["docs"], "prisma": run["prisma"],
                    "excluded_docs": run["excluded_docs"], "runId": run_id})


@app.route("/api/results/folder", methods=["GET"])
def get_results_folder():
    return jsonify({"path": RESULTS_DIR, "adhocPath": ADHOC_RESULTS_DIR})


@app.route("/api/results/open-folder", methods=["POST"])
def open_results_folder():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(RESULTS_DIR)  # Windows Explorer
        elif sys.platform == "darwin":
            subprocess.run(["open", RESULTS_DIR], check=False)
        else:
            subprocess.run(["xdg-open", RESULTS_DIR], check=False)
        return jsonify({"ok": True, "path": RESULTS_DIR})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "path": RESULTS_DIR}), 500


@app.route("/api/results/runs", methods=["GET"])
def list_result_runs():
    limit = min(max(_to_int(request.args.get("limit"), 300), 1), 2000)
    runs = []

    # Ad-hoc runs: one file per run under data/results/adhoc
    if os.path.isdir(ADHOC_RESULTS_DIR):
        for name in os.listdir(ADHOC_RESULTS_DIR):
            if not name.endswith(".json"):
                continue
            run_id = name[:-5]
            if not _UUID_RE.fullmatch(run_id):
                continue
            file_path = os.path.join(ADHOC_RESULTS_DIR, name)
            payload = _read_json_file(file_path, {})
            if not isinstance(payload, dict):
                continue
            if not payload.get("timestamp"):
                payload["timestamp"] = _file_iso_mtime(file_path)
            runs.append(_run_summary(
                payload,
                "adhoc",
                f"/api/results/run/adhoc/{run_id}",
                f"/api/results/download/adhoc/{run_id}",
            ))

    # Saved search runs: history array per saved search file
    if os.path.isdir(RESULTS_DIR):
        for name in os.listdir(RESULTS_DIR):
            if name == "adhoc" or not name.endswith(".json"):
                continue
            search_id = name[:-5]
            if not _UUID_RE.fullmatch(search_id):
                continue
            file_path = os.path.join(RESULTS_DIR, name)
            history = _read_json_file(file_path, [])
            if not isinstance(history, list):
                continue
            for idx, entry in enumerate(history):
                if not isinstance(entry, dict):
                    continue
                if not entry.get("timestamp"):
                    entry["timestamp"] = _file_iso_mtime(file_path)
                runs.append(_run_summary(
                    entry,
                    "saved",
                    f"/api/results/run/saved/{search_id}/{idx}",
                    f"/api/results/download/saved/{search_id}/{idx}",
                    search_id=search_id,
                    history_index=idx,
                ))

    runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return jsonify(runs[:limit])


@app.route("/api/results/run/adhoc/<run_id>", methods=["GET"])
def get_adhoc_run(run_id):
    if not _UUID_RE.fullmatch(run_id):
        return jsonify({"error": "Invalid run ID"}), 400
    file_path = os.path.join(ADHOC_RESULTS_DIR, f"{run_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Run not found"}), 404
    payload = _read_json_file(file_path, None)
    if payload is None:
        return jsonify({"error": "Invalid run file"}), 500
    return jsonify(payload)


@app.route("/api/results/run/saved/<search_id>/<int:history_index>", methods=["GET"])
def get_saved_run(search_id, history_index):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    if history_index < 0:
        return jsonify({"error": "Invalid history index"}), 400
    file_path = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Search results not found"}), 404
    history = _read_json_file(file_path, None)
    if not isinstance(history, list):
        return jsonify({"error": "Invalid history file"}), 500
    if history_index >= len(history):
        return jsonify({"error": "Run index out of range"}), 404
    return jsonify(history[history_index])


@app.route("/api/results/run/saved/<search_id>/by-id/<run_id>", methods=["GET"])
def get_saved_run_by_id(search_id, run_id):
    if not _UUID_RE.fullmatch(search_id) or not _UUID_RE.fullmatch(run_id):
        return jsonify({"error": "Invalid run or search ID"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        return jsonify(target[1])


_CHECKLIST_IDS = (
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10a", "10b", "11", "12",
    "13a", "13b", "13c", "13d", "13e", "13f", "14", "15", "16a", "16b",
    "17", "18", "19", "20a", "20b", "20c", "20d", "21", "22", "23a",
    "23b", "23c", "24", "25", "26", "27",
)


def _checklist_context_valid(context, run_id):
    return (isinstance(context, dict) and isinstance(context.get("prisma"), dict)
            and context["prisma"].get("runId") == run_id
            and isinstance(context.get("formState"), dict)
            and isinstance(context.get("docs"), list)
            and isinstance(context.get("excludedDocs"), dict)
            and isinstance(context.get("manualSteps"), list)
            and (context.get("snowballing") is None or isinstance(context.get("snowballing"), dict)))


def _checklist_revision(run, context, protocol=None):
    protocol = {k: v for k, v in (protocol if protocol is not None else _load_protocol()).items()
                if k != "updatedAt"}
    run_evidence = {k: run.get(k) for k in (
        "runId", "query", "databases", "filters", "prisma", "docs",
        "rawDocs", "excluded_docs", "dbCounts", "screeningDecisions",
    )}
    if (run.get("fullTextScreening") or {}).get("decisions"):
        run_evidence["fullTextScreening"] = run["fullTextScreening"]
    if run.get("dualScreening"):
        run_evidence["dualScreening"] = run["dualScreening"]
    evidence = {
        "run": run_evidence,
        "protocol": protocol,
        "browserEvidence": context,
    }
    encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checklist_target(run_id, search_id=None):
    path = os.path.join(RESULTS_DIR if search_id else ADHOC_RESULTS_DIR,
                        f"{search_id if search_id else run_id}.json")
    if not os.path.exists(path):
        return None, "Run not found", 404
    data = _read_json_file(path, None)
    if search_id:
        if not isinstance(data, list):
            return None, "Invalid run history", 500
        for index, run in enumerate(data):
            if isinstance(run, dict) and run.get("runId") == run_id:
                return (path, run, data, index), None, 200
        return None, "Run not found", 404
    if not isinstance(data, dict):
        return None, "Invalid run file", 500
    if data.get("runId") != run_id:
        return None, "Run ID does not match stored record", 500
    return (path, data, None, None), None, 200


def _screening_paper_id(doc):
    doi = str(doc.get("doi") or "").strip().lower()
    return "doi:" + doi if doi else "title:" + str(doc.get("title") or "").strip().lower()


def _dual_stage(run, stage):
    return (run.get("dualScreening") or {}).get(stage)


def _dual_candidates(run, stage):
    docs = run.get("docs") or []
    counts = {}
    for doc in docs:
        pid = _screening_paper_id(doc)
        counts[pid] = counts.get(pid, 0) + 1
    ta = run.get("screeningDecisions") or {}
    return [{"paperId": _screening_paper_id(doc), "paper": doc,
             "ambiguous": _screening_paper_id(doc) == "title:" or counts[_screening_paper_id(doc)] != 1}
            for doc in docs if stage == "title-abstract" or
            (ta.get(_screening_paper_id(doc)) or {}).get("decision") == "include"]


def _dual_response(run, stage):
    current = _dual_stage(run, stage)
    candidates = _dual_candidates(run, stage)
    eligible = {row["paperId"] for row in candidates if not row["ambiguous"]}
    inactive = sum(1 for pid in (current or {}).get("records", {}) if pid not in eligible)
    return {"stage": current, "candidates": candidates, "inactive": inactive}


@app.route("/api/results/run/adhoc/<run_id>/dual-screening/<stage>", methods=["GET", "POST", "PUT"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/dual-screening/<stage>", methods=["GET", "POST", "PUT"])
def run_dual_screening(run_id, stage, search_id=None):
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    if stage not in ("title-abstract", "full-text"):
        return jsonify({"error": "Invalid screening stage"}), 400
    body = request.get_json(silent=True) if request.method != "GET" else None
    if request.method != "GET" and not isinstance(body, dict):
        return jsonify({"error": "Invalid screening request"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        path, run, history, index = target
        current = _dual_stage(run, stage)
        if request.method == "GET":
            return jsonify(_dual_response(run, stage))
        if request.method == "POST":
            reviewers = body.get("reviewers")
            if (not isinstance(reviewers, list) or len(reviewers) != 2 or
                    any(not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 for name in reviewers)
                    or reviewers[0].strip().casefold() == reviewers[1].strip().casefold()):
                return jsonify({"error": "Enter two distinct reviewer labels (up to 100 characters each)."}), 400
            existing = (run.get("screeningDecisions") or {}) if stage == "title-abstract" else (
                (run.get("fullTextScreening") or {}).get("decisions") or {})
            if current or existing:
                return jsonify({"error": "Two-reviewer mode can only be enabled before this stage has decisions."}), 409
            current = {"version": 0, "reviewers": [name.strip() for name in reviewers], "records": {}, "history": []}
            run.setdefault("dualScreening", {})[stage] = current
        else:
            if not current:
                return jsonify({"error": "Two-reviewer mode is not enabled for this stage."}), 409
            if type(body.get("version")) is not int or body["version"] != current["version"]:
                return jsonify({"error": "Screening changed elsewhere. Reload before saving."}), 409
            pid, action = body.get("paperId"), body.get("action")
            candidate = next((row for row in _dual_candidates(run, stage) if row["paperId"] == pid), None)
            if not candidate or candidate["ambiguous"]:
                return jsonify({"error": "Paper is not uniquely eligible for this stage."}), 409
            options = ("include", "exclude", "unsure") if stage == "title-abstract" else (
                "include", "exclude", "unsure", "not_retrieved")
            decision, reason = body.get("decision"), body.get("reason")
            if (action not in ("review", "adjudicate") or decision not in options or
                    not isinstance(reason, str) or len(reason) > 5000):
                return jsonify({"error": "Invalid decision or reason."}), 400
            reason = reason.strip()
            if decision in ("exclude", "not_retrieved") and not reason:
                return jsonify({"error": "A reason is required for exclusion or non-retrieval."}), 400
            record = current["records"].setdefault(pid, {"reviews": {}, "adjudication": None})
            timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
            if action == "review":
                reviewer = body.get("reviewer")
                if type(reviewer) is not int or reviewer not in (0, 1):
                    return jsonify({"error": "Select a reviewer."}), 400
                entry = {"decision": decision, "reason": reason, "timestamp": timestamp}
                record["reviews"][str(reviewer)] = entry
                record["adjudication"] = None
                event = {"paperId": pid, "action": action, "reviewer": current["reviewers"][reviewer], **entry}
            else:
                reviews = record["reviews"]
                adjudicator = body.get("adjudicator")
                if (len(reviews) != 2 or
                        (reviews["0"]["decision"] == reviews["1"]["decision"] != "unsure")):
                    return jsonify({"error": "Adjudication requires two conflicting or unsure reviews."}), 409
                if (not isinstance(adjudicator, str) or not adjudicator.strip() or len(adjudicator.strip()) > 100
                        or not reason or decision == "unsure"):
                    return jsonify({"error": "Enter an adjudicator, a final decision, and an explanation."}), 400
                entry = {"decision": decision, "reason": reason, "timestamp": timestamp,
                         "adjudicator": adjudicator.strip()}
                record["adjudication"] = entry
                event = {"paperId": pid, "action": action, **entry}
            current["history"].append(event)
            current["version"] += 1
            reviews = record["reviews"]
            final = record["adjudication"]
            if not final and len(reviews) == 2 and reviews["0"]["decision"] == reviews["1"]["decision"] != "unsure":
                reasons = [f'{current["reviewers"][i]}: {reviews[str(i)]["reason"]}' for i in range(2)
                           if reviews[str(i)]["reason"]]
                final = {"decision": reviews["0"]["decision"], "reason": "; ".join(reasons), "timestamp": timestamp}
            if stage == "title-abstract":
                decisions = run.setdefault("screeningDecisions", {})
            else:
                full_text = run.setdefault("fullTextScreening", {"version": 0, "decisions": {}})
                full_text["version"] += 1
                decisions = full_text["decisions"]
            if final:
                decisions[pid] = final.copy()
            else:
                decisions.pop(pid, None)
        if history is not None:
            history[index] = run
        _write_json_atomic(path, history if history is not None else run)
        return jsonify(_dual_response(run, stage))


def _full_text_records(run):
    docs = run.get("docs") or []
    ta = run.get("screeningDecisions") or {}
    stored = run.get("fullTextScreening") or {"version": 0, "decisions": {}}
    id_counts = {}
    for doc in docs:
        pid = _screening_paper_id(doc)
        id_counts[pid] = id_counts.get(pid, 0) + 1
    candidates = []
    eligible_ids = set()
    for doc in docs:
        pid = _screening_paper_id(doc)
        if (ta.get(pid) or {}).get("decision") != "include":
            continue
        ambiguous = pid == "title:" or id_counts[pid] != 1
        eligible_ids.add(pid)
        candidates.append({"paperId": pid, "paper": doc, "ambiguous": ambiguous,
                           "decision": None if ambiguous else stored["decisions"].get(pid)})
    inactive = [{"paperId": pid, "decision": decision} for pid, decision in stored["decisions"].items()
                if pid not in eligible_ids or id_counts.get(pid, 0) != 1 or pid == "title:"]
    return {"version": stored["version"], "candidates": candidates, "inactive": inactive,
            "dualReview": bool(_dual_stage(run, "full-text"))}


def _full_text_summary(records):
    candidates = records["candidates"]
    counts = {name: 0 for name in ("include", "exclude", "unsure", "not_retrieved")}
    excluded = []
    for row in candidates:
        decision = row["decision"]
        if not decision:
            continue
        counts[decision["decision"]] += 1
        if decision["decision"] == "exclude":
            excluded.append({"paper": row["paper"], "reason": decision["reason"]})
    screened = sum(counts.values())
    return {"total": len(candidates), "screened": screened, "included": counts["include"],
            "excluded": counts["exclude"], "unsure": counts["unsure"],
            "notRetrieved": counts["not_retrieved"],
            "pending": len(candidates) - screened, "excludedRecords": excluded,
            "inactive": len(records["inactive"]),
            "ambiguous": sum(1 for row in candidates if row["ambiguous"]),
            "dualReview": records["dualReview"]}


@app.route("/api/results/run/adhoc/<run_id>/full-text", methods=["GET", "PUT"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/full-text", methods=["GET", "PUT"])
def run_full_text_screening(run_id, search_id=None):
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True) if request.method == "PUT" else None
    if request.method == "PUT":
        if (not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] < 0
                or not isinstance(body.get("paperId"), str) or not body["paperId"]
                or body.get("decision") not in ("include", "exclude", "unsure", "not_retrieved", "clear")
                or not isinstance(body.get("reason"), str) or len(body["reason"]) > 5000):
            return jsonify({"error": "Invalid full-text decision"}), 400
        if body["decision"] in ("exclude", "not_retrieved") and not body["reason"].strip():
            return jsonify({"error": "A reason is required for exclusion or non-retrieval"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        path, run, history, index = target
        current = _full_text_records(run)
        if request.method == "GET":
            return jsonify(current)
        if _dual_stage(run, "full-text"):
            return jsonify({"error": "Use two-reviewer screening for this stage."}), 409
        if body["version"] != current["version"]:
            return jsonify({"error": "Full-text decisions changed elsewhere. Reload before saving."}), 409
        candidate = next((row for row in current["candidates"] if row["paperId"] == body["paperId"]), None)
        if not candidate or candidate["ambiguous"]:
            return jsonify({"error": "Paper is not uniquely eligible for full-text screening. Refresh title and abstract screening."}), 409
        stored = run.get("fullTextScreening") or {"version": 0, "decisions": {}}
        decisions = stored["decisions"]
        if body["decision"] == "clear":
            decisions.pop(body["paperId"], None)
        else:
            decisions[body["paperId"]] = {"decision": body["decision"], "reason": body["reason"].strip(),
                                         "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}
        run["fullTextScreening"] = {"version": stored["version"] + 1, "decisions": decisions}
        if history is not None:
            history[index] = run
        _write_json_atomic(path, history if history is not None else run)
        return jsonify(_full_text_records(run))


def _manual_evidence(run):
    return run.get("prismaManualEvidence") or {"version": 0, "manualSteps": [], "snowballing": None}


def _manual_evidence_matches(run, context):
    evidence = _manual_evidence(run)
    return (context.get("manualSteps") == evidence["manualSteps"]
            and context.get("snowballing") == evidence["snowballing"])


@app.route("/api/results/run/adhoc/<run_id>/manual-evidence", methods=["GET", "PUT"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/manual-evidence", methods=["GET", "PUT"])
def run_manual_evidence(run_id, search_id=None):
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True) if request.method == "PUT" else None
    if request.method == "PUT":
        if not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] < 0:
            return jsonify({"error": "Invalid manual evidence version"}), 400
        steps, snowballing = body.get("manualSteps"), body.get("snowballing")
        def nonnegative(value):
            return type(value) is int and 0 <= value <= 1000000000
        if (not isinstance(steps, list) or len(steps) > 1000 or
                any(not isinstance(step, dict) or not isinstance(step.get("name"), str)
                    or not step["name"].strip() or len(step["name"]) > 1000
                    or not isinstance(step.get("desc"), str) or len(step["desc"]) > 50000
                    or not nonnegative(step.get("added")) or not nonnegative(step.get("removed"))
                    for step in steps)):
            return jsonify({"error": "Invalid manual review steps"}), 400
        if snowballing is not None and (
                not isinstance(snowballing, dict)
                or any(not nonnegative(snowballing.get(key)) for key in ("backward", "forward", "other", "excluded"))
                or snowballing["excluded"] > snowballing["backward"] + snowballing["forward"] + snowballing["other"]
                or not isinstance(snowballing.get("desc"), str) or len(snowballing["desc"]) > 50000):
            return jsonify({"error": "Invalid snowballing evidence"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        path, run, history, index = target
        stored = _manual_evidence(run)
        if request.method == "GET":
            return jsonify(stored)
        if body["version"] != stored["version"]:
            return jsonify({"error": "Manual evidence changed elsewhere. Reload before saving."}), 409
        updated = {"version": stored["version"] + 1,
                   "manualSteps": [{"name": s["name"], "added": s["added"], "removed": s["removed"],
                                    "desc": s["desc"]} for s in steps],
                   "snowballing": ({key: snowballing[key] for key in
                                    ("backward", "forward", "other", "excluded", "desc")}
                                   if snowballing is not None else None)}
        run["prismaManualEvidence"] = updated
        if history is not None:
            history[index] = run
        _write_json_atomic(path, history if history is not None else run)
        return jsonify(updated)


@app.route("/api/results/run/adhoc/<run_id>/checklist/evaluate", methods=["POST"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/checklist/evaluate", methods=["POST"])
def evaluate_adhoc_checklist(run_id, search_id=None):
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not _checklist_context_valid(body.get("context"), run_id):
        return jsonify({"error": "Checklist evidence must belong to this run"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        _, run, _, _ = target
        if not _manual_evidence_matches(run, body["context"]):
            return jsonify({"error": "Manual evidence changed. Reload this run before confirming."}), 409
        stored = run.get("prismaChecklist") or {"version": 0, "items": {}}
        return jsonify({"version": stored["version"], "items": stored["items"],
                        "revision": _checklist_revision(run, body["context"]),
                        "fullText": _full_text_summary(_full_text_records(run)),
                        "screeningDecisions": run.get("screeningDecisions") or {},
                        "screeningDocs": run.get("docs") or []})


@app.route("/api/results/run/adhoc/<run_id>/checklist", methods=["PUT"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/checklist", methods=["PUT"])
def save_adhoc_checklist(run_id, search_id=None):
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not _checklist_context_valid(body.get("context"), run_id):
        return jsonify({"error": "Checklist evidence must belong to this run"}), 400
    items = body.get("items")
    version = body.get("version")
    if not isinstance(items, dict) or type(version) is not int or version < 0:
        return jsonify({"error": "Invalid checklist or version"}), 400
    if any(key not in _CHECKLIST_IDS for key in items):
        return jsonify({"error": "Unknown checklist item"}), 400
    for item in items.values():
        if (not isinstance(item, dict) or type(item.get("checked")) is not bool
                or not isinstance(item.get("text"), str) or len(item["text"]) > 50000):
            return jsonify({"error": "Invalid checklist item or note"}), 400
        revision = item.get("confirmedRevision")
        if item["checked"]:
            if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
                return jsonify({"error": "A confirmation requires an evidence revision"}), 400
        elif revision is not None:
            return jsonify({"error": "Unchecked items cannot have a confirmation revision"}), 400
    with _data_io_lock:
        target, error, status = _checklist_target(run_id, search_id)
        if error:
            return jsonify({"error": error}), status
        path, run, history, index = target
        if not _manual_evidence_matches(run, body["context"]):
            return jsonify({"error": "Manual evidence changed. Reload this run before confirming."}), 409
        stored = run.get("prismaChecklist") or {"version": 0, "items": {}}
        if version != stored["version"]:
            return jsonify({"error": "Checklist was changed elsewhere. Reload before saving."}), 409
        current_revision = _checklist_revision(run, body["context"])
        for key, item in items.items():
            if item["checked"] and item["confirmedRevision"] != current_revision:
                previous = stored["items"].get(key, {})
                if not previous.get("checked") or previous.get("confirmedRevision") != item["confirmedRevision"]:
                    return jsonify({"error": "Evidence changed before confirmation. Review and confirm again."}), 409
        cleaned = {key: {"checked": item["checked"], "text": item["text"],
                         "confirmedRevision": item.get("confirmedRevision")} for key, item in items.items()}
        new_version = version + 1
        run["prismaChecklist"] = {"version": new_version, "items": cleaned}
        if history is not None:
            history[index] = run
        _write_json_atomic(path, history if history is not None else run)
        return jsonify({"version": new_version, "revision": current_revision, "items": cleaned})


@app.route("/api/results/run/adhoc/<run_id>/screening/decision", methods=["POST"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/screening/decision", methods=["POST"])
def save_adhoc_screening_decision(run_id, search_id=None):
    """Upsert one screening decision in an ad-hoc run file."""
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True) or {}
    paper_id = str(body.get("paperId", "")).strip()
    decision = str(body.get("decision", "")).strip()
    if not paper_id:
        return jsonify({"error": "paperId required"}), 400
    if decision not in ("include", "exclude", "unsure"):
        return jsonify({"error": "decision must be include, exclude, or unsure"}), 400
    reason = str(body.get("reason", "")).strip()
    try:
        with _data_io_lock:
            target, error, status = _checklist_target(run_id, search_id)
            if error:
                return jsonify({"error": error}), status
            file_path, payload, history, index = target
            if _dual_stage(payload, "title-abstract"):
                return jsonify({"error": "Use two-reviewer screening for this stage."}), 409
            if "screeningDecisions" not in payload:
                payload["screeningDecisions"] = {}
            entry = {
                "decision": decision,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            }
            # Provenance for PRISMA item 8: was this decision accepted from
            # an AI suggestion, and by which model?
            if body.get("aiAssisted"):
                entry["aiAssisted"] = True
            if body.get("aiSuggested") in ("include", "exclude", "unsure"):
                entry["aiSuggested"] = body["aiSuggested"]
                entry["aiConfidence"] = max(0, min(100, _to_int(body.get("aiConfidence"), 0)))
                entry["aiVendor"] = str(body.get("aiVendor", ""))[:32]
            if body.get("aiAssisted") or entry.get("aiSuggested"):
                entry["aiModel"] = str(body.get("aiModel", ""))[:100]
            payload["screeningDecisions"][paper_id] = entry
            if history is not None:
                history[index] = payload
            _write_json_atomic(file_path, history if history is not None else payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/results/run/adhoc/<run_id>/screening/decision", methods=["DELETE"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/screening/decision", methods=["DELETE"])
def delete_adhoc_screening_decision(run_id, search_id=None):
    """Remove a single screening decision from an ad-hoc run file."""
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    body = request.get_json(silent=True) or {}
    paper_id = str(body.get("paperId", "")).strip()
    if not paper_id:
        return jsonify({"error": "paperId required"}), 400
    try:
        with _data_io_lock:
            target, error, status = _checklist_target(run_id, search_id)
            if error:
                return jsonify({"error": error}), status
            file_path, payload, history, index = target
            if _dual_stage(payload, "title-abstract"):
                return jsonify({"error": "Use two-reviewer screening for this stage."}), 409
            decisions = payload.get("screeningDecisions") or {}
            decisions.pop(paper_id, None)
            payload["screeningDecisions"] = decisions
            if history is not None:
                history[index] = payload
            _write_json_atomic(file_path, history if history is not None else payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/results/run/adhoc/<run_id>/screening", methods=["DELETE"])
@app.route("/api/results/run/saved/<search_id>/<run_id>/screening", methods=["DELETE"])
def reset_adhoc_screening(run_id, search_id=None):
    """Clear all screening decisions from an ad-hoc run file."""
    if not _UUID_RE.fullmatch(run_id) or (search_id and not _UUID_RE.fullmatch(search_id)):
        return jsonify({"error": "Invalid run or search ID"}), 400
    try:
        with _data_io_lock:
            target, error, status = _checklist_target(run_id, search_id)
            if error:
                return jsonify({"error": error}), status
            file_path, payload, history, index = target
            if _dual_stage(payload, "title-abstract"):
                return jsonify({"error": "Use two-reviewer screening for this stage."}), 409
            payload["screeningDecisions"] = {}
            if history is not None:
                history[index] = payload
            _write_json_atomic(file_path, history if history is not None else payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/results/run/adhoc/<run_id>", methods=["DELETE"])
def delete_adhoc_run(run_id):
    if not _UUID_RE.fullmatch(run_id):
        return jsonify({"error": "Invalid run ID"}), 400
    file_path = os.path.join(ADHOC_RESULTS_DIR, f"{run_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Run not found"}), 404
    try:
        os.remove(file_path)
        return jsonify({"ok": True, "deleted": {"storage": "adhoc", "runId": run_id}})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/results/run/saved/<search_id>/<int:history_index>", methods=["DELETE"])
def delete_saved_run(search_id, history_index):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    if history_index < 0:
        return jsonify({"error": "Invalid history index"}), 400

    file_path = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Search results not found"}), 404

    try:
        with _data_io_lock:
            history = _read_json_file(file_path, None)
            if not isinstance(history, list):
                return jsonify({"error": "Invalid history file"}), 500
            if history_index >= len(history):
                return jsonify({"error": "Run index out of range"}), 404
            deleted_entry = history.pop(history_index)
            _write_json_atomic(file_path, history)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "deleted": {
            "storage": "saved",
            "searchId": search_id,
            "historyIndex": history_index,
            "runId": deleted_entry.get("runId", ""),
        },
    })


@app.route("/api/results/download/adhoc/<run_id>", methods=["GET"])
def download_adhoc_run(run_id):
    if not _UUID_RE.fullmatch(run_id):
        return jsonify({"error": "Invalid run ID"}), 400
    file_path = os.path.join(ADHOC_RESULTS_DIR, f"{run_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Run not found"}), 404
    return send_file(
        file_path,
        as_attachment=True,
        download_name=f"adhoc_run_{run_id}.json",
        mimetype="application/json",
    )


@app.route("/api/results/download/saved/<search_id>/<int:history_index>", methods=["GET"])
def download_saved_run(search_id, history_index):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    if history_index < 0:
        return jsonify({"error": "Invalid history index"}), 400
    file_path = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Search results not found"}), 404
    history = _read_json_file(file_path, None)
    if not isinstance(history, list):
        return jsonify({"error": "Invalid history file"}), 500
    if history_index >= len(history):
        return jsonify({"error": "Run index out of range"}), 404

    payload = history[history_index]
    bytes_buf = io.BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
    run_id = payload.get("runId", f"idx{history_index}")
    return send_file(
        bytes_buf,
        as_attachment=True,
        download_name=f"saved_run_{search_id}_{run_id}.json",
        mimetype="application/json",
    )


@app.route("/api/results/download-zip/adhoc/<run_id>", methods=["GET"])
def download_adhoc_run_zip(run_id):
    if not _UUID_RE.fullmatch(run_id):
        return jsonify({"error": "Invalid run ID"}), 400
    file_path = os.path.join(ADHOC_RESULTS_DIR, f"{run_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Run not found"}), 404
    payload = _read_json_file(file_path, None)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid run file"}), 500

    zip_buf = _build_run_zip_buffer(payload)
    return send_file(
        zip_buf,
        as_attachment=True,
        download_name=f"adhoc_run_{run_id}.zip",
        mimetype="application/zip",
    )


@app.route("/api/results/download-zip/saved/<search_id>/<int:history_index>", methods=["GET"])
def download_saved_run_zip(search_id, history_index):
    if not _UUID_RE.fullmatch(search_id):
        return jsonify({"error": "Invalid search ID"}), 400
    if history_index < 0:
        return jsonify({"error": "Invalid history index"}), 400
    file_path = os.path.join(RESULTS_DIR, f"{search_id}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Search results not found"}), 404
    history = _read_json_file(file_path, None)
    if not isinstance(history, list):
        return jsonify({"error": "Invalid history file"}), 500
    if history_index >= len(history):
        return jsonify({"error": "Run index out of range"}), 404

    payload = history[history_index]
    zip_buf = _build_run_zip_buffer(payload)
    run_id = payload.get("runId", f"idx{history_index}")
    return send_file(
        zip_buf,
        as_attachment=True,
        download_name=f"saved_run_{search_id}_{run_id}.zip",
        mimetype="application/zip",
    )


def _slim_doc(doc):
    """Return a minimal doc dict for excluded-papers storage (no abstract to save space)."""
    return {
        "title": doc.get("title") or "",
        "authors": doc.get("authors") or "",
        "year": doc.get("year"),
        "doi": doc.get("doi") or "",
        "url": doc.get("url") or "",
        "source": doc.get("source") or "",
        "docType": doc.get("docType") or "",
        "journal": doc.get("journal") or "",
        "language": doc.get("language") or "",
        "languageSource": doc.get("languageSource") or "",
    }

_MAX_EXCL = 200  # max excluded docs stored per reason


def _term_matches(term, text_lower):
    """Word-boundary match for one filter term inside lowercased text.

    - Surrounding quotes are stripped, so a user-typed "machine learning"
      matches the phrase instead of silently never matching.
    - Word boundaries prevent substring hits: exclude term `art` must not
      remove papers containing "artificial".
    """
    term = (term or "").strip().strip('"').strip("'").strip()
    if not term:
        return False
    # Any run of whitespace in a phrase matches any run in the text, so a
    # phrase still matches across the line breaks arXiv puts in abstracts.
    words = [re.escape(w) for w in term.lower().split()]
    pattern = r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"
    return re.search(pattern, text_lower) is not None


def _split_filter_terms(terms):
    """Normalise a term list: one term per comma- or newline-separated entry.

    A list pasted one-phrase-per-line arrives as a single entry with embedded
    newlines, which would otherwise be matched as one impossible phrase.
    Commas inside double quotes are kept, so "safety, critical" stays whole.
    """
    if isinstance(terms, str):
        terms = [terms]
    out = []
    for entry in terms or []:
        for part in re.findall(r'"[^"]*"|[^,\n\r]+', str(entry)):
            part = part.strip()
            if part.strip('"').strip("'").strip():
                out.append(part)
    return out


def _term_haystack(doc):
    """Lowercased text that include/exclude terms are matched against.

    Keywords are included because some connectors (e.g. Web of Science Starter)
    return no abstract at all, leaving title-only records unmatchable.
    """
    keywords = doc.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    parts = [
        doc.get("title") or "",
        doc.get("abstract") or "",
        " ".join(str(k) for k in keywords),
    ]
    return " ".join(parts).lower()


def _apply_post_filters(all_docs, filters, prisma, origin=None):
    """Apply post-search filters and deduplication. Mutates prisma dict. Returns filtered docs.

    origin maps id(doc) -> database key; when given, prisma["removed_by_db"]
    records how many records of each database every filter removed.
    """
    excl = {}  # excluded docs keyed by reason, slimmed
    removed_by_db = {}

    def _tally(reason, removed):
        if origin is None:
            return
        counts = removed_by_db.setdefault(reason, {})
        for d in removed:
            key = origin.get(id(d), "unknown")
            counts[key] = counts.get(key, 0) + 1

    # Remove duplicates
    # Uses DOI as primary key and normalised title as fallback.
    # Both maps are updated for every unique paper so cross-database pairs
    # (e.g. arXiv with no DOI + Scopus with a DOI for the same paper) are
    # correctly detected regardless of which source arrives first.
    before_dedup = len(all_docs)
    seen_dois = {}     # doi -> index into unique_docs
    seen_titles = {}   # normalised title -> index into unique_docs
    unique_docs = []
    dup_docs = []
    for doc in all_docs:
        doi = (doc.get("doi") or "").strip().lower()
        title_norm = "".join(c for c in (doc.get("title") or "").lower() if c.isalnum())
        if title_norm == "untitled":
            # Connector placeholder for a missing title — two unrelated
            # DOI-less records must not merge on it.
            title_norm = ""
        if doi and doi in seen_dois:
            dup_docs.append(doc)
            continue
        if title_norm and title_norm in seen_titles:
            idx = seen_titles[title_norm]
            kept = unique_docs[idx]
            # Corroborate a title-only match with the year: identical titles
            # published years apart are distinct papers, not duplicates.
            distinct = False
            try:
                y1, y2 = kept.get("year"), doc.get("year")
                if y1 is not None and y2 is not None and abs(int(y1) - int(y2)) > 1:
                    distinct = True
            except (TypeError, ValueError):
                pass
            if not distinct:
                # Duplicate — keep the record that carries a DOI so the
                # canonical identifier survives regardless of arrival order.
                if doi and not (kept.get("doi") or "").strip():
                    unique_docs[idx] = doc
                    seen_dois[doi] = idx
                    dup_docs.append(kept)
                else:
                    dup_docs.append(doc)
                continue
        # Register both so either lookup catches a later duplicate
        idx = len(unique_docs)
        if doi:
            seen_dois[doi] = idx
        if title_norm:
            seen_titles[title_norm] = idx
        unique_docs.append(doc)
    all_docs = unique_docs
    duplicates_removed = before_dedup - len(all_docs)
    if duplicates_removed > 0:
        prisma["screening"]["duplicates"] = duplicates_removed
        excl["duplicates"] = [_slim_doc(d) for d in dup_docs[:_MAX_EXCL]]
        _tally("duplicates", dup_docs)

    year_from = filters.get("yearFrom")
    year_to = filters.get("yearTo")
    if year_from or year_to:
        before = len(all_docs)
        filtered = []
        removed_yr = []
        for doc in all_docs:
            y = doc.get("year")
            if y is None:
                # Unknown year cannot be verified against the range — keep it.
                filtered.append(doc)
                continue
            if year_from and y < year_from:
                removed_yr.append(doc)
                continue
            if year_to and y > year_to:
                removed_yr.append(doc)
                continue
            filtered.append(doc)
        all_docs = filtered
        prisma["screening"]["year_range"] = before - len(all_docs)
        if removed_yr:
            excl["year_range"] = [_slim_doc(d) for d in removed_yr[:_MAX_EXCL]]
            _tally("year_range", removed_yr)

    doc_types = filters.get("docTypes", [])
    if doc_types:
        before = len(all_docs)
        canonical_map = {"ART": "article", "COMM": "conference", "THESE": "thesis",
                         "COUV": "book chapter", "REPORT": "report", "PREPRINT": "preprint"}
        allowed = {canonical_map.get(t, t).lower() for t in doc_types}
        kept, removed_dt = [], []
        for d in all_docs:
            dt = (d.get("docType") or "").lower()
            if dt in allowed or not dt.strip():
                kept.append(d)
            else:
                removed_dt.append(d)
        all_docs = kept
        prisma["screening"]["doc_type"] = before - len(all_docs)
        if removed_dt:
            excl["doc_type"] = [_slim_doc(d) for d in removed_dt[:_MAX_EXCL]]
            _tally("doc_type", removed_dt)

    languages = filters.get("languages", [])
    if languages:
        before = len(all_docs)
        allowed = {_normalize_language(l) for l in languages}
        allowed.discard("")
        include_unknown = bool(filters.get("includeUnknownLanguage", True))
        kept, removed_lang = [], []
        for d in all_docs:
            lang = _normalize_language(d.get("language"))
            if lang:
                d["language"] = lang
            if (lang and lang in allowed) or (not lang and include_unknown):
                kept.append(d)
            else:
                removed_lang.append(d)
        all_docs = kept
        prisma["screening"]["language"] = before - len(all_docs)
        if removed_lang:
            excl["language"] = [_slim_doc(d) for d in removed_lang[:_MAX_EXCL]]
            _tally("language", removed_lang)

    exclude_terms = _split_filter_terms(filters.get("excludeTerms", []))
    if exclude_terms:
        before = len(all_docs)
        exclude_mode = filters.get("excludeTermsMode", "any")
        kept, removed_et = [], []
        for d in all_docs:
            text = _term_haystack(d)
            if exclude_mode == "all":
                hit = all(_term_matches(t, text) for t in exclude_terms)
            else:
                hit = any(_term_matches(t, text) for t in exclude_terms)
            if hit:
                removed_et.append(d)
            else:
                kept.append(d)
        all_docs = kept
        prisma["eligibility"]["exclude_terms"] = before - len(all_docs)
        if removed_et:
            excl["exclude_terms"] = [_slim_doc(d) for d in removed_et[:_MAX_EXCL]]
            _tally("exclude_terms", removed_et)

    include_terms = _split_filter_terms(filters.get("includeTerms", []))
    if include_terms:
        before = len(all_docs)
        include_mode = filters.get("includeTermsMode", "all")
        filtered = []
        removed_inc = []
        for doc in all_docs:
            text = _term_haystack(doc)
            if include_mode == "any":
                match = any(_term_matches(term, text) for term in include_terms)
            else:
                match = all(_term_matches(term, text) for term in include_terms)
            if match:
                filtered.append(doc)
            else:
                removed_inc.append(doc)
        all_docs = filtered
        prisma["eligibility"]["must_include_terms"] = before - len(all_docs)
        if removed_inc:
            excl["must_include_terms"] = [_slim_doc(d) for d in removed_inc[:_MAX_EXCL]]
            _tally("must_include_terms", removed_inc)

    include_venues = filters.get("includeVenues", [])
    if include_venues:
        before = len(all_docs)
        include_venues_mode = filters.get("includeVenuesMode", "any")
        include_venues_lower = [v.strip().strip('"').strip("'").lower() for v in include_venues]
        include_venues_lower = [v for v in include_venues_lower if v]
        kept, removed_iv = [], []
        for doc in all_docs:
            # Match against the actual publication venue only — never doc["source"]
            # (the database label), or a venue filter like "ACM" / "IEEE" would
            # match every record coming from that database.
            venue_text = (doc.get("journal") or "").lower()
            if include_venues_mode == "all":
                match = all(v in venue_text for v in include_venues_lower)
            else:
                match = any(v in venue_text for v in include_venues_lower)
            if match:
                kept.append(doc)
            else:
                removed_iv.append(doc)
        all_docs = kept
        prisma["eligibility"]["include_venues"] = before - len(all_docs)
        if removed_iv:
            excl["include_venues"] = [_slim_doc(d) for d in removed_iv[:_MAX_EXCL]]
            _tally("include_venues", removed_iv)

    exclude_venues = filters.get("excludeVenues", [])
    if exclude_venues:
        before = len(all_docs)
        exclude_venues_mode = filters.get("excludeVenuesMode", "any")
        exclude_venues_lower = [v.strip().strip('"').strip("'").lower() for v in exclude_venues]
        exclude_venues_lower = [v for v in exclude_venues_lower if v]
        kept, removed_ev = [], []
        for doc in all_docs:
            # Match against the actual publication venue only — never doc["source"]
            # (the database label), or a venue filter like "ACM" / "IEEE" would
            # match every record coming from that database.
            venue_text = (doc.get("journal") or "").lower()
            if exclude_venues_mode == "all":
                hit = all(v in venue_text for v in exclude_venues_lower)
            else:
                hit = any(v in venue_text for v in exclude_venues_lower)
            if not hit:
                kept.append(doc)
            else:
                removed_ev.append(doc)
        all_docs = kept
        prisma["eligibility"]["exclude_venues"] = before - len(all_docs)
        if removed_ev:
            excl["exclude_venues"] = [_slim_doc(d) for d in removed_ev[:_MAX_EXCL]]
            _tally("exclude_venues", removed_ev)

    prisma["excluded_docs"] = excl
    if origin is not None:
        prisma["removed_by_db"] = removed_by_db
    return all_docs


def _reconstruct_abstract(inverted_index):
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    positions = []
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions.append((pos, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions)


def _normalize_openalex_doc(work, source_label):
    """Normalize an OpenAlex work object to RSP doc format."""
    doi_raw = (work.get("doi") or "").strip()
    doi = doi_raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    authors = [
        a["author"]["display_name"]
        for a in work.get("authorships", [])
        if (a.get("author") or {}).get("display_name")
    ]
    primary_loc = work.get("primary_location") or {}
    source_info = primary_loc.get("source") or {}
    journal = source_info.get("display_name", "")
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index") or {})
    keywords = [kw.get("display_name", "") for kw in (work.get("keywords") or []) if kw.get("display_name")]
    _openalex_type_map = {
        "article": "article", "review": "article", "review-article": "article",
        "editorial": "article", "letter": "article", "erratum": "article",
        "peer-review": "article", "paratext": "article",
        "book-chapter": "book chapter", "book": "book chapter",
        "conference-paper": "conference", "proceedings-article": "conference",
        "preprint": "preprint",
        "dissertation": "thesis",
        "report": "report", "report-component": "report",
    }
    raw_type = (work.get("type") or "").lower()
    doc_type = _openalex_type_map.get(raw_type, raw_type)
    return {
        "title":   work.get("title", "") or "",
        "authors": authors,
        "year":    work.get("publication_year"),
        "journal": journal,
        "doi":     doi,
        "url":     doi_raw if doi_raw else (work.get("id") or ""),
        "abstract": abstract,
        "keywords": keywords,
        "docType": doc_type,
        "source":  source_label,
        "sources": [source_label],
        "language": work.get("language", "") or "",
    }


@app.route("/api/snowball", methods=["POST"])
def snowball():
    """Citation snowballing via OpenAlex (free, no key required).
    Accepts DOIs or paper titles as seeds. Supports forward and backward modes.
    """
    data = request.get_json()
    raw_seeds = data.get("seeds") or data.get("dois") or []
    mode = data.get("mode", "forward")  # "forward", "backward", "both"

    seeds = [(s or "").strip() for s in raw_seeds if (s or "").strip()]
    seeds = seeds[:20]  # cap at 20

    if not seeds:
        return jsonify({"error": "No seeds provided"}), 400

    import re
    _doi_pattern = re.compile(r'^10\.\d{4,}/')

    all_docs = []
    errors = []
    seed_info = []  # [{"input": ..., "resolved_title": ..., "work_id": ...}]
    ua_params = {"mailto": "rsp@local"}
    _oa_key = get_openalex_api_key()
    if _oa_key:
        ua_params["api_key"] = _oa_key

    for seed in seeds:
        doi_clean = seed.replace("https://doi.org/", "").replace("http://doi.org/", "")
        is_doi = bool(_doi_pattern.match(doi_clean))
        try:
            if is_doi:
                r = requests.get(
                    f"https://api.openalex.org/works/https://doi.org/{doi_clean}",
                    params=ua_params, timeout=15
                )
            else:
                # Title search — pick the top result
                r = requests.get(
                    "https://api.openalex.org/works",
                    params={**ua_params, "filter": f"title.search:{seed}", "per-page": 1,
                            "select": "id,doi,title,authorships,publication_year,primary_location,abstract_inverted_index,keywords,type,referenced_works"},
                    timeout=15
                )
                if r.status_code == 200:
                    results = r.json().get("results", [])
                    if not results:
                        errors.append(f'"{seed}": no match found in OpenAlex by title')
                        continue
                    # Re-fetch full work by ID to get referenced_works
                    work_id = results[0].get("id", "")
                    r = requests.get(work_id, params=ua_params, timeout=15)

            if r.status_code != 200:
                errors.append(f'"{seed}": HTTP {r.status_code} from OpenAlex')
                continue

            seed_work = r.json()
            work_id = seed_work.get("id", "")
            resolved_title = seed_work.get("title", seed)
            seed_info.append({"input": seed, "resolved": resolved_title, "id": work_id})

            # Forward — papers that CITE the seed
            if mode in ("forward", "both"):
                fwd_r = requests.get(
                    "https://api.openalex.org/works",
                    params={**ua_params, "filter": f"cites:{work_id}", "per-page": 200,
                            "select": "id,doi,title,authorships,publication_year,primary_location,abstract_inverted_index,keywords,type"},
                    timeout=30
                )
                if fwd_r.status_code == 200:
                    for w in fwd_r.json().get("results", []):
                        all_docs.append(_normalize_openalex_doc(w, "Snowball\u2191Forward"))

            # Backward — papers the seed CITES
            if mode in ("backward", "both"):
                ref_ids = seed_work.get("referenced_works", [])[:50]
                if ref_ids:
                    ids_filter = "|".join(ref_ids)
                    bwd_r = requests.get(
                        "https://api.openalex.org/works",
                        params={**ua_params, "filter": f"openalex_id:{ids_filter}", "per-page": 200,
                                "select": "id,doi,title,authorships,publication_year,primary_location,abstract_inverted_index,keywords,type"},
                        timeout=30
                    )
                    if bwd_r.status_code == 200:
                        for w in bwd_r.json().get("results", []):
                            all_docs.append(_normalize_openalex_doc(w, "Snowball\u2193Backward"))

        except Exception as exc:
            errors.append(f'"{seed}": {exc}')

    # Deduplicate by DOI within snowball results
    seen = set()
    unique_docs = []
    for doc in all_docs:
        key = doc["doi"].lower() if doc["doi"] else doc["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique_docs.append(doc)

    return jsonify({"docs": unique_docs, "count": len(unique_docs), "errors": errors, "resolved": seed_info})


@app.route("/api/raw-cache-info", methods=["GET"])
def raw_cache_info():
    """Return metadata about the locally cached raw search results."""
    if not os.path.exists(RAW_CACHE_FILE):
        return jsonify({"exists": False})
    try:
        with open(RAW_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return jsonify({
            "exists": True,
            "query": cache.get("query", ""),
            "databases": cache.get("databases", []),
            "timestamp": cache.get("timestamp", ""),
            "count": len(cache.get("rawDocs", [])),
            "total_found": cache.get("total_found", 0),
        })
    except Exception as e:
        return jsonify({"exists": False, "error": str(e)})


@app.route("/search/stream", methods=["POST"])
def search_stream():
    """SSE endpoint: streams per-database progress events, then a final 'done' event."""
    data = request.get_json()
    keywords = normalize_boolean_operators(data.get("keywords") or "")
    rows = max(1, _to_int(data.get("rows"), 10000))
    databases = data.get("databases", ["hal", "arxiv"])
    filters = data.get("filters", {})
    use_cache = bool(data.get("use_cache", False))
    api_filters, count_filters = _search_filters_for_apis(filters)
    date_limited = count_filters is not None

    if not keywords:
        def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Please provide at least one keyword.'})}\n\n"
        return Response(stream_with_context(_err()), mimetype="text/event-stream")

    # Catch malformed queries before any database is called, instead of
    # letting each API fail with its own cryptic error.
    if not use_cache:
        query_problems = validate_search_query(keywords, (filters or {}).get("excludeTerms"), databases)
        if query_problems:
            message = ("Please fix your search query before searching: "
                       + " ".join(query_problems))
            def _query_err():
                yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"
            return Response(stream_with_context(_query_err()), mimetype="text/event-stream")

    def generate():
        all_docs = []
        total_found = 0
        db_counts = {}

        # ── Re-filter mode: load raw docs from local cache ──────
        if use_cache:
            if not os.path.exists(RAW_CACHE_FILE):
                yield f"data: {json.dumps({'type': 'error', 'message': 'No cached results found. Run a live search first to populate the cache.'})}\n\n"
                return
            try:
                with open(RAW_CACHE_FILE, "r", encoding="utf-8") as _f:
                    _cache = json.load(_f)
            except Exception as _e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to load cache: {_e}'})}\n\n"
                return
            raw_docs = _cache.get("rawDocs", [])
            total_found = _cache.get("total_found", 0)
            db_counts = _cache.get("db_counts", {})
            all_docs = list(raw_docs)
            yield f"data: {json.dumps({'type': 'cache_loaded', 'count': len(raw_docs), 'query': _cache.get('query', ''), 'timestamp': _cache.get('timestamp', '')})}\n\n"

        # ── Live mode: call APIs ─────────────────────────────────
        else:
            results_by_db = {}
            for key, result, count in _run_connectors(databases, keywords, rows, api_filters, count_filters):
                results_by_db[key] = result
                db_counts[key] = _make_db_entry(key, result, count, date_limited)
                yield f"data: {json.dumps(_progress_event(key, db_counts[key]))}\n\n"

            all_docs, origin = _assemble_docs(results_by_db)
            total_found = sum(e["found"] for e in db_counts.values())
            _enrich_docs_language(all_docs)
            raw_docs = list(all_docs)

            # Save raw cache for future re-filter runs
            try:
                _cache_data = {
                    "query": keywords,
                    "databases": databases,
                    "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "total_found": total_found,
                    "db_counts": db_counts,
                    "rawDocs": raw_docs,
                }
                _write_json_atomic(RAW_CACHE_FILE, _cache_data, indent=None)
            except Exception:
                pass  # cache save failure is non-fatal

        prisma = {
            "identification": {"total_found": total_found, "retrieved": len(raw_docs) if not use_cache else len(all_docs), "databases": db_counts},
            "screening": {},
            "eligibility": {},
            "search_date": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d") if not use_cache else _cache.get("timestamp", "")[:10],
        }
        if use_cache:
            # Cached raw docs carry their source label, not the database key.
            origin = {id(d): _SOURCE_TO_DB.get(d.get("source"), d.get("source") or "unknown") for d in all_docs}
        if not use_cache and date_limited:
            prisma["identification"]["date_limit_at_database"] = {
                "yearFrom": filters.get("yearFrom"), "yearTo": filters.get("yearTo")}
        all_docs_filtered = _apply_post_filters(all_docs, filters, prisma, origin=origin)
        prisma["included"] = len(all_docs_filtered)
        excluded_docs = prisma.pop("excluded_docs", {})
        _attach_filter_breakdown(db_counts, prisma, all_docs_filtered, origin)
        _check_date_limit_applied(db_counts)
        _identification_breakdown(prisma, db_counts)

        # Diff against previous run with same query+databases before saving
        prev_run      = _find_previous_adhoc_run(keywords, databases)
        prev_keys     = {_doc_key(d) for d in prev_run.get("docs", []) if _doc_key(d)} if prev_run else set()
        curr_keys     = {_doc_key(d) for d in all_docs_filtered if _doc_key(d)}
        new_doc_keys  = curr_keys - prev_keys if prev_run else set()
        removed_docs_diff = [d for d in prev_run.get("docs", [])
                             if _doc_key(d) and _doc_key(d) not in curr_keys] if prev_run else []

        run_id = save_ad_hoc_run({
            "numFound": total_found,
            "docs": all_docs_filtered,
            "query": keywords,
            "databases": databases,
            "filters": filters,
            "rawDocs": all_docs,
            "excluded_docs": excluded_docs,
            "prisma": prisma,
            "dbCounts": db_counts,
            "fromCache": use_cache,
        })

        diff_payload = None
        if prev_run:
            diff_payload = {
                "hasPreviousRun": True,
                "previousRunDate": prev_run.get("timestamp", "")[:10],
                "newCount": len(new_doc_keys),
                "removedCount": len(removed_docs_diff),
                "newDocKeys": list(new_doc_keys),
                "removedDocs": [
                    {"title": d.get("title", ""), "doi": d.get("doi", ""),
                     "authors": d.get("authors", []), "year": d.get("year"),
                     "source": d.get("source", "")}
                    for d in removed_docs_diff[:50]  # cap to keep payload manageable
                ],
            }

        done_payload = {
            "type": "done",
            "numFound": total_found,
            "docs": all_docs_filtered,
            "prisma": prisma,
            "excluded_docs": excluded_docs,
            "runId": run_id,
            "diff": diff_payload,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- API: Export Replication Package ---

@app.route("/api/export-replication-package", methods=["POST"])
def export_replication_package():
    """Generate a PRISMA-aligned SLR Replication Package as a ZIP file."""
    data = request.get_json()
    search_name = data.get("searchName", "Untitled_Search")
    keywords = data.get("keywords", "")
    rows_per_page = data.get("rowsPerPage", 25)
    databases = data.get("databases", [])
    filters = data.get("filters", {})
    validation_papers = data.get("validationPapers", [])
    docs = data.get("docs", [])
    prisma = data.get("prisma", {})
    submitted_prisma = copy.deepcopy({k: v for k, v in prisma.items()
                                      if k not in ("taScreening", "fullTextScreening")}) if isinstance(prisma, dict) else None
    submitted_docs = docs
    execution_date = data.get("executionDate", datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    dedup_docs = data.get("dedupDocs", [])
    excluded_docs = data.get("excludedDocs", [])
    screening_decisions = data.get("screeningDecisions", [])
    raw_docs = []

    # ── Enrich from the canonical server run record when available ─────────
    # The browser copies can be truncated (localStorage quota) or absent
    # (saved-search export path); the run file holds the authoritative
    # rawDocs, per-reason excluded lists, prisma stats, and T&A decisions.
    # Client-supplied values still win when present, so in-browser
    # refinements (e.g. snowballing merges) are not lost.
    run_id = str(data.get("runId") or "").strip()
    search_id = str(data.get("searchId") or "").strip()
    server_run = None
    full_text_records = None
    full_text_summary = None
    with _data_io_lock:
        review_protocol = _load_protocol()
        if run_id and _UUID_RE.fullmatch(run_id) and (not search_id or _UUID_RE.fullmatch(search_id)):
            target, error, _ = _checklist_target(run_id, search_id or None)
            if not error:
                server_run = target[1]
    if server_run:
        full_text_records = _full_text_records(server_run)
        full_text_summary = _full_text_summary(full_text_records)
        raw_docs = server_run.get("rawDocs") or []
        if not docs:
            docs = server_run.get("docs") or []
        if not prisma:
            prisma = copy.deepcopy(server_run.get("prisma") or {})
        prisma["fullTextScreening"] = full_text_summary
        if not excluded_docs:
            _reason_labels = {
                "duplicates": "Duplicate record",
                "year_range": "Outside year range",
                "doc_type": "Document type excluded",
                "language": "Language excluded",
                "exclude_terms": "Matched exclude term",
                "must_include_terms": "Missing required include term",
                "include_venues": "Venue not in include list",
                "exclude_venues": "Venue in exclude list",
            }
            for reason_key, reason_docs in (server_run.get("excluded_docs") or {}).items():
                label = _reason_labels.get(reason_key, reason_key)
                for d in reason_docs or []:
                    if isinstance(d, dict):
                        excluded_docs.append({**d, "exclusion_reason": label})
        if isinstance(server_run.get("screeningDecisions"), dict):
            screening_decisions = []
            _by_id = {}
            for d in server_run.get("docs") or []:
                doi = (d.get("doi") or "").strip().lower()
                if doi:
                    _by_id["doi:" + doi] = d
                title_key = "title:" + (d.get("title") or "").lower().strip()
                _by_id.setdefault(title_key, d)
            for pid, dec in server_run["screeningDecisions"].items():
                if not isinstance(dec, dict):
                    continue
                doc = _by_id.get(pid, {})
                authors = doc.get("authors")
                screening_decisions.append({
                    "title": doc.get("title", pid),
                    "authors": "; ".join(authors) if isinstance(authors, list) else (authors or ""),
                    "year": doc.get("year", ""),
                    "source": doc.get("source", ""),
                    "doi": doc.get("doi", ""),
                    "decision": dec.get("decision", ""),
                    "reason": dec.get("reason", ""),
                    "screened_at": dec.get("timestamp", ""),
                    "ai_assisted": "yes" if dec.get("aiAssisted") else "no",
                    "ai_model": dec.get("aiModel", ""),
                    "ai_vendor": dec.get("aiVendor", ""),
                    "ai_suggested": dec.get("aiSuggested", ""),
                    "ai_confidence": dec.get("aiConfidence", ""),
                })

    # T&A screening summary for the exported flow diagram
    ta_screening = None
    if screening_decisions or (server_run and _dual_stage(server_run, "title-abstract")):
        _inc = sum(1 for r in screening_decisions if r.get("decision") == "include")
        _exc = sum(1 for r in screening_decisions if r.get("decision") == "exclude")
        _uns = sum(1 for r in screening_decisions if r.get("decision") == "unsure")
        ta_screening = {
            "total": prisma.get("included") or len(docs),
            "screened": _inc + _exc + _uns,
            "included": _inc,
            "excluded": _exc,
            "unsure": _uns,
        }

    # PRISMA 2020 reports citation searching as a separate identification arm.
    _identified = raw_docs or dedup_docs or docs
    if _identified:
        prisma.setdefault("identification", {})["other_methods"] = _count_other_methods(_identified)

    # Sanitize folder name
    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in search_name).strip()
    folder_name = f"{safe_name}_{execution_date}"
    db_counts = (prisma.get("identification") or {}).get("databases", {})

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = folder_name + "/"

        # --- 1. Search Strategy Documentation ---
        strategy = _build_search_strategy(search_name, keywords, databases, filters, execution_date, db_counts)
        zf.writestr(prefix + "01_search_strategy/search_strategy.txt", strategy)

        # Per-database search strings — exact queries as sent to each API
        db_labels = {
            "hal": "HAL Open Science", "arxiv": "arXiv", "ieee": "IEEE Xplore",
            "scopus": "Scopus", "inspec": "Inspec (Engineering Village)",
            "sciencedirect": "ScienceDirect",
            "springer": "Springer Nature", "pubmed": "PubMed",
            "acm": "ACM Digital Library", "openalex": "OpenAlex",
            "ebsco": "EBSCO Discovery Service", "wos": "Web of Science"
        }
        # Use _api_filters so the documented queries match what was actually
        # dispatched: by default all inclusion/exclusion filtering happens
        # locally after retrieval, so no filter clauses were sent to the APIs.
        query_filters = dict(_api_filters(filters))
        # Settings → Retrieval: the run may have sent only the year range.
        date_limit = (prisma.get("identification") or {}).get("date_limit_at_database") or {}
        query_filters.update({k: v for k, v in date_limit.items() if v})
        db_query_map = build_db_queries(keywords, query_filters)
        api_filters_active = bool(_api_filters(filters))
        if api_filters_active:
            filter_note = ""
        elif date_limit:
            filter_note = ("NOTE: the year range was applied AT THE DATABASE (included in the\n"
                           "queries below); records outside it were counted with a separate\n"
                           "all-years count request but not downloaded (see search_log.csv).\n"
                           "All other inclusion/exclusion criteria were applied LOCALLY by\n"
                           "Review Sentinel Prime after retrieval — see filters_and_constraints.txt.")
        else:
            filter_note = ("NOTE: inclusion/exclusion criteria (terms, venues, year range, document\n"
                           "type, language) were applied LOCALLY by Review Sentinel Prime after\n"
                           "retrieval — see filters_and_constraints.txt. Replaying these queries\n"
                           "against the APIs returns the pre-filter record set.")
        db_strings_lines = [
            "SEARCH STRINGS PER DATABASE",
            "=" * 70,
            "These are the exact query strings submitted to each database API.",
            "API keys are not shown.",
            filter_note,
            "",
        ]
        for db in databases:
            info = db_query_map.get(db, {})
            db_strings_lines.append(f"{'─' * 70}")
            db_strings_lines.append(f"Database   : {db_labels.get(db, db)}")
            db_strings_lines.append(f"API param  : {info.get('query_param', 'query')}")
            db_strings_lines.append(f"Query      : {info.get('query', keywords)}")
            extra = info.get("extra_params", {})
            if extra:
                for k, v in extra.items():
                    db_strings_lines.append(f"  + {k}: {v}")
            db_strings_lines.append("")
        db_strings = "\n".join(db_strings_lines)
        zf.writestr(prefix + "01_search_strategy/search_strings_per_database.txt", db_strings)

        # Filters & constraints
        filter_doc = _build_filter_documentation(filters)
        zf.writestr(prefix + "01_search_strategy/filters_and_constraints.txt", filter_doc)

        # Per-database retrieval log: what each API was sent, what it reported,
        # what was retrieved and removed, and whether coverage was complete.
        if db_counts:
            zf.writestr(prefix + "01_search_strategy/search_log.csv", _search_log_csv(db_counts))
            zf.writestr(prefix + "01_search_strategy/search_log.json",
                        json.dumps(_search_log_rows(db_counts), indent=2, ensure_ascii=False))

        # --- 2. Data Files (CSV) ---
        # Initial results (all retrieved before dedup/filtering) — prefer the
        # server's rawDocs (true pre-dedup set) over the browser's copy
        initial_csv = _docs_to_csv(raw_docs or docs)
        zf.writestr(prefix + "02_data/initial_search_results.csv", initial_csv)

        # Deduplicated records
        if dedup_docs:
            dedup_csv = _docs_to_csv(dedup_docs)
        else:
            dedup_csv = _docs_to_csv(docs)
        zf.writestr(prefix + "02_data/deduplicated_records.csv", dedup_csv)

        # Included studies (final set after all filters)
        included_docs = data.get("includedDocs", docs)
        if full_text_summary and (full_text_summary["screened"] or _dual_stage(server_run, "full-text")):
            included_docs = [row["paper"] for row in full_text_records["candidates"]
                             if row["decision"] and row["decision"]["decision"] == "include"]
        elif server_run and (screening_decisions or _dual_stage(server_run, "title-abstract")):
            included_docs = [row["paper"] for row in full_text_records["candidates"]]
        included_csv = _docs_to_csv(included_docs)
        zf.writestr(prefix + "02_data/included_studies.csv", included_csv)

        # Excluded studies with reasons
        if excluded_docs:
            excluded_csv = _excluded_docs_to_csv(excluded_docs)
        else:
            excluded_csv = "title,authors,year,source,doi,exclusion_reason\n(No exclusion data available)\n"
        zf.writestr(prefix + "02_data/excluded_studies.csv", excluded_csv)

        # Screening decisions (title & abstract screening — if performed via Screening page)
        if screening_decisions:
            sd_output = io.StringIO()
            sd_fields = ["title", "authors", "year", "source", "doi", "decision", "reason", "screened_at",
                         "ai_assisted", "ai_model", "ai_vendor", "ai_suggested", "ai_confidence"]
            sd_writer = csv.DictWriter(sd_output, fieldnames=sd_fields, extrasaction="ignore")
            sd_writer.writeheader()
            sd_writer.writerows(_csv_safe_row(row) for row in screening_decisions)
            zf.writestr(prefix + "02_data/screening_decisions.csv", sd_output.getvalue())

        if full_text_records and (full_text_records["candidates"] or full_text_records["inactive"]):
            output = io.StringIO()
            fields = ["paper_id", "title", "authors", "year", "source", "doi", "eligibility", "decision", "reason", "screened_at"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            by_id = {_screening_paper_id(doc): doc for doc in (server_run.get("docs") or [])}
            for row in full_text_records["candidates"] + full_text_records["inactive"]:
                paper = row.get("paper") or by_id.get(row["paperId"], {})
                decision = row.get("decision") or {}
                authors = paper.get("authors") or ""
                writer.writerow(_csv_safe_row({
                    "paper_id": row["paperId"], "title": paper.get("title", ""),
                    "authors": "; ".join(authors) if isinstance(authors, list) else authors,
                    "year": paper.get("year", ""), "source": paper.get("source", ""),
                    "doi": paper.get("doi", ""),
                    "eligibility": "ambiguous identity" if row.get("ambiguous") else
                                   "eligible" if "paper" in row else "no longer eligible",
                    "decision": decision.get("decision", "pending"), "reason": decision.get("reason", ""),
                    "screened_at": decision.get("timestamp", ""),
                }))
            zf.writestr(prefix + "02_data/full_text_screening.csv", output.getvalue())

        if server_run and server_run.get("dualScreening"):
            zf.writestr(prefix + "02_data/two_reviewer_screening.json",
                        json.dumps(server_run["dualScreening"], indent=2, ensure_ascii=False))

        # Fill-in templates for PRISMA items 18/21 (risk of bias) and 22 (certainty)
        final_studies = (included_docs if full_text_summary and
                         (full_text_summary["screened"] or _dual_stage(server_run, "full-text")) else
                         [r for r in screening_decisions if r.get("decision") == "include"]
                         if screening_decisions else included_docs)
        rob_csv, grade_csv = _assessment_templates(final_studies, review_protocol)
        zf.writestr(prefix + "02_data/risk_of_bias_template.csv", rob_csv)
        zf.writestr(prefix + "02_data/grade_certainty_template.csv", grade_csv)

        # --- 3. Images ---
        import base64 as _b64

        # Always generate PRISMA SVG server-side (reliable, resolution-independent)
        prisma_svg = _build_prisma_svg(prisma, search_name, ta_screening=ta_screening,
                                       manual_evidence=_manual_evidence(server_run) if server_run else None,
                                       full_text=full_text_summary)
        if prisma_svg:
            zf.writestr(prefix + "04_images/prisma_flow_diagram.svg", prisma_svg)

        # Also decode any frontend-rendered PNGs (charts etc.) from the payload
        images_payload = data.get("images", {})  # { "filename.png": "data:image/png;base64,..." }
        if images_payload:
            for img_name, data_url in images_payload.items():
                try:
                    # Strip "data:image/png;base64," prefix
                    if "," in data_url:
                        img_bytes = _b64.b64decode(data_url.split(",", 1)[1])
                    else:
                        img_bytes = _b64.b64decode(data_url)
                    safe_img_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in img_name)
                    zf.writestr(prefix + "04_images/" + safe_img_name, img_bytes)
                except Exception:
                    pass  # skip corrupted image data
        if not images_payload and not prisma_svg:
            images_readme = ("Place PRISMA flow diagrams and other visualisations in this folder.\n\n"
                             "Suggested files:\n"
                             "- prisma_flow_diagram.svg\n"
                             "- prisma_flow_diagram.png\n"
                             "- chart_by_year.png\n"
                             "- chart_by_database.png\n"
                             "- chart_by_doc_type.png\n\n"
                             "Tip: Use the Charts page in RSP to export PNG visualisations,\n"
                             "and the PRISMA page to capture the flow diagram.\n")
            zf.writestr(prefix + "04_images/README_images.txt", images_readme)

        # --- 4. README ---
        readme = _build_readme(folder_name, search_name, keywords, databases, filters,
                               execution_date, prisma, db_labels)
        zf.writestr(prefix + "README.md", readme)

        # --- 5. PRISMA data (JSON for reproducibility) ---
        prisma_json = json.dumps(prisma, indent=2, ensure_ascii=False)
        zf.writestr(prefix + "02_data/prisma_data.json", prisma_json)

        # --- 7. search_configuration.json (machine-readable, complete reproducibility record) ---
        db_labels = {
            "hal": "HAL Open Science", "arxiv": "arXiv", "ieee": "IEEE Xplore",
            "scopus": "Scopus", "inspec": "Inspec (Engineering Village)",
            "sciencedirect": "ScienceDirect",
            "springer": "Springer Nature", "pubmed": "PubMed",
            "acm": "ACM Digital Library", "openalex": "OpenAlex",
            "ebsco": "EBSCO Discovery Service", "wos": "Web of Science"
        }
        search_config = {
            "schema_version": "1.0",
            "tool": "Review Sentinel Prime (RSP)",
            "execution_date": execution_date,
            "search_name": search_name,
            "query": {
                "keywords": keywords,
                "rows_per_page": rows_per_page,
            },
            "databases": [
                {"id": db, "label": db_labels.get(db, db)} for db in databases
            ],
            "inclusion_exclusion_criteria": {
                "year_range": {
                    "from": filters.get("yearFrom"),
                    "to": filters.get("yearTo"),
                },
                "include_terms": filters.get("includeTerms", []),
                "include_terms_mode": filters.get("includeTermsMode", "all"),
                "exclude_terms": filters.get("excludeTerms", []),
                "exclude_terms_mode": filters.get("excludeTermsMode", "any"),
                "include_venues": filters.get("includeVenues", []),
                "include_venues_mode": filters.get("includeVenuesMode", "any"),
                "exclude_venues": filters.get("excludeVenues", []),
                "exclude_venues_mode": filters.get("excludeVenuesMode", "any"),
                "document_types": filters.get("docTypes", []),
                "languages": filters.get("languages", []),
            },
            "validation_papers": validation_papers if validation_papers else [],
            "results_summary": {
                "total_found": prisma.get("identification", {}).get("total_found", 0),
                "total_retrieved": prisma.get("identification", {}).get("retrieved", 0),
                "duplicates_removed": prisma.get("screening", {}).get("duplicates", 0),
                "total_included": len(docs),
                "per_database": prisma.get("identification", {}).get("databases", {}),
            },
        }
        config_json = json.dumps(search_config, indent=2, ensure_ascii=False)
        zf.writestr(
            prefix + "01_search_strategy/search_configuration.json",
            config_json
        )

        # --- 8. Full search metadata (JSON, legacy / summary) ---
        metadata = {
            "search_name": search_name,
            "keywords": keywords,
            "databases": databases,
            "filters": filters,
            "execution_date": execution_date,
            "total_found": prisma.get("identification", {}).get("total_found", 0),
            "total_retrieved": prisma.get("identification", {}).get("retrieved", 0),
            "total_included": prisma.get("included", len(docs)),
            "tool": "Review Sentinel Prime (RSP)",
            "export_version": "1.0",
            "db_queries": {
                db: db_query_map[db]
                for db in databases
                if db in db_query_map
            },
        }
        zf.writestr(prefix + "01_search_strategy/search_metadata.json", json.dumps(metadata, indent=2))

        # --- 9. Protocol (IC/EC as prose, Markdown) ---
        protocol = _build_protocol(search_name, keywords, databases, filters, execution_date, db_labels)
        zf.writestr(prefix + "03_protocol/protocol.md", protocol)

        # Machine-readable review protocol (research questions, registration,
        # reviewer team, synthesis/RoB methods) when the researcher filled it in
        if any(v for k, v in review_protocol.items() if k != "updatedAt"):
            zf.writestr(prefix + "03_protocol/review_protocol.json",
                        json.dumps(review_protocol, indent=2, ensure_ascii=False))

        # --- 10. Root-level importable config (same schema as "Export Config" button) ---
        # Placed at the ZIP root so it can be immediately imported via "📂 Import Config"
        importable_config = {
            "schema_version": "1.0",
            "tool": "Review Sentinel Prime (RSP)",
            "exported_at": execution_date,
            "query": {
                "keywords": keywords,
                "rows_per_page": rows_per_page,
            },
            "databases": [
                {"id": db, "label": db_labels.get(db, db)} for db in databases
            ],
            "inclusion_exclusion_criteria": {
                "year_range": {
                    "from": filters.get("yearFrom"),
                    "to":   filters.get("yearTo"),
                },
                "include_terms":  filters.get("includeTerms",  []),
                "include_terms_mode": filters.get("includeTermsMode", "all"),
                "exclude_terms":  filters.get("excludeTerms",  []),
                "exclude_terms_mode": filters.get("excludeTermsMode", "any"),
                "include_venues": filters.get("includeVenues", []),
                "include_venues_mode": filters.get("includeVenuesMode", "any"),
                "exclude_venues": filters.get("excludeVenues", []),
                "exclude_venues_mode": filters.get("excludeVenuesMode", "any"),
                "document_types": filters.get("docTypes",      []),
                "languages":      filters.get("languages",     []),
            },
            "validation_papers": validation_papers if validation_papers else [],
        }
        zf.writestr(
            prefix + "search_configuration.json",
            json.dumps(importable_config, indent=2, ensure_ascii=False)
        )

        checklist_record = None
        if server_run:
            stored = server_run.get("prismaChecklist") or {"version": 0, "items": {}}
            zf.writestr(prefix + "02_data/prisma_manual_evidence.json",
                        json.dumps(_manual_evidence(server_run), indent=2, ensure_ascii=False))
            context = data.get("checklistContext")
            matching_docs = (context.get("docs") == submitted_docs == docs) if isinstance(context, dict) else False
            if search_id and isinstance(context, dict):
                matching_docs = (context.get("docs") == data.get("includedDocs") == server_run.get("docs", [])
                                 and submitted_docs == docs
                                 and docs == (server_run.get("rawDocs") or server_run.get("docs", [])))
            matches_export = (_checklist_context_valid(context, run_id)
                              and _manual_evidence_matches(server_run, context)
                              and context["prisma"] == submitted_prisma
                              and matching_docs
                              and filters == server_run.get("filters", {})
                              and normalize_boolean_operators(keywords) == server_run.get("query", "")
                              and sorted(databases) == sorted(server_run.get("databases", [])))
            revision = _checklist_revision(server_run, context, review_protocol) if matches_export else None
            entries = []
            for item_id in _CHECKLIST_IDS:
                item = stored["items"].get(item_id, {})
                checked = item.get("checked", False)
                status = ("unconfirmed" if not checked else
                          "unverified" if revision is None else
                          "confirmed" if item.get("confirmedRevision") == revision else "stale")
                entries.append({"id": item_id, "status": status, "note": item.get("text", ""),
                                "confirmedRevision": item.get("confirmedRevision")})
            checklist_record = {"runId": run_id, "version": stored["version"],
                                "currentRevision": revision, "items": entries}
            zf.writestr(prefix + "05_evidence_checklist/researcher_checklist.json",
                        json.dumps(checklist_record, indent=2, ensure_ascii=False))

        # Written last so it can report which artefacts actually made it into the ZIP.
        checklist = _build_evidence_checklist(
            prisma,
            has_screening=bool(screening_decisions),
            written_files={n[len(prefix):] for n in zf.namelist() if n.startswith(prefix)},
            ta_screening=ta_screening,
            recall=_validation_recall(validation_papers, docs),
            review_protocol=review_protocol,
            full_text=full_text_summary,
            ai_usage={
                "count": sum(1 for r in screening_decisions if r.get("ai_assisted") == "yes"),
                "total": len(screening_decisions),
                "models": sorted({r.get("ai_model") for r in screening_decisions
                                  if r.get("ai_model") and r.get("ai_assisted") == "yes"}),
                "agreement": _ai_agreement_stats(screening_decisions),
            },
        )
        if checklist_record:
            checklist += "\n--- RESEARCHER CHECKLIST (run " + run_id + ") ---\n"
            if checklist_record["currentRevision"] is None:
                checklist += "Current browser evidence was not supplied; confirmations cannot be verified.\n"
            for item in checklist_record["items"]:
                checklist += f"  {item['id']:<4} {item['status']:<11}"
                if item["note"]:
                    checklist += " note: " + json.dumps(item["note"], ensure_ascii=False)
                checklist += "\n"
        else:
            checklist += "\n--- RESEARCHER CHECKLIST ---\nNo matching saved run was provided; checklist notes are unavailable.\n"
        zf.writestr(prefix + "05_evidence_checklist/PRISMA_evidence_checklist.txt", checklist)

    zip_buffer.seek(0)
    filename = f"{folder_name}.zip"
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name=filename)


def _build_search_strategy(search_name, keywords, databases, filters, execution_date, db_counts=None):
    """Generate the search strategy documentation text."""
    db_counts = db_counts or {}
    lines = [
        "=" * 70,
        "SEARCH STRATEGY DOCUMENTATION",
        "=" * 70,
        f"",
        f"Search Name:      {search_name}",
        f"Execution Date:   {execution_date}",
        f"Tool:             Review Sentinel Prime (RSP)",
        f"",
        "--- SEARCH QUERY ---",
        f"Keywords/Query:   {keywords}",
        f"",
        "--- DATABASES SEARCHED ---",
    ]
    db_labels = {
        "hal": "HAL Open Science", "arxiv": "arXiv", "ieee": "IEEE Xplore",
        "scopus": "Scopus", "inspec": "Inspec (Engineering Village)",
        "sciencedirect": "ScienceDirect",
        "springer": "Springer Nature", "pubmed": "PubMed",
        "acm": "ACM Digital Library", "openalex": "OpenAlex",
        "ebsco": "EBSCO Discovery Service", "wos": "Web of Science"
    }
    for db in databases:
        info = db_counts.get(db, {})
        date_str = info.get("date", execution_date[:10] if execution_date else "unknown")
        found_str = f", {info['found']:,} records found" if "found" in info else ""
        retrieved_str = f", {info['retrieved']:,} retrieved" if "retrieved" in info else ""
        lines.append(f"  - {db_labels.get(db, db)} — searched on {date_str}{found_str}{retrieved_str}")
        if info.get("dateLimitAtDatabase"):
            removed = info.get("removedDateAtDatabase")
            removed_txt = f"{removed:,}" if isinstance(removed, int) else "unknown"
            lines.append(f"      Date limit applied at the database: {info.get('foundInRange', 0) or 0:,} records in range; "
                         f"{removed_txt} outside the range were counted, not downloaded")
        status = info.get("status")
        if status and status != "complete":
            lines.append(f"      COVERAGE {status.upper()}: {info.get('message', '')}")
        elif not status and (info.get("error") or info.get("warning")):
            lines.append(f"      COVERAGE WARNING: {info.get('error') or info.get('warning')}")
    if db_counts:
        lines.append("  (Per-database details: search_log.csv in this folder)")
    lines.append("")
    lines.append("--- FILTERS & CONSTRAINTS ---")
    if filters.get("yearFrom") or filters.get("yearTo"):
        lines.append(f"  Year range: {filters.get('yearFrom', 'any')} - {filters.get('yearTo', 'any')}")
    if filters.get("excludeTerms"):
        mode_label = "ALL" if filters.get("excludeTermsMode", "any") == "all" else "ANY"
        lines.append(f"  Exclude terms ({mode_label}): {', '.join(filters['excludeTerms'])}")
    if filters.get("includeTerms"):
        mode_label = "ALL" if filters.get("includeTermsMode", "all") == "all" else "ANY"
        lines.append(f"  Include terms ({mode_label}): {', '.join(filters['includeTerms'])}")
    if filters.get("docTypes"):
        lines.append(f"  Document types: {', '.join(filters['docTypes'])}")
    if filters.get("languages"):
        lines.append(f"  Languages: {', '.join(filters['languages'])}")
    if filters.get("includeVenues"):
        lines.append(f"  Include venues: {', '.join(filters['includeVenues'])}")
    if filters.get("excludeVenues"):
        lines.append(f"  Exclude venues: {', '.join(filters['excludeVenues'])}")
    if not any(filters.get(k) for k in ["yearFrom", "yearTo", "excludeTerms", "includeTerms", "docTypes", "languages", "includeVenues", "excludeVenues"]):
        lines.append("  (No filters applied)")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def _build_filter_documentation(filters):
    """Generate detailed filter documentation."""
    lines = ["APPLIED FILTERS AND CONSTRAINTS", "=" * 40, ""]
    if filters.get("yearFrom"):
        lines.append(f"Year From: {filters['yearFrom']}")
    if filters.get("yearTo"):
        lines.append(f"Year To: {filters['yearTo']}")
    if filters.get("excludeTerms"):
        mode_label = "ALL" if filters.get("excludeTermsMode", "any") == "all" else "ANY"
        lines.append(f"Exclude Terms ({mode_label}): {', '.join(filters['excludeTerms'])}")
    if filters.get("includeTerms"):
        mode_label = "ALL" if filters.get("includeTermsMode", "all") == "all" else "ANY"
        lines.append(f"Include Terms ({mode_label}): {', '.join(filters['includeTerms'])}")
    if filters.get("docTypes"):
        lines.append(f"Document Types: {', '.join(filters['docTypes'])}")
    if filters.get("languages"):
        lines.append(f"Languages: {', '.join(filters['languages'])}")
    if filters.get("includeVenues"):
        mode_label = "ALL" if filters.get("includeVenuesMode", "any") == "all" else "ANY"
        lines.append(f"Include Venues ({mode_label}): {', '.join(filters['includeVenues'])}")
    if filters.get("excludeVenues"):
        mode_label = "ALL" if filters.get("excludeVenuesMode", "any") == "all" else "ANY"
        lines.append(f"Exclude Venues ({mode_label}): {', '.join(filters['excludeVenues'])}")
    if not any(filters.values()):
        lines.append("No filters were applied to this search.")
    return "\n".join(lines)


def _build_protocol(search_name, keywords, databases, filters, execution_date, db_labels):
    """Generate a Markdown protocol document describing IC/EC criteria."""
    ic_lines = []
    ec_lines = []

    if filters.get("yearFrom") or filters.get("yearTo"):
        y_from = filters.get("yearFrom", "any")
        y_to   = filters.get("yearTo",   "any")
        ic_lines.append(f"- Published between **{y_from}** and **{y_to}**")
    if filters.get("includeTerms"):
        mode_label = "all of" if filters.get("includeTermsMode", "all") == "all" else "any of"
        ic_lines.append(f"- Title or abstract must contain {mode_label}: "
                        + ", ".join(f"`{t}`" for t in filters["includeTerms"]))
    if filters.get("includeVenues"):
        mode_label = "all of" if filters.get("includeVenuesMode", "any") == "all" else "any of"
        ic_lines.append("- Published in " + mode_label + " the following venues: "
                        + ", ".join(f"`{v}`" for v in filters["includeVenues"]))
    if filters.get("docTypes"):
        ic_lines.append("- Document type is one of: " + ", ".join(filters["docTypes"]))
    if filters.get("languages"):
        ic_lines.append("- Language is one of: " + ", ".join(filters["languages"]))

    if filters.get("excludeTerms"):
        mode_label = "all of" if filters.get("excludeTermsMode", "any") == "all" else "any of"
        ec_lines.append("- Contains " + mode_label + " the exclude terms: "
                        + ", ".join(f"`{t}`" for t in filters["excludeTerms"]))
    if filters.get("excludeVenues"):
        mode_label = "all of" if filters.get("excludeVenuesMode", "any") == "all" else "any of"
        ec_lines.append("- Published in " + mode_label + " the excluded venues: "
                        + ", ".join(f"`{v}`" for v in filters["excludeVenues"]))

    if not ic_lines:
        ic_lines = ["- No specific inclusion criteria were applied beyond the search query."]
    if not ec_lines:
        ec_lines = ["- No explicit exclusion criteria were applied."]

    db_list = "\n".join(f"- {db_labels.get(db, db)}" for db in databases)
    ic_block = "\n".join(ic_lines)
    ec_block = "\n".join(ec_lines)

    return (
        f"# Search Protocol: {search_name}\n\n"
        f"**Tool:** Review Sentinel Prime (RSP)  \n"
        f"**Execution Date:** {execution_date}\n\n"
        f"---\n\n"
        f"## 1. Research Query\n\n"
        f"```\n{keywords}\n```\n\n"
        f"## 2. Databases Searched\n\n"
        f"{db_list}\n\n"
        f"## 3. Inclusion Criteria\n\n"
        f"{ic_block}\n\n"
        f"## 4. Exclusion Criteria\n\n"
        f"{ec_block}\n\n"
        f"## 5. Reproducibility Notes\n\n"
        f"- The complete machine-readable configuration (query, databases, criteria,\n"
        f"  validation papers) is in `01_search_strategy/search_configuration.json`.\n"
        f"- Results may vary slightly across executions due to database indexing updates.\n"
        f"- API keys are required for IEEE, Scopus, Inspec, ScienceDirect, Springer.\n\n"
        f"## 6. References\n\n"
        f"- Page, M. J., et al. (2021). The PRISMA 2020 statement. *BMJ*, 372, n71.\n"
        f"- Kitchenham, B., & Charters, S. (2007). Guidelines for performing systematic\n"
        f"  literature reviews in software engineering. EBSE Technical Report.\n"
    )


def _docs_to_csv(docs):
    """Convert document list to CSV string."""
    output = io.StringIO()
    fields = ["source", "title", "authors", "year", "journal", "doi", "url", "abstract", "keywords", "docType"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for doc in docs:
        row = dict(doc)
        if isinstance(row.get("authors"), list):
            row["authors"] = "; ".join(row["authors"])
        if isinstance(row.get("keywords"), list):
            row["keywords"] = "; ".join(row["keywords"])
        writer.writerow(_csv_safe_row(row))
    return "\ufeff" + output.getvalue()


def _excluded_docs_to_csv(excluded_docs):
    """Convert excluded documents with reasons to CSV."""
    output = io.StringIO()
    fields = ["source", "title", "authors", "year", "doi", "exclusion_reason"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for doc in excluded_docs:
        row = dict(doc)
        if isinstance(row.get("authors"), list):
            row["authors"] = "; ".join(row["authors"])
        writer.writerow(_csv_safe_row(row))
    return "\ufeff" + output.getvalue()


def _build_prisma_svg(prisma, search_name="", ta_screening=None, manual_evidence=None, full_text=None):
    """Generate a PRISMA 2020 flow diagram as an SVG string from prisma data.

    ta_screening: optional {total, screened, included, excluded, unsure}
    from manual title & abstract screening — rendered as an extra stage so
    the exported diagram matches the interactive flow on the PRISMA page.
    """
    identification = prisma.get("identification", {})
    if not identification:
        return ""

    screening   = prisma.get("screening",   {})
    eligibility = prisma.get("eligibility", {})
    included    = int(prisma.get("included", 0))

    total_identified = int(identification.get("total_found", 0))
    retrieved        = int(identification.get("retrieved",   0))
    databases        = identification.get("databases", {})
    removed_date     = int(identification.get("removed_date_at_database") or 0)
    not_retrieved    = int(identification.get("not_retrieved") or 0)

    duplicates_removed = int(screening.get("duplicates", 0))
    after_dedup        = retrieved - duplicates_removed

    screening_excluded = {k: int(v) for k, v in screening.items()
                          if isinstance(v, (int, float)) and k != "duplicates"}
    screening_total    = sum(screening_excluded.values())
    after_screening    = after_dedup - screening_total

    eligibility_excluded = {k: int(v) for k, v in eligibility.items()
                             if isinstance(v, (int, float))}
    eligibility_total = sum(eligibility_excluded.values())

    reason_labels = {
        "duplicates": "Duplicates", "year_range": "Year range",
        "doc_type": "Document type", "language": "Language",
        "exclude_terms": "Exclude terms", "must_include_terms": "Must-include terms",
        "include_venues": "Venue inclusion", "exclude_venues": "Venue exclusion",
    }
    db_labels = {
        "hal": "HAL", "arxiv": "arXiv", "ieee": "IEEE Xplore",
        "scopus": "Scopus", "inspec": "Inspec",
        "sciencedirect": "ScienceDirect", "springer": "Springer",
        "pubmed": "PubMed", "acm": "ACM", "openalex": "OpenAlex",
        "ebsco": "EBSCO",
        "wos": "Web of Science",
    }

    def esc(s):
        return (str(s)
                .replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    # Layout constants
    MAIN_X  = 60
    BW, BH  = 320, 90
    EXCL_X  = MAIN_X + BW + 60
    EXCL_W  = 240
    EXCL_H  = 68
    ARROW_GAP = 40
    DB_W, DB_H = 110, 52
    DB_GAP     = 10
    SVG_W = EXCL_X + EXCL_W + 40

    lines = []

    def box(x, y, w, h, fill, stroke, label, title, detail):
        lw = len(label) * 7 + 12
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>'
            f'<rect x="{x+12}" y="{y-9}" width="{lw}" height="18" rx="3" fill="{stroke}"/>'
            f'<text x="{x+17}" y="{y+5}" class="lbl">{esc(label)}</text>'
            f'<text x="{x+w//2}" y="{y+42}" text-anchor="middle" class="ttl">{esc(title)}</text>'
            f'<text x="{x+w//2}" y="{y+62}" text-anchor="middle" class="dtl">{esc(detail)}</text>'
        )

    def varrow(x, y, length):
        return (f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y+length-6}" '
                f'stroke="#555" stroke-width="2" marker-end="url(#ah)"/>')

    def harrow(x1, y, x2):
        return (f'<line x1="{x1}" y1="{y}" x2="{x2-6}" y2="{y}" '
                f'stroke="#c0392b" stroke-width="2" marker-end="url(#ah-red)"/>')

    def excl_box(x, y, title_text, detail_text):
        max_chars = (EXCL_W - 20) // 6
        trimmed = (detail_text[:max_chars - 2] + "…") if len(detail_text) > max_chars else detail_text
        return (
            f'<rect x="{x}" y="{y}" width="{EXCL_W}" height="{EXCL_H}" rx="5" '
            f'fill="#fdedec" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="5,3"/>'
            f'<text x="{x+10}" y="{y+22}" class="ex-ttl">{esc(title_text)}</text>'
            f'<text x="{x+10}" y="{y+44}" class="ex-dtl">{esc(trimmed)}</text>'
        )

    cur_y = 30

    # Title
    lines.append(f'<text x="{MAIN_X + BW // 2}" y="{cur_y}" '
                 f'text-anchor="middle" class="page-ttl">{esc("PRISMA 2020 Flow Diagram")}</text>')
    cur_y += 8
    if search_name:
        lines.append(f'<text x="{MAIN_X + BW // 2}" y="{cur_y + 16}" '
                     f'text-anchor="middle" class="sub-ttl">{esc(search_name)}</text>')
        cur_y += 22
    cur_y += 12

    # Database chips
    db_entries = list(databases.items())
    if db_entries:
        cols   = min(len(db_entries), 5)
        rows   = -(-len(db_entries) // cols)   # ceiling div
        grid_w = cols * (DB_W + DB_GAP) - DB_GAP
        start_x = MAIN_X + BW // 2 - grid_w // 2
        for i, (db, counts) in enumerate(db_entries):
            col = i % cols
            row = i // cols
            cx  = start_x + col * (DB_W + DB_GAP)
            cy  = cur_y + row * (DB_H + DB_GAP)
            found = counts.get("found", 0) if isinstance(counts, dict) else counts
            lines.append(
                f'<rect x="{cx}" y="{cy}" width="{DB_W}" height="{DB_H}" rx="5" '
                f'fill="#eaf5fc" stroke="#2563eb" stroke-width="1.5"/>'
                f'<text x="{cx + DB_W // 2}" y="{cy + 20}" text-anchor="middle" class="db-nm">'
                f'{esc(db_labels.get(db, db))}</text>'
                f'<text x="{cx + DB_W // 2}" y="{cy + 38}" text-anchor="middle" class="db-ct">'
                f'{found:,}</text>'
            )
        cur_y += rows * (DB_H + DB_GAP) + 5
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP

    # IDENTIFICATION
    lines.append(box(MAIN_X, cur_y, BW, BH,
                     "#eaf5fc", "#2563eb", "IDENTIFICATION",
                     f"Records identified: {total_identified:,}",
                     f"Retrieved: {retrieved:,}"))
    if removed_date or not_retrieved:
        # Records counted by the databases but never downloaded: outside the
        # date limit applied at the database, or not retrieved (cap/error).
        mid_y = cur_y + BH // 2
        parts = []
        if removed_date:
            parts.append(f"Date limit: {removed_date:,}")
        if not_retrieved:
            parts.append(f"Not retrieved: {not_retrieved:,}")
        lines.append(harrow(MAIN_X + BW, mid_y, EXCL_X))
        lines.append(excl_box(EXCL_X, mid_y - EXCL_H // 2,
                              f"Removed before retrieval (n={removed_date + not_retrieved:,})",
                              "; ".join(parts)))
        cur_y += BH
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP
        lines.append(box(MAIN_X, cur_y, BW, BH,
                         "#eaf5fc", "#2563eb", "RETRIEVAL",
                         f"Records retrieved: {retrieved:,}",
                         "Downloaded for screening"))
    cur_y += BH
    lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
    cur_y += ARROW_GAP

    # DEDUPLICATION
    lines.append(box(MAIN_X, cur_y, BW, BH,
                     "#fef9e7", "#d97706", "DEDUPLICATION",
                     f"After dedup: {after_dedup:,}",
                     f"Duplicates removed: {duplicates_removed:,}"))
    if duplicates_removed > 0:
        mid_y = cur_y + BH // 2
        lines.append(harrow(MAIN_X + BW, mid_y, EXCL_X))
        lines.append(excl_box(EXCL_X, mid_y - EXCL_H // 2,
                               f"Removed (n={duplicates_removed:,})",
                               "Matched by DOI or title"))
    cur_y += BH
    lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
    cur_y += ARROW_GAP

    # SCREENING
    lines.append(box(MAIN_X, cur_y, BW, BH,
                     "#fef9e7", "#d97706", "SCREENING",
                     f"After screening: {after_screening:,}",
                     f"Screened: {after_dedup:,} | Excluded: {screening_total:,}"))
    if screening_total > 0:
        mid_y = cur_y + BH // 2
        lines.append(harrow(MAIN_X + BW, mid_y, EXCL_X))
        ex_detail = ", ".join(
            f"{reason_labels.get(k, k)}: {v}"
            for k, v in screening_excluded.items()
        )
        lines.append(excl_box(EXCL_X, mid_y - EXCL_H // 2,
                               f"Excluded (n={screening_total:,})", ex_detail))
    cur_y += BH
    lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
    cur_y += ARROW_GAP

    # ELIGIBILITY
    lines.append(box(MAIN_X, cur_y, BW, BH,
                     "#fdebd0", "#ea580c", "ELIGIBILITY",
                     f"Assessed: {after_screening:,}",
                     f"Excluded: {eligibility_total:,}"))
    if eligibility_total > 0:
        mid_y = cur_y + BH // 2
        lines.append(harrow(MAIN_X + BW, mid_y, EXCL_X))
        ex_detail = ", ".join(
            f"{reason_labels.get(k, k)}: {v}"
            for k, v in eligibility_excluded.items()
        )
        lines.append(excl_box(EXCL_X, mid_y - EXCL_H // 2,
                               f"Excluded (n={eligibility_total:,})", ex_detail))
    cur_y += BH
    lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
    cur_y += ARROW_GAP

    # T&A SCREENING (manual title & abstract screening, when performed)
    final_included = included
    included_detail = "Final set after automated filters"
    incomplete_screening = False
    if ta_screening and ta_screening.get("total"):
        ta_total = int(ta_screening.get("total", 0))
        ta_inc = int(ta_screening.get("included", 0))
        ta_exc = int(ta_screening.get("excluded", 0))
        ta_uns = int(ta_screening.get("unsure", 0))
        ta_screened = int(ta_screening.get("screened", ta_inc + ta_exc + ta_uns))
        ta_pending = max(0, ta_total - ta_screened)
        incomplete_screening = ta_pending > 0 or ta_uns > 0
        lines.append(box(MAIN_X, cur_y, BW, BH,
                         "#f5f3ff", "#7c3aed", "T&A SCREENING",
                         f"Screened: {ta_screened:,}/{ta_total:,}",
                         f"Included: {ta_inc:,} | Excluded: {ta_exc:,} | Unsure: {ta_uns:,}"))
        not_retained = ta_exc + ta_uns + ta_pending
        if not_retained > 0:
            mid_y = cur_y + BH // 2
            lines.append(harrow(MAIN_X + BW, mid_y, EXCL_X))
            lines.append(excl_box(EXCL_X, mid_y - EXCL_H // 2,
                                   f"Not retained (n={not_retained:,})",
                                   f"Excluded: {ta_exc}, unsure: {ta_uns}, pending: {ta_pending}"))
        cur_y += BH
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP
        final_included = ta_inc
        included_detail = "After manual title & abstract screening"

    if full_text and (full_text["screened"] or full_text.get("dualReview")):
        incomplete_screening = incomplete_screening or full_text["pending"] > 0 or full_text["unsure"] > 0
        lines.append(box(MAIN_X, cur_y, BW, BH, "#f5f3ff", "#7c3aed", "FULL TEXT",
                         f"Included: {full_text['included']:,} | Excluded: {full_text['excluded']:,}",
                         f"Not retrieved: {full_text['notRetrieved']:,} | Unsure: {full_text['unsure']:,} | Pending: {full_text['pending']:,}"))
        cur_y += BH
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP
        final_included = full_text["included"]
        included_detail = "After recorded full-text decisions"

    manual_evidence = manual_evidence or {}
    snowballing = manual_evidence.get("snowballing")
    if snowballing:
        found = snowballing["backward"] + snowballing["forward"] + snowballing["other"]
        added = found - snowballing["excluded"]
        final_included += added
        included_detail += f" + {added:,} other-source records"

    # INCLUDED
    lines.append(box(MAIN_X, cur_y, BW, BH,
                     "#d4efdf", "#16a34a", "INCLUDED",
                     f"{'Included so far' if incomplete_screening else 'Studies included'}: {final_included:,}",
                     included_detail))
    if snowballing:
        lines.append(box(EXCL_X, cur_y, EXCL_W, BH,
                         "#f0fdf4", "#15803d", "OTHER SOURCES",
                         f"Other-source records: {found:,}",
                         f"Excluded: {snowballing['excluded']:,} | Added: {added:,}"))
        lines.append(f'<line x1="{EXCL_X}" y1="{cur_y + BH // 2}" '
                     f'x2="{MAIN_X + BW + 6}" y2="{cur_y + BH // 2}" '
                     'stroke="#555" stroke-width="2" marker-end="url(#ah)"/>')
    cur_y += BH
    for step in manual_evidence.get("manualSteps", []):
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP
        final_included += step["added"] - step["removed"]
        lines.append(box(MAIN_X, cur_y, BW, BH,
                         "#f4ecf7", "#8e44ad", "MANUAL STEP",
                         step["name"], f"Remaining: {final_included:,} | +{step['added']:,} / -{step['removed']:,}"))
        cur_y += BH
    if manual_evidence.get("manualSteps"):
        lines.append(varrow(MAIN_X + BW // 2, cur_y, ARROW_GAP))
        cur_y += ARROW_GAP
        lines.append(box(MAIN_X, cur_y, BW, BH,
                         "#d4efdf", "#16a34a", "FINAL",
                         f"{'Current total' if incomplete_screening else 'Final studies for synthesis'}: {final_included:,}",
                         "After manual review steps"))
        cur_y += BH
    cur_y += 30

    svg_h = cur_y

    svg_header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{SVG_W}" height="{svg_h}" viewBox="0 0 {SVG_W} {svg_h}">'
        f'<rect width="{SVG_W}" height="{svg_h}" fill="#ffffff"/>'
        f'<defs>'
        f'<marker id="ah" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
        f'<polygon points="0 0,10 3.5,0 7" fill="#555"/></marker>'
        f'<marker id="ah-red" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
        f'<polygon points="0 0,10 3.5,0 7" fill="#c0392b"/></marker>'
        f'</defs>'
        f'<style>'
        f'text{{font-family:\'Segoe UI\',Arial,Helvetica,sans-serif}}'
        f'.page-ttl{{font-size:17px;font-weight:700;fill:#0f172a}}'
        f'.sub-ttl{{font-size:12px;fill:#475569}}'
        f'.lbl{{font-size:9px;font-weight:700;fill:#fff;letter-spacing:.6px}}'
        f'.ttl{{font-size:14px;font-weight:600;fill:#0f172a}}'
        f'.dtl{{font-size:11px;fill:#475569}}'
        f'.ex-ttl{{font-size:11px;font-weight:700;fill:#b91c1c}}'
        f'.ex-dtl{{font-size:10px;fill:#b91c1c}}'
        f'.db-nm{{font-size:10px;font-weight:600;fill:#1e3a5f}}'
        f'.db-ct{{font-size:14px;font-weight:700;fill:#2563eb}}'
        f'</style>'
    )

    return svg_header + "\n".join(lines) + "\n</svg>"


def _build_readme(folder_name, search_name, keywords, databases, filters, execution_date, prisma, db_labels):
    """Generate the comprehensive README for the replication package."""
    identification = prisma.get("identification", {})
    screening = prisma.get("screening", {})
    eligibility = prisma.get("eligibility", {})
    included = prisma.get("included", 0)

    readme = f"""# SLR Replication Package: {search_name}

## Overview

This replication package contains all artefacts required to reproduce the systematic literature
search conducted using **Review Sentinel Prime (RSP)**. It is structured to align with the
PRISMA 2020 guidelines for transparent reporting of systematic reviews.

**Search Name:** {search_name}
**Execution Date:** {execution_date}
**Tool Used:** Review Sentinel Prime (RSP)

---

## Package Structure

```
{folder_name}/
├── README.md                                    ← This file
├── search_configuration.json                    ← Re-importable config ("Import Config (JSON)")
├── 01_search_strategy/
│   ├── search_strategy.txt                      ← Full search strategy documentation
│   ├── search_strings_per_database.txt          ← Search strings per data source
│   ├── filters_and_constraints.txt              ← Applied filters and constraints
│   ├── search_configuration.json                ← Full search configuration snapshot
│   └── search_metadata.json                     ← Machine-readable search metadata
├── 02_data/
│   ├── initial_search_results.csv               ← All records retrieved (before deduplication)
│   ├── deduplicated_records.csv                 ← Records after duplicate removal
│   ├── included_studies.csv                     ← Final included studies
│   ├── excluded_studies.csv                     ← Excluded studies with reasons
│   ├── screening_decisions.csv                  ← Title & abstract screening decisions (if performed)
│   ├── full_text_screening.csv                  ← Full-text decisions and reasons (if candidates exist)
│   ├── two_reviewer_screening.json              ← Reviewer and adjudication history (if enabled)
│   ├── risk_of_bias_template.csv                ← Blank risk-of-bias table per included study (fill in)
│   ├── grade_certainty_template.csv             ← Blank GRADE certainty table per outcome (fill in)
│   ├── prisma_data.json                         ← PRISMA flow data (machine-readable)
│   └── prisma_manual_evidence.json              ← Manual steps and other sources (when a run is available)
├── 03_protocol/
│   └── protocol.md                              ← Inclusion/exclusion criteria as prose
├── 04_images/
│   └── README_images.txt                        ← Instructions for adding visualisations
└── 05_evidence_checklist/
    └── PRISMA_evidence_checklist.txt            ← PRISMA item-to-artefact mapping
```

---

## PRISMA Workflow Mapping

### Identification
- **Total records found across databases:** {identification.get('total_found', 'N/A')}
- **Records retrieved:** {identification.get('retrieved', 'N/A')}
- **Databases searched:** {', '.join(db_labels.get(db, db) for db in databases)}
- **Artefact:** `01_search_strategy/`, `02_data/initial_search_results.csv`

### Screening
- **Duplicates removed:** {screening.get('duplicates', 0)}
- **Excluded by year range:** {screening.get('year_range', 0)}
- **Excluded by document type:** {screening.get('doc_type', 0)}
- **Excluded by language:** {screening.get('language', 0)}
- **Artefact:** `02_data/deduplicated_records.csv`, `02_data/excluded_studies.csv`

### Eligibility
- **Excluded by include terms:** {eligibility.get('must_include_terms', 0)}
- **Excluded by exclude terms:** {eligibility.get('exclude_terms', 0)}
- **Excluded by include venues filter:** {eligibility.get('include_venues', 0)}
- **Excluded by exclude venues filter:** {eligibility.get('exclude_venues', 0)}
- **Artefact:** `02_data/excluded_studies.csv`

### Inclusion
- **Final included studies:** {included}
- **Artefact:** `02_data/included_studies.csv`

---

## How to Reproduce

1. Install Review Sentinel Prime (RSP) and activate the virtual environment.
2. Configure API keys for the required databases in Settings.
3. Enter the following search query:
   ```
   {keywords}
   ```
4. Select databases: {', '.join(db_labels.get(db, db) for db in databases)}
5. Apply filters as documented in `01_search_strategy/filters_and_constraints.txt`.
6. Execute the search and compare results with `02_data/included_studies.csv`.

---

## Citation

If you use this replication package, please acknowledge the tool:

> "Search conducted using Review Sentinel Prime (RSP), a systematic literature
> search tool built upon the Replication Package Builder by J. Eduardo Ferreira
> Ribeiro and J. Antonio Dantas Macedo."

---

## License

This replication package is provided for research reproducibility and transparency.
"""
    return readme


def _is_snowball_doc(doc):
    """True only if every source for this record came from citation searching."""
    sources = doc.get("sources") or ([doc.get("source")] if doc.get("source") else [])
    return bool(sources) and all(str(s).startswith("Snowball") for s in sources)


def _count_other_methods(docs):
    """PRISMA 2020 separates database identification from citation searching."""
    return sum(1 for d in docs if _is_snowball_doc(d))


def _normalize_title_for_match(text):
    """Mirror of the browser's title normalisation used for validation papers."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _titles_match(a, b):
    """Fuzzy title equivalence, matching the browser's validation-panel rules."""
    if not a or not b:
        return False
    if a == b:
        return True
    # Guard against degenerate one-word titles trivially matching longer ones.
    if min(len(a), len(b)) >= 12 and (a in b or b in a):
        return True
    set_a, set_b = set(a.split(" ")), set(b.split(" "))
    common = len(set_a & set_b)
    overlap_max = common / (max(len(set_a), len(set_b)) or 1)
    overlap_min = common / (min(len(set_a), len(set_b)) or 1)
    return overlap_max > 0.85 or (common >= 4 and overlap_min > 0.9)


def _validation_recall(validation_papers, docs):
    """Report which seed papers the search actually retrieved.

    Validation papers are stored as plain title strings by the UI.
    """
    titles = [str(p.get("title") if isinstance(p, dict) else p or "").strip()
              for p in (validation_papers or [])]
    titles = [t for t in titles if t]
    if not titles:
        return None

    normalized_docs = [(_normalize_title_for_match(d.get("title")), d) for d in docs]
    found, missing = [], []
    for title in titles:
        norm = _normalize_title_for_match(title)
        sources = sorted({
            str(d.get("source") or "Unknown")
            for doc_norm, d in normalized_docs
            if _titles_match(norm, doc_norm)
        })
        (found if sources else missing).append((title, sources))
    return {"found": found, "missing": missing, "total": len(titles)}


_EXCLUSION_REASON_LABELS = {
    "duplicates": "Duplicate records",
    "year_range": "Outside year range",
    "doc_type": "Document type excluded",
    "language": "Language excluded",
    "exclude_terms": "Matched an exclude term",
    "must_include_terms": "Missing a required include term",
    "include_venues": "Venue not in include list",
    "exclude_venues": "Venue in exclude list",
}


def _build_evidence_checklist(prisma, has_screening=False, written_files=None, ta_screening=None,
                              recall=None, ai_usage=None, review_protocol=None, full_text=None):
    """Generate an evidence checklist mapping artefacts to PRISMA items.

    `written_files` are the ZIP-relative paths actually written, so each row can
    be marked present rather than merely asserted.
    """
    identification = prisma.get("identification", {})
    screening = prisma.get("screening", {})
    eligibility = prisma.get("eligibility", {})
    included = prisma.get("included", 0)
    if review_protocol is None:
        review_protocol = _load_protocol()

    def present(path):
        if written_files is None:
            return " "
        if path.endswith("/"):
            return "OK" if any(f.startswith(path) for f in written_files) else "--"
        return "OK" if path in written_files else "--"

    def row(item, artefact):
        return f"  [{present(artefact):>2}] {item:<30} {artefact}"

    lines = [
        "=" * 88,
        "PRISMA 2020 EVIDENCE CHECKLIST",
        "Mapping of exported artefacts to PRISMA reporting items",
        "=" * 88,
        "",
        "This checklist maps the contents of this replication package to the",
        "relevant PRISMA 2020 items, supporting auditability and transparency.",
        "File presence does not establish that an item is reported or that a",
        "template is completed. Confirm the report and public availability.",
        "",
        "  [OK] = artefact is present in this package",
        "  [--] = artefact not present in this package",
        "",
        "-" * 88,
        row("S1: Search strategy", "01_search_strategy/"),
        row("S2: Search strings", "01_search_strategy/search_strings_per_database.txt"),
        row("S3: Databases & registers", "01_search_strategy/search_metadata.json"),
        row("S4: Date of search", "01_search_strategy/search_metadata.json"),
        row("S5: Constraints/limits", "01_search_strategy/filters_and_constraints.txt"),
        row("5: Eligibility criteria", "01_search_strategy/filters_and_constraints.txt"),
        row("6: Information sources", "01_search_strategy/search_metadata.json"),
        row("7: Search strategies", "01_search_strategy/search_strings_per_database.txt"),
        row("8: Selection process", "02_data/prisma_data.json"),
        row("16a: Manual flow evidence", "02_data/prisma_manual_evidence.json"),
        row("Record metadata", "02_data/initial_search_results.csv"),
        row("16a: Identification results", "02_data/initial_search_results.csv"),
        row("16a: Deduplicated records", "02_data/deduplicated_records.csv"),
        row("16a: Filtered records", "02_data/excluded_studies.csv"),
        row("16a: T&A decisions", "02_data/screening_decisions.csv"),
        row("16b: Full-text decisions", "02_data/full_text_screening.csv"),
        row("17: Record metadata", "02_data/included_studies.csv"),
        row("18/21: Risk of bias (template)", "02_data/risk_of_bias_template.csv"),
        row("22: Certainty/GRADE (template)", "02_data/grade_certainty_template.csv"),
        row("Flow diagram", "04_images/prisma_flow_diagram.svg"),
        row("24: Protocol details", "03_protocol/review_protocol.json"),
        row("24: Search protocol", "03_protocol/protocol.md"),
        row("Search configuration", "search_configuration.json"),
        "-" * 88,
        "",
        "--- SUMMARY STATISTICS ---",
        f"  Total identified:    {identification.get('total_found', 'N/A')}",
        f"  Retrieved:           {identification.get('retrieved', 'N/A')}",
        f"  Duplicates removed:  {screening.get('duplicates', 0)}",
        f"  After filters:       {included}",
        "",
    ]
    if full_text:
        lines += ["", "--- FULL-TEXT SCREENING ---",
                  f"  Candidates: {full_text['total']}; included: {full_text['included']}; excluded: {full_text['excluded']}; not retrieved: {full_text['notRetrieved']}; unsure: {full_text['unsure']}; pending: {full_text['pending']}.",
                  f"  Decisions no longer eligible: {full_text['inactive']}; ambiguous identities: {full_text['ambiguous']}.",
                  "  Verify reporting in the manuscript."]
        if full_text["total"] or full_text["inactive"]:
            lines.append("  See full_text_screening.csv for individual records and reasons.")
        if full_text["screened"] or full_text.get("dualReview"):
            lines.append("  Other-source counts are not assessed by this full-text workflow; review manual steps for double counting.")
            lines.append("  included_studies.csv lists full-text inclusions from T&A candidates, not unlinked other-source or manual-step records.")

    other_methods = identification.get("other_methods")
    if other_methods is not None:
        retrieved = _to_int(identification.get("retrieved"), 0)
        lines += [
            "--- IDENTIFICATION BY SOURCE (PRISMA 16a) ---",
            f"  Via databases/registers:   {max(0, retrieved - int(other_methods))}",
            f"  Via citation searching:    {other_methods}",
            "",
        ]

    lines += [
        "--- RECORDS EXCLUDED BY AUTOMATED FILTERS (PRISMA 16a) ---",
    ]

    reason_counts = [
        (_EXCLUSION_REASON_LABELS.get(k, k), int(v))
        for source in (screening, eligibility)
        for k, v in source.items()
        if isinstance(v, int) and v > 0
    ]
    if reason_counts:
        for label, count in reason_counts:
            lines.append(f"  {label:<38} {count}")
        lines.append(f"  {'-' * 38} {'-' * 5}")
        lines.append(f"  {'Total excluded before screening':<38} {sum(c for _, c in reason_counts)}")
    else:
        lines.append("  No records were excluded by automated filters.")

    if ta_screening:
        lines += [
            "",
            "--- TITLE & ABSTRACT SCREENING (PRISMA 16a) ---",
            f"  Records to screen:   {ta_screening.get('total', 0)}",
            f"  Screened:            {ta_screening.get('screened', 0)}",
            f"  Included:            {ta_screening.get('included', 0)}",
            f"  Excluded:            {ta_screening.get('excluded', 0)}",
            f"  Unsure:              {ta_screening.get('unsure', 0)}",
        ]
        remaining = int(ta_screening.get("total", 0)) - int(ta_screening.get("screened", 0))
        if remaining > 0:
            lines.append(f"  NOT YET SCREENED:    {remaining}")

    lines += ["", "--- DATABASE BREAKDOWN ---"]
    db_counts = identification.get("databases", {})
    for db, counts in db_counts.items():
        line = f"  {db:<20} found: {counts.get('found', 0):<8} retrieved: {counts.get('retrieved', 0)}"
        if counts.get("date"):
            line += f"  (searched {counts['date']})"
        lines.append(line)
        if counts.get("warning"):
            lines.append(f"  {'':<20} WARNING: {counts['warning']}")
        if counts.get("error"):
            lines.append(f"  {'':<20} ERROR: {counts['error']}")

    if any(v for k, v in review_protocol.items() if k != "updatedAt"):
        lines += ["", "--- REVIEW PROTOCOL (see 03_protocol/review_protocol.json) ---"]
        if review_protocol.get("registered"):
            reg = f"  Registration:        {review_protocol.get('registerName') or 'register not named'}"
            if review_protocol.get("registrationNumber"):
                reg += f", no. {review_protocol['registrationNumber']}"
            if review_protocol.get("registrationDate"):
                reg += f", {review_protocol['registrationDate']}"
            lines.append(reg)
        else:
            lines.append("  Registration:        protocol shows not registered; confirm status")
        if review_protocol.get("reviewers"):
            lines.append(f"  Reviewers:           {review_protocol['reviewers']} (confirm screening roles)")
        for label, field in (
            ("Synthesis method",   "synthesisMethod"),
            ("Risk-of-bias tool",  "robTool"),
            ("Effect measures",    "effectMeasures"),
            ("Reporting bias",     "reportingBiasMethod"),
            ("Certainty method",   "certaintyMethod"),
            ("Heterogeneity",      "heterogeneityMethod"),
            ("Sensitivity",        "sensitivityMethod"),
            ("Funding",            "funding"),
            ("Competing interests", "competingInterests"),
        ):
            if review_protocol.get(field):
                lines.append(f"  {label + ':':<20} {review_protocol[field]}")

    if recall:
        lines += [
            "",
            "--- VALIDATION (SEED) PAPER RECALL ---",            f"  Retrieved {len(recall['found'])} of {recall['total']} known papers.",
        ]
        for title, sources in recall["found"]:
            lines.append(f"    [OK] {title[:56]:<56} {', '.join(sources)[:20]}")
        for title, _ in recall["missing"]:
            lines.append(f"    [--] {title[:56]}")
        if recall["missing"]:
            lines.append("  Missing seeds suggest the query may be too narrow - review before reporting.")

    if ai_usage and (ai_usage.get("count") or ai_usage.get("agreement")):
        lines += [
            "",
            "--- AUTOMATION TOOLS USED IN SELECTION (PRISMA 8) ---",
            f"  AI-assisted decisions: {ai_usage.get('count', 0)} of {ai_usage.get('total', 0)}",
            f"  Model(s):              {', '.join(ai_usage.get('models') or ['unspecified'])}",
            "  Every AI suggestion was accepted or overridden by a human reviewer.",
        ]
        ag = ai_usage.get("agreement")
        if ag:
            lines.append(f"  Reviewer-AI agreement: {ag['agree']}/{ag['n']} ({ag['pct']}%), Cohen's kappa = {ag['kappa']:.2f}")

    lines += ["", "--- ITEMS STILL NEEDING MANUAL ATTENTION ---"]
    todo = []
    if not review_protocol.get("registered"):
        todo.append("24: confirm registration status - record the register and number, or state 'not registered'.")
    if review_protocol.get("reviewers"):
        todo.append("8: confirm how many reviewers screened each record and whether they worked independently.")
    else:
        todo.append("8: reviewer count and screening independence are not recorded; confirm them.")
    for label, field in (
        ("11: risk-of-bias assessment", "robTool"),
        ("13: synthesis method", "synthesisMethod"),
        ("13e: heterogeneity exploration", "heterogeneityMethod"),
        ("13f: sensitivity analyses", "sensitivityMethod"),
        ("12: effect measures", "effectMeasures"),
        ("14: reporting-bias assessment", "reportingBiasMethod"),
        ("15: certainty assessment", "certaintyMethod"),
        ("25: funding/support", "funding"),
        ("26: competing interests", "competingInterests"),
    ):
        if not review_protocol.get(field):
            todo.append(f"{label}: not recorded in the protocol.")
    if not has_screening:
        todo.append("16a: no title/abstract screening decisions were recorded in this package.")
    todo.append("16b: verify citations and reasons for studies excluded after full-text assessment.")
    todo.append("All items: verify reporting in the manuscript; artefact presence alone is not confirmation.")
    for db, counts in db_counts.items():
        if counts.get("error"):
            todo.append(f"16a: '{db}' returned an error - its records are missing or incomplete.")
        elif counts.get("warning"):
            todo.append(f"16a: '{db}' was truncated by an API limit - report as a search limitation.")
    for entry in todo:
        lines.append(f"  [ ] {entry}")

    lines.append("")
    lines.append("=" * 88)
    return "\n".join(lines)


# Load scheduled searches on startup.
# In Flask debug mode the werkzeug reloader runs the module in two processes:
#   - the parent monitor process (WERKZEUG_RUN_MAIN not set)
#   - the child server process  (WERKZEUG_RUN_MAIN == 'true')
# We only load schedules in the child (the one that actually handles requests)
# so jobs are not registered and fired twice.
# Without the reloader (production / debug off) WERKZEUG_RUN_MAIN is never
# set, so the guard must only apply when debug mode will spawn the reloader.
_flask_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
if not _flask_debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    load_all_schedules()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5001)
