"""ExtensionDesk MCP server.

Tools the agent (the language model) may use:
  list_clusters, get_cluster_profile, get_rainfall_signal, get_pest_alerts   (read)
  draft_advisory, schedule_follow_up                                          (act)

Tools reserved for the officer gate (the agent code filters them out of the model's toolset):
  list_drafts, officer_record_decision, release_advisory                      (act, irreversible)

Every tool call is logged by audit.logged. Every source ID handed to the model is
recorded in the evidence table, and drafts may only cite recorded IDs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from . import config, data, rainfall, store
from .audit import logged

mcp = FastMCP("extensiondesk", log_level="WARNING")

AGENT_TOOLS = ["list_clusters", "get_cluster_profile", "get_rainfall_signal", "get_pest_alerts",
               "draft_advisory", "schedule_follow_up"]
OFFICER_TOOLS = ["list_drafts", "officer_record_decision", "release_advisory"]


class Reason(BaseModel):
    claim: str = Field(description="One factual statement the advisory relies on.")
    source_ids: list[str] = Field(description="Source IDs, exactly as returned by the read tools, that back the claim.")


def _parse_day(as_of: str) -> date:
    return date.fromisoformat(as_of)


def _in_window(cal: dict, d: date) -> bool:
    start = date(d.year, *map(int, cal["earliest_planting"].split("-")))
    end = date(d.year, *map(int, cal["latest_planting"].split("-")))
    return start <= d <= end


# ----------------------------------------------------------------------------- read tools

@mcp.tool()
@logged
def list_clusters() -> dict:
    """List the household clusters this desk covers, with village and rainfall zone."""
    return {"status": "ok", "clusters": list(data.clusters().values())}


@mcp.tool()
@logged
def get_cluster_profile(cluster_id: str) -> dict:
    """Households in a cluster: crops, plot sizes, planned planting dates, and data-quality issues.
    Also returns the planting calendar for each crop. Cite the returned source IDs."""
    cl = data.clusters().get(cluster_id)
    if not cl:
        return {"status": "error", "error": f"Unknown cluster {cluster_id}. Use list_clusters."}
    hhs = data.households(cluster_id)
    cal = data.crop_calendar()
    reg_id = store.put_evidence(f"HHREG:{cluster_id}", "household_register",
                                f"Synthetic household register, cluster {cluster_id} ({cl['village']})",
                                {"cluster_id": cluster_id, "zone_id": cl["zone_id"],
                                 "households": [h.household_id for h in hhs]})
    crops = sorted({h.crop for h in hhs if h.crop in cal})
    calendars = []
    for c in crops:
        sid = store.put_evidence(f"CAL:{c}", "crop_calendar", f"Desk planting calendar for {c}", cal[c])
        calendars.append({"source_id": sid, **cal[c]})
    by_crop: dict[str, int] = {}
    for h in hhs:
        if not h.duplicate_of:
            by_crop[h.crop] = by_crop.get(h.crop, 0) + 1
    return {
        "status": "ok",
        "source_id": reg_id,
        "cluster_id": cluster_id, "village": cl["village"], "zone_id": cl["zone_id"],
        "households_by_crop": by_crop,
        "households": [{"household_id": h.household_id, "crop": h.crop, "plot_ha": h.plot_ha,
                        "planned_planting": h.planned_planting.isoformat() if h.planned_planting else None,
                        "language": h.language, "excluded": bool(h.duplicate_of)} for h in hhs],
        "data_quality_issues": [{"household_id": h.household_id, "issue": i} for h in hhs for i in h.issues],
        "crop_calendars": calendars,
    }


@mcp.tool()
@logged
def get_rainfall_signal(cluster_id: str, as_of: str) -> dict:
    """Rainfall for the cluster's zone up to as_of (YYYY-MM-DD): 10- and 30-day totals, anomaly against
    the 2010-2024 normal, current dry spell, onset date, missing days, and the rule verdict from
    rules.yaml (plant_ok, hold or insufficient_data). Cite the returned source_id."""
    cl = data.clusters().get(cluster_id)
    if not cl:
        return {"status": "error", "error": f"Unknown cluster {cluster_id}."}
    try:
        day = _parse_day(as_of)
    except ValueError:
        return {"status": "error", "error": "as_of must be YYYY-MM-DD"}
    sig = rainfall.signal(cl["zone_id"], day)
    if not sig["available"]:
        sig["rule_verdict"] = "insufficient_data"
        sig["rule_reason"] = sig["reason"]
        sig["citation"] = f"Rainfall feed index, zone {cl['zone_id']}: {sig['reason']}"
    sid = store.put_evidence(f"RAIN:{cl['zone_id']}:{as_of}", "rainfall_signal", sig["citation"],
                             {**sig, "cluster_id": cluster_id})
    return {"status": "ok", "source_id": sid, "cluster_id": cluster_id, **sig}


@mcp.tool()
@logged
def get_pest_alerts(cluster_id: str, as_of: str) -> dict:
    """Pest alerts for the cluster's zone reported in the lookback window before as_of. Cite alert source IDs."""
    cl = data.clusters().get(cluster_id)
    if not cl:
        return {"status": "error", "error": f"Unknown cluster {cluster_id}."}
    day = _parse_day(as_of)
    lookback = config.rules()["pest"]["lookback_days"]
    found = []
    for a in data.pest_alerts():
        rep = date.fromisoformat(a["reported_on"])
        if a["zone_id"] == cl["zone_id"] and day - timedelta(days=lookback) <= rep <= day:
            sid = store.put_evidence(f"PEST:{a['alert_id']}", "pest_alert",
                                     f"Pest alert {a['alert_id']} ({a['origin']})", a)
            found.append({"source_id": sid, **a})
    return {"status": "ok", "cluster_id": cluster_id, "zone_id": cl["zone_id"], "lookback_days": lookback,
            "alerts": found}


