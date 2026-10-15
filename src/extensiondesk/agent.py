"""The ExtensionDesk agent: a LangGraph graph with a human gate.

Shape:
    start_cluster -> agent <-> tools   (loop per cluster, model picks tools)
                  -> next_cluster -> ... -> collect_drafts -> officer_gate -> release -> END

The model only ever sees read tools plus draft_advisory and schedule_follow_up.
The officer tools (record decision, release) are called by graph code after
interrupt() has collected a decision from a named human.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from contextlib import AsyncExitStack
from typing import Any, Callable, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config
from .server import AGENT_TOOLS, OFFICER_TOOLS

FILESYSTEM_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"
FILESYSTEM_READ_TOOLS = {"list_directory", "read_text_file"}
MAX_STEPS_PER_CLUSTER = int(os.environ.get("EXTENSIONDESK_MAX_STEPS", "14"))

SYSTEM_PROMPT = """You are ExtensionDesk, an assistant to a two-person agricultural extension office in Ikara LGA, Kaduna State, Nigeria.
You prepare DRAFT SMS advisories for smallholder households. You never send anything: a named officer approves every draft.

For the cluster and date you are given, work like this:
1. Call get_cluster_profile to see crops, planting calendars and data-quality issues.
2. Call get_rainfall_signal for the same cluster and date. Its rule_verdict is computed from rules.yaml; respect it.
3. Call get_pest_alerts for the same cluster and date.
4. Optionally list and read the officer's field notes (list_directory / read_text_file) for local context. Cite a note as NOTE:<file name>.
5. Call draft_advisory once per distinct piece of advice (for example one 'plant' draft for maize and sorghum, and one 'scout_and_spray_check' draft if there is a pest alert).
   - Allowed actions: plant, hold, scout_and_spray_check, no_advice_insufficient_data.
   - Only use 'plant' when rule_verdict is plant_ok and the date is inside the crop's planting window. Cite RAIN:..., CAL:<crop> and HHREG:....
   - If rule_verdict is insufficient_data, draft no_advice_insufficient_data and say what data is missing.
   - Every reason must cite source IDs copied exactly from tool results. Never invent a source ID.
   - The message is a plain, practical SMS under 320 characters, in English, with no names or phone numbers.
