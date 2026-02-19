# Apex

> **Note**: For the high-level project intents, architecture, and the Deterministic PR Agent Loop, please refer to the [Root README](../README.md).

Apex is the unified trading workspace implementation encompassing:
- `alpaca` (stocks & crypto)
- `kalshi` (event contracts)
- `dfs` (sports betting)

This directory houses the codebase for the **FastAPI backend** and **React frontend**. It provides shared settings, synchronized notifications, and live/offline bot controls.

## What Apex Includes

- **Stocks & Crypto** (`/api/v1/alpaca`): portfolio, scanner, dynamic execution engine.
- **Events** (`/api/v1/kalshi`): market browsing, bot control, scalper endpoints.
- **DFS** (`/api/v1/dfs`): calculator, scanner/sniper, auto-slip builders.

## Repository Layout

```text
apex/
├── backend/
│   ├── main.py
│   ├── config.json              # local runtime settings (DO NOT COMMIT)
│   ├── integrations/            # vendored provider logic (alpaca/kalshi/dfs)
│   ├── core/
│   ├── routers/
│   ├── services/
│   └── tests/
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── run.sh
├── .gitignore
└── README.md
```

## Prerequisites

- Python 3.11+ (3.12 recommended)
- Node.js 20+
- npm 10+

## First-Time Setup

### 1. Clone and enter project

```bash
cd /Users/johan/Projects/trading/apex
```

### 2. Backend environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Frontend environment

```bash
cd ../frontend
npm install
```

## Required Local Config Files

These files are local-only and should never be committed with secrets or personal settings.

### `backend/.env`

Create `/Users/johan/Projects/trading/apex/backend/.env` for backend runtime secrets used by Apex.

### `backend/config.json` (required)

Create `/Users/johan/Projects/trading/apex/backend/config.json` before running the app.
This file stores UI settings/toggles (for example `events.kalshi.trading_mode`) and is persisted by the Settings API.

Example starter file:

```json
{
  "stocks": {
    "atr_multipliers": { "aggressive": 2.0, "conservative": 2.5, "trend": 3.0 },
    "rsi_period": 14,
    "sma_periods": [20, 50, 200],
    "ema_periods": [9, 21],
    "backtest_targets": {
      "aggressive_target_pct": 6.0,
      "aggressive_stop_pct": 3.0,
      "conservative_target_pct": 10.0,
      "conservative_stop_pct": 5.0
    },
    "scanner_min_price": 5.0,
    "scanner_min_volume": 500000
  },
  "dfs": {
    "sniper": {
      "min_line_diff": 1.5,
      "poll_interval": 30,
      "max_stale_window": 600,
      "max_movements": 100
    },
    "slip_builder": {
      "slip_sizes": [3, 4, 5],
      "min_edge_pct": 0.0,
      "top_n_slips": 5,
      "max_pool_size": 15
    },
    "ev_calculator": {
      "default_stake": 100.0,
      "kelly_fraction_cap": 0.25
    }
  },
  "events": {
    "kalshi": {
      "trading_mode": "live",
      "max_position_size": 100.0,
      "max_total_exposure": 1000.0,
      "stop_loss_pct": 10.0,
      "arbitrage_min_profit": 0.02,
      "market_maker_spread": 0.02,
      "copy_trade_ratio": 0.1,
      "copy_follow_accounts": [],
      "bot_detection_threshold": 0.7,
      "bot_interval": 60
    }
  }
}
```

## Run

### One command

```bash
cd /Users/johan/Projects/trading/apex
chmod +x run.sh
./run.sh
```

### Manual run

Backend:

```bash
cd /Users/johan/Projects/trading/apex/backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

Frontend:

```bash
cd /Users/johan/Projects/trading/apex/frontend
npm run dev
```

## URLs

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Settings and Toggles Behavior

- UI toggles do **not** modify `.env` files.
- Toggles persist to `/Users/johan/Projects/trading/apex/backend/config.json` via `/api/v1/settings`.
- `Trading Mode` in Events settings controls execution gating:
  - `Live`: execution endpoints allowed
  - `Offline`: execution blocked, detection alerts can still fire
- Copy trading controls:
  - `events.kalshi.copy_trade_ratio` controls position scaling (0-1)
  - `events.kalshi.copy_follow_accounts` supplies accounts for `copy` strategy
  - UI includes a **Copy Trading** bot-start button in Kalshi dashboard

## Public Git Safety

- `.env` files are ignored.
- `backend/config.json` is ignored.
- Do not commit private keys, API secrets, or personal runtime settings.
- If secrets were ever committed previously, rotate them before publishing.
- Apex no longer requires sibling `alpaca/`, `kalshi/`, or `sportsbetting/` code folders at runtime.

## Validation Commands

Backend tests:

```bash
cd /Users/johan/Projects/trading/apex/backend
./venv/bin/python -m pytest tests/test_smoke_api.py tests/test_api_contracts.py -q
```

Frontend build:

```bash
cd /Users/johan/Projects/trading/apex/frontend
npm run build
```

## Troubleshooting

### Kalshi auth error: `INCORRECT_API_KEY_SIGNATURE`

Check that Apex is pointing to the intended Kalshi credentials and RSA key path. Ensure key file path and key contents match the account used for the API key.

### Toggle changed in UI but behavior unchanged

- Click **Save** in Settings.
- Verify `backend/config.json` updated.
- Restart running bot/session if mode was captured at bot start.

## License

Private project. Share only after removing or rotating any sensitive credentials.