# ----------------------------------------------------------------------------- act tools

def _validate_sources(source_ids: list[str]) -> tuple[dict, list[str]]:
    errors = []
    notes = [s for s in source_ids if s.startswith("NOTE:")]
    others = [s for s in source_ids if not s.startswith("NOTE:")]
    found = store.get_evidence(others)
    for s in others:
        if s not in found:
            errors.append(f"Source '{s}' was never returned by a tool. Cite only source IDs returned by tools.")
    for s in notes:
        name = s.split(":", 1)[1]
        if "/" in name or "\\" in name or not (config.notes_dir() / name).is_file():
            errors.append(f"Note source '{s}' does not match a file in desk/officer_notes.")
        else:
            found[s] = {"kind": "officer_note", "citation": f"Officer note {name}", "payload": {}}
    return found, errors


@mcp.tool()
@logged
def draft_advisory(cluster_id: str, as_of: str, action: str, crops: list[str], message: str,
                   reasons: list[Reason]) -> dict:
    """Draft an SMS advisory for the households in a cluster growing the given crops.
    The draft is NOT sent. It waits for a named extension officer to approve it.
    action must be one of: plant, hold, scout_and_spray_check, no_advice_insufficient_data.
    Every reason must cite source IDs returned by the read tools. The server rejects drafts that
    cite unknown sources, contradict the rainfall rule verdict, or fall outside a crop's planting window."""
    rules = config.rules()["advisory"]
    errors: list[str] = []
    cl = data.clusters().get(cluster_id)
    if not cl:
        return {"status": "rejected", "errors": [f"Unknown cluster {cluster_id}."]}
    try:
        day = _parse_day(as_of)
    except ValueError:
        return {"status": "rejected", "errors": ["as_of must be YYYY-MM-DD"]}
    if action not in rules["allowed_actions"]:
        errors.append(f"action '{action}' is not allowed. Use one of {rules['allowed_actions']}.")
    if not message.strip():
        errors.append("message is empty.")
    if len(message) > rules["sms_max_chars"]:
        errors.append(f"message is {len(message)} characters; the limit is {rules['sms_max_chars']}.")
    reasons = [r if isinstance(r, Reason) else Reason(**r) for r in reasons]
    if not reasons:
        errors.append("At least one reason with a source is required. An unsourced advisory is worse than none.")
    for r in reasons:
        if not r.source_ids:
            errors.append(f"Reason '{r.claim[:60]}' has no source. Every claim needs a source ID.")
    all_ids = sorted({s for r in reasons for s in r.source_ids})
    evidence, src_errors = _validate_sources(all_ids)
    errors += src_errors

    hhs = [h for h in data.households(cluster_id) if not h.duplicate_of]
    present = {h.crop for h in hhs}
    crops = [c.strip().lower() for c in crops]
    if not crops:
        errors.append("crops is empty; name the crops this advisory is for.")
    for c in crops:
        if c not in present:
            errors.append(f"No household in {cluster_id} grows '{c}'. Crops present: {sorted(present)}.")

    # evidence must belong to this cluster
    for sid, ev in evidence.items():
        p = ev.get("payload", {})
        if ev.get("kind") == "rainfall_signal" and p.get("zone") != cl["zone_id"]:
            errors.append(f"{sid} is rainfall for zone {p.get('zone')}, but {cluster_id} is in zone {cl['zone_id']}.")
        if ev.get("kind") == "household_register" and p.get("cluster_id") != cluster_id:
            errors.append(f"{sid} is the register for another cluster.")
        if ev.get("kind") == "pest_alert" and p.get("zone_id") != cl["zone_id"]:
            errors.append(f"{sid} is a pest alert for zone {p.get('zone_id')}, not {cl['zone_id']}.")

    rain = [ev for ev in evidence.values() if ev.get("kind") == "rainfall_signal"
            and ev["payload"].get("zone") == cl["zone_id"] and ev["payload"].get("as_of", as_of) == as_of]
    verdict = rain[0]["payload"].get("rule_verdict") if rain else None
    if action in rules["rainfall_backed_actions"] and not rain:
        errors.append(f"'{action}' must cite the rainfall signal for {cluster_id} on {as_of} (call get_rainfall_signal).")
    if action == "plant":
        if verdict and verdict != "plant_ok":
            errors.append(f"'plant' contradicts the rainfall rule verdict '{verdict}'. "
                          f"Reason: {rain[0]['payload'].get('rule_reason')}")
        cal = data.crop_calendar()
        for c in crops:
            if c in cal and not _in_window(cal[c], day):
                errors.append(f"{as_of} is outside the planting window for {c} "
                              f"({cal[c]['earliest_planting']} to {cal[c]['latest_planting']}).")
            if f"CAL:{c}" not in evidence:
                errors.append(f"'plant' for {c} must cite CAL:{c} (from get_cluster_profile).")
    if action == "scout_and_spray_check" and not any(ev.get("kind") == "pest_alert" for ev in evidence.values()):
        errors.append("'scout_and_spray_check' must cite a pest alert source (PEST:...).")
    if action == "no_advice_insufficient_data" and verdict not in (None, "insufficient_data"):
        errors.append(f"Rainfall verdict is '{verdict}', so the data is not insufficient.")

    if errors:
        return {"status": "rejected", "errors": errors,
                "hint": "Fix the listed problems and call draft_advisory again."}

    recipients = [{"household_id": h.household_id, "phone": h.phone, "language": h.language}
                  for h in hhs if h.crop in crops]
    issues = [{"household_id": h.household_id, "issue": i}
              for h in data.households(cluster_id) if h.crop in crops for i in h.issues]
    draft_id = store.next_id("drafts", "DRAFT", "draft_id")
    with store.db() as con:
        con.execute("INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (draft_id, config.run_id(), cluster_id, as_of, action, json.dumps(crops), message,
                     json.dumps([r.model_dump() for r in reasons]), json.dumps(recipients),
                     json.dumps(issues), "PENDING_OFFICER", store.now()))
    return {"status": "ok", "draft_id": draft_id, "state": "PENDING_OFFICER",
            "recipients": len(recipients), "data_issues": len(issues),
            "cited_sources": {k: v["citation"] for k, v in evidence.items()},
            "note": "Draft saved. Nothing has been sent. A named officer must approve it."}


