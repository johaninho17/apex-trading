from services.crypto.activity_governor import apply_governor_to_config, compute_governor_state, default_governor_config, default_governor_state


def _order(ts, side="buy"):
    return {"ts": ts, "action_type": "order_submitted", "status": "success", "side": side}


def _signal(ts):
    return {"ts": ts, "action_type": "signal_detected", "status": "signal", "side": "buy"}


def _trace(ts, score=66.0, submitted=False, block_reason="score_floor"):
    return {"ts": ts, "submitted": submitted, "final_score": score, "block_reason": block_reason, "payload": {}}


def _outcome(ts, label):
    return {"decision_ts": ts, "outcome_label": label}


def test_activity_governor_enters_cold_mode_after_hysteresis():
    now_ms = 3 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [_signal(now_ms - 60 * 60 * 1000)] * 5
    traces = [_trace(now_ms - 30 * 60 * 1000, score=65.0) for _ in range(4)]

    first = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=traces,
        decision_outcomes=[],
        open_positions_count=0,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )
    second = compute_governor_state(
        cfg,
        now_ms=now_ms + 600000,
        actions=actions,
        decision_traces=traces,
        open_positions_count=0,
        macro_risk_mult=1.0,
        previous_state=first,
    )

    assert first["mode"] == "normal"
    assert second["mode"] == "cold"
    assert second["pressure_score"] < 0
    assert "no_buys_48h" in second["reason_codes"]


def test_activity_governor_enters_overactive_mode_with_churn():
    now_ms = 10 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [
        _order(now_ms - 5 * 60 * 1000),
        _order(now_ms - 10 * 60 * 1000),
        _order(now_ms - 15 * 60 * 1000),
        _order(now_ms - 20 * 60 * 1000),
        _order(now_ms - 25 * 60 * 1000),
        _order(now_ms - 30 * 60 * 1000),
        _order(now_ms - 35 * 60 * 1000),
        _order(now_ms - 40 * 60 * 1000),
        {"ts": now_ms - 50 * 60 * 1000, "action_type": "synthetic_exit", "status": "success", "side": "sell"},
        {"ts": now_ms - 60 * 60 * 1000, "action_type": "synthetic_exit_trailing", "status": "success", "side": "sell"},
    ]
    first = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=[],
        decision_outcomes=[],
        open_positions_count=7,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )
    second = compute_governor_state(
        cfg,
        now_ms=now_ms + 600000,
        actions=actions,
        decision_traces=[],
        open_positions_count=7,
        macro_risk_mult=1.0,
        previous_state=first,
    )

    assert second["mode"] == "overactive"
    assert second["pressure_score"] >= 65
    assert "fast_exit_churn" in second["reason_codes"]


def test_apply_governor_to_config_clamps_effective_thresholds():
    cfg = {
        "min_signal_score": 77.5,
        "trade_spacing_min": 58,
        "short_term": {"rsi_oversold": 37.5, "breakout_volume_mult": 2.25},
        "activity_governor": default_governor_config(),
    }
    state = {
        "mode": "overactive",
        "pressure_score": 88,
        "reason_codes": ["fast_exit_churn"],
        "modifiers": default_governor_config()["modes"]["overactive"],
    }
    applied = apply_governor_to_config(cfg, state)
    assert applied["min_signal_score"] == 78.0
    assert applied["trade_spacing_min"] == 60
    assert applied["short_term"]["rsi_oversold"] == 35.5
    assert applied["short_term"]["breakout_volume_mult"] == 2.3


def test_activity_governor_relaxes_after_bad_block_cluster():
    now_ms = 10 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [_signal(now_ms - 2 * 3600 * 1000) for _ in range(4)]
    traces = [_trace(now_ms - 90 * 60 * 1000, score=66.0) for _ in range(2)]
    outcomes = [_outcome(now_ms - 3 * 3600 * 1000, "bad_block") for _ in range(3)]

    state = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=traces,
        decision_outcomes=outcomes,
        open_positions_count=0,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )

    assert state["pressure_score"] < 0
    assert "bad_block_cluster" in state["reason_codes"]
    assert state["metrics"]["bad_block_24h"] == 3


def test_activity_governor_tightens_after_good_block_cluster():
    now_ms = 10 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [_order(now_ms - 15 * 60 * 1000) for _ in range(3)]
    outcomes = [_outcome(now_ms - 2 * 3600 * 1000, "good_block") for _ in range(3)]

    state = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=[],
        decision_outcomes=outcomes,
        open_positions_count=2,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )

    assert state["pressure_score"] > 0
    assert "good_block_cluster" in state["reason_codes"]
    assert state["metrics"]["good_block_24h"] == 3


def test_activity_governor_uses_min_interval_when_mode_boundary_is_active():
    now_ms = 3 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [_signal(now_ms - 60 * 60 * 1000)] * 5
    traces = [_trace(now_ms - 30 * 60 * 1000, score=65.0) for _ in range(4)]

    state = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=traces,
        decision_outcomes=[],
        open_positions_count=0,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )

    assert state["mode"] == "normal"
    assert state["candidate_mode"] == "cold"
    assert state["candidate_streak"] == 1
    assert state["next_update_interval_sec"] == 600


def test_activity_governor_uses_max_interval_when_behavior_is_stable():
    now_ms = 20 * 24 * 3600 * 1000
    cfg = {
        "activity_governor": default_governor_config(),
        "min_signal_score": 68.0,
        "max_trades_per_hour": 8,
        "max_open_positions": 8,
    }
    actions = [
        _order(now_ms - 3 * 60 * 60 * 1000),
        _order(now_ms - 2 * 60 * 60 * 1000),
    ]

    state = compute_governor_state(
        cfg,
        now_ms=now_ms,
        actions=actions,
        decision_traces=[],
        decision_outcomes=[],
        open_positions_count=1,
        macro_risk_mult=1.0,
        previous_state=default_governor_state(now_ms - 600000),
    )

    assert state["mode"] == "normal"
    assert state["pressure_score"] == 0
    assert state["next_update_interval_sec"] == 2400
