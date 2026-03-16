import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { BrainCircuit, Gauge, Play, Power, RefreshCw, ScanSearch, Settings2, Sparkles } from 'lucide-react';
import CryptoSymbolInspector from '../../components/crypto/CryptoSymbolInspector';
import LightweightTimeSeriesChart from '../../components/charts/LightweightTimeSeriesChart';
import { postBotAction, updateTrackedCoinSaved, type PositionItem, type RangeKey, type ScannerItem, type TrackedCoinItem } from '../../lib/cryptoApi';
import {
  invalidateTerminalShell,
  prefetchSymbolSnapshot,
  useAssetCatalogQuery,
  useEquityHistoryQuery,
  useScannerQuery,
  useTerminalSummaryQuery,
  useTrackedCoinsQuery,
} from '../../lib/cryptoQueries';

const RANGE_OPTIONS: RangeKey[] = ['1H', '1D', '7D', '30D', '90D', 'ALL'];

function numberOf(value: unknown, fallback = 0): number {
  return typeof value === 'number' ? value : Number(value ?? fallback) || fallback;
}

function stringOf(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function fmtMoney(value: unknown, digits = 2): string {
  return numberOf(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPlain(value: unknown, digits = 1): string {
  return numberOf(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatAgeFromMs(value: unknown): string {
  const ts = numberOf(value, 0);
  if (!ts) return 'n/a';
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  return formatAgeFromSeconds(diffSec);
}

function formatAgeFromSeconds(value: unknown): string {
  const diffSec = Math.max(0, Math.floor(numberOf(value, 0)));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
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

function biasTone(value: unknown): string {
  const normalized = stringOf(value, 'neutral').toLowerCase();
  if (normalized.includes('bull')) return 'var(--accent-green)';
  if (normalized.includes('bear')) return 'var(--accent-red)';
  if (normalized.includes('risk')) return 'var(--accent-orange)';
  return 'var(--text-secondary)';
}

function modeTone(value: unknown): string {
  const normalized = stringOf(value, '').toLowerCase();
  if (normalized.includes('normal') || normalized.includes('live') || normalized.includes('online')) return 'var(--accent-green)';
  if (normalized.includes('warm') || normalized.includes('paper')) return 'var(--accent-orange)';
  if (normalized.includes('halt') || normalized.includes('block') || normalized.includes('error')) return 'var(--accent-red)';
  return 'var(--accent-cyan)';
}

function StatusChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        gap: 6,
        minWidth: 0,
        padding: '10px 12px',
        borderRadius: 14,
        border: `1px solid color-mix(in srgb, ${tone} 18%, rgba(159, 176, 204, 0.16))`,
        background: `linear-gradient(180deg, color-mix(in srgb, ${tone} 10%, rgba(14, 18, 29, 0.98)) 0%, rgba(10, 13, 23, 0.98) 100%)`,
        color: 'var(--text-primary)',
        lineHeight: 1.1,
        fontVariantNumeric: 'tabular-nums',
        boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.02)',
      }}
    >
      <span style={{ color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.64rem' }}>
        {label}
      </span>
      <span style={{ color: tone, fontWeight: 700, fontSize: '0.92rem', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </div>
  );
}

function normalizeScannerSymbol(value: string): string {
  const raw = value.trim().toUpperCase();
  if (!raw) return '';
  if (raw.includes('/')) return raw;
  if (raw.endsWith('USD') && raw.length > 3) {
    return `${raw.slice(0, -3)}/USD`;
  }
  return `${raw}/USD`;
}

function freshnessTone(status?: string): string {
  switch (String(status || '').toLowerCase()) {
    case 'fresh':
      return '#2dd4bf';
    case 'stale':
      return '#fbbf24';
    case 'error':
      return '#f87171';
    default:
      return '#93a4c3';
  }
}

function FreshnessPill({ label, status }: { label: string; status?: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 10px',
        borderRadius: 999,
        border: '1px solid rgba(159, 176, 204, 0.14)',
        color: freshnessTone(status),
        background: 'rgba(11, 15, 26, 0.72)',
        fontSize: '0.74rem',
        textTransform: 'uppercase',
        letterSpacing: '0.08em',
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: freshnessTone(status) }} />
      {label}
    </span>
  );
}

function OpenPositionCard({
  position,
  onOpen,
  onWarm,
}: {
  position: PositionItem;
  onOpen: (symbol: string) => void;
  onWarm: (symbol: string) => void;
}) {
  const symbol = stringOf(position.symbol);
  const unrealized = numberOf(position.unrealized_pl);
  const tone = unrealized >= 0 ? '#34d399' : '#f87171';

  return (
    <button
      className="ts-pulse-card"
      style={{
        textAlign: 'left',
        cursor: 'pointer',
        color: 'var(--text-primary)',
        background: 'linear-gradient(180deg, rgba(19, 25, 39, 0.95) 0%, rgba(12, 16, 27, 0.95) 100%)',
        borderColor: 'rgba(159, 176, 204, 0.16)',
      }}
      onMouseEnter={() => onWarm(symbol)}
      onFocus={() => onWarm(symbol)}
      onClick={() => onOpen(symbol)}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
        <strong style={{ color: 'var(--text-primary)', fontSize: '0.96rem' }}>{symbol}</strong>
        <span style={{ color: tone, fontWeight: 700 }}>{unrealized >= 0 ? '+' : ''}${fmtMoney(unrealized)}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
        <div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Entry</div>
          <div style={{ color: 'var(--text-primary)', fontSize: '0.88rem' }}>${fmtMoney(position.avg_entry_price, 4)}</div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Market Value</div>
          <div style={{ color: 'var(--text-primary)', fontSize: '0.88rem' }}>${fmtMoney(position.market_value)}</div>
        </div>
      </div>
    </button>
  );
}

export default function CryptoDashboard() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState('');
  const [rangeKey, setRangeKey] = useState<RangeKey>('1D');
  const [scannerDraft, setScannerDraft] = useState('');
  const [scannerSaving, setScannerSaving] = useState(false);
  const queryClient = useQueryClient();

  const summaryQuery = useTerminalSummaryQuery(true, true);
  const scannerQuery = useScannerQuery(10, true);
  const trackedQuery = useTrackedCoinsQuery(80, true);
  const assetQuery = useAssetCatalogQuery(scannerDraft.trim(), 40, true, true);
  const equityQuery = useEquityHistoryQuery(rangeKey, true);

  const summary = summaryQuery.data;
  const scanner = scannerQuery.data?.items ?? [];
  const positions = Array.isArray(summary?.positions) ? summary.positions : [];
  const account = (summary?.account ?? {}) as Record<string, unknown>;
  const positionsSummary = (summary?.positions_summary ?? {}) as Record<string, unknown>;
  const botStatus = (summary?.bot_status ?? {}) as Record<string, unknown>;
  const runtime = (botStatus.runtime ?? {}) as Record<string, unknown>;
  const activityGovernor = (runtime.activity_governor ?? {}) as Record<string, unknown>;
  const brainScore = (summary?.brain_self_score ?? {}) as Record<string, unknown>;
  const oracleSummary = (summary?.oracle_summary ?? {}) as Record<string, unknown>;
  const oracleStatus = (oracleSummary.oracle ?? {}) as Record<string, unknown>;
  const oracleDerived = (oracleSummary.derived_state ?? {}) as Record<string, unknown>;
  const botConfig = (summary?.bot_config_summary ?? {}) as Record<string, unknown>;
  const activitySummary = (summary?.activity_summary ?? {}) as Record<string, unknown>;
  const latestAction = (activitySummary.latest_action ?? {}) as Record<string, unknown>;
  const isRunning = Boolean((runtime.running ?? (botStatus.persisted as Record<string, unknown> | undefined)?.running) ?? false);
  const openSlots = Math.max(0, numberOf(botConfig.max_open_positions, positions.length) - positions.length);
  const netUnrealized = numberOf(positionsSummary.net_unrealized);
  const trackedItems = trackedQuery.data?.items ?? [];
  const savedTracked = trackedItems.filter((item) => item.saved);
  const activeTracked = trackedItems.filter((item) => String(item.active_state || '') === 'active');
  const recentTracked = trackedItems.filter((item) => String(item.active_state || '') === 'recently_active');
  const latestActionLabel = stringOf(latestAction.symbol)
    ? `${stringOf(latestAction.action_type, 'action')} ${stringOf(latestAction.symbol)}`
    : stringOf(latestAction.action_type, 'No recent action');

  const shellError = summaryQuery.error?.message || scannerQuery.error?.message || trackedQuery.error?.message || equityQuery.error?.message || '';
  const chartData = useMemo(
    () => (equityQuery.data?.history ?? []).map((point) => ({ ts: numberOf(point.ts), value: numberOf(point.equity) })),
    [equityQuery.data?.history],
  );
  const scannerSuggestions = useMemo(() => {
    return Array.from(
      new Set(
        [
          ...trackedItems.map((item) => item.symbol),
          ...scanner.map((item) => item.symbol),
          ...((assetQuery.data?.items ?? []).map((item) => item.symbol)),
        ]
          .map((entry) => normalizeScannerSymbol(entry))
          .filter(Boolean),
      ),
    ).slice(0, 40);
  }, [assetQuery.data?.items, scanner, trackedItems]);

  const refreshAll = async () => {
    await invalidateTerminalShell(queryClient);
    await Promise.all([
      summaryQuery.refetch(),
      scannerQuery.refetch(),
      trackedQuery.refetch(),
      equityQuery.refetch(),
    ]);
  };

  const warmSymbol = (symbol: string) => {
    void prefetchSymbolSnapshot(queryClient, symbol);
  };

  const runAction = async (action: 'start' | 'stop' | 'train' | 'finetune' | 'flatten') => {
    setBusyAction(action);
    try {
      await postBotAction(action);
      await refreshAll();
    } finally {
      setBusyAction('');
    }
  };

  const saveTrackedSymbol = async (symbol: string, saved: boolean) => {
    setScannerSaving(true);
    try {
      await updateTrackedCoinSaved(symbol, saved);
      setScannerDraft('');
      await refreshAll();
    } finally {
      setScannerSaving(false);
    }
  };

  const addScannerSymbol = async () => {
    const normalized = normalizeScannerSymbol(scannerDraft);
    if (!normalized || savedTracked.some((item) => item.symbol === normalized)) {
      setScannerDraft('');
      return;
    }
    await saveTrackedSymbol(normalized, true);
  };

  const removeScannerSymbol = async (symbol: string) => {
    await saveTrackedSymbol(symbol, false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {shellError && (
        <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem' }}>
          {shellError}
        </div>
      )}

      <div className="ts-panel">
        <div className="ts-panel-header" style={{ paddingBottom: 10 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="ts-panel-title"><Sparkles size={15} /> Crypto Command Deck</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <FreshnessPill label={`Shell ${summary?.cache_state ?? 'warming'}`} status={summary?.status} />
              <FreshnessPill label={`Scanner ${scannerQuery.data?.cache_state ?? 'warming'}`} status={scannerQuery.data?.status} />
              <FreshnessPill label={`Chart ${equityQuery.data?.cache_state ?? 'warming'}`} status={equityQuery.data?.status} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="ts-refresh-btn" onClick={() => void refreshAll()} disabled={summaryQuery.isFetching || scannerQuery.isFetching || equityQuery.isFetching}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button className={`ts-bot-toggle ${isRunning ? 'stop' : 'start'}`} onClick={() => void runAction(isRunning ? 'stop' : 'start')} disabled={busyAction !== ''}>
              {isRunning ? <><Power size={14} /> Stop Bot</> : <><Play size={14} /> Start Bot</>}
            </button>
            <button className="ts-refresh-btn" onClick={() => void runAction('train')} disabled={busyAction !== ''}>Train AI</button>
            <button className="ts-refresh-btn" onClick={() => void runAction('finetune')} disabled={busyAction !== ''}>Fine-Tune</button>
            <button className="ts-refresh-btn" onClick={() => void runAction('flatten')} disabled={busyAction !== ''}>Flatten</button>
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 12,
            paddingTop: 12,
            borderTop: '1px solid rgba(159, 176, 204, 0.12)',
            marginTop: 12,
          }}
        >
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Equity</span>
            <span className="pulse-value">${fmtMoney(account.equity)}</span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Cash</span>
            <span className="pulse-value" style={{ color: 'var(--accent-cyan)' }}>${fmtMoney(account.cash)}</span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Exposure</span>
            <span className="pulse-value" style={{ color: 'var(--accent-blue)' }}>${fmtMoney(positionsSummary.total_exposure, 0)}</span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Unrealized</span>
            <span className="pulse-value" style={{ color: positiveNegativeTone(netUnrealized) }}>
              {netUnrealized >= 0 ? '+' : ''}${fmtMoney(netUnrealized)}
            </span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Brain Self-Score</span>
            <span className="pulse-value" style={{ color: scoreTone(brainScore.score) }}>{fmtPlain(brainScore.score, 1)}</span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Governor</span>
            <span className="pulse-value" style={{ color: modeTone(activityGovernor.mode) }}>
              {stringOf(activityGovernor.mode, 'normal').toUpperCase()}
            </span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Oracle Regime</span>
            <span className="pulse-value" style={{ color: biasTone(oracleDerived.market_regime || oracleSummary.market_regime) }}>
              {stringOf(oracleDerived.market_regime || oracleSummary.market_regime, 'normal').toUpperCase()}
            </span>
          </div>
          <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '12px 14px', minWidth: 0 }}>
            <span className="pulse-label">Tracked</span>
            <span className="pulse-value" style={{ color: 'var(--accent-cyan)' }}>{fmtPlain(trackedItems.length, 0)}</span>
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 10,
            paddingTop: 12,
            borderTop: '1px solid rgba(159, 176, 204, 0.12)',
            marginTop: 12,
          }}
        >
          <StatusChip label="Recent Actions" value={String(numberOf(activitySummary.recent_count, 0))} tone="var(--accent-green)" />
          <StatusChip label="Latest" value={latestActionLabel} tone="var(--accent-blue)" />
          <StatusChip label="Blocked/Error" value={String(numberOf(activitySummary.blocked_or_error_count, 0))} tone="var(--accent-orange)" />
          <StatusChip label="Open Positions" value={String(positions.length)} tone="var(--accent-green)" />
          <StatusChip label="Capacity Left" value={String(openSlots)} tone={openSlots > 0 ? 'var(--accent-blue)' : 'var(--accent-red)'} />
          <StatusChip label="Oracle Age" value={formatAgeFromSeconds(oracleStatus.age_sec)} tone="var(--accent-orange)" />
          <StatusChip label="DRL" value={formatAgeFromMs(brainScore.ts)} tone="var(--accent-cyan)" />
          <StatusChip label="Bot" value={isRunning ? 'Live' : 'Idle'} tone={isRunning ? 'var(--accent-green)' : 'var(--text-secondary)'} />
        </div>
      </div>

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div className="ts-panel-title"><Gauge size={15} /> Equity & Performance</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option}
                className={`ts-tab ${rangeKey === option ? 'active' : ''}`}
                style={{ minWidth: 48 }}
                onClick={() => setRangeKey(option)}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <LightweightTimeSeriesChart
          data={chartData}
          rangeKey={rangeKey}
          height={360}
          lineColor="#2dd4bf"
          emptyLabel={
            equityQuery.isLoading
              ? 'Loading performance graph...'
              : equityQuery.data?.status === 'partial'
                ? 'Performance graph is warming up with partial account history.'
                : 'Waiting for more equity history...'
          }
          valueFormatter={(value) => `$${fmtMoney(value)}`}
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', color: 'var(--text-secondary)', fontSize: '0.78rem', padding: '0 16px 16px 16px' }}>
          <span>
            {stringOf(equityQuery.data?.status, 'partial').toUpperCase()} chart state
            {equityQuery.data?.cache_state ? ` · ${stringOf(equityQuery.data.cache_state).toUpperCase()} cache` : ''}
          </span>
          <span>
            {fmtPlain(equityQuery.data?.point_count ?? chartData.length, 0)} plotted
            {numberOf(equityQuery.data?.point_count_raw, chartData.length) > numberOf(equityQuery.data?.point_count, chartData.length)
              ? ` from ${fmtPlain(equityQuery.data?.point_count_raw, 0)} raw points`
              : ' points'}
          </span>
          <span>
            {equityQuery.data?.status === 'stale'
              ? 'Using the latest cached equity curve while the backend refreshes.'
              : equityQuery.data?.status === 'partial'
                ? 'Partial history stays visible instead of timing out while the shell warms.'
                : 'Equity history is local-first and range-aware.'}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16 }}>
        <div className="ts-panel">
          <div className="ts-panel-header">
            <div className="ts-panel-title"><Settings2 size={15} /> Bot Configuration</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
              Runtime controls, risk limits, and discovery configuration.
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Brain</span><span className="pulse-value">{stringOf(botConfig.active_brain || 'drl_event_fusion')}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Runtime</span><span className="pulse-value" style={{ color: modeTone(isRunning ? 'live' : 'idle') }}>{isRunning ? 'LIVE' : 'IDLE'}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Mode</span><span className="pulse-value" style={{ color: modeTone(botConfig.trading_mode) }}>{stringOf(botConfig.trading_mode || 'offline').toUpperCase()}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Account</span><span className="pulse-value" style={{ color: modeTone(botConfig.account_mode) }}>{stringOf(botConfig.account_mode || 'paper').toUpperCase()}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Governor</span><span className="pulse-value" style={{ color: modeTone(botConfig.governor_mode) }}>{stringOf(botConfig.governor_mode || 'normal').toUpperCase()}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Risk State</span><span className="pulse-value" style={{ color: modeTone(botConfig.risk_state) }}>{stringOf(botConfig.risk_state || 'normal').toUpperCase()}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Feedback</span><span className="pulse-value">{Array.isArray(botConfig.feedback_horizons_min) ? `${(botConfig.feedback_horizons_min as number[]).join(' / ')}m` : '0 windows'}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Poll Interval</span><span className="pulse-value">{fmtPlain(botConfig.poll_interval_sec, 0)}s</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Cooldown</span><span className="pulse-value">{fmtPlain(botConfig.cooldown_sec, 0)}s</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Anti-Spam</span><span className="pulse-value">{fmtPlain(botConfig.anti_spam_sec, 0)}s</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Max Open</span><span className="pulse-value">{fmtPlain(botConfig.max_open_positions, 0)}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Max Exposure</span><span className="pulse-value">${fmtMoney(botConfig.max_total_exposure, 0)}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Max Per Trade</span><span className="pulse-value">${fmtMoney(botConfig.max_notional_per_trade, 0)}</span></div>
            <div className="ts-pulse-card" style={{ display: 'grid', gap: 6, padding: '10px 12px' }}><span className="pulse-label">Universe Refresh</span><span className="pulse-value">{fmtPlain(botConfig.universe_refresh_sec, 0)}s</span></div>
          </div>
        </div>

        <div className="ts-panel">
          <div className="ts-panel-header">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div className="ts-panel-title"><ScanSearch size={15} /> Market Scanner</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
                Active opportunities stay in the main scanner while tracked coins keep saved and recently-active names visible between promotions.
              </div>
            </div>
          </div>
          <div style={{ display: 'grid', gap: 12, padding: '0 16px 12px 16px' }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input
                list="crypto-scanner-suggestions"
                aria-label="Add custom market scanner symbol"
                value={scannerDraft}
                onChange={(event) => setScannerDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void addScannerSymbol();
                  }
                }}
                placeholder="Add symbol like HYPE/USD"
                style={{
                  flex: '1 1 220px',
                  minWidth: 0,
                  padding: '10px 12px',
                  borderRadius: 12,
                  border: '1px solid rgba(159, 176, 204, 0.16)',
                  background: 'rgba(10, 13, 23, 0.96)',
                  color: 'var(--text-primary)',
                }}
              />
              <datalist id="crypto-scanner-suggestions">
                {scannerSuggestions.map((entry) => (
                  <option key={entry} value={entry} />
                ))}
              </datalist>
              <button className="ts-refresh-btn" onClick={() => void addScannerSymbol()} disabled={scannerSaving || !scannerDraft.trim()}>
                Save Coin
              </button>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {activeTracked.length > 0 && (
                <div style={{ display: 'grid', gap: 6 }}>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Active Now</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {activeTracked.slice(0, 10).map((entry: TrackedCoinItem) => (
                      <button
                        key={`active-${entry.symbol}`}
                        type="button"
                        onClick={() => setSelectedSymbol(entry.symbol)}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 10px',
                          borderRadius: 999,
                          border: '1px solid rgba(52, 211, 153, 0.22)',
                          background: 'rgba(13, 28, 23, 0.94)',
                          color: 'var(--accent-green)',
                          fontSize: '0.74rem',
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        {entry.symbol}
                        <span style={{ color: 'var(--text-secondary)' }}>{stringOf(entry.active_reason, 'active')}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {recentTracked.length > 0 && (
                <div style={{ display: 'grid', gap: 6 }}>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Recently Active</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {recentTracked.slice(0, 10).map((entry: TrackedCoinItem) => (
                      <button
                        key={`recent-${entry.symbol}`}
                        type="button"
                        onClick={() => setSelectedSymbol(entry.symbol)}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 10px',
                          borderRadius: 999,
                          border: '1px solid rgba(251, 191, 36, 0.22)',
                          background: 'rgba(32, 22, 8, 0.94)',
                          color: 'var(--accent-orange)',
                          fontSize: '0.74rem',
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        {entry.symbol}
                        <span style={{ color: 'var(--text-secondary)' }}>{entry.has_position ? 'held/follow-up' : 'follow-up'}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {savedTracked.length > 0 && (
              <div style={{ display: 'grid', gap: 6 }}>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Saved</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {savedTracked.map((entry: TrackedCoinItem) => (
                  <button
                    key={entry.symbol}
                    type="button"
                    onClick={() => void removeScannerSymbol(entry.symbol)}
                    disabled={scannerSaving}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '6px 10px',
                      borderRadius: 999,
                      border: '1px solid rgba(64, 224, 208, 0.24)',
                      background: 'rgba(14, 25, 30, 0.94)',
                      color: 'var(--accent-cyan)',
                      fontSize: '0.74rem',
                      fontWeight: 700,
                      cursor: 'pointer',
                    }}
                  >
                    {entry.symbol}
                    <span style={{ color: 'var(--text-secondary)' }}>remove</span>
                  </button>
                ))}
                </div>
              </div>
            )}
            <div className="ts-results-wrap">
              {scanner.length === 0 && scannerQuery.isLoading ? (
                <div className="ts-empty" style={{ minHeight: 220 }}>
                  <p>Loading actionable scanner shortlist...</p>
                </div>
              ) : (
                <table className="ts-results-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Price</th>
                      <th>Conviction</th>
                      <th>Bias</th>
                      <th>Overlay</th>
                      <th>Blocked 24H</th>
                      <th>Inspect</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scanner.map((item: ScannerItem) => (
                      <tr
                        key={item.symbol}
                        onMouseEnter={() => warmSymbol(item.symbol)}
                        onFocus={() => warmSymbol(item.symbol)}
                      >
                        <td style={{ color: 'var(--text-primary)', fontWeight: 700 }}>
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span>{item.symbol}</span>
                            {item.active_state === 'active' && (
                              <span
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  padding: '3px 8px',
                                  borderRadius: 999,
                                  background: 'rgba(52, 211, 153, 0.14)',
                                  color: 'var(--accent-green)',
                                  fontSize: '0.68rem',
                                  fontWeight: 700,
                                  letterSpacing: '0.04em',
                                  textTransform: 'uppercase',
                                }}
                              >
                                Active
                              </span>
                            )}
                            {item.saved && (
                              <span
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  padding: '3px 8px',
                                  borderRadius: 999,
                                  background: 'rgba(45, 212, 191, 0.14)',
                                  color: 'var(--accent-cyan)',
                                  fontSize: '0.68rem',
                                  fontWeight: 700,
                                  letterSpacing: '0.04em',
                                  textTransform: 'uppercase',
                                }}
                              >
                                Saved
                              </span>
                            )}
                            {item.news_context_state === 'unavailable' && (
                              <span
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  padding: '3px 8px',
                                  borderRadius: 999,
                                  background: 'rgba(251, 191, 36, 0.14)',
                                  color: 'var(--accent-orange)',
                                  fontSize: '0.68rem',
                                  fontWeight: 700,
                                  letterSpacing: '0.04em',
                                  textTransform: 'uppercase',
                                }}
                              >
                                News Unavail
                              </span>
                            )}
                          </div>
                        </td>
                        <td style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>${fmtMoney(item.price, 4)}</td>
                        <td style={{ color: scoreTone(item.conviction_score), fontVariantNumeric: 'tabular-nums' }}>{fmtPlain(item.conviction_score, 1)}</td>
                        <td style={{ color: biasTone(item.event_bias) }}>{item.event_bias.toUpperCase()}</td>
                        <td style={{ color: positiveNegativeTone(item.overlay?.score_delta ?? 0), fontVariantNumeric: 'tabular-nums' }}>{fmtPlain(item.overlay?.score_delta ?? 0, 1)}</td>
                        <td style={{ color: numberOf(item.blocked_trades_24h, 0) > 0 ? 'var(--accent-orange)' : 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{item.blocked_trades_24h}</td>
                        <td>
                          <button className="ts-refresh-btn" onClick={() => setSelectedSymbol(item.symbol)}>
                            Open
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="ts-panel">
        <div className="ts-panel-header">
          <div className="ts-panel-title"><BrainCircuit size={15} /> Open Positions</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
            High-contrast cards for active crypto exposure.
          </div>
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
            gap: 12,
            padding: 16,
          }}
        >
          {positions.length === 0 ? (
            <div className="ts-empty" style={{ minHeight: 240, gridColumn: '1 / -1' }}>
              <p>No active crypto positions.</p>
            </div>
          ) : (
            positions.map((position) => (
              <OpenPositionCard
                key={position.symbol}
                position={position}
                onOpen={setSelectedSymbol}
                onWarm={warmSymbol}
              />
            ))
          )}
        </div>
      </div>

      {selectedSymbol && (
        <CryptoSymbolInspector symbol={selectedSymbol} mode="modal" onClose={() => setSelectedSymbol(null)} />
      )}
    </div>
  );
}