@mcp.tool()
@logged
def schedule_follow_up(cluster_id: str, due_date: str, reason: str, source_ids: list[str]) -> dict:
    """Schedule a follow-up check (visit or call) for the officer, backed by sources."""
    if cluster_id not in data.clusters():
        return {"status": "rejected", "errors": [f"Unknown cluster {cluster_id}."]}
    try:
        _parse_day(due_date)
    except ValueError:
        return {"status": "rejected", "errors": ["due_date must be YYYY-MM-DD"]}
    _, errors = _validate_sources(source_ids)
    if not source_ids:
        errors.append("A follow-up needs at least one source ID.")
    if errors:
        return {"status": "rejected", "errors": errors}
    fid = store.next_id("followups", "FU", "followup_id")
    with store.db() as con:
        con.execute("INSERT INTO followups VALUES (?,?,?,?,?,?,?)",
                    (fid, config.run_id(), cluster_id, due_date, reason, json.dumps(source_ids), store.now()))
    return {"status": "ok", "followup_id": fid, "cluster_id": cluster_id, "due_date": due_date}


# ----------------------------------------------------------------------------- officer gate tools

@mcp.tool()
@logged
def list_drafts(status: str = "PENDING_OFFICER", run_id: str | None = None) -> dict:
    """List drafts by status, with their reasons, sources and data issues (for the officer screen)."""
    with store.db() as con:
        sql, args = "SELECT * FROM drafts WHERE status = ?", [status]
        if run_id:
            sql += " AND run_id = ?"
            args.append(run_id)
        rows = [store.row_to_draft(r) for r in con.execute(sql + " ORDER BY draft_id", args)]
    for d in rows:
        ids = sorted({s for r in d["reasons"] for s in r["source_ids"]})
        ev, _ = _validate_sources(ids)
        d["sources"] = {k: v["citation"] for k, v in ev.items()}
    return {"status": "ok", "drafts": rows}


