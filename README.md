# BlameBot

> An AI-powered incident response agent that digs through your Fly.io logs and GitHub history to find the guilty commit — and tells you exactly how to fix it.

---

## What it does

When production breaks at 2am, BlameBot does the first 20 minutes of on-call work for you.

Give it an incident description. It will:

1. **Search your live Fly.io logs** for errors, stack traces, or suspicious patterns around the incident
2. **Pull recent commits** from your GitHub repo to find what changed
3. **Inspect the diff** of any commit that looks load-bearing
4. **Read the relevant source files** to understand the context
5. **Produce a root cause analysis** — with evidence citations (log lines, commit SHAs, file paths) — and a concrete suggested fix

---

## How it works

BlameBot runs a manual agent loop over the OpenAI Chat Completions API. The model decides which tools to call, in what order, and when it has enough evidence to form a conclusion.

```
Incident description
       │
       ▼
  ┌─────────────────────────────────────┐
  │           Agent Loop                │
  │                                     │
  │  ┌─────────┐    ┌────────────────┐  │
  │  │  OpenAI │◄──►│  Tool Dispatch │  │
  │  │  GPT-5  │    │                │  │
  │  └─────────┘    │  search_logs   │  │
  │                 │  get_file      │  │
  │                 │  get_commits   │  │
  │                 │  get_diff      │  │
  │                 └────────────────┘  │
  └─────────────────────────────────────┘
       │
       ▼
  Root cause + suggested fix
```

**Tools available to the agent:**

| Tool | What it does |
|---|---|
| `search_logs` | Streams live logs from Fly.io via `flyctl`, filters by regex |
| `get_file` | Fetches a file from your GitHub repo at any ref/branch/SHA |
| `get_recent_commits` | Lists the N most recent commits on a branch |
| `get_diff` | Returns the full patch for a specific commit |

---

## Setup

### Prerequisites

- Python 3.9+
- [`flyctl`](https://fly.io/docs/hands-on/install-flyctl/) installed and authenticated (`flyctl auth login`)
- An OpenAI API key
- A GitHub personal access token (repo read scope)

### Install

```bash
git clone https://github.com/debojyotiroy13/BlameBot
cd BlameBot
pip install -e .
```

### Configure

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5          # or gpt-4.1, gpt-4o

# GitHub — format: owner/repo
GITHUB_TOKEN=ghp_...
GITHUB_REPO=your-org/your-repo

# Fly.io — auth is handled by flyctl automatically
FLY_APP_NAME=your-app-name
```

---

## Usage

```bash
# Pass the incident description inline
incident-agent "Users are getting 500s on /checkout since the deploy at 14:30"

# Or pipe it in
echo "Database connection timeouts spiking on all instances" | incident-agent

# Increase iteration limit for deeper investigations
incident-agent --max-iterations 30 "Memory leak causing OOM restarts every ~2 hours"
```

### Example output

```
=== Iteration 1 ===
[tool] get_recent_commits({"n": 10})

=== Iteration 2 ===
[tool] get_diff({"sha": "a3f9c21..."})

=== Iteration 3 ===
[tool] search_logs({"query": "connection refused|timeout", "max_results": 50})

...

============================================================
FINAL ANALYSIS
============================================================
**Likely root cause:** The connection pool size was reduced from 20 to 2 in
commit a3f9c21, causing request queuing under any meaningful load.

**Evidence:**
- Log line 47: `[ERROR] connection pool exhausted after 30s`
- Commit a3f9c21 (2024-01-15): "chore: tighten db config" — changed
  `DATABASE_POOL_SIZE` from 20 to 2 in `config/database.py`
- `config/database.py` line 14: `POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", 2))`

**Suggested fix:** In `config/database.py`, change the default from `2` back
to `20`, or set `DATABASE_POOL_SIZE=20` in your Fly.io app secrets:
`flyctl secrets set DATABASE_POOL_SIZE=20 --app your-app`
```

---

## Project structure

```
blamebot/
├── src/
│   └── incident_agent/
│       ├── agent.py      # Agent loop — calls OpenAI, dispatches tools, iterates
│       ├── cli.py        # CLI entry point (argparse + dotenv)
│       └── tools.py      # Tool functions + OpenAI function schemas
├── .env.example          # Configuration template
├── pyproject.toml        # Package config and dependencies
└── README.md
```

---

## Roadmap

- [ ] Webhook trigger (PagerDuty / Grafana / Datadog alerts)
- [ ] Slack delivery — post the analysis to your incident channel
- [ ] Multi-repo support — trace incidents across service boundaries
- [ ] Historical log search via Datadog / CloudWatch / Logtail
- [ ] Auto-open a draft PR with the suggested fix
- [ ] Memory — learn from past incidents and past fixes

---

## Contributing

PRs welcome. Open an issue first for anything beyond a small fix.

---

## License

MIT
