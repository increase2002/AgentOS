# Bootstrap & Deployment Guide

> How to set up AgentOS for local development and push to GitHub.

## Prerequisites

- Python >= 3.11
- Git >= 2.40 (Windows: Git for Windows with SSH support)
- An SSH key registered with GitHub

## Local Setup

```bash
git clone git@github.com:increase2002/AgentOS.git
cd AgentOS
pip install -e .[dev]
pytest
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
pytest                          # all tests
pytest tests/test_schemas.py    # schema tests only (no openai dep)
```

## Directory Layout

See [`README.md`](../README.md), [`docs/01-protocol-v0.1.md`](01-protocol-v0.1.md),
and [`docs/ADR/`](ADR/README.md).