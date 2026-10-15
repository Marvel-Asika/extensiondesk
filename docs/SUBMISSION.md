# Submission text

## Project name

ExtensionDesk

## Track and sub-theme

Agriculture, *The Extension Backlog*. Sub-theme: climate-smart advisory.

## Description (about 300 words)

ExtensionDesk is an MCP agent for a two-person agricultural extension desk, modelled on an LGA office in Ikara, Kaduna State, Nigeria. Before advising farmers, officers cross-check rainfall, pest alerts and each cluster's crops and planting calendar by hand, which takes about fifteen minutes per household cluster. Most clusters end up with generic advice or none.

ExtensionDesk does that cross-checking and drafting, then stops for the officer. For each cluster it reads the household register, real CHIRPS rainfall for the cluster's zone, recent pest alerts and the officer's own field notes. It then drafts short SMS advisories such as "plant", "hold", "scout before spraying" or "no advice: rainfall data missing". Rainfall statistics and the onset and dry-spell verdicts are computed by deterministic code from thresholds in rules.yaml. The model's job is judgement: deciding which advisories a cluster needs, writing them plainly, citing evidence and scheduling follow-ups.

Every claim in a draft must cite a source ID that a tool actually returned. Our MCP server rejects drafts with invented sources, drafts that contradict the rainfall verdict, advice outside a crop's planting window and evidence from the wrong zone. The model never holds the release tool. LangGraph pauses with interrupt() and a named officer approves, edits or rejects each draft. Only then are approved messages written to the SMS outbox. Every tool call is logged with timestamp, inputs, outputs and approver.

The household data is synthetic and deliberately messy: duplicate farmer IDs, missing GPS points, acres mixed with hectares, unreadable dates and a ten-day rainfall feed gap. The agent flags these instead of assuming them away. It runs fully offline on an open-weights model (Qwen3 through Ollama), so household records never leave the office's machine.

It is built on our own MCP server (nine tools), the official MCP Filesystem server for officer notes, and LangGraph.

## Links to fill in

- Repository: https://github.com/<username>/extensiondesk
- Demo video: https://youtu.be/<id>
