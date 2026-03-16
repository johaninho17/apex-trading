import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Activity, FileText, RefreshCw, Search } from 'lucide-react';
import { Virtuoso } from 'react-virtuoso';
import { useActivityFeedInfiniteQuery, useReportsOverviewQuery } from '../../lib/cryptoQueries';
import type { ActivityItem, ReportItem } from '../../lib/cryptoApi';

function fmtTs(value: number): string {
  return new Date(value).toLocaleString();
}

function gradeLetter(report: ReportItem): string {
  const grade = report.grade as Record<string, unknown> | undefined;
  const letter = grade?.letter;
  return typeof letter === 'string' ? letter : '-';
}

function normalizeTab(value: string | null): 'feed' | 'reports' {
  return value === 'reports' ? 'reports' : 'feed';
}

function parseWindow(windowKey: string): number {
  switch (windowKey) {
    case '1h':
      return 60 * 60 * 1000;
    case '6h':
      return 6 * 60 * 60 * 1000;
    case '7d':
      return 7 * 24 * 60 * 60 * 1000;
    case 'all':
      return Number.POSITIVE_INFINITY;
    case '24h':
    default:
      return 24 * 60 * 60 * 1000;
  }
}

function terminalTone(status: string): string {
  if (status === 'success') return '#34d399';
  if (status === 'blocked') return '#fbbf24';
  if (status === 'error') return '#f87171';
  return '#9fb0cc';
}

function eventFamily(actionType: string): string {
  const normalized = actionType.toLowerCase();
  if (normalized.includes('synthetic_exit') || normalized.includes('flatten') || normalized.includes('close')) return 'exits';
  if (normalized.includes('risk') || normalized.includes('block') || normalized.includes('governor')) return 'risk';
  if (normalized.includes('order') || normalized === 'manual_trade') return 'orders';
  if (normalized.includes('signal') || normalized.includes('candidate')) return 'signals';
  if (normalized.includes('oracle') || normalized.includes('narrative') || normalized.includes('report') || normalized.includes('brain')) return 'intelligence';
  return 'other';
}

