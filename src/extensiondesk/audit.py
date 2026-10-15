"""Audit log: every MCP tool call is written to logs/tool_calls.jsonl.

One JSON object per line with timestamp, run ID, tool, inputs, output, status,
duration and, for gated tools, the named officer who approved.
"""

from __future__ import annotations

import functools
import inspect
import json
import time

from . import config
from .store import now

GATED_TOOLS = {"officer_record_decision", "release_advisory"}


def _default(o):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


def write(entry: dict) -> None:
    with config.log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=_default, ensure_ascii=False) + "\n")


def logged(fn):
    """Decorator for MCP tool functions. Keeps the signature so FastMCP can build the schema."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        inputs = dict(bound.arguments)
        t0 = time.perf_counter()
        entry = {"ts": now(), "run_id": config.run_id(), "tool": fn.__name__, "inputs": inputs}
        try:
            out = fn(*args, **kwargs)
            entry.update(status=out.get("status", "ok") if isinstance(out, dict) else "ok", output=out)
            return out
        except Exception as e:  # logged, then re-raised so the client sees the error
            entry.update(status="error", output={"error": str(e)})
            raise
        finally:
            entry["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if fn.__name__ in GATED_TOOLS:
                out = entry.get("output") or {}
                entry["approver"] = inputs.get("officer_name") or (out.get("approved_by") if isinstance(out, dict) else None)
            write(entry)

    return wrapper
