# Incident Response Agent

## Goal
An agentic platform that connects to GitHub + app logs.
On an incident, it searches logs and recent commits/diffs,
then uses Claude to suggest a fix.

## Stack
- Python, FastAPI, Anthropic SDK, PyGithub
- Tools: search_logs, get_file, get_recent_commits, get_diff
- Output: Slack webhook

## Current task
Build the tool functions first, then the agent loop.