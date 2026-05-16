"""
Интеграция ЮКассы: создание платежа, обработка webhook, активация тарифа.

Документация: https://yookassa.ru/developers/api
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import os
import logging

from yookassa import Configuration, Payment as YkPayment

from database import get_db, User, Payment
from auth import get_current_user

logger = logging.getLogger("payments")

router = APIRouter(prefix="/api/payment", tags=["payments"])


# ─── Конфиг ─────────────────────────────────────────────────────────────────

PLAN_PRICES = {
    "standard": 399,
    "pro": 1190,
}

PLAN_TITLES = {
    "standard": "Тариф «Стандарт» — 1 месяц",
    "pro": "Тариф «Про» — 1 месяц",
}


def _configure_yookassa():
    """Подгружает ключи из env. Вызывать перед каждой операцией —
    Configuration хранит ключи в class attributes, и они переживают reload."""
    shop_id = os.getenv("YOOKASSA_SHOP_ID", "")
    secret_key = os.getenv("YOOKASSA_SECRET_KEY", "")
    if not shop_id or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="ЮКасса не настроена: проверьте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY",
        )
    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key


# ─── Schemas ────────────────────────────────────────────────────────────────

class CreatePaymentRequest(BaseModel):
    plan: str  # standard | pro


class CreatePaymentResponse(BaseModel):
    payment_id: str
    confirmation_url: str
    amount: float


# ─── Эндпоинты ──────────────────────────────────────────────────────────────

@router.post("/create", response_model=CreatePaymentResponse)
def create_payment(
    body: CreatePaymentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создаёт платёж в ЮКассе и возвращает URL для редиректа на форму оплаты."""
    if user.plan == "guest":
        raise HTTPException(status_code=403, detail="Сначала зарегистрируйтесь")

    plan = body.plan.lower().strip()
    if plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")

    _configure_yookassa()

    amount = PLAN_PRICES[plan]
    base_url = os.getenv("BASE_URL", "https://getscriptor.ru")
    return_url = f"{base_url}/payment-success"

    import uuid
    idempotence_key = str(uuid.uuid4())

    try:
        yk = YkPayment.create({
            "amount": {"value": f"{amount}.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": return_url},
            "capture": True,
            "description": PLAN_TITLES[plan],
            "metadata": {"user_id": str(user.id), "plan": plan},
        }, idempotence_key)
    except Exception as e:
        logger.exception("YooKassa create failed")
        raise HTTPException(status_code=502, detail=f"Не удалось создать платёж: {e}")

    # Сохраняем pending платёж
    record = Payment(
        user_id=user.id,
        yk_payment_id=yk.id,
        plan=plan,
        amount=float(amount),
        status="pending",
    )
    db.add(record)
    db.commit()

    return CreatePaymentResponse(
        payment_id=yk.id,
        confirmation_url=yk.confirmation.confirmation_url,
        amount=float(amount),
    )


@router.post("/webhook")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    """Принимает уведомление от ЮКассы и активирует тариф при успешной оплате."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event", "")
    obj = body.get("object") or {}
    yk_id = obj.get("id")
    if not yk_id:
        raise HTTPException(status_code=400, detail="Нет id платежа")

    # Перепроверяем статус через API ЮКассы (защита от поддельных webhook)
    _configure_yookassa()
    try:
        yk = YkPayment.find_one(yk_id)
    except Exception as e:
        logger.exception("YooKassa find_one failed")
        raise HTTPException(status_code=502, detail=f"Не удалось проверить платёж: {e}")

    record = db.query(Payment).filter(Payment.yk_payment_id == yk_id).first()
    if not record:
        logger.warning(f"Webhook для неизвестного платежа {yk_id}")
        # Отвечаем 200, чтобы ЮКасса не ретраила
        return {"ok": True, "ignored": "unknown payment"}

    metadata = getattr(yk, "metadata", None) or {}
    user_id_meta = metadata.get("user_id")
    plan_meta = metadata.get("plan")
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        return {"ok": True, "ignored": "user not found"}

    # Активируем тариф
    if yk.status == "succeeded" and record.status != "succeeded":
        record.status = "succeeded"
        record.paid_at = datetime.utcnow()
        if plan_meta in PLAN_PRICES:
            user.plan = plan_meta
            user.plan_paid_at = datetime.utcnow()
        db.commit()
        logger.info(f"Платёж {yk_id} успешен: user={user.email}, plan={plan_meta}")
        return {"ok": True}

    if yk.status == "canceled" and record.status != "canceled":
        record.status = "canceled"
        db.commit()
        logger.info(f"Платёж {yk_id} отменён: user={user.email}")
        return {"ok": True}

    return {"ok": True, "status": yk.status}


@router.get("/status/{yk_payment_id}")
def get_payment_status(
    yk_payment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Проверка статуса платежа — для polling на странице payment-success."""
    record = db.query(Payment).filter(
        Payment.yk_payment_id == yk_payment_id,
        Payment.user_id == user.id,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    return {
        "status": record.status,
        "plan": record.plan,
        "amount": record.amount,
        "paid_at": record.paid_at.isoformat() if record.paid_at else None,
    }


@router.get("/admin/list")
def admin_list_payments(token: str = "", db: Session = Depends(get_db)):
    """Список платежей для админки."""
    if token != os.getenv("ADMIN_TOKEN", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = db.query(Payment).order_by(Payment.created_at.desc()).limit(200).all()
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "yk_payment_id": p.yk_payment_id,
            "plan": p.plan,
            "amount": p.amount,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in rows
    ]
