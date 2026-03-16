"""
self_tuner.py - Autonomous Hyperparameter Tuning
Runs daily to evaluate the bot's performance and apply safe JSON patches to a
small set of execution thresholds and exit settings.
"""

import json
import logging
import time
import traceback
from typing import Dict, Any, Optional

from . import report_generator
from . import bot
from . import store

logger = logging.getLogger(__name__)

_TUNER_SYSTEM = """You are APEX-Tuner, an autonomous quantitative trading architect.
Your job is to read the bot's Daily Performance Report, blocked-vs-executed outcome feedback,
and current configuration, then output a JSON patch with small, safe changes for tomorrow.

You may tune ONLY these 6 variables:
1. "take_profit_pct" (current values typically 1.0 - 5.0)
2. "stop_loss_pct" (current values typically 1.0 - 5.0)
3. "rsi_1m_sniper_dip" (current values typically 45.0 - 55.0)
4. "min_signal_score" (current values typically 62.0 - 78.0)
5. "rsi_oversold" (current values typically 28.0 - 38.0)
6. "breakout_volume_mult" (current values typically 1.5 - 2.3)

Rules:
- Make only incremental changes. Stay within roughly +/- 10% of the current value unless the current value is clearly out of band.
- If bad_block counts are high and score_too_low / strategy_score_gate dominates, consider slightly LOWERING min_signal_score or RAISING rsi_oversold.
- If good_block counts are high, keep or TIGHTEN entry thresholds slightly.
- If churn / fast exits are high, TIGHTEN entry thresholds and consider LOWERING take_profit_pct slightly only if winners are not reaching TP.
- If stops are getting hit too often, consider WIDENING stop_loss_pct slightly.
- Do not make both min_signal_score and breakout_volume_mult materially looser at the same time unless bad_block evidence is strong.
- Keep take_profit_pct generally >= stop_loss_pct when reasonable.
- Use the activity governor summary as context: if a mode is consistently hurting, bias toward returning thresholds closer to baseline rather than pushing further in the same direction.

RESPOND EXACTLY WITH A RAW JSON OBJECT (No markdown, no explanation).
Format:
{
    "take_profit_pct": 3.2,
    "stop_loss_pct": 2.1,
    "rsi_1m_sniper_dip": 49.5,
    "min_signal_score": 66.0,
    "rsi_oversold": 33.0,
    "breakout_volume_mult": 1.8,
    "reasoning": "A one sentence summary of why you made these tweaks based on today's report and decision feedback."
}
"""


def _summarize_activity_governor() -> Dict[str, Any]:
    history = store.get_activity_history_sync(limit=288)
    if not history:
        return {
            "samples": 0,
            "current_mode": "unknown",
            "current_pressure": 0.0,
            "current_effectiveness": 0.0,
            "current_confidence": 0.0,
            "mode_counts": {},
            "verdict_counts": {},
            "avg_pressure": 0.0,
            "avg_effectiveness": 0.0,
            "avg_confidence": 0.0,
            "recent_reason_codes": [],
        }

    pressure_values = [float(row.get("pressure_score", 0.0) or 0.0) for row in history]
    effectiveness_values = [float(row.get("effectiveness_score", 0.0) or 0.0) for row in history]
    confidence_values = [float(row.get("confidence_score", 0.0) or 0.0) for row in history]
    mode_counts: Dict[str, int] = {}
    verdict_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    for row in history:
        mode = str(row.get("mode", "normal") or "normal")
        verdict = str(row.get("verdict", "") or "")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        if verdict:
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        for reason in list(row.get("reason_codes") or []):
            key = str(reason or "")
            if key:
                reason_counts[key] = reason_counts.get(key, 0) + 1

    latest = history[-1]
    top_reasons = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    return {
        "samples": len(history),
        "current_mode": str(latest.get("mode", "normal") or "normal"),
        "current_pressure": float(latest.get("pressure_score", 0.0) or 0.0),
        "current_effectiveness": float(latest.get("effectiveness_score", 0.0) or 0.0),
        "current_confidence": float(latest.get("confidence_score", 0.0) or 0.0),
        "mode_counts": mode_counts,
        "verdict_counts": verdict_counts,
        "avg_pressure": round(sum(pressure_values) / len(pressure_values), 2),
        "avg_effectiveness": round(sum(effectiveness_values) / len(effectiveness_values), 2),
        "avg_confidence": round(sum(confidence_values) / len(confidence_values), 2),
        "recent_reason_codes": [reason for reason, _ in top_reasons],
    }


