"""Cluster 20: request ids, structured logs, DB error handling, transactions, pagination."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import SessionLocal
from app.main import app
from app.models import GraphRecord
from app.utils import JsonFormatter, paginate, request_id_var, with_transaction


def test_request_id_is_generated_echoed_and_validated(client):
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32
    assert client.get("/health", headers={"X-Request-ID": "trace-abc.123"}).headers["x-request-id"] == "trace-abc.123"
    injected = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["x-request-id"]
    assert injected != "bad id\nwith newline" and len(injected) == 32  # no log injection via the header


def test_access_log_line_has_structured_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="agentforge.access"):
        client.get("/node-types", headers={"X-Request-ID": "req-42"})
    record = next(r for r in caplog.records if r.name == "agentforge.access" and getattr(r, "path", "") == "/node-types")
    assert record.method == "GET" and record.status == 200 and record.duration_ms >= 0
    line = json.loads(JsonFormatter().format(record))
    assert line["event"] == "request" and line["path"] == "/node-types" and line["status"] == 200


def test_json_formatter_includes_request_id_and_exceptions():
    token = request_id_var.set("rid-7")
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "failed %s", ("x",),
                                                       exc_info=__import__("sys").exc_info(), extra={"run_id": 9})
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "failed x" and line["request_id"] == "rid-7" and line["run_id"] == 9
    assert line["level"] == "ERROR" and "ValueError: boom" in line["exc"]


def test_database_errors_become_clear_http_errors(client):
    def conflict():
        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed: users.email"))

    def busy():
        raise OperationalError("SELECT ...", {}, Exception("database is locked"))

    app.add_api_route("/_test/conflict", conflict)
    app.add_api_route("/_test/busy", busy)
    try:
        r = client.get("/_test/conflict", headers={"X-Request-ID": "rq-1"})
        assert r.status_code == 409 and r.json()["request_id"] == "rq-1"
        assert "UNIQUE" not in r.text  # internal details stay in the logs
        r = client.get("/_test/busy")
        assert r.status_code == 503 and r.headers["retry-after"] == "2"
    finally:
        app.router.routes = [route for route in app.router.routes if not getattr(route, "path", "").startswith("/_test/")]


def test_with_transaction_rolls_back_everything_on_error():
    with SessionLocal() as db:
        before = len(db.scalars(select(GraphRecord)).all())
        try:
            with with_transaction(db):
                db.add(GraphRecord(name="tx-1", definition={}))
                db.add(GraphRecord(name="tx-2", definition={}))
                db.flush()
                raise RuntimeError("second step failed")
        except RuntimeError:
            pass
        assert len(db.scalars(select(GraphRecord)).all()) == before  # neither row was kept


def test_paginate_walks_all_rows_once():
    with SessionLocal() as db, with_transaction(db):
        for i in range(7):
            db.add(GraphRecord(name=f"page-{i}", definition={}))
    with SessionLocal() as db:
        query = select(GraphRecord).where(GraphRecord.name.startswith("page-"))
        seen, cursor = [], None
        while True:
            rows, cursor = paginate(db, query, GraphRecord.id, before_id=cursor, limit=3)
            seen += [r.id for r in rows]
            if not cursor:
                break
    assert len(seen) == 7 and seen == sorted(seen, reverse=True) and len(set(seen)) == 7
