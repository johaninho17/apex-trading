import os
import json
import asyncio
import numpy as np
import pandas as pd
import logging

from core.ollama import set_ollama_env

try:
    from stable_baselines3 import PPO
    HAS_SB3 = True
except ImportError:
    HAS_SB3 = False
    
from .feature_engineering import engineer_features

logger = logging.getLogger(__name__)

# Cache models in memory, but track their file modification times to allow live hot-swapping 
_MODEL_CACHE = {} # Format: {ticker: {"model": PPO, "mtime": float}}

def get_ppo_model(ticker: str):
    if not HAS_SB3:
        return None
        
    safe_ticker = ticker.replace("/", "_")
    base_coin = safe_ticker.split("_")[0]
    
    # Check for direct match first, then fallback to Binance quote currencies
    possible_names = [
        f"ppo_{safe_ticker}_specialist.zip",
        f"ppo_{base_coin}_USDT_specialist.zip",
        f"ppo_{base_coin}_USDC_specialist.zip",
        f"ppo_{base_coin}_BTC_specialist.zip"
    ]
    
    model_path = None
    for name in possible_names:
        test_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models", name))
        if os.path.exists(test_path):
            model_path = test_path
            break
            
    # Check if a model was found
    if not model_path:
        return None
        
    current_mtime = os.path.getmtime(model_path)
    
    # Check cache to see if we already have this exact version loaded
    if ticker in _MODEL_CACHE:
        cached_entry = _MODEL_CACHE[ticker]
        if cached_entry.get("mtime") == current_mtime:
            return cached_entry.get("model")
        else:
            logger.info(f"🔄 Hot-swapping upgraded Deep Learning brain for {ticker}. Old brain purged.")
            
    # Load or Reload the Brain
    try:
        model = PPO.load(model_path, device="cpu")
        _MODEL_CACHE[ticker] = {"model": model, "mtime": current_mtime}
        return model
    except Exception as e:
        logger.error(f"Failed to load ML model {model_path}: {e}")
            
    return None

def build_live_observation(df: pd.DataFrame, balance: float, crypto_held: float, current_price: float, lookback: int = 40, initial_balance: float = 10000.0, gemini_macro: float = 1.0) -> np.ndarray:
    """
    Reconstructs the EXACT mathematical observation state used in `crypto_trading_env.py`
    so the live bot speaks the same language as the training simulator.
    """
    # 1. We need at least 'lookback' engineered rows.
    # Note: feature_engineering drops initial NaNs, so we need extra raw rows before engineering.
    if len(df) < lookback + 30:
        return None
        
    # Apply identical feature engineering
    try:
        eng_df = engineer_features(df.copy())
        eng_df['gemini_macro'] = gemini_macro # 🧠 Inject Live Oracle Score
    except Exception as e:
        logger.error(f"Inference engineering failed: {e}")
        return None
        
    if len(eng_df) < lookback:
        return None
        
    # 2. Extract the final window frame
    frame = eng_df.iloc[-lookback:].copy()
    
    drop_cols = ['timestamp', 'symbol']
    features = [col for col in frame.columns if col not in drop_cols]
    
    # 3. Apply identical relative normalization
    base_price = max(frame['close'].iloc[0], 1e-9)
    price_cols = ['open', 'high', 'low', 'close', 'vwap', 'bb_upper', 'bb_lower', 'bb_mid']
    
    for col in price_cols:
        if col in frame.columns:
            frame[col] = (frame[col] / base_price) - 1.0
            
    for col in features:
        if col not in price_cols and not str(col).endswith('_sin') and not str(col).endswith('_cos') and not str(col).startswith('log_return'):
            c_mean = frame[col].mean()
            c_std = frame[col].std() + 1e-9
            frame[col] = (frame[col] - c_mean) / c_std
            
    # 4. Flatten
    obs = frame[features].values.flatten()
    
    # 5. Append Account Context
    net_worth = balance + (crypto_held * current_price)
    account_info = np.array([
        balance / initial_balance,
        (crypto_held * current_price) / initial_balance,
        net_worth / initial_balance
    ], dtype=np.float32)
    
    obs = np.concatenate([obs, account_info])
    return obs.astype(np.float32)

