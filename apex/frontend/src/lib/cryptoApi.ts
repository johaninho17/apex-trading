import { fetchJson } from './api';

export type ApiStatus = 'fresh' | 'stale' | 'partial' | 'error' | string;
export type CacheState = 'hot' | 'stale' | 'warming' | 'error' | string;
export type RangeKey = '1H' | '1D' | '7D' | '30D' | '90D' | 'ALL';

export interface ApiMeta {
  status: ApiStatus;
  cache_state?: CacheState;
  snapshot_ts?: number;
  last_updated_ms?: number;
}

export interface BrainScorePoint {
  ts: number;
  score: number;
  confidence?: number;
  discipline?: number;
  pnl_quality?: number;
  blocking_quality?: number;
  freshness?: number;
}

export interface OverlayRecord {
  symbol: string;
  score_delta: number;
  size_multiplier: number;
  cooldown_multiplier: number;
  confidence_boost: number;
  veto_tightness: number;
  decision_samples: number;
  updated_at?: number;
}

export interface ScannerItem {
  symbol: string;
  price: number;
  conviction_score: number;
  edge_weight: number;
  event_bias: string;
  event_score: number;
  active_narrative: string;
  blocked_trades_24h: number;
  has_position: boolean;
  position_unrealized: number;
  freshness?: string;
  saved?: boolean;
  watchlisted?: boolean;
  active_state?: string;
  active_reason?: string;
  monitor_tier?: string;
  news_context_state?: string;
  overlay?: OverlayRecord;
}

export interface UniverseItem {
  symbol: string;
  bucket: string;
  selected: boolean;
  conviction_score: number;
  liquidity_score: number;
  event_score: number;
  historical_edge_score: number;
  overlay_score: number;
  event_bias: string;
  active_narrative: string;
  blocked_trades_24h: number;
  freshness: string;
  saved?: boolean;
  active_state?: string;
  active_reason?: string;
  monitor_tier?: string;
  news_context_state?: string;
  overlay?: OverlayRecord;
}

export interface TrackedCoinItem {
  symbol: string;
  saved: boolean;
  active_state: string;
  active_reason?: string;
  monitor_tier?: string;
  active_since?: number;
  active_min_until?: number;
  recent_until?: number;
  last_rank_score?: number;
  price?: number;
  conviction_score?: number;
  event_bias?: string;
  news_context_state?: string;
  has_position?: boolean;
}

export interface AssetCatalogItem {
  symbol: string;
  name: string;
  tradable: boolean;
  status: string;
  saved?: boolean;
  active_state?: string;
  active_reason?: string;
  monitor_tier?: string;
  news_context_state?: string;
}

export interface ActivityItem {
  id?: number;
  ts: number;
  action_type: string;
  symbol?: string;
  side?: string;
  qty?: number;
  notional?: number;
  price?: number;
  status: string;
  reason?: string;
  payload?: Record<string, unknown>;
}

