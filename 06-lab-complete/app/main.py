"""
Production AI Agent — Part 6 Final Project.

    uvicorn app.main:app --workers 2
    python check_production_ready.py
"""
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.log import get_logger
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask

logger = get_logger(__name__)
STATE = {"shutting_down": False, "started_at": time.time()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: KHÔNG ping Redis ở đây — app phải lên được cả khi Redis chưa sẵn sàng
    logger.info("startup", extra={"fields": {
        "environment": settings.environment,
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "api_key_configured": bool(settings.agent_api_key),
    }})
    yield
    # Shutdown (graceful, tiêu chí 11): uvicorn nhận SIGTERM → ngừng nhận kết nối mới,
    # chờ request đang chạy xong → mới tới đây. Ta chỉ đánh dấu và dọn tài nguyên.
    STATE["shutting_down"] = True
    logger.info("shutdown", extra={"fields": {"uptime_s": round(time.time() - STATE["started_at"], 1)}})
    r.close()


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    logger.info("request", extra={"fields": {
        "request_id": request_id, "method": request.method, "path": request.url.path,
        "status": response.status_code, "ms": ms,
    }})
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Ghi traceback vào log (1 dòng JSON), KHÔNG trả stack trace cho client
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/")
def root():
    return {"service": "production-ai-agent", "docs": "/docs", "health": "/health", "ready": "/ready"}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200 — không phụ thuộc Redis."""
    return {"status": "ok", "uptime_s": round(time.time() - STATE["started_at"], 1)}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và chưa bắt đầu tắt."""
    redis_ok = redis_healthy()
    if STATE["shutting_down"] or not redis_ok:
        return JSONResponse(status_code=503, content={
            "status": "not_ready", "redis": redis_ok, "shutting_down": STATE["shutting_down"]})
    return {"status": "ready", "redis": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)   # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()              # 402 khi hết budget — kiểm TRƯỚC khi tốn tiền gọi LLM

    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost, 8)},
    }
