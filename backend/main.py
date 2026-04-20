from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional
import tempfile, os, pathlib, io, smtplib, time, asyncio, traceback as tb
import httpx
import requests as req_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
import assemblyai as aai

from database import get_db, User, Transcription, Feedback, ErrorLog, init_db, engine
from auth import hash_password, verify_password, create_access_token, create_guest_token, get_current_user
from usage import get_used_minutes_this_month, get_remaining_minutes, check_quota, PLAN_LIMITS

from dotenv import load_dotenv
load_dotenv()

aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")

app = FastAPI(title="Scriptor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Error logging middleware ─────────────────────────────────────────────────

@app.middleware("http")
async def log_errors(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        try:
            db = next(get_db())
            db.add(ErrorLog(
                path=str(request.url.path),
                method=request.method,
                error=str(e),
                traceback=tb.format_exc(),
            ))
            db.commit()
        except Exception:
            pass
        raise


# ─── Schemas ────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str

class DownloadRequest(BaseModel):
    text: str
    fmt: str
    filename: str = "transcription"
    token_type: str = "bearer"

class UserInfo(BaseModel):
    id: int
    email: str
    plan: str
    used_minutes: float
    limit_minutes: float | str
    remaining_minutes: float | str
    balance: float


# ─── Rate limiting ───────────────────────────────────────────────────────────

# Формат: { ip: {"count": N, "blocked_until": timestamp} }
_login_attempts: dict = defaultdict(lambda: {"count": 0, "blocked_until": 0})
# Формат: { ip: [timestamp, timestamp, ...] }
_register_attempts: dict = defaultdict(list)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_BLOCK_SECONDS = 15 * 60  # 15 минут
REGISTER_MAX_PER_HOUR = 3


def check_login_rate(ip: str):
    entry = _login_attempts[ip]
    now = time.time()
    if entry["blocked_until"] > now:
        remaining = int((entry["blocked_until"] - now) / 60) + 1
        raise HTTPException(status_code=429, detail=f"Слишком много попыток. Попробуйте через {remaining} мин.")


def record_login_fail(ip: str):
    entry = _login_attempts[ip]
    entry["count"] += 1
    if entry["count"] >= LOGIN_MAX_ATTEMPTS:
        entry["blocked_until"] = time.time() + LOGIN_BLOCK_SECONDS
        entry["count"] = 0


def reset_login_attempts(ip: str):
    _login_attempts[ip] = {"count": 0, "blocked_until": 0}


def check_register_rate(ip: str):
    now = time.time()
    hour_ago = now - 3600
    attempts = [t for t in _register_attempts[ip] if t > hour_ago]
    _register_attempts[ip] = attempts
    if len(attempts) >= REGISTER_MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="Слишком много регистраций. Попробуйте через час.")
    _register_attempts[ip].append(now)


def send_welcome_email(email: str, password: str):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not smtp_host or not smtp_user:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Добро пожаловать в Scriptor 🎙"
        msg["From"] = smtp_user
        msg["To"] = email
        html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:60px 0;background:#080810;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:520px;margin:0 auto;background:#0e0e1a;border-radius:16px;overflow:hidden;border:1px solid #1e1e30">

  <!-- Шапка -->
  <div style="background:linear-gradient(135deg,#7C6FFF,#FF6584);padding:32px;text-align:center">
    <div style="margin-bottom:10px">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="56" height="56" style="display:inline-block">
        <rect x="10" y="10" width="180" height="180" rx="38" fill="#1a1040" stroke="#00d4ff" stroke-width="3.5"/>
        <defs><linearGradient id="em" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#b0a0ff"/><stop offset="100%" stop-color="#7C6FFF"/></linearGradient></defs>
        <rect x="80" y="44" width="40" height="64" rx="4" fill="url(#em)"/>
        <rect x="80" y="60" width="40" height="3" fill="rgba(255,255,255,0.25)"/>
        <rect x="80" y="70" width="40" height="3" fill="rgba(255,255,255,0.25)"/>
        <rect x="80" y="80" width="40" height="3" fill="rgba(255,255,255,0.25)"/>
        <rect x="80" y="90" width="40" height="3" fill="rgba(255,255,255,0.25)"/>
        <path d="M 66,110 Q 66,138 100,138 Q 134,138 134,110" fill="none" stroke="#9080ef" stroke-width="5" stroke-linecap="square"/>
        <rect x="97" y="138" width="6" height="13" fill="#9080ef"/>
        <rect x="76" y="150" width="48" height="6" rx="1" fill="#9080ef"/>
      </svg>
    </div>
    <div style="color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px">Scriptor</div>
    <div style="color:rgba(255,255,255,0.8);font-size:14px;margin-top:6px">Голос в текст</div>
  </div>

  <!-- Приветствие -->
  <div style="padding:32px 32px 0">
    <h2 style="margin:0 0 10px;color:#eeeef8;font-size:22px">Поздравляем с регистрацией! 🎉</h2>
    <p style="margin:0;color:#9090b0;font-size:15px;line-height:1.6">
      Ваш аккаунт успешно создан. Теперь вам доступны все возможности сервиса.
    </p>
  </div>

  <!-- Данные аккаунта -->
  <div style="margin:24px 32px;background:#161625;border:1px solid #252540;border-radius:12px;padding:20px">
    <div style="color:#7878a0;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:14px">Данные аккаунта</div>
    <div style="margin-bottom:10px">
      <span style="color:#7878a0;font-size:13px">📧 Email</span><br>
      <span style="color:#eeeef8;font-size:15px;font-weight:600">{email}</span>
    </div>
    <div>
      <span style="color:#7878a0;font-size:13px">🔑 Пароль</span><br>
      <span style="color:#eeeef8;font-size:15px;font-weight:600">{password}</span>
    </div>
    <div style="margin-top:14px;padding-top:14px;border-top:1px solid #252540;color:#7878a0;font-size:12px">
      Сохраните эти данные — они понадобятся для входа.
    </div>
  </div>

  <!-- Что доступно -->
  <div style="padding:0 32px">
    <div style="color:#7878a0;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:14px">Что доступно с аккаунтом</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 16px 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4;width:50%"><b>20 минут</b> бесплатно каждый месяц</td>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 0 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4"><b>История</b> всех транскрипций</td>
      </tr>
      <tr><td colspan="4" height="6"></td></tr>
      <tr>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 16px 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4"><b>Редактирование</b> текста</td>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 0 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4"><b>Переименование</b> спикеров</td>
      </tr>
      <tr><td colspan="4" height="6"></td></tr>
      <tr>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 16px 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4"><b>Запись голоса</b> в браузере</td>
        <td width="22" style="vertical-align:top;padding:4px 0;color:#eeeef8;font-size:14px">✅</td>
        <td style="vertical-align:top;padding:4px 0 4px 6px;color:#eeeef8;font-size:14px;line-height:1.4">Экспорт в <b>Word, PDF, Excel, TXT</b></td>
      </tr>
    </table>
  </div>

  <!-- Кнопка -->
  <div style="padding:32px;text-align:center">
    <a href="http://localhost:8000/app" style="display:inline-block;background:linear-gradient(135deg,#7C6FFF,#5a52d5);color:#fff;text-decoration:none;padding:14px 36px;border-radius:10px;font-size:15px;font-weight:600">
      Открыть приложение →
    </a>
  </div>

  <!-- Подвал -->
  <div style="padding:20px 32px;border-top:1px solid #1e1e30;text-align:center">
    <p style="margin:0;color:#555570;font-size:12px">
      Если вы не регистрировались — просто проигнорируйте это письмо.
    </p>
    <p style="margin:8px 0 0;color:#555570;font-size:12px">© Scriptor</p>
  </div>

</div>
</body>
</html>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, email, msg.as_string())
    except Exception as e:
        print(f"[EMAIL ERROR send_welcome_email to {email}]: {e}", flush=True)


