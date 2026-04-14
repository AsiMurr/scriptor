from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import datetime
from database import Transcription, User

# Лимиты по тарифам (в минутах)
PLAN_LIMITS = {
    "guest": 10,
    "free": 20,
    "standard": 300,   # 5 часов
    "pro": float("inf"),
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
    if limit == float("inf"):
        return float("inf")
    used = get_used_minutes_this_month(user.id, db)
    return max(0.0, limit - used)


def check_quota(user: User, db: Session):
    remaining = get_remaining_minutes(user, db)
    if remaining <= 0:
        raise Exception(
            f"Лимит исчерпан. Ваш тариф «{user.plan}» включает "
            f"{PLAN_LIMITS[user.plan]} мин/мес. Обновите тариф для продолжения."
        )
    return remaining
