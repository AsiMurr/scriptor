const API = '';
const token = localStorage.getItem('vt_token');

if (!token) {
  localStorage.removeItem('vt_guest_token');
  window.location.href = '/';
}

let selectedFile = null;
let lastResultText = '';
let lastResultId = null;
let lastFilename = 'transcription';
let allHistoryItems = [];
let isGuest = false;

// ── Init ──────────────────────────────────────────────────────────

async function init() {
  await loadUserInfo();
  await loadHistory();
  setupDropZone();
  document.getElementById('speaker-inputs').addEventListener('input', applyRenames);
}

async function authFetch(url, opts = {}) {
  return fetch(url, {
    ...opts,
    headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` },
  });
}

function logout() {
  localStorage.removeItem('vt_token');
  localStorage.removeItem('vt_guest_token');
  window.location.href = '/';
}

// ── User info ──────────────────────────────────────────────────────

async function loadUserInfo() {
  try {
    const res = await authFetch(`${API}/api/me`);
    if (res.status === 401) { logout(); return; }
    const data = await res.json();

    document.getElementById('user-email').textContent = data.plan === 'guest' ? 'Гость' : data.email;
    if (data.plan === 'guest') {
      isGuest = true;
      document.getElementById('guest-banner').style.display = '';
      document.getElementById('result-text').readOnly = true;
      document.getElementById('record-btn').closest('.record-row').style.display = 'none';
    }

    const widget = document.getElementById('account-widget');
    const used = data.used_minutes;
    const limit = data.limit_minutes;
    const remaining = data.remaining_minutes;
    const balance = data.balance ?? 0;

    widget.style.display = '';

    // Минуты
    const remText = remaining === '∞' ? '∞' : `${remaining} мин`;
    document.getElementById('widget-remaining').textContent = remText;
    document.getElementById('widget-remaining-detail').textContent = remText;

    // Прогресс-бар
    const fill = document.getElementById('widget-fill');
    if (limit !== '∞') {
      const pct = Math.min((remaining / limit) * 100, 100);
      fill.style.width = pct + '%';
      fill.classList.remove('warn', 'danger');
      if (pct <= 0) fill.classList.add('danger');
      else if (pct <= 20) fill.classList.add('warn');
    } else {
      fill.style.width = '100%';
    }

    // Баланс — только для зарегистрированных
    if (data.plan !== 'guest') {
      document.getElementById('widget-balance').textContent =
        balance.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽';
    } else {
      // Для гостей вместо баланса — подсказка зарегистрироваться
      document.getElementById('widget-balance').textContent = '—';
      document.querySelector('.account-widget-topup').textContent = 'Зарегистрироваться';
      document.querySelector('.account-widget-topup').href = '/?modal=register';
    }
  } catch (e) {
    console.error('loadUserInfo error', e);
  }
}

// ── Drop zone ──────────────────────────────────────────────────────

function setupDropZone() {
  const dz = document.getElementById('drop-zone');
  const fi = document.getElementById('file-input');
  const btn = document.getElementById('pick-btn');

  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault(); dz.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  });
  // Only button opens the dialog — no dz click handler to avoid double-open
  btn.addEventListener('click', (e) => { e.stopPropagation(); fi.click(); });
  fi.addEventListener('change', () => { if (fi.files[0]) setFile(fi.files[0]); });
}

function setFile(file) {
  selectedFile = file;
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-size').textContent = formatSize(file.size);
  document.getElementById('file-info').style.display = 'flex';
  document.getElementById('transcribe-btn').disabled = false;
  hideResult(); hideError();
}

function clearFile() {
  selectedFile = null;
  document.getElementById('file-input').value = '';
  document.getElementById('file-info').style.display = 'none';
  document.getElementById('transcribe-btn').disabled = true;
}

// ── Transcribe ─────────────────────────────────────────────────────

async function doTranscribe() {
  if (!selectedFile) return;

  showProgress('Загрузка файла…', 20);
  hideResult(); hideError();
  document.getElementById('transcribe-btn').disabled = true;

  const form = new FormData();
  form.append('file', selectedFile);

  try {
    setProgress(50, 'Транскрибация…');
    const res = await authFetch(`${API}/api/transcribe`, { method: 'POST', body: form });
    const data = await res.json();

    hideProgress();
    document.getElementById('transcribe-btn').disabled = false;

    if (!res.ok) {
      showError(data.detail || 'Неизвестная ошибка');
      if (res.status === 402) {
        showError(data.detail + ' <a href="#pricing">Обновить тариф →</a>');
      }
      return;
    }

    lastResultText = data.text;
    lastResultId = data.id;
    lastFilename = selectedFile?.name || 'transcription';
    document.getElementById('result-text').value = data.text;
    updateDownloadButtons(data.id);
    renderSpeakerPanel(extractSpeakers(data.text));
    document.getElementById('result-meta').textContent =
      `Длительность: ${Math.round(data.duration_seconds)}с · Использовано: ${data.used_minutes} мин · Осталось: ${data.remaining_minutes} мин`;
    showResult();
    await loadUserInfo();
    await loadHistory();
  } catch (e) {
    hideProgress();
    document.getElementById('transcribe-btn').disabled = false;
    showError('Ошибка сети. Проверь подключение.');
  }
}

// ── Copy / Download ────────────────────────────────────────────────

function copyText() {
  navigator.clipboard.writeText(lastResultText).then(() => {
    const btn = event.target;
    btn.textContent = '✓ Скопировано';
    setTimeout(() => btn.textContent = '📋 Копировать', 2000);
  });
}

function downloadText(fmt) {
  if (lastResultId) {
    downloadFmt(lastResultId, fmt);
  } else if (fmt === 'txt') {
    const blob = new Blob([lastResultText], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (selectedFile?.name || 'transcription').replace(/\.[^.]+$/, '') + '.txt';
    a.click();
  }
}

// ── History ────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const res = await authFetch(`${API}/api/history`);
    const items = await res.json();
    const list = document.getElementById('history-list');

    allHistoryItems = items;
    renderHistoryList(items);
  } catch (e) {
    console.error('loadHistory error', e);
  }
}

function filterHistory() {
  const q = document.getElementById('history-search').value.toLowerCase();
  const filtered = q
    ? allHistoryItems.filter(i => i.filename.toLowerCase().includes(q) || i.text.toLowerCase().includes(q) || (i.tags || '').toLowerCase().includes(q))
    : allHistoryItems;
  renderHistoryList(filtered);
}

function renderHistoryList(items) {
  const list = document.getElementById('history-list');
    if (!items.length) {
      list.innerHTML = '<p class="history-empty">Здесь появятся ваши транскрипции</p>';
      return;
    }

    list.innerHTML = items.map(item => `
      <div class="history-item" data-id="${item.id}">
        <div class="h-header">
          <div class="h-file">🎵 ${escHtml(item.filename)}</div>
          <button class="btn btn-sm btn-danger h-delete" onclick="deleteHistory(${item.id}, event)">✕</button>
        </div>
        <div class="h-preview" onclick="openHistoryItem(${item.id})">${escHtml(item.text)}</div>
        <div class="h-meta-row">
          <div class="h-date">${formatDate(item.created_at)} · ${formatDuration(item.duration_seconds)}</div>
          <div class="h-tags">${renderTagChips(item.tags, item.id)}</div>
        </div>
      </div>
    `).join('');
}

async function openHistoryItem(id) {
  try {
    const res = await authFetch(`${API}/api/history/${id}`);
    const item = await res.json();
    lastResultText = item.text;
    lastResultId = item.id;
    lastFilename = item.filename || 'transcription';
    document.getElementById('result-text').value = item.text;
    document.getElementById('result-meta').textContent = `Файл: ${item.filename} · ${Math.round(item.duration_seconds)}с`;
    showResult();
    updateDownloadButtons(item.id);
    renderSpeakerPanel(extractSpeakers(item.text));
  } catch (e) {
    console.error('openHistoryItem error', e);
  }
}

function updateDownloadButtons(id) {
  const wrap = document.getElementById('download-buttons');
  if (!wrap) return;
  wrap.innerHTML = `
    <button class="btn btn-sm btn-outline" onclick="copyText()">📋 Копировать</button>
    <button class="btn btn-sm btn-outline" onclick="downloadFmt(${id},'txt')">TXT</button>
    <button class="btn btn-sm btn-outline" onclick="downloadFmt(${id},'docx')">Word</button>
    <button class="btn btn-sm btn-outline" onclick="downloadFmt(${id},'pdf')">PDF</button>
    <button class="btn btn-sm btn-outline" onclick="downloadFmt(${id},'xlsx')">Excel</button>
  `;
}

async function downloadFmt(id, fmt) {
  try {
    // Use current textarea value so speaker renames are included
    const currentText = document.getElementById('result-text').value || lastResultText;
    const res = await authFetch(`${API}/api/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: currentText, fmt, filename: lastFilename }),
    });
    if (!res.ok) { showError('Ошибка скачивания'); return; }
    const blob = await res.blob();
    const base = lastFilename.replace(/\.[^.]+$/, '') || 'transcription';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${base}.${fmt === 'docx' ? 'docx' : fmt}`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    showError('Ошибка скачивания');
  }
}

async function deleteHistory(id, e) {
  e.stopPropagation();
  if (!confirm('Удалить эту транскрипцию?')) return;
  try {
    await authFetch(`${API}/api/history/${id}`, { method: 'DELETE' });
    if (lastResultId === id) { hideResult(); lastResultId = null; }
    await loadHistory();
  } catch (err) {
    console.error('deleteHistory error', err);
  }
}

// ── Microphone recording ───────────────────────────────────────────

let mediaRecorder = null;
let recordChunks = [];
let recordTimerInterval = null;
let recordSeconds = 0;
let vizAnimFrame = null;

function startViz(stream) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.75;
  src.connect(analyser);

  const canvas = document.getElementById('record-canvas');
  const c = canvas.getContext('2d');
  const BARS = 28;
  const data = new Uint8Array(analyser.fftSize);

  document.getElementById('record-viz').style.display = '';

  function draw() {
    vizAnimFrame = requestAnimationFrame(draw);
    analyser.getByteTimeDomainData(data);
    c.clearRect(0, 0, canvas.width, canvas.height);

    const barW = Math.floor(canvas.width / BARS) - 2;
    const step = Math.floor(data.length / BARS);
    const cy = canvas.height / 2;

    for (let i = 0; i < BARS; i++) {
      // average a chunk of samples for this bar
      let sum = 0;
      for (let j = 0; j < step; j++) sum += Math.abs(data[i * step + j] - 128);
      const amp = (sum / step) / 128;
      const h = Math.max(3, amp * canvas.height * 2.2);
      const alpha = 0.35 + amp * 0.65;
      c.fillStyle = `rgba(124,111,255,${alpha})`;
      c.beginPath();
      c.roundRect(i * (barW + 2), cy - h / 2, barW, h, 2);
      c.fill();
    }
  }
  draw();

  return () => {
    cancelAnimationFrame(vizAnimFrame);
    ctx.close();
    const cv = document.getElementById('record-canvas');
    cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
    document.getElementById('record-viz').style.display = 'none';
  };
}

async function toggleRecord() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordChunks = [];
    recordSeconds = 0;

    const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg';
    mediaRecorder = new MediaRecorder(stream, { mimeType });

    const stopViz = startViz(stream);

    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      stopViz();
      clearInterval(recordTimerInterval);
      document.getElementById('record-timer').style.display = 'none';
      document.getElementById('record-btn').classList.remove('recording');
      document.getElementById('record-btn').innerHTML = '<span class="mic-icon">🎙</span> Записать с микрофона';

      const ext = mimeType.includes('webm') ? 'webm' : 'ogg';
      const blob = new Blob(recordChunks, { type: mimeType });
      const file = new File([blob], `запись_${Date.now()}.${ext}`, { type: mimeType });
      setFile(file);
    };

    mediaRecorder.start();

    // UI
    const btn = document.getElementById('record-btn');
    btn.classList.add('recording');
    btn.textContent = '⏹ Остановить';
    document.getElementById('record-timer').style.display = '';
    recordTimerInterval = setInterval(() => {
      recordSeconds++;
      const m = String(Math.floor(recordSeconds / 60)).padStart(2, '0');
      const s = String(recordSeconds % 60).padStart(2, '0');
      document.getElementById('record-timer').textContent = `${m}:${s}`;
    }, 1000);

  } catch (e) {
    showError('Нет доступа к микрофону. Разреши доступ в браузере.');
  }
}

// ── Edit & Save ────────────────────────────────────────────────────

function onTextEdit() {
  if (isGuest) return;
  document.getElementById('save-btn').style.display = '';
}

async function saveEdit() {
  if (!lastResultId) return;
  const text = document.getElementById('result-text').value;
  const btn = document.getElementById('save-btn');
  btn.disabled = true;
  btn.textContent = 'Сохраняю…';
  try {
    const res = await authFetch(`${API}/api/history/${lastResultId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (res.ok) {
      lastResultText = text;
      btn.textContent = '✓ Сохранено';
      setTimeout(() => { btn.style.display = 'none'; btn.textContent = 'Сохранить'; btn.disabled = false; }, 1500);
      await loadHistory();
    } else {
      btn.textContent = 'Ошибка';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Ошибка';
    btn.disabled = false;
  }
}