@app.post("/api/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    import secrets
    ip = request.client.host
    check_register_rate(ip)
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль минимум 6 символов")
    verify_token = secrets.token_urlsafe(32)
    user = User(email=req.email, hashed_password=hash_password(req.password),
                is_verified=False, verify_token=verify_token)
    db.add(user)
    db.commit()
    db.refresh(user)
    send_welcome_email(req.email, req.password)
    send_verify_email(req.email, verify_token)
    return TokenResponse(access_token=create_access_token(user.id, user.email))


@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host
    check_login_rate(ip)
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        record_login_fail(ip)
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Подтвердите email перед входом. Проверьте почту.")
    reset_login_attempts(ip)
    return TokenResponse(access_token=create_access_token(user.id, user.email))


@app.post("/api/auth/guest", response_model=TokenResponse)
def guest_login(request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
    return TokenResponse(access_token=create_guest_token(ip))


# ─── User info ───────────────────────────────────────────────────────────────

@app.get("/api/me", response_model=UserInfo)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    used = get_used_minutes_this_month(user.id, db)
    limit = PLAN_LIMITS[user.plan]
    remaining = get_remaining_minutes(user, db)
    return UserInfo(
        id=user.id,
        email=user.email,
        plan=user.plan,
        used_minutes=round(used, 2),
        limit_minutes=limit if limit != float("inf") else "∞",
        remaining_minutes=round(remaining, 2) if remaining != float("inf") else "∞",
        balance=round(user.balance or 0.0, 2),
    )


# ─── Transcription ───────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".webm", ".flac"}
MAX_FILE_SIZE_MB = 25


def _fmt_time(ms: int) -> str:
    """Миллисекунды → MM:SS"""
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Проверка расширения
    ext = pathlib.Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Формат не поддерживается. Разрешены: {', '.join(ALLOWED_EXTENSIONS)}")

    # Читаем файл
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Файл слишком большой. Максимум {MAX_FILE_SIZE_MB} МБ")

    # Проверка квоты
    try:
        check_quota(user, db)
    except Exception as e:
        raise HTTPException(status_code=402, detail=str(e))

    # Отправка в AssemblyAI через REST API напрямую
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    json_headers = {"authorization": api_key, "content-type": "application/json"}

    def _upload(data: bytes) -> str:
        r = req_lib.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": api_key},
            data=data,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["upload_url"]

    def _create_transcript(audio_url: str) -> str:
        r = req_lib.post(
            "https://api.assemblyai.com/v2/transcript",
            headers=json_headers,
            json={
                "audio_url": audio_url,
                "speaker_labels": True,
                "language_detection": True,
                "speech_models": ["universal-2"],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["id"]

    def _poll(transcript_id: str) -> dict:
        r = req_lib.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=json_headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    try:
        audio_url = await asyncio.to_thread(_upload, content)
        transcript_id = await asyncio.to_thread(_create_transcript, audio_url)

        result = None
        for _ in range(100):
            await asyncio.sleep(3)
            result = await asyncio.to_thread(_poll, transcript_id)
            if result.get("status") == "completed":
                break
            if result.get("status") == "error":
                raise HTTPException(status_code=500, detail=f"Ошибка транскрибации: {result.get('error')}")
        else:
            raise HTTPException(status_code=504, detail="Превышено время ожидания (5 мин). Попробуйте файл меньшего размера.")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка транскрибации: {str(e)}")

    duration_sec = result.get("audio_duration") or 0.0

    # Форматируем текст с разделением по спикерам
    utterances = result.get("utterances") or []
    if utterances:
        lines = []
        for utt in utterances:
            start = _fmt_time(utt.get("start", 0))
            lines.append(f"[Спикер {utt.get('speaker')} | {start}] {utt.get('text', '')}")
        text = "\n\n".join(lines)
    else:
        text = result.get("text") or ""

    # Сохраняем в БД (гостям только для учёта квоты, без текста)
    record = Transcription(
        user_id=user.id,
        filename=file.filename,
        duration_seconds=duration_sec,
        text="" if user.plan == "guest" else text,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    used = get_used_minutes_this_month(user.id, db)
    remaining = get_remaining_minutes(user, db)

    return {
        "id": record.id,
        "text": text,
        "duration_seconds": round(duration_sec, 1),
        "used_minutes": round(used, 2),
        "remaining_minutes": round(remaining, 2) if remaining != float("inf") else "∞",
    }


@app.get("/api/history")
def history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    if user.plan == "guest":
        return []
    rows = (
        db.query(Transcription)
        .filter(Transcription.user_id == user.id)
        .order_by(Transcription.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "duration_seconds": r.duration_seconds,
            "text": r.text[:200] + ("…" if len(r.text) > 200 else ""),
            "tags": r.tags or "",
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/api/history/{item_id}")
def history_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(Transcription).filter(Transcription.id == item_id, Transcription.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")
    return {"id": row.id, "filename": row.filename, "duration_seconds": row.duration_seconds, "text": row.text, "tags": row.tags or "", "created_at": row.created_at.isoformat()}


@app.put("/api/history/{item_id}")
def update_history_item(
    item_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(Transcription).filter(Transcription.id == item_id, Transcription.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")
    if "text" in body:
        row.text = body["text"]
    if "tags" in body:
        row.tags = body["tags"]
    db.commit()
    return {"ok": True}


@app.delete("/api/history/{item_id}")
def delete_history_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(Transcription).filter(Transcription.id == item_id, Transcription.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/api/history/{item_id}/download")
def download_history_item(
    item_id: int,
    fmt: str = "txt",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(Transcription).filter(Transcription.id == item_id, Transcription.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")

    base_name = pathlib.Path(row.filename).stem

    if fmt == "txt":
        buf = io.BytesIO(row.text.encode("utf-8"))
        return StreamingResponse(buf, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{base_name}.txt"'})

    elif fmt == "docx":
        from docx import Document
        doc = Document()
        doc.add_heading(row.filename, 0)
        for line in row.text.split("\n"):
            doc.add_paragraph(line)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                  headers={"Content-Disposition": f'attachment; filename="{base_name}.docx"'})

    elif fmt == "pdf":
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.set_title(row.filename)
        for line in row.text.split("\n"):
            safe = line.encode("latin-1", "replace").decode("latin-1")
            pdf.multi_cell(0, 8, safe)
        buf = io.BytesIO(pdf.output())
        return StreamingResponse(buf, media_type="application/pdf",
                                  headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'})

    elif fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Транскрипция"
        ws.append(["Файл", "Дата", "Длительность (с)", "Текст"])
        ws.append([row.filename, row.created_at.isoformat(), row.duration_seconds, row.text])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  headers={"Content-Disposition": f'attachment; filename="{base_name}.xlsx"'})

    raise HTTPException(status_code=400, detail="Неизвестный формат")


@app.post("/api/download")
def download_text_body(body: DownloadRequest, user: User = Depends(get_current_user)):
    base_name = pathlib.Path(body.filename).stem or "transcription"
    text = body.text
    fmt = body.fmt

    if fmt == "txt":
        buf = io.BytesIO(text.encode("utf-8"))
        return StreamingResponse(buf, media_type="text/plain",
                                 headers={"Content-Disposition": f'attachment; filename="{base_name}.txt"'})

    elif fmt == "docx":
        from docx import Document
        doc = Document()
        doc.add_heading(body.filename, 0)
        for line in text.split("\n"):
            doc.add_paragraph(line)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return StreamingResponse(buf,
                                 media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                 headers={"Content-Disposition": f'attachment; filename="{base_name}.docx"'})

    elif fmt == "pdf":
        import fpdf as fpdf_module
        import os as _os
        from fpdf import FPDF
        font_path = _os.path.join(_os.path.dirname(fpdf_module.__file__), "fonts", "DejaVuSans.ttf")
        pdf = FPDF()
        pdf.add_page()
        pdf.add_font("DejaVu", fname=font_path)
        pdf.set_font("DejaVu", size=12)
        for line in text.split("\n"):
            pdf.multi_cell(0, 8, line)
        buf = io.BytesIO(bytes(pdf.output()))
        return StreamingResponse(buf, media_type="application/pdf",
                                 headers={"Content-Disposition": f'attachment; filename="{base_name}.pdf"'})

    elif fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Транскрипция"
        ws.append(["Файл", "Текст"])
        ws.append([body.filename, text])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf,
                                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="{base_name}.xlsx"'})

    raise HTTPException(status_code=400, detail="Неизвестный формат")


# ─── Health check ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    from sqlalchemy import text
    db.execute(text("SELECT 1"))
    from datetime import datetime
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ─── Feedback ────────────────────────────────────────────────────────────────

_feedback_attempts: dict = defaultdict(list)
FEEDBACK_MAX_PER_HOUR = 3


class FeedbackRequest(BaseModel):
    email: Optional[str] = None
    message: str


@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host
    now = time.time()
    hour_ago = now - 3600
    attempts = [t for t in _feedback_attempts[ip] if t > hour_ago]
    _feedback_attempts[ip] = attempts
    if len(attempts) >= FEEDBACK_MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="Слишком много сообщений. Попробуйте через час.")
    _feedback_attempts[ip].append(now)

    if not req.message or len(req.message.strip()) < 5:
        raise HTTPException(status_code=400, detail="Сообщение слишком короткое.")

    item = Feedback(email=req.email, message=req.message.strip())
    db.add(item)
    db.commit()
    return {"ok": True}


@app.get("/api/feedback/admin")
def get_feedback(token: str = "", db: Session = Depends(get_db)):
    if token != os.getenv("ADMIN_TOKEN", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    items = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    return [
        {
            "id": f.id,
            "email": f.email,
            "message": f.message,
            "status": f.status,
            "resolution": f.resolution,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in items
    ]


@app.get("/api/admin/logs")
def get_error_logs(token: str = "", db: Session = Depends(get_db)):
    if token != os.getenv("ADMIN_TOKEN", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    logs = db.query(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(50).all()
    return [
        {
            "id": l.id,
            "path": l.path,
            "method": l.method,
            "error": l.error,
            "traceback": l.traceback,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]


def _check_admin(token: str):
    if token != os.getenv("ADMIN_TOKEN", ""):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/api/admin/users")
def admin_get_users(token: str = "", db: Session = Depends(get_db)):
    _check_admin(token)
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        used = get_used_minutes_this_month(u.id, db)
        result.append({
            "id": u.id,
            "email": u.email,
            "plan": u.plan,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "used_minutes": round(used, 1),
            "balance": u.balance,
        })
    return result


class AdminUserUpdate(BaseModel):
    plan: Optional[str] = None
    is_active: Optional[bool] = None
    balance: Optional[float] = None

@app.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, data: AdminUserUpdate, token: str = "", db: Session = Depends(get_db)):
    _check_admin(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.plan is not None:
        user.plan = data.plan
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.balance is not None:
        user.balance = data.balance
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, token: str = "", db: Session = Depends(get_db)):
    _check_admin(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/stats")
def admin_stats(token: str = "", db: Session = Depends(get_db)):
    _check_admin(token)
    total = db.query(User).count()
    active = db.query(User).filter(User.is_active == True, User.plan != "free").count()
    free = db.query(User).filter(User.plan == "free").count()
    paid = db.query(User).filter(User.plan.in_(["standard", "pro"])).count()
    return {
        "total": total,
        "active": active,
        "free": free,
        "paid": paid,
    }


# ─── Email verify & password reset ──────────────────────────────────────────

def send_verify_email(email: str, token: str):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    if not smtp_host or not smtp_user:
        return
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    link = f"{base_url}/api/auth/verify/{token}"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Подтвердите email — Scriptor"
        msg["From"] = smtp_user
        msg["To"] = email
        html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:40px 0;background:#080810;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:480px;margin:0 auto;background:#0e0e1a;border-radius:16px;overflow:hidden;border:1px solid #1e1e30">
  <div style="background:linear-gradient(135deg,#7C6FFF,#FF6584);padding:28px;text-align:center">
    <div style="color:#fff;font-size:22px;font-weight:700">Scriptor</div>
    <div style="color:rgba(255,255,255,0.8);font-size:13px;margin-top:4px">Голос в текст</div>
  </div>
  <div style="padding:32px">
    <h2 style="margin:0 0 12px;color:#eeeef8;font-size:20px">Подтвердите ваш email</h2>
    <p style="color:#9090b0;font-size:14px;line-height:1.6;margin:0 0 24px">
      Нажмите кнопку ниже чтобы активировать аккаунт. Ссылка действует 24 часа.
    </p>
    <div style="text-align:center">
      <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#7C6FFF,#5a52d5);color:#fff;text-decoration:none;padding:14px 36px;border-radius:10px;font-size:15px;font-weight:600">
        Подтвердить email →
      </a>
    </div>
    <p style="margin:24px 0 0;color:#555570;font-size:12px;text-align:center">
      Если вы не регистрировались — просто проигнорируйте это письмо.
    </p>
  </div>
</div>
</body></html>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, email, msg.as_string())
    except Exception as e:
        print(f"[EMAIL ERROR send_verify_email to {email}]: {e}", flush=True)


def send_reset_email(email: str, token: str):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    if not smtp_host or not smtp_user:
        return
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    link = f"{base_url}/?modal=reset&token={token}"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Сброс пароля — Scriptor"
        msg["From"] = smtp_user
        msg["To"] = email
        html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:40px 0;background:#080810;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:480px;margin:0 auto;background:#0e0e1a;border-radius:16px;overflow:hidden;border:1px solid #1e1e30">
  <div style="background:linear-gradient(135deg,#7C6FFF,#FF6584);padding:28px;text-align:center">
    <div style="color:#fff;font-size:22px;font-weight:700">Scriptor</div>
    <div style="color:rgba(255,255,255,0.8);font-size:13px;margin-top:4px">Голос в текст</div>
  </div>
  <div style="padding:32px">
    <h2 style="margin:0 0 12px;color:#eeeef8;font-size:20px">Сброс пароля</h2>
    <p style="color:#9090b0;font-size:14px;line-height:1.6;margin:0 0 24px">
      Вы запросили сброс пароля. Нажмите кнопку ниже — ссылка действует 1 час.
    </p>
    <div style="text-align:center">
      <a href="{link}" style="display:inline-block;background:linear-gradient(135deg,#7C6FFF,#5a52d5);color:#fff;text-decoration:none;padding:14px 36px;border-radius:10px;font-size:15px;font-weight:600">
        Сбросить пароль →
      </a>
    </div>
    <p style="margin:24px 0 0;color:#555570;font-size:12px;text-align:center">
      Если вы не запрашивали сброс — просто проигнорируйте это письмо.
    </p>
  </div>
</div>
</body></html>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, email, msg.as_string())
    except Exception as e:
        print(f"[EMAIL ERROR send_reset_email to {email}]: {e}", flush=True)


class ResendVerifyRequest(BaseModel):
    email: str

@app.post("/api/auth/resend-verify")
def resend_verify(req: ResendVerifyRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        return {"ok": True}
    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email уже подтверждён")
    if not user.verify_token:
        user.verify_token = secrets.token_urlsafe(32)
        db.commit()
    send_verify_email(user.email, user.verify_token)
    return {"ok": True}


@app.get("/api/auth/verify/{token}", include_in_schema=False)
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verify_token == token).first()
    if not user:
        return FileResponse(str(pathlib.Path(__file__).parent.parent / "frontend" / "index.html"))
    user.is_verified = True
    user.verify_token = None
    db.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/?verified=1")


@app.post("/api/auth/forgot-password")
def forgot_password(body: dict, request: Request, db: Session = Depends(get_db)):
    email = body.get("email", "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        import secrets
        from datetime import datetime, timedelta
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()
        send_reset_email(email, token)
    # Всегда отвечаем одинаково — не раскрываем существует ли email
    return {"ok": True}


@app.post("/api/auth/reset-password")
def reset_password(body: dict, db: Session = Depends(get_db)):
    from datetime import datetime
    token = body.get("token", "")
    new_password = body.get("password", "")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Пароль минимум 6 символов")
    user = db.query(User).filter(User.reset_token == token).first()
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Ссылка недействительна или истекла")
    user.hashed_password = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return {"ok": True}


# ─── Serve frontend ──────────────────────────────────────────────────────────

frontend_path = pathlib.Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(str(frontend_path / "index.html"))

    @app.get("/app", include_in_schema=False)
    def app_page():
        return FileResponse(str(frontend_path / "app.html"))

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(str(frontend_path / "admin.html"))


# ─── Startup ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    from sqlalchemy import text
    with engine.connect() as conn:
        for migration in [
            "ALTER TABLE transcriptions ADD COLUMN tags TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN verify_token TEXT",
            "ALTER TABLE users ADD COLUMN reset_token TEXT",
            "ALTER TABLE users ADD COLUMN reset_token_expires TEXT",
            "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, email TEXT, message TEXT NOT NULL, status TEXT DEFAULT 'new', resolution TEXT, created_at TEXT, updated_at TEXT)",
            "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0.0",
            "CREATE TABLE IF NOT EXISTS error_logs (id INTEGER PRIMARY KEY, path TEXT, method TEXT, error TEXT, traceback TEXT, created_at TEXT)",
        ]:
            try:
                conn.execute(text(migration))
                conn.commit()
            except Exception:
                pass  # column already exists


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
