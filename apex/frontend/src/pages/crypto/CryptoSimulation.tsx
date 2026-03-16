import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Play, RefreshCw } from 'lucide-react';
import LightweightTimeSeriesChart from '../../components/charts/LightweightTimeSeriesChart';
import { type RangeKey, type SimulationResultResponse } from '../../lib/cryptoApi';
import { runSimulationMutation, useSimulationConfigQuery } from '../../lib/cryptoQueries';

function numberOf(value: unknown, fallback = 0): number {
  return typeof value === 'number' ? value : Number(value ?? fallback) || fallback;
}

function fmtNum(value: unknown, digits = 2): string {
  return numberOf(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export default function CryptoSimulation() {
  const queryClient = useQueryClient();
  const simulationConfigQuery = useSimulationConfigQuery(true);
  const [scope, setScope] = useState('single');
  const [symbolText, setSymbolText] = useState('BTC/USD');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [initialEquity, setInitialEquity] = useState('10000');
  const [macroScore, setMacroScore] = useState('1.0');
  const [slippagePct, setSlippagePct] = useState('0.05');
  const [commissionPct, setCommissionPct] = useState('0');
  const [maxPositionPct, setMaxPositionPct] = useState('0.10');
  const [result, setResult] = useState<SimulationResultResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  const availableScopes = simulationConfigQuery.data?.scopes ?? [];
  const trackedPreview = simulationConfigQuery.data?.tracked_preview ?? [];
  const activePreview = simulationConfigQuery.data?.active_preview ?? [];
  const chartData = useMemo(
    () => (result?.equity_curve ?? []).map((point) => ({ ts: numberOf((point as Record<string, unknown>).ts_ms), value: numberOf((point as Record<string, unknown>).equity) })),
    [result?.equity_curve],
  );

  const runSimulation = async () => {
    setRunning(true);
    setError('');
    try {
      const symbols = symbolText
        .split(',')
        .map((value) => value.trim().toUpperCase())
        .filter(Boolean);
      const payload = {
        scope,
        symbols,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        initial_equity: numberOf(initialEquity, 10000),
        macro_score: numberOf(macroScore, 1.0),
        slippage_pct: numberOf(slippagePct, 0.05),
        commission_pct: numberOf(commissionPct, 0.0),
        max_position_pct: numberOf(maxPositionPct, 0.10),
      };
      const next = await runSimulationMutation(queryClient, payload);
      setResult(next);
      if (next.message) {
        setError(String(next.message));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Simulation failed');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {(error || simulationConfigQuery.error?.message) && (
        <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem' }}>
          {error || simulationConfigQuery.error?.message}
        </div>
      )}

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="ts-panel-title"><FlaskConical size={15} /> Simulation Lab</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
              First pass uses the live crypto backtest engine with configurable replay controls, then aggregates basket results into one lab view.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="ts-refresh-btn" onClick={() => void simulationConfigQuery.refetch()} disabled={simulationConfigQuery.isFetching}>
              <RefreshCw size={14} />
            </button>
            <button className="ts-bot-toggle start" onClick={() => void runSimulation()} disabled={running}>
              <Play size={14} /> {running ? 'Running...' : 'Run Simulation'}
            </button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Scope</span>
            <select value={scope} onChange={(event) => setScope(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }}>
              {availableScopes.map((item) => (
                <option key={String(item.id)} value={String(item.id)}>{String(item.label)}</option>
              ))}
            </select>
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Symbols</span>
            <input value={symbolText} onChange={(event) => setSymbolText(event.target.value)} placeholder="BTC/USD or BTC/USD,ETH/USD" style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Start Date</span>
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">End Date</span>
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Initial Equity</span>
            <input value={initialEquity} onChange={(event) => setInitialEquity(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Macro Score</span>
            <input value={macroScore} onChange={(event) => setMacroScore(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Slippage %</span>
            <input value={slippagePct} onChange={(event) => setSlippagePct(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Commission %</span>
            <input value={commissionPct} onChange={(event) => setCommissionPct(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
          <label className="ts-pulse-card" style={{ display: 'grid', gap: 6 }}>
            <span className="pulse-label">Max Position %</span>
            <input value={maxPositionPct} onChange={(event) => setMaxPositionPct(event.target.value)} style={{ background: 'rgba(10, 13, 23, 0.96)', color: 'var(--text-primary)', border: '1px solid rgba(159, 176, 204, 0.16)', borderRadius: 10, padding: '10px 12px' }} />
          </label>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
          <div className="ts-pulse-card" style={{ minHeight: 110 }}>
            <span className="pulse-label">Tracked Preview</span>
            <div style={{ color: 'var(--text-primary)', fontSize: '0.86rem', marginTop: 8 }}>{trackedPreview.join(', ') || 'No tracked preview available.'}</div>
          </div>
          <div className="ts-pulse-card" style={{ minHeight: 110 }}>
            <span className="pulse-label">Active Preview</span>
            <div style={{ color: 'var(--text-primary)', fontSize: '0.86rem', marginTop: 8 }}>{activePreview.join(', ') || 'No active preview available.'}</div>
          </div>
        </div>
      </div>

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div className="ts-panel-title">Equity Curve</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
            Replay output uses the same crypto backtest engine, then aggregates the selected symbols into one curve.
          </div>
        </div>
        <LightweightTimeSeriesChart
          data={chartData}
          rangeKey={'ALL' as RangeKey}
          height={340}
          lineColor="#2dd4bf"
          emptyLabel={running ? 'Running simulation...' : 'Run a simulation to see the equity curve.'}
          valueFormatter={(value) => `$${fmtNum(value, 2)}`}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title">Simulation Stats</div>
          </div>
          {result?.stats ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="ts-pulse-card"><span className="pulse-label">Symbols</span><span className="pulse-value">{fmtNum((result.stats as Record<string, unknown>).symbol_count, 0)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Trades</span><span className="pulse-value">{fmtNum((result.stats as Record<string, unknown>).total_trades, 0)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Win Rate</span><span className="pulse-value">{fmtNum((result.stats as Record<string, unknown>).win_rate_pct, 1)}%</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Total PnL</span><span className="pulse-value">${fmtNum((result.stats as Record<string, unknown>).total_pnl_usd, 2)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">PnL %</span><span className="pulse-value">{fmtNum((result.stats as Record<string, unknown>).total_pnl_pct, 2)}%</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Final Equity</span><span className="pulse-value">${fmtNum((result.stats as Record<string, unknown>).final_equity, 2)}</span></div>
            </div>
          ) : (
            <div className="ts-empty" style={{ minHeight: 220 }}>
              <p>No simulation result yet.</p>
            </div>
          )}
        </div>

        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title">Safeguard Diagnostics</div>
          </div>
          {result?.safeguard_diagnostics ? (
            <div style={{ display: 'grid', gap: 12 }}>
              <div className="ts-pulse-card"><span className="pulse-label">Trades / Hour Proxy</span><span className="pulse-value">{fmtNum((result.safeguard_diagnostics as Record<string, unknown>).trades_per_hour_proxy, 2)}</span></div>
              <div className="ts-pulse-card">
                <span className="pulse-label">Same-Symbol Trade Counts</span>
                <div style={{ color: 'var(--text-primary)', fontSize: '0.82rem', marginTop: 8 }}>
                  {Object.entries(((result.safeguard_diagnostics as Record<string, unknown>).same_symbol_trade_counts ?? {}) as Record<string, unknown>).map(([symbol, count]) => `${symbol}: ${fmtNum(count, 0)}`).join(' | ') || 'n/a'}
                </div>
              </div>
              <div className="ts-pulse-card">
                <span className="pulse-label">Blocked Reasons</span>
                <div style={{ color: 'var(--text-primary)', fontSize: '0.82rem', marginTop: 8 }}>
                  {Object.entries(((result.safeguard_diagnostics as Record<string, unknown>).blocked_reasons ?? {}) as Record<string, unknown>).map(([reason, count]) => `${reason}: ${fmtNum(count, 0)}`).join(' | ') || 'none'}
                </div>
              </div>
            </div>
          ) : (
            <div className="ts-empty" style={{ minHeight: 220 }}>
              <p>Run a simulation to inspect trade-frequency and block diagnostics.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