// ── Speaker renaming ───────────────────────────────────────────────

function extractSpeakers(text) {
  const found = new Set();
  for (const m of text.matchAll(/\[Спикер (\w+) \|/g)) found.add(m[1]);
  return [...found];
}

function renderSpeakerPanel(speakers) {
  const panel = document.getElementById('speaker-panel');
  const inputs = document.getElementById('speaker-inputs');
  if (!speakers.length || isGuest) { panel.style.display = 'none'; return; }
  inputs.innerHTML = speakers.map(s => `
    <div class="speaker-row">
      <span class="speaker-label">Спикер ${s}</span>
      <span class="speaker-arrow">→</span>
      <input class="speaker-input" type="text" placeholder="Имя" data-speaker="${s}" />
    </div>
  `).join('');
  panel.style.display = '';
}

function applyRenames() {
  const inputs = document.querySelectorAll('#speaker-inputs .speaker-input');
  let text = lastResultText;
  inputs.forEach(inp => {
    const name = inp.value.trim();
    if (name) {
      const re = new RegExp(`\\[Спикер ${inp.dataset.speaker} \\|`, 'g');
      text = text.replace(re, `[${name} |`);
    }
  });
  document.getElementById('result-text').value = text;
}

// ── UI helpers ─────────────────────────────────────────────────────

function showProgress(text, pct) {
  document.getElementById('progress-wrap').style.display = '';
  setProgress(pct, text);
}
function setProgress(pct, text) {
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-text').textContent = text;
}
function hideProgress() { document.getElementById('progress-wrap').style.display = 'none'; }
function showResult() { document.getElementById('result-wrap').style.display = ''; }
function hideResult() { document.getElementById('result-wrap').style.display = 'none'; }
function showError(html) {
  const el = document.getElementById('error-wrap');
  document.getElementById('error-msg').innerHTML = html;
  el.style.display = '';
}
function hideError() { document.getElementById('error-wrap').style.display = 'none'; }

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' Б';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' КБ';
  return (bytes / (1024 * 1024)).toFixed(1) + ' МБ';
}

