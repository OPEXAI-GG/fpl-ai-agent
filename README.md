# FPL AI Agent

[![CI](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/OPEXAI-GG/fpl-ai-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)

**Football forecasts, personalized team planning, and a weekly learning loop for Fantasy Premier League.**

FPL AI Agent combines a football database, player-level match simulations, and a constrained
planner to recommend transfers, chips, a starting XI, and captaincy. The aim is to help managers
make informed decisions and improve their teams and ranks.

The current product is a CLI with GitHub Actions automation and an optional Discord interface.
A customer-facing website and subscriptions are planned; see the [roadmap](ROADMAP.md).

**Dry by default:** the agent reports its recommendations without signing in to FPL or changing
your team. Live execution is implemented but has only been tested against mock servers.

[Documentation](docs/README.md) · [Usage](docs/usage.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

## What it does

- **Builds a football database:** FPL snapshots and points, fixtures, appearances, xG, shots,
  odds, and cross-source player and club identities.
- **Forecasts distributions:** minutes, goals, assists, clean sheets, saves, defensive
  contributions, cards, and bonus combine into expected points, risk, and upside.
- **Plans for your squad:** budget, selling prices, free transfers, club limits, and available
  chips constrain an eight-gameweek horizon by default (`--horizon` is configurable).
- **Explains and reviews decisions:** stores forecasts and plans, reviews finalized outcomes,
  and records bounded calibration and learning updates.
- **Automates the routine:** data refresh, planning, backtests, and weekly reviews have existing
  GitHub Actions workflows. Discord provides an optional command interface.

## Current status

| Area | Status |
| --- | --- |
| Database, simulation, and team planning | Implemented; covered by offline tests and recorded data |
| Weekly evaluation and learning | Implemented with finality checks, matched comparisons, and rollback |
| Workload feature | Shadow by default; historical validation has not established an accuracy gain |
| Live FPL execution | Implemented; real-account behavior remains unverified |
| Website and subscriptions | Planned |

Performance evidence and its limitations are documented in [model validation](docs/intelligence.md)
and the [runtime verification report](docs/validation-2026-11-04.md). Rank improvement is an
objective, not a guaranteed outcome.

## Quick start

Requirements: Python 3.12+ (3.13 recommended), [uv](https://docs.astral.sh/uv/), and Git.
Node.js 22+ is needed only for the Discord component.

```bash
uv sync --frozen
uv run --frozen fpl-agent --help
uv run --frozen fpl-agent status
```

The offline test suite needs no FPL account or API keys:

```bash
uv run --frozen pytest -q
```

For a team recommendation, first prepare the database and supply your public FPL entry ID.
Follow the [first-plan guide](docs/usage.md#your-first-team-plan).

## Documentation

| Guide | Contents |
| --- | --- |
| [Usage](docs/usage.md) | Prepare data, request a team plan, backfill history, and use the CLI |
| [Operations](docs/operations.md) | Configuration, workflows, data storage, and execution modes |
| [Architecture](docs/architecture.md) | Database, statistical models, simulation, planner, and rails |
| [Evaluation and learning](docs/intelligence.md) | Cutoffs, workload features, promotion, and rollback |
| [Runtime verification](docs/validation-2026-11-04.md) | Dataset identity, observed results, and workflow evidence |
| [Discord](docs/discord-bot.md) | Commands, setup, and deployment configuration |
| [Contributing](CONTRIBUTING.md) | Development workflow, validation, and review expectations |
| [Roadmap](ROADMAP.md) | Direction toward a customer-facing subscription product |

## Repository structure

```text
src/fpl_agent/       Application and CLI
  data/             Source clients and public squad state
  db/               Schema, ingestion, identity resolution, and quality checks
  model/            Strength, minutes, player rates, simulation, and projections
  solver/           Transfers, chips, lineup, and captaincy
  execution/        Safety checks and dry/live execution
rules/              Versioned FPL season rules
tests/             Offline, contract, property, workflow, and end-to-end tests
discord/           Discord worker and its tests
scripts/           Data restore/publish and fixture utilities
docs/              User, developer, and validation guides
.github/           CI, automation, and contribution templates
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the required checks and model-change evidence.
Report reproducible problems with the [bug form](https://github.com/OPEXAI-GG/fpl-ai-agent/issues/new?template=bug_report.yml)
or propose a user outcome with the [feature form](https://github.com/OPEXAI-GG/fpl-ai-agent/issues/new?template=feature_request.yml).
