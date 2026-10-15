import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Each test gets its own database, log and outbox so tests never touch the real desk."""
    monkeypatch.setenv("EXTENSIONDESK_HOME", str(REPO))
    monkeypatch.setenv("EXTENSIONDESK_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("EXTENSIONDESK_LOG", str(tmp_path / "tool_calls.jsonl"))
    monkeypatch.setenv("EXTENSIONDESK_OUTBOX", str(tmp_path / "outbox"))
    yield tmp_path


def has_npx() -> bool:
    return bool(shutil.which("npx") or shutil.which("npx.cmd"))
