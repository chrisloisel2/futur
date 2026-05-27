import json
import logging
import sys
from typing import Any, Dict

DEFAULT_LEVEL = logging.INFO

class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - thin wrapper
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage() if not isinstance(record.msg, dict) else None,
            "time": self.formatTime(record, self.datefmt),
        }
        if isinstance(record.msg, dict):
            payload.update(record.msg)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("run_id", "symbol", "mode"):
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)

def configure_logging(level: int = DEFAULT_LEVEL) -> logging.Logger:  # pragma: no cover - setup helper
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    return logger

def get_logger(name: str) -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        configure_logging()
    return logging.getLogger(name)
