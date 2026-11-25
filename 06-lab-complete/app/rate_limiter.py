"""
Rate limiting (tiêu chí 7) — sliding window 60 giây, state trong Redis.

Mỗi API key có một sorted set: member = 1 request, score = thời điểm (giây).
Vì state nằm trong Redis nên mọi worker / instance dùng chung và vẫn còn sau khi restart.
"""
import hashlib
import math
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.log import get_logger
from app.redis_client import r

WINDOW_SECONDS = 60
logger = get_logger(__name__)


def _bucket(key: str) -> str:
    # Không lưu API key thô trong Redis, chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex}"
    try:
        # MULTI/EXEC: dọn → ghi → đếm trong 1 transaction, nhiều worker gọi cùng lúc vẫn đếm đúng
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)  # bỏ request ngoài cửa sổ
        pipe.zadd(bucket, {member: now})                        # ghi request này
        pipe.zcard(bucket)                                       # đếm request trong cửa sổ
        pipe.expire(bucket, WINDOW_SECONDS + 5)
        _, _, count, _ = pipe.execute()

        if count > limit:
            r.zrem(bucket, member)  # request bị chặn không tính vào quota
            oldest = r.zrange(bucket, 0, 0, withscores=True)
            retry_after = WINDOW_SECONDS
            if oldest:
                retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now))
            logger.warning("rate_limited", extra={"fields": {"limit": limit, "retry_after": retry_after}})
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} requests per minute",
                headers={"Retry-After": str(retry_after)},
            )
    except redis.RedisError:
        logger.error("redis_unavailable", extra={"fields": {"where": "rate_limiter"}})
        raise HTTPException(status_code=503, detail="Rate limiter unavailable")
