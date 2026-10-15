# ExtensionDesk

**Planting advice for small farmers, prepared by AI and signed off by a real farm officer.**

Built by Marvellous Asika at Dev-Afrique Development Advisors, Lagos, for the African Agentic AI Design Challenge (Agriculture track, climate-smart advisory).

---

## The problem, in plain words

In Nigeria, every Local Government Area (LGA) has a small office of **agricultural extension officers**. They are government farm advisers. Their job is to tell farmers things like:

- "The rains have properly started. Plant your maize now."
- "Wait. It has been dry for a week, your seeds could die."
- "Armyworms were found near your village. Check your plants before you spray."

The trouble is the numbers. One office might have **two officers for thousands of farmers**. Before sending advice to one village, an officer has to check the rainfall, check pest reports, check which crops that village grows and when they plan to plant. That takes about 15 minutes per village, so most villages get late advice, generic advice, or none.

A farmer who plants a week too early after a "false start" to the rains can lose the whole crop. That is the gap this project tries to close.

## What ExtensionDesk does

Think of it as a careful assistant sitting at the officer's desk (that is where the name comes from: the extension officer's desk).

1. **It does the homework.** For each village it reads real satellite rainfall, recent pest alerts, the list of farming households and the officer's own notes.
2. **It writes a draft SMS** for the farmers, in simple words, and lists exactly where every fact came from.
3. **It stops and waits.** Nothing is sent until the officer reads the draft, types their name and presses approve. The officer can also edit or reject it.
4. **It keeps a record** of every step and who approved what.

The AI never sends a message by itself. It cannot even reach the "send" button. That is a deliberate design choice: advice that farmers act on must come from a person who is accountable for it.

## A real example from the data

On 20 June 2025, for the village of Gidan Haki in Ikara LGA, Kaduna State:

- Satellite rainfall shows the rains properly started on **27 May**, and the last 30 days were **19% wetter than normal**.
- A fall armyworm warning was reported nearby on 16 June.
- One farmer appears twice in the register, so the duplicate is flagged and left out of the SMS list.

Draft for the officer: *"Rains are established in your area since 27 May. You can plant maize and sorghum this week. Scout maize for armyworm."*

For the village of Tudun Wada on the same day, ten days of rainfall data are missing. ExtensionDesk refuses to guess and drafts "no advice yet, rainfall data missing" instead.

## Why it can be trusted

The AI is not allowed to make things up. The program around it checks every draft and sends it back if:

- a claim has no source,
- it cites a source that does not exist,
- it says "plant" when the rainfall says otherwise,
- it suggests planting a crop outside that crop's season,
- it uses information from a different area.

These checks are tested. See [EVALS.md](EVALS.md).

## Why Dev-Afrique built this

Dev-Afrique works with governments, donors and farming programmes across Nigeria, Kenya, DRC and beyond on agriculture, geospatial data and monitoring. We see the same thing in the field again and again: good data exists, but the people who have to act on it do not have the time to read it. This is a small, honest attempt at fixing that for one office.

---

## For developers

### Run it

You need [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) (running) and [Node.js LTS](https://nodejs.org). Then:

```bash
uv run extensiondesk demo
```

The first run downloads the open-weights model `qwen3:8b` (several GB). It then works through two villages for 20 June 2025 and pauses for you, as the officer, to approve each draft. On a laptop with less than 16 GB of memory, use `uv run extensiondesk --model qwen3:4b demo`.

On Windows, `scripts\windows_setup.ps1` installs everything.

Other commands:

```bash
uv run extensiondesk run --clusters C07 --as-of 2025-07-10   # any village C01-C10, any date 1 May to 31 Aug 2025
uv run extensiondesk evals --server-only                     # safety checks, no AI model needed
uv run extensiondesk evals                                   # full tests with the AI model
```

### How it is built

- **Our own MCP server** (`src/extensiondesk/server.py`) with 9 tools: 4 that read data, 2 the AI uses to act (draft an SMS, schedule a follow-up visit) and 3 kept for the officer (approve, release).
- **A borrowed MCP server**: the official Filesystem server, so the AI can read the officer's notes without us writing file-access code.
- **LangGraph** runs the agent and pauses it for the officer.
- **Qwen3**, an open-weights model, runs on the officer's own laptop through Ollama. No household data leaves the office.
- **Rules in plain YAML** (`rules.yaml`): what counts as the start of the rains, when to hold, how much missing data is too much.

More detail in [ARCHITECTURE.md](ARCHITECTURE.md).

### Where the data comes from

| Data | Source | Real or made up |
|---|---|---|
| Daily rainfall, May to Aug 2025, 5 areas of Ikara LGA | CHIRPS satellite rainfall (UC Santa Barbara), via NASA SERVIR ClimateSERV | Real |
| Normal rainfall, 2010 to 2024 | Same source | Real |
| A 10-day gap in one area | Added by us to test what happens when data is missing | Made up, on purpose |
| 51 farming households in 10 villages | `scripts/make_synthetic_data.py` | Made up, with deliberate mistakes (duplicates, missing GPS points, acres instead of hectares) |
| Pest alerts and officer notes | Written by us, modelled on real field reports | Made up |
| Planting seasons per crop | Typical windows for northern Nigeria | Needs confirming by a real office |

No real farmer's data is used anywhere.

### Licence and credits

MIT licence. Built during the challenge build window with AI coding help, which the rules allow. Uses the MCP Filesystem server, LangGraph, the MCP Python SDK, Ollama, Qwen3 and CHIRPS rainfall data (Funk et al., 2015).