function formatDuration(sec) {
  const s = Math.round(sec);
  if (s < 60) return `${s}с`;
  const m = Math.floor(s / 60), r = s % 60;
  return r ? `${m}м ${r}с` : `${m}м`;
}

const PRESET_TAGS = ['встреча', 'лекция', 'звонок', 'заметка', 'интервью'];

function renderTagChips(tagsStr, id) {
  const active = (tagsStr || '').split(',').map(t => t.trim()).filter(Boolean);
  return PRESET_TAGS.map(tag => {
    const on = active.includes(tag);
    return `<span class="tag-chip${on ? ' tag-on' : ''}" onclick="toggleTag(${id},'${tag}',this)">${tag}</span>`;
  }).join('');
}

async function toggleTag(id, tag, el) {
  const item = allHistoryItems.find(i => i.id === id);
  if (!item) return;
  const active = (item.tags || '').split(',').map(t => t.trim()).filter(Boolean);
  const idx = active.indexOf(tag);
  if (idx === -1) active.push(tag); else active.splice(idx, 1);
  item.tags = active.join(',');
  el.classList.toggle('tag-on');
  await authFetch(`${API}/api/history/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags: item.tags }),
  });
}

function formatDate(iso) {
  return new Date(iso).toLocaleString('ru-RU', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' });
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Start ──────────────────────────────────────────────────────────
init();
