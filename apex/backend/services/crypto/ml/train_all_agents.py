import os
import glob
import time
import json
from services.crypto.ml.train_specialist import train_agent

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ai_training"))
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models"))

def train_all_agents():
    print("==================================================")
    print("🚀 APEX MASS TRAINING SUPERVISOR INITIALIZED")
    print("==================================================\n")
    
    # Find all engineered CSV files
    csv_files = glob.glob(os.path.join(DATA_DIR, "*_15Min_engineered.csv"))
    
    if not csv_files:
        print("❌ No engineered data found. Please run feature_engineering.py first.")
        return
        
    print(f"📡 Found {len(csv_files)} specialized datasets. Preparing to train army of AIs...\n")
    
    success_count = 0
    fail_count = 0
    
    for file_path in csv_files:
        ticker = os.path.basename(file_path).split('_15Min')[0]
        model_path = os.path.join(MODEL_DIR, f"ppo_{ticker}_specialist.zip")
        
        print("\n" + "="*50)
        print(f"🎯 DEPLOYING TRAINING RUN FOR: {ticker}")
        print("="*50)
        
        try:
            # Continuously Fine-Tune the existing agent (or build from scratch if first run)
            resume_learning = os.path.exists(model_path)
            if resume_learning:
                print(f"✅ Found existing brain. Executing Trace-Force Fine-Tuning Sequence...")
            
            # Run the PPO Neuromodulation Loop
            # Dropped to 100k timesteps for rapid UI feedback instead of 5M baseline
            train_agent(ticker, timesteps=100_000, resume=resume_learning)
            success_count += 1

        except Exception as e:
            print(f"\n❌ FATAL: Failed to train {ticker}: {e}")
            fail_count += 1
            
    print("\n==================================================")
    print(f"🎉 MASS TRAINING COMPLETE!")
    print(f"✅ Generated {success_count} Specialist Brains.")
    if fail_count > 0:
        print(f"❌ Failed to build {fail_count} brains.")
    print("==================================================")
    print("Your live bot 'run.sh' will now automatically load ALL of these Neural Networks!")

    # 4. Save the timestamp for the frontend UI Warner
    state_file = os.path.abspath(os.path.join(DATA_DIR, "..", "runtime_state.json"))
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except Exception:
        state = {}
        
    state["last_ai_training_ms"] = int(time.time() * 1000)
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=4)
        print("🕒 Last AI Training timestamp successfully recorded to runtime state.")
    except Exception as e:
        print(f"⚠️ Failed to record training timestamp: {e}")

if __name__ == "__main__":
    train_all_agents()
