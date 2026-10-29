---
name: ci-local
description: Run the same checks as .github/workflows/ci.yml locally before pushing (ruff, format, tests with the 85% coverage gate, CLI smoke, Discord bot tests).
disable-model-invocation: true
---

# Local CI

Reproduce `.github/workflows/ci.yml` exactly. Run each step from the repo root, in order, and keep
going after a failure so the report covers every gate.

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=85`
5. CLI smoke:
   - `uv run fpl-agent --help`
   - `uv run python -c "from fpl_agent.rules import Rules; Rules.load(2026)"`
6. Discord bot (only if anything under `discord/` changed versus `master`, or if asked):
   `cd discord && npm ci && npm test`

## Report

A short table: step, pass/fail, and for failures the key lines (failing test names, ruff codes,
coverage percentage and the files dragging it down). Do not fix anything unless asked; if
`ruff format --check` is the only failure, say that `uv run ruff format .` fixes it.
