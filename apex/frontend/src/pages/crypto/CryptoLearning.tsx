import { useMemo, useState } from 'react';
import { BrainCircuit, RefreshCw, Sparkles, Terminal } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useBrainOverviewQuery, useLearningOverviewQuery } from '../../lib/cryptoQueries';

function numberOf(value: unknown, fallback = 0): number {
  return typeof value === 'number' ? value : Number(value ?? fallback) || fallback;
}

function fmtNum(value: unknown, digits = 2): string {
  return numberOf(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function positiveNegativeTone(value: unknown): string {
  const num = numberOf(value);
  if (num > 0) return 'var(--accent-green)';
  if (num < 0) return 'var(--accent-red)';
  return 'var(--text-primary)';
}

function scoreTone(value: unknown): string {
  const score = numberOf(value);
  if (score >= 80) return 'var(--accent-green)';
  if (score >= 60) return 'var(--accent-cyan)';
  if (score >= 40) return 'var(--accent-orange)';
  return 'var(--accent-red)';
}

export default function CryptoLearning() {
  const [activeTab, setActiveTab] = useState<'learning' | 'brain'>('learning');
  const learningQuery = useLearningOverviewQuery(activeTab === 'learning');
  const brainQuery = useBrainOverviewQuery(activeTab === 'brain');

  const learning = learningQuery.data;
  const brain = brainQuery.data;
  const activeQuery = activeTab === 'learning' ? learningQuery : brainQuery;
  const scoreHistory = activeTab === 'learning'
    ? (learning?.brain_self_score_history ?? [])
    : (brain?.brain_self_score_history ?? []);
  const chartData = useMemo(
    () => scoreHistory.map((point) => ({ ts: point.ts, score: point.score })),
    [scoreHistory],
  );
  const overlays = activeTab === 'learning' ? (learning?.overlays ?? []) : (brain?.overlays ?? []);
  const models = brain?.models ?? [];
  const strategies = brain?.strategy_performance ?? [];
  const logTail = learning?.log_tail ?? [];
  const pendingOutcomes = learning?.pending_outcomes ?? {};
  const liveExperience = (learning?.live_experience ?? {}) as Record<string, unknown>;
  const safeguards = (learning?.safeguards ?? {}) as Record<string, unknown>;
  const topBlockReasons = Array.isArray(safeguards.top_block_reasons_24h) ? safeguards.top_block_reasons_24h : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {activeQuery.error?.message && (
        <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem' }}>
          {activeQuery.error.message}
        </div>
      )}

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div className="ts-panel-title"><BrainCircuit size={15} /> Neural Net & Intelligence</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className={`ts-tab ${activeTab === 'learning' ? 'active' : ''}`} onClick={() => setActiveTab('learning')}>
              <Sparkles size={12} /> Learning Loop
            </button>
            <button className={`ts-tab ${activeTab === 'brain' ? 'active' : ''}`} onClick={() => setActiveTab('brain')}>
              <BrainCircuit size={12} /> Brain
            </button>
            <button className="ts-refresh-btn" aria-label="Refresh learning data" onClick={() => void activeQuery.refetch()} disabled={activeQuery.isFetching}>
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 16 }}>
          <div className="ts-panel">
            <div className="ts-panel-header">
              <div className="ts-panel-title">Self-Score Over Time</div>
            </div>
            <div style={{ width: '100%', height: 260 }}>
              {activeQuery.isLoading && chartData.length === 0 ? (
                <div className="ts-empty" style={{ minHeight: 220 }}>
                  <p>Loading self-score history…</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <XAxis dataKey="ts" tickFormatter={(value) => new Date(numberOf(value)).toLocaleTimeString()} />
                    <YAxis domain={[0, 100]} />
                    <Tooltip
                      contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
                      labelFormatter={(value) => new Date(numberOf(value)).toLocaleString()}
                    />
                    <Line type="monotone" dataKey="score" stroke="#34d399" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="ts-panel">
            <div className="ts-panel-header">
              <div className="ts-panel-title">{activeTab === 'learning' ? 'Learning Loop Status' : 'Brain Status'}</div>
            </div>
            {activeTab === 'learning' ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="ts-pulse-card"><span className="pulse-label">Closed Trades</span><span className="pulse-value">{fmtNum(liveExperience.closed, 0)}</span></div>
                <div className="ts-pulse-card"><span className="pulse-label">Win Rate</span><span className="pulse-value" style={{ color: scoreTone(liveExperience.win_rate_pct) }}>{fmtNum(liveExperience.win_rate_pct, 1)}%</span></div>
                <div className="ts-pulse-card"><span className="pulse-label">Avg PnL</span><span className="pulse-value" style={{ color: positiveNegativeTone(liveExperience.avg_pnl) }}>{fmtNum(liveExperience.avg_pnl, 2)}%</span></div>
                <div className="ts-pulse-card"><span className="pulse-label">Total PnL</span><span className="pulse-value" style={{ color: positiveNegativeTone(liveExperience.total_pnl) }}>{fmtNum(liveExperience.total_pnl, 2)}%</span></div>
                {Object.entries(pendingOutcomes).map(([key, value]) => (
                  <div className="ts-pulse-card" key={key}>
                    <span className="pulse-label">Pending {key}m</span>
                    <span className="pulse-value">{fmtNum(value, 0)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 10 }}>
                {models.length === 0 ? (
                  <div className="ts-empty" style={{ minHeight: 220 }}>
                    <p>No brain model artifacts found yet.</p>
                  </div>
                ) : (
                  models.slice(0, 8).map((model, index) => (
                    <div key={`model-${index}`} className="ts-pulse-card">
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                        <strong>{String(model.name ?? 'ppo_model.zip')}</strong>
                        <span>{fmtNum(model.size_kb, 1)} KB</span>
                      </div>
                      <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
                        Updated {new Date(numberOf(model.last_modified_ms)).toLocaleString()}
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title">Per-Symbol Overlay Leaderboard</div>
          </div>
          {overlays.length === 0 ? (
            <div className="ts-empty" style={{ minHeight: 320 }}>
              <p>No learned symbol overlays yet.</p>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.82rem' }}>
                The leaderboard activates once decision outcomes start being written for each coin.
              </p>
            </div>
          ) : (
            <div className="ts-results-wrap">
              <table className="ts-results-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Score</th>
                    <th>Size</th>
                    <th>Cooldown</th>
                    <th>Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {overlays.map((overlay) => (
                    <tr key={overlay.symbol}>
                      <td style={{ color: 'var(--color-alpaca)', fontWeight: 700 }}>{overlay.symbol}</td>
                      <td style={{ color: positiveNegativeTone(overlay.score_delta), fontVariantNumeric: 'tabular-nums' }}>{fmtNum(overlay.score_delta, 1)}</td>
                      <td>{fmtNum(overlay.size_multiplier, 2)}x</td>
                      <td>{fmtNum(overlay.cooldown_multiplier, 2)}x</td>
                      <td>{fmtNum(overlay.decision_samples, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.76rem', padding: '0 16px 16px 16px', lineHeight: 1.5 }}>
            Score is the learned edge delta applied to the symbol and is bounded near -20 to +20. Size is the learned position-size multiplier, cooldown is the learned pacing multiplier, and samples shows how many evaluated outcomes the overlay has seen.
          </div>
        </div>

        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title">{activeTab === 'brain' ? 'Strategy Leaderboard' : 'Training Log'}</div>
          </div>
          {activeTab === 'brain' ? (
            strategies.length === 0 ? (
              <div className="ts-empty" style={{ minHeight: 320 }}>
                <p>No strategy performance rows available yet.</p>
              </div>
            ) : (
              <div className="ts-results-wrap">
                <table className="ts-results-table">
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Trades</th>
                      <th>Win %</th>
                      <th>Total PnL %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategies.map((strategy, index) => {
                      const row = strategy as Record<string, unknown>;
                      return (
                        <tr key={`strategy-${index}`}>
                          <td style={{ color: 'var(--color-alpaca)', fontWeight: 700 }}>{String(row.strategy ?? 'unknown')}</td>
                          <td>{fmtNum(row.trades, 0)}</td>
                          <td style={{ color: scoreTone(row.win_rate_pct), fontVariantNumeric: 'tabular-nums' }}>{fmtNum(row.win_rate_pct, 1)}%</td>
                          <td style={{ color: positiveNegativeTone(row.total_pnl_pct), fontVariantNumeric: 'tabular-nums' }}>{fmtNum(row.total_pnl_pct, 2)}%</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.74rem', padding: '0 16px 10px 16px' }}>
                Newest lines are shown first so the latest training activity stays at the top.
              </div>
              <div className="ts-slm-terminal" style={{ height: 480, maxHeight: 'none' }}>
              {logTail.length === 0 ? (
                <div className="ts-empty" style={{ minHeight: 240 }}>
                  <Terminal size={26} />
                  <p>No training log lines available yet.</p>
                </div>
              ) : (
                logTail.map((line, index) => (
                  <div key={`log-${index}`} className="slm-line slm-ai">{line}</div>
                ))
              )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div className="ts-panel-title">Safeguard Audit</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
            These metrics confirm the bot is respecting spacing, cooldown, and governor pressure instead of churning on noise.
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
          <div className="ts-pulse-card"><span className="pulse-label">Trades Last Hour</span><span className="pulse-value">{fmtNum(safeguards.trades_last_hour, 0)}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Buys Last Hour</span><span className="pulse-value">{fmtNum(safeguards.buys_last_hour, 0)}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Trades Last 24H</span><span className="pulse-value">{fmtNum(safeguards.trades_last_24h, 0)}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Avg Same-Coin Gap</span><span className="pulse-value">{safeguards.avg_minutes_between_same_symbol_buys == null ? 'n/a' : `${fmtNum(safeguards.avg_minutes_between_same_symbol_buys, 1)}m`}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Max Trades / Hour</span><span className="pulse-value">{fmtNum(((safeguards.limits ?? {}) as Record<string, unknown>).max_trades_per_hour, 0)}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Per-Coin / Day</span><span className="pulse-value">{fmtNum(((safeguards.limits ?? {}) as Record<string, unknown>).max_trades_per_coin_per_day, 0)}</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Trade Spacing</span><span className="pulse-value">{fmtNum(((safeguards.limits ?? {}) as Record<string, unknown>).trade_spacing_min, 0)}m</span></div>
          <div className="ts-pulse-card"><span className="pulse-label">Governor Mode</span><span className="pulse-value">{String((((safeguards.governor ?? {}) as Record<string, unknown>).mode ?? 'normal')).toUpperCase()}</span></div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 16, padding: '16px' }}>
          <div className="ts-pulse-card" style={{ minHeight: 140 }}>
            <span className="pulse-label">Top Block Reasons (24H)</span>
            <div style={{ color: 'var(--text-primary)', fontSize: '0.84rem', marginTop: 10, lineHeight: 1.55 }}>
              {topBlockReasons.length === 0
                ? 'No recent block reasons recorded.'
                : topBlockReasons.map((item) => `${String((item as Record<string, unknown>).reason ?? 'unknown')}: ${fmtNum((item as Record<string, unknown>).count, 0)}`).join(' | ')}
            </div>
          </div>
          <div className="ts-pulse-card" style={{ minHeight: 140 }}>
            <span className="pulse-label">Buys By Coin (24H)</span>
            <div style={{ color: 'var(--text-primary)', fontSize: '0.84rem', marginTop: 10, lineHeight: 1.55 }}>
              {Object.entries(((safeguards.buys_by_symbol_24h ?? {}) as Record<string, unknown>))
                .map(([symbol, count]) => `${symbol}: ${fmtNum(count, 0)}`)
                .join(' | ') || 'No recent buy actions recorded.'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
