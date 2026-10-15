"""Paths and rules. Everything is resolved from EXTENSIONDESK_HOME (default: repo root)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def home() -> Path:
    return Path(os.environ.get("EXTENSIONDESK_HOME", REPO_ROOT)).resolve()


def data_dir() -> Path:
    return home() / "data"


def desk_dir() -> Path:
    return home() / "desk"


def outbox_dir() -> Path:
    return Path(os.environ.get("EXTENSIONDESK_OUTBOX", desk_dir() / "outbox"))


def notes_dir() -> Path:
    return desk_dir() / "officer_notes"


def state_dir() -> Path:
    p = Path(os.environ.get("EXTENSIONDESK_STATE", home() / "state"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path() -> Path:
    p = Path(os.environ.get("EXTENSIONDESK_LOG", home() / "logs" / "tool_calls.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def run_id() -> str:
    return os.environ.get("EXTENSIONDESK_RUN_ID", "adhoc")


@lru_cache(maxsize=1)
def rules() -> dict:
    path = Path(os.environ.get("EXTENSIONDESK_RULES", home() / "rules.yaml"))
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
