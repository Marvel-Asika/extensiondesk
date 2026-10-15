"""Command line for ExtensionDesk.

  extensiondesk demo                 the demo run (C03 and C09, 20 June 2025), officer answers at the prompt
  extensiondesk run --clusters C03,C07 --as-of 2025-07-10
  extensiondesk evals                server contract cases + agent cases on the open-weights model
  extensiondesk evals --server-only  just the deterministic server cases (no model needed)
  extensiondesk check                checks Ollama, the model and Node.js
  extensiondesk reset                clears the local database, log and outbox
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import urllib.request

from . import config


def _model(args) -> str:
    return args.model or os.environ.get("EXTENSIONDESK_MODEL", "qwen3:8b")


def cmd_run(args, clusters: list[str], as_of: str) -> None:
    from .runner import interactive_officer, run_desk
    asyncio.run(run_desk(clusters, as_of, _model(args), interactive_officer(args.officer),
                         use_filesystem=not args.no_filesystem))


def cmd_evals(args) -> None:
    from . import evals
    from .runner import console
    server = asyncio.run(evals.run_server_cases())
    p = evals.save("server-contract", server, evals.meta(None, "Server contract cases (no model, deterministic)"))
    console.print(f"server cases: {sum(r['pass'] for r in server)}/{len(server)} passed -> {p}")
    if not args.server_only:
        model = _model(args)
        agent = asyncio.run(evals.run_agent_cases(model, use_filesystem=not args.no_filesystem))
        p = evals.save(f"agent-{model}", agent, evals.meta(model, f"Agent cases on {model}"))
        log = os.environ.get("EXTENSIONDESK_LOG")
        if log and os.path.exists(log):  # keep the full tool-call log of the eval runs as evidence
            shutil.copyfile(log, p.with_suffix(".tool_calls.jsonl"))
        console.print(f"agent cases on {model}: {sum(r['pass'] for r in agent)}/{len(agent)} passed -> {p}")
    evals.update_evals_md()
    console.print("EVALS.md updated.")


def cmd_check(args) -> None:
    ok = True
    base = os.environ.get("EXTENSIONDESK_BASE_URL", "http://localhost:11434/v1")
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/models", timeout=5) as r:
            models = [m["id"] for m in json.load(r).get("data", [])]
        print(f"[ok] model server at {base}: {', '.join(models) or 'no models'}")
        if _model(args) not in models:
            ok = False
            print(f"[!!] model {_model(args)} not found. Run: ollama pull {_model(args)}")
    except Exception as e:
        ok = False
        print(f"[!!] cannot reach {base} ({e}). Is Ollama running?")
    if shutil.which("npx") or shutil.which("npx.cmd"):
        print("[ok] npx found (borrowed Filesystem MCP server)")
    else:
        ok = False
        print("[!!] npx not found. Install Node.js LTS, or run with --no-filesystem")
    print("ready" if ok else "not ready")
    sys.exit(0 if ok else 1)


def cmd_reset(args) -> None:
    for p in (config.state_dir() / "extensiondesk.db", config.log_path()):
        if p.exists():
            p.unlink()
    out = config.outbox_dir()
    if out.exists():
        for d in out.iterdir():
            if d.is_dir():
                shutil.rmtree(d)
    print("cleared database, log and outbox")


def main() -> None:
    ap = argparse.ArgumentParser(prog="extensiondesk", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model name (default qwen3:8b or $EXTENSIONDESK_MODEL)")
    ap.add_argument("--no-filesystem", action="store_true", help="skip the borrowed Filesystem MCP server")
    ap.add_argument("--officer", help="pre-fill the officer name at the gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    r = sub.add_parser("run")
    r.add_argument("--clusters", required=True, help="comma-separated, e.g. C03,C07")
    r.add_argument("--as-of", required=True, help="YYYY-MM-DD (data covers 2025-05-01 to 2025-08-31)")
    e = sub.add_parser("evals")
    e.add_argument("--server-only", action="store_true")
    sub.add_parser("check")
    sub.add_parser("reset")
    args = ap.parse_args()
    if args.cmd == "demo":
        cmd_run(args, ["C03", "C09"], "2025-06-20")
    elif args.cmd == "run":
        cmd_run(args, [c.strip() for c in args.clusters.split(",")], args.as_of)
    elif args.cmd == "evals":
        cmd_evals(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "reset":
        cmd_reset(args)


if __name__ == "__main__":
    main()
