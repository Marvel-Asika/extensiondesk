# Demo video script (under 3 minutes, one unedited take)

The rules ask for an unedited agent run with tool calls on screen, not slides. Do one practice run first so the model is already downloaded and warm.

## Before you press record

1. Open PowerShell in the `extensiondesk` folder. Make the window large and set the font to 16 or bigger (right-click the title bar, Properties, Font).
2. Run `uv run extensiondesk reset` so the outbox is empty.
3. Run `uv run extensiondesk demo` once as a practice, approve the drafts, then run `uv run extensiondesk reset` again.
4. Start recording with Win + Alt + R (Xbox Game Bar), or with OBS if you have it. Record your voice too.

## The take

**0:00 to 0:15, say:**
"This is ExtensionDesk, an MCP agent for a two-person extension desk in Ikara LGA, Kaduna State. It drafts planting advice from real CHIRPS rainfall, pest alerts and the household register. It never sends anything. An officer approves every message. It runs on Qwen3, an open-weights model, on this laptop."

**Type:** `uv run extensiondesk demo` and press Enter.

**While the tool calls scroll, say:**
- "First line: the model sees only read tools plus draft and follow-up. It has no release tool."
- When `get_rainfall_signal` returns: "Rainfall is computed by code, not by the model. Here the rule verdict is plant_ok, and the onset was 27 May."
- When `get_pest_alerts` returns PA-2025-014: "There is a fall armyworm alert in this zone."
- If a `draft_advisory` comes back red (rejected): "The server rejected that draft. It cited something no tool returned. The agent fixes it and tries again." (If it does not happen, skip this line.)
- For C09: "Cluster C09 has a ten-day rainfall gap, so the agent drafts 'no advice, data missing' instead of guessing."

**When the yellow officer panel appears, say:**
"Now it stops. Nothing has been sent. I'm the officer: I can see the SMS, every reason with its source, and the data problems in the register."
Type your name, press `a` to approve. For the second draft, you can press `e` and shorten the SMS to show editing, or `r` to reject one.

**At the summary table, say:**
"Only approved drafts were released. Let me show the log."

**Type:** `Get-Content logs\tool_calls.jsonl -Tail 3`
"Every tool call is logged with time, inputs, outputs and, for the release, my name as the approver."

**Type:** `Get-ChildItem desk\outbox -Recurse`
"And these are the SMS files waiting in the outbox. Thank you."

Stop recording.

## Upload

1. YouTube, Create, Upload video. Title: "ExtensionDesk: Agentic AI for an extension desk (African Agentic AI Design Challenge)".
2. Visibility: **Public**. Not "Unlisted" and not "Private".
3. Copy the link into `docs/SUBMISSION.md` and the challenge form.

If the run is slow on your laptop and goes past 3 minutes, use the smaller model (`uv run extensiondesk --model qwen3:4b demo`) or run a single cluster (`uv run extensiondesk run --clusters C03 --as-of 2025-06-20`). Do not cut or speed up the video.
