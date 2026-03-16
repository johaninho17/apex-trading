from services.crypto.mode_effectiveness import evaluate_mode_effectiveness


def test_mode_effectiveness_scores_helping_mode(monkeypatch):
    outcomes = [
        {
            'decision_ts': 5_000_000,
            'submitted': True,
            'pnl_pct': 1.2,
            'outcome_label': 'good_entry',
            'payload': {'meta': {'activity_governor_mode': 'cold'}},
        },
        {
            'decision_ts': 5_100_000,
            'submitted': False,
            'pnl_pct': -1.5,
            'outcome_label': 'good_block',
            'payload': {'meta': {'activity_governor_mode': 'cold'}},
        },
    ]
    recorded = []
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.list_decision_outcomes_sync', lambda limit=1000: outcomes)
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.list_governor_mode_history_sync', lambda limit=48: [])
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.record_governor_mode_history_sync', lambda **kwargs: recorded.append(kwargs) or 1)

    result = evaluate_mode_effectiveness(current_mode='cold', pressure_score=-42.0, cfg={'window_hours': 24, 'min_samples': 2}, now_ms=9_000_000)

    assert result['verdict'] == 'helping'
    assert result['effectiveness_score'] > 0
    assert result['sample_size'] == 2
    assert recorded and recorded[0]['mode'] == 'cold'


def test_mode_effectiveness_scores_hurting_mode(monkeypatch):
    outcomes = [
        {
            'decision_ts': 5_000_000,
            'submitted': True,
            'pnl_pct': -1.8,
            'outcome_label': 'bad_entry',
            'payload': {'meta': {'activity_governor_mode': 'hot'}},
        },
        {
            'decision_ts': 5_100_000,
            'submitted': False,
            'pnl_pct': 1.6,
            'outcome_label': 'bad_block',
            'payload': {'meta': {'activity_governor_mode': 'hot'}},
        },
    ]
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.list_decision_outcomes_sync', lambda limit=1000: outcomes)
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.list_governor_mode_history_sync', lambda limit=48: [])
    monkeypatch.setattr('services.crypto.mode_effectiveness.store.record_governor_mode_history_sync', lambda **kwargs: 1)

    result = evaluate_mode_effectiveness(current_mode='hot', pressure_score=55.0, cfg={'window_hours': 24, 'min_samples': 2}, now_ms=9_000_000)

    assert result['verdict'] == 'hurting'
    assert result['effectiveness_score'] < 0
