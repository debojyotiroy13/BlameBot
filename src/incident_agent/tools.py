"""Tool functions exposed to the incident-response agent."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from github import Auth, Github
from github.GithubException import UnknownObjectException

_repo = None


def _get_repo():
    global _repo
    if _repo is None:
        token = os.environ["GITHUB_TOKEN"]
        repo_name = os.environ["GITHUB_REPO"]
        _repo = Github(auth=Auth.Token(token)).get_repo(repo_name)
    return _repo


def search_logs(query: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Return log lines from Fly.io matching `query` (regex, case-insensitive; falls back to literal)."""
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    token = os.environ.get("FLY_API_TOKEN", "")
    app_name = os.environ.get("FLY_APP_NAME", "svadesha-api")

    if not token:
        return [{"error": "FLY_API_TOKEN not set. Get your token with: flyctl auth token"}]

    url = f"https://api.machines.dev/v1/apps/{app_name}/logs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }

    matches: list[dict[str, Any]] = []
    scanned = 0

    try:
        with requests.get(url, headers=headers, stream=True, timeout=15) as resp:
            if resp.status_code == 401:
                return [{"error": "Fly.io auth failed. Refresh your token: flyctl auth token"}]
            if resp.status_code == 404:
                return [{"error": f"App '{app_name}' not found on Fly.io. Check FLY_APP_NAME."}]
            resp.raise_for_status()

            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data: "):
                    continue
                data_str = raw[6:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                    if not isinstance(event, dict):
                        continue
                    # Fly.io SSE shape: {"event":"log","data":{...}} or flat {message, timestamp, level}
                    nested = event.get("data")
                    payload = nested if isinstance(nested, dict) else event
                    msg = payload.get("message", "")
                    if not msg:
                        continue
                    scanned += 1
                    ts = payload.get("timestamp", "")
                    level = payload.get("level", "")
                    text = f"[{ts}] [{level}] {msg}" if ts else msg
                    if pattern.search(text):
                        matches.append({"line": scanned, "text": text})
                        if len(matches) >= max_results:
                            break
                except json.JSONDecodeError:
                    continue

                if scanned >= 2000:  # cap at 2000 lines per call
                    break

    except requests.exceptions.Timeout:
        if not matches:
            return [{"error": "Timed out fetching Fly.io logs. The app may be idle or the token expired."}]
    except requests.exceptions.RequestException as e:
        return [{"error": f"Failed to fetch Fly.io logs: {e}"}]

    if not matches:
        return [{"info": f"No log lines matched '{query}' in the last {scanned} entries."}]
    return matches


def get_file(path: str, ref: str = "HEAD") -> str:
    """Fetch a file from the configured GitHub repo at the given ref."""
    repo = _get_repo()
    if ref == "HEAD":
        ref = repo.default_branch
    try:
        contents = repo.get_contents(path, ref=ref)
    except UnknownObjectException:
        try:
            root = repo.get_contents("", ref=ref)
            listing = ", ".join(c.path for c in root) if isinstance(root, list) else ""
            return (
                f"Path '{path}' not found at ref '{ref}'. "
                f"Repo root contains: {listing}. "
                f"Call get_file again with one of these paths to explore further."
            )
        except Exception as e:
            return f"Path '{path}' not found at ref '{ref}', and listing root failed: {e}"
    if isinstance(contents, list):
        return f"Path '{path}' is a directory containing: " + ", ".join(
            c.name for c in contents
        )
    return contents.decoded_content.decode("utf-8", errors="replace")


def get_recent_commits(n: int = 10, branch: str | None = None) -> list[dict[str, Any]]:
    """Return the n most recent commits on the given branch (default: repo default branch)."""
    repo = _get_repo()
    branch = branch or repo.default_branch
    out: list[dict[str, Any]] = []
    for i, c in enumerate(repo.get_commits(sha=branch)):
        if i >= n:
            break
        author = c.commit.author
        out.append(
            {
                "sha": c.sha,
                "author": author.name if author else "unknown",
                "date": author.date.isoformat() if author else None,
                "message": c.commit.message,
            }
        )
    return out


def get_diff(sha: str) -> str:
    """Return the patch (diff) for a single commit."""
    repo = _get_repo()
    commit = repo.get_commit(sha)
    parts: list[str] = []
    for f in commit.files:
        parts.append(
            f"--- {f.filename} ({f.status}, +{f.additions}/-{f.deletions})"
        )
        if f.patch:
            parts.append(f.patch)
        parts.append("")
    return "\n".join(parts)


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": (
                "Search the live Fly.io application logs for lines matching a query. "
                "Streams up to 2000 recent log lines from the app and filters them. "
                "The query is treated as a case-insensitive regex; falls back to literal match. "
                "Use this to find errors, stack traces, request IDs, or specific events "
                "around the time of the incident."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Regex pattern or substring to search for.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return.",
                        "default": 50,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file",
            "description": (
                "Fetch the contents of a file from the configured GitHub repository at a "
                "specific ref. Use this to read source code that looks suspicious based on "
                "log evidence or recent commits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path within the repository, e.g. 'src/auth/login.py'.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch name, tag, or commit SHA. Defaults to the repo's default branch.",
                        "default": "HEAD",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_commits",
            "description": (
                "List recent commits on the configured repository. Use this early in an "
                "investigation to identify changes that may have caused the incident."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "How many commits to return.",
                        "default": 10,
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to read commits from. Defaults to the repo's default branch.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diff",
            "description": (
                "Return the diff (patch) for a specific commit. Use this after "
                "get_recent_commits to inspect what a suspicious commit actually changed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sha": {
                        "type": "string",
                        "description": "Full commit SHA from get_recent_commits.",
                    },
                },
                "required": ["sha"],
            },
        },
    },
]


TOOL_DISPATCH = {
    "search_logs": search_logs,
    "get_file": get_file,
    "get_recent_commits": get_recent_commits,
    "get_diff": get_diff,
}
