import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd
import math

class CryptoTradingEnv(gym.Env):
    """
    A custom Gymnasium environment for training a Reinforcement Learning Agent
    to trade crypto based on historical OHLCV data.
    """
    metadata = {'render_modes': ['human']}

    def __init__(self, df: pd.DataFrame, initial_balance: float = 10000.0, lookback_window_size: int = 40, historical_grades: dict = None):
        super(CryptoTradingEnv, self).__init__()
        
        self.df = df.reset_index(drop=True)

        # ── Defensive: ensure 'timestamp' column exists ───────────────────────
        # Stale CSVs from old runs may be missing it. Synthesize a sequence so
        # the LLM grade lookup doesn't crash (grades just won't match, neutral).
        if 'timestamp' not in self.df.columns:
            import datetime as _dt
            _base = _dt.datetime(2024, 1, 1)
            self.df['timestamp'] = [
                (_base + _dt.timedelta(minutes=15 * i)).isoformat()
                for i in range(len(self.df))
            ]

        self.initial_balance = initial_balance
        self.lookback_window_size = lookback_window_size
        self.historical_grades = historical_grades or {}
        self.timestamps_ms = pd.to_datetime(self.df['timestamp']).values.astype('datetime64[ms]').astype('int64')
        
        # Action space: 0 = Hold, 1 = Buy (convert 100% cash to crypto), 2 = Sell (convert 100% crypto to cash)
        self.action_space = spaces.Discrete(3)
        
        # Observation space: Dynamically loaded from DataFrame (excluding time/symbol cols)
        # We also append current balance, crypto held, and current net worth
        drop_cols = ['timestamp', 'symbol']
        self.features = [col for col in self.df.columns if col not in drop_cols]
        self.num_features = len(self.features)
        
        # 🚀 OPTIMIZATION: Convert Pandas to Numpy for 100x faster slicing in step()
        self.df_values = self.df[self.features].values.astype(np.float32)
        self.close_prices = self.df['close'].values.astype(np.float32)
        self.total_steps = len(df)
        
        self.close_idx = self.features.index('close')
        
        price_cols = ['open', 'high', 'low', 'close', 'vwap', 'bb_upper', 'bb_lower', 'bb_mid']
        self.price_col_indices = [self.features.index(c) for c in price_cols if c in self.features]
        
        self.non_price_col_indices = []
        for i, col in enumerate(self.features):
            if col not in price_cols and not col.endswith('_sin') and not col.endswith('_cos') and not col.startswith('log_return'):
                self.non_price_col_indices.append(i)
        
        # Space shape: [lookback, features] + [account_info]
        # We flatten it to a 1D array for standard MLP policies
        # Observations: (lookback * num_features) + 3 (balance, crypto_held, net_worth)
        obs_dim = (self.lookback_window_size * self.num_features) + 3
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )
        
        self.current_step = 0
        self.balance = 0.0
        self.crypto_held = 0.0
        self.net_worth = 0.0
        self.max_net_worth = 0.0
        self.current_price = 0.0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = self.lookback_window_size
        self.balance = self.initial_balance
        self.crypto_held = 0.0
        self.net_worth = self.initial_balance
        self.max_net_worth = self.initial_balance
        self.current_price = self.close_prices[self.current_step]
        
        return self._next_observation(), {}

    def _next_observation(self):
        # Extract the window using lightning-fast Numpy slicing instead of pandas
        start = self.current_step - self.lookback_window_size
        end = self.current_step
        frame_data = self.df_values[start:end].copy()
        
        # Normalize the price-based data relative to the window's starting close price
        base_price = max(frame_data[0, self.close_idx], 1e-9)
        
        for idx in self.price_col_indices:
            frame_data[:, idx] = (frame_data[:, idx] / base_price) - 1.0
                
        # Normalize non-price indicators to z-score within window
        for idx in self.non_price_col_indices:
            col_data = frame_data[:, idx]
            c_mean = np.mean(col_data)
            c_std = np.std(col_data) + 1e-9
            frame_data[:, idx] = (col_data - c_mean) / c_std

        # Flatten features
        obs = frame_data.flatten()
        
        # Append account state (normalized to initial balance)
        account_info = np.array([
            self.balance / self.initial_balance,
            (self.crypto_held * self.current_price) / self.initial_balance,
            self.net_worth / self.initial_balance
        ], dtype=np.float32)
        
        obs = np.concatenate([obs, account_info])
        return obs.astype(np.float32)

    def step(self, action):
        out_of_bounds = self.current_step >= self.total_steps - 1
        if out_of_bounds:
            return self._next_observation(), 0.0, True, False, {}

        self.current_price = self.close_prices[self.current_step]
        
        prev_net_worth = self.net_worth
        
        # Execute Action
        # 0 = Hold: Do nothing
        delay_modifier = (self.current_step / self.total_steps)
        
        if action == 1: # Buy (All In)
            if self.balance > 0:
                # Deduct 0.1% maker/taker simulated fee
                fee = self.balance * 0.001
                self.crypto_held += (self.balance - fee) / self.current_price
                self.balance = 0.0
                
        elif action == 2: # Sell (All Out)
            if self.crypto_held > 0:
                fee = (self.crypto_held * self.current_price) * 0.001
                self.balance += (self.crypto_held * self.current_price) - fee
                self.crypto_held = 0.0

        # Update Net Worth
        self.net_worth = self.balance + (self.crypto_held * self.current_price)
        self.max_net_worth = max(self.max_net_worth, self.net_worth)

        # Calculate Reward
        # Reward is the change in net worth (did this action make us richer?)
        reward = self.net_worth - prev_net_worth
        
        # 🧠 LLM-as-Reward-Function: Wire LLM Judge grades into the bot's math.
        # If the bot trades during a window that received a high grade from the AI Judge,
        # multiply its financial reward so it learns to recognize and repeat "smart" setups.
        if self.historical_grades and (action == 1 or action == 2):
            current_ms = self.timestamps_ms[self.current_step]
            
            # Find the nearest grade within 24 hours (86,400,000 ms)
            nearest_grade = None
            min_diff = 86400000
            for ts_ms, grade_score in self.historical_grades.items():
                diff = abs(current_ms - ts_ms)
                if diff < min_diff:
                    min_diff = diff
                    nearest_grade = grade_score
            
            if nearest_grade is not None:
                # 50 = neutral (1x), 100 = brilliant (1.5x bonus), 0 = terrible (0.5x nerf)
                multiplier = 0.5 + (nearest_grade / 100.0) # Range: 0.5 to 1.5
                
                if reward > 0:
                    reward *= multiplier # High grade boosts profit reward
                elif reward < 0:
                    # High grade softens the blow of a loss (smart risk), low grade punishes a dumb loss harder
                    penalty_factor = 2.0 - multiplier # Range: 1.5 to 0.5 (reversed logic)
                    reward *= penalty_factor

        # We can also add a small penalty to holding to force the agent to find alpha rather than just surfing market beta
        if action == 0 and self.crypto_held > 0:
            reward -= (self.initial_balance * 0.00001) # Time decay penalty

        self.current_step += 1
        
        done = self.net_worth <= 0 or self.current_step >= self.total_steps - 1
        
        return self._next_observation(), reward, done, False, {}

    def render(self):
        print(f"Step: {self.current_step} | Net Worth: ${self.net_worth:.2f} | Crypto: {self.crypto_held:.4f} | Balance: ${self.balance:.2f} | Price: ${self.current_price:.2f}")

