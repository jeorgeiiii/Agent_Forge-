"""Cluster 11 - run events: ingest from the worker, read back, price LLM calls.

Event types written by the worker:
  run.started, run.paused, run.resumed, run.finished
  node.started, node.finished, node.skipped
  agent.step, llm.call, tool.call, subagent.finished
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import RunEventRecord, RunRecord

MAX_EVENT_DATA_CHARS = 20_000


@lru_cache
def _price_table() -> dict[str, dict[str, float]]:
    try:
        table = json.loads(get_settings().model_prices or "{}")
        return table if isinstance(table, dict) else {}
    except json.JSONDecodeError:
        return {}


def price_llm_call(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost of one call from the configured price table (None if the model is unpriced)."""
    price = _price_table().get(model or "")
    if not price:
        return None
    cost = input_tokens * float(price.get("input", 0)) / 1e6 + output_tokens * float(price.get("output", 0)) / 1e6
    return round(cost, 8)


def _shrink(data: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(data, default=str)
    if len(text) <= MAX_EVENT_DATA_CHARS:
        return data
    return {"truncated": True, "preview": text[:MAX_EVENT_DATA_CHARS]}


def ingest_events(db: Session, run: RunRecord, events: list[dict[str, Any]], *, source: str = "",
                  commit: bool = True) -> int:
    """Store worker events. Idempotent: an event with an already-stored seq is skipped.

    With commit=False the caller commits, so a run's final status and its last
    events become visible together (the live stream never ends early).
    """
    existing = set(db.scalars(
        select(RunEventRecord.seq).where(RunEventRecord.run_id == run.id, RunEventRecord.source == source,
                                         RunEventRecord.seq.in_([int(e.get("seq", -1)) for e in events]))
    ))
    added = 0
    for event in sorted(events, key=lambda e: int(e.get("seq", 0))):
        seq = int(event.get("seq", -1))
        if seq < 0 or seq in existing:
            continue
        data = dict(event.get("data") or {})
        if event.get("type") == "llm.call":
            data["cost_usd"] = price_llm_call(data.get("model"), int(data.get("input_tokens", 0)),
                                              int(data.get("output_tokens", 0)))
        db.add(RunEventRecord(run_id=run.id, source=source[:250], seq=seq, type=str(event.get("type", "unknown"))[:40],
                              node_id=event.get("node_id"), data=_shrink(data), at=event.get("at")))
        existing.add(seq)
        added += 1
    if not commit:
        return added
    try:
        db.commit()
    except IntegrityError:  # concurrent duplicate delivery
        db.rollback()
        return 0
    return added


def list_events(db: Session, run_id: int, after_id: int = 0, limit: int = 500) -> list[RunEventRecord]:
    return list(db.scalars(
        select(RunEventRecord)
        .where(RunEventRecord.run_id == run_id, RunEventRecord.id > after_id)
        .order_by(RunEventRecord.id)
        .limit(max(1, min(limit, 1000)))
    ))


def event_to_dict(event: RunEventRecord) -> dict[str, Any]:
    return {"id": event.id, "source": event.source, "seq": event.seq, "type": event.type, "node_id": event.node_id,
            "at": event.at, "data": event.data}


def run_cost(db: Session, run_id: int) -> float | None:
    """Total USD of a run's priced LLM calls (None if none were priced).

    Summed from the event log on read, so concurrent writers (parent run and
    sub-agents) can never lose an increment.
    """
    costs = [
        (e.data or {}).get("cost_usd")
        for e in db.scalars(select(RunEventRecord).where(RunEventRecord.run_id == run_id, RunEventRecord.type == "llm.call"))
    ]
    priced = [c for c in costs if c is not None]
    return round(sum(priced), 8) if priced else None
