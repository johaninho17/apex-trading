# Apex Application

This directory contains the actual Apex product: the FastAPI backend, the React frontend, the crypto bot runtime, and the local persistence used to operate and evaluate the system.

If the root [`README.md`](../README.md) explains the repo-level truth, this document explains the app-level truth.

## What Apex Is Right Now

Apex is currently a crypto-first trading terminal and bot runtime.

That means the important path through this codebase is:

- fetch and cache crypto market data
- rank a tradable crypto universe
- generate and gate symbol-level trade decisions
- execute or block those decisions
- record what happened
- score later outcomes
- turn those outcomes into bounded learning overlays
- expose the whole loop through the crypto UI

As of March 14, 2026, this learning loop was re-validated in the WSL-native runtime and proved to write new `decision_outcomes`, `symbol_policy_overlays`, and `brain_self_score_history` rows on the real SQLite database.

## Scope and Priority

### Primary focus

The active product surface is the crypto bot and its terminal.

The four primary user-facing crypto pages are:

- Dashboard
- Hub
- Learning
- Activity

These are the views that should shape most architecture, performance, and persistence decisions.

### Secondary but still present

The codebase still contains:

- Stocks
- DFS
- Events / Kalshi / Polymarket

Those domains are not deleted, but they are not the center of the current roadmap. Maintain them when touched, but do not let them distort crypto-critical startup paths, docs, or implementation priorities.

## Architecture

### Backend

The backend is a FastAPI application under `backend/`.

Important areas:

- `backend/main.py`
  - app startup and router registration
- `backend/routers/`
  - HTTP API surfaces
- `backend/services/crypto/`
  - the most important active backend package in the repo
- `backend/core/`
  - config, background services, shared runtime state
- `backend/data/`
  - local SQLite databases and JSON/JSONL runtime artifacts

### Frontend

The frontend is a React + Vite app under `frontend/`.

Important areas:

- `frontend/src/pages/crypto/`
  - the current main product pages
- `frontend/src/lib/cryptoApi.ts`
  - crypto API client contract
- `frontend/src/lib/cryptoQueries.ts`
  - React Query integration and cache behavior
- `frontend/src/components/crypto/`
  - crypto page components and inspectors

### Local persistence

The active crypto system depends heavily on local persisted state rather than ephemeral in-memory behavior. The main runtime DB is:

- `backend/data/crypto_bot.db`

This DB is operationally important. It stores the learning chain, action history, event state, universe snapshots, reports, and runtime traces that make the bot explainable and auditable.

## The Crypto Runtime Flow

At a high level, the bot loop does this:

1. load runtime config and account mode
2. fetch or reuse cached account, positions, and market state
3. build or refresh the trade universe
4. evaluate symbols for candidate actions
5. record candidate and decision traces
6. execute buys/sells or record blocks
7. run synthetic exit checks
8. evaluate matured decision outcomes
9. refresh symbol policy overlays
10. append a fresh brain self-score snapshot when feedback moved

This is not meant to be a hidden black box. A large part of the project value is that these steps are persisted and visible after the fact.

## Core Crypto Backend Files

If another AI agent has to debug or extend the live crypto system, these are the first files to read.

### Runtime, orchestration, and shell state

- `backend/services/crypto/bot.py`
  - main runtime loop, decision recording, feedback trigger point
- `backend/services/crypto/terminal_data.py`
  - shell endpoints, summary payloads, local-first terminal data assembly
- `backend/services/crypto/store.py`
  - SQLite persistence contract for traces, outcomes, overlays, reports, and event state
- `backend/services/crypto/market_data.py`
  - crypto quotes, bars, positions, account summaries, and local price fallback behavior

### Learning and self-scoring

- `backend/services/crypto/decision_feedback.py`
  - evaluates matured decision traces and records `decision_outcomes`
- `backend/services/crypto/policy_overlays.py`
  - computes per-symbol overlays and brain self-score snapshots

### News and event context

- `backend/services/crypto/news_pipeline.py`
  - ingest, classify, and persist Binance/CryptoPanic news items
- `backend/services/crypto/universe.py`
  - selects and ranks the active crypto universe

### Strategy and execution

- `backend/services/crypto/strategy.py`
  - candidate generation and brain/strategy logic
- `backend/services/crypto/execution.py`
  - order placement and stale-order cleanup behavior

## The Learning Contract

The most important persistence chain in the current project is:

- `candidate_trace`
- `brain_decision_trace`
- `live_experience`
- `decision_outcomes`
- `symbol_policy_overlays`
- `brain_self_score_history`

### What each table means

- `candidate_trace`
  - the bot noticed a possible action
- `brain_decision_trace`
  - the bot committed to buy, sell, or block, with reasoning payload
- `live_experience`
  - closed-trade experience used for longer-horizon performance memory
- `decision_outcomes`
  - forward-looking outcome labels at configured horizons
- `symbol_policy_overlays`
  - bounded per-symbol adjustments derived from accumulated outcomes
- `brain_self_score_history`
  - a periodic confidence/discipline/blocking/pnl snapshot of the bot itself

### Why this matters

The project does not currently treat self-learning as "live PPO weight mutation." Instead, the bot learns through persisted evidence, bounded overlays, and later retraining inputs. That design choice is intentional and should be preserved unless the user explicitly chooses a new direction.

### March 14, 2026 proof point

In the WSL-native runtime:

- `decision_outcomes` moved from `0` to `80`
- `symbol_policy_overlays` moved from `0` to `5`
- `brain_self_score_history` moved from `7` to `8`

That is the current proof that the feedback loop is functioning in practice.

## News and Event Context

The crypto system also tries to learn from market context, not only price bars.

