# Bootstrap & Deployment Guide

> How to set up AgentOS for local development and push to GitHub.

## Prerequisites

- Python >= 3.11
- Git >= 2.40 (Windows: Git for Windows with SSH support)
- An SSH key registered with GitHub
- (Optional) A running OpenClaw instance for the OpenClaw driver

## Local Setup

```bash
git clone [email protected]:increase2002/AgentOS.git
cd AgentOS
pip install -e .[dev]
pytest
```

This installs:
- `agentos` (editable, includes CLI `agentos` command)
- `openai`, `httpx`, `websockets`, `fastapi`, `uvicorn`, `pydantic`
- dev extras: `pytest`, `pytest-asyncio`, `ruff`, `mypy`

## Dogfooding the bus

```bash
agentos send --to codex --from openclaw --text "hello"
agentos receive --to codex
agentos search "tool_subset"
agentos show --task t-001
agentos inbox
```

Full workflow: see [docs/03-dogfood-bus.md](docs/03-dogfood-bus.md).

## Running demos

```bash
# Plan B full loop (TASK_REQUEST -> Engine -> reply)
python examples/demo_bus_loop.py

# Pure Engine 4-stage DAG + partial-success replay
python examples/demo_dogfood.py
```

## GitHub Push from Windows (SSH)

### Pitfall 1: Git for Windows SCP-style URL bug

Git for Windows sometimes mis-parses SSH URLs of the form
`[email protected]:user/repo.git`, treating `email protected` as the
hostname. This produces:

```
ssh: Could not resolve hostname email protected: Name or service not known
```

**Fix:** Add an SSH config alias to `~/.ssh/config`:

```
Host gh.increase2002
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_rsa
    IdentitiesOnly yes
```

Then use the alias in the remote URL:

```bash
git remote set-url origin gh.increase2002:increase2002/AgentOS.git
git push -u origin main
```

### Pitfall 2: GitHub auto-init README conflict

If you create the GitHub repo via the web UI with "Initialize with README",
GitHub commits a default README to `main`. When you push local commits
that also include `README.md`, git refuses with a non-fast-forward error.

**Fix (keep local README):**

```bash
git pull origin main --allow-unrelated-histories
git checkout --ours README.md
git add README.md
git commit -m "merge: keep local README over GitHub default"
git push -u origin main
```

## Test Run

```bash
pytest                          # all tests (~150 across 14 modules)
pytest tests/test_memory.py      # memory tests only
pytest tests/test_planner.py     # planner tests only
```

If you hit `tmp_path` permission errors on Windows (pytest trying to
clean up a stale temp dir):

```bash
pytest --basetemp=".pytest-tmp-$(date +%s)" -p no:cacheprovider
```

## Directory Layout

See [`README.md`](../README.md), [`docs/01-protocol-v0.1.md`](01-protocol-v0.1.md),
and [`docs/ADR/`](ADR/).