# Trading Workspace

This repository is the working home for Apex, a trader-operated automation stack whose current center of gravity is the crypto bot inside `apex/`.

The most important current truth is simple:

- the active product is the Apex crypto bot and its supporting terminal
- the active implementation environment is WSL, not the Windows-mounted repo path
- the main engineering priority is making the bot observable, fast, and self-improving per coin
- Stocks, DFS, and Events still exist in the codebase, but they are not the primary product focus right now

As of March 14, 2026, the crypto learning loop was re-validated in the WSL-native runtime and proved to write new `decision_outcomes`, `symbol_policy_overlays`, and `brain_self_score_history` rows on the real database. That matters because it means the repo is no longer only tracing decisions in theory; the live feedback path is now producing persisted learning artifacts in practice.

## What This Repo Is

At a high level, this repo contains:

- a FastAPI backend
- a React + Vite frontend
- local persistence for trading state, learning traces, reports, and activity history
- multiple trading/betting domains that share one workspace

Today, those domains do not have equal priority.

### Current priority order

1. Crypto bot runtime and crypto terminal
2. Crypto learning, news/event context, and per-symbol operator visibility
3. Documentation and runbooks that reflect the verified crypto system
4. Secondary maintenance of Stocks, DFS, and Events surfaces

If a future engineer or AI agent needs to decide where to spend effort first, default to the crypto path unless the user explicitly redirects the work.

## Current Product Scope

### Crypto

Crypto is the active surface of the project.

The current product is not just "a dashboard" and not just "a trading script." It is a full crypto operating loop:

- scan and rank a tradable crypto universe
- generate symbol-level trade candidates
- record what the bot bought, skipped, or blocked
- evaluate later price outcomes against those decisions
- convert outcomes into bounded symbol-level policy overlays
- surface the resulting state in the Dashboard, Hub, Learning, and Activity views

This is the part of the repo that should be treated as the source of present-day product truth.

### Stocks

Stocks infrastructure is still present, including scanners, analysis pages, and older Alpaca-oriented code, but it is currently de-prioritized. Do not remove it casually, but do not let it steer the architecture of the active crypto workflow.

### DFS

DFS tooling remains in the repo for calculators, scanning, and slip-building, but it is currently parked compared with crypto. It should be maintained carefully when touched, not treated as the main roadmap.

### Events

Events and Kalshi research/scalping code also remain available, but they are not the lead product surface right now. They should not sit on the crypto-critical startup path or dominate shared documentation.

## Repo Map

The repo root is mostly orchestration, policy, and project memory. The real application lives under `apex/`.

### Important top-level areas

- `apex/`
  - the actual application: backend, frontend, runtime scripts, tests
- `tasks/`
  - active working memory for the project
  - use this first before guessing from stale docs
- `model_context/`
  - local agent guidance used during implementation work in this repo
- `.github/workflows/`
  - CI and automation policy for the repository

### Important project-memory files

If you are a new AI agent or engineer taking over work, read these early:

- `tasks/todo.md`
  - active implementation checklist and review notes
- `tasks/APEX-WSL-PLAN.md`
  - current WSL-first execution roadmap
- `tasks/session_history_detailed.md`
  - detailed handoff of recent work, decisions, validations, and unresolved issues
- `tasks/lessons.md`
  - durable corrections and workflow rules learned during implementation

Those files are the fastest way to avoid repeating already-resolved mistakes.

## The Crypto Bot, In One Paragraph

The crypto bot runs inside the Apex backend, evaluates a ranked crypto universe, produces per-symbol decision traces, and learns from later market outcomes. It is designed around visible, persisted state rather than opaque "trust me" automation. The core learning chain is:

- `candidate_trace`
- `brain_decision_trace`
- `live_experience`
- `decision_outcomes`
- `symbol_policy_overlays`
- `brain_self_score_history`

That chain is now important enough to treat as a contract. If future work touches the bot, do not casually break or bypass it.

## Verified Current State

As of March 14, 2026:

- the repo was cut over to a WSL-native working copy at `~/trading`
- focused WSL validation passed for the learning and market-data changes
- a real WSL audit pass proved:
  - `decision_outcomes` moved from `0` to `80`
  - `symbol_policy_overlays` moved from `0` to `5`
  - `brain_self_score_history` moved from `7` to `8`
- the feedback loop now works even when Alpaca bars are unavailable, because it can fall back to stored universe snapshot prices

One important nuance remains on the news side:

- the code path for broader per-symbol news attribution was improved and covered by tests
- but on March 14, 2026, a forced live Binance + CryptoPanic poll in WSL returned `0` fresh rows
- so the current stored DB still does not yet prove broad live non-BTC news coverage, even though the attribution logic is better than before

That means "bot learning is now proven" is true, while "live news breadth is fully proven in the current DB" is not yet fully true.

## WSL-First Operating Model

This project should now be treated as WSL-first.

### Use WSL for

- Python environments
- pytest
- backend runtime
- frontend install/build/dev commands
- SQLite-backed runtime validation
- bot scripts and timing audits

### Avoid

- mixing Windows Python and WSL Python against the same backend tree
- mixing Windows Node and WSL Node against the same frontend tree
- running the active app from `/mnt/c/Users/.../trading` once the WSL-native repo exists

The mixed Windows/WSL workflow caused enough instability that it is now considered a project risk, not a convenience.

## How To Work In This Repo

If you are continuing implementation work:

1. Read `tasks/todo.md`, `tasks/lessons.md`, and `tasks/session_history_detailed.md`.
2. Work from the WSL-native repo if you are running code.
3. Keep the crypto bot as the main product frame unless told otherwise.
4. Validate behavior with tests, audits, and timing scripts before declaring success.
5. Update the project-memory docs after substantial work.

## Where To Go Next

- For the detailed app runbook, architecture, and validation commands, read [`apex/README.md`](apex/README.md).
- For crypto-specific backend mechanics, `apex/backend/services/crypto/` is the most important code area.
- For current priorities and what is still open, start with `tasks/todo.md`.

## Short Version

If you only remember five things about this repository, remember these:

1. Apex is crypto-first right now.
2. The bot and its learning loop are the center of the project.
3. WSL is the correct execution environment.
4. Stocks, DFS, and Events are still present but secondary.
5. The README and task docs should describe verified behavior, not aspirational architecture.