### Current intended role

News and event state should help the bot understand symbol-specific catalysts across the active universe, not just generic BTC macro headlines.

### Important news behavior

- Binance and CryptoPanic are the active feed sources in the current crypto path
- news items are normalized, classified, and persisted into `news_events`
- aggregated symbol state is persisted into `symbol_event_state`
- the bot can use event bias and hard-veto context when evaluating trades

### Current caveat

The code path for per-symbol attribution was improved on March 14, 2026 by preserving requested asset context and recovering common non-BTC asset names from titles/body text.

However, the live WSL poll that same day returned no fresh provider rows:

- Binance fetch: `0`
- CryptoPanic fetch: `0`

So the code path is better and tested, but the stored DB still needs fresh upstream rows before broad live non-BTC news coverage is fully proven in persistence.

## Frontend Surfaces

### Dashboard

The tactical operating page.

Use it for:

- crypto summary and shell health
- market scanner
- watchlist interaction
- high-signal configuration and immediate decision context

### Hub

The broader market-intelligence view.

Use it for:

- top-universe review
- conviction sorting
- learned edge visibility
- per-symbol comparative context

### Learning

The self-evaluation view.

Use it for:

- decision outcomes
- overlays
- brain self-score history
- learning-loop visibility

### Activity

The audit trail.

Use it for:

- action feed
- reports
- event and runtime history

## WSL-First Setup

Run Apex from a WSL-native checkout, not from the Windows-mounted repo path.

Recommended working repo:

```bash
~/trading
```

### Backend setup

```bash
cd ~/trading/apex/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Frontend setup

```bash
cd ~/trading/apex/frontend
npm install
```

## Config and Secrets

### `backend/.env`

Holds local secrets and provider credentials.

Typical categories include:

- Alpaca credentials
- Gemini / LLM credentials
- CryptoPanic token
- Telegram secrets

Do not assume every environment has every credential. The crypto system should degrade gracefully when some upstream services are unavailable.

### `backend/config.json`

Holds runtime behavior, bot toggles, symbols, feedback horizons, startup behavior, and UI-linked settings.

Important current crypto areas include:

- symbol universe and discovery
- account mode and trading mode
- decision feedback horizons
- news polling configuration
- startup service toggles

Treat the current file as environment-specific runtime truth, not as a generic portable sample.

## Run Commands

### Backend

```bash
cd ~/trading/apex/backend
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001
```

### Frontend

```bash
cd ~/trading/apex/frontend
npm run dev
```

### Frontend build

```bash
cd ~/trading/apex/frontend
npm run build
```

## Validation Commands

These are the most useful commands for the current crypto-first workflow.

### Focused backend learning and news validation

```bash
cd ~/trading/apex/backend
source venv/bin/activate
pytest \
  tests/test_decision_feedback_contracts.py \
  tests/test_market_data_cache_contracts.py \
  tests/test_learning_feedback_runtime_contracts.py \
  tests/test_news_pipeline_backoff_contracts.py::NewsPipelineBackoffContracts::test_select_cryptopanic_assets_prefers_forced_then_top_discovery \
  tests/test_news_pipeline_backoff_contracts.py::NewsPipelineBackoffContracts::test_fetch_cryptopanic_posts_preserves_requested_assets_for_symbol_recovery \
  tests/test_news_pipeline_backoff_contracts.py::NewsPipelineBackoffContracts::test_ingest_news_items_recovers_non_btc_symbol_from_requested_asset_context \
  -q
```

### Learning audit against the real DB

```bash
cd ~/trading/apex/backend
source venv/bin/activate
python scripts/crypto_learning_audit.py --run-feedback --news-limit 100 --limit-per-pass 20
```

### Shell timing smoke

```bash
cd ~/trading/apex/backend
source venv/bin/activate
python scripts/crypto_terminal_data_timing.py
```

## How To Debug The Current System

### If the bot looks active but not learning

Check:

- `brain_decision_trace` is increasing
- `decision_outcomes` is increasing after eligible horizons
- `symbol_policy_overlays` is no longer zero
- `brain_self_score_history` gets a fresh row after feedback

Use:

- `scripts/crypto_learning_audit.py`

### If outcomes are not recording

Read:

- `backend/services/crypto/decision_feedback.py`
- `backend/services/crypto/market_data.py`
- `backend/services/crypto/store.py`

The current known-sensitive parts are:

- decision-time reference price extraction
- forward price lookup near the target horizon
- backlog ordering of pending traces

### If the crypto pages feel slow again

Read:

- `backend/services/crypto/terminal_data.py`
- `backend/services/crypto/bot.py`
- `backend/core/background_services.py`

Then rerun:

- `python scripts/crypto_terminal_data_timing.py`

### If news still looks BTC-heavy

Check both code and data separately.

Code path:

- `backend/services/crypto/news_pipeline.py`

Stored evidence:

- `news_events`
- `symbol_event_state`
- `scripts/crypto_learning_audit.py`

Do not assume poor stored coverage means the attribution code is still broken. It may also mean the providers simply did not return fresh rows in the current environment.

## Project Memory

For active planning and recent verified context, also read:

- [`../tasks/todo.md`](../tasks/todo.md)
- [`../tasks/APEX-WSL-PLAN.md`](../tasks/APEX-WSL-PLAN.md)
- [`../tasks/session_history_detailed.md`](../tasks/session_history_detailed.md)
- [`../tasks/lessons.md`](../tasks/lessons.md)

## Final Orientation

If a new agent lands here and needs a single sentence summary:

This app is currently a WSL-first, crypto-first trading terminal whose main engineering priority is a fast, observable bot that can learn per symbol from persisted real-world outcomes.
