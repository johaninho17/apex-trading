import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Activity, BarChart3, BrainCircuit, ExternalLink, Newspaper, X } from 'lucide-react';
import LightweightTimeSeriesChart from '../charts/LightweightTimeSeriesChart';
import {
  useSymbolBrainQuery,
  useSymbolDecisionsQuery,
  useSymbolHistoryQuery,
  useSymbolLearningQuery,
  useSymbolNewsQuery,
  useSymbolRawContextQuery,
  useSymbolReportsQuery,
  useSymbolSnapshotQuery,
  useSymbolTradesQuery,
} from '../../lib/cryptoQueries';
import type { RangeKey } from '../../lib/cryptoApi';

type InspectorTab = 'overview' | 'market' | 'decisions' | 'trades' | 'learning' | 'brain' | 'news' | 'reports' | 'raw';
const INSPECTOR_TABS: InspectorTab[] = ['overview', 'market', 'decisions', 'trades', 'learning', 'brain', 'news', 'reports', 'raw'];

interface CryptoSymbolInspectorProps {
  symbol: string;
  mode?: 'modal' | 'page';
  onClose?: () => void;
}

const RANGE_OPTIONS: RangeKey[] = ['1H', '1D', '7D', '30D', '90D', 'ALL'];

function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function arrayOf(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === 'object' && item !== null) as Array<Record<string, unknown>>
    : [];
}

function numberOf(value: unknown, fallback = 0): number {
  return typeof value === 'number' ? value : Number(value ?? fallback) || fallback;
}

