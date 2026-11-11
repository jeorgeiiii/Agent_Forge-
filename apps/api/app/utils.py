"""Cluster 20 - small shared utilities: ids, time, pagination, transactions, structured logs, request ids."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def generate_id() -> str:
    return str(uuid.uuid4())


def get_current_timestamp() -> datetime:
    return datetime.now(timezone.utc)


def current_request_id() -> str:
    return request_id_var.get()


def accept_request_id(incoming: str | None) -> str:
    """Use the caller's X-Request-ID if it is sane (so traces join across services), else make one."""
    return incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex


def paginate(db: Session, query, id_column, *, before_id: int | None, limit: int, max_limit: int = 500):
    """Cursor pagination, newest first: returns (rows, next_cursor). Pass next_cursor back as before_id."""
    limit = max(1, min(limit, max_limit))
    if before_id:
        query = query.where(id_column < before_id)
    rows = list(db.scalars(query.order_by(id_column.desc()).limit(limit + 1)))
    page = rows[:limit]
    next_cursor = getattr(page[-1], id_column.key) if len(rows) > limit and page else None
    return page, next_cursor


@contextmanager
def with_transaction(db: Session) -> Iterator[Session]:
    """All writes inside commit together or roll back together."""
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


# ------------------------------------------------------------------ structured logging


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, request_id + any `extra` fields."""

    _RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in self._RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    """LOG_FORMAT=json for log pipelines (Loki, Datadog, CloudWatch ...); text for humans."""
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.addFilter(_RequestIdFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    if fmt == "json":
        logging.getLogger("uvicorn.access").disabled = True  # replaced by our structured access log


def log_structured_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """logger.info with machine-readable fields (become JSON keys when LOG_FORMAT=json)."""
    logger.info(event, extra={"event": event, **fields})


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.start) * 1000, 1)