6. If draft_advisory is rejected, read the errors, fix the draft and call it again.
7. If something needs checking in person (a pest alert, missing data, an unresolved duplicate), call schedule_follow_up with sources.
8. When the drafts for this cluster are done, reply with a two-line summary and stop calling tools."""


class DeskState(TypedDict, total=False):
    run_id: str
    as_of: str
    clusters: list[str]
    idx: int
    steps: int
    messages: list[BaseMessage]
    transcript: list[dict]
    drafts: list[dict]
    decisions: list[dict]
    releases: list[dict]


def make_llm(model: str | None = None):
    model = model or os.environ.get("EXTENSIONDESK_MODEL", "qwen3:8b")
    base_url = os.environ.get("EXTENSIONDESK_BASE_URL", "http://localhost:11434/v1")
    api_key = os.environ.get("EXTENSIONDESK_API_KEY", "ollama")
    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0, timeout=600)


def ensure_model(model: str) -> None:
    """If the model server is a local Ollama and the model is missing, pull it (one time)."""
    base = os.environ.get("EXTENSIONDESK_BASE_URL", "http://localhost:11434/v1")
    if "11434" not in base:
        return
    root = base.rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(root + "/api/tags", timeout=5) as r:
            names = {m["name"] for m in json.load(r).get("models", [])}
    except Exception as e:
        raise RuntimeError(f"Cannot reach Ollama at {root}. Install it from https://ollama.com and start it. ({e})")
    if model in names or f"{model}:latest" in names:
        return
    print(f"Pulling {model} into Ollama (one time, several GB)...", flush=True)
    req = urllib.request.Request(root + "/api/pull", data=json.dumps({"model": model, "stream": False}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        r.read()


def _content_to_json(content: Any) -> Any:
    if isinstance(content, list):
        content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    if isinstance(content, tuple):
        content = content[0]
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return content


def _strip_think(text: str) -> str:
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    return text.strip()


class DeskTools:
    """Opens both MCP servers over stdio and keeps the sessions for the whole run."""

    def __init__(self, run_id: str, use_filesystem: bool = True):
        self.run_id = run_id
        self.use_filesystem = use_filesystem
        self.stack = AsyncExitStack()
        self.agent_tools: list = []
        self.officer: dict[str, Any] = {}
        self.filesystem_enabled = False

    async def __aenter__(self):
        env = {**os.environ, "EXTENSIONDESK_RUN_ID": self.run_id, "EXTENSIONDESK_HOME": str(config.home())}
        own = StdioServerParameters(command=sys.executable, args=["-m", "extensiondesk.server"], env=env)
        r, w = await self.stack.enter_async_context(stdio_client(own))
        s = await self.stack.enter_async_context(ClientSession(r, w))
        await s.initialize()
        tools = await load_mcp_tools(s)
        self.agent_tools = [t for t in tools if t.name in AGENT_TOOLS]
        self.officer = {t.name: t for t in tools if t.name in OFFICER_TOOLS}

        if self.use_filesystem:
            npx = shutil.which("npx") or shutil.which("npx.cmd")
            if not npx:
                raise RuntimeError("npx (Node.js) is needed for the borrowed Filesystem MCP server. "
                                   "Install Node.js LTS, or pass --no-filesystem.")
            fs_args = ["-y", FILESYSTEM_PACKAGE, str(config.notes_dir())]
            if os.name == "nt":  # npx is a .cmd shim on Windows; run it through cmd.exe
                fs = StdioServerParameters(command="cmd", args=["/c", "npx", *fs_args])
            else:
                fs = StdioServerParameters(command=npx, args=fs_args)
            r2, w2 = await self.stack.enter_async_context(stdio_client(fs))
            s2 = await self.stack.enter_async_context(ClientSession(r2, w2))
            await s2.initialize()
            fs_tools = await load_mcp_tools(s2)
            self.agent_tools += [t for t in fs_tools if t.name in FILESYSTEM_READ_TOOLS]
            self.filesystem_enabled = True
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def officer_call(self, name: str, **args) -> Any:
        return _content_to_json(await self.officer[name].ainvoke(args))


def build_graph(llm, tools: DeskTools, on_event: Callable[[str, dict], None] | None = None):
    emit = on_event or (lambda kind, payload: None)
    by_name = {t.name: t for t in tools.agent_tools}
    bound = llm.bind_tools(tools.agent_tools)
    no_think = os.environ.get("EXTENSIONDESK_THINK", "0") != "1"

    def start_cluster(state: DeskState) -> DeskState:
        cid = state["clusters"][state["idx"]]
        emit("cluster", {"cluster_id": cid, "as_of": state["as_of"]})
        notes = f" The officer's field notes folder is: {config.notes_dir()}" if tools.filesystem_enabled else ""
        task = (f"Cluster {cid}, date {state['as_of']}. Prepare the draft advisories for this cluster.{notes}"
                + (" /no_think" if no_think else ""))
        return {"messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(task)], "steps": 0}

    async def agent(state: DeskState) -> DeskState:
        ai: AIMessage = await bound.ainvoke(state["messages"])
        if isinstance(ai.content, str):
            ai.content = _strip_think(ai.content)
        emit("model", {"text": ai.content, "tool_calls": [tc["name"] for tc in ai.tool_calls]})
        return {"messages": state["messages"] + [ai], "steps": state.get("steps", 0) + 1}

    async def run_tools(state: DeskState) -> DeskState:
        ai: AIMessage = state["messages"][-1]
        out: list[BaseMessage] = []
        transcript = list(state.get("transcript", []))
        for tc in ai.tool_calls:
            emit("tool_call", {"name": tc["name"], "args": tc["args"]})
            tool = by_name.get(tc["name"])
            if tool is None:
                result: Any = {"status": "error", "error": f"Tool {tc['name']} is not available to you."}
            else:
                try:
                    result = _content_to_json(await tool.ainvoke(tc["args"]))
                except Exception as e:  # tool errors go back to the model, not up the stack
                    result = {"status": "error", "error": str(e)}
            emit("tool_result", {"name": tc["name"], "result": result})
            transcript.append({"cluster": state["clusters"][state["idx"]], "tool": tc["name"],
                               "args": tc["args"], "result": result})
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            out.append(ToolMessage(content=text, tool_call_id=tc["id"], name=tc["name"]))
        return {"messages": state["messages"] + out, "transcript": transcript}

    def after_agent(state: DeskState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state.get("steps", 0) < MAX_STEPS_PER_CLUSTER:
            return "tools"
        return "next_cluster"

    def next_cluster(state: DeskState) -> DeskState:
        return {"idx": state["idx"] + 1}

    def more_clusters(state: DeskState) -> str:
        return "start_cluster" if state["idx"] < len(state["clusters"]) else "collect_drafts"

    async def collect_drafts(state: DeskState) -> DeskState:
        res = await tools.officer_call("list_drafts", status="PENDING_OFFICER", run_id=state["run_id"])
        drafts = res.get("drafts", []) if isinstance(res, dict) else []
        emit("drafts", {"count": len(drafts)})
        return {"drafts": drafts}

    async def officer_gate(state: DeskState) -> DeskState:
        """Handles ONE draft per pass, so each pass has exactly one interrupt and the
        decision tool runs once, after the officer has answered."""
        decisions = list(state.get("decisions", []))
        done = {d["draft_id"] for d in decisions}
        pending = [d for d in state.get("drafts", []) if d["draft_id"] not in done]
        if not pending:
            return {}
        d = pending[0]
        # The graph pauses here. Nothing moves until a named officer answers.
        answer = interrupt({"draft": d, "remaining": len(pending)})
        res = await tools.officer_call(
            "officer_record_decision", draft_id=d["draft_id"], officer_name=answer.get("officer", ""),
            decision=answer.get("decision", "reject"), final_message=answer.get("final_message"),
            comment=answer.get("comment", ""))
        emit("decision", {"draft_id": d["draft_id"], "result": res})
        decisions.append({"draft_id": d["draft_id"], **answer, "result": res})
        return {"decisions": decisions}

    def more_drafts(state: DeskState) -> str:
        done = {d["draft_id"] for d in state.get("decisions", [])}
        return "officer_gate" if any(d["draft_id"] not in done for d in state.get("drafts", [])) else "release"

    async def release(state: DeskState) -> DeskState:
        releases = []
        for dec in state.get("decisions", []):
            res = dec.get("result") or {}
            if isinstance(res, dict) and res.get("state") == "APPROVED":
                r = await tools.officer_call("release_advisory", draft_id=dec["draft_id"])
                emit("release", r if isinstance(r, dict) else {"result": r})
                releases.append(r)
        return {"releases": releases}

    g = StateGraph(DeskState)
    g.add_node("start_cluster", start_cluster)
    g.add_node("agent", agent)
    g.add_node("tools", run_tools)
    g.add_node("next_cluster", next_cluster)
    g.add_node("collect_drafts", collect_drafts)
    g.add_node("officer_gate", officer_gate)
    g.add_node("release", release)
    g.set_entry_point("start_cluster")
    g.add_edge("start_cluster", "agent")
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "next_cluster": "next_cluster"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("next_cluster", more_clusters,
                            {"start_cluster": "start_cluster", "collect_drafts": "collect_drafts"})
    g.add_edge("collect_drafts", "officer_gate")
    g.add_conditional_edges("officer_gate", more_drafts, {"officer_gate": "officer_gate", "release": "release"})
    g.add_edge("release", END)
    return g.compile(checkpointer=InMemorySaver())
