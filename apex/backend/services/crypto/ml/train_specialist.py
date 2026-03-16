import os
import sys
import time
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.callbacks import EvalCallback, StopTrainingOnNoModelImprovement
from stable_baselines3.common.vec_env import SubprocVecEnv

from services.crypto.ml.crypto_trading_env import CryptoTradingEnv
from services.crypto.ml.replay_buffer import ReplayBuffer

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from services.crypto import store

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ai_training"))
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models"))
os.makedirs(MODEL_DIR, exist_ok=True)

def train_agent(ticker: str = "BTC_USD", timesteps: int = 500_000, resume: bool = False):
    print(f"🦾 Initializing Local Specialist DRL Training for {ticker}...")
    
    csv_path = os.path.join(DATA_DIR, f"{ticker}_15Min_engineered.csv")
    if not os.path.exists(csv_path):
        print(f"❌ Cannot find engineered data at {csv_path}. Please run `python3 feature_engineering.py` first.")
        return
        
    print(f"📊 Loading 15-Minute Candlestick memory from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    if len(df) < 500:
        print("❌ Data file is too small. Need at least 500 bars to train effectively.")
        return

    # Phase 7: Online RL - Load Live Experiences
    experiences = ReplayBuffer.load_experiences(ticker)
    if experiences:
        print(f"🧠 Online RL: Loaded {len(experiences)} live trading experiences from Replay Buffer.")
        
        valid_traces = []
        for exp in experiences:
            pnl_pct = exp.get("pnl_pct", 0.0)
            timestamp = exp.get("feature_state", {}).get("timestamp")
            gemini_macro = exp.get("feature_state", {}).get("gemini_macro", 1.0)
            if not timestamp:
                continue
                
            # Find the row matching the exact minute the ML model made its decision
            matches = df[df["timestamp"] == timestamp]
            if not matches.empty:
                idx = matches.index[0]
                
                # Siphon the exact context window (40 bars lead-up + 20 bars resolution)
                start_idx = max(0, idx - 40)
                end_idx = min(len(df), idx + 20)
                trace_window = df.iloc[start_idx:end_idx].copy()
                
                # INJECT LIVE GEMINI ORACLE SCORE INTO THE TRAINING TRACE
                trace_window['gemini_macro'] = gemini_macro
                
                # Trace Amplification: Mathematically force the PPO agent to rehearse huge mistakes/wins
                weight = 1
                if abs(pnl_pct) > 2.0: weight = 3
                if abs(pnl_pct) > 5.0: weight = 5
                
                for _ in range(weight):
                    valid_traces.append(trace_window)
                    
        if valid_traces:
            print(f"🔥 Trace Forcing: Merging {len(valid_traces)} heavily-weighted replay windows into training simulations...")
            df = pd.concat([df] + valid_traces, ignore_index=True)
            
    else:
        print("🧠 Online RL: No live trading experiences found in Replay Buffer yet.")

    # Split into Train and Eval (80/20)
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    eval_df = df.iloc[split_idx:].reset_index(drop=True)
    
    print(f"🏋️‍♂️ Training Frames: {len(train_df)} | 🎯 Eval Frames: {len(eval_df)}")

    print("🎓 Fetching Historical LLM Judge Grades to wire into Reward Function...")
    historical_grades = store.get_all_historical_grades_sync()
    if historical_grades:
        print(f"   Found {len(historical_grades)} LLM Judge Grades! AI will actively optimize for these.")

    # Drop non-numeric columns that would crash np.float32 conversion.
    # IMPORTANT: keep 'timestamp' and 'symbol' — CryptoTradingEnv needs 'timestamp'
    # for LLM grade lookup and already excludes both from the obs vector via drop_cols.
    _ENV_PASSTHROUGH = {'timestamp', 'symbol'}
    numeric_cols = [
        c for c in train_df.columns
        if c in _ENV_PASSTHROUGH or train_df[c].dtype.kind in ('i', 'u', 'f', 'b')
    ]
    if len(numeric_cols) < len(train_df.columns):
        dropped = set(train_df.columns) - set(numeric_cols)
        print(f"⚠️  Dropping non-numeric columns before env creation: {dropped}")
        train_df = train_df[numeric_cols]
        eval_df  = eval_df[numeric_cols]

    # Use SubprocVecEnv when running as top-level __main__, DummyVecEnv otherwise.
    import __main__ as _main
    _is_main = getattr(_main, '__file__', None) is not None and _main.__file__ == __file__
    if _is_main:
        from stable_baselines3.common.vec_env import SubprocVecEnv as _VecCls
        _n_envs = 8
        print("🔀 Using SubprocVecEnv (8 parallel workers)")
    else:
        from stable_baselines3.common.vec_env import DummyVecEnv as _VecCls
        _n_envs = 1
        print("🔁 Using DummyVecEnv (programmatic call — single worker)")

    train_env = make_vec_env(lambda: CryptoTradingEnv(train_df, historical_grades=historical_grades), n_envs=_n_envs, vec_env_cls=_VecCls)
    eval_env = make_vec_env(lambda: CryptoTradingEnv(eval_df, historical_grades=historical_grades), n_envs=1)
    
    # Early stopping if the AI stops getting smarter
    stop_train_callback = StopTrainingOnNoModelImprovement(max_no_improvement_evals=5, min_evals=10, verbose=1)
    eval_callback = EvalCallback(
        eval_env, 
        eval_freq=10_000, 
        callback_after_eval=stop_train_callback,
        best_model_save_path=MODEL_DIR,
        verbose=1
    )

    # Initialize the Proximal Policy Optimization (PPO) Neural Network
    model_base_path = os.path.join(MODEL_DIR, f"ppo_{ticker}_specialist")
    zip_path = f"{model_base_path}.zip"
    
    if resume and os.path.exists(zip_path):
        print(f"\n🧠 Loading existing Neural Network for {ticker} (Continuous Learning)...")
        try:
            model = PPO.load(zip_path, env=train_env, device="auto", custom_objects={'learning_rate': 0.0001}) 
            reset_num_timesteps = False
        except ValueError as e:
            print(f"⚠️ Incompatible neural weights detected ({e}). Wiping old brain and starting fresh...")
            os.remove(zip_path)
            print("\n🧠 Booting FRESH Neural Network Architecture (PPO)...")
            model = PPO(
                "MlpPolicy", 
                train_env, 
                verbose=1, 
                learning_rate=0.0003,
                n_steps=2048,
                batch_size=64,
                ent_coef=0.01, # Encourage exploration
                device="auto"  # Automatically uses 'mps' on Apple Silicon if available
            )
            reset_num_timesteps = True
    else:
        print("\n🧠 Booting FRESH Neural Network Architecture (PPO)...")
        model = PPO(
            "MlpPolicy", 
            train_env, 
            verbose=1, 
            learning_rate=0.0003,
            n_steps=2048,
            batch_size=64,
            ent_coef=0.01, # Encourage exploration
            device="auto"  # Automatically uses 'mps' on Apple Silicon if available
        )
        reset_num_timesteps = True
    
    print(f"🚀 Training Commencing. Target Timesteps: {timesteps}...")
    start_time = time.time()
    
    try:
        model.learn(total_timesteps=timesteps, callback=eval_callback, reset_num_timesteps=reset_num_timesteps)
    except KeyboardInterrupt:
        print("\n⚠️ Training interrupted by user.")
        
    elapsed = time.time() - start_time
    print(f"\n✅ Training Sequence Finished in {elapsed/60:.2f} minutes.")
    
    # Save the final agent
    model.save(model_base_path)
    print(f"💾 Agent Brain Saved: {model_base_path}.zip")

if __name__ == "__main__":
    train_agent("BTC_USD", timesteps=5_000_000) # Full multi-hour training run
