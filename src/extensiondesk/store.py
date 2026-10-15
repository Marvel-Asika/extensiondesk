"""SQLite store for evidence, drafts, officer decisions, releases and follow-ups.

The evidence table is what makes citations checkable: every source ID a read tool
hands to the model is written here first, and a draft may only cite IDs that exist.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
  source_id TEXT PRIMARY KEY, kind TEXT, citation TEXT, payload TEXT, run_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS drafts (
  draft_id TEXT PRIMARY KEY, run_id TEXT, cluster_id TEXT, as_of TEXT, action TEXT, crops TEXT,
  message TEXT, reasons TEXT, recipients TEXT, data_issues TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT, officer TEXT, decision TEXT,
  final_message TEXT, comment TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS releases (
  draft_id TEXT PRIMARY KEY, officer TEXT, channel TEXT, recipients INTEGER, released_at TEXT);
CREATE TABLE IF NOT EXISTS followups (
  followup_id TEXT PRIMARY KEY, run_id TEXT, cluster_id TEXT, due_date TEXT, reason TEXT,
  source_ids TEXT, created_at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    con = sqlite3.connect(config.state_dir() / "extensiondesk.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def put_evidence(source_id: str, kind: str, citation: str, payload: dict) -> str:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?)",
                    (source_id, kind, citation, json.dumps(payload, default=str), config.run_id(), now()))
    return source_id


def get_evidence(source_ids: list[str]) -> dict[str, dict]:
    if not source_ids:
        return {}
    with db() as con:
        q = ",".join("?" * len(source_ids))
        rows = con.execute(f"SELECT * FROM evidence WHERE source_id IN ({q})", source_ids).fetchall()
    return {r["source_id"]: {**dict(r), "payload": json.loads(r["payload"])} for r in rows}


def next_id(table: str, prefix: str, col: str) -> str:
    with db() as con:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return f"{prefix}-{n + 1:04d}"


def row_to_draft(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("crops", "reasons", "recipients", "data_issues"):
        d[k] = json.loads(d[k])
    return d
