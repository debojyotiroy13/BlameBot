"""Manual agent loop: investigate an incident with OpenAI + the four tools."""
from __future__ import annotations

import json
import os

from openai import OpenAI

from .tools import TOOL_DISPATCH, TOOL_SCHEMAS

SYSTEM_PROMPT = """You are an incident response engineer. You have just been handed a description of a production incident.

Your job:
1. Investigate methodically using the available tools — search logs for the symptom, list recent commits to find suspect changes, read the relevant source files, and inspect the diff of any commit that looks load-bearing.
2. Form a hypothesis about the root cause backed by concrete evidence.
3. Suggest a specific fix.

Heuristics:
- Start broad (recent commits + a log search for the symptom), then narrow down.
- If a hypothesis doesn't hold up under the evidence, say so explicitly and try a different angle.
- Cite your evidence: log line numbers, commit SHAs, file paths.

End your response with this structure:

**Likely root cause:** <one sentence>

**Evidence:**
- <citation 1>
- <citation 2>
...

**Suggested fix:** <concrete change to make, with file path>"""


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


def investigate(incident_description: str, *, max_iterations: int = 20) -> str:
    """Run the agent loop and return the model's final analysis."""
    model = os.environ.get("OPENAI_MODEL", "gpt-5")
    client = OpenAI()

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": incident_description},
    ]

    for i in range(max_iterations):
        print(f"\n=== Iteration {i+1} ===", flush=True)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        msg = response.choices[0].message

        if msg.content:
            print(msg.content, flush=True)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"\n[tool] {tc.function.name}({tc.function.arguments})", flush=True)

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
        print(json.dumps(assistant_msg, indent=2), flush=True)  # Debug: print the full assistant message with tool calls
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return msg.content or ""

        for tc in msg.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            result = _execute_tool(tc.function.name, arguments)
            tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
            }
            print(json.dumps(tool_msg, indent=2), flush=True)  # Debug: print the full tool message
            messages.append(tool_msg)

    return "Agent exceeded max iterations without producing a final answer."