def predict_action(symbol: str, bars_df: pd.DataFrame, current_position_qty: float = 0.0, available_cash: float = 10000.0, gemini_macro: float = 1.0) -> int:
    """
    Returns: 0 (Hold), 1 (Buy), 2 (Sell), -1 (no model / not enough data)
    """
    model = get_ppo_model(symbol)
    if not model:
        return -1  # Model not trained yet
        
    if bars_df.empty:
        return -1
        
    current_price = bars_df['close'].iloc[-1]
    
    obs = build_live_observation(bars_df, balance=available_cash, crypto_held=current_position_qty, current_price=current_price, gemini_macro=gemini_macro)
    if obs is None:
        return -1

    try:
        action, _states = model.predict(obs, deterministic=True)
        return int(action)
    except ValueError as e:
        # Observation shape doesn't match model's expected input — this happens when
        # feature engineering changes after a model was trained. The model is stale.
        # Log clearly and return -1 (hold) so the bot continues running safely.
        if "observation shape" in str(e).lower() or "unexpected" in str(e).lower():
            logger.warning(
                f"[ML] Model for {symbol} has stale observation shape — "
                f"feature vector is {obs.shape[0]} dims but model expects different. "
                f"Skipping ML signal. Retrain the model to fix this."
            )
        else:
            logger.error(f"[ML] predict_action failed for {symbol}: {e}")
        return -1
    except Exception as e:
        logger.error(f"[ML] Unexpected error in predict_action for {symbol}: {e}")
        return -1

