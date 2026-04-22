const API = '';  // Same origin

// Show "Open app" button if already logged in (but not if ?modal= param is set)
const _urlParams = new URLSearchParams(window.location.search);
const _modalParam = _urlParams.get('modal');

const _verifiedParam = _urlParams.get('verified');
const _resetToken = _urlParams.get('token');

if (_modalParam === 'reset' && _resetToken) {
  sessionStorage.setItem('reset_token', _resetToken);
  openModal('reset');
  history.replaceState(null, '', '/');
} else if (_verifiedParam) {
  openModal('login');
  history.replaceState(null, '', '/');
  setTimeout(() => {
    const err = document.getElementById('login-error');
    if (err) { err.style.color = '#4caf50'; err.textContent = 'Email подтверждён! Теперь войдите.'; }
  }, 100);
} else if (_modalParam) {
  openModal(_modalParam);
  history.replaceState(null, '', '/');
} else if (localStorage.getItem('vt_token')) {
  const links = document.querySelector('.nav-links');
  if (links) {
    links.innerHTML = `<a class="btn btn-primary" href="/app">Открыть приложение →</a>`;
  }
}

function openModal(form) {
  document.getElementById('modal-overlay').classList.add('open');
  switchForm(form);
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}

function switchForm(form) {
  ['login','register','forgot','reset','success'].forEach(f => {
    const el = document.getElementById('form-' + f);
    if (el) el.style.display = f === form ? '' : 'none';
  });
}

async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');
  errEl.textContent = '';

  if (!email || !password) { errEl.textContent = 'Заполни все поля'; return; }
  if (!isValidEmail(email)) { errEl.textContent = 'Введи корректный email (например: name@gmail.com)'; return; }

  try {
    const res = await fetch(`${API}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.detail || 'Ошибка входа';
      if (res.status === 403) {
        document.getElementById('resend-verify-wrap').style.display = '';
      }
      return;
    }
    document.getElementById('resend-verify-wrap').style.display = 'none';
    localStorage.setItem('vt_token', data.access_token);
    window.location.href = '/app';
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

async function doResendVerifyFromSuccess() {
  const email = document.getElementById('reg-email').value.trim();
  const errEl = document.getElementById('resend-success-error');
  errEl.style.color = '';
  errEl.textContent = '';
  if (!email || !isValidEmail(email)) { errEl.textContent = 'Email не найден. Вернитесь и зарегистрируйтесь заново.'; return; }
  try {
    await fetch(`${API}/api/auth/resend-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    errEl.style.color = '#4caf50';
    errEl.textContent = 'Письмо отправлено повторно! Проверьте почту.';
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

async function doResendVerify() {
  const email = document.getElementById('login-email').value.trim();
  const errEl = document.getElementById('login-error');
  if (!email) { errEl.textContent = 'Введи email выше'; return; }
  if (!isValidEmail(email)) { errEl.textContent = 'Введи корректный email (например: name@gmail.com)'; return; }
  try {
    const res = await fetch(`${API}/api/auth/resend-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Ошибка'; return; }
    errEl.style.color = '#4caf50';
    errEl.textContent = 'Письмо отправлено! Проверьте почту.';
    document.getElementById('resend-verify-wrap').style.display = 'none';
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

async function doRegister() {
  const email = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-password').value;
  const errEl = document.getElementById('reg-error');
  errEl.textContent = '';

  if (!email || !password) { errEl.textContent = 'Заполни все поля'; return; }
  if (password.length < 6) { errEl.textContent = 'Пароль минимум 6 символов'; return; }

  try {
    const res = await fetch(`${API}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Ошибка регистрации'; return; }
    document.getElementById('form-register').style.display = 'none';
    document.getElementById('form-success').style.display = '';
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

async function doForgot() {
  const email = document.getElementById('forgot-email').value.trim();
  const errEl = document.getElementById('forgot-error');
  errEl.textContent = '';
  if (!email) { errEl.textContent = 'Введи email'; return; }
  if (!isValidEmail(email)) { errEl.textContent = 'Введи корректный email (например: name@gmail.com)'; return; }
  try {
    await fetch(`${API}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    errEl.style.color = '#4caf50';
    errEl.textContent = 'Письмо отправлено — проверь почту';
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

async function doReset() {
  const password = document.getElementById('reset-password').value;
  const errEl = document.getElementById('reset-error');
  errEl.textContent = '';
  if (password.length < 6) { errEl.textContent = 'Пароль минимум 6 символов'; return; }
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || sessionStorage.getItem('reset_token');
  try {
    const res = await fetch(`${API}/api/auth/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, password }),
    });
    const data = await res.json();
    if (!res.ok) { errEl.textContent = data.detail || 'Ошибка'; return; }
    errEl.style.color = '#4caf50';
    errEl.textContent = 'Пароль изменён! Войдите с новым паролем.';
    setTimeout(() => { history.replaceState(null, '', '/'); switchForm('login'); }, 2000);
  } catch {
    errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

function tryAsGuest() {
  openModal('register');
}

async function doFeedback() {
  const email = (document.getElementById('fb-email')?.value || '').trim();
  const message = (document.getElementById('fb-message')?.value || '').trim();
  const errEl = document.getElementById('fb-error');
  if (errEl) errEl.textContent = '';
  if (!message || message.length < 5) { if (errEl) errEl.textContent = 'Опишите проблему подробнее.'; return; }
  try {
    const res = await fetch(`${API}/api/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email || null, message }),
    });
    if (!res.ok) { const d = await res.json(); if (errEl) errEl.textContent = d.detail || 'Ошибка'; return; }
    document.getElementById('feedback-form').style.display = 'none';
    document.getElementById('feedback-thanks').style.display = '';
  } catch {
    if (errEl) errEl.textContent = 'Ошибка сети. Попробуй снова.';
  }
}

// Enter key support
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  if (document.getElementById('form-login').style.display !== 'none') doLogin();
  else if (document.getElementById('form-register').style.display !== 'none') doRegister();
});
