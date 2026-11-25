"""
Structured logging (tiêu chí 10): mỗi dòng log của app là đúng 1 JSON object.

Dùng: logger = get_logger(__name__); logger.info("request", extra={"fields": {...}})
"""
import json
import logging
import sys
from datetime import datetime, timezone

from app.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            # json.dumps escape xuống dòng → traceback vẫn nằm trên 1 dòng log
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_root = logging.getLogger("agent")
if not _root.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter())
    _root.addHandler(_handler)
    _root.setLevel(settings.log_level)
    _root.propagate = False  # không lẫn với log text của uvicorn


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name.split(".")[-1])
