"""Evaluation harness.

Two layers, both real runs:
  * Server contract cases (S*): call the MCP server over stdio exactly as the agent does.
    No model is involved, so these are deterministic.
  * Agent cases (A*): run the full LangGraph agent on an open-weights model. The officer gate
    is answered by the harness and logged as "EVAL HARNESS (simulated officer)".

Results go to evals/results/<name>.json and the table in EVALS.md is regenerated from them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config

RESULTS = config.REPO_ROOT / "evals" / "results"
HARNESS_OFFICER = "EVAL HARNESS (simulated officer)"


def _isolate(tag: str) -> Path:
    """Point state, log and outbox at a fresh temp folder for one eval session."""
    tmp = Path(tempfile.mkdtemp(prefix=f"extensiondesk-{tag}-"))
    os.environ["EXTENSIONDESK_STATE"] = str(tmp / "state")
    os.environ["EXTENSIONDESK_LOG"] = str(tmp / "tool_calls.jsonl")
    os.environ["EXTENSIONDESK_OUTBOX"] = str(tmp / "outbox")
    return tmp


# ----------------------------------------------------------------------------- server contract cases

async def run_server_cases() -> list[dict]:
    tmp = _isolate("server")
    env = {**os.environ, "EXTENSIONDESK_RUN_ID": "eval-server", "EXTENSIONDESK_HOME": str(config.home())}
    params = StdioServerParameters(command=sys.executable, args=["-m", "extensiondesk.server"], env=env)
    results: list[dict] = []

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name: str, args: dict) -> dict:
                res = await s.call_tool(name, args)
                text = res.content[0].text if res.content else "{}"
                try:
                    return json.loads(text)
                except ValueError:
                    return {"status": "error", "raw": text}

            def record(cid: str, title: str, passed: bool, detail: str):
                results.append({"id": cid, "layer": "server", "title": title, "pass": bool(passed), "detail": detail})

            prof = await call("get_cluster_profile", {"cluster_id": "C03"})
            rain = await call("get_rainfall_signal", {"cluster_id": "C03", "as_of": "2025-06-20"})
            base = {"cluster_id": "C03", "as_of": "2025-06-20", "action": "plant", "crops": ["maize"],
                    "message": "Rains are established. You can plant maize this week."}

            r1 = await call("draft_advisory", {**base, "reasons": [{"claim": "Rains are good", "source_ids": []}]})
            record("S01", "Draft with an unsourced reason is rejected", r1["status"] == "rejected",
                   "; ".join(r1.get("errors", []))[:200])

            r2 = await call("draft_advisory", {**base, "reasons": [{"claim": "NiMet says so", "source_ids": ["NIMET:2025-06"]}]})
            record("S02", "Draft citing a source no tool returned is rejected", r2["status"] == "rejected"
                   and any("never returned" in e for e in r2.get("errors", [])), "; ".join(r2.get("errors", []))[:200])

            c09 = await call("get_rainfall_signal", {"cluster_id": "C09", "as_of": "2025-06-20"})
            r3 = await call("draft_advisory", {**base, "cluster_id": "C09", "crops": ["maize"],
                                               "reasons": [{"claim": "Rains", "source_ids": ["RAIN:Z5:2025-06-20", "CAL:maize"]}]})
            record("S03", "'plant' is rejected when the rainfall verdict is insufficient_data (C09, feed gap)",
                   c09.get("rule_verdict") == "insufficient_data" and r3["status"] == "rejected"
                   and any("contradicts" in e for e in r3.get("errors", [])),
                   f"verdict={c09.get('rule_verdict')}, missing_days={len(c09.get('missing_days', []))}")

            await call("get_cluster_profile", {"cluster_id": "C05"})
            await call("get_rainfall_signal", {"cluster_id": "C05", "as_of": "2025-06-20"})
            r4 = await call("draft_advisory", {**base, "cluster_id": "C05", "crops": ["cowpea"],
                                               "reasons": [{"claim": "Rains", "source_ids": ["RAIN:Z3:2025-06-20", "CAL:cowpea"]}]})
            record("S04", "'plant' is rejected outside the crop's planting window (cowpea on 20 June)",
                   r4["status"] == "rejected" and any("outside the planting window" in e for e in r4.get("errors", [])),
                   "; ".join(r4.get("errors", []))[:200])

            ok = await call("draft_advisory", {**base, "reasons": [
                {"claim": "Onset established", "source_ids": [rain["source_id"]]},
                {"claim": "Inside the maize window", "source_ids": ["CAL:maize", prof["source_id"]]}]})
            draft_id = ok.get("draft_id")
            r5 = await call("release_advisory", {"draft_id": draft_id})
            record("S05", "A valid draft cannot be released before an officer decides",
                   ok["status"] == "ok" and r5["status"] == "rejected", f"draft={draft_id}, release={r5.get('status')}")

            r6 = await call("officer_record_decision", {"draft_id": draft_id, "officer_name": "agent", "decision": "approve"})
            record("S06", "The decision tool refuses a non-human approver name", r6["status"] == "rejected",
                   "; ".join(r6.get("errors", [])))

            dup = [i for i in prof.get("data_quality_issues", []) if "already registered" in i["issue"]]
            excluded = [h for h in prof.get("households", []) if h["excluded"]]
            record("S07", "Duplicate farmer ID is flagged and excluded from SMS recipients (C03)",
                   bool(dup) and bool(excluded), f"flagged={[d['household_id'] for d in dup]}")

            issues = []
            for cid in ("C01", "C02", "C05", "C07"):
                p = await call("get_cluster_profile", {"cluster_id": cid})
                issues += [i["issue"] for i in p.get("data_quality_issues", [])]
            want = ["no GPS point", "read as 'maize'", "acres", "could not be read"]
            record("S08", "Messy register rows are flagged, not silently fixed (GPS, misspelt crop, acres, bad date)",
                   all(any(w in i for i in issues) for w in want), f"{len(issues)} issues flagged")

            c10 = await call("get_rainfall_signal", {"cluster_id": "C10", "as_of": "2025-06-20"})
            record("S09", "Cluster with no rainfall feed returns insufficient_data, not a guess (C10, zone Z6)",
                   c10.get("available") is False and c10.get("rule_verdict") == "insufficient_data", c10.get("rule_reason", ""))

            record("S10", "Feed gap is detected and reported with the missing dates (C09, zone Z5)",
                   len(c09.get("missing_days", [])) >= 3, f"missing={c09.get('missing_days')}")

            d1 = await call("officer_record_decision", {"draft_id": draft_id, "officer_name": "Amina Yusuf (test officer)",
                                                        "decision": "approve"})
            rel1 = await call("release_advisory", {"draft_id": draft_id})
            rel2 = await call("release_advisory", {"draft_id": draft_id})
            record("S11", "Approved draft releases once; a second release is refused",
                   d1["status"] == "ok" and rel1["status"] == "ok" and rel2["status"] == "rejected",
                   f"first={rel1.get('status')}, second={rel2.get('status')}")

            await call("get_pest_alerts", {"cluster_id": "C07", "as_of": "2025-07-10"})  # issues PEST:PA-2025-019 (zone Z4)
            r12 = await call("draft_advisory", {**base, "action": "scout_and_spray_check",
                                                "message": "Scout maize whorls for armyworm before any spraying.",
                                                "reasons": [{"claim": "FAW alert", "source_ids": ["PEST:PA-2025-019"]}]})
            record("S12", "Evidence from another zone is rejected (Z4 pest alert cited for a Z2 cluster)",
                   r12["status"] == "rejected" and any("not Z2" in e for e in r12.get("errors", [])),
                   "; ".join(r12.get("errors", []))[:200])

            # Hausa: known gap, left unfixed on purpose (see EVALS.md)
            outbox = Path(os.environ["EXTENSIONDESK_OUTBOX"]) / str(draft_id)
            texts = [p.read_text(encoding="utf-8") for p in outbox.glob("*.txt")] if outbox.exists() else []
            ha = [t for t in texts if "LANGUAGE: ha" in t]
            record("S13", "Hausa-preferring households receive the advisory in Hausa",
                   bool(ha) and all("Hausa version needed" not in t for t in ha),
                   f"{len(ha)} of {len(texts)} recipients prefer Hausa; outbox marks them 'Hausa version needed'")

    log = [json.loads(x) for x in open(os.environ["EXTENSIONDESK_LOG"], encoding="utf-8")]
    complete = all({"ts", "run_id", "tool", "inputs", "output", "status"} <= set(e) for e in log)
    gated = [e for e in log if e["tool"] in ("officer_record_decision", "release_advisory")]
    results.append({"id": "S14", "layer": "server", "title": "Every tool call is logged with timestamp, inputs and output; gated calls name the approver",
                    "pass": complete and all("approver" in e for e in gated),
                    "detail": f"{len(log)} calls logged, {len(gated)} gated calls"})
    return results


# ----------------------------------------------------------------------------- agent cases

AGENT_CASES = [
    {"id": "A01", "cluster": "C03", "as_of": "2025-06-20",
     "title": "Onset established and inside window: drafts a sourced 'plant' advisory (C03)",
     "check": lambda drafts, tr: any(d["action"] == "plant" for d in drafts)},
    {"id": "A02", "cluster": "C03", "as_of": "2025-06-20",
     "title": "Same run: the fall armyworm alert PA-2025-014 is acted on (scout draft, citation or follow-up)",
     "check": lambda drafts, tr: _cites(drafts, tr, "PEST:PA-2025-014")},
    {"id": "A03", "cluster": "C09", "as_of": "2025-06-20",
     "title": "Rainfall feed gap: no 'plant' advice; drafts no_advice_insufficient_data or hold (C09)",
     "check": lambda drafts, tr: bool(drafts) and all(d["action"] in ("no_advice_insufficient_data", "hold") for d in drafts)},
    {"id": "A04", "cluster": "C10", "as_of": "2025-06-20",
     "title": "No rainfall feed for the zone: says so instead of advising (C10)",
     "check": lambda drafts, tr: bool(drafts) and all(d["action"] in ("no_advice_insufficient_data", "hold") for d in drafts)},
    {"id": "A05", "cluster": "C01", "as_of": "2025-05-12",
     "title": "Before onset, 11 dry days: advises 'hold', not 'plant' (C01)",
     "check": lambda drafts, tr: any(d["action"] == "hold" for d in drafts) and not any(d["action"] == "plant" for d in drafts)},
    {"id": "A06", "cluster": "C07", "as_of": "2025-07-10",
     "title": "High fall armyworm alert: drafts scout_and_spray_check citing PA-2025-019 (C07)",
     "check": lambda drafts, tr: any(d["action"] == "scout_and_spray_check" and
                                     any("PEST:PA-2025-019" in r["source_ids"] for r in d["reasons"]) for d in drafts)},
    {"id": "A07", "cluster": "C03", "as_of": "2025-06-20",
     "title": "Same C03 run: uses the officer's field note (fertiliser delay) via the borrowed Filesystem server",
     "check": lambda drafts, tr: _cites(drafts, tr, "NOTE:") or any(t["tool"] == "read_text_file" for t in tr)},
    {"id": "A08", "cluster": "*", "as_of": "*",
     "title": "Every agent run finished inside the step limit with at least one accepted draft",
     "check": None},
]


def _cites(drafts, transcript, prefix: str) -> bool:
    in_drafts = any(any(sid.startswith(prefix) for r in d["reasons"] for sid in r["source_ids"]) for d in drafts)
    in_followups = any(t["tool"] == "schedule_follow_up" and isinstance(t["result"], dict) and t["result"].get("status") == "ok"
                       and any(str(s).startswith(prefix) for s in t["args"].get("source_ids", [])) for t in transcript)
    return in_drafts or in_followups


async def run_agent_cases(model: str | None, use_filesystem: bool = True) -> list[dict]:
    from .agent import MAX_STEPS_PER_CLUSTER
    from .runner import auto_officer, run_desk

    _isolate("agent")
    runs: dict[tuple[str, str], dict] = {}
    results = []
    for case in AGENT_CASES:
        if case["check"] is None:
            continue
        key = (case["cluster"], case["as_of"])
        if key not in runs:
            t0 = time.perf_counter()
            try:
                res = await run_desk([case["cluster"]], case["as_of"], model, auto_officer(HARNESS_OFFICER),
                                     use_filesystem=use_filesystem,
                                     run_id=f"eval-{case['cluster']}-{case['as_of']}-{int(time.time())}")
                res["error"] = None
            except Exception as e:  # a crashed run is a failed case, not a crashed harness
                res = {"drafts": [], "transcript": [], "error": repr(e), "steps": 0}
            res["case_seconds"] = round(time.perf_counter() - t0, 1)
            runs[key] = res
        res = runs[key]
        drafts, tr = res.get("drafts", []), res.get("transcript", [])
        passed = res.get("error") is None and bool(case["check"](drafts, tr))
        rejections = sum(1 for t in tr if t["tool"] == "draft_advisory" and isinstance(t["result"], dict)
                         and t["result"].get("status") == "rejected")
        results.append({"id": case["id"], "layer": "agent", "title": case["title"], "pass": passed,
                        "detail": (f"drafts={[d['action'] for d in drafts]}, tool_calls={len(tr)}, "
                                   f"server_rejections={rejections}, {res.get('case_seconds')}s"
                                   + (f", error={res['error'][:120]}" if res.get("error") else "")),
                        "run_id": res.get("run_id")})
    ok_runs = [r for r in runs.values() if r.get("error") is None and r.get("drafts")
               and r.get("steps", 0) < MAX_STEPS_PER_CLUSTER]
    results.append({"id": "A08", "layer": "agent", "title": AGENT_CASES[-1]["title"],
                    "pass": len(ok_runs) == len(runs), "detail": f"{len(ok_runs)} of {len(runs)} runs"})
    return sorted(results, key=lambda r: r["id"])


# ----------------------------------------------------------------------------- reporting

def save(name: str, results: list[dict], meta: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.json"
    path.write_text(json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    return path


def render_markdown() -> str:
    lines = []
    for f in sorted(RESULTS.glob("*.json")):
        blob = json.loads(f.read_text(encoding="utf-8"))
        meta, res = blob["meta"], blob["results"]
        passed = sum(r["pass"] for r in res)
        lines.append(f"### {meta.get('label', f.stem)}\n")
        lines.append(f"Run {meta.get('finished_at')} | model: `{meta.get('model') or 'none'}` | "
                     f"{passed}/{len(res)} passed\n")
        lines.append("| ID | Test | Result | Detail |\n|---|---|---|---|")
        for r in res:
            detail = str(r["detail"]).replace("|", "/").replace("\n", " ")
            lines.append(f"| {r['id']} | {r['title']} | {'PASS' if r['pass'] else 'FAIL'} | {detail} |")
        lines.append("")
    return "\n".join(lines) if lines else "_No results yet. Run `uv run extensiondesk evals`._\n"


def update_evals_md() -> None:
    path = config.REPO_ROOT / "EVALS.md"
    text = path.read_text(encoding="utf-8")
    start, end = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
    a, b = text.index(start) + len(start), text.index(end)
    path.write_text(text[:a] + "\n" + render_markdown() + "\n" + text[b:], encoding="utf-8")


def meta(model: str | None, label: str) -> dict:
    return {"label": label, "model": model, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": sys.version.split()[0], "platform": sys.platform}
