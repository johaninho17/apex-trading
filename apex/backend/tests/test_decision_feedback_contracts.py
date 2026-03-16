from services.crypto.decision_feedback import evaluate_pending_decision_outcomes


def _trace(trace_id, decision='buy', submitted=False, close=100.0, block_reason='score_floor'):
    return {
        'id': trace_id,
        'ts': 1_000_000,
        'symbol': 'BTC/USD',
        'decision': decision,
        'submitted': submitted,
        'block_reason': block_reason,
        'payload': {
            'signal': {'close': close, 'strategy': 'mean_reversion'},
            'meta': {'brain_mode': 'drl_event_fusion'},
        },
    }


def test_decision_feedback_labels_good_and_bad_blocks(monkeypatch):
    recorded = []
    traces = [_trace(1, submitted=False), _trace(2, submitted=True)]

    monkeypatch.setattr('services.crypto.decision_feedback.store.list_pending_decision_traces_for_outcome_sync', lambda horizon_min, older_than_ts, limit=100: traces if horizon_min == 30 else [])
    monkeypatch.setattr('services.crypto.decision_feedback.market_data.fetch_price_near_ts', lambda symbol, target_ts_ms, timeframe='1Min', mode=None, window_min=180: 103.0 if target_ts_ms else None)
    monkeypatch.setattr('services.crypto.decision_feedback.store.record_decision_outcome_sync', lambda **kwargs: recorded.append(kwargs) or len(recorded))

    result = evaluate_pending_decision_outcomes(mode='paper', feedback_cfg={'horizons_min': [30], 'limit_per_pass': 10}, now_ms=5_000_000)

    assert result['recorded'] == 2
    labels = {row['trace_id']: row['outcome_label'] for row in recorded}
    assert labels[1] == 'bad_block'
    assert labels[2] == 'good_entry'


def test_decision_feedback_labels_good_block_and_bad_entry(monkeypatch):
    recorded = []
    traces = [_trace(3, submitted=False), _trace(4, submitted=True)]

    monkeypatch.setattr('services.crypto.decision_feedback.store.list_pending_decision_traces_for_outcome_sync', lambda horizon_min, older_than_ts, limit=100: traces if horizon_min == 120 else [])
    monkeypatch.setattr('services.crypto.decision_feedback.market_data.fetch_price_near_ts', lambda symbol, target_ts_ms, timeframe='1Min', mode=None, window_min=180: 96.0)
    monkeypatch.setattr('services.crypto.decision_feedback.store.record_decision_outcome_sync', lambda **kwargs: recorded.append(kwargs) or len(recorded))

    result = evaluate_pending_decision_outcomes(mode='paper', feedback_cfg={'horizons_min': [120], 'limit_per_pass': 10}, now_ms=10_000_000)

    assert result['recorded'] == 2
    labels = {row['trace_id']: row['outcome_label'] for row in recorded}
    assert labels[3] == 'good_block'
    assert labels[4] == 'bad_entry'


def test_decision_feedback_uses_market_lookup_when_trace_has_no_ref_price(monkeypatch):
    recorded = []
    trace = _trace(5, submitted=True, close=0.0)
    trace['payload']['signal'].pop('close', None)
    decision_ts = trace['ts']

    monkeypatch.setattr(
        'services.crypto.decision_feedback.store.list_pending_decision_traces_for_outcome_sync',
        lambda horizon_min, older_than_ts, limit=100: [trace] if horizon_min == 30 else [],
    )
    monkeypatch.setattr(
        'services.crypto.decision_feedback.market_data.fetch_price_near_ts',
        lambda symbol, target_ts_ms, timeframe='1Min', mode=None, window_min=180: 100.0 if target_ts_ms == decision_ts else 103.0,
    )
    monkeypatch.setattr('services.crypto.decision_feedback.store.record_decision_outcome_sync', lambda **kwargs: recorded.append(kwargs) or len(recorded))

    result = evaluate_pending_decision_outcomes(mode='paper', feedback_cfg={'horizons_min': [30], 'limit_per_pass': 10}, now_ms=5_000_000)

    assert result['recorded'] == 1
    assert recorded[0]['ref_price'] == 100.0
    assert recorded[0]['outcome_price'] == 103.0
