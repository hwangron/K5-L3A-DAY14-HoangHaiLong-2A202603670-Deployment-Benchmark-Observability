"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng
  • Tổng chi phí đã đạt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
"""
from datetime import datetime, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.log import get_logger
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

logger = get_logger(__name__)


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Mock LLM không trả số token → ước lượng ~4 ký tự / token."""
    return max(1, len(text) // 4)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily_key, monthly_key = _keys()
    try:
        daily, monthly = (float(v or 0) for v in r.mget(daily_key, monthly_key))
    except redis.RedisError:
        logger.error("redis_unavailable", extra={"fields": {"where": "cost_guard"}})
        raise HTTPException(status_code=503, detail="Cost guard unavailable")

    if daily >= settings.daily_budget_usd or monthly >= settings.monthly_budget_usd:
        logger.warning("budget_exceeded", extra={"fields": {
            "daily_usd": round(daily, 6), "monthly_usd": round(monthly, 6),
            "daily_budget": settings.daily_budget_usd, "monthly_budget": settings.monthly_budget_usd,
        }})
        raise HTTPException(status_code=402, detail="Budget exceeded")


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    try:
        pipe = r.pipeline()
        pipe.incrbyfloat(daily_key, cost)
        pipe.expire(daily_key, 2 * 24 * 3600)
        pipe.incrbyfloat(monthly_key, cost)
        pipe.expire(monthly_key, 32 * 24 * 3600)
        pipe.execute()
    except redis.RedisError:
        # LLM đã trả lời rồi → không fail request, chỉ ghi log
        logger.error("redis_unavailable", extra={"fields": {"where": "record_cost"}})
    return cost