function stringOf(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function fmtNum(value: unknown, digits = 2): string {
  return numberOf(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtTs(value: unknown): string {
  const ts = numberOf(value);
  if (!ts) return 'N/A';
  return new Date(ts).toLocaleString();
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return '{}';
  }
}

function normalizeInspectorTab(value: string | null): InspectorTab {
  return INSPECTOR_TABS.includes((value ?? '') as InspectorTab) ? (value as InspectorTab) : 'overview';
}

export default function CryptoSymbolInspector({
  symbol,
  mode = 'modal',
  onClose,
}: CryptoSymbolInspectorProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [localTab, setLocalTab] = useState<InspectorTab>('overview');
  const [localRange, setLocalRange] = useState<RangeKey>('1D');
  const [visitedTabs, setVisitedTabs] = useState<Set<InspectorTab>>(new Set(['overview']));

  const activeTab = mode === 'page'
    ? normalizeInspectorTab(searchParams.get('tab'))
    : localTab;
  const rangeKey = mode === 'page'
    ? (((searchParams.get('range') as RangeKey | null) ?? '1D'))
    : localRange;

  useEffect(() => {
    setVisitedTabs((prev) => {
      const next = new Set(prev);
      next.add(activeTab);
      return next;
    });
  }, [activeTab]);

  const setTab = (tab: InspectorTab) => {
    if (mode === 'page') {
      const next = new URLSearchParams(searchParams);
      next.set('tab', tab);
      setSearchParams(next, { replace: true });
      return;
    }
    setLocalTab(tab);
  };

  const setRange = (nextRange: RangeKey) => {
    if (mode === 'page') {
      const next = new URLSearchParams(searchParams);
      next.set('range', nextRange);
      setSearchParams(next, { replace: true });
      return;
    }
    setLocalRange(nextRange);
  };

  const snapshotQuery = useSymbolSnapshotQuery(symbol, Boolean(symbol));
  const historyQuery = useSymbolHistoryQuery(symbol, rangeKey, visitedTabs.has('market'));
  const decisionsQuery = useSymbolDecisionsQuery(symbol, visitedTabs.has('decisions'));
  const tradesQuery = useSymbolTradesQuery(symbol, visitedTabs.has('trades'));
  const learningQuery = useSymbolLearningQuery(symbol, visitedTabs.has('learning'));
  const brainQuery = useSymbolBrainQuery(symbol, visitedTabs.has('brain'));
  const newsQuery = useSymbolNewsQuery(symbol, visitedTabs.has('news'));
  const reportsQuery = useSymbolReportsQuery(symbol, visitedTabs.has('reports'));
  const rawQuery = useSymbolRawContextQuery(symbol, visitedTabs.has('raw'));

  const snapshot = snapshotQuery.data;
  const overview = recordOf(snapshot?.overview);
  const quote = recordOf(overview.quote);
  const overlay = recordOf(overview.overlay);
  const counts = recordOf(overview.counts);
  const position = recordOf(overview.position);
  const decisionTraces = arrayOf(decisionsQuery.data?.decision_traces ?? snapshot?.decision_preview);
  const outcomeRows = arrayOf(decisionsQuery.data?.decision_outcomes ?? snapshot?.outcome_preview);
  const tradeRows = arrayOf(tradesQuery.data?.items ?? snapshot?.trade_preview);
  const newsRows = arrayOf(newsQuery.data?.items ?? snapshot?.news_preview);
  const reportRows = arrayOf(reportsQuery.data?.items ?? snapshot?.report_preview);
  const historyRows = arrayOf(historyQuery.data?.items);
  const chartRows = useMemo(
    () => historyRows.map((row) => ({ ts: Date.parse(stringOf(row.timestamp)), value: numberOf(row.close) })).filter((row) => Number.isFinite(row.ts)),
    [historyRows],
  );

  const combinedError = snapshotQuery.error?.message
    || historyQuery.error?.message
    || decisionsQuery.error?.message
    || tradesQuery.error?.message
    || learningQuery.error?.message
    || brainQuery.error?.message
    || newsQuery.error?.message
    || reportsQuery.error?.message
    || rawQuery.error?.message
    || '';

  const shellStyle: CSSProperties = mode === 'modal'
    ? {
        position: 'fixed',
        inset: 0,
        background: 'rgba(5, 8, 18, 0.72)',
        backdropFilter: 'blur(6px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50,
        padding: '20px 20px 20px calc(var(--shell-sidebar-offset, 0px) + 20px)',
      }
    : {
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        padding: 24,
      };

  const panelStyle: CSSProperties = mode === 'modal'
    ? {
        width: 'min(1180px, 100%)',
        maxHeight: '90vh',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid var(--border)',
        borderRadius: 18,
        background: 'linear-gradient(180deg, rgba(15,18,29,0.98) 0%, rgba(9,11,19,0.98) 100%)',
        boxShadow: '0 30px 80px rgba(0,0,0,0.45)',
      }
    : {
        border: '1px solid var(--border)',
        borderRadius: 18,
        background: 'linear-gradient(180deg, rgba(15,18,29,0.98) 0%, rgba(9,11,19,0.98) 100%)',
        overflow: 'hidden',
      };

  const tabButton = (tab: InspectorTab, label: string) => (
    <button
      key={tab}
      className={`ts-tab ${activeTab === tab ? 'active' : ''}`}
      onClick={() => setTab(tab)}
      style={{ padding: '8px 12px', fontSize: '0.78rem' }}
    >
      {label}
    </button>
  );

  const renderTab = () => {
    if (snapshotQuery.isLoading && !snapshot) {
      return <div className="ts-empty" style={{ minHeight: 280 }}><p>Loading symbol workspace...</p></div>;
    }
    if (combinedError && !snapshot) {
      return <div className="ts-empty" style={{ minHeight: 280, color: 'var(--accent-red)' }}><p>{combinedError}</p></div>;
    }

    if (activeTab === 'overview') {
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: 16 }}>
          <div className="ts-panel">
            <div className="ts-panel-header">
              <div className="ts-panel-title"><BarChart3 size={15} /> Snapshot</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="ts-pulse-card"><span className="pulse-label">Mid Price</span><span className="pulse-value">${fmtNum(quote.mid_price, 4)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Overlay Score</span><span className="pulse-value">{fmtNum(overlay.score_delta, 2)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Size Multiplier</span><span className="pulse-value">{fmtNum(overlay.size_multiplier || 1, 2)}x</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Cooldown</span><span className="pulse-value">{fmtNum(overlay.cooldown_multiplier || 1, 2)}x</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Candidates</span><span className="pulse-value">{fmtNum(counts.candidates, 0)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Blocked</span><span className="pulse-value">{fmtNum(counts.blocked, 0)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">Trades</span><span className="pulse-value">{fmtNum(counts.trades, 0)}</span></div>
              <div className="ts-pulse-card"><span className="pulse-label">News</span><span className="pulse-value">{fmtNum(counts.news, 0)}</span></div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="ts-panel">
              <div className="ts-panel-header">
                <div className="ts-panel-title"><BrainCircuit size={15} /> Position</div>
              </div>
              {Object.keys(position).length === 0 ? (
                <div style={{ color: 'var(--text-muted)' }}>No open position.</div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div><span className="pulse-label">Qty</span><div>{fmtNum(position.qty, 4)}</div></div>
                  <div><span className="pulse-label">Entry</span><div>${fmtNum(position.avg_entry_price, 4)}</div></div>
                  <div><span className="pulse-label">Market Value</span><div>${fmtNum(position.market_value, 2)}</div></div>
                  <div><span className="pulse-label">Unrealized</span><div>{fmtNum(position.unrealized_pl, 2)}</div></div>
                </div>
              )}
            </div>

            <div className="ts-panel">
              <div className="ts-panel-header">
                <div className="ts-panel-title">Context Counts</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 10 }}>
                <div className="ts-pulse-card"><span className="pulse-label">Actions</span><span className="pulse-value">{fmtNum(snapshot?.context_counts.actions, 0)}</span></div>
                <div className="ts-pulse-card"><span className="pulse-label">Reports</span><span className="pulse-value">{fmtNum(snapshot?.context_counts.reports, 0)}</span></div>
                <div className="ts-pulse-card"><span className="pulse-label">News Events</span><span className="pulse-value">{fmtNum(snapshot?.context_counts.news_events, 0)}</span></div>
              </div>
            </div>
          </div>

          <div className="ts-panel" style={{ gridColumn: '1 / -1' }}>
            <div className="ts-panel-header">
              <div className="ts-panel-title"><Activity size={15} /> Workflow & Narratives</div>
            </div>
            <div className="ts-slm-terminal" style={{ minHeight: 520, height: 'clamp(520px, 56vh, 760px)', maxHeight: 'none' }}>
              {decisionTraces.slice(0, 3).map((row, index) => (
                <div key={`trace-${index}`} className="slm-line slm-ai">
                  {stringOf(row.decision, 'decision')} | score {fmtNum(row.final_score, 1)} | {row.submitted ? 'submitted' : stringOf(row.block_reason, 'blocked')}
                </div>
              ))}
              {tradeRows.slice(0, 3).map((row, index) => (
                <div key={`trade-${index}`} className="slm-line slm-system">
                  trade | {stringOf(row.strategy_used, 'unknown')} | pnl {fmtNum(row.outcome_pnl_pct, 2)}%
                </div>
              ))}
              {newsRows.slice(0, 3).map((row, index) => (
                <div key={`news-${index}`} className="slm-line slm-ai">
                  news | {stringOf(row.headline, stringOf(row.event_type, 'event'))}
                </div>
              ))}
              {reportRows.slice(0, 3).map((row, index) => (
                <div key={`report-${index}`} className="slm-line slm-system">
                  report | {stringOf(row.report_type, 'report')} | {fmtTs(row.ts)}
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === 'market') {
      return (
        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title"><BarChart3 size={15} /> Market History</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {RANGE_OPTIONS.map((option) => (
                <button key={option} className={`ts-tab ${rangeKey === option ? 'active' : ''}`} onClick={() => setRange(option)}>
                  {option}
                </button>
              ))}
            </div>
          </div>
          <LightweightTimeSeriesChart
            key={`${symbol}-${rangeKey}`}
            data={chartRows}
            rangeKey={rangeKey}
            height={360}
            lineColor="#60a5fa"
            emptyLabel={historyQuery.isLoading ? 'Loading symbol market history...' : 'No history available for this range.'}
            valueFormatter={(value) => `$${fmtNum(value, 4)}`}
          />
        </div>
      );
    }

    if (activeTab === 'decisions') {
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="ts-panel">
            <div className="ts-panel-header"><div className="ts-panel-title">Decision Traces</div></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {decisionTraces.slice(0, 12).map((row, index) => (
                <div key={`${symbol}-trace-${index}`} className="ts-pulse-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <strong>{stringOf(row.decision, 'n/a').toUpperCase()}</strong>
                    <span>{fmtNum(row.final_score, 1)}</span>
                  </div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
                    {row.submitted ? 'Submitted' : stringOf(row.block_reason, 'Blocked')}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="ts-panel">
            <div className="ts-panel-header"><div className="ts-panel-title">Forward Outcomes</div></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {outcomeRows.slice(0, 12).map((row, index) => (
                <div key={`${symbol}-outcome-${index}`} className="ts-pulse-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <strong>{stringOf(row.outcome_label, 'unknown')}</strong>
                    <span>{fmtNum(row.pnl_pct, 2)}%</span>
                  </div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
                    Horizon {fmtNum(row.horizon_min, 0)}m
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === 'trades') {
      return (
        <div className="ts-panel">
          <div className="ts-panel-header"><div className="ts-panel-title">Trade History</div></div>
          <div className="ts-results-wrap">
            <table className="ts-results-table">
              <thead>
                <tr>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Strategy</th>
                  <th>PnL %</th>
                </tr>
              </thead>
              <tbody>
                {tradeRows.map((row, index) => (
                  <tr key={`${symbol}-trade-${index}`}>
                    <td>{fmtTs(row.entry_ts)}</td>
                    <td>{fmtTs(row.exit_ts)}</td>
                    <td>{stringOf(row.strategy_used, 'unknown')}</td>
                    <td>{fmtNum(row.outcome_pnl_pct, 2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    if (activeTab === 'learning') {
      const learningRecord = recordOf(learningQuery.data);
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="ts-panel">
            <div className="ts-panel-header"><div className="ts-panel-title">Learning Overlay</div></div>
            <pre style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {prettyJson(learningRecord.overlay)}
            </pre>
          </div>
          <div className="ts-panel">
            <div className="ts-panel-header"><div className="ts-panel-title">Outcome Labels</div></div>
            <pre style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {prettyJson(learningRecord.label_counts)}
            </pre>
          </div>
        </div>
      );
    }

    if (activeTab === 'brain') {
      return (
        <div className="ts-panel">
          <div className="ts-panel-header"><div className="ts-panel-title">Brain Context</div></div>
          <pre style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
            {prettyJson(brainQuery.data)}
          </pre>
        </div>
      );
    }

    if (activeTab === 'news') {
      return (
        <div className="ts-panel">
          <div className="ts-panel-header"><div className="ts-panel-title"><Newspaper size={15} /> News & Narrative</div></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {newsRows.map((row, index) => (
              <div key={`${symbol}-news-${index}`} className="ts-pulse-card">
                <div style={{ fontWeight: 600 }}>{stringOf(row.headline, stringOf(row.event_type, 'News event'))}</div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
                  {stringOf(row.source, 'source')} | {fmtTs(row.published_at)}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    if (activeTab === 'reports') {
      return (
        <div className="ts-panel">
          <div className="ts-panel-header"><div className="ts-panel-title">Reports Mentioning {symbol}</div></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {reportRows.map((row, index) => (
              <div key={`${symbol}-report-${index}`} className="ts-pulse-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <strong>{stringOf(row.report_type, 'report').toUpperCase()}</strong>
                  <span>{fmtTs(row.ts)}</span>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.82rem', whiteSpace: 'pre-wrap' }}>
                  {stringOf(row.content, stringOf(row.snippet, 'No report content available.'))}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    return (
      <div className="ts-panel">
        <div className="ts-panel-header"><div className="ts-panel-title">Raw Context</div></div>
        <pre style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
          {prettyJson(rawQuery.data)}
        </pre>
      </div>
    );
  };

  return (
    <div style={shellStyle}>
      <div style={panelStyle}>
        <div className="ts-panel-header" style={{ padding: '18px 20px 10px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="ts-panel-title">{symbol} Workflow</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
              Snapshot-first workspace. Deep tabs load only when opened.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {mode === 'modal' && (
              <Link className="ts-refresh-btn" to={`/crypto/symbol/${encodeURIComponent(symbol)}?tab=${activeTab}&range=${rangeKey}`}>
                <ExternalLink size={14} /> Full Page
              </Link>
            )}
            {mode === 'modal' && (
              <button className="ts-refresh-btn" onClick={onClose}>
                <X size={14} /> Close
              </button>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
          {tabButton('overview', 'Overview')}
          {tabButton('market', 'Market')}
          {tabButton('decisions', 'Decisions')}
          {tabButton('trades', 'Trades')}
          {tabButton('learning', 'Learning')}
          {tabButton('brain', 'Brain')}
          {tabButton('news', 'News')}
          {tabButton('reports', 'Reports')}
          {tabButton('raw', 'Raw Context')}
        </div>

        <div style={{ padding: 20, overflowY: 'auto', flex: 1, minHeight: 0 }}>
          {combinedError && (
            <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem', marginBottom: 12 }}>
              {combinedError}
            </div>
          )}
          {renderTab()}
        </div>
      </div>
    </div>
  );
}
