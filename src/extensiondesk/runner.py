"""Runs the graph, prints every tool call, and handles the officer gate."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Callable

from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .agent import DeskTools, build_graph, ensure_model, make_llm

console = Console()

OfficerPolicy = Callable[[dict], dict]


def _short(result) -> str:
    if not isinstance(result, dict):
        return str(result)[:160]
    keys = ["status", "source_id", "rule_verdict", "onset_date", "rain_30d_mm", "anomaly_30d_pct",
            "current_dry_spell_days", "draft_id", "state", "recipients", "followup_id", "errors"]
    parts = []
    for k in keys:
        if k in result and result[k] not in (None, [], ""):
            v = result[k]
            if k == "errors":
                v = "; ".join(v)[:220]
            parts.append(f"{k}={v}")
    if "alerts" in result:
        parts.append("alerts=" + (",".join(a["source_id"] for a in result["alerts"]) or "none"))
    if "data_quality_issues" in result:
        parts.append(f"data_issues={len(result['data_quality_issues'])}")
    if "missing_days" in result and result["missing_days"]:
        parts.append(f"missing_days={len(result['missing_days'])}")
    return ", ".join(parts)[:400]


def printer(kind: str, p: dict) -> None:
    if kind == "cluster":
        console.rule(f"[bold]Cluster {p['cluster_id']}  as of {p['as_of']}")
    elif kind == "model":
        if p["tool_calls"]:
            console.print(f"[magenta]model[/] decides to call: {', '.join(p['tool_calls'])}")
        elif p["text"]:
            console.print(f"[magenta]model[/] {p['text'][:400]}")
    elif kind == "tool_call":
        console.print(f"  [cyan]-> {p['name']}[/] {json.dumps(p['args'], ensure_ascii=False)[:300]}")
    elif kind == "tool_result":
        r = p["result"]
        style = "red" if isinstance(r, dict) and r.get("status") in ("rejected", "error") else "green"
        console.print(f"  [{style}]<- {_short(r)}[/]")
    elif kind == "drafts":
        console.rule(f"[bold yellow]Officer gate: {p['count']} draft(s) waiting. Nothing has been sent.")
    elif kind == "decision":
        console.print(f"  [yellow]decision recorded[/] {_short(p['result'])}")
    elif kind == "release":
        console.print(f"  [bold green]RELEASED to outbox[/] {_short(p)} approved_by={p.get('approved_by')}")


def show_draft(payload: dict) -> None:
    d = payload["draft"]
    lines = [f"[bold]{d['draft_id']}[/]  cluster {d['cluster_id']}  as of {d['as_of']}",
             f"action: [bold]{d['action']}[/]   crops: {', '.join(d['crops'])}   recipients: {len(d['recipients'])}",
             "", f"[bold]SMS:[/] {d['message']}", "", "[bold]Why (with sources):[/]"]
    for r in d["reasons"]:
        lines.append(f" - {r['claim']}  [dim][{', '.join(r['source_ids'])}][/]")
    lines.append("")
    lines.append("[bold]Sources:[/]")
    for sid, cit in d.get("sources", {}).items():
        lines.append(f" {sid}: [dim]{cit}[/]")
    if d["data_issues"]:
        lines.append("")
        lines.append(f"[bold red]Data issues ({len(d['data_issues'])}):[/]")
        for i in d["data_issues"][:6]:
            lines.append(f" - {i['household_id']}: {i['issue']}")
    console.print(Panel("\n".join(lines), title="Draft for officer review", border_style="yellow"))


def interactive_officer(default_name: str | None = None) -> OfficerPolicy:
    def policy(payload: dict) -> dict:
        show_draft(payload)
        name = Prompt.ask("Officer name", default=default_name) if default_name else Prompt.ask("Officer name")
        choice = Prompt.ask("Decision: [a]pprove, [e]dit and approve, [r]eject", choices=["a", "e", "r"], default="a")
        decision = {"a": "approve", "e": "edit_and_approve", "r": "reject"}[choice]
        final = Prompt.ask("Edited SMS") if decision == "edit_and_approve" else None
        comment = Prompt.ask("Comment (optional)", default="")
        return {"officer": name, "decision": decision, "final_message": final, "comment": comment}
    return policy


def auto_officer(name: str, decision: str = "approve") -> OfficerPolicy:
    """Used by the eval harness only. The name makes it obvious in the logs that no human decided."""
    def policy(payload: dict) -> dict:
        show_draft(payload)
        console.print(f"[dim]{name} -> {decision}[/]")
        return {"officer": name, "decision": decision, "final_message": None, "comment": "automated eval run"}
    return policy


async def run_desk(clusters: list[str], as_of: str, model: str | None, officer: OfficerPolicy,
                   use_filesystem: bool = True, run_id: str | None = None, quiet: bool = False) -> dict:
    llm = make_llm(model)
    if not str(llm.model_name).startswith("scripted"):
        ensure_model(llm.model_name)
    run_id = run_id or f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    t0 = time.perf_counter()
    async with DeskTools(run_id, use_filesystem=use_filesystem) as tools:
        console.print(f"[dim]run {run_id} | model {llm.model_name} at {llm.openai_api_base} | "
                      f"tools for the model: {', '.join(t.name for t in tools.agent_tools)}[/]")
        graph = build_graph(llm, tools, on_event=None if quiet else printer)
        cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": 400}
        state = {"run_id": run_id, "as_of": as_of, "clusters": clusters, "idx": 0,
                 "transcript": [], "decisions": []}
        result = await graph.ainvoke(state, cfg)
        while result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            result = await graph.ainvoke(Command(resume=officer(payload)), cfg)
    result["run_id"] = run_id
    result["model"] = llm.model_name
    result["seconds"] = round(time.perf_counter() - t0, 1)
    if not quiet:
        summary(result)
    return result


def summary(result: dict) -> None:
    t = Table(title=f"Run {result['run_id']} ({result['model']}, {result['seconds']} s)")
    for c in ("draft", "cluster", "action", "officer", "decision", "released"):
        t.add_column(c)
    released = {r.get("draft_id") for r in result.get("releases", []) if isinstance(r, dict) and r.get("state") == "RELEASED"}
    decs = {d["draft_id"]: d for d in result.get("decisions", [])}
    for d in result.get("drafts", []):
        dec = decs.get(d["draft_id"], {})
        t.add_row(d["draft_id"], d["cluster_id"], d["action"], dec.get("officer", "-"),
                  dec.get("decision", "-"), "yes" if d["draft_id"] in released else "no")
    console.print(t)
    console.print("[dim]Every tool call is in logs/tool_calls.jsonl. Released SMS files are in desk/outbox/.[/]")