export default function CryptoActivity() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = normalizeTab(searchParams.get('view'));
  const statusFilter = searchParams.get('status') || 'all';
  const symbolFilter = searchParams.get('symbol') || '';
  const search = searchParams.get('search') || '';
  const eventTypeFilter = searchParams.get('event') || 'all';
  const timeWindow = searchParams.get('window') || '24h';
  const selectedFeedTs = Number(searchParams.get('selected') || 0);
  const reportType = searchParams.get('reportType') || 'hourly';
  const selectedReportTs = Number(searchParams.get('reportTs') || 0);

  const feedQuery = useActivityFeedInfiniteQuery({
    limit: 100,
    status: statusFilter === 'all' ? '' : statusFilter,
    symbol: symbolFilter,
    search,
  }, activeTab === 'feed');
  const reportsQuery = useReportsOverviewQuery(activeTab === 'reports');
  const feedPages = (feedQuery.data?.pages ?? []) as Array<{ items?: ActivityItem[] }>;

  const updateParams = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (!value) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    });
    setSearchParams(next, { replace: true });
  };

  const feedItems = useMemo<ActivityItem[]>(
    () => feedPages.flatMap((page) => page.items ?? []),
    [feedPages],
  );
  const filteredFeed = useMemo(() => {
    const now = Date.now();
    const windowMs = parseWindow(timeWindow);
    return feedItems.filter((item: ActivityItem) => {
      if (eventTypeFilter !== 'all' && eventFamily(item.action_type) !== eventTypeFilter) return false;
      if (windowMs !== Number.POSITIVE_INFINITY && now - item.ts > windowMs) return false;
      return true;
    });
  }, [eventTypeFilter, feedItems, timeWindow]);

  const selectedFeedItem = filteredFeed.find((item: ActivityItem) => item.ts === selectedFeedTs) ?? filteredFeed[0] ?? null;
  const reports = reportsQuery.data?.reports ?? {};
  const history = reportsQuery.data?.history ?? {};
  const reportHistory = history[reportType] ?? [];
  const activeReport = reportHistory.find((item) => item.ts === selectedReportTs) ?? reports[reportType] ?? reportHistory[0] ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {(feedQuery.error?.message || reportsQuery.error?.message) && (
        <div className="ts-shimmer" style={{ color: 'var(--accent-red)', fontSize: '0.8rem' }}>
          {feedQuery.error?.message || reportsQuery.error?.message}
        </div>
      )}

      <div className="ts-panel" style={{ minHeight: 640 }}>
        <div className="ts-panel-header">
          <div className="ts-panel-title"><Activity size={15} /> Activity & Reports</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className={`ts-tab ${activeTab === 'feed' ? 'active' : ''}`} onClick={() => updateParams({ view: 'feed', reportTs: null })}>
              Live Feed
            </button>
            <button className={`ts-tab ${activeTab === 'reports' ? 'active' : ''}`} onClick={() => updateParams({ view: 'reports', selected: null })}>
              Reports
            </button>
            <button
              className="ts-refresh-btn"
              aria-label={`Refresh ${activeTab === 'feed' ? 'activity feed' : 'reports'}`}
              onClick={() => void (activeTab === 'feed' ? feedQuery.refetch() : reportsQuery.refetch())}
              disabled={feedQuery.isFetching || reportsQuery.isFetching}
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {activeTab === 'feed' ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.85fr', gap: 16, height: '100%' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px' }}>
                  <Search size={13} style={{ color: 'var(--text-muted)' }} />
                  <input
                    value={search}
                    onChange={(event) => updateParams({ search: event.target.value || null })}
                    aria-label="Search activity feed"
                    placeholder="Search actions, symbols, reasons…"
                    style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', minWidth: 220 }}
                  />
                </div>
                <input
                  value={symbolFilter}
                  onChange={(event) => updateParams({ symbol: event.target.value || null })}
                  aria-label="Filter activity by symbol"
                  placeholder="Symbol"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)', padding: '6px 10px', minWidth: 120 }}
                />
                <select
                  value={statusFilter}
                  aria-label="Filter activity by status"
                  onChange={(event) => updateParams({ status: event.target.value === 'all' ? null : event.target.value })}
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)', padding: '6px 10px' }}
                >
                  <option value="all">All statuses</option>
                  <option value="success">Success</option>
                  <option value="blocked">Blocked</option>
                  <option value="error">Error</option>
                  <option value="signal">Signal</option>
                </select>
                <select
                  value={eventTypeFilter}
                  aria-label="Filter activity by event type"
                  onChange={(event) => updateParams({ event: event.target.value === 'all' ? null : event.target.value })}
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)', padding: '6px 10px' }}
                >
                  <option value="all">All event types</option>
                  <option value="signals">Signals</option>
                  <option value="orders">Orders</option>
                  <option value="exits">Exits</option>
                  <option value="intelligence">Intelligence</option>
                  <option value="risk">Risk / Blocks</option>
                </select>
                <select
                  value={timeWindow}
                  aria-label="Filter activity by time window"
                  onChange={(event) => updateParams({ window: event.target.value })}
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)', padding: '6px 10px' }}
                >
                  <option value="1h">1H</option>
                  <option value="6h">6H</option>
                  <option value="24h">24H</option>
                  <option value="7d">7D</option>
                  <option value="all">All</option>
                </select>
              </div>

              <div style={{ border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden', background: 'rgba(7, 11, 19, 0.92)' }}>
                {feedQuery.isLoading && filteredFeed.length === 0 ? (
                  <div className="ts-empty" style={{ minHeight: 400 }}>
                    <p>Loading live crypto activity…</p>
                  </div>
                ) : (
                  <Virtuoso
                    style={{ height: 520 }}
                    data={filteredFeed}
                    itemContent={(_index, item) => {
                      const selected = selectedFeedItem?.ts === item.ts;
                      return (
                        <button
                          onClick={() => updateParams({ selected: String(item.ts) })}
                          style={{
                            width: '100%',
                            textAlign: 'left',
                            padding: '12px 16px',
                            border: 'none',
                            borderBottom: '1px solid rgba(159, 176, 204, 0.12)',
                            background: selected ? 'rgba(45, 212, 191, 0.08)' : 'transparent',
                            color: 'var(--text-primary)',
                            cursor: 'pointer',
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' }}>
                            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                              <strong style={{ color: 'var(--text-primary)' }}>{item.symbol || 'SYS'}</strong>
                              <span style={{ fontFamily: 'monospace' }}>{item.action_type}</span>
                              <span style={{ color: 'var(--text-secondary)' }}>{item.side ?? 'info'}</span>
                              <span style={{ color: 'var(--accent-cyan)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                                {eventFamily(item.action_type)}
                              </span>
                              <span style={{ color: terminalTone(item.status), textTransform: 'uppercase', fontSize: '0.76rem', letterSpacing: '0.08em' }}>
                                {item.status}
                              </span>
                            </div>
                            <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{fmtTs(item.ts)}</span>
                          </div>
                          {item.reason && (
                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.82rem', marginTop: 6 }}>
                              {item.reason}
                            </div>
                          )}
                        </button>
                      );
                    }}
                  />
                )}
              </div>

              {feedQuery.hasNextPage && (
                <button className="ts-refresh-btn" onClick={() => void feedQuery.fetchNextPage()} disabled={feedQuery.isFetchingNextPage}>
                  Load older actions
                </button>
              )}
            </div>

            <div className="ts-panel" style={{ minHeight: 540 }}>
              <div className="ts-panel-header">
                <div className="ts-panel-title"><FileText size={15} /> Selected Event</div>
              </div>
              {!selectedFeedItem ? (
                <div className="ts-empty" style={{ minHeight: 360 }}>
                  <p>No activity row matches the current filters.</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <div className="ts-pulse-card">
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                      <strong>{selectedFeedItem.symbol || 'SYS'} {selectedFeedItem.action_type}</strong>
                      <span style={{ color: terminalTone(selectedFeedItem.status) }}>{selectedFeedItem.status}</span>
                    </div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', marginTop: 6 }}>
                      {fmtTs(selectedFeedItem.ts)}
                    </div>
                  </div>
                  <div className="ts-slm-terminal" style={{ height: 420, maxHeight: 'none' }}>
                    <div className="slm-line slm-ai">reason: {selectedFeedItem.reason || 'n/a'}</div>
                    <div className="slm-line slm-system">side: {selectedFeedItem.side || 'n/a'}</div>
                    <div className="slm-line slm-system">qty: {selectedFeedItem.qty ?? '-'}</div>
                    <div className="slm-line slm-system">notional: {selectedFeedItem.notional ?? '-'}</div>
                    <div className="slm-line slm-system">price: {selectedFeedItem.price ?? '-'}</div>
                    <div className="slm-line slm-ai">payload:</div>
                    <pre style={{ margin: 0, color: '#c9d7f0', fontSize: '0.8rem', whiteSpace: 'pre-wrap' }}>
                      {JSON.stringify(selectedFeedItem.payload ?? {}, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 16, height: '100%' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {['hourly', 'daily', 'weekly', 'monthly'].map((key) => (
                <button
                  key={key}
                  className={`ts-tab ${reportType === key ? 'active' : ''}`}
                  style={{ justifyContent: 'flex-start', padding: '10px 12px' }}
                  onClick={() => updateParams({ reportType: key, reportTs: null })}
                >
                  <FileText size={13} /> {key.toUpperCase()}
                </button>
              ))}

              <div className="ts-panel" style={{ marginTop: 8 }}>
                <div className="ts-panel-header">
                  <div className="ts-panel-title">History</div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {reportHistory.slice(0, 12).map((item, index) => (
                    <button
                      key={`${reportType}-${index}`}
                      className="ts-pulse-card"
                      style={{
                        textAlign: 'left',
                        cursor: 'pointer',
                        color: 'var(--text-primary)',
                        background: 'linear-gradient(180deg, rgba(17, 22, 36, 0.98) 0%, rgba(10, 13, 22, 0.98) 100%)',
                        borderColor: 'rgba(159, 176, 204, 0.16)',
                      }}
                      onClick={() => updateParams({ reportTs: String(item.ts) })}
                    >
                      <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{fmtTs(item.ts)}</div>
                      <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>{gradeLetter(item)} grade</div>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="ts-panel">
              <div className="ts-panel-header">
                <div className="ts-panel-title">{reportType.toUpperCase()} Report</div>
              </div>
              {!activeReport ? (
                <div className="ts-empty" style={{ minHeight: 360 }}>
                  <p>No {reportType} report available.</p>
                </div>
              ) : (
                <div
                  style={{
                    whiteSpace: 'pre-wrap',
                    color: '#d7e2f5',
                    lineHeight: 1.72,
                    fontSize: '0.92rem',
                    padding: '4px 2px 12px 2px',
                  }}
                >
                  {activeReport.content}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
