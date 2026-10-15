# ExtensionDesk

**An MCP agent that turns rainfall, pest and household data into sourced, officer-approved planting advisories for a two-person agricultural extension desk.**

Track: Agriculture, *The Extension Backlog*. Sub-theme: **climate-smart advisory** (when to plant, hold or scout, from rainfall, pest signals and plot records).

## The problem

A local extension desk, here modelled on an LGA desk in Ikara, Kaduna State, Nigeria, has two officers and hundreds of smallholder households. Before every advisory they cross-check rainfall, pest alerts and each cluster's crops and planting calendar by hand. That takes about 15 minutes per household cluster, so most clusters get generic advice or none.

ExtensionDesk does the cross-checking and drafting. The officer decides.

| Before | With ExtensionDesk |
|---|---|
| Officer pulls rainfall, reads scouting reports, checks the register, writes an SMS | Agent pulls the signals, applies the desk's rules, drafts the SMS with every claim cited |
| Messy register rows are noticed late, or never | Duplicates, missing GPS points, unit mix-ups and unreadable dates are flagged on the draft |
| Nothing records why an advisory said what it said | Every tool call is logged, and every draft carries its sources and the approving officer's name |

## Run it in one command

You need [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) (running) and [Node.js LTS](https://nodejs.org) (for the borrowed MCP server). Then:

```bash
uv run extensiondesk demo
```

This installs the Python dependencies, pulls the open-weights model `qwen3:8b` into Ollama the first time (several GB), and runs the agent on clusters C03 and C09 for 20 June 2025. When the drafts are ready the run **stops and asks you, as the officer, to type your name and approve, edit or reject each one.** Only approved drafts are written to the SMS outbox (`desk/outbox/`).

On a laptop with less than 16 GB of RAM, use the smaller model: `uv run extensiondesk --model qwen3:4b demo`.

Windows users can run `scripts\windows_setup.ps1`, which installs uv, Node.js and Ollama with winget and then runs the checks and evals.

Other commands:

```bash
uv run extensiondesk run --clusters C07 --as-of 2025-07-10   # any cluster C01-C10, any date 2025-05-01 to 2025-08-31
uv run extensiondesk evals --server-only                     # deterministic contract tests, no model needed
uv run extensiondesk evals                                   # full evals on the open-weights model
uv run extensiondesk check                                   # is Ollama up, is the model there, is Node.js there
uv run extensiondesk reset                                   # clear the local database, log and outbox
uv run --extra dev pytest                                    # unit and wiring tests
```

To compare with another model on any OpenAI-compatible endpoint, set `EXTENSIONDESK_BASE_URL`, `EXTENSIONDESK_API_KEY` and `--model`. Use synthetic data only for hosted models.

## What stays with a human

- The agent **drafts**. It never sends. It does not even have the release tool: `officer_record_decision` and `release_advisory` are filtered out of the model's toolset and called by graph code only after LangGraph's `interrupt()` has collected a decision from a named officer.
- The server refuses to release anything that a named officer has not approved, and refuses approver names like "agent" or "model".
- Every reason in a draft must cite a source ID that a tool actually returned in this database. Invented sources are rejected. So are drafts that contradict the rainfall rule verdict, fall outside a crop's planting window, or cite evidence from another zone.
- Every tool call is written to `logs/tool_calls.jsonl` with timestamp, run ID, inputs, output, duration and, for gated tools, the approver.

## Data: origin and limits

| Data | Origin | Real or synthetic | Limits |
|---|---|---|---|
| `data/rainfall/chirps_daily_2025.csv` | CHIRPS v2.0 daily rainfall (UCSB Climate Hazards Center), area mean over a 0.06 degree box per zone, retrieved through SERVIR ClimateSERV | **Real** | Satellite-gauge blend, about 5 km resolution; no forecast. 1 May to 31 Aug 2025 only |
| `data/rainfall/chirps_dekadal_climatology_2010_2024.csv` | Same source, 2010 to 2024 dekadal means | **Real** | 15-year normal, May to August |
| `data/rainfall/zones.csv` | Five points inside Ikara LGA chosen by us | Real coordinates | Zones are not official wards |
| `data/rainfall/feed_outages.csv` | Our own | **Simulated** | A 10-day gap injected into zone Z5 to test gap handling |
| `data/households.csv` | `scripts/make_synthetic_data.py` | **Synthetic** | 51 households in 10 clusters. Deliberately messy: missing GPS, duplicate farmer IDs, a misspelt crop, acres instead of hectares, an unreadable date. No real person's data |
| `data/pest_alerts.csv` | Our own, modelled on ward scouting reports | **Synthetic** | 3 alerts |
| `data/crop_calendar.csv` | Indicative Northern Guinea Savanna windows | Indicative | Must be confirmed by the extension office |
| `desk/officer_notes/` | Our own | **Synthetic** | Field notes read through the borrowed Filesystem MCP server |

Cluster C10 deliberately points at a zone with no rainfall feed (Z6), so the agent must say "no data" instead of guessing.

## Repository map

```
src/extensiondesk/server.py   our MCP server: 9 tools (4 read, 2 act for the agent, 3 for the officer gate)
src/extensiondesk/agent.py    LangGraph graph, MCP sessions, the officer gate
src/extensiondesk/rainfall.py rainfall metrics and the rule verdict (deterministic)
src/extensiondesk/data.py     register loading and data-quality checks
src/extensiondesk/audit.py    tool-call log
src/extensiondesk/evals.py    eval harness that writes EVALS.md
rules.yaml                    every threshold the agent relies on
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and [EVALS.md](EVALS.md) for test results, including one failure we left unfixed.

## Disclosure

Built during the challenge build window with AI coding assistance, which the rules allow. Third-party components: the official MCP Filesystem server (`@modelcontextprotocol/server-filesystem`), LangGraph, LangChain MCP adapters, the MCP Python SDK, Ollama and the Qwen3 open-weights model. Rainfall data: CHIRPS (Funk et al., 2015) via SERVIR ClimateSERV.

Licence: MIT.
