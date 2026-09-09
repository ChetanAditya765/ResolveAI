import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "method", "path", "status_code", "latency_ms", "error_type"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        # Do not serialize exception args: SQL errors can contain requests and credentials.
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("resolveai")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
