"""Manual agent loop: investigate an incident with OpenAI + the four tools."""
from __future__ import annotations

import json
import logging
import os

from openai import OpenAI

from .tools import TOOL_DISPATCH, TOOL_SCHEMAS

logger = logging.getLogger("blamebot")

SYSTEM_PROMPT = """You are an incident response engineer. You have just been handed a description of a production incident.

Your job:
1. Investigate methodically — search logs for the symptom, list recent commits to find suspect changes, read the relevant source files, and inspect the diff of any commit that looks load-bearing.
2. Form a hypothesis about the root cause backed by concrete evidence.
3. Once confident, use get_file to read the exact file that needs changing, apply the fix, then call create_fix_pr ONCE to open a pull request with the corrected file content. The PR body must include your full analysis.

Heuristics:
- Start broad (recent commits + a log search for the symptom), then narrow down.
- If a hypothesis doesn't hold up under the evidence, say so explicitly and try a different angle.
- Cite your evidence: log line numbers, commit SHAs, file paths.
- Only call create_fix_pr when you are confident. Do not call it more than once.
- If the fix involves a config/env change rather than a code change, still open a PR that updates the default value or adds a comment documenting the required setting.

After create_fix_pr succeeds, end your response with this structure:

**Likely root cause:** <one sentence>

**Evidence:**
- <citation 1>
- <citation 2>
...

**Suggested fix:** <concrete change made, with file path>

**PR:** <pr_url from create_fix_pr>"""


def _execute_tool(name: str, arguments: dict) -> str:
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        result = fn(**arguments)
    except Exception as e:
        return f"Tool {name} raised {type(e).__name__}: {e}"
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


def investigate(incident_description: str, *, max_iterations: int = 20) -> tuple[str, str | None]:
    """Run the agent loop and return (final_analysis, pr_url | None)."""
    model = os.environ.get("OPENAI_MODEL", "gpt-5")
    client = OpenAI()

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": incident_description},
    ]

    # Audit the initial messages
    for m in messages:
        logger.debug("messages.append:\n%s", json.dumps(m, indent=2, default=str))

    pr_url: str | None = None

    for i in range(max_iterations):
        logger.info("=== Iteration %d ===", i + 1)

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        msg = response.choices[0].message

        # Build and audit the assistant message
        assistant_msg: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        logger.debug("messages.append:\n%s", json.dumps(assistant_msg, indent=2, default=str))
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return msg.content or "", pr_url

        for tc in msg.tool_calls:
            logger.info("  ↳ %s", tc.function.name)

            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}

            result = _execute_tool(tc.function.name, arguments)

            # Capture PR URL if the agent just opened one
            if tc.function.name == "create_fix_pr":
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and "pr_url" in parsed:
                        pr_url = parsed["pr_url"]
                        logger.info("✅ PR opened: %s", pr_url)
                except (json.JSONDecodeError, TypeError):
                    pass

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            }
            logger.debug("messages.append:\n%s", json.dumps(tool_msg, indent=2, default=str))
            messages.append(tool_msg)

    return "Agent exceeded max iterations without producing a final answer.", pr_url