def _summarize_feedback() -> Dict[str, Any]:
    outcomes = store.list_decision_outcomes_sync(limit=500)
    traces = store.list_brain_decision_traces_sync(limit=500)
    actions = store._list_actions_sync(limit=500)

    label_counts: Dict[str, int] = {}
    block_reason_counts: Dict[str, int] = {}
    candidate_count = 0
    for row in outcomes:
        label = str(row.get("outcome_label", "") or "")
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1
        reason = str(row.get("block_reason", "") or "")
        if reason:
            block_reason_counts[reason] = block_reason_counts.get(reason, 0) + 1
    for row in traces:
        payload = dict(row.get("payload") or {})
        signal = dict(payload.get("signal") or {})
        meta = dict(signal.get("meta") or {})
        if bool(meta.get("candidate_only", False)):
            candidate_count += 1
            reason = str(row.get("block_reason", "") or "candidate_suppressed")
            block_reason_counts[reason] = block_reason_counts.get(reason, 0) + 1

    candidate_actions = sum(1 for row in actions if str(row.get("action_type", "") or "") == "candidate_detected")

    return {
        "decision_outcomes_count": len(outcomes),
        "label_counts": label_counts,
        "block_reason_counts": block_reason_counts,
        "candidate_trace_count": candidate_count,
        "candidate_action_count": candidate_actions,
    }


