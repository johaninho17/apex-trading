from __future__ import annotations

from typing import Any, Dict, List


def build_query_scratchpad(
    *,
    question: str,
    intent: str,
    entity_resolution: Dict[str, Any],
    datasets: List[str],
    calculations: List[str],
    evidence_summary: Dict[str, Any],
    computed_metrics: Dict[str, Any],
    critique: Dict[str, Any],
    final_answer: str,
) -> Dict[str, Any]:
    return {
        "question": str(question or ""),
        "intent": str(intent or ""),
        "entity_resolution": dict(entity_resolution or {}),
        "plan_steps": [
            "resolve_intent_and_entities",
            "retrieve_structured_data",
            "retrieve_bot_knowledge",
            "compute_metrics",
            "critique_grounding",
            "write_final_answer",
        ],
        "retrieved_datasets": list(datasets or []),
        "computed_calculations": list(calculations or []),
        "evidence_summary": dict(evidence_summary or {}),
        "computed_metrics": dict(computed_metrics or {}),
        "critique": dict(critique or {}),
        "final_answer": str(final_answer or ""),
    }


def critique_query_answer(question: str, metrics: Dict[str, Any], answer: str) -> Dict[str, Any]:
    text = str(answer or "")
    issues: List[str] = []
    confidence = 100.0
    if not text.strip():
        issues.append("empty_answer")
        confidence -= 60.0
    if metrics and len(text) < 40:
        issues.append("answer_too_short_for_available_metrics")
        confidence -= 15.0
    for key in ("avg_pnl_pct", "win_rate_pct", "macro_risk_multiplier", "edge_score"):
        if key in metrics and str(metrics[key]) not in text:
            confidence -= 4.0
    if "unknown" in text.lower():
        confidence -= 8.0
    if "i think" in text.lower():
        issues.append("speculative_language")
        confidence -= 10.0
    if "ask next" in text.lower() and len(text.splitlines()) <= 3:
        issues.append("followups_over_answer")
        confidence -= 8.0
    verdict = "grounded" if confidence >= 80 else "partial" if confidence >= 60 else "weak"
    return {
        "verdict": verdict,
        "confidence": round(max(0.0, min(100.0, confidence)), 2),
        "issues": issues,
        "question": str(question or ""),
    }
