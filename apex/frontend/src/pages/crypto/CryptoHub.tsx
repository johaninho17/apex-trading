import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Radar, RefreshCw } from 'lucide-react';
import CryptoSymbolInspector from '../../components/crypto/CryptoSymbolInspector';
import { prefetchSymbolSnapshot, useUniverseQuery } from '../../lib/cryptoQueries';

function fmtNum(value: number, digits = 2): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function scoreTone(value: number): string {
  if (value >= 80) return 'var(--accent-green)';
  if (value >= 60) return 'var(--accent-cyan)';
  if (value >= 40) return 'var(--accent-orange)';
  return 'var(--accent-red)';
}

function positiveNegativeTone(value: number): string {
  if (value > 0) return 'var(--accent-green)';
  if (value < 0) return 'var(--accent-red)';
  return 'var(--text-primary)';
}

function biasTone(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized.includes('bull')) return 'var(--accent-green)';
  if (normalized.includes('bear')) return 'var(--accent-red)';
  if (normalized.includes('risk')) return 'var(--accent-orange)';
  return 'var(--text-secondary)';
}

export default function CryptoHub() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'selected' | 'event'>('all');
  const [sortBy, setSortBy] = useState<'top30' | 'conviction' | 'learned_edge'>('top30');
  const queryClient = useQueryClient();
  const universeQuery = useUniverseQuery(30, true);
  const items = universeQuery.data?.items ?? [];

  const rows = useMemo(() => {
    const filtered = filter === 'all'
      ? items
      : filter === 'event'
        ? items.filter((item) => item.active_narrative || Math.abs(item.event_score) >= 15)
        : items.filter((item) => item.selected);
    if (sortBy === 'conviction') {
      return [...filtered].sort((left, right) => right.conviction_score - left.conviction_score);
    }
    if (sortBy === 'learned_edge') {
      return [...filtered].sort(
        (left, right) => (right.overlay?.score_delta ?? right.overlay_score) - (left.overlay?.score_delta ?? left.overlay_score),
      );
    }
    return filtered;
  }, [filter, items, sortBy]);

  const warmSymbol = (symbol: string) => {
    void prefetchSymbolSnapshot(queryClient, symbol);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {universeQuery.error?.message && (
        <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem' }}>
          {universeQuery.error.message}
        </div>
      )}

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="ts-panel-title"><Radar size={15} /> Top-30 Crypto Universe</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
              Hub stays broad; the scanner on Dashboard stays tactical.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className={`ts-tab ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>All</button>
            <button className={`ts-tab ${filter === 'selected' ? 'active' : ''}`} onClick={() => setFilter('selected')}>Tracked</button>
            <button className={`ts-tab ${filter === 'event' ? 'active' : ''}`} onClick={() => setFilter('event')}>Event Driven</button>
            <select
              value={sortBy}
              aria-label="Sort crypto hub universe"
              onChange={(event) => setSortBy(event.target.value as 'top30' | 'conviction' | 'learned_edge')}
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)', padding: '6px 10px' }}
            >
              <option value="top30">Top 30</option>
              <option value="conviction">Conviction</option>
              <option value="learned_edge">Learned Edge</option>
            </select>
            <button
              className="ts-refresh-btn"
              aria-label="Refresh crypto universe"
              onClick={() => void universeQuery.refetch()}
              disabled={universeQuery.isFetching}
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        <div className="ts-results-wrap">
          {rows.length === 0 && universeQuery.isLoading ? (
            <div className="ts-empty" style={{ minHeight: 280 }}>
              <p>Loading top-30 crypto universe…</p>
            </div>
          ) : (
            <table className="ts-results-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Bucket</th>
                  <th>Conviction</th>
                  <th>Learned Edge</th>
                  <th>Event Bias</th>
                  <th>Blocked 24H</th>
                  <th>Freshness</th>
                  <th>Active Narrative</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr
                    key={item.symbol}
                    tabIndex={0}
                    onMouseEnter={() => warmSymbol(item.symbol)}
                    onFocus={() => warmSymbol(item.symbol)}
                    onClick={() => setSelectedSymbol(item.symbol)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedSymbol(item.symbol);
                      }
                    }}
                    style={{ cursor: 'pointer' }}
                  >
                    <td style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{item.symbol}</td>
                    <td>{item.bucket}</td>
                    <td style={{ color: scoreTone(item.conviction_score), fontVariantNumeric: 'tabular-nums' }}>{fmtNum(item.conviction_score, 1)}</td>
                    <td style={{ color: positiveNegativeTone(item.overlay?.score_delta ?? item.overlay_score), fontVariantNumeric: 'tabular-nums' }}>{fmtNum(item.overlay?.score_delta ?? item.overlay_score, 1)}</td>
                    <td style={{ color: biasTone(item.event_bias) }}>{item.event_bias.toUpperCase()}</td>
                    <td style={{ color: item.blocked_trades_24h > 0 ? 'var(--accent-orange)' : 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{item.blocked_trades_24h}</td>
                    <td>{item.freshness}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{item.active_narrative || 'No active narrative'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.76rem', padding: '12px 16px 16px 16px', lineHeight: 1.5 }}>
          Bucket shows how a coin entered the ranked universe: held, event override, core, or discovery. Conviction is the total rank score and typically ranges from roughly -20 to 120 after learned edge is applied. Learned edge is the overlay score delta and is bounded near -20 to +20. Freshness reflects whether the current universe snapshot is fresh, stale, or still warming.
        </div>
      </div>

      {selectedSymbol && (
        <CryptoSymbolInspector symbol={selectedSymbol} mode="modal" onClose={() => setSelectedSymbol(null)} />
      )}
    </div>
  );
}