export interface ReportItem {
  ts: number;
  report_type: string;
  content: string;
  grade?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export interface PositionItem {
  symbol: string;
  qty?: number;
  avg_entry_price?: number;
  market_value?: number;
  unrealized_pl?: number;
  unrealized_plpc?: number;
  current_price?: number;
}

export interface BotConfigSummary {
  active_brain?: string;
  trading_mode?: string;
  account_mode?: string;
  enabled?: boolean;
  runtime_running?: boolean;
  governor_mode?: string;
  risk_state?: string;
  risk_source?: string;
  feedback_horizons_min?: number[];
  live_retrain_interval_days?: number;
  universe_refresh_sec?: number;
  poll_interval_sec?: number;
  cooldown_sec?: number;
  anti_spam_sec?: number;
  max_open_positions?: number;
  max_total_exposure?: number;
  max_notional_per_trade?: number;
  auto_discover_pairs?: boolean;
  tracked_symbol_count?: number;
  tracked_symbols_preview?: string[];
  tracked_state_counts?: Record<string, number>;
}

export interface TerminalSummary extends ApiMeta {
  account?: Record<string, unknown>;
  positions?: PositionItem[];
  positions_summary?: Record<string, unknown>;
  config_summary?: Record<string, unknown>;
  bot_config_summary?: BotConfigSummary;
  bot_status?: Record<string, unknown>;
  oracle_summary?: Record<string, unknown>;
  brain_self_score?: BrainScorePoint;
  activity_summary?: Record<string, unknown>;
  reports?: Record<string, ReportItem | null>;
  universe?: Record<string, unknown>;
  services?: Record<string, unknown>;
}

export interface EquityPoint {
  ts: number;
  equity: number;
}

export interface EquityHistoryResponse extends ApiMeta {
  history: EquityPoint[];
  point_count?: number;
  point_count_raw?: number;
  range_since_ms?: number;
}

export interface ScannerResponse extends ApiMeta {
  items: ScannerItem[];
}

export interface UniverseResponse extends ApiMeta {
  items: UniverseItem[];
  selected_symbols?: string[];
  promoted_symbols?: string[];
  recently_active_symbols?: string[];
}

export interface TrackedCoinsResponse extends ApiMeta {
  items: TrackedCoinItem[];
  count?: number;
  active_count?: number;
  recent_count?: number;
  saved_count?: number;
}

export interface AssetCatalogResponse extends ApiMeta {
  items: AssetCatalogItem[];
  count?: number;
  query?: string;
}

export interface LearningOverview extends ApiMeta {
  brain_self_score?: BrainScorePoint;
  brain_self_score_history?: BrainScorePoint[];
  overlays?: OverlayRecord[];
  outcome_stats?: Record<string, unknown>;
  live_experience?: Record<string, unknown>;
  safeguards?: Record<string, unknown>;
  pending_outcomes?: Record<string, number>;
  training_schedule?: Record<string, unknown>;
  log_tail?: string[];
}

export interface BrainOverview extends ApiMeta {
  models?: Array<Record<string, unknown>>;
  strategy_performance?: Array<Record<string, unknown>>;
  brain_self_score?: BrainScorePoint;
  brain_self_score_history?: BrainScorePoint[];
  overlays?: OverlayRecord[];
  decision_outcomes?: Record<string, unknown>;
  services?: Record<string, unknown>;
}

export interface SimulationConfigResponse extends ApiMeta {
  defaults?: Record<string, unknown>;
  scopes?: Array<Record<string, string>>;
  tracked_preview?: string[];
  active_preview?: string[];
}

export interface SimulationResultResponse extends ApiMeta {
  controls?: Record<string, unknown>;
  results?: Array<Record<string, unknown>>;
  equity_curve?: Array<Record<string, unknown>>;
  trade_log?: Array<Record<string, unknown>>;
  stats?: Record<string, unknown>;
  safeguard_diagnostics?: Record<string, unknown>;
  message?: string;
  ts?: number;
}

export interface ActivityFeedResponse extends ApiMeta {
  items: ActivityItem[];
  count?: number;
  next_before_ts?: number | null;
}

export interface ReportsResponse extends ApiMeta {
  reports: Record<string, ReportItem | null>;
  history: Record<string, ReportItem[]>;
}

export interface SymbolOverviewResponse extends ApiMeta {
  symbol?: string;
  quote?: Record<string, unknown>;
  overlay?: OverlayRecord | Record<string, unknown>;
  counts?: Record<string, unknown>;
  position?: PositionItem | Record<string, unknown>;
  tracked?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SymbolHistoryResponse extends ApiMeta {
  symbol?: string;
  timeframe?: string;
  range_key?: RangeKey | string;
  items: Array<Record<string, unknown>>;
}

export interface SymbolDecisionsResponse extends ApiMeta {
  symbol?: string;
  decision_traces?: Array<Record<string, unknown>>;
  candidate_traces?: Array<Record<string, unknown>>;
  decision_outcomes?: Array<Record<string, unknown>>;
  component_calibration?: Record<string, unknown>;
}

export interface SymbolTradesResponse extends ApiMeta {
  symbol?: string;
  items: Array<Record<string, unknown>>;
}

export interface SymbolLearningResponse extends ApiMeta {
  symbol?: string;
  overlay?: Record<string, unknown>;
  outcomes?: Array<Record<string, unknown>>;
  label_counts?: Record<string, number>;
}

export interface SymbolBrainResponse extends ApiMeta {
  symbol?: string;
  overlay?: Record<string, unknown>;
  decision_traces?: Array<Record<string, unknown>>;
  candidate_traces?: Array<Record<string, unknown>>;
}

export interface SymbolNewsResponse extends ApiMeta {
  symbol?: string;
  items: Array<Record<string, unknown>>;
  count?: number;
  event_state?: Record<string, unknown>;
  related_reports?: Array<Record<string, unknown>>;
}

export interface SymbolReportsResponse extends ApiMeta {
  symbol?: string;
  items: Array<Record<string, unknown>>;
}

export interface SymbolRawContextResponse extends ApiMeta {
  symbol?: string;
  ts?: number;
  window_ms?: number;
  actions?: Array<Record<string, unknown>>;
  reports?: Array<Record<string, unknown>>;
  news_events?: Array<Record<string, unknown>>;
}

export interface SymbolSnapshotResponse extends ApiMeta {
  symbol: string;
  overview: SymbolOverviewResponse;
  decision_preview: Array<Record<string, unknown>>;
  outcome_preview: Array<Record<string, unknown>>;
  trade_preview: Array<Record<string, unknown>>;
  news_preview: Array<Record<string, unknown>>;
  report_preview: Array<Record<string, unknown>>;
  context_counts: {
    actions: number;
    reports: number;
    news_events: number;
  };
}

function withQuery(path: string, params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return;
    }
    search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function symbolPath(symbol: string, suffix: string): string {
  return `/api/v1/crypto/symbol/${encodeURIComponent(symbol)}/${suffix}`;
}

export function getTerminalSummary(preferCached = true): Promise<TerminalSummary> {
  return fetchJson<TerminalSummary>(
    withQuery('/api/v1/crypto/terminal/summary', { prefer_cached: preferCached ? 1 : 0 }),
    { timeout: 8000 },
  );
}

export function getEquityHistory(sinceMs: number): Promise<EquityHistoryResponse> {
  return fetchJson<EquityHistoryResponse>(
    withQuery('/api/v1/crypto/terminal/equity-history', { since_ms: sinceMs }),
    { timeout: 10000 },
  );
}

export function getScanner(limit = 10): Promise<ScannerResponse> {
  return fetchJson<ScannerResponse>(withQuery('/api/v1/crypto/scanner', { limit }), { timeout: 8000 });
}

export function getTrackedCoins(limit = 80): Promise<TrackedCoinsResponse> {
  return fetchJson<TrackedCoinsResponse>(withQuery('/api/v1/crypto/tracked', { limit }), { timeout: 8000 });
}

export function getAssetCatalog(query = '', limit = 40, tradableOnly = true): Promise<AssetCatalogResponse> {
  return fetchJson<AssetCatalogResponse>(
    withQuery('/api/v1/crypto/assets', { query, limit, tradable_only: tradableOnly ? 1 : 0 }),
    { timeout: 8000 },
  );
}

export function getUniverse(limit = 30): Promise<UniverseResponse> {
  return fetchJson<UniverseResponse>(withQuery('/api/v1/crypto/universe', { limit }), { timeout: 10000 });
}

export function getLearningOverview(): Promise<LearningOverview> {
  return fetchJson<LearningOverview>('/api/v1/crypto/learning/overview', { timeout: 10000 });
}

export function getBrainOverview(): Promise<BrainOverview> {
  return fetchJson<BrainOverview>('/api/v1/crypto/brain/overview', { timeout: 10000 });
}

export function getSimulationConfig(): Promise<SimulationConfigResponse> {
  return fetchJson<SimulationConfigResponse>('/api/v1/crypto/simulation/config', { timeout: 10000 });
}

export function runSimulation(payload: Record<string, unknown>): Promise<SimulationResultResponse> {
  return fetchJson<SimulationResultResponse>('/api/v1/crypto/simulation/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeout: 45000,
  });
}

export function getActivityFeed(params: {
  limit?: number;
  beforeTs?: number | null;
  status?: string;
  symbol?: string;
  search?: string;
}): Promise<ActivityFeedResponse> {
  return fetchJson<ActivityFeedResponse>(
    withQuery('/api/v1/crypto/activity/feed', {
      limit: params.limit ?? 100,
      before_ts: params.beforeTs ?? undefined,
      status: params.status,
      symbol: params.symbol,
      search: params.search,
    }),
    { timeout: 10000 },
  );
}

export function getReports(): Promise<ReportsResponse> {
  return fetchJson<ReportsResponse>('/api/v1/crypto/reports', { timeout: 10000 });
}

export function getSymbolOverview(symbol: string): Promise<SymbolOverviewResponse> {
  return fetchJson<SymbolOverviewResponse>(symbolPath(symbol, 'overview'), { timeout: 10000 });
}

export function getSymbolSnapshot(symbol: string): Promise<SymbolSnapshotResponse> {
  return fetchJson<SymbolSnapshotResponse>(symbolPath(symbol, 'snapshot'), { timeout: 10000 });
}

export function getSymbolHistory(symbol: string, rangeKey: RangeKey | string): Promise<SymbolHistoryResponse> {
  return fetchJson<SymbolHistoryResponse>(
    withQuery(symbolPath(symbol, 'history'), { range_key: rangeKey }),
    { timeout: 10000 },
  );
}

export function getSymbolTrades(symbol: string): Promise<SymbolTradesResponse> {
  return fetchJson<SymbolTradesResponse>(symbolPath(symbol, 'trades'), { timeout: 10000 });
}

export function getSymbolDecisions(symbol: string): Promise<SymbolDecisionsResponse> {
  return fetchJson<SymbolDecisionsResponse>(symbolPath(symbol, 'decisions'), { timeout: 10000 });
}

export function getSymbolLearning(symbol: string): Promise<SymbolLearningResponse> {
  return fetchJson<SymbolLearningResponse>(symbolPath(symbol, 'learning'), { timeout: 10000 });
}

export function getSymbolBrain(symbol: string): Promise<SymbolBrainResponse> {
  return fetchJson<SymbolBrainResponse>(symbolPath(symbol, 'brain'), { timeout: 10000 });
}

export function getSymbolNews(symbol: string): Promise<SymbolNewsResponse> {
  return fetchJson<SymbolNewsResponse>(symbolPath(symbol, 'news'), { timeout: 10000 });
}

export function getSymbolReports(symbol: string): Promise<SymbolReportsResponse> {
  return fetchJson<SymbolReportsResponse>(symbolPath(symbol, 'reports'), { timeout: 10000 });
}

export function getSymbolRawContext(symbol: string): Promise<SymbolRawContextResponse> {
  return fetchJson<SymbolRawContextResponse>(symbolPath(symbol, 'raw-context'), { timeout: 10000 });
}

export function postBotAction(action: 'start' | 'stop' | 'train' | 'finetune' | 'flatten'): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(`/api/v1/crypto/bot/${action}`, { method: 'POST', timeout: 15000 });
}

export function postTradingMode(mode: 'paper' | 'live'): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(`/api/v1/crypto/settings/trading-mode?mode=${mode}`, {
    method: 'POST',
    timeout: 10000,
  });
}

export function updateCryptoBotConfig(updates: Record<string, unknown>): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/v1/crypto/bot/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
    timeout: 10000,
  });
}

export function updateTrackedCoinSaved(symbol: string, saved: boolean): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/v1/crypto/tracked/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, saved }),
    timeout: 10000,
  });
}
