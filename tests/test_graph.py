"""Wiring test for the LangGraph graph with a SCRIPTED stand-in model.

This checks plumbing only (tools, gate, release, logging). It is not an eval of the
agent's judgement; the real evals run against an open-weights model (see EVALS.md).
"""

import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from extensiondesk.runner import auto_officer, run_desk


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def model_name(self):
        return "scripted-test-model"

    @property
    def openai_api_base(self):
        return "none"


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}])


SCRIPT = [
    call("get_cluster_profile", {"cluster_id": "C03"}, 1),
    call("get_rainfall_signal", {"cluster_id": "C03", "as_of": "2025-06-20"}, 2),
    call("get_pest_alerts", {"cluster_id": "C03", "as_of": "2025-06-20"}, 3),
    # first attempt cites a made-up source and must be rejected by the server
    call("draft_advisory", {"cluster_id": "C03", "as_of": "2025-06-20", "action": "plant", "crops": ["maize"],
                            "message": "Rains established. Plant maize now.",
                            "reasons": [{"claim": "Rains are good", "source_ids": ["NIMET:123"]}]}, 4),
    call("draft_advisory", {"cluster_id": "C03", "as_of": "2025-06-20", "action": "plant", "crops": ["maize", "sorghum"],
                            "message": "Rains are established in your area. You can plant maize and sorghum this week.",
                            "reasons": [{"claim": "Onset established 27 May", "source_ids": ["RAIN:Z2:2025-06-20"]},
                                        {"claim": "Inside planting windows", "source_ids": ["CAL:maize", "CAL:sorghum"]}]}, 5),
    AIMessage(content="Drafted one planting advisory for C03."),
]


async def test_graph_gate_and_release(monkeypatch, isolated_state):
    model = ScriptedModel(responses=SCRIPT)
    monkeypatch.setattr("extensiondesk.runner.make_llm", lambda m=None: model)
    result = await run_desk(["C03"], "2025-06-20", None, auto_officer("Test Officer"), use_filesystem=False,
                            run_id="test-run")
    assert len(result["drafts"]) == 1
    assert result["decisions"][0]["result"]["state"] == "APPROVED"
    assert result["releases"][0]["state"] == "RELEASED"
    rejected = [t for t in result["transcript"] if t["tool"] == "draft_advisory" and t["result"]["status"] == "rejected"]
    assert len(rejected) == 1
    log = [json.loads(line) for line in open(isolated_state / "tool_calls.jsonl")]
    released = [e for e in log if e["tool"] == "release_advisory"]
    assert released and released[0]["approver"] == "Test Officer"
    assert all("ts" in e and "inputs" in e and "output" in e for e in log)
    assert (isolated_state / "outbox" / result["drafts"][0]["draft_id"]).is_dir()


async def test_rejected_draft_is_never_released(monkeypatch, isolated_state):
    model = ScriptedModel(responses=SCRIPT)
    monkeypatch.setattr("extensiondesk.runner.make_llm", lambda m=None: model)
    result = await run_desk(["C03"], "2025-06-20", None, auto_officer("Test Officer", "reject"),
                            use_filesystem=False, run_id="test-run-2")
    assert result["releases"] == []
    assert not (isolated_state / "outbox").exists() or not any((isolated_state / "outbox").iterdir())


async def test_borrowed_filesystem_server_is_read_only_for_the_model(monkeypatch, isolated_state):
    import pytest
    from conftest import has_npx
    from extensiondesk import config
    if not has_npx():
        pytest.skip("Node.js not installed")
    notes = str(config.notes_dir())
    script = [
        call("list_directory", {"path": notes}, 1),
        call("read_text_file", {"path": notes + "/2025-06-field-notes.md"}, 2),
        call("write_file", {"path": notes + "/x.md", "content": "hack"}, 3),
        AIMessage(content="done"),
    ]
    monkeypatch.setattr("extensiondesk.runner.make_llm", lambda m=None: ScriptedModel(responses=script))
    result = await run_desk(["C03"], "2025-06-20", None, auto_officer("Test Officer"), use_filesystem=True,
                            run_id="test-fs")
    tr = {t["tool"]: t["result"] for t in result["transcript"]}
    assert "2025-06-field-notes.md" in str(tr["list_directory"])
    assert "fertiliser" in str(tr["read_text_file"])
    assert "not available" in str(tr["write_file"])
    assert not (config.notes_dir() / "x.md").exists()
