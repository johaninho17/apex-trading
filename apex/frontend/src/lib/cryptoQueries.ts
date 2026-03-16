import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  getActivityFeed,
  getAssetCatalog,
  getBrainOverview,
  getEquityHistory,
  getLearningOverview,
  getReports,
  getScanner,
  getSimulationConfig,
  getTrackedCoins,
  getSymbolBrain,
  getSymbolDecisions,
  getSymbolHistory,
  getSymbolLearning,
  getSymbolNews,
  getSymbolOverview,
  getSymbolRawContext,
  getSymbolReports,
  getSymbolSnapshot,
  getSymbolTrades,
  getTerminalSummary,
  getUniverse,
  runSimulation,
  type AssetCatalogResponse,
  type BrainOverview,
  type EquityHistoryResponse,
  type LearningOverview,
  type RangeKey,
  type ReportsResponse,
  type ScannerResponse,
  type SimulationConfigResponse,
  type SimulationResultResponse,
  type SymbolBrainResponse,
  type SymbolDecisionsResponse,
  type SymbolHistoryResponse,
  type SymbolLearningResponse,
  type SymbolNewsResponse,
  type SymbolOverviewResponse,
  type SymbolRawContextResponse,
  type SymbolReportsResponse,
  type SymbolSnapshotResponse,
  type SymbolTradesResponse,
  type TerminalSummary,
  type TrackedCoinsResponse,
  type UniverseResponse,
} from './cryptoApi';

export const cryptoQueryKeys = {
  root: ['crypto'] as const,
  terminalSummary: (preferCached: boolean) => ['crypto', 'terminal-summary', preferCached ? 'cached' : 'fresh'] as const,
  equityHistory: (rangeKey: RangeKey) => ['crypto', 'equity-history', rangeKey] as const,
  scanner: (limit: number) => ['crypto', 'scanner', limit] as const,
  tracked: (limit: number) => ['crypto', 'tracked', limit] as const,
  assets: (query: string, limit: number, tradableOnly: boolean) => ['crypto', 'assets', query, limit, tradableOnly ? 'tradable' : 'all'] as const,
  universe: (limit: number) => ['crypto', 'universe', limit] as const,
  learningOverview: () => ['crypto', 'learning-overview'] as const,
  brainOverview: () => ['crypto', 'brain-overview'] as const,
  simulationConfig: () => ['crypto', 'simulation-config'] as const,
  reports: () => ['crypto', 'reports'] as const,
  activityFeed: (filters: Record<string, unknown>) => ['crypto', 'activity-feed', filters] as const,
  symbol: (symbol: string) => ['crypto', 'symbol', symbol] as const,
  symbolOverview: (symbol: string) => ['crypto', 'symbol', symbol, 'overview'] as const,
  symbolSnapshot: (symbol: string) => ['crypto', 'symbol', symbol, 'snapshot'] as const,
  symbolHistory: (symbol: string, rangeKey: RangeKey | string) => ['crypto', 'symbol', symbol, 'history', rangeKey] as const,
  symbolTrades: (symbol: string) => ['crypto', 'symbol', symbol, 'trades'] as const,
  symbolDecisions: (symbol: string) => ['crypto', 'symbol', symbol, 'decisions'] as const,
  symbolLearning: (symbol: string) => ['crypto', 'symbol', symbol, 'learning'] as const,
  symbolBrain: (symbol: string) => ['crypto', 'symbol', symbol, 'brain'] as const,
  symbolNews: (symbol: string) => ['crypto', 'symbol', symbol, 'news'] as const,
  symbolReports: (symbol: string) => ['crypto', 'symbol', symbol, 'reports'] as const,
  symbolRawContext: (symbol: string) => ['crypto', 'symbol', symbol, 'raw-context'] as const,
};

export function rangeToSinceMs(rangeKey: RangeKey): number {
  const now = Date.now();
  switch (rangeKey) {
    case '1H':
      return now - 60 * 60 * 1000;
    case '7D':
      return now - 7 * 24 * 60 * 60 * 1000;
    case '30D':
      return now - 30 * 24 * 60 * 60 * 1000;
    case '90D':
      return now - 90 * 24 * 60 * 60 * 1000;
    case 'ALL':
      return 0;
    case '1D':
    default:
      return now - 24 * 60 * 60 * 1000;
  }
}

function warmPlaceholder<T>(previousData: T | undefined): T | undefined {
  return previousData;
}

export function useTerminalSummaryQuery(preferCached = true, enabled = true): UseQueryResult<TerminalSummary> {
  return useQuery({
    queryKey: cryptoQueryKeys.terminalSummary(preferCached),
    queryFn: () => getTerminalSummary(preferCached),
    enabled,
    staleTime: preferCached ? 15_000 : 5_000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: enabled ? 30_000 : false,
    placeholderData: warmPlaceholder,
  });
}

export function useEquityHistoryQuery(rangeKey: RangeKey, enabled = true): UseQueryResult<EquityHistoryResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.equityHistory(rangeKey),
    queryFn: () => getEquityHistory(rangeToSinceMs(rangeKey)),
    enabled,
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useScannerQuery(limit = 10, enabled = true): UseQueryResult<ScannerResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.scanner(limit),
    queryFn: () => getScanner(limit),
    enabled,
    staleTime: 15_000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: enabled ? 30_000 : false,
    placeholderData: warmPlaceholder,
  });
}

export function useTrackedCoinsQuery(limit = 80, enabled = true): UseQueryResult<TrackedCoinsResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.tracked(limit),
    queryFn: () => getTrackedCoins(limit),
    enabled,
    staleTime: 15_000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: enabled ? 30_000 : false,
    placeholderData: warmPlaceholder,
  });
}

