# Apex Crypto Bot — Complete Technical Reference

> **Version:** Sprint 4 Complete | **Last Updated:** March 2026
> 
> This document is the single source of truth for how the Apex crypto bot works — every strategy, every safeguard, every exit mechanic, and how they interact.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [The Intelligence Stack](#2-the-intelligence-stack)
3. [Trading Strategies](#3-trading-strategies)
4. [The Sizing Engine](#4-the-sizing-engine)
5. [Strategy Memory — How Buys Remember Their Strategy](#5-strategy-memory)
6. [Exit Mechanics](#6-exit-mechanics)
7. [Capital Rotation Engine](#7-capital-rotation-engine)
8. [Safeguard Layers](#8-safeguard-layers)
9. [Capacity Mode](#9-capacity-mode)
10. [The Full Execution Pipeline](#10-the-full-execution-pipeline)
11. [Action Feed Reference](#11-action-feed-reference)
12. [Configuration Reference](#12-configuration-reference)

---

## 1. System Architecture

The bot runs as a background service inside the Apex FastAPI backend. Every N seconds (`cycle_interval_sec`), `_cycle()` is called and processes every symbol in the watchlist.

```
┌─────────────────────────────────────────────────────────────┐
│                        _cycle()                             │
│                                                             │
│  1. Fetch account, positions, cash, exposure                │
│  2. Oracle refresh (Gemini macro score)                     │
│  3. Capacity sentinel check (skip loop if truly full)       │
│  4. For each symbol:                                        │
│     a. Fetch 15M + 1M OHLCV bars                           │
│     b. Enrich indicators (RSI, EMA, BB, MACD, ATR)         │
│     c. evaluate_symbol() → signal candidate                 │
│     d. Run buy/sell gate stack                              │
│     e. Execute or rotate                                    │
│  5. Synthetic exits pass (all open positions)               │
└─────────────────────────────────────────────────────────────┘
```

**Key files:**
| File | Role |
|---|---|
| `bot.py` | Main loop, all gates, execution, exits |
| `strategy.py` | Signal generation for all strategies |
| `oracle.py` | Gemini macro sentiment scoring |
| `risk_engine.py` | Per-asset tier classification, SL/TP bands |
| `market_data.py` | Alpaca bar/position/quote fetching |
| `execution.py` | Alpaca order placement |
| `store.py` | SQLite persistence, action feed, signal state |
| `self_tuner.py` | Session-end AI parameter adjustment |
| `report_generator.py` | Hourly/daily Gemini-graded performance reports |

---

## 2. The Intelligence Stack

The bot uses a **layered brain** — each layer adds or validates the previous:

```
Layer 1: Technical Indicators (always on)
    RSI14 (15M + 1M), EMA fast/slow, Bollinger Bands, MACD, ATR, volume

Layer 2: Strategy Signal Generator
    Evaluates indicator conditions → produces a signal candidate with score + notional

Layer 3: AI / DRL Model (optional)
    Deep Reinforcement Learning model trained on historical crypto OHLCV
    Outputs: BUY(1), SELL(2), HOLD(0) — only BUY/SELL trigger further processing

Layer 4: Oracle (Gemini macro score)
    Refreshed every 15 min via Gemini API
    Outputs: macro_risk_mult = [0.2, 1.8]
    1.0 = neutral, >1.0 = bullish, <1.0 = bearish
    Scales position sizing up/down accordingly

Layer 5: SLM Gatekeeper (DualBrain mode only)
    Small Language Model (Ollama: qwen3:8b / llama3) validates DRL signal
    Both must agree on direction AND SLM confidence ≥ 70% to proceed
    Prevents DRL from trading on purely technical patterns with bad macro context
```

### Active Brain Modes

| Mode | Description |
|---|---|
| `drl` | DRL only. Fast, no LLM call. |
| `dual_brain` | DRL spots pattern, SLM validates with live market context. Higher accuracy, slower. |
| `gemini_only` | No DRL. Raw Gemini macro sentiment drives signals. |

---

## 3. Trading Strategies

Each strategy has a distinct signal condition, conviction score range, and optimal hold profile. After Sprint 1 (EX-1), every position is tagged with the strategy that opened it, which determines its TP, SL, and max hold duration.

---

### 3.1 `micro_scalper`

**Philosophy:** Catch very short, sharp momentum moves in the 1–2% range. Fast in, fast out.

**Signal conditions:**
- 1M RSI dips below `rsi_1m_sniper_dip` threshold (default: 48)
- Price confirms a micro-dip on 1-minute chart
- Oracle macro ≥ 0.8 (won't scalp in bear macro)

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 1.2% |
| Stop Loss | 1.5% |
| Max Hold | 90 minutes |
| TP1 Fraction | 50% |

**Reinforcement:** ATR stop runs tight. Time exit closes at 90 min if TP not hit. SL lockout 30–60 min after stop. Loss streak pause after 3 consecutive stops.

---

### 3.2 `mean_reversion`

**Philosophy:** RSI oversold bounce. Buy when sellers are exhausted, sell when momentum recovers to neutral.

**Signal conditions:**
- 15M RSI ≤ `rsi_oversold` (default: 35)
- Price near or below lower Bollinger Band
- Volume spike confirms reversal

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 2.0% |
| Stop Loss | 2.5% |
| Max Hold | 4 hours |
| TP1 Fraction | 50% |

**Reinforcement:** RSI overbought exit fires at RSI ≥ 85 when in profit. Trailing stop activates after TP1.

---

### 3.3 `breakout_momentum`

**Philosophy:** Buy confirmed breakouts above recent resistance with volume confirmation.

**Signal conditions:**
- Price breaks above 20-bar high (`breakout_lookback_bars`)
- Volume ≥ `breakout_volume_mult` × average volume (default: 1.5×)
- ATR confirms sufficient volatility for meaningful breakout

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 2.5% |
| Stop Loss | 3.0% |
| Max Hold | 6 hours |
| TP1 Fraction | 50% |

**Reinforcement:** Hard narrative veto fires if Gemini reports fundamental bad news (SG-1). Correlation guard prevents entering a 3rd correlated breakout simultaneously.

---

### 3.4 `dynamic_dca` (Dollar Cost Averaging)

**Philosophy:** Add to a position during controlled dips, not random ones. Scientific dip buying.

**Signal conditions:**
- Already holds the asset
- Price drops ≥ `dca_dip_pct` (default: 1.5%) from average entry
- DCA interval has elapsed (`dca_interval_min`, default: 60 min)
- Universal Chop Lock DISABLED: dip is real (< -1.5% from entry)

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 2.5% |
| Stop Loss | 3.0% |
| Max Hold | 8 hours |
| TP1 Fraction | 50% |

**Reinforcement:** Chop Lock prevents DCA into a flat position. Only adds during genuine dips.

---

### 3.5 `ai_macro` / `dual_brain`

**Philosophy:** AI-driven trade that integrates DRL pattern recognition with live macro awareness.

**Signal conditions:**
- DRL outputs BUY or SELL signal
- (DualBrain) SLM validates with ≥ 70% confidence and agrees on direction
- 1M RSI sniper entry: waits for RSI dip ≤ `sniper_dip` before entering
- Fused score = (DRL confidence × 0.4) + (SLM confidence × 0.6)

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 3.0% |
| Stop Loss | ATR-dynamic (3.5–8%) |
| Max Hold | 12 hours |
| TP1 Fraction | 50% |

**Reinforcement:** Narrative veto (SG-1) hard-blocks buys when Gemini flags `extreme_negative`. Oracle macro factor scales position size. SL lockout prevents immediate re-entry after stop.

---

### 3.6 `swing_momentum`

**Philosophy:** Multi-day position. Ride a sustained directional move. EMA golden/death cross driven.

**Signal conditions:**
- EMA fast (20) crosses EMA slow (50) from below (golden cross) → BUY
- EMA fast crosses EMA slow from above (death cross) → SELL signal on existing position
- Oracle macro ≥ 1.0 preferred (won't swing in bear macro without high conviction)

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 5.0% |
| Stop Loss | 5.0% |
| Max Hold | 7 days |
| TP1 Fraction | **33%** (exits 1/3 at TP1, trails remaining 2/3) |

**Reinforcement:** Wider SL and TP give the trade room to breathe over days. Time exit protects against long-term stagnation. Correlation guard requires very high conviction (score ≥ 88) to hold a 3rd correlated position alongside.

---

### 3.7 `position_golden_cross`

**Philosophy:** Long-term hold. Monthly-timeframe signal. Full conviction position that holds through noise.

**Signal conditions:**
- 50-day MA crosses 200-day MA (Golden Cross) → BUY
- Requires strong macro (oracle ≥ 1.1)
- Score threshold: ≥ 80

**Exit profile:**
| Parameter | Value |
|---|---|
| TP1 | 8.0% |
| Stop Loss | 7.0% |
| Max Hold | 30 days |
| TP1 Fraction | **20%** (exits only 20% at TP1, holds 80% for long run) |

**Reinforcement:** Very wide SL/TP to avoid being shaken out by normal volatility. Time exit at 30 days ensures stagnant positions don't tie up capital forever.

---

## 4. The Sizing Engine

Every buy is sized by a 4-factor formula. No fixed dollar amounts — size adapts to market conditions dynamically.

```
equity_ceiling  = live_cash × notional_pct_of_cash
                  (clamped to hard_cap if configured)

conviction_factor = 0.30 + 0.70 × (score / 100)   → [0.30, 1.00]
oracle_factor     = clamp(oracle_mult, 0.5, 1.5)   → [0.50, 1.50]
rsi_depth         = min(1.0, |rsi - 50| / 30)      → [0.00, 1.00]
rsi_factor        = 0.70 + 0.30 × rsi_depth        → [0.70, 1.00]

raw_notional     = ceiling × conviction × oracle × rsi_factor
desired_notional = clamp(raw_notional, min_order_usd, ceiling)
allowed_notional = min(desired_notional, live_cash)
```

**What this means in plain English:**
- A **weak signal (score=20)** at **neutral RSI** with **bearish oracle** = 0.44 × ceiling (very small position)
- A **max conviction signal (score=100)** at **deep oversold RSI** with **bull oracle** = 100% of ceiling
- **No single buy ever exceeds what's in the cash account** — `allowed_notional` is hard-capped to `live_cash`

---

## 5. Strategy Memory

**The question: "Does a buy order remember what strategy it was bought on?"**

**Yes — fully, after Sprint 1 (EX-1).**

### How it works

**At buy-time:** When a buy executes successfully, before the order confirmation is logged, the bot immediately saves a `tp_config` record to signal_state (SQLite KV store):

```python
store.save_signal_state_sync(f"tp_config:{symbol}", {
    "opening_strategy":  "swing_momentum",
    "tp_pct":            5.0,         # Take-profit threshold
    "sl_pct":            5.0,         # Stop-loss threshold  
    "max_hold_min":      10080,        # 7 days in minutes
    "tp1_fraction":      0.33,         # Sell 33% at TP1
    "entry_ts_ms":       1741234567890 # Millisecond timestamp
})
```

**At exit-time (every cycle):** The synthetic exits loop reads `tp_config:{symbol}` for every open position:

```python
_pos_tp_cfg = store.get_signal_state_sync(f"tp_config:{symbol}")
if _pos_tp_cfg:
    tp_pct      = _pos_tp_cfg["tp_pct"]       # Override global config
    tp1_fraction = _pos_tp_cfg["tp1_fraction"] # Override sell fraction
    max_hold_min = _pos_tp_cfg["max_hold_min"] # For time-based exit
    entry_ts_ms  = _pos_tp_cfg["entry_ts_ms"]  # For age calculation
```

### What happens to old positions (bought before EX-1)?

Positions opened before this update have no `tp_config` record. The exit loop detects an empty dict and gracefully falls back to global config values (`tp_pct=3.0`, `tp1_fraction=0.5`, no time exit). No crash, no bad behavior.

### What cleans up the tp_config?

- On successful **time exit**: `tp_config:{symbol}` is cleared
- On **trailing stop** exit: cleared
- On **RSI overbought** exit: cleared (implicitly via position close)
- On **ATR stop-loss**: cleared via `tp1_hw_{symbol}` reset

---

## 6. Exit Mechanics

All exits are managed by the **Synthetic Exits** loop that runs at the end of every `_cycle()` pass over all currently open positions.

### 6.1 RSI Overbought Exit (Priority 1)

```
Trigger:  15M RSI ≥ 85 AND PnL > 0%
Action:   Close 100% of position immediately
Log:      synthetic_exit_rsi
Side effect: Resets loss_streak:{symbol} to 0 (SG-3)
```

**Why:** Captures violent pumps at the peak before the inevitable pullback. Exits while in profit regardless of TP threshold.

---

### 6.2 ATR Stop-Loss (Priority 2)

```
Trigger:  PnL ≤ -(ATR × 1.5), clamped between [3.5%, 8.0%]
          If tp_config exists: uses strategy sl_pct instead
Action:   Close 100% of position
Log:      synthetic_exit
Side effects:
  - EX-2: Sets sl_lockout:{symbol} for 6× cooldown (30–60 min)
  - SG-3: Increments loss_streak:{symbol}. If streak ≥ 3: sets streak_pause_until:{symbol} +4 hours
```

**Why:** Dynamic stop adapts to each coin's actual volatility. BTC (low ATR%) gets a tighter stop than a volatile altcoin. Flat strategy SL (from tp_config) overrides for swing/position trades that need room.

---

### 6.3 TP1 Fractional Exit (Priority 3)

```
Trigger:  PnL ≥ tp_pct (from tp_config or global)
Action:   Sell tp1_fraction of position (50% default, 33% for swing, 20% for position)
          Save tp1_taken_{symbol} timestamp (for EX-3 mutex)
          Save tp1_hw_{symbol} = current_price (trailing stop watermark)
          Reset loss_streak:{symbol} to 0 (SG-3)
Log:      synthetic_exit (partial)
```

**Why:** Locks in guaranteed profit while leaving upside open. The remaining portion is then managed by the trailing stop.

---

### 6.4 Trailing Stop (Priority 3, post-TP1)

```
Trigger:  TP1 was already taken AND current_price < (high_watermark × (1 - trailing_pct))
          trailing_pct = 2.5% default, 4.0% in bull macro (oracle ≥ 1.4)
Action:   Close 100% of remaining position
Log:      synthetic_exit (trailing)
```

**Why:** Lets winners grow without a fixed TP2 cap. Adapts to bull markets by widening the trail.

---

### 6.5 Time-Based Exit (Priority 4, EX-1)

```
Trigger:  Position age > max_hold_min (from tp_config) AND PnL > -1%
Action:   Close 100% of position
Log:      time_exit
Side effect: Clears tp_config:{symbol}
```

**Why:** Prevents scalper trades from sitting open for days tying up capital. A `micro_scalper` position at 90 minutes flat becomes dead weight — close it and redeploy.

**Note:** The -1% guard prevents forcing a loss exit on time alone. A position that missed TP but is slightly down will wait for a better price or its SL.

---

## 7. Capital Rotation Engine

When total exposure is maxed, instead of simply blocking new buys, the bot evaluates whether it makes more sense to **partially sell a weaker position** to fund a higher-conviction new opportunity.

### Rotation Candidate Scoring

```
rotation_score = (pnl_factor × 0.40) + (momentum_factor × 0.35) + (narrative_factor × 0.25)

pnl_factor        = min(1.0, pnl_pct / 20.0)        — trim winners first
momentum_factor   = min(1.0, (rsi - 50) / 30.0)     — trim overbought/fading positions
narrative_factor  = 0.7 if narrative is "negative"   — trim coins with bad news
```

### Safety Gates (ALL must pass)

| Gate | Condition |
|---|---|
| Score gate | New signal score ≥ 78 |
| Underwater gate | Candidate PnL ≥ -0.5% (never sell a loser) |
| Size gate | Candidate value ≥ 1.5× needed notional |
| Cooldown gate | Candidate not on cooldown |
| Same-coin gate | Can't rotate INTO the coin we'd sell |
| **EX-3 — TP1 Mutex** | Skip if TP1 already taken on this position today |
| **SG-4 — Cycle Cap** | Max 1 rotation per cycle |

### What Rotation Actually Does

1. Scores all open positions as rotation candidates
2. Picks the weakest (highest rotation score = most tired position)
3. Sells a **partial qty** (not full position) to free the needed notional
4. Immediately logs `capital_rotation` to action feed
5. Proceeds with the new buy

---

## 8. Safeguard Layers

All safeguards run as gates in the buy path. If any gate fails, the buy is skipped for that cycle. Each safeguard logs its own `action_type` to the feed.

### SG-1 — Hard Narrative Veto
**Trigger:** `narrative_label == "extreme_negative"` from Gemini  
**Action:** Hard block, regardless of DRL/SLM score  
**Feed:** `narrative_veto`  
**Why:** Math (score, RSI, ATR) can't price in a breaking news crisis. If Gemini reports BTC is collapsing due to regulatory action, no score justifies a buy.

---

### SG-2 — Correlation Risk Guard
**Trigger:** ≥ 2 positions already held in the same asset bucket AND new signal score < 88  
**Buckets:** `bluechip` (BTC/USD, ETH/USD) | `midalt` (SOL, AVAX, LINK, etc.)  
**Action:** Block new buy  
**Feed:** `correlation_veto`  
**Why:** Holding BTC + ETH + SOL is not diversification — it's 3× correlated beta. When BTC dumps 15%, they all dump. The guard requires very high conviction (88+) to add a 3rd correlated position.

---

### SG-3 — Consecutive Loss Streak Cooldown
**Trigger:** Same coin stopped out 3+ times in a row (current session)  
**Action:** 4-hour pause on that coin (`streak_pause_until:{symbol}`)  
**Feed:** `loss_streak_pause`  
**Resets:** On any profitable exit (RSI exit, TP1, trailing stop)  
**Why:** 3 consecutive losses on the same coin = the bot is trading against the trend. Pause and let the market settle before re-entering.

---

### SG-4 — Rotation Cap (1 per cycle)
**Trigger:** `_rotations_this_cycle >= 1`  
**Action:** Silently skip second rotation attempt  
**Why:** Multiple signals firing simultaneously could trigger 3–4 rotations in one cycle (6–8 trades), burning the daily trade quota instantly. One rotation per cycle is enough.

---

### SG-5 — Spread / Liquidity Check
**Trigger:** Bid-ask spread > 0.5% of mid price  
**Action:** Block buy  
**Feed:** `spread_veto`  
**Why:** A market order on a thin altcoin with a wide spread eats 0.5–1% immediately before the trade even starts moving. The spread guard protects against this invisible cost.

---

### Stale Order Blacklist (pre-existing)
**Trigger:** Symbol has 3+ PENDING_NEW failures  
**Action:** 24-hour ban on that symbol  
**Feed:** `order_blocked` (Blacklist Veto)

---

### Universal Chop Lock (pre-existing)
**Trigger:** DCA buy attempted while PnL is between -1.5% and +1.5%  
**Action:** Block DCA  
**Why:** If the position is just chopping around breakeven, adding more is random. Only DCA on real dips (< -1.5%) or confirmed winners (> +1.5%).

---

### Hard Daily Loss Halt (pre-existing)
**Trigger:** Session total PnL ≤ -`max_daily_drawdown_pct`  
**Action:** Full halt — no new buys, no sells, only monitoring  
**Feed:** `halt`

---

## 9. Capacity Mode

When total exposure ≥ 90% of the effective cap, the bot enters **Capacity Mode**. The goal is to stop chasing new positions and focus on maximizing what's already held.

```
Layer 1 — Pre-loop sentinel:
    If exposure ≥ 90% cap at cycle start:
    _cycle_at_capacity = True
    (signals still evaluated, but buys are pre-screened)

Layer 2 — Per-symbol at-capacity check:
    If at_capacity AND signal is "buy":
    skip silently — no feed entry, no DRL call

Layer 3 — Rotation attempt:
    If at-capacity AND new signal score ≥ 78:
    try capital rotation before giving up
    (this is the only buy path open at full capacity)
```

**What still runs at full capacity:**
- Synthetic exits (RSI exit, ATR stop, TP1, trailing stop, time exit)
- Sell signals from any strategy
- Info signals (logged but no order)
- Oracle refresh

---

## 10. The Full Execution Pipeline

Every buy signal passes through the following gates **in order**. Failing any gate exits immediately:

```
1.  [Capacity Mode]        at_capacity AND buy → skip
2.  [Stale Blacklist]      symbol banned for 24h → block (order_blocked)
3.  [EX-2 SL Lockout]     stopped-out within 30-60 min → block (sl_lockout)
4.  [SG-1 Narrative Veto] extreme_negative Gemini → block (narrative_veto)
5.  [SG-3 Loss Streak]    3+ consecutive stops this session → block (loss_streak_pause)
6.  [SG-2 Correlation]    ≥2 correlated positions AND score < 88 → block (correlation_veto)
7.  [Exposure Gate]        exposure ≥ max → attempt rotation OR skip
8.  [SG-4 Rotation Cap]   already rotated this cycle → skip
9.  [Max Positions]        too many open positions → block
10. [Chop Lock]            DCA into flat position → block
11. [Sizing Engine]        compute allowed_notional
12. [Min Order]            allowed < min_order_usd → block
13. [Per-buy Overflow]     exposure + notional > max → attempt rotation OR skip
14. [SG-5 Spread Check]    bid-ask spread > 0.5% → block (spread_veto)
15. [EXECUTE BUY]          place_crypto_order()
16. [EX-1 Tag Position]   save tp_config:{symbol} to signal_state
17. [Log]                  record_action(order_submitted)
```

---

## 11. Action Feed Reference

Every significant bot event is logged with an `action_type`. Here's the complete reference:

| action_type | What it means |
|---|---|
| `order_submitted` | Buy or sell executed successfully |
| `order_blocked` | Buy blocked (blacklist, min order, max positions) |
| `narrative_veto` | **[SG-1]** Gemini flagged extreme_negative — buy blocked |
| `sl_lockout` | **[EX-2]** Re-entry blocked after recent stop-loss |
| `loss_streak_pause` | **[SG-3]** Coin paused after 3 consecutive losses |
| `correlation_veto` | **[SG-2]** 3rd correlated position blocked |
| `spread_veto` | **[SG-5]** Bid-ask spread too wide — buy skipped |
| `capital_rotation` | Partial sell of holder to fund new buy |
| `synthetic_exit` | ATR stop or TP fractional exit |
| `synthetic_exit_rsi` | RSI overbought exit |
| `time_exit` | **[EX-1]** Position closed because max hold exceeded |
| `synthetic_exit_failed` | Exit tried but Alpaca rejected |
| `halt` | Daily drawdown limit hit — trading stopped |
| `oracle_refresh` | Gemini macro score updated |
| `signal` | Info signal (no order) |

---

## 12. Configuration Reference

Key parameters in `config.json` under `stocks.crypto`:

### Risk & Exposure
| Parameter | Default | Description |
|---|---|---|
| `max_total_exposure` | 5000.0 | Hard cap on total USD in positions |
| `max_daily_drawdown_pct` | 15.0 | Session loss limit before halt |
| `max_open_positions` | 4 | Maximum concurrent positions |
| `min_order_notional_usd` | 2.0 | Minimum trade size |

### Sizing
| Parameter | Default | Description |
|---|---|---|
| `notional_pct_of_cash` | 20.0 | % of cash used per trade ceiling |
| `max_notional_per_trade` | 500.0 | Hard cap per single trade |

### Exits (Global Fallback)
| Parameter | Default | Description |
|---|---|---|
| `take_profit_pct` | 3.0 | TP1 trigger (overridden per-strategy by EX-1) |
| `stop_loss_pct` | 1.8 | SL fallback (ATR-dynamic preferred) |
| `tp1_fraction` | 0.5 | Fraction to sell at TP1 (overridden per-strategy) |
| `rsi_exit_threshold` | 85.0 | RSI level for overbought exit |

### Cooldowns
| Parameter | Default | Description |
|---|---|---|
| `cooldown_sec` | 300 | Per-symbol cooldown after any trade |
| `anti_spam_sec` | 60 | Min seconds between same action type logs |

### AI
| Parameter | Default | Description |
|---|---|---|
| `active_brain` | `drl` | `drl`, `dual_brain`, or `gemini_only` |
| `use_ml_model` | true | Enable/disable DRL inference |
| `ollama_model` | `qwen3:8b` | SLM model name for DualBrain |

---

## Appendix: How Strategies Interact with Each Safeguard

| Strategy | SG-1 | SG-2 | SG-3 | EX-1 | EX-2 |
|---|---|---|---|---|---|
| micro_scalper | ✅ Hard blocked | ✅ Corr. guard | ✅ Streak pause | 90min exit | 30-60min lockout |
| mean_reversion | ✅ Hard blocked | ✅ Corr. guard | ✅ Streak pause | 4hr exit | 30-60min lockout |
| breakout_momentum | ✅ Hard blocked | ✅ Corr. guard | ✅ Streak pause | 6hr exit | 30-60min lockout |
| dual_brain | ✅ Hard blocked | ✅ Corr. guard | ✅ Streak pause | 12hr exit | 30-60min lockout |
| swing_momentum | ✅ Hard blocked | ✅ Corr. guard (88 req) | ✅ Streak pause | 7-day exit | 30-60min lockout |
| position_golden_cross | ✅ Hard blocked | ✅ Corr. guard (88 req) | ✅ Streak pause | 30-day exit | 30-60min lockout |

All strategies are also subject to: capacity mode, chop lock, stale blacklist, spread check, and daily halt.

---

## 13. Equity-Based Performance Reporting

### The Problem with Unrealized P&L

The old reports used `unrealized_pl` from open positions as the "Net P&L" figure. This number is **misleading** because:
- It resets to ~0 every time you close a position
- It cannot show cumulative losses from closed trades
- Closing a sell for a loss and immediately buying a new position makes it look like P&L improved

### New Approach: Equity History

Every 5 minutes, the bot snapshots `total_equity = cash + market_value_of_all_positions` into the `equity_history` SQLite table.

Reports now compute P&L as `equity_now - equity_at_period_start`:

```
Hourly report:  equity_now - equity_60min_ago
Daily report:   equity_now - equity_at_midnight
Weekly report:  equity_now - equity_7days_ago
```

### Report Structure

Every report now includes an **Account Equity block** at the top:

```
--- ACCOUNT EQUITY (Primary Performance Metric) ---
  Current Equity:     $66,890.22
  Change (24h):       -$3,041.10 (-4.35%)
  Risk Flag: 🚨 CRITICAL — equity drawdown -4.35% in 24h

  [Note: Equity delta = real money won/lost. Unrealized P&L below is a snapshot only.]

Open Positions (2) — Unrealized P&L (snapshot):
  • BTC/USD: 0.012 @ $87,400 | MV: $1,048.80 | uPnL: +$4.21 (+0.41%)
```

The Gemini system prompts are also updated to grade performance based on equity delta, not unrealized P&L. A bot that made 20 trades and ended with -$3k equity gets a poor grade, regardless of what open unrealized positions say.

---

## 14. Trade Frequency Controls (4 Layers)

Added to protect against over-trading, which was causing equity losses despite many "positive" individual signal scores.

### Layer 1 — Equity Drawdown Guard (Cycle-Level)

**Check:** `_check_equity_guard(cfg)` runs at the top of every `_cycle()`.

| 1h Equity Drop | Response |
|---|---|
| < 2% | No action |
| ≥ 2% and < 4% | Signal threshold + 12 points, notional × 0.5 |
| ≥ 4% | 30-minute trading soft-halt (buys only, exits still run) |

After the soft-halt expires, the bot resumes with normal parameters.

**Config params:** `equity_guard_soft_pct` (default: 2.0), `equity_guard_hard_pct` (default: 4.0)

### Layer 2 — Per-Symbol Trade Spacing

After any successful buy, `last_buy_ts:{symbol}` is saved to signal_state. The next buy on that same symbol is blocked until `trade_spacing_min` minutes have elapsed.

**Default:** 30 minutes between buys on the same symbol.  
**Config param:** `trade_spacing_min`

DCA does not bypass this limit.

### Layer 3 — Win-Rate Awareness

Before executing a buy, `_get_symbol_win_rate(symbol)` checks the last 10 closed trades on that symbol. If fewer than 40% were profitable:
- Signal score threshold is raised by **+8 points** for that symbol this cycle
- Applied on top of any equity guard bonus from Layer 1

**Example:** Config floor is 68. Equity guard adds +12 (soft guard). Win rate is 30%. Final floor = 68 + 12 + 8 = **88** — very high conviction required.

### Layer 4 — Global Hourly Trade Cap

A rolling timestamp deque tracks every executed trade across all symbols. If `max_trades_per_hour` trades have already fired in the past 60 minutes, all new buys are skipped for the current cycle.

**Default:** 5 trades per 60-minute window.  
**Config param:** `max_trades_per_hour`

Timestamps older than 60 minutes are pruned automatically.

### 🌟 Golden Trade Bypass

If the DRL/Oracle fusion produces a signal score of **85 or higher**, this indicates an exceptionally rare, high-conviction opportunity (e.g. panic crash recovery or perfect breakout).

When `score ≥ 85.0`:
- **Layer 4 (Global Cap) is ignored:** The trade executes even if 5+ trades already fired today.
- **Layer 2 (Trade Spacing) is ignored:** The trade executes even if the symbol was bought <30min ago.

*Note: Layer 1 (Equity Guard) and Layer 3 (Win-Rate Penalty) still apply to ensure risk management.*

### Layer Summary

```
_cycle() start:
  [L1] _check_equity_guard()     → skip_buys = True OR (signal_bonus, notional_mult)
  [L4] _check_hourly_trade_cap() → skip_buys = True if ≥ 5 trades/hr

For each BUY signal:
  [is_golden_trade] = score >= 85
  [L1+L4] if _skip_buys AND NOT is_golden_trade: continue
  [L2] Check last_buy_ts:{symbol}: if < 30min ago AND NOT is_golden_trade: continue
  [L3] Check win_rate: if < 40%: raise score threshold +8
  [Execute buy]
  [L2] Store last_buy_ts:{symbol} = now
  [L4] _record_trade_timestamp() → append to rolling deque
```

### New Action Feed Events

| action_type | What it means |
|---|---|
| `risk_guard` | Equity guard triggered (soft or hard) |
| `trade_spacing` (toast) | Per-symbol 30-min cooldown active |
| `winrate_gate` (toast) | Win-rate below 40%, score raised |

---
