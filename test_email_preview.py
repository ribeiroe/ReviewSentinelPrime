"""
Generates a preview of the notification email with 1 new and 1 removed paper.
Run with: python test_email_preview.py
Opens the resulting HTML in your default browser.
"""
import json, os, webbrowser
from datetime import datetime

# ── Load real results ────────────────────────────────────────────────────────
RESULTS_FILE = "data/results/12ed36d8-9be3-4b83-b33e-6088ec0400d6.json"
history = json.load(open(RESULTS_FILE, encoding="utf-8"))
real_docs = history[0]["docs"]          # current run docs

# ── Build simulated diff ─────────────────────────────────────────────────────
# "Removed" = take the last real paper and pretend it vanished
removed_docs = [real_docs[-1]]

# "New" = inject a fake paper at position 2 in current docs
fake_new = {
    "source": "arXiv",
    "title": "[SIMULATION] Agile Certification for Safety-Critical Avionics Under DO-178C Level A",
    "authors": ["Jane Test", "John Demo"],
    "year": 2026,
    "journal": "",
    "abstract": "This is a simulated paper injected to preview the NEW badge in the email.",
    "url": "https://arxiv.org/abs/0000.00000",
    "doi": "",
    "keywords": ["DO-178C", "Agile", "Simulation"],
    "docType": "Preprint",
}

# Current run: real docs (minus the last one) + fake new paper inserted at index 2
current_docs = real_docs[:-1]
current_docs.insert(2, fake_new)

# Key function (same as app.py)
def _doc_key(doc):
    doi = (doc.get("doi") or "").strip().lower()
    if doi:
        return doi
    return "".join(c for c in (doc.get("title") or "").lower() if c.isalnum())

prev_keys  = {_doc_key(d) for d in real_docs if _doc_key(d)}
curr_keys  = {_doc_key(d) for d in current_docs if _doc_key(d)}
new_doc_keys = curr_keys - prev_keys
is_first_run = False

# ── Replicate HTML build from app.py ─────────────────────────────────────────
search_name   = "Scrum4DO178C  [PREVIEW]"
num_found     = len(current_docs)
run_timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

sample = current_docs[:10]
rows_html = ""
for i, doc in enumerate(sample):
    title  = doc.get("title", "(no title)")
    year   = doc.get("year") or ""
    source = doc.get("source", "")
    url    = doc.get("url") or ""
    doi    = doc.get("doi") or ""
    link   = f"https://doi.org/{doi}" if doi else url
    is_new = _doc_key(doc) in new_doc_keys
    title_cell = f'<a href="{link}" style="color:#1a2a4a;text-decoration:none;">{title}</a>' if link else title
    new_badge  = ('&nbsp;<span style="background:#d4edda;color:#155724;font-size:0.78em;'
                  'padding:1px 5px;border-radius:3px;font-weight:600;">NEW</span>') if is_new else ""
    year_span  = f'&nbsp;&nbsp;<span style="color:#888;font-size:0.9em;">({year})</span>' if year else ""
    rows_html += (
        f'<tr{"  style=background:#f0fff4;" if is_new else ""}>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;">{i+1}.</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;">{title_cell}{year_span}{new_badge}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;color:#555;white-space:nowrap;">{source}</td>'
        f'</tr>'
    )

more_note = f'<p style="color:#888;">… and {num_found - len(sample)} more results.</p>' if num_found > len(sample) else ""

n_new     = len(new_doc_keys)
n_removed = len(removed_docs)
parts = []
if n_new:     parts.append(f'<span style="color:#155724;font-weight:600;">+{n_new} new</span>')
if n_removed: parts.append(f'<span style="color:#721c24;font-weight:600;">−{n_removed} removed</span>')
change_summary = f'<p style="margin:4px 0 12px 0;">Changes since last run: {", ".join(parts)}</p>' if parts else \
                 '<p style="margin:4px 0 12px 0;color:#555;">No changes since last run.</p>'

removed_rows = ""
for doc in removed_docs:
    title  = doc.get("title", "(no title)")
    year   = doc.get("year") or ""
    url    = doc.get("url") or ""
    doi    = doc.get("doi") or ""
    link   = f"https://doi.org/{doi}" if doi else url
    title_cell = f'<a href="{link}" style="color:#721c24;text-decoration:none;">{title}</a>' if link else title
    year_span  = f'&nbsp;&nbsp;<span style="color:#888;font-size:0.9em;">({year})</span>' if year else ""
    removed_rows += (
        f'<tr><td style="padding:5px 8px;border-bottom:1px solid #f5c6cb;">{title_cell}{year_span}</td>'
        f'<td style="padding:5px 8px;border-bottom:1px solid #f5c6cb;color:#721c24;white-space:nowrap;">'
        f'{doc.get("source","")}</td></tr>'
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

html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;color:#222;max-width:860px;margin:24px auto;padding:0 16px;">
<p style="background:#fffbe6;border:1px solid #ffe58f;padding:8px 12px;border-radius:4px;color:#7d5100;">
  ⚠️ This is a <strong>simulation preview</strong> — one fake NEW paper was injected and the last real paper was marked REMOVED.
</p>
<h2 style="color:#1a2a4a;">Review Sentinel Prime &mdash; Scheduled Run: {search_name}</h2>
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
</body></html>"""

out = "test_email_preview.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Preview saved → {os.path.abspath(out)}")
webbrowser.open(f"file:///{os.path.abspath(out)}")
