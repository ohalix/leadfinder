/**
 * Shared utilities loaded on every page.
 * Templates may define their own page-specific functions inline.
 */

// ── Toast ─────────────────────────────────────────────────────────────────

let _toastTimer = null;

function showToast(message, duration = 1800) {
  // Remove existing toast
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  if (_toastTimer) clearTimeout(_toastTimer);

  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  document.body.appendChild(el);

  _toastTimer = setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .2s';
    setTimeout(() => el.remove(), 220);
  }, duration);
}

// ── Clipboard ─────────────────────────────────────────────────────────────

function copyText(text) {
  if (!navigator.clipboard) {
    // Fallback for older browsers
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity  = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast('Copied!');
    return;
  }
  navigator.clipboard.writeText(text).then(
    ()  => showToast('Copied!'),
    ()  => showToast('Copy failed')
  );
}

// ── Confidence badge HTML helper ──────────────────────────────────────────

function confidenceBadge(conf) {
  const map = {
    high:   '<span class="badge badge-high">[ HIGH ]</span>',
    medium: '<span class="badge badge-med">[ MED ]</span>',
    low:    '<span class="badge badge-low">[ LOW ]</span>',
  };
  const key = (conf || '').toLowerCase();
  return map[key] || `<span class="badge badge-low">[ ${(conf || '?').toUpperCase()} ]</span>`;
}

// ── HTML escape helpers ───────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return String(str)
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;');
}

// ── DOM helpers ───────────────────────────────────────────────────────────

function show(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
}

function hide(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}
