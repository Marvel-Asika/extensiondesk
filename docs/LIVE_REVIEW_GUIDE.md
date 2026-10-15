# Live review guide (20 minutes: 10 walking through the code, then a new requirement live)

## The 10-minute walkthrough

Open the files in this order and say one or two sentences each.

1. **`rules.yaml` (1 min).** "Every threshold lives here: what counts as onset, when to hold, how many missing days we tolerate. An office changes a rule without touching code."
2. **`src/extensiondesk/rainfall.py`, function `signal` (1.5 min).** "Rainfall maths is deterministic. It returns totals, anomaly against the 2010 to 2024 normal, the current dry spell, the onset date and a rule verdict. The model reads this; it never does the arithmetic."
3. **`src/extensiondesk/server.py` (3 min).** Show `get_rainfall_signal` calling `store.put_evidence`: "every source ID the model sees is recorded first." Then `draft_advisory`: "this is where advice is checked. Unknown source, rejected. Contradicts the verdict, rejected. Outside the crop window, rejected. Evidence from another zone, rejected." Then `release_advisory`: "it refuses anything a named officer has not approved."
4. **`src/extensiondesk/agent.py` (3 min).** `DeskTools`: "two MCP servers over stdio: ours, and the official Filesystem server scoped to the officer's notes, read tools only. The officer tools are split off and never given to the model." `build_graph`: walk the nodes. Point at `interrupt()` in `officer_gate`: "the graph pauses here, one draft at a time, and the decision tool runs only after a person answers."
5. **`src/extensiondesk/audit.py` and `EVALS.md` (1.5 min).** "Every tool call is logged with inputs, outputs, time and approver. The evals are real runs: deterministic server contract tests and agent tests on the open model. S13 is the failure we left unfixed, on purpose: Hausa delivery."

## Likely questions, short answers

- **Why not let the model compute rainfall?** Small models get arithmetic wrong, and an unsourced or miscalculated number is worse than none. Code computes; the model judges and explains.
- **What stops the model from sending?** It never receives `release_advisory`. Even if it did, the server refuses releases without a recorded decision from a named officer.
- **Why one cluster at a time?** It keeps the context short for an 8B local model, which makes tool use more reliable.
- **Why Ollama and Qwen3?** Open weights, tool calling, runs offline on a laptop, so household records stay in the office.
- **Is the data real?** Rainfall is real CHIRPS. Households, pest alerts and notes are synthetic, labelled as such, and deliberately messy. The Z5 gap is injected and documented.
- **How do you know a draft is good?** EVALS.md: 14 server cases and 8 agent cases with pass or fail, and the logs of those runs are committed in `evals/results/`.

## Practice drills for "a new requirement, live"

Do each once before the review. Each one takes under 5 minutes.

### Drill 1: change a rule (no code)
"Treat 5 dry days as a reason to hold." Edit `rules.yaml`: `hold: current_dry_spell_days: 5`. Run `uv run extensiondesk evals --server-only`.

### Drill 2: add a market price tool (new MCP tool)
In `server.py`, under the read tools:

```python
@mcp.tool()
@logged
def get_market_price(crop: str) -> dict:
    """Latest market price for a crop at the Ikara market (synthetic). Cite the source_id."""
    prices = {"maize": 520, "sorghum": 480, "soybean": 610, "cowpea": 900}  # NGN per kg, synthetic
    if crop not in prices:
        return {"status": "error", "error": f"No price for {crop}"}
    sid = store.put_evidence(f"PRICE:{crop}", "market_price", f"Synthetic Ikara market price for {crop}",
                             {"crop": crop, "ngn_per_kg": prices[crop]})
    return {"status": "ok", "source_id": sid, "crop": crop, "ngn_per_kg": prices[crop]}
```

Add `"get_market_price"` to `AGENT_TOOLS` at the top of the file. Run `uv run extensiondesk run --clusters C03 --as-of 2025-06-20` and show the new tool in the log.

### Drill 3: a new hard rule
"Never advise planting when the 30-day rainfall is more than 40 percent below normal." In `draft_advisory`, after the `if action == "plant":` block, add:

```python
    if action == "plant" and rain and (rain[0]["payload"].get("anomaly_30d_pct") or 0) < -40:
        errors.append("30-day rainfall is more than 40% below normal; 'plant' is not allowed.")
```

### Drill 4: a second approver
"Releases need two officers." In `release_advisory`, count distinct officers in `decisions` for the draft and refuse if fewer than two. In `agent.py`, loop `officer_gate` until two decisions exist for each draft.

## If something breaks live

Stay calm and read the error out loud. Most issues are: Ollama not running (`ollama serve`), the model name (`--model qwen3:4b`), or a typo in the edit. `uv run extensiondesk evals --server-only` is a quick sanity check that needs no model.
