#!/usr/bin/env python3
"""
stock_trading_env.py — Apex Stocks ML: OpenAI Gym Environment

A custom Gymnasium environment for training a PPO agent to trade stocks.
Adapted from CryptoTradingEnv but tuned for stock-specific dynamics:

  - Market Hours: Stocks only trade 9:30–16:00. Intraday positions should
    be flat or held intentionally through close, not left open by neglect.
  - ATR-aware stop logic: Backtesting proved 2.5x ATR trailing stop wins.
    We encode ATR state directly into the observation so the PPO agent
    naturally learns to exit when price falls below the stop level.
  - Golden Cross filter: The environment gives a small bonus reward when the
    agent is long AND golden_cross=1 (macro bull regime).
  - SPY Canary: spy_vwap_delta is already in every feature row — the agent
    will learn to treat macro deviation as a risk signal automatically.
  - Commission: Alpaca stocks = free (0% fee), so we reward every $0.01 gain
    without dilution. This encourages the agent to trade more precisely.

Actions:
    0 = Hold / Stay Flat
    1 = BUY (enter full position — sized later by risk engine at runtime)
    2 = SELL / EXIT (take profits or cut losses)
"""

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd


class StockTradingEnv(gym.Env):
    """Custom Gymnasium environment for stock swing/position trading."""

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10_000.0,
        lookback_window_size: int = 40,
        # Backtester-proven parameters (from VectorBT Phase 3 grid search)
        atr_stop_multiplier: float = 2.5,
    ):
        super().__init__()

        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.lookback_window_size = lookback_window_size
        self.atr_stop_multiplier = atr_stop_multiplier

        # Action space: 0=Hold, 1=Buy, 2=Sell
        self.action_space = spaces.Discrete(3)

        # Build feature list (exclude timestamp/symbol columns)
        drop_cols = {"timestamp", "symbol"}
        self.features = [c for c in df.columns if c not in drop_cols]
        self.num_features = len(self.features)

        # Pre-convert to numpy for lightning-fast step() slicing
        self.df_values = df[self.features].values.astype(np.float32)
        self.close_prices = df["close"].values.astype(np.float32)
        self.atr_pct_values = (
            df["atr_pct"].values.astype(np.float32)
            if "atr_pct" in df.columns
            else np.full(len(df), 0.02, dtype=np.float32)  # 2% default ATR
        )
        self.golden_cross_values = (
            df["golden_cross"].values.astype(np.float32)
            if "golden_cross" in df.columns
            else np.ones(len(df), dtype=np.float32)
        )
        self.total_steps = len(df)

        # Column index for close (used for price normalization)
        self.close_idx = self.features.index("close")

        # Identify price-based columns for relative normalization
        price_cols = {"open", "high", "low", "close", "vwap",
                      "bb_upper", "bb_lower", "bb_mid",
                      "ema_20", "ema_50", "ema_200", "sma_50", "sma_200"}
        self.price_col_indices = [
            i for i, c in enumerate(self.features) if c in price_cols
        ]
        self.non_price_col_indices = [
            i for i, c in enumerate(self.features)
            if c not in price_cols and not c.endswith("_sin") and not c.endswith("_cos")
            and not c.startswith("log_return")
        ]

        # Observation: (window * features) + [balance, shares_held, net_worth, atr_stop_pct]
        obs_dim = (self.lookback_window_size * self.num_features) + 4
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )

        # Runtime state
        self.current_step = 0
        self.balance = 0.0
        self.shares_held = 0.0
        self.net_worth = 0.0
        self.max_net_worth = 0.0
        self.current_price = 0.0
        self.entry_price = 0.0        # Price of last buy (for stop loss calculation)
        self.trailing_high = 0.0      # Highest price since entry (for trailing stop)

    # ──────────────────────────────────────────────────────────────────────────
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = self.lookback_window_size
        self.balance = self.initial_balance
        self.shares_held = 0.0
        self.net_worth = self.initial_balance
        self.max_net_worth = self.initial_balance
        self.current_price = self.close_prices[self.current_step]
        self.entry_price = 0.0
        self.trailing_high = 0.0
        return self._next_observation(), {}

    def _next_observation(self) -> np.ndarray:
        start = self.current_step - self.lookback_window_size
        end = self.current_step
        frame = self.df_values[start:end].copy()

        # Normalize price columns relative to window's first close
        base_price = max(frame[0, self.close_idx], 1e-9)
        for idx in self.price_col_indices:
            frame[:, idx] = (frame[:, idx] / base_price) - 1.0

        # Z-score normalize non-price indicators within the window
        for idx in self.non_price_col_indices:
            col = frame[:, idx]
            mu, sigma = np.mean(col), np.std(col) + 1e-9
            frame[:, idx] = (col - mu) / sigma

        obs = frame.flatten()

        # ATR trailing stop distance as % of current price (dynamic per bar)
        atr_stop_pct = self.atr_pct_values[self.current_step] * self.atr_stop_multiplier

        account_info = np.array([
            self.balance / self.initial_balance,
            (self.shares_held * self.current_price) / self.initial_balance,
            self.net_worth / self.initial_balance,
            atr_stop_pct,              # Agent sees how tight the current stop is
        ], dtype=np.float32)

        return np.concatenate([obs, account_info]).astype(np.float32)

    # ──────────────────────────────────────────────────────────────────────────
    def step(self, action: int):
        if self.current_step >= self.total_steps - 1:
            return self._next_observation(), 0.0, True, False, {}

        self.current_price = self.close_prices[self.current_step]
        prev_net_worth = self.net_worth

        # ── Execute Action ────────────────────────────────────────────────────
        if action == 1:  # BUY (enter full position)
            if self.balance > 0 and self.shares_held == 0:
                # Alpaca stocks are commission-free — no fee deduction needed
                self.shares_held = self.balance / self.current_price
                self.balance = 0.0
                self.entry_price = self.current_price
                self.trailing_high = self.current_price

        elif action == 2:  # SELL / EXIT
            if self.shares_held > 0:
                self.balance += self.shares_held * self.current_price
                self.shares_held = 0.0
                self.entry_price = 0.0
                self.trailing_high = 0.0

        # ── Update trailing high (for stop loss logic) ────────────────────────
        if self.shares_held > 0:
            if self.current_price > self.trailing_high:
                self.trailing_high = self.current_price

            # Auto-trigger stop loss if price drops X ATR below trailing high
            atr_stop_pct = self.atr_pct_values[self.current_step] * self.atr_stop_multiplier
            stop_price = self.trailing_high * (1 - atr_stop_pct)
            if self.current_price <= stop_price:
                # Stop triggered — simulate an exit
                self.balance += self.shares_held * self.current_price
                self.shares_held = 0.0
                self.entry_price = 0.0
                self.trailing_high = 0.0

        # ── Update Net Worth ──────────────────────────────────────────────────
        self.net_worth = self.balance + (self.shares_held * self.current_price)
        self.max_net_worth = max(self.max_net_worth, self.net_worth)

        # ── Reward Shaping ────────────────────────────────────────────────────
        reward = self.net_worth - prev_net_worth

        # Golden Cross bonus: being long in a macro uptrend is a good habit
        if action == 1 and self.golden_cross_values[self.current_step] == 1:
            reward += abs(reward) * 0.1  # 10% bonus for buying in bull regime

        # Drawdown penalty: large drawdowns from peak are penalized
        if self.max_net_worth > 0:
            drawdown_pct = (self.max_net_worth - self.net_worth) / self.max_net_worth
            if drawdown_pct > 0.15:  # >15% drawdown from peak
                reward -= self.initial_balance * 0.0001 * drawdown_pct

        # Time decay: penalize holding through adverse drift
        if action == 0 and self.shares_held > 0:
            # Scaled by how long we've been holding relative to session
            reward -= self.initial_balance * 0.000005

        self.current_step += 1
        done = self.net_worth <= 0 or self.current_step >= self.total_steps - 1

        return self._next_observation(), reward, done, False, {}

    # ──────────────────────────────────────────────────────────────────────────
    def render(self):
        print(
            f"Step: {self.current_step:5d} | "
            f"Net Worth: ${self.net_worth:10.2f} | "
            f"Shares: {self.shares_held:6.2f} | "
            f"Balance: ${self.balance:10.2f} | "
            f"Price: ${self.current_price:8.2f}"
        )
