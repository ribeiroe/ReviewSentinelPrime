/**
 * Review Sentinel Prime – feedback attachment receiver (Google Apps Script).
 *
 * Deploy once as a Web app (Execute as: Me, Who has access: Anyone).
 * The app POSTs JSON { token, submissionId, type, title, files:[{name,mimeType,data(base64)}] }
 * and gets back { ok, folderUrl, links:[{name,url}] }.
 *
 * Files are stored privately in FOLDER_ID (only you and people you share the
 * folder with can open them). The token ships inside every release, so it is
 * an anti-abuse filter, not a real secret – the limits below are the real guard.
 */

// ── Configure these ───────────────────────────────────────────────────────────
const FOLDER_ID    = 'PASTE_DRIVE_FOLDER_ID_HERE';
const TOKEN        = 'PASTE_LONG_RANDOM_TOKEN_HERE';
const NOTIFY_EMAIL = '';            // e.g. 'you@example.com'; leave '' to disable
// ──────────────────────────────────────────────────────────────────────────────

const MAX_FILES       = 5;
const MAX_FILE_BYTES  = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 10 * 1024 * 1024;
const MAX_PER_DAY     = 100;        // submissions with attachments per day
const ALLOWED_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf',
                     'txt', 'log', 'csv', 'json', 'ris', 'bib'];

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.token !== TOKEN) return out_({ ok: false, error: 'unauthorized' });

    const files = Array.isArray(body.files) ? body.files : [];
    if (!files.length) return out_({ ok: false, error: 'no files' });
    if (files.length > MAX_FILES) return out_({ ok: false, error: 'too many files' });
    if (!underDailyLimit_()) return out_({ ok: false, error: 'daily limit reached' });

    const blobs = [];
    let total = 0;
    files.forEach(function (f) {
      const name = String(f.name || 'file').replace(/[\\\/:*?"<>|]/g, '_').slice(0, 120);
      const ext = (name.split('.').pop() || '').toLowerCase();
      if (ALLOWED_EXT.indexOf(ext) === -1) throw new Error('file type not allowed: ' + name);
      const bytes = Utilities.base64Decode(String(f.data || ''));
      if (bytes.length > MAX_FILE_BYTES) throw new Error('file too large: ' + name);
      total += bytes.length;
      if (total > MAX_TOTAL_BYTES) throw new Error('attachments too large');
      blobs.push(Utilities.newBlob(bytes, String(f.mimeType || 'application/octet-stream'), name));
    });

    const stamp = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd_HHmmss');
    const label = String(body.submissionId || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40);
    const folder = DriveApp.getFolderById(FOLDER_ID)
      .createFolder(stamp + (label ? '_' + label : ''));
    folder.setDescription(String(body.type || '') + ': ' + String(body.title || '').slice(0, 200));

    const links = blobs.map(function (b) {
      const file = folder.createFile(b);
      return { name: file.getName(), url: file.getUrl() };
    });

    if (NOTIFY_EMAIL) {
      MailApp.sendEmail({
        to: NOTIFY_EMAIL,
        subject: '[Review Sentinel Prime] Attachments: ' + String(body.title || '(no title)').slice(0, 150),
        body: 'Type: ' + (body.type || '') + '\nFolder: ' + folder.getUrl() + '\n\n' +
              links.map(function (l) { return l.name + ': ' + l.url; }).join('\n'),
      });
    }

    return out_({ ok: true, folderUrl: folder.getUrl(), links: links });
  } catch (err) {
    return out_({ ok: false, error: String(err && err.message || err) });
  }
}

// Simple GET so you can check the deployment URL in a browser.
function doGet() {
  return out_({ ok: true, service: 'rsp-feedback-uploads' });
}

function underDailyLimit_() {
  const props = PropertiesService.getScriptProperties();
  const key = 'count_' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const n = Number(props.getProperty(key) || 0);
    if (n >= MAX_PER_DAY) return false;
    props.setProperty(key, String(n + 1));
    return true;
  } finally {
    lock.releaseLock();
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