export function useAssetCatalogQuery(query: string, limit = 40, tradableOnly = true, enabled = true): UseQueryResult<AssetCatalogResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.assets(query, limit, tradableOnly),
    queryFn: () => getAssetCatalog(query, limit, tradableOnly),
    enabled,
    staleTime: 10_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useUniverseQuery(limit = 30, enabled = true): UseQueryResult<UniverseResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.universe(limit),
    queryFn: () => getUniverse(limit),
    enabled,
    staleTime: 30_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useLearningOverviewQuery(enabled = true): UseQueryResult<LearningOverview> {
  return useQuery({
    queryKey: cryptoQueryKeys.learningOverview(),
    queryFn: getLearningOverview,
    enabled,
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useBrainOverviewQuery(enabled = true): UseQueryResult<BrainOverview> {
  return useQuery({
    queryKey: cryptoQueryKeys.brainOverview(),
    queryFn: getBrainOverview,
    enabled,
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSimulationConfigQuery(enabled = true): UseQueryResult<SimulationConfigResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.simulationConfig(),
    queryFn: getSimulationConfig,
    enabled,
    staleTime: 60_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useReportsOverviewQuery(enabled = true): UseQueryResult<ReportsResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.reports(),
    queryFn: getReports,
    enabled,
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useActivityFeedInfiniteQuery(filters: {
  limit?: number;
  status?: string;
  symbol?: string;
  search?: string;
}, enabled = true) {
  return useInfiniteQuery({
    queryKey: cryptoQueryKeys.activityFeed({
      limit: filters.limit ?? 100,
      status: filters.status ?? '',
      symbol: filters.symbol ?? '',
      search: filters.search ?? '',
    }),
    queryFn: ({ pageParam }) => getActivityFeed({
      limit: filters.limit ?? 100,
      beforeTs: (pageParam ?? null) as number | null,
      status: filters.status,
      symbol: filters.symbol,
      search: filters.search,
    }),
    enabled,
    staleTime: 5_000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: enabled ? 15_000 : false,
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.next_before_ts ?? null,
  });
}

export function useSymbolOverviewQuery(symbol: string, enabled = true): UseQueryResult<SymbolOverviewResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolOverview(symbol),
    queryFn: () => getSymbolOverview(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolSnapshotQuery(symbol: string, enabled = true): UseQueryResult<SymbolSnapshotResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolSnapshot(symbol),
    queryFn: () => getSymbolSnapshot(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: (query) => {
      if (!enabled || !symbol) {
        return false;
      }
      const status = query.state.data?.status ?? '';
      return status === 'partial' || status === 'stale' ? 5_000 : false;
    },
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolHistoryQuery(
  symbol: string,
  rangeKey: RangeKey | string,
  enabled = true,
): UseQueryResult<SymbolHistoryResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolHistory(symbol, rangeKey),
    queryFn: () => getSymbolHistory(symbol, rangeKey),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolTradesQuery(symbol: string, enabled = true): UseQueryResult<SymbolTradesResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolTrades(symbol),
    queryFn: () => getSymbolTrades(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolDecisionsQuery(symbol: string, enabled = true): UseQueryResult<SymbolDecisionsResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolDecisions(symbol),
    queryFn: () => getSymbolDecisions(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolLearningQuery(symbol: string, enabled = true): UseQueryResult<SymbolLearningResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolLearning(symbol),
    queryFn: () => getSymbolLearning(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolBrainQuery(symbol: string, enabled = true): UseQueryResult<SymbolBrainResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolBrain(symbol),
    queryFn: () => getSymbolBrain(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolNewsQuery(symbol: string, enabled = true): UseQueryResult<SymbolNewsResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolNews(symbol),
    queryFn: () => getSymbolNews(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolReportsQuery(symbol: string, enabled = true): UseQueryResult<SymbolReportsResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolReports(symbol),
    queryFn: () => getSymbolReports(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export function useSymbolRawContextQuery(symbol: string, enabled = true): UseQueryResult<SymbolRawContextResponse> {
  return useQuery({
    queryKey: cryptoQueryKeys.symbolRawContext(symbol),
    queryFn: () => getSymbolRawContext(symbol),
    enabled: enabled && Boolean(symbol),
    staleTime: 20_000,
    gcTime: 10 * 60 * 1000,
    placeholderData: warmPlaceholder,
  });
}

export async function prefetchSymbolSnapshot(queryClient: QueryClient, symbol: string): Promise<void> {
  if (!symbol) {
    return;
  }
  await queryClient.prefetchQuery({
    queryKey: cryptoQueryKeys.symbolSnapshot(symbol),
    queryFn: () => getSymbolSnapshot(symbol),
    staleTime: 20_000,
  });
}

export function usePrefetchSymbolSnapshot() {
  const queryClient = useQueryClient();
  return (symbol: string) => prefetchSymbolSnapshot(queryClient, symbol);
}

export async function invalidateTerminalShell(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['crypto', 'terminal-summary'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'equity-history'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'scanner'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'tracked'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'assets'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'universe'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'learning-overview'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'brain-overview'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'reports'] }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'activity-feed'] }),
  ]);
}

export async function runSimulationMutation(queryClient: QueryClient, payload: Record<string, unknown>): Promise<SimulationResultResponse> {
  const result = await runSimulation(payload);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: cryptoQueryKeys.simulationConfig() }),
    queryClient.invalidateQueries({ queryKey: ['crypto', 'equity-history'] }),
  ]);
  return result;
}