def generate_and_apply_tuning_patch() -> Optional[Dict[str, Any]]:
    """Fetches daily report, asks Gemini for a bounded tuning patch, and applies it."""
    logger.info("AI Self-Tuner waking up for daily evaluation...")
    try:
        reports = report_generator.get_all_latest_reports()
        daily = reports.get("daily")
        if not daily or not daily.get("content"):
            logger.warning("No daily report found. Skipping self-tuning.")
            return None

        report_text = daily["content"]
        grade = daily.get("grade", {})
        feedback_summary = _summarize_feedback()
        activity_summary = _summarize_activity_governor()
        component_calibration = store.get_latest_component_calibration_sync()
        runtime_state = store._get_runtime_state_sync()
        behavior_state = dict(runtime_state.get("behavior_state") or {})
        effective_risk_profile = dict(runtime_state.get("effective_risk_profile") or {})

        full_cfg = bot.get_config().get("stocks", {}).get("crypto", {})
        active_exchange = full_cfg.get("active_exchange", "alpaca")
        account_mode = full_cfg.get("account_mode", "paper")
        profile_key = f"{active_exchange}-{account_mode}"
        current_bot_cfg = full_cfg.get(profile_key, bot.DEFAULT_CRYPTO_CONFIG)

        synthetic_exits = dict(current_bot_cfg.get("synthetic_exits") or {})
        short_term = dict(current_bot_cfg.get("short_term") or {})

        take_profit = float(synthetic_exits.get("take_profit_pct", 3.0))
        stop_loss = float(synthetic_exits.get("stop_loss_pct", 2.0))
        rsi_dip = float(short_term.get("rsi_1m_sniper_dip", 48.0))
        min_signal_score = float(current_bot_cfg.get("min_signal_score", 68.0))
        rsi_oversold = float(short_term.get("rsi_oversold", 32.0))
        breakout_volume_mult = float(short_term.get("breakout_volume_mult", 1.9))

        prompt = f"""
## CURRENT CONFIGURATION:
- take_profit_pct: {take_profit}
- stop_loss_pct: {stop_loss}
- rsi_1m_sniper_dip: {rsi_dip}
- min_signal_score: {min_signal_score}
- rsi_oversold: {rsi_oversold}
- breakout_volume_mult: {breakout_volume_mult}

## DAILY BOT REPORT:
{report_text}

## JUDGE'S GRADE & FEEDBACK:
Grade: {grade.get('grade', 'N/A')} ({grade.get('score', 0)}/100)
Strengths: {grade.get('strengths', 'None noted')}
Weaknesses: {grade.get('weaknesses', 'None noted')}
Suggestion: {grade.get('suggestion', 'None noted')}

## DECISION FEEDBACK SUMMARY:
{json.dumps(feedback_summary, indent=2)}

## ACTIVITY GOVERNOR SUMMARY:
{json.dumps(activity_summary, indent=2)}

## COMPONENT CALIBRATION SUMMARY:
{json.dumps(component_calibration, indent=2)}

## BEHAVIOR STATE SNAPSHOT:
{json.dumps(behavior_state, indent=2)}

## EFFECTIVE RISK PROFILE:
{json.dumps(effective_risk_profile, indent=2)}

Given the feedback above, output your JSON tuning patch for the supported variables only.
"""

        result_str = report_generator._call_gemini(
            prompt=prompt,
            system=_TUNER_SYSTEM,
            max_tokens=384,
            json_mode=True,
        )

        try:
            patch = json.loads(result_str)
        except json.JSONDecodeError:
            clean = result_str.replace("```json", "").replace("```", "").strip()
            patch = json.loads(clean)

        new_tp = float(patch.get("take_profit_pct", take_profit))
        new_sl = float(patch.get("stop_loss_pct", stop_loss))
        new_rsi = float(patch.get("rsi_1m_sniper_dip", rsi_dip))
        new_min_signal = float(patch.get("min_signal_score", min_signal_score))
        new_rsi_oversold = float(patch.get("rsi_oversold", rsi_oversold))
        new_breakout_mult = float(patch.get("breakout_volume_mult", breakout_volume_mult))
        reasoning = patch.get("reasoning", "Autonomous safety tuning.")

        new_tp = max(0.5, min(10.0, new_tp))
        new_sl = max(0.5, min(10.0, new_sl))
        new_rsi = max(25.0, min(65.0, new_rsi))
        new_min_signal = max(62.0, min(78.0, new_min_signal))
        new_rsi_oversold = max(28.0, min(38.0, new_rsi_oversold))
        new_breakout_mult = max(1.5, min(2.3, new_breakout_mult))

        current_bot_cfg.setdefault("synthetic_exits", {})
        current_bot_cfg.setdefault("short_term", {})
        current_bot_cfg["synthetic_exits"]["take_profit_pct"] = round(new_tp, 2)
        current_bot_cfg["synthetic_exits"]["stop_loss_pct"] = round(new_sl, 2)
        current_bot_cfg["short_term"]["rsi_1m_sniper_dip"] = round(new_rsi, 2)
        current_bot_cfg["short_term"]["rsi_oversold"] = round(new_rsi_oversold, 2)
        current_bot_cfg["short_term"]["breakout_volume_mult"] = round(new_breakout_mult, 3)
        current_bot_cfg["min_signal_score"] = round(new_min_signal, 2)
        current_bot_cfg["last_ai_tuning_ts"] = int(time.time() * 1000)

        full_cfg[profile_key] = current_bot_cfg
        bot.update_config({"stocks": {"crypto": full_cfg}})

        store._record_action_sync(
            action_type="gemini_report",
            symbol="AI TUNER",
            side="info",
            status="info",
            reason=f"Hyperparameters tuned automatically. Reason: {reasoning}",
            payload={
                "old_cfg": {
                    "take_profit_pct": take_profit,
                    "stop_loss_pct": stop_loss,
                    "rsi_1m_sniper_dip": rsi_dip,
                    "min_signal_score": min_signal_score,
                    "rsi_oversold": rsi_oversold,
                    "breakout_volume_mult": breakout_volume_mult,
                },
                "new_cfg": {
                    "take_profit_pct": round(new_tp, 2),
                    "stop_loss_pct": round(new_sl, 2),
                    "rsi_1m_sniper_dip": round(new_rsi, 2),
                    "min_signal_score": round(new_min_signal, 2),
                    "rsi_oversold": round(new_rsi_oversold, 2),
                    "breakout_volume_mult": round(new_breakout_mult, 3),
                },
                "decision_feedback_summary": feedback_summary,
                "activity_governor_summary": activity_summary,
                "component_calibration": component_calibration,
                "behavior_state": behavior_state,
                "effective_risk_profile": effective_risk_profile,
            },
        )

        logger.info(
            "Auto-Tuning Applied: TP %s->%s | SL %s->%s | RSI dip %s->%s | min score %s->%s | RSI oversold %s->%s | breakout mult %s->%s",
            take_profit,
            new_tp,
            stop_loss,
            new_sl,
            rsi_dip,
            new_rsi,
            min_signal_score,
            new_min_signal,
            rsi_oversold,
            new_rsi_oversold,
            breakout_volume_mult,
            new_breakout_mult,
        )
        return {
            "take_profit_pct": round(new_tp, 2),
            "stop_loss_pct": round(new_sl, 2),
            "rsi_1m_sniper_dip": round(new_rsi, 2),
            "min_signal_score": round(new_min_signal, 2),
            "rsi_oversold": round(new_rsi_oversold, 2),
            "breakout_volume_mult": round(new_breakout_mult, 3),
            "reasoning": reasoning,
        }
    except Exception as e:
        logger.error(f"Failed to run Self-Tuner: {e}\n{traceback.format_exc()}")
        return None


