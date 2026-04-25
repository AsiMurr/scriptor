from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import datetime
from database import Transcription, User

# Лимиты по тарифам (в минутах)
PLAN_LIMITS = {
    "guest": 10,
    "free": 60,        # 60 минут
    "standard": 600,   # 10 часов
    "pro": 1800,       # 30 часов
}

PLAN_NAMES = {
    "guest": "Гостевой",
    "free": "Бесплатный",
    "standard": "Стандарт",
    "pro": "Про",
}

PLAN_FEATURES = {
    "guest":    {"record": False, "edit": False, "speakers": 0,  "export": ["txt"],                         "history_days": 0},
    "free":     {"record": False, "edit": False, "speakers": 0,  "export": ["txt"],                         "history_days": 0},
    "standard": {"record": True,  "edit": True,  "speakers": 5,  "export": ["txt", "docx"],                "history_days": 90},
    "pro":      {"record": True,  "edit": True,  "speakers": 10, "export": ["txt", "docx", "pdf", "xlsx"], "history_days": None},
}


def get_used_minutes_this_month(user_id: int, db: Session) -> float:
    now = datetime.utcnow()
    result = db.query(func.sum(Transcription.duration_seconds)).filter(
        Transcription.user_id == user_id,
        extract("year", Transcription.created_at) == now.year,
        extract("month", Transcription.created_at) == now.month,
    ).scalar()
    return (result or 0.0) / 60.0


def get_remaining_minutes(user: User, db: Session) -> float:
    limit = PLAN_LIMITS[user.plan]
    used = get_used_minutes_this_month(user.id, db)
    return max(0.0, limit - used)


def check_quota(user: User, db: Session):
    remaining = get_remaining_minutes(user, db)
    if remaining <= 0:
        limits_text = {"free": "60 мин", "standard": "10 часов", "pro": "30 часов"}
        raise Exception(
            f"Лимит исчерпан. Ваш тариф «{PLAN_NAMES.get(user.plan, user.plan)}» включает "
            f"{limits_text.get(user.plan, str(PLAN_LIMITS[user.plan]) + ' мин')}/мес. "
            f"Обновите тариф для продолжения."
        )
    return remaining
