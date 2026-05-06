// =============================================================================
// main.js
// Parametric Optimization Driver
// Version: v1.1.4
// Role: Full-stack Developer
// Last modified: 2026-05-06
// Description: Frontend stepper navigation, drag-and-drop CSV upload, SSE
//              progress streaming, Plotly chart rendering, editable suggestions
//              table with live GP re-check, config save/load, toast notifications.
// =============================================================================

/**
 * Parametric Optimization Driver — Frontend Logic
 * Stepper navigation, drag-and-drop upload, SSE progress, Plotly charts,
 * editable suggestions table with live GP re-check, config save/load, toasts.
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
const STATE = {
  jobId: null,
  columns: [],
  numericColumns: [],
  stats: {},
  preview: [],
  nRows: 0,             // total rows in the uploaded CSV (for outlier mask length)
  currentStep: 1,
  maxUnlockedStep: 1,
  outlierMask: [],      // bool[] of length nRows, parallel to original CSV rows (true = include)
  outlierData: null,    // raw outlier detection result from server
  configDirty: false,
  lastResult: null,
  currentEventSource: null,
  notifHistory: [],
  lastPayload: null,
};

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─────────────────────────────────────────────────────────────────────────────
// Tooltip engine  (data-tip="plain text||optional formula")
// ─────────────────────────────────────────────────────────────────────────────
const _tipBox = document.createElement('div');
_tipBox.id = 'tooltip-box';
document.body.appendChild(_tipBox);

document.addEventListener('mouseover', e => {
  const target = e.target.closest('[data-tip]');
  if (!target) { _tipBox.style.display = 'none'; return; }
  const [plain, formula] = target.dataset.tip.split('||');
  _tipBox.innerHTML = `<div>${escHtml(plain)}</div>` +
    (formula ? `<div class="tip-formula">${escHtml(formula)}</div>` : '');
  _tipBox.style.display = 'block';
});
document.addEventListener('mousemove', e => {
  _tipBox.style.left = (e.clientX + 14) + 'px';
  _tipBox.style.top  = (e.clientY + 14) + 'px';
});
document.addEventListener('mouseout', e => {
  if (!e.target.closest('[data-tip]')) _tipBox.style.display = 'none';
});

// ─────────────────────────────────────────────────────────────────────────────
// DOM helpers
// ─────────────────────────────────────────────────────────────────────────────
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const el = (id)  => document.getElementById(id);

function show(id)  { el(id)?.classList.remove('hidden'); }
function hide(id)  { el(id)?.classList.add('hidden'); }

// ─────────────────────────────────────────────────────────────────────────────
// Toast notifications
// ─────────────────────────────────────────────────────────────────────────────
const ICONS = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' };

function toast(type, title, msg = '', persist = false) {
  const container = el('toast-container');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.innerHTML = `
    <span class="toast-icon">${ICONS[type] ?? 'ℹ'}</span>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      ${msg ? `<div class="toast-msg">${msg}</div>` : ''}
    </div>
    <button class="toast-close" aria-label="Dismiss">✕</button>`;
  container.appendChild(t);
  t.querySelector('.toast-close').onclick = () => t.remove();
  if (!persist) setTimeout(() => t.remove(), 4000);

  // Add to history
  const ts = new Date().toLocaleTimeString();
  STATE.notifHistory.unshift({ type, title, msg, ts });
  _renderNotifHistory();
}

function _renderNotifHistory() {
  const drawer = el('notif-drawer');
  drawer.innerHTML = STATE.notifHistory.slice(0, 20).map(n =>
    `<div class="notif-item">
      <strong>${n.title}</strong>${n.msg ? ': ' + n.msg : ''}
      <div class="notif-time">${n.ts}</div>
    </div>`
  ).join('') || '<div class="notif-item" style="color:var(--text-muted)">No notifications yet.</div>';
}

function _clearFieldErrors() {
  $$('.field-error[data-dynamic]').forEach(e => e.remove());
  $$('.has-error').forEach(e => e.classList.remove('has-error'));
}

function _renderFieldErrors(errors) {
  errors.forEach(err => {
    const target = err.el || (err.anchor ? $(err.anchor) : null);
    if (!target) return;
    target.classList.add('has-error');
    const span = document.createElement('span');
    span.className = 'field-error';
    span.dataset.dynamic = '1';
    span.textContent = err.label;
    target.insertAdjacentElement('afterend', span);
  });
}

function downloadErrorLog(evt, tb, cfg) {
  evt.preventDefault();
  const content = `=== TRACEBACK ===\n${tb}\n\n=== CONFIG SNAPSHOT ===\n${JSON.stringify(cfg, null, 2)}`;
  downloadBlob(content, 'text/plain', 'error_log.txt');
}

// ─────────────────────────────────────────────────────────────────────────────
// Theme toggle
// ─────────────────────────────────────────────────────────────────────────────
el('theme-toggle').onclick = () => {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? 'light' : 'dark');
  el('theme-toggle').textContent = isDark ? '🌙' : '☀️';
  // Swap Plotly chart templates if charts are rendered
  $$('.js-plotly-plot').forEach(div => {
    if (div._fullData) {
      Plotly.relayout(div, { template: isDark ? 'plotly' : 'plotly_white' });
    }
  });
};

// ─────────────────────────────────────────────────────────────────────────────
// Notification drawer toggle
// ─────────────────────────────────────────────────────────────────────────────
el('notif-btn').onclick = () => el('notif-drawer').classList.toggle('open');
document.addEventListener('click', e => {
  if (!el('notif-drawer').contains(e.target) && e.target !== el('notif-btn')) {
    el('notif-drawer').classList.remove('open');
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Row detail sidebar
// ─────────────────────────────────────────────────────────────────────────────
el('sidebar-close').onclick = () => el('row-sidebar').classList.remove('open');

function showRowSidebar(rowData) {
  const content = el('sidebar-content');
  content.innerHTML = Object.entries(rowData).map(([k, v]) =>
    `<div class="sidebar-row">
      <span class="sidebar-key">${k}</span>
      <span class="sidebar-val">${typeof v === 'number' ? v.toPrecision(6) : v}</span>
    </div>`
  ).join('');
  el('row-sidebar').classList.add('open');
}

// ─────────────────────────────────────────────────────────────────────────────
// Stepper navigation
// ─────────────────────────────────────────────────────────────────────────────
function goToStep(n) {
  if (n < 1 || n > 5) return;
  if (n > STATE.maxUnlockedStep) return;

  STATE.currentStep = n;
  $$('.step-panel').forEach((p, i) => p.classList.toggle('active', i + 1 === n));

  $$('.step[data-step]').forEach(s => {
    const sn = +s.dataset.step;
    s.className = 'step ' + (sn < n ? 'done' : sn === n ? 'active' : 'locked');
  });

  // Connectors
  for (let i = 1; i <= 4; i++) {
    const c = el(`connector-${i}-${i+1}`);
    if (c) c.classList.toggle('done', i < n);
  }
}

function unlockStep(n) {
  STATE.maxUnlockedStep = Math.max(STATE.maxUnlockedStep, n);
}

// Back buttons
el('back-to-upload-btn').onclick     = () => goToStep(1);
el('back-to-preprocess-btn').onclick = () => goToStep(2);
el('back-to-configure-btn').onclick  = () => goToStep(3);

// Clicking a done step navigates back
$$('.step[data-step]').forEach(s => {
  s.addEventListener('click', () => {
    const sn = +s.dataset.step;
    if (sn <= STATE.maxUnlockedStep) goToStep(sn);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// File upload — drag and drop + file input
// ─────────────────────────────────────────────────────────────────────────────
const dropZone = el('drop-zone');
const fileInput = el('file-input');

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragging'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragging');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

el('reupload-btn').onclick = () => {
  if (STATE.configDirty) {
    if (!confirm('Loading new data will reset your configuration.\n\nClick OK to continue without saving, or Cancel to save your config first.')) return;
  }
  resetToUpload();
};

el('new-session-btn').onclick = () => {
  if (STATE.configDirty) {
    if (!confirm('Loading new data will reset your configuration.\n\nClick OK to continue without saving, or Cancel to save your config first.')) return;
  }
  resetToUpload();
};

el('sample-btn').onclick = () => loadSampleData();

function resetToUpload() {
  STATE.jobId = null;
  STATE.configDirty = false;
  STATE.maxUnlockedStep = 1;
  hide('upload-result');
  show('empty-state');
  el('col-assignment-grid').innerHTML = '';
  el('constraint-list').innerHTML = '';
  fileInput.value = '';
  goToStep(1);
}

async function handleFile(file) {
  if (!file.name.endsWith('.csv')) {
    toast('error', 'Invalid file', 'Only CSV files are accepted.');
    return;
  }
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) { toast('error', 'Upload failed', data.error); return; }
    applyUploadResult(data, file.name);
  } catch (e) {
    toast('error', 'Upload error', e.message);
  }
}

function applyUploadResult(data, filename) {
  STATE.jobId = data.job_id;
  STATE.columns = data.columns;
  STATE.numericColumns = data.numeric_columns;
  STATE.stats = data.stats;
  STATE.preview = data.preview;
  STATE.nRows = data.n_rows;

  el('upload-filename').textContent = filename;
  el('upload-rowcol').textContent = `${data.n_rows} rows × ${data.columns.length} columns`;

  renderPreviewTable(data.preview, data.columns, data.stats);

  hide('empty-state');
  show('upload-result');
  unlockStep(2);
  toast('success', 'File loaded', `${data.n_rows} rows, ${data.columns.length} columns`);
}

function renderPreviewTable(rows, cols, stats) {
  const table = el('preview-table');
  const thead = `<thead><tr>${cols.map(c => `<th title="min:${stats[c]?.min} max:${stats[c]?.max}">${c}</th>`).join('')}</tr></thead>`;
  const tbody = '<tbody>' + rows.map(row =>
    '<tr>' + cols.map(c => {
      const v = row[c];
      const cls = (v === null || v === undefined || v !== v) ? ' class="nan-cell"' : '';
      return `<td${cls}>${v ?? 'NaN'}</td>`;
    }).join('') + '</tr>'
  ).join('') + '</tbody>';
  table.innerHTML = thead + tbody;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sample data
// ─────────────────────────────────────────────────────────────────────────────
function loadSampleData() {
  // Generate synthetic aerodynamics data: speed, pitch → thrust, power, Cm
  const rows = [];
  for (let i = 0; i < 20; i++) {
    const speed = 20 + Math.random() * 80;
    const pitch = -10 + Math.random() * 20;
    const thrust = Math.sin(pitch * Math.PI / 180) * speed ** 2 * 0.05 + Math.random() * 5;
    const power  = speed ** 3 * 0.01 + Math.random() * 10;
    const Cm     = Math.cos(pitch * Math.PI / 180) - 0.01 * speed + Math.random() * 0.05 - 0.025;
    rows.push({ speed: +speed.toFixed(2), pitch: +pitch.toFixed(2),
                thrust: +thrust.toFixed(2), power: +power.toFixed(2), Cm: +Cm.toFixed(4) });
  }
  const cols = ['speed', 'pitch', 'thrust', 'power', 'Cm'];
  const csvContent = [cols.join(','), ...rows.map(r => cols.map(c => r[c]).join(','))].join('\n');
  const file = new File([csvContent], 'sample_aero_data.csv', { type: 'text/csv' });
  handleFile(file);
}

// ─────────────────────────────────────────────────────────────────────────────
// Step 1 → 2: proceed to preprocess
// ─────────────────────────────────────────────────────────────────────────────
el('to-preprocess-btn').onclick = () => {
  runOutlierDetection();
  goToStep(2);
  unlockStep(3);
  buildColumnAssignment();
};

function runOutlierDetection() {
  // Client-side IQR outlier detection on the preview rows only (server runs the
  // authoritative IQR + Isolation Forest on all rows at run time). The outlier
  // mask covers ALL nRows so its length matches what the server expects.
  const cols = STATE.numericColumns;
  const preview = STATE.preview;
  const nPreview = preview.length;
  const nTotal = STATE.nRows;

  // Flagged array for preview rows only (indices 0..nPreview-1)
  const flaggedPreview = new Array(nPreview).fill(false);
  cols.forEach(col => {
    const vals = preview.map(r => r[col]).filter(v => v != null && !isNaN(v)).sort((a,b) => a-b);
    if (vals.length < 4) return;
    const q1 = vals[Math.floor(vals.length * 0.25)];
    const q3 = vals[Math.floor(vals.length * 0.75)];
    const iqr = q3 - q1;
    if (iqr === 0) return;
    const lo = q1 - 1.5 * iqr, hi = q3 + 1.5 * iqr;
    preview.forEach((r, i) => { if (r[col] < lo || r[col] > hi) flaggedPreview[i] = true; });
  });

  // Full mask: preview rows use IQR flags; remaining rows default to included.
  STATE.outlierMask = new Array(nTotal).fill(true);

  const nFlagged = flaggedPreview.filter(Boolean).length;
  const shown = nPreview < nTotal ? ` (first ${nPreview} of ${nTotal} shown)` : '';
  el('outlier-summary').textContent = `${nFlagged} row(s) flagged as potential outliers out of ${nPreview} shown${shown}`;

  renderOutlierChart(preview, cols, flaggedPreview);
}

function renderOutlierChart(rows, cols, flagged) {
  const dims = cols.map(col => ({
    label: col,
    values: rows.map(r => r[col] ?? 0),
    range: [Math.min(...rows.map(r=>r[col]??0)), Math.max(...rows.map(r=>r[col]??0))]
  }));
  const colorVals = flagged.map(f => f ? 1 : 0);
  const trace = {
    type: 'parcoords',
    line: { color: colorVals, colorscale: [[0,'#5b8ef7'],[1,'#f0b429']],
            showscale: true,
            colorbar: { title: 'Outlier', tickvals: [0,1], ticktext: ['Clean','Flagged'], thickness: 12, len: 0.6 }},
    dimensions: dims,
    labelfont: { color: '#9090b8', size: 11 },
    tickfont: { color: '#9090b8', size: 10 },
  };
  Plotly.react('outlier-chart', [trace], {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e4e4f0' }, margin: { l: 80, r: 60, t: 20, b: 20 }, height: 300
  }, {responsive: true});
}

el('include-all-btn').onclick = () => { STATE.outlierMask.fill(true); toast('info', 'All rows included'); };
el('exclude-flagged-btn').onclick = () => {
  // Re-run IQR on preview rows and exclude flagged. The mask covers all nRows
  // so non-preview rows remain included (their true outlier status is unknown here).
  const cols = STATE.numericColumns;
  const preview = STATE.preview;
  const flaggedPreview = new Array(preview.length).fill(false);
  cols.forEach(col => {
    const vals = preview.map(r => r[col]).filter(v => v != null && !isNaN(v)).sort((a,b) => a-b);
    if (vals.length < 4) return;
    const q1 = vals[Math.floor(vals.length*0.25)], q3 = vals[Math.floor(vals.length*0.75)];
    const iqr = q3-q1; if (!iqr) return;
    const lo = q1-1.5*iqr, hi = q3+1.5*iqr;
    preview.forEach((r,i) => { if (r[col]<lo||r[col]>hi) flaggedPreview[i]=true; });
  });
  // Build full-length mask: exclude flagged preview rows, keep all non-preview rows included.
  STATE.outlierMask = new Array(STATE.nRows).fill(true);
  flaggedPreview.forEach((f, i) => { if (f) STATE.outlierMask[i] = false; });
  const nExcluded = flaggedPreview.filter(Boolean).length;
  toast('info', `${nExcluded} outlier row(s) excluded`);
};

el('export-outliers-btn').onclick = () => {
  const cols = STATE.columns;
  const rows = STATE.preview.filter((_, i) => !STATE.outlierMask[i]);
  if (!rows.length) { toast('warning', 'No outlier rows to export'); return; }
  const csv = [cols.join(','), ...rows.map(r => cols.map(c => r[c]??'').join(','))].join('\n');
  downloadBlob(csv, 'text/csv', 'outlier_rows.csv');
};

// ─────────────────────────────────────────────────────────────────────────────
// Step 2 → 3: configure
// ─────────────────────────────────────────────────────────────────────────────
el('to-configure-btn').onclick = () => { goToStep(3); populateObjectiveSelects(); };

function buildColumnAssignment() {
  const grid = el('col-assignment-grid');
  grid.innerHTML = '';
  STATE.numericColumns.forEach(col => {
    const s = STATE.stats[col] || {};
    const card = document.createElement('div');
    card.className = 'col-card';
    card.dataset.col = col;
    card.innerHTML = `
      <div class="col-name">${col}</div>
      <div class="col-stats">min ${s.min?.toFixed(3)??'–'} · max ${s.max?.toFixed(3)??'–'} · ${s.nan_count??0} NaN</div>
      <div class="form-row" style="margin:6px 0 0">
        <select class="col-role" style="flex:1"
          data-tip="Input: the optimizer suggests values within bounds. Output: the GP surrogate learns to predict it. Ignore: excluded from the model.">
          <option value="input">Input</option>
          <option value="output">Output</option>
          <option value="ignore">Ignore</option>
        </select>
        <label style="display:flex;align-items:center;gap:4px;font-size:12px;cursor:pointer"
          data-tip="Mark this input as integer — suggested values will be rounded to the nearest whole number.">
          <input type="checkbox" class="col-integer"> Integer
        </label>
      </div>
      <div class="bounds-row col-bounds">
        <label data-tip="The optimizer will never suggest values outside these bounds. Pre-filled from your data range — widen them if you want to explore beyond your current data.">Min</label>
        <input type="number" class="col-min" value="${s.min?.toFixed(4)??0}" step="any">
        <label data-tip="The optimizer will never suggest values outside these bounds. Pre-filled from your data range — widen them if you want to explore beyond your current data.">Max</label>
        <input type="number" class="col-max" value="${s.max?.toFixed(4)??1}" step="any">
      </div>`;
    // Show/hide bounds based on role
    const roleSelect = card.querySelector('.col-role');
    const boundsRow  = card.querySelector('.col-bounds');
    const intCheck   = card.querySelector('.col-integer');
    roleSelect.addEventListener('change', () => {
      const isInput = roleSelect.value === 'input';
      boundsRow.style.display = isInput ? 'flex' : 'none';
      intCheck.parentElement.style.display = isInput ? 'flex' : 'none';
      STATE.configDirty = true;
      validateConfig();
    });
    card.addEventListener('change', () => { STATE.configDirty = true; validateConfig(); });

    const minInput = card.querySelector('.col-min');
    const maxInput = card.querySelector('.col-max');
    [minInput, maxInput].forEach(inp => {
      inp.addEventListener('input', () => _validateBoundsRow(col, card));
      inp.addEventListener('blur',  () => _validateBoundsRow(col, card));
    });

    grid.appendChild(card);
  });
}

function _validateBoundsRow(col, card) {
  const maxInput = card.querySelector('.col-max');
  const minV = parseFloat(card.querySelector('.col-min').value);
  const maxV = parseFloat(maxInput.value);
  validateConfig();  // updates banner and clears all dynamic errors
  if (!isNaN(minV) && !isNaN(maxV) && minV >= maxV) {
    maxInput.classList.add('has-error');
    const span = document.createElement('span');
    span.className = 'field-error';
    span.dataset.dynamic = '1';
    span.textContent = 'Min must be less than max';
    maxInput.insertAdjacentElement('afterend', span);
  }
}

function getColumnConfig() {
  const inputs = [], outputs = [], bounds = {}, integerCols = [];
  $$('.col-card').forEach(card => {
    const col  = card.dataset.col;
    const role = card.querySelector('.col-role').value;
    if (role === 'input') {
      inputs.push(col);
      const minV = parseFloat(card.querySelector('.col-min').value);
      const maxV = parseFloat(card.querySelector('.col-max').value);
      bounds[col] = { min: minV, max: maxV };
      if (card.querySelector('.col-integer').checked) integerCols.push(col);
    } else if (role === 'output') {
      outputs.push(col);
    }
  });
  return { inputs, outputs, bounds, integerCols };
}

// ─────────────────────────────────────────────────────────────────────────────
// Mode selector
// ─────────────────────────────────────────────────────────────────────────────
$$('.mode-tab').forEach(btn => {
  btn.onclick = () => {
    $$('.mode-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const isOpt = btn.dataset.mode === 'optimization';
    el('opt-panel').classList.toggle('hidden', !isOpt);
    el('mode-description').textContent = isOpt
      ? 'Finds input conditions that optimize the objective while satisfying all constraints. Uses Constrained Expected Improvement.'
      : 'Fills gaps in the design space by targeting regions of highest GP prediction uncertainty. Best for building a reliable global surrogate first.';
    STATE.configDirty = true;
    validateConfig();
  };
});

// ─────────────────────────────────────────────────────────────────────────────
// Objective builder
// ─────────────────────────────────────────────────────────────────────────────
function populateObjectiveSelects() {
  const { inputs, outputs } = getColumnConfig();
  const allCols = [...inputs, ...outputs];

  const objSelect = el('obj-column');
  objSelect.innerHTML = allCols.map(c => `<option value="${c}">${c}</option>`).join('');

  // Weight rows
  const weightRows = el('weight-rows');
  weightRows.innerHTML = allCols.map(c => `
    <div class="weight-row">
      <span style="min-width:100px;font-family:var(--font-mono);font-size:12px">${c}</span>
      <input type="number" class="weight-input" data-col="${c}" value="0" step="0.1" style="width:80px">
    </div>`).join('');
}

el('toggle-weighted-btn').onclick = () => {
  el('objective-weighted').classList.add('visible');
  el('objective-simple').style.display = 'none';
  populateObjectiveSelects();
};
el('toggle-simple-btn').onclick = () => {
  el('objective-weighted').classList.remove('visible');
  el('objective-simple').style.display = 'flex';
};

function getObjectiveSpec() {
  const isWeighted = el('objective-weighted').classList.contains('visible');
  if (isWeighted) {
    const weights = {};
    $$('.weight-input').forEach(inp => {
      const w = parseFloat(inp.value) || 0;
      if (w !== 0) weights[inp.dataset.col] = w;
    });
    return { type: 'weighted', weights };
  }
  return {
    type: 'single',
    column: el('obj-column').value,
    direction: el('obj-direction').value,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Constraint builder
// ─────────────────────────────────────────────────────────────────────────────
el('add-constraint-btn').onclick = () => addConstraintRow();

function addConstraintRow(cfg = null) {
  const { outputs } = getColumnConfig();
  const id = Date.now();
  const row = document.createElement('div');
  row.className = 'constraint-row';
  row.dataset.id = id;

  row.innerHTML = `
    <button class="remove-btn" title="Remove">✕</button>
    <div class="form-group" style="min-width:130px">
      <label>Output column</label>
      <select class="c-col">${outputs.map(c=>`<option>${c}</option>`).join('')}</select>
    </div>
    <div class="form-group" style="min-width:100px">
      <label>Type</label>
      <select class="c-type">
        <option value="leq">≤ (max limit)</option>
        <option value="geq">≥ (min limit)</option>
        <option value="eq">= target ± tol</option>
      </select>
    </div>
    <div class="c-eq-fields form-group hidden" style="min-width:100px">
      <label>Target</label>
      <input type="number" class="c-target" value="0" step="any">
    </div>
    <div class="c-eq-fields form-group hidden" style="min-width:80px">
      <label>Tolerance</label>
      <input type="number" class="c-tol" value="0.001" step="any">
    </div>
    <div class="form-group" style="min-width:110px">
      <label>Limit type</label>
      <select class="c-ltype">
        <option value="constant">Constant</option>
        <option value="expression">Expression</option>
        <option value="table">Table CSV</option>
      </select>
    </div>
    <div class="c-limit-constant form-group" style="min-width:100px">
      <label>Limit value</label>
      <input type="number" class="c-limit-val" value="0" step="any">
    </div>
    <div class="c-limit-expression form-group hidden" style="min-width:200px">
      <label>Expression (numpy + input vars)</label>
      <input type="text" class="c-expr" placeholder="e.g. 0.5 * speed**2">
    </div>
    <div class="c-limit-table hidden" style="min-width:180px">
      <label style="font-size:12px;color:var(--text-secondary)">Lookup table CSV</label>
      <input type="file" class="c-table-file" accept=".csv" style="font-size:12px;margin-top:4px">
      <input type="hidden" class="c-table-id">
      <div class="c-table-cols hidden" style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap"></div>
    </div>`;

  // Toggle eq fields
  row.querySelector('.c-type').addEventListener('change', function() {
    const isEq = this.value === 'eq';
    row.querySelectorAll('.c-eq-fields').forEach(f => f.classList.toggle('hidden', !isEq));
    row.querySelector('.c-limit-constant').classList.toggle('hidden', isEq);
    row.querySelector('.c-limit-expression').classList.toggle('hidden', true);
    row.querySelector('.c-limit-table').classList.toggle('hidden', true);
    if (!isEq) row.querySelector('.c-ltype').dispatchEvent(new Event('change'));
    STATE.configDirty = true;
  });

  // Toggle limit type fields
  row.querySelector('.c-ltype').addEventListener('change', function() {
    const v = this.value;
    row.querySelector('.c-limit-constant').classList.toggle('hidden', v !== 'constant');
    row.querySelector('.c-limit-expression').classList.toggle('hidden', v !== 'expression');
    row.querySelector('.c-limit-table').classList.toggle('hidden', v !== 'table');
    STATE.configDirty = true;
  });

  // Table file upload
  row.querySelector('.c-table-file').addEventListener('change', async function() {
    const file = this.files[0];
    if (!file) return;
    const fd = new FormData(); fd.append('file', file);
    const res = await fetch('/upload_constraint_table', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) { toast('error', 'Table upload failed', data.error); return; }
    row.querySelector('.c-table-id').value = data.table_id;
    const colDiv = row.querySelector('.c-table-cols');
    colDiv.classList.remove('hidden');
    colDiv.innerHTML = `
      <div class="form-group"><label>Condition cols (comma-sep)</label>
        <input type="text" class="c-table-cond-cols" placeholder="${data.columns.slice(0,-1).join(',')}" style="font-size:12px"></div>
      <div class="form-group"><label>Limit column</label>
        <select class="c-table-limit-col">${data.columns.map(c=>`<option>${c}</option>`).join('')}</select></div>`;
    toast('success', 'Table uploaded', `${data.n_rows} rows, cols: ${data.columns.join(', ')}`);
  });

  row.querySelector('.remove-btn').onclick = () => { row.remove(); validateConfig(); };
  row.addEventListener('change', () => { STATE.configDirty = true; validateConfig(); });

  if (cfg) restoreConstraintRow(row, cfg);
  el('constraint-list').appendChild(row);
  validateConfig();
}

function restoreConstraintRow(row, cfg) {
  if (cfg.col) row.querySelector('.c-col').value = cfg.col;
  if (cfg.type) {
    row.querySelector('.c-type').value = cfg.type;
    row.querySelector('.c-type').dispatchEvent(new Event('change'));
  }
  if (cfg.target !== undefined) row.querySelector('.c-target').value = cfg.target;
  if (cfg.tolerance !== undefined) row.querySelector('.c-tol').value = cfg.tolerance;
  if (cfg.limit_type) {
    row.querySelector('.c-ltype').value = cfg.limit_type;
    row.querySelector('.c-ltype').dispatchEvent(new Event('change'));
  }
  if (cfg.limit_value !== undefined && cfg.limit_type === 'constant') row.querySelector('.c-limit-val').value = cfg.limit_value;
  if (cfg.limit_value !== undefined && cfg.limit_type === 'expression') row.querySelector('.c-expr').value = cfg.limit_value;
}

function getConstraints() {
  return $$('.constraint-row').map(row => {
    const ltype = row.querySelector('.c-ltype').value;
    let limit_value = null;
    if (ltype === 'constant') limit_value = parseFloat(row.querySelector('.c-limit-val').value);
    else if (ltype === 'expression') limit_value = row.querySelector('.c-expr').value.trim();
    else if (ltype === 'table') limit_value = row.querySelector('.c-table-id').value;
    return {
      col: row.querySelector('.c-col').value,
      type: row.querySelector('.c-type').value,
      target: parseFloat(row.querySelector('.c-target')?.value ?? 0),
      tolerance: parseFloat(row.querySelector('.c-tol')?.value ?? 0.001),
      limit_type: ltype,
      limit_value,
      table_condition_cols: (row.querySelector('.c-table-cond-cols')?.value ?? '').split(',').map(s=>s.trim()).filter(Boolean),
      table_limit_col: row.querySelector('.c-table-limit-col')?.value ?? '',
    };
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Input constraints
// ─────────────────────────────────────────────────────────────────────────────
el('add-input-constraint-btn').onclick = () => {
  const list = el('input-constraint-list');
  const row = document.createElement('div');
  row.className = 'form-row';
  row.style.marginBottom = '8px';
  row.innerHTML = `
    <input type="text" class="input-constraint-expr full-width" placeholder="e.g. chord * twist <= 15.0" style="flex:1">
    <button class="btn btn-ghost">✕</button>`;
  row.querySelector('button').onclick = () => row.remove();
  list.appendChild(row);
};

function getInputConstraints() {
  return $$('.input-constraint-expr').map(i => i.value.trim()).filter(Boolean);
}

// ─────────────────────────────────────────────────────────────────────────────
// Validation
// ─────────────────────────────────────────────────────────────────────────────
function validateConfig() {
  _clearFieldErrors();
  const errors = [];
  const { inputs, outputs, bounds, integerCols } = getColumnConfig();
  const mode = $('.mode-tab.active')?.dataset.mode || 'refinement';

  if (inputs.length === 0)  errors.push({ label: 'No input columns assigned',  anchor: '#col-assignment-grid' });
  if (outputs.length === 0) errors.push({ label: "No output columns assigned — set at least one column's role to 'Output'", anchor: '#col-assignment-grid' });

  inputs.forEach(col => {
    const b = bounds[col];
    const card = $(`.col-card[data-col="${CSS.escape(col)}"]`);
    if (b && b.min >= b.max) {
      errors.push({ label: `"${col}": min must be less than max`, el: card?.querySelector('.col-max') });
    }
    if (integerCols.includes(col) && b && (!Number.isInteger(b.min) || !Number.isInteger(b.max))) {
      errors.push({ label: `"${col}": integer bounds must be whole numbers`, el: card?.querySelector('.col-min') });
    }
  });

  const batchVal = parseInt(el('batch-size').value);
  if (isNaN(batchVal) || batchVal < 1 || batchVal > 50) {
    el('err-batch-size').classList.add('visible');
    errors.push({ label: 'Batch size must be between 1 and 50', anchor: '#batch-size' });
  } else {
    el('err-batch-size').classList.remove('visible');
  }

  if (mode === 'optimization') {
    const objCol = el('obj-column')?.value;
    if (!objCol) errors.push({ label: 'Objective: no column selected', anchor: '#obj-column' });

    $$('.constraint-row').forEach((row, i) => {
      const colSel = row.querySelector('.c-col');
      if (!colSel?.value) {
        errors.push({ label: `Constraint ${i + 1}: no output column selected`, el: colSel });
      }
      const ctype = row.querySelector('.c-type')?.value;
      const ltype = row.querySelector('.c-ltype')?.value;
      const colName = colSel?.value || `#${i + 1}`;
      if (ctype !== 'eq' && ltype === 'constant') {
        const lvalEl = row.querySelector('.c-limit-val');
        if (!lvalEl?.value || isNaN(parseFloat(lvalEl.value))) {
          errors.push({ label: `Constraint "${colName}": limit value required`, el: lvalEl });
        }
      }
      if (ctype === 'eq') {
        const targetEl = row.querySelector('.c-target');
        if (!targetEl?.value || isNaN(parseFloat(targetEl.value))) {
          errors.push({ label: `Constraint "${colName}": target value required`, el: targetEl });
        }
      }
    });
  }

  const errorBanner = el('config-error-banner');
  const runBtn = el('run-btn');
  if (errors.length > 0) {
    errorBanner.classList.add('visible');
    const listItems = errors.map(e => `<li>${escHtml(e.label)}</li>`).join('');
    errorBanner.innerHTML = `
      <div class="banner-error-header">
        <span>⚠ ${errors.length} configuration error${errors.length > 1 ? 's' : ''} — fix before running</span>
        <button class="error-toggle-btn" id="config-error-toggle">▼ Show</button>
      </div>
      <ul class="config-error-list hidden" id="config-error-list">${listItems}</ul>`;
    el('config-error-toggle').onclick = function() {
      const list = el('config-error-list');
      const nowHidden = list.classList.toggle('hidden');
      this.textContent = nowHidden ? '▼ Show' : '▲ Hide';
    };
    runBtn.disabled = true;
  } else {
    errorBanner.classList.remove('visible');
    errorBanner.innerHTML = '';
    runBtn.disabled = !STATE.jobId;
  }
  return errors;
}

// ─────────────────────────────────────────────────────────────────────────────
// Config save / load
// ─────────────────────────────────────────────────────────────────────────────
el('save-config-btn').onclick = () => {
  const cfg = buildRunPayload();
  downloadBlob(JSON.stringify(cfg, null, 2), 'application/json', 'opt_config.json');
  STATE.configDirty = false;
  toast('success', 'Config saved');
};

el('load-config-input').addEventListener('change', async function() {
  const file = this.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const cfg = JSON.parse(text);
    applyConfig(cfg);
    STATE.configDirty = false;
    toast('success', 'Config loaded');
  } catch(e) {
    toast('error', 'Config load failed', e.message);
  }
});

function applyConfig(cfg) {
  // Apply column roles
  if (cfg.input_cols && cfg.output_cols) {
    $$('.col-card').forEach(card => {
      const col = card.dataset.col;
      const sel = card.querySelector('.col-role');
      if (cfg.input_cols.includes(col)) sel.value = 'input';
      else if (cfg.output_cols.includes(col)) sel.value = 'output';
      else sel.value = 'ignore';
      sel.dispatchEvent(new Event('change'));
    });
  }
  // Bounds
  if (cfg.bounds) {
    $$('.col-card').forEach(card => {
      const col = card.dataset.col;
      if (cfg.bounds[col]) {
        card.querySelector('.col-min').value = cfg.bounds[col].min;
        card.querySelector('.col-max').value = cfg.bounds[col].max;
      }
    });
  }
  // Integer cols
  if (cfg.integer_cols) {
    $$('.col-card').forEach(card => {
      card.querySelector('.col-integer').checked = cfg.integer_cols.includes(card.dataset.col);
    });
  }
  // Mode
  if (cfg.mode) {
    const modeBtn = $$(`.mode-tab[data-mode="${cfg.mode}"]`)[0];
    if (modeBtn) modeBtn.click();
  }
  // Batch size
  if (cfg.n_suggestions) el('batch-size').value = cfg.n_suggestions;
  // Constraints
  el('constraint-list').innerHTML = '';
  (cfg.constraints || []).forEach(c => addConstraintRow(c));
  // GP settings
  if (cfg.gp_settings) {
    const gp = cfg.gp_settings;
    if (gp.kernel) el('gp-kernel').value = gp.kernel;
    if (gp.n_restarts) el('gp-restarts').value = gp.n_restarts;
    if (gp.length_scale_type) el('gp-lengthscale').value = gp.length_scale_type;
  }
  if (cfg.dup_threshold) el('dup-threshold').value = cfg.dup_threshold;
  if (cfg.convergence_threshold) el('convergence-threshold').value = cfg.convergence_threshold;
  populateObjectiveSelects();
  validateConfig();
}

// ─────────────────────────────────────────────────────────────────────────────
// Build run payload
// ─────────────────────────────────────────────────────────────────────────────
function buildRunPayload() {
  const { inputs, outputs, bounds, integerCols } = getColumnConfig();
  const mode = $('.mode-tab.active').dataset.mode;
  const objSpec = getObjectiveSpec();
  const constraints = getConstraints();
  const inputConstraints = getInputConstraints();

  const payload = {
    job_id: STATE.jobId,
    mode,
    input_cols: inputs,
    output_cols: outputs,
    bounds,
    integer_cols: integerCols,
    n_suggestions: parseInt(el('batch-size').value),
    objective_spec: objSpec,
    constraints,
    input_constraints: inputConstraints,
    outlier_include_mask: STATE.outlierMask,
    gp_settings: {
      kernel: el('gp-kernel').value,
      n_restarts: parseInt(el('gp-restarts').value),
      length_scale_type: el('gp-lengthscale').value,
    },
    dup_threshold: parseFloat(el('dup-threshold').value),
    convergence_threshold: parseFloat(el('convergence-threshold').value),
  };
  STATE.lastPayload = payload;
  return payload;
}

// ─────────────────────────────────────────────────────────────────────────────
// Run
// ─────────────────────────────────────────────────────────────────────────────
el('run-btn').onclick = async () => {
  const errors = validateConfig();
  if (errors.length > 0) {
    _renderFieldErrors(errors);
    const first = errors.find(e => e.el || e.anchor);
    const target = first?.el || (first?.anchor ? $(first.anchor) : null);
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  const payload = buildRunPayload();
  const runBtn = el('run-btn');
  runBtn.disabled = true;

  unlockStep(4);
  goToStep(4);
  unlockStep(5);
  el('progress-bar-fill').style.width = '5%';
  el('progress-label').textContent = 'Starting…';

  try {
    const res = await fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      toast('error', 'Run failed', data.error, true);
      runBtn.disabled = false;
      return;
    }
    startSSE(data.job_id);
  } catch(e) {
    toast('error', 'Run error', e.message, true);
    runBtn.disabled = false;
  }
};

el('cancel-run-btn').onclick = () => {
  STATE.currentEventSource?.close();
  el('run-btn').disabled = false;
  goToStep(3);
  toast('warning', 'Run cancelled');
};

function startSSE(jobId) {
  STATE.currentEventSource?.close();
  let closed = false;
  const es = new EventSource(`/stream/${jobId}`);
  STATE.currentEventSource = es;

  const STEP_LABELS = {
    1: 'Cleaning data…',
    2: 'Fitting surrogate…',
    3: 'Computing sensitivity…',
    4: 'Running acquisition…',
    5: 'Building charts…',
    6: 'Done.',
  };

  es.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);

    if (msg.type === 'progress') {
      const pct = Math.round((msg.step / msg.total) * 90) + 5;
      el('progress-bar-fill').style.width = pct + '%';
      el('progress-label').textContent = msg.message || STEP_LABELS[msg.step] || '';
    }

    if (msg.type === 'result') {
      el('progress-bar-fill').style.width = '100%';
      el('progress-label').textContent = 'Complete!';
      closed = true; es.close();
      STATE.lastResult = msg.data;
      setTimeout(() => { goToStep(5); renderResults(msg.data); }, 600);
    }

    if (msg.type === 'error') {
      closed = true; es.close();
      el('run-btn').disabled = false;
      const errMsg  = msg.message  || 'Unknown error';
      const frames  = msg.last_frames || [];
      const fullTb  = msg.traceback  || errMsg;
      const cfgSnap = msg.config_snapshot || STATE.lastPayload || {};

      const frameHtml = frames.map(f => `<code>${escHtml(f)}</code>`).join('<br>');
      const tbEncoded = encodeURIComponent(fullTb);
      const cfgEncoded = encodeURIComponent(JSON.stringify(cfgSnap, null, 2));
      const detailsHtml = `
        <div class="error-toggle-row">
          <button class="error-toggle-btn" onclick="
            var d=this.nextElementSibling;
            var h=d.classList.toggle('hidden');
            this.textContent=h?'▼ Show details':'▲ Hide details';">
            ▼ Show details
          </button>
          <a class="error-download-link" href="#"
             onclick="downloadErrorLog(event,decodeURIComponent('${tbEncoded}'),JSON.parse(decodeURIComponent('${cfgEncoded}')));return false;">
            Download log
          </a>
        </div>
        <div class="error-details hidden">${frameHtml}${frameHtml ? '<hr style=\'border-color:var(--border);margin:6px 0\'>' : ''}<pre style='margin:0'>${escHtml(fullTb)}</pre></div>`;
      toast('error', 'Pipeline error', escHtml(errMsg) + detailsHtml, true);
      goToStep(3);
    }
  };

  es.onerror = () => {
    if (closed) return;
    closed = true; es.close();
    el('run-btn').disabled = false;
    toast('error', 'Connection lost', 'The SSE stream disconnected.', true);
    goToStep(3);
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Results rendering
// ─────────────────────────────────────────────────────────────────────────────
function renderResults(result) {
  renderBanners(result);
  renderDiagnostics(result.diagnostics || {});
  renderCharts(result.plots || {}, result.unc_axes || {});
  renderSuggestionsTable(result.suggestions || [], result);
  toast('success', 'Results ready', `${result.suggestions?.length ?? 0} cases suggested`);
}

function renderBanners(result) {
  const container = el('result-banners');
  container.innerHTML = '';

  if (result.feasibility_mode) {
    container.innerHTML += `<div class="banner banner-warning">
      ⚠ No feasible point found — switched to feasibility search mode.
      Suggestions target regions most likely to satisfy constraints.
    </div>`;
    toast('warning', 'No feasible point', 'Optimizer switched to feasibility search mode.');
  }

  if (result.convergence?.converged) {
    container.innerHTML += `<div class="banner banner-success">
      ✓ Convergence likely — Max CEI (${result.convergence.max_cei.toExponential(2)})
      is below threshold (${result.convergence.threshold}).
    </div>`;
    toast('info', 'Convergence detected', `Max CEI = ${result.convergence.max_cei.toExponential(2)}`);
  }

  if (result.nan_info?.rows_with_nan > 0) {
    container.innerHTML += `<div class="banner banner-info">
      ℹ ${result.nan_info.rows_with_nan} row(s) with missing output values were excluded from fitting.
    </div>`;
  }
}

function renderDiagnostics(diag) {
  const tbody = el('diag-table-body');
  tbody.innerHTML = Object.entries(diag).map(([col, d]) => {
    const r2 = d.r2 ?? 0;
    let badge;
    if (r2 >= 0.95)      badge = '<span class="badge badge-good">✓ Good</span>';
    else if (r2 >= 0.80) badge = '<span class="badge badge-fair">⚠ Fair</span>';
    else                 badge = '<span class="badge badge-poor">✗ Poor</span>';
    return `<tr>
      <td class="text-mono">${col}</td>
      <td>${r2.toFixed(4)}</td>
      <td>${(d.rmse ?? 0).toFixed(5)}</td>
      <td>${badge}</td>
      <td class="text-muted">${d.kernel ?? '–'}</td>
    </tr>`;
  }).join('');
}

function renderCharts(plots, uncAxes) {
  const theme = document.documentElement.getAttribute('data-theme') === 'light';
  const bgColor = 'rgba(0,0,0,0)';
  const fontColor = theme ? '#1a1a2e' : '#e4e4f0';
  const gridColor = theme ? '#d0d4e8' : '#2e2e52';

  function applyTheme(layout) {
    return { ...layout, paper_bgcolor: bgColor, plot_bgcolor: bgColor,
             font: { color: fontColor }, xaxis: { ...layout.xaxis, gridcolor: gridColor },
             yaxis: { ...layout.yaxis, gridcolor: gridColor } };
  }

  const plotCfg = {responsive: true};

  if (plots.sensitivity) {
    Plotly.react('chart-sensitivity-plot', plots.sensitivity.data,
      applyTheme(plots.sensitivity.layout), plotCfg);
  }

  if (plots.convergence) {
    Plotly.react('chart-convergence-plot', plots.convergence.data,
      applyTheme(plots.convergence.layout), plotCfg);
  } else {
    el('chart-convergence').style.display = 'none';
  }

  if (plots.scatter_matrix) {
    Plotly.react('chart-scatter-plot', plots.scatter_matrix.data,
      applyTheme(plots.scatter_matrix.layout), plotCfg);
    // Click handler for row sidebar
    el('chart-scatter-plot').on('plotly_click', data => {
      const pt = data.points[0];
      if (pt && STATE.lastResult) showRowSidebar({ x: pt.x, y: pt.y, index: pt.pointIndex });
    });
  }

  if (plots.uncertainty_map) {
    Plotly.react('chart-uncertainty-plot', plots.uncertainty_map.data,
      applyTheme(plots.uncertainty_map.layout), plotCfg);
    el('chart-uncertainty-plot').on('plotly_click', data => {
      const pt = data.points[0];
      if (pt) showRowSidebar({ [uncAxes.x]: pt.x, [uncAxes.y]: pt.y });
    });
  }

  // Populate axis selectors for uncertainty map
  const inputCols = buildRunPayload().input_cols;
  [el('unc-xaxis'), el('unc-yaxis')].forEach((sel, idx) => {
    sel.innerHTML = inputCols.map(c => `<option value="${c}">${c}</option>`).join('');
    if (inputCols[idx]) sel.value = inputCols[idx];
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Suggestions table (editable, live GP re-check)
// ─────────────────────────────────────────────────────────────────────────────
function renderSuggestionsTable(suggestions, result) {
  if (!suggestions.length) return;
  const table = el('suggestions-table');
  const cols = Object.keys(suggestions[0]);
  const inputCols = buildRunPayload().input_cols;

  const thead = `<thead><tr>${cols.map(c => {
    const isInput = inputCols.includes(c);
    const cls = isInput ? '' : ' class="pred-col"';
    return `<th${cls}>${c}</th>`;
  }).join('')}</tr></thead>`;

  const tbody = '<tbody>' + suggestions.map((row, ri) => {
    return '<tr data-row="' + ri + '">' + cols.map(col => {
      const isInput = inputCols.includes(col);
      const v = row[col];
      const display = typeof v === 'number' ? v.toPrecision(6) : (v ?? '');
      if (isInput) {
        // Extrapolation: value outside the training data min/max for this input column.
        const s = STATE.stats[col];
        const isExtrap = s && typeof v === 'number' && (v < s.min || v > s.max);
        const cls = isExtrap ? ' class="extrap-cell"' : '';
        return `<td contenteditable="true"${cls} data-col="${col}" data-row="${ri}">${display}</td>`;
      }
      const isViolation = col.startsWith('p_feasible_') && typeof v === 'number' && v < 0.5;
      return `<td class="${isViolation ? 'violation' : 'pred-col'}">${display}</td>`;
    }).join('') + '</tr>';
  }).join('') + '</tbody>';

  table.innerHTML = thead + tbody;

  // Live GP re-check on cell edit
  table.querySelectorAll('td[contenteditable]').forEach(cell => {
    cell.addEventListener('blur', () => liveRecheck(cell, suggestions, result));
  });
}

async function liveRecheck(cell, suggestions, result) {
  const row = parseInt(cell.dataset.row);
  const col = cell.dataset.col;
  const newVal = parseFloat(cell.textContent);
  if (isNaN(newVal)) return;

  suggestions[row][col] = newVal;
  const payload = {
    job_id: STATE.jobId,
    x_row: buildRunPayload().input_cols.map(c => suggestions[row][c] ?? 0),
  };

  try {
    const res = await fetch('/predict_row', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return;
    const data = await res.json();
    // Update pred cells in that row
    const tr = el('suggestions-table').querySelector(`tr[data-row="${row}"]`);
    if (!tr) return;
    Object.entries(data.predictions).forEach(([pCol, val]) => {
      const cells = tr.querySelectorAll('td:not([contenteditable])');
      cells.forEach(td => {
        if (td.closest('tr') === tr) {
          const th = el('suggestions-table').querySelector(`thead th:nth-child(${[...tr.children].indexOf(td)+1})`);
          if (th && th.textContent === pCol) td.textContent = val.toFixed(6);
        }
      });
    });
  } catch(e) {
    // Silent fail — predictions are best-effort
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Download & export
// ─────────────────────────────────────────────────────────────────────────────
el('download-csv-btn').onclick = () => {
  if (!STATE.jobId) return;
  window.location.href = `/download/${STATE.jobId}`;
};

el('export-report-btn').onclick = () => {
  if (!STATE.jobId) return;
  window.location.href = `/export_report/${STATE.jobId}`;
};

// ─────────────────────────────────────────────────────────────────────────────
// Utility
// ─────────────────────────────────────────────────────────────────────────────
function downloadBlob(content, mimeType, filename) {
  const blob = new Blob([content], { type: mimeType });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  validateConfig();
  goToStep(1);
});
