"""Tool functions exposed to the incident-response agent."""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from typing import Any

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


def _target_branch() -> str:
    """Branch to investigate and open PRs against.

    Reads GITHUB_BRANCH from the environment; falls back to the repo's
    default branch so existing setups that don't set the var keep working.
    """
    explicit = os.environ.get("GITHUB_BRANCH", "").strip()
    return explicit if explicit else _get_repo().default_branch


def search_logs(query: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Return log lines from Fly.io matching `query` (regex, case-insensitive; falls back to literal)."""
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    app_name = os.environ.get("FLY_APP_NAME", "svadesha-api")

    try:
        proc = subprocess.run(
            ["flyctl", "logs", "--app", app_name],
            capture_output=True,
            timeout=12,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not output.strip():
            stderr = proc.stderr.decode("utf-8", errors="replace")
            return [{"error": f"flyctl logs failed: {stderr[:300]}"}]
    except FileNotFoundError:
        return [{"error": "flyctl not found. Install with: brew install flyctl"}]
    except subprocess.TimeoutExpired as e:
        # flyctl streams indefinitely — grab whatever arrived in the window
        output = (e.stdout or b"").decode("utf-8", errors="replace")

    lines = output.splitlines()
    matches: list[dict[str, Any]] = []
    for i, line in enumerate(lines, start=1):
        if pattern.search(line):
            matches.append({"line": i, "text": line})
            if len(matches) >= max_results:
                break

    if not matches:
        return [{"info": f"No lines matched '{query}' in {len(lines)} log entries."}]
    return matches


def get_file(path: str, ref: str = "HEAD") -> str:
    """Fetch a file from the configured GitHub repo at the given ref."""
    repo = _get_repo()
    if ref == "HEAD":
        ref = _target_branch()
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
    branch = branch or _target_branch()
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


def create_fix_pr(
    title: str,
    body: str,
    file_path: str,
    new_content: str,
    base_branch: str | None = None,
) -> dict[str, Any]:
    """Open a GitHub PR on a new branch with a single-file fix."""
    repo = _get_repo()
    base = base_branch or _target_branch()

    # Create a timestamped branch so multiple runs don't collide
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"blamebot/fix-{ts}"

    base_sha = repo.get_branch(base).commit.sha
    repo.create_git_ref(f"refs/heads/{branch_name}", base_sha)

    # Commit the corrected file
    try:
        existing = repo.get_contents(file_path, ref=base)
        repo.update_file(
            path=file_path,
            message=f"fix: {title}",
            content=new_content,
            sha=existing.sha,
            branch=branch_name,
        )
    except UnknownObjectException:
        repo.create_file(
            path=file_path,
            message=f"fix: {title}",
            content=new_content,
            branch=branch_name,
        )

    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base,
    )

    return {
        "pr_url": pr.html_url,
        "pr_number": pr.number,
        "branch": branch_name,
        "file_changed": file_path,
    }


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
    {
        "type": "function",
        "function": {
            "name": "create_fix_pr",
            "description": (
                "Open a GitHub pull request on a new branch containing the fix. "
                "Call this once — only after you are confident in the root cause. "
                "Use get_file first to read the current file content, apply your fix, "
                "then pass the complete corrected file here. The PR body should include "
                "your full analysis: root cause, evidence citations, and fix explanation. "
                "Do NOT call this tool more than once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short PR title, e.g. 'fix: restore database pool size to 20'.",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "Full PR description. Include: likely root cause, evidence (log lines, "
                            "commit SHAs, file paths), and a clear explanation of what the fix changes and why."
                        ),
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Repo-relative path to the file being fixed, e.g. 'src/config/db.py'.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Complete new content of the file after the fix is applied.",
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Branch to open the PR against. Defaults to the repo's default branch.",
                    },
                },
                "required": ["title", "body", "file_path", "new_content"],
            },
        },
    },
]


TOOL_DISPATCH = {
    "search_logs": search_logs,
    "get_file": get_file,
    "get_recent_commits": get_recent_commits,
    "get_diff": get_diff,
    "create_fix_pr": create_fix_pr,
}