async def predict_action_ollama(
    symbol: str,
    s_15m: dict,
    s_1m: dict,
    macro_score: float,
    model_name: str = "qwen3:8b",
    position_context: dict = None,
    session_memory: dict = None,
    narrative_context: dict = None,
    think: bool = False,
) -> dict:
    """
    Constructs a plain-english math prompt from the technical indicators and pings the local Ollama SLM.
    Returns a dict with {"action": "BUY"|"SELL"|"HOLD", "confidence": 0-100, "reason": "..."}

    position_context: optional dict with keys:
      - pnl_pct: float (e.g. 3.5 means +3.5% unrealized)
      - market_value: float (USD value of current holding)
      - total_exposure: float (total USD across all open positions)
      - exposure_pct: float (% of total equity deployed)
    session_memory: optional dict — rolling win stats for this coin in recent hours
      - trades_count, win_rate_pct, total_pnl_pct, last_exit_min_ago, last_exit_reason
    narrative_context: optional dict from Gemini narrative scan
      - label: str ("positive", "extreme_positive", "negative", "extreme_negative")
      - reason: str (one-sentence description)
    """
    import asyncio
    try:
        set_ollama_env()
        import ollama
    except ImportError:
        logger.error("[ML] Ollama python package is missing. Run `pip install ollama`.")
        return {"action": "HOLD", "confidence": 0, "reason": "Ollama SDK not installed."}

    # Extract critical math
    close = s_1m.get("close", 0)
    rsi_15m = s_15m.get("rsi14", 50.0)
    rsi_1m = s_1m.get("rsi14", 50.0)
    bb_lower = s_15m.get("bb_lower", 0.0)
    bb_upper = s_15m.get("bb_upper", 0.0)
    vwap_1m = s_1m.get("vwap", 0.0)

    has_position = position_context and float(position_context.get("market_value", 0.0)) > 0
    pnl_pct = float(position_context.get("pnl_pct", 0.0)) if has_position else 0.0

    # Math Gatekeeper: Skip if market is flat AND we have no open position to worry about.
    if not has_position and 43 <= rsi_15m <= 57 and bb_lower < close < bb_upper:
        return {
            "action": "HOLD",
            "confidence": 0,
            "reason": f"Math Gatekeeper: {symbol} is chopping sideways (RSI {rsi_15m:.1f}). Saved CPU by skipping SLM."
        }

    # Math Gatekeeper: The Chop Lock for Existing Positions
    # Prevents AI from constant 2-minute whipsawing on breakeven positions.
    if has_position:
        is_pnl_flat = -0.8 < pnl_pct < 0.8  # Tightened from ±1.5% — releases chop lock sooner
        is_rsi_neutral = 35 <= rsi_15m <= 65
        if is_pnl_flat and is_rsi_neutral:
            return {
                "action": "HOLD",
                "confidence": 0,
                "reason": f"Math Gatekeeper: {symbol} Chop Lock active. PnL is flat ({pnl_pct:+.2f}%) and RSI is neutral ({rsi_15m:.1f}). Forced HOLD."
            }

    # --- Helper: Contextual RSI label so SLM doesn't have to do raw math ---
    def _rsi_label(rsi: float) -> str:
        if rsi <= 20: return f"{rsi:.1f} (EXTREME OVERSOLD — reversal very likely)"
        if rsi <= 30: return f"{rsi:.1f} (SEVERELY OVERSOLD — high reversal probability)"
        if rsi <= 40: return f"{rsi:.1f} (OVERSOLD — consider buying dip)"
        if rsi <= 55: return f"{rsi:.1f} (NEUTRAL / SLIGHT WEAKNESS)"
        if rsi <= 65: return f"{rsi:.1f} (NEUTRAL / SLIGHT STRENGTH)"
        if rsi <= 75: return f"{rsi:.1f} (OVERBOUGHT — momentum fading, consider reducing)"
        if rsi <= 85: return f"{rsi:.1f} (SEVERELY OVERBOUGHT — reversal likely)"
        return f"{rsi:.1f} (EXTREME OVERBOUGHT — imminent reversal, lean SELL)"

    # --- Macro score as a readable sentence ---
    if macro_score >= 1.5:
        macro_desc = f"{macro_score:.2f}/1.8 → EXTREME GREED / BULL RUN. Let winners run. Be aggressive on buys."
    elif macro_score >= 1.2:
        macro_desc = f"{macro_score:.2f}/1.8 → BULLISH. Favor BUY signals. Hold positions longer."
    elif macro_score >= 0.9:
        macro_desc = f"{macro_score:.2f}/1.8 → NEUTRAL. No strong directional bias."
    elif macro_score >= 0.6:
        macro_desc = f"{macro_score:.2f}/1.8 → FEARFUL / BEARISH. Be cautious on buys. Protect capital."
    else:
        macro_desc = f"{macro_score:.2f}/1.8 → EXTREME FEAR / CRASH MODE. Avoid buying. Prioritize exits."

    # --- Position context block ---
    position_block = ""
    if has_position:
        pnl = float(position_context.get("pnl_pct", 0.0))
        mv = float(position_context.get("market_value", 0.0))
        exp_pct = float(position_context.get("exposure_pct", 0.0))
        pnl_label = "IN PROFIT" if pnl >= 0 else "IN LOSS"
        position_block = f"""
Current Position in {symbol}:
  Status: OPEN — {pnl_label}
  Unrealized PnL: {pnl:+.2f}%
  Market Value: ${mv:,.2f}
  Portfolio Exposure: {exp_pct:.1f}% of equity deployed across all positions
"""
    else:
        position_block = f"\nCurrent Position: NONE — No existing holding in {symbol}.\n"

    # --- Session Memory block ---
    session_block = ""
    if session_memory and session_memory.get("trades_count", 0) > 0:
        tc = session_memory.get("trades_count", 0)
        wr = session_memory.get("win_rate_pct", 0.0)
        pnl_s = session_memory.get("total_pnl_pct", 0.0)
        min_ago = session_memory.get("last_exit_min_ago", None)
        last_reason = session_memory.get("last_exit_reason", "")
        perf_label = "POSITIVE" if pnl_s >= 0 else "NEGATIVE"
        session_block = f"""
[Session Memory — Last 6 Hours on {symbol}]
  Trades Taken   : {tc}
  Win Rate       : {wr:.0f}%
  Realized PnL   : {pnl_s:+.2f}% ({perf_label})"""
        if min_ago is not None:
            session_block += f"\n  Last Exit      : {int(min_ago)} min ago — {last_reason[:80]}"
        if tc >= 3 and wr < 35:
            session_block += f"\n  ⚠️  WARNING: Win rate is very low ({wr:.0f}%). Market may be choppy — be HIGHLY selective or HOLD."
        session_block += "\n"

    # --- Narrative context block ---
    narrative_block = ""
    if narrative_context:
        label = str(narrative_context.get("label", "")).lower()
        reason = str(narrative_context.get("reason", ""))
        if label in ("extreme_positive", "positive"):
            intensity = "MAJOR CATALYST 🚀" if label == "extreme_positive" else "Positive catalyst"
            narrative_block = f"\n[Gemini News Context] {intensity}: {reason}\n"
        elif label in ("extreme_negative", "negative"):
            intensity = "MAJOR CRISIS ☠️" if label == "extreme_negative" else "Negative catalyst"
            narrative_block = f"\n[Gemini News Context] {intensity}: {reason}. Consider avoiding new buys.\n"

    prompt = f"""You are a quantitative crypto trading AI. Evaluate {symbol}.

Current Price: ${close:.4f}
15-Minute RSI: {_rsi_label(rsi_15m)}
1-Minute RSI: {_rsi_label(rsi_1m)}
15-Minute Bollinger Bands: Lower=${bb_lower:.4f}, Upper=${bb_upper:.4f}
1-Minute VWAP: ${vwap_1m:.4f}

Global Macro Sentiment: {macro_desc}
{position_block}{session_block}{narrative_block}
TRADING RULES:
- If Price is near/below Lower Band and RSI is oversold (context says OVERSOLD), strongly consider BUY.
- If Price is near/above Upper Band and RSI is overbought (context says OVERBOUGHT), strongly consider SELL.
- If Global Macro is FEARFUL or CRASH MODE, heavily penalize BUY setups. Prefer HOLD or SELL.
- If you have an open position IN PROFIT and RSI context says SEVERELY OVERBOUGHT or higher, lean SELL to protect gains.
- If you have an open position IN LOSS, consider whether DCA (buying more) or exiting makes more sense given macro.
- If Session Memory shows a low win rate (< 35%), require stronger RSI evidence before BUY. Prefer HOLD in chop.
- If Gemini News Context shows a MAJOR CATALYST, allow aggressive buys even in neutral RSI zones.
- If Gemini News Context shows a MAJOR CRISIS, veto BUY signals even if RSI is oversold.
- If no clear trend, output HOLD.

Respond ONLY with valid JSON. Do not include markdown blocks or conversational text.
Format: {{"action": "BUY" or "SELL" or "HOLD", "confidence": 0-100, "reason": "brief explanation"}}
"""

    try:
        response = await asyncio.wait_for(
            ollama.AsyncClient().generate(
                model=model_name,
                prompt=prompt,
                format="json",
                options={
                    # modelfile already bakes in temperature/top_k/num_ctx/repeat_penalty;
                    # we only override think at call-time to ensure it's honoured per config.
                    "think": think,   # False = disable Qwen3 chain-of-thought
                },
                keep_alive="-1"
            ),
            timeout=15.0
        )
        raw_text = response.get("response", "").strip()

        # Robustly extract JSON: find first { and last } to ignore
        # any leading/trailing conversational text or markdown fences.
        start = raw_text.find('{')
        end = raw_text.rfind('}')
        if start != -1 and end != -1 and end >= start:
            raw_text = raw_text[start:end + 1]

        try:
            decision = json.loads(raw_text)
        except Exception as e:
            logger.error(f"[ML] Ollama returned invalid JSON for {symbol}. Text: {raw_text[:100]}")
            return {"action": "HOLD", "confidence": 0, "reason": "SLM JSON Exception: Model hallucinated non-JSON output."}

        # Validate format
        act = str(decision.get("action", "HOLD")).upper()
        if act not in ["BUY", "SELL", "HOLD"]:
            act = "HOLD"
        conf = int(decision.get("confidence", 0))
        reason = str(decision.get("reason", "SLM execution successful."))

        return {"action": act, "confidence": conf, "reason": f"[Ollama_{model_name}] {reason}"}

    except asyncio.TimeoutError:
        logger.warning(f"[ML] Ollama SLM timed out after 15s for {symbol}.")
        return {"action": "HOLD", "confidence": 0, "reason": "SLM generation timeout (15s)."}
    except Exception as e:
        logger.error(f"[ML] Ollama generation failed for {symbol}: {e}")
        return {"action": "HOLD", "confidence": 0, "reason": f"SLM Exception: {str(e)}"}