@mcp.tool()
@logged
def officer_record_decision(draft_id: str, officer_name: str, decision: str,
                            final_message: str | None = None, comment: str = "") -> dict:
    """Record a named officer's decision on a draft: approve, edit_and_approve, or reject.
    Only the officer gate calls this. The model never has this tool."""
    officer_name = (officer_name or "").strip()
    if len(officer_name) < 3 or officer_name.lower() in {"agent", "ai", "assistant", "model", "system"}:
        return {"status": "rejected", "errors": ["A real officer name is required to record a decision."]}
    if decision not in {"approve", "edit_and_approve", "reject"}:
        return {"status": "rejected", "errors": ["decision must be approve, edit_and_approve or reject."]}
    with store.db() as con:
        r = con.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        if not r:
            return {"status": "rejected", "errors": [f"Unknown draft {draft_id}."]}
        if r["status"] != "PENDING_OFFICER":
            return {"status": "rejected", "errors": [f"{draft_id} is {r['status']}, not pending."]}
        msg = final_message if decision == "edit_and_approve" else r["message"]
        if decision == "edit_and_approve":
            limit = config.rules()["advisory"]["sms_max_chars"]
            if not msg or len(msg) > limit:
                return {"status": "rejected", "errors": [f"Edited message must be 1 to {limit} characters."]}
        status = "REJECTED" if decision == "reject" else "APPROVED"
        con.execute("INSERT INTO decisions (draft_id, officer, decision, final_message, comment, decided_at) "
                    "VALUES (?,?,?,?,?,?)", (draft_id, officer_name, decision, msg, comment, store.now()))
        con.execute("UPDATE drafts SET status = ?, message = ? WHERE draft_id = ?", (status, msg, draft_id))
    return {"status": "ok", "draft_id": draft_id, "state": status, "approved_by": officer_name}


@mcp.tool()
@logged
def release_advisory(draft_id: str) -> dict:
    """IRREVERSIBLE. Release an APPROVED draft to the SMS outbox (desk/outbox). Refuses anything not
    approved by a named officer. In this build the outbox is a folder; no SMS gateway is called."""
    with store.db() as con:
        r = con.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        if not r:
            return {"status": "rejected", "errors": [f"Unknown draft {draft_id}."]}
        dec = con.execute("SELECT * FROM decisions WHERE draft_id = ? ORDER BY decision_id DESC LIMIT 1",
                          (draft_id,)).fetchone()
        if r["status"] != "APPROVED" or not dec or dec["decision"] == "reject":
            return {"status": "rejected",
                    "errors": [f"{draft_id} is {r['status']}. Only drafts approved by a named officer can be released."]}
        d = store.row_to_draft(r)
        folder = config.outbox_dir() / draft_id
        folder.mkdir(parents=True, exist_ok=True)
        for rec in d["recipients"]:
            (folder / f"{rec['household_id']}.txt").write_text(
                f"TO: {rec['phone']}\nLANGUAGE: {rec['language']}"
                f"{' (Hausa version needed before sending)' if rec['language'] == 'ha' else ''}\n"
                f"APPROVED BY: {dec['officer']} at {dec['decided_at']}\nDRAFT: {draft_id}\n\n{d['message']}\n",
                encoding="utf-8")
        con.execute("UPDATE drafts SET status = 'RELEASED' WHERE draft_id = ?", (draft_id,))
        con.execute("INSERT INTO releases VALUES (?,?,?,?,?)",
                    (draft_id, dec["officer"], "sms_outbox_folder", len(d["recipients"]), store.now()))
    return {"status": "ok", "draft_id": draft_id, "state": "RELEASED", "approved_by": dec["officer"],
            "recipients": len(d["recipients"]), "outbox": str(folder)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
