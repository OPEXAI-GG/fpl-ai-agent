# Contributing

FPL AI Agent is developing toward a reliable, personalized subscription product. Changes
should improve a manager's decisions or a necessary foundation for that product.
See the [roadmap](ROADMAP.md) for direction and the [documentation index](docs/README.md)
for the current implementation.

## Set up development

Use Python 3.12+ (3.13 recommended) and uv. From the repository root:

```bash
uv sync --frozen
uv run --frozen fpl-agent --help
```

For the optional Discord worker, use Node.js 22+:

```bash
cd discord
npm ci
npm test
```

Use the repository's lockfiles. Update dependency declarations and their lockfile together
when a dependency change is necessary. Keep local data and credentials outside Git.

## Make a focused change

1. Inspect the relevant code and existing tests; preserve any pre-existing work.
2. Reproduce a substantive bug with a failing regression test.
3. Implement the smallest cohesive fix or feature and document externally visible behavior.
4. Run focused checks while developing, then the checks appropriate to the completed change.
5. Open a reviewable PR with the problem, resulting behavior, and actual validation results.

Documentation-only changes need link and example verification rather than new application
tests. Avoid unrelated formatting, speculative abstractions, and unnecessary dependencies.

## Required code checks

These match the CI workflow. Run from the repository root:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q --cov --cov-report=term-missing --cov-fail-under=85
uv run --frozen fpl-agent --help
uv run --frozen python -c 'from fpl_agent.rules import Rules; Rules.load(2026)'
```

Run the Discord tests when that component changes. The Python suite uses recorded data and
mocks by default; live source checks are separate. Preserve required assertions and the
coverage gate. Distinguish passing, failing, skipped, and unexecuted checks in the PR.

## Data and model changes

- Record units, source identity, observed time, and the forecast cutoff of new inputs.
- Keep training, tuning, and final testing chronological. Test post-cutoff invariance.
- Compare models on matched eligible cohorts, including relevant missing-data cases.
- Evaluate both the forecast and its effect on planning. Synthetic tests prove behavior;
  accuracy claims require suitable real-data evidence.
- Include dataset identity, commands, split, metrics, limitations, and feature modes with
  benchmark results. Retain the baseline when improvement is not established.
- Keep schema changes compatible with existing databases and exports; test migrations.
- Verify repeatability, bounded updates, and rollback when changing persistent learning state.

## Operational boundaries

Keep dry execution as the default. Make changes to live execution, notification behavior,
publishing, billing, or deployment explicit in the PR. Validate workflow stages on a copy of
the data before invoking workflows that publish releases or send messages.

Never include tokens, refresh tokens, webhook URLs, or account credentials in commits,
issue bodies, screenshots, or test logs. For configuration, use the documented environment
variables and secret stores in [operations](docs/operations.md).
