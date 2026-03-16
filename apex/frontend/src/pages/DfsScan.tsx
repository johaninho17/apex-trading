import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Fragment } from 'react';
import { Search, Loader2, Zap, TrendingUp, Plus, X, Clipboard, Clock, ArrowUp, ArrowDown, Volleyball, Shield, CircleDot, Goal, ChevronDown, CheckCircle2, MinusCircle, Lock, Unlock, SlidersHorizontal, History, Save, Trash2 } from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import {
    loadCalcProfile,
    saveCalcProfile,
    loadProfileFromSettings,
} from '../lib/calcProfiles';
import type { DfsCalcProfile, CalcPreset } from '../lib/calcProfiles';
import { pushToast } from '../components/Toaster';
import './DfsScan.css';

type ScanScope = 'smart' | 'full';
type TargetPlatform = 'sleeper' | 'any' | 'prizepicks' | 'underdog';

interface ScanResult {
    player_name: string;
    market: string;
    line: number;
    side: string;                    // 'over' or 'under'
    sharp_odds: number;
    sharp_book: string;
    edge_pct: number;
    is_play: boolean;
    is_calculated?: boolean;
    calc_reason?: string | null;
    is_trending: boolean;
    // Info tooltip fields
    opposing_odds: number | null;
    sharp_implied_prob: number;      // Already in %
    opposing_implied_prob: number | null;  // Already in %
    fair_prob: number | null;        // Already in %
    fixed_implied_prob: number;      // Already in %
    vig_pct: number | null;
    apex_odds?: number;
    consensus_prob_pct?: number;
    books_used?: number;
    weight_coverage_pct?: number;
    book_odds?: Array<{ book: string; odds: number; weight?: number; implied_prob_pct?: number }>;
    available_books?: string[];
    available_on_sleeper_compatible?: boolean;
    available_on_sleeper_direct?: boolean;
    available_on_prizepicks_direct?: boolean;
    available_on_underdog_direct?: boolean;
    eligible_for_slip?: boolean;
    slip_platform?: string;
    commence_time?: string | null;
    home_team?: string | null;
    away_team?: string | null;
}

interface SlipStats {
    valid: boolean;
    slip_size: number;
    combined_edge_pct: number;
    win_probability_pct: number;
    payout_multiplier: number;
    expected_value_pct: number;
    error?: string;
}

interface EvCalcSnapshot {
    ev_percent: number;
    kelly_fraction: number;
    kelly_stake: number;
    blended_probability: number;
    fair_prob?: number | null;
    vig_pct?: number | null;
}

interface ScanStats {
    trending_players: number;
    total_scanned: number;
    plays_found: number;
    scan_scope?: ScanScope;
    games_queried?: number;
    persisted_count?: number;
    persisted_trimmed_count?: number;
}

interface ScanHistorySummary {
    id: string;
    ts: number;
    sport: string;
    scan_scope: ScanScope;
    total_scanned: number;
    plays_found: number;
    games_queried: number;
    results_count?: number;
    slip_count?: number;
}

interface ScanHistoryDetail extends ScanHistorySummary {
    stats: ScanStats;
    results: ScanResult[];
    slip: ScanResult[];
    locked_keys: string[];
}

// ── SessionStorage helpers ──
const SCAN_KEY = 'dfs_scan_results';
const SCAN_META_KEY = 'dfs_scan_meta';
const SLIP_KEY = 'dfs_scan_slip';
const PREFS_KEY = 'dfs_scan_prefs';
const PLAYER_EXPAND_KEY = 'dfs_scan_player_expanded';
const SELECTED_SCAN_VERSION_KEY = 'dfs_selected_scan_version';
const MAX_PERSISTED_SCAN_ROWS = 600;
const PLAYER_GROUPS_PER_PAGE = 20;

function compactScanResult(row: ScanResult): ScanResult {
    const slimBooks = Array.isArray(row.book_odds) ? row.book_odds.slice(0, 8) : undefined;
    return {
        ...row,
        book_odds: slimBooks,
    };
}

function loadSavedScan(): { results: ScanResult[]; stats: ScanStats | null; lastScanTime: string | null } {
    try {
        const raw = sessionStorage.getItem(SCAN_KEY);
        const meta = sessionStorage.getItem(SCAN_META_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            const metaParsed = meta ? JSON.parse(meta) : {};
            return { results: parsed.results || [], stats: parsed.stats || null, lastScanTime: metaParsed.lastScanTime || null };
        }
    } catch { }
    return { results: [], stats: null, lastScanTime: null };
}

function saveScan(results: ScanResult[], stats: ScanStats, sport: string = 'nba') {
    const now = new Date().toISOString();
    const compact = results.slice(0, MAX_PERSISTED_SCAN_ROWS).map(compactScanResult);
    const trimmed = Math.max(0, results.length - compact.length);
    const persistedStats: ScanStats = {
        ...stats,
        persisted_count: compact.length,
        persisted_trimmed_count: trimmed,
    };
    sessionStorage.setItem(SCAN_KEY, JSON.stringify({ results: compact, stats: persistedStats, sport }));
    sessionStorage.setItem(SCAN_META_KEY, JSON.stringify({ lastScanTime: now }));
    return now;
}

function formatDateTime(value: string): string {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString([], {
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}

const TEAM_ABBREVIATIONS: Record<string, string> = {
    'Atlanta Hawks': 'ATL',
    'Boston Celtics': 'BOS',
    'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA',
    'Chicago Bulls': 'CHI',
    'Cleveland Cavaliers': 'CLE',
    'Dallas Mavericks': 'DAL',
    'Denver Nuggets': 'DEN',
    'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GSW',
    'Houston Rockets': 'HOU',
    'Indiana Pacers': 'IND',
    'LA Clippers': 'LAC',
    'Los Angeles Clippers': 'LAC',
    'Los Angeles Lakers': 'LAL',
    'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA',
    'Milwaukee Bucks': 'MIL',
    'Minnesota Timberwolves': 'MIN',
    'New Orleans Pelicans': 'NOP',
    'New York Knicks': 'NYK',
    'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL',
    'Philadelphia 76ers': 'PHI',
    'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR',
    'Sacramento Kings': 'SAC',
    'San Antonio Spurs': 'SAS',
    'Toronto Raptors': 'TOR',
    'Utah Jazz': 'UTA',
    'Washington Wizards': 'WAS',
    'Arizona Cardinals': 'ARI',
    'Atlanta Falcons': 'ATL',
    'Baltimore Ravens': 'BAL',
    'Buffalo Bills': 'BUF',
    'Carolina Panthers': 'CAR',
    'Chicago Bears': 'CHI',
    'Cincinnati Bengals': 'CIN',
    'Cleveland Browns': 'CLE',
    'Dallas Cowboys': 'DAL',
    'Denver Broncos': 'DEN',
    'Detroit Lions': 'DET',
    'Green Bay Packers': 'GB',
    'Houston Texans': 'HOU',
    'Indianapolis Colts': 'IND',
    'Jacksonville Jaguars': 'JAX',
    'Kansas City Chiefs': 'KC',
    'Las Vegas Raiders': 'LV',
    'Los Angeles Chargers': 'LAC',
    'Los Angeles Rams': 'LAR',
    'Miami Dolphins': 'MIA',
    'Minnesota Vikings': 'MIN',
    'New England Patriots': 'NE',
    'New Orleans Saints': 'NO',
    'New York Giants': 'NYG',
    'New York Jets': 'NYJ',
    'Philadelphia Eagles': 'PHI',
    'Pittsburgh Steelers': 'PIT',
    'San Francisco 49ers': 'SF',
    'Seattle Seahawks': 'SEA',
    'Tampa Bay Buccaneers': 'TB',
    'Tennessee Titans': 'TEN',
    'Washington Commanders': 'WAS',
};

function fallbackTeamAbbrev(team: string): string {
    const cleaned = team.replace(/[^A-Za-z0-9 ]+/g, ' ').trim();
    if (!cleaned) return 'TBD';
    const words = cleaned.split(/\s+/).filter(Boolean);
    if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
    if (words.length === 2) return `${words[0][0]}${words[1].slice(0, 2)}`.toUpperCase();
    return `${words[0][0]}${words[1][0]}${words[words.length - 1][0]}`.toUpperCase();
}

function teamAbbrev(team: string | null | undefined): string {
    if (!team) return 'TBD';
    return TEAM_ABBREVIATIONS[team] || fallbackTeamAbbrev(team);
}

function formatGameWhen(row: ScanResult): string | null {
    if (!row.commence_time) return null;
    const d = new Date(row.commence_time);
    if (Number.isNaN(d.getTime())) return null;
    const day = d.toLocaleString([], { weekday: 'short', timeZone: 'America/Los_Angeles' });
    const time = d.toLocaleString([], {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
        timeZone: 'America/Los_Angeles',
    });
    return `${day} @ ${time} PT`;
}

function formatGameMatchup(row: ScanResult): string | null {
    const home = row.home_team?.trim() || '';
    const away = row.away_team?.trim() || '';
    return home && away ? `${teamAbbrev(away)} vs ${teamAbbrev(home)}` : null;
}

function gameFilterKey(row: ScanResult): string {
    const home = row.home_team?.trim() || '';
    const away = row.away_team?.trim() || '';
    if (!home && !away) return '__unknown__';
    return `${away}@@${home}`;
}

function gameFilterLabel(row: ScanResult): string {
    const matchup = formatGameMatchup(row) || 'TBD';
    const when = formatGameWhen(row);
    return when ? `${matchup} | ${when}` : matchup;
}

function loadSavedSlip(): ScanResult[] {
    try {
        const raw = sessionStorage.getItem(SLIP_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function loadSavedPrefs(): {
    sport: string;
    scanScope: ScanScope;
    maxGames: number;
    trendingLimit: number;
    minEdge: number;
    playerSearch: string;
    selectedProps: string[];
    selectedGame: string;
    trendingOnly: boolean;
    minBooksFilter: number;
    showPlaysOnly: boolean;
    sideFilter: 'all' | 'over' | 'under';
    targetPlatform: TargetPlatform;
    sleeperMarketsOnly: boolean;
    consensusMinBooks: number;
    consensusLineWindow: number;
    consensusMainLineOnly: boolean;
    consensusMinTrendCount: number;
} {
    try {
        const raw = sessionStorage.getItem(PREFS_KEY);
        if (!raw) {
            return {
                sport: 'nba',
                scanScope: 'smart',
                maxGames: 8,
                trendingLimit: 80,
                minEdge: 0,
                playerSearch: '',
                selectedProps: [],
                selectedGame: 'all',
                trendingOnly: false,
                minBooksFilter: 1,
                showPlaysOnly: false,
                sideFilter: 'all',
                targetPlatform: 'sleeper',
                sleeperMarketsOnly: true,
                consensusMinBooks: 1,
                consensusLineWindow: 1,
                consensusMainLineOnly: true,
                consensusMinTrendCount: 0,
            };
        }
        const parsed = JSON.parse(raw);
        const targetRaw = typeof parsed.targetPlatform === 'string' ? parsed.targetPlatform.toLowerCase() : 'sleeper';
        return {
            sport: String(parsed.sport || 'nba'),
            scanScope: parsed.scanScope === 'full' ? 'full' : 'smart',
            maxGames: Number.isFinite(Number(parsed.maxGames)) ? Math.max(1, Math.min(20, Number(parsed.maxGames))) : 8,
            trendingLimit: Number.isFinite(Number(parsed.trendingLimit)) ? Math.max(1, Math.min(200, Number(parsed.trendingLimit))) : 80,
            minEdge: Number(parsed.minEdge || 0),
            playerSearch: typeof parsed.playerSearch === 'string' ? parsed.playerSearch : '',
            selectedProps: Array.isArray(parsed.selectedProps) ? parsed.selectedProps.filter((v: unknown) => typeof v === 'string') : [],
            selectedGame: typeof parsed.selectedGame === 'string' ? parsed.selectedGame : 'all',
            trendingOnly: !!parsed.trendingOnly,
            minBooksFilter: Number.isFinite(Number(parsed.minBooksFilter)) ? Math.max(1, Math.min(8, Number(parsed.minBooksFilter))) : 1,
            showPlaysOnly: !!parsed.showPlaysOnly,
            sideFilter: parsed.sideFilter === 'over' || parsed.sideFilter === 'under' ? parsed.sideFilter : 'all',
            targetPlatform: (['sleeper', 'any', 'prizepicks', 'underdog'] as const).includes(targetRaw as TargetPlatform)
                ? (targetRaw as TargetPlatform)
                : 'sleeper',
            sleeperMarketsOnly: parsed.sleeperMarketsOnly !== false,
            consensusMinBooks: Number.isFinite(Number(parsed.consensusMinBooks)) ? Math.max(1, Number(parsed.consensusMinBooks)) : 1,
            consensusLineWindow: Number.isFinite(Number(parsed.consensusLineWindow)) ? Math.max(0, Number(parsed.consensusLineWindow)) : 1,
            consensusMainLineOnly: parsed.consensusMainLineOnly !== false,
            consensusMinTrendCount: Number.isFinite(Number(parsed.consensusMinTrendCount)) ? Math.max(0, Number(parsed.consensusMinTrendCount)) : 0,
        };
    } catch {
        return {
            sport: 'nba',
            scanScope: 'smart',
            maxGames: 8,
            trendingLimit: 80,
            minEdge: 0,
            playerSearch: '',
            selectedProps: [],
            selectedGame: 'all',
            trendingOnly: false,
            minBooksFilter: 1,
            showPlaysOnly: false,
            sideFilter: 'all',
            targetPlatform: 'sleeper',
            sleeperMarketsOnly: true,
            consensusMinBooks: 1,
            consensusLineWindow: 1,
            consensusMainLineOnly: true,
            consensusMinTrendCount: 0,
        };
    }
}

function loadSavedLocks(): string[] {
    try {
        const raw = sessionStorage.getItem('dfs_scan_locks');
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : [];
    } catch {
        return [];
    }
}

function lockMapFromKeys(keys: string[]): Record<string, boolean> {
    const out: Record<string, boolean> = {};
    for (const key of keys) out[key] = true;
    return out;
}


function rowKey(r: ScanResult): string {
    return `${r.player_name}|${r.market}|${r.side}|${r.line}|${(r.sharp_book || '').toLowerCase()}`;
}

function playerKey(playerName: string): string {
    return playerName.trim().toLowerCase();
}

function propBookKey(r: ScanResult): string {
    return `${r.player_name}|${r.market}|${r.line}|${r.side || 'over'}`.toLowerCase();
}

function normalizeBookName(book: string): string {
    return String(book || '').trim().replace(/\s+/g, ' ');
}

function canonicalBookKey(book: string): string {
    const compact = normalizeBookName(book).toLowerCase().replace(/[^a-z0-9]/g, '');
    const aliases: Record<string, string> = {
        fanduel: 'fanduel', fanduelsportsbook: 'fanduel',
        draftkings: 'draftkings', draftkingssportsbook: 'draftkings',
        betmgm: 'betmgm', mgm: 'betmgm',
        bovada: 'bovada', bovadalk: 'bovada',
        underdog: 'underdog', underdogsports: 'underdog', undersog: 'underdog',
        pinnacle: 'pinnacle', pinnaclesports: 'pinnacle', pin: 'pinnacle',
        bookmaker: 'bookmaker', thebookmaker: 'bookmaker', bm: 'bookmaker',
        caesars: 'caesars', caesarssportsbook: 'caesars', czr: 'caesars',
        betrivers: 'betrivers', br: 'betrivers',
        pointsbet: 'pointsbet', pb: 'pointsbet',
        espnbet: 'espnbet', espn: 'espnbet',
    };
    return aliases[compact] || compact;
}

function bookBadge(book: string): { mark: string; label: string } {
    const raw = normalizeBookName(book);
    const key = canonicalBookKey(raw);
    const known: Record<string, { mark: string; label: string }> = {
        draftkings: { mark: 'DK', label: 'DraftKings' },
        fanduel: { mark: 'FD', label: 'FanDuel' },
        pinnacle: { mark: 'PIN', label: 'Pinnacle' },
        bookmaker: { mark: 'BM', label: 'BookMaker' },
        betmgm: { mark: 'MGM', label: 'BetMGM' },
        bovada: { mark: 'BVD', label: 'Bovada' },
        underdog: { mark: 'UD', label: 'Underdog' },
        caesars: { mark: 'CZ', label: 'Caesars' },
        betrivers: { mark: 'BR', label: 'BetRivers' },
        pointsbet: { mark: 'PB', label: 'PointsBet' },
        espnbet: { mark: 'ESPN', label: 'ESPN Bet' },
    };
    if (known[key]) return known[key];
    const mark = raw.split(' ').map(p => p[0]).join('').slice(0, 3).toUpperCase() || 'BK';
    return { mark, label: raw || 'Unknown Book' };
}

function isSleeperCompatibleRow(r: ScanResult): boolean {
    if (typeof r.eligible_for_slip === 'boolean') return r.eligible_for_slip;
    if (typeof r.available_on_sleeper_compatible === 'boolean') return r.available_on_sleeper_compatible;
    if (r.available_on_sleeper_direct) return true;
    const sleeperMarkets = new Set([
        'player_points', 'player_rebounds', 'player_assists',
        'player_threes', 'player_blocks', 'player_steals',
        'player_turnovers',
        'player_pass_yds', 'player_pass_tds', 'player_pass_completions',
        'player_pass_attempts', 'player_pass_interceptions',
        'player_rush_yds', 'player_rush_attempts', 'player_rush_tds',
        'player_receptions', 'player_reception_yds', 'player_reception_tds',
        'player_anytime_td', 'player_kicking_points',
        'pitcher_strikeouts', 'pitcher_outs', 'batter_hits',
        'batter_total_bases', 'batter_rbis', 'batter_runs_scored',
        'batter_walks', 'batter_stolen_bases', 'batter_home_runs',
        'player_shots', 'player_shots_on_target', 'player_goal_scorer_anytime',
    ]);
    return sleeperMarkets.has(String(r.market || '').trim());
}

function median(values: number[]): number {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    if (sorted.length % 2) return sorted[mid];
    return (sorted[mid - 1] + sorted[mid]) / 2;
}

function confidenceScore(r: ScanResult, profile: DfsCalcProfile): number {
    const edge = Math.max(-20, Math.min(20, r.edge_pct || 0));
    const vigPenalty = profile.useVigPenalty ? Math.max(0, r.vig_pct || 0) * 0.65 : 0;
    const fairBonus = profile.useDevig && r.fair_prob != null ? 4 : 0;
    const trendBonus = profile.useTrendBonus && r.is_trending ? 5 : 0;
    const raw = 50 + edge * 2.2 - vigPenalty + fairBonus + trendBonus;
    return Math.max(0, Math.min(100, raw));
}

function confidenceTier(score: number): string {
    if (score >= 74) return 'High';
    if (score >= 58) return 'Medium';
    return 'Low';
}

function riskTier(r: ScanResult): string {
    const vig = r.vig_pct || 0;
    if (vig >= 8 || Math.abs(r.edge_pct) < 1.2) return 'Elevated';
    if (vig >= 5 || Math.abs(r.edge_pct) < 2.2) return 'Moderate';
    return 'Controlled';
}

function suggestedStakePct(r: ScanResult, profile: DfsCalcProfile): number {
    if (r.edge_pct <= 0) return 0;
    const baseProb = profile.useDevig && r.fair_prob != null ? r.fair_prob : r.sharp_implied_prob;
    let p = (baseProb || 0) / 100;
    const fixed = (r.fixed_implied_prob || 0) / 100;
    if (profile.useConfidenceShrink) {
        const conf = confidenceScore(r, profile) / 100;
        p = 0.5 + ((p - 0.5) * conf);
    }
    if (p <= 0 || p >= 1 || fixed <= 0 || fixed >= 1) return Math.min(2.5, r.edge_pct * 0.16);
    const b = (1 / fixed) - 1;
    if (b <= 0) return Math.min(2.5, r.edge_pct * 0.16);
    const q = 1 - p;
    const fullKelly = ((b * p) - q) / b;
    const fractional = Math.max(0, fullKelly * 0.25);
    const uncapped = Math.max(0, fractional * 100);
    if (!profile.useKellyCap) return uncapped;
    return Math.max(0, Math.min(profile.kellyCapPct, uncapped));
}

function aiScore(
    r: ScanResult,
    profile: DfsCalcProfile,
    correlationPenalty: number = 0,
    bookCount: number = 1,
    oddsDispersion: number = 0
): number {
    const conf = confidenceScore(r, profile) / 100;
    const edge = Math.max(-20, Math.min(20, r.edge_pct || 0));
    const stake = suggestedStakePct(r, profile);
    const vigPenalty = profile.useVigPenalty ? Math.max(0, (r.vig_pct || 0) - 2) * 0.22 : 0;
    const corrPenalty = profile.useCorrelationPenalty ? correlationPenalty * 0.18 : 0;
    const liquidityBoost = Math.min(0.18, Math.log2(Math.max(1, bookCount)) * 0.08);
    const disagreementPenalty = Math.min(0.22, Math.max(0, oddsDispersion - 14) * 0.006);
    // Use tanh scaling to avoid score saturation at 99/100 for typical edges.
    const signal = (edge * 0.07 * profile.edgeWeight)
        + (conf * 0.9 * profile.confidenceWeight)
        + (Math.min(12, stake) * 0.02 * profile.stakeWeight)
        + liquidityBoost
        - vigPenalty
        - corrPenalty
        - disagreementPenalty
        - 0.75;
    const bounded = 50 + (Math.tanh(signal) * 40);
    return Math.max(8, Math.min(92, bounded));
}

function correlationWarningsForSlip(slip: ScanResult[]): string[] {
    if (slip.length < 2) return [];
    const warnings: string[] = [];
    const marketCounts = new Map<string, number>();
    const sideCounts = new Map<string, number>();
    for (const p of slip) {
        const market = p.market || 'unknown';
        marketCounts.set(market, (marketCounts.get(market) || 0) + 1);
        const side = p.side || 'over';
        sideCounts.set(side, (sideCounts.get(side) || 0) + 1);
    }
    const repeatedMarket = Array.from(marketCounts.entries()).find(([, c]) => c >= 2);
    if (repeatedMarket) {
        warnings.push(`Correlation risk: ${repeatedMarket[1]} picks share ${formatMarket(repeatedMarket[0])}.`);
    }
    const oneSided = Array.from(sideCounts.entries()).find(([, c]) => c >= 3);
    if (oneSided) {
        warnings.push(`Concentration risk: ${oneSided[1]} picks on ${oneSided[0].toUpperCase()} side.`);
    }
    if (warnings.length === 0 && slip.length >= 4) {
        warnings.push('Moderate correlation risk due to slip size; diversify markets/sides where possible.');
    }
    return warnings;
}

function correlationPenaltyForPick(pick: ScanResult, slip: ScanResult[]): number {
    const sameMarketCount = slip.filter(p => p.market === pick.market).length;
    const sameSideCount = slip.filter(p => (p.side || 'over') === (pick.side || 'over')).length;
    return Math.max(0, (sameMarketCount * 3.5) + (Math.max(0, sameSideCount - 1) * 1.2));
}

export default function DfsScan() {
    const saved = loadSavedScan();
    const savedPrefs = loadSavedPrefs();
    const [sport, setSport] = useState(savedPrefs.sport);
    const [scanScope, setScanScope] = useState<ScanScope>(savedPrefs.scanScope);
    const [maxGames, setMaxGames] = useState<number>(savedPrefs.maxGames);
    const [trendingLimit, setTrendingLimit] = useState<number>(savedPrefs.trendingLimit);
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState<ScanResult[]>(saved.results);
    const [stats, setStats] = useState<ScanStats | null>(saved.stats);
    const [error, setError] = useState('');
    const [minEdge, setMinEdge] = useState(savedPrefs.minEdge);
    const [playerSearch, setPlayerSearch] = useState(savedPrefs.playerSearch);
    const [selectedProps, setSelectedProps] = useState<string[]>(savedPrefs.selectedProps);
    const [selectedGame, setSelectedGame] = useState(savedPrefs.selectedGame);
    const [trendingOnly, setTrendingOnly] = useState(savedPrefs.trendingOnly);
    const [minBooksFilter, setMinBooksFilter] = useState(savedPrefs.minBooksFilter);
    const [showPlaysOnly, setShowPlaysOnly] = useState(savedPrefs.showPlaysOnly);
    const [sideFilter, setSideFilter] = useState<'all' | 'over' | 'under'>(savedPrefs.sideFilter);
    const [targetPlatform, setTargetPlatform] = useState<TargetPlatform>(savedPrefs.targetPlatform);
    const [sleeperMarketsOnly, setSleeperMarketsOnly] = useState(savedPrefs.sleeperMarketsOnly);
    const [consensusMinBooks, setConsensusMinBooks] = useState(savedPrefs.consensusMinBooks);
    const [consensusLineWindow, setConsensusLineWindow] = useState(savedPrefs.consensusLineWindow);
    const [consensusMainLineOnly, setConsensusMainLineOnly] = useState(savedPrefs.consensusMainLineOnly);
    const [consensusMinTrendCount, setConsensusMinTrendCount] = useState(savedPrefs.consensusMinTrendCount);
    const [autoSortPlayScore, setAutoSortPlayScore] = useState(true);
    const [lastScanTime, setLastScanTime] = useState<string | null>(saved.lastScanTime);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historySaving, setHistorySaving] = useState(false);
    const [historyDeletingId, setHistoryDeletingId] = useState<string | null>(null);
    const [historyError, setHistoryError] = useState('');
    const [scanHistory, setScanHistory] = useState<ScanHistorySummary[]>([]);
    const [selectedScanVersionId, setSelectedScanVersionId] = useState<string | null>(() => {
        try {
            const raw = sessionStorage.getItem(SELECTED_SCAN_VERSION_KEY);
            return raw && raw.trim() ? raw : null;
        } catch {
            return null;
        }
    });
    const [slipExpanded, setSlipExpanded] = useState(false);
    const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
    const [expandedPlayers, setExpandedPlayers] = useState<Record<string, boolean>>(() => {
        try {
            const raw = sessionStorage.getItem(PLAYER_EXPAND_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch {
            return {};
        }
    });
    const [playerRenderLimit, setPlayerRenderLimit] = useState<Record<string, number>>({});
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [minEdgeInput, setMinEdgeInput] = useState<string>(String(savedPrefs.minEdge));
    const [defaultStake, setDefaultStake] = useState<number>(25);
    const [recommendedSlips, setRecommendedSlips] = useState<any[]>([]);
    const [optimizingSlips, setOptimizingSlips] = useState(false);
    const [correlationEv, setCorrelationEv] = useState<{ ev_percent: number; recommendation: string } | null>(null);
    const [calcProfile, setCalcProfile] = useState<DfsCalcProfile>(loadCalcProfile('dfs'));
    const [calcOpen, setCalcOpen] = useState(false);
    const [tableCollapsed, setTableCollapsed] = useState(false);
    const [currentPage, setCurrentPage] = useState(1);

    // ── Slip Builder State ──
    const [slip, setSlip] = useState<ScanResult[]>(loadSavedSlip());
    const [lockedPicks, setLockedPicks] = useState<Record<string, boolean>>(() => {
        const init: Record<string, boolean> = {};
        for (const k of loadSavedLocks()) init[k] = true;
        return init;
    });
    const prevSlipLenRef = useRef(0);
    const [slipStats, setSlipStats] = useState<SlipStats | null>(null);
    const [slipLoading, setSlipLoading] = useState(false);
    const [evCalcByKey, setEvCalcByKey] = useState<Record<string, EvCalcSnapshot>>({});
    const [evLoadingByKey, setEvLoadingByKey] = useState<Record<string, boolean>>({});

    useEffect(() => {
        setMinEdgeInput(String(minEdge));
    }, [minEdge]);

    function togglePropSelection(prop: string) {
        setSelectedProps(prev => (
            prev.includes(prop) ? prev.filter(p => p !== prop) : [...prev, prop]
        ));
    }

    useEffect(() => {
        try {
            sessionStorage.setItem(PLAYER_EXPAND_KEY, JSON.stringify(expandedPlayers));
        } catch {
            // ignore
        }
    }, [expandedPlayers]);

    function applyDfsSettings(config: any) {
        const quick = config?.dfs?.quick_settings;
        if (!quick) return;
        if (Number.isFinite(Number(quick.min_edge))) setMinEdge(Number(quick.min_edge));
        if (typeof quick.plays_only === 'boolean') setShowPlaysOnly(quick.plays_only);
        if (quick.side_filter === 'all' || quick.side_filter === 'over' || quick.side_filter === 'under') setSideFilter(quick.side_filter);
        if (typeof quick.auto_sort_play_score === 'boolean') setAutoSortPlayScore(quick.auto_sort_play_score);
        if (typeof quick.sleeper_markets_only === 'boolean') setSleeperMarketsOnly(quick.sleeper_markets_only);
        const consensus = config?.dfs?.consensus;
        if (consensus && typeof consensus === 'object') {
            if (Number.isFinite(Number(consensus.min_books))) setConsensusMinBooks(Math.max(1, Number(consensus.min_books)));
            if (Number.isFinite(Number(consensus.line_window))) setConsensusLineWindow(Math.max(0, Number(consensus.line_window)));
            if (typeof consensus.main_line_only === 'boolean') setConsensusMainLineOnly(consensus.main_line_only);
            if (Number.isFinite(Number(consensus.min_trend_count))) setConsensusMinTrendCount(Math.max(0, Number(consensus.min_trend_count)));
        }
    }

    async function refreshScanHistory(showSpinner: boolean = true) {
        if (showSpinner) setHistoryLoading(true);
        setHistoryError('');
        try {
            const res = await fetch('/api/v1/dfs/scan-history?limit=80');
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            const rows = Array.isArray(data.versions) ? data.versions : [];
            setScanHistory(rows);
        } catch (e: any) {
            setHistoryError(e?.message || 'Failed to load scan history');
        } finally {
            if (showSpinner) setHistoryLoading(false);
        }
    }

    async function persistScanVersion(opps: ScanResult[], scanStats: ScanStats): Promise<ScanHistorySummary | null> {
        setHistorySaving(true);
        try {
            const res = await fetch('/api/v1/dfs/scan-history', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sport,
                    scan_scope: scanScope,
                    stats: scanStats,
                    results: opps,
                    slip,
                    locked_keys: Object.keys(lockedPicks).filter(k => lockedPicks[k]),
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const saved: ScanHistorySummary = await res.json();
            if (saved?.id) {
                setSelectedScanVersionId(saved.id);
                try {
                    sessionStorage.setItem(SELECTED_SCAN_VERSION_KEY, saved.id);
                } catch {
                    // ignore
                }
                setScanHistory(prev => {
                    const next = [saved, ...prev.filter(v => v.id !== saved.id)];
                    return next.slice(0, 80);
                });
            }
            return saved;
        } catch {
            // keep scan flow non-blocking if history persistence fails
            return null;
        } finally {
            setHistorySaving(false);
        }
    }

    async function saveCurrentScanSnapshot() {
        if (!results.length) {
            pushToast({
                title: 'No Scan Loaded',
                message: 'Run a scan first, then save the current version.',
                type: 'info',
                domain: 'Sports',
            });
            return;
        }
        const currentStats: ScanStats = stats || {
            trending_players: 0,
            total_scanned: results.length,
            plays_found: results.filter(r => r.is_play).length,
            scan_scope: scanScope,
            games_queried: 0,
        };
        const ts = saveScan(results, currentStats, sport);
        setLastScanTime(ts);
        const saved = await persistScanVersion(results, currentStats);
        if (saved?.id) {
            pushToast({
                title: 'Scan Saved',
                message: `Current scan saved to Last Scans (${formatDateTime(new Date(saved.ts).toISOString())}).`,
                type: 'success',
                domain: 'Sports',
            });
        } else {
            pushToast({
                title: 'Save Failed',
                message: 'Could not save current scan version.',
                type: 'error',
                domain: 'Sports',
            });
        }
    }

    async function loadScanVersion(versionId: string, closePanel: boolean = true, silent: boolean = false) {
        setHistoryLoading(true);
        setHistoryError('');
        try {
            const res = await fetch(`/api/v1/dfs/scan-history/${versionId}`);
            if (!res.ok) throw new Error(await res.text());
            const detail: ScanHistoryDetail = await res.json();
            const rows = Array.isArray(detail.results) ? detail.results : [];
            const nextStats: ScanStats = {
                trending_players: Number(detail?.stats?.trending_players || 0),
                total_scanned: Number(detail?.stats?.total_scanned || rows.length),
                plays_found: Number(detail?.stats?.plays_found || 0),
                scan_scope: detail.scan_scope === 'full' ? 'full' : 'smart',
                games_queried: Number(detail?.stats?.games_queried || 0),
            };
            setResults(rows);
            setStats(nextStats);
            setSport(detail.sport || sport);
            setScanScope(detail.scan_scope === 'full' ? 'full' : 'smart');
            setCurrentPage(1);
            setError('');
            const selectedTs = Number(detail.ts || 0);
            if (selectedTs > 0) {
                const iso = new Date(selectedTs).toISOString();
                setLastScanTime(iso);
            }
            saveScan(rows, nextStats, detail.sport || sport);

            const nextSlip = Array.isArray(detail.slip) ? detail.slip : [];
            setSlip(nextSlip);
            const nextLockedKeys = Array.isArray(detail.locked_keys) ? detail.locked_keys : [];
            setLockedPicks(lockMapFromKeys(nextLockedKeys));
            if (!nextSlip.length) setSlipExpanded(false);

            setSelectedScanVersionId(versionId);
            try {
                sessionStorage.setItem(SELECTED_SCAN_VERSION_KEY, versionId);
            } catch {
                // ignore
            }
            if (closePanel) setHistoryOpen(false);
            if (!silent) {
                pushToast({
                    title: 'Loaded Scan Version',
                    message: `Loaded ${detail.sport?.toUpperCase?.() || 'DFS'} scan from ${formatDateTime(new Date(Number(detail.ts || Date.now())).toISOString())}`,
                    type: 'info',
                    domain: 'Sports',
                });
            }
        } catch (e: any) {
            setHistoryError(e?.message || 'Failed to load scan version');
        } finally {
            setHistoryLoading(false);
        }
    }

    async function removeScanVersion(versionId: string) {
        if (!versionId) return;
        setHistoryDeletingId(versionId);
        setHistoryError('');
        try {
            const res = await fetch(`/api/v1/dfs/scan-history/${versionId}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await res.text());

            setScanHistory(prev => prev.filter(v => v.id !== versionId));
            if (selectedScanVersionId === versionId) {
                setSelectedScanVersionId(null);
                try {
                    sessionStorage.removeItem(SELECTED_SCAN_VERSION_KEY);
                } catch {
                    // ignore
                }
            }

            pushToast({
                title: 'Scan Removed',
                message: 'Saved scan version removed from Last Scans.',
                type: 'info',
                domain: 'Sports',
            });
        } catch (e: any) {
            setHistoryError(e?.message || 'Failed to remove scan version');
        } finally {
            setHistoryDeletingId(null);
        }
    }

    async function runScan() {
        setLoading(true);
        setError('');
        try {
            const res = await fetch('/api/v1/dfs/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sport,
                    scope: scanScope,
                    trending_limit: scanScope === 'smart' ? trendingLimit : 0,
                    max_games: maxGames,
                    target_platform: targetPlatform,
                    sleeper_markets_only: sleeperMarketsOnly,
                    consensus_min_books: consensusMinBooks,
                    consensus_line_window: consensusLineWindow,
                    consensus_main_line_only: consensusMainLineOnly,
                    consensus_min_trend_count: consensusMinTrendCount,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            const opps = data.opportunities || [];
            const scanStats: ScanStats = {
                trending_players: Number(data.trending_players || 0),
                total_scanned: Number(data.total_scanned || 0),
                plays_found: Number(data.plays_found || 0),
                scan_scope: data.scan_scope === 'full' ? 'full' : 'smart',
                games_queried: Number(data.games_queried || 0),
            };
            setResults(opps);
            setStats(scanStats);
            setCurrentPage(1);
            const ts = saveScan(opps, scanStats, sport);
            setLastScanTime(ts);
            await persistScanVersion(opps, scanStats);
            if (data.message && !opps.length) setError(data.message);
        } catch (e: any) {
            setError(e.message || 'Scan failed');
        }
        setLoading(false);
    }

    // ── Slip Builder Logic ──
    function addToSlip(result: ScanResult) {
        if (slip.length >= 6) return; // Sleeper max
        if (slip.some(s => s.player_name === result.player_name)) return; // No dupes
        if (!isSleeperCompatibleRow(result)) {
            pushToast({
                title: 'Unavailable on Sleeper',
                message: 'This line is not Sleeper-compatible and cannot be added to the slip.',
                type: 'info',
                domain: 'Sports',
            });
            return;
        }
        setSlip(prev => [...prev, result]);
    }

    function removeFromSlip(playerName: string) {
        setSlip(prev => prev.filter(s => s.player_name !== playerName));
    }

    function clearSlip() {
        setSlip([]);
        setSlipStats(null);
        setLockedPicks({});
    }

    function applyPreset(next: CalcPreset) {
        setPreset(next);
        if (next === 'safe') {
            setMinEdge(2.5);
            setShowPlaysOnly(true);
            setCalcProfile(prev => ({
                ...prev,
                edgeWeight: 1.5,
                confidenceWeight: 1.6,
                stakeWeight: 0.8,
                useDevig: true,
                useConfidenceShrink: true,
                useVigPenalty: true,
                useTrendBonus: false,
                useKellyCap: true,
                useCorrelationPenalty: true,
            }));
        } else if (next === 'balanced') {
            setMinEdge(1.2);
            setShowPlaysOnly(true);
            setCalcProfile(prev => ({
                ...prev,
                edgeWeight: 1.8,
                confidenceWeight: 1.2,
                stakeWeight: 1.0,
                useDevig: true,
                useConfidenceShrink: true,
                useVigPenalty: true,
                useTrendBonus: true,
                useKellyCap: true,
                useCorrelationPenalty: true,
            }));
        } else {
            setMinEdge(0);
            setShowPlaysOnly(false);
            setCalcProfile(prev => ({
                ...prev,
                edgeWeight: 2.4,
                confidenceWeight: 0.8,
                stakeWeight: 1.2,
                useDevig: false,
                useConfidenceShrink: false,
                useVigPenalty: false,
                useTrendBonus: true,
                useKellyCap: false,
                useCorrelationPenalty: false,
            }));
        }
    }

    function isInSlip(playerName: string) {
        return slip.some(s => s.player_name === playerName);
    }

    function togglePickLock(pick: ScanResult) {
        const key = rowKey(pick);
        setLockedPicks(prev => ({ ...prev, [key]: !prev[key] }));
    }

    async function optimizeSlips() {
        setOptimizingSlips(true);
        try {
            const opportunities = filtered
                .filter((r) => isSleeperCompatibleRow(r))
                .map((r) => ({
                player_name: r.player_name,
                market: r.market,
                line: r.line,
                side: r.side,
                sharp_odds: r.sharp_odds,
                edge_pct: r.edge_pct,
                books_used: r.books_used,
                weight_coverage_pct: r.weight_coverage_pct,
                book_odds: r.book_odds,
                fair_prob: r.fair_prob,
                fixed_implied_prob: r.fixed_implied_prob,
                available_on_sleeper_compatible: r.available_on_sleeper_compatible,
                eligible_for_slip: r.eligible_for_slip,
            }));
            if (opportunities.length < 2) {
                pushToast({
                    title: 'Not Enough Sleeper Rows',
                    message: 'Need at least 2 Sleeper-compatible rows to optimize combos.',
                    type: 'info',
                    domain: 'Sports',
                });
                setRecommendedSlips([]);
                return;
            }
            const res = await fetch('/api/v1/dfs/generate-slips', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    opportunities,
                    slip_sizes: [2, 3, 4, 5],
                    top_n: 5,
                    min_edge: Math.max(0, minEdge),
                    book: 'sleeper',
                    mode: 'power',
                    sport,
                }),
            });
            if (!res.ok) throw new Error('Optimize failed');
            const data = await res.json();
            const slips = Array.isArray(data.slips) ? data.slips.slice(0, 3) : [];
            setRecommendedSlips(slips);
            if (slips.length > 0) {
                pushToast({
                    title: 'Combos Optimized',
                    message: `Optimized combos were added at the bottom of the page (${slips.length})`,
                    type: 'success',
                    domain: 'Sports',
                });
            } else {
                pushToast({
                    title: 'No Combos Added',
                    message: 'Optimization completed, but no valid combos were found.',
                    type: 'info',
                    domain: 'Sports',
                });
            }
        } catch {
            setRecommendedSlips([]);
            pushToast({
                title: 'Optimize Failed',
                message: 'Could not optimize combos from current scan results.',
                type: 'error',
                domain: 'Sports',
            });
        } finally {
            setOptimizingSlips(false);
        }
    }

    function applyRecommendedSlip(slipCandidate: any) {
        const picksRaw = Array.isArray(slipCandidate?.players)
            ? slipCandidate.players
            : Array.isArray(slipCandidate?.picks)
                ? slipCandidate.picks
                : [];
        if (!picksRaw.length) return;
        const mapped: ScanResult[] = picksRaw
            .map((p: any) =>
                filtered.find((r) =>
                    r.player_name === p.player_name &&
                    r.market === p.market &&
                    Number(r.line) === Number(p.line)
                )
            )
            .filter((v: ScanResult | undefined): v is ScanResult => !!v);
        const sleeperOnly = mapped.filter((r) => isSleeperCompatibleRow(r));
        if (!sleeperOnly.length) {
            pushToast({
                title: 'No Sleeper-Compatible Picks',
                message: 'Recommended combo did not contain Sleeper-compatible rows.',
                type: 'info',
                domain: 'Sports',
            });
            return;
        }
        const locked = slip.filter(p => lockedPicks[rowKey(p)]);
        const lockedKeys = new Set(locked.map(rowKey));
        const combined = [...locked, ...sleeperOnly.filter(p => !lockedKeys.has(rowKey(p)))];
        setSlip(combined.slice(0, 6));
    }

    // Calculate slip EV when slip changes
    const calcSlipEV = useCallback(async () => {
        if (slip.length < 2) {
            setSlipStats(null);
            return;
        }
        setSlipLoading(true);
        try {
            const res = await fetch('/api/v1/dfs/manual-slip-ev', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ picks: slip, platform: 'sleeper' }),
            });
            const data = await res.json();
            setSlipStats(data);
        } catch {
            setSlipStats(null);
        }
        setSlipLoading(false);
    }, [slip]);

    useEffect(() => { calcSlipEV(); }, [calcSlipEV]);

    // Auto-expand when user adds the second pick (1 -> 2 transition).
    useEffect(() => {
        const prevLen = prevSlipLenRef.current;
        if (slip.length === 0) {
            setSlipExpanded(false);
        }
        if (prevLen < 2 && slip.length >= 2) {
            setSlipExpanded(true);
        }
        prevSlipLenRef.current = slip.length;
    }, [slip.length]);

    useEffect(() => {
        sessionStorage.setItem(SLIP_KEY, JSON.stringify(slip));
    }, [slip]);

    useEffect(() => {
        const keys = Object.keys(lockedPicks).filter(k => lockedPicks[k]);
        sessionStorage.setItem('dfs_scan_locks', JSON.stringify(keys));
    }, [lockedPicks]);

    useEffect(() => {
        const live = new Set(slip.map(rowKey));
        setLockedPicks(prev => {
            const next: Record<string, boolean> = {};
            Object.keys(prev).forEach(k => {
                if (prev[k] && live.has(k)) next[k] = true;
            });
            return next;
        });
    }, [slip]);

    useEffect(() => {
        sessionStorage.setItem(
            PREFS_KEY,
            JSON.stringify({
                sport,
                scanScope,
                maxGames,
                trendingLimit,
                minEdge,
                playerSearch,
                selectedProps,
                selectedGame,
                trendingOnly,
                minBooksFilter,
                showPlaysOnly,
                sideFilter,
                targetPlatform,
                sleeperMarketsOnly,
                consensusMinBooks,
                consensusLineWindow,
                consensusMainLineOnly,
                consensusMinTrendCount,
            })
        );
    }, [sport, scanScope, maxGames, trendingLimit, minEdge, playerSearch, selectedProps, selectedGame, trendingOnly, minBooksFilter, showPlaysOnly, sideFilter, targetPlatform, sleeperMarketsOnly, consensusMinBooks, consensusLineWindow, consensusMainLineOnly, consensusMinTrendCount]);

    useEffect(() => {
        saveCalcProfile('dfs', calcProfile);
    }, [calcProfile]);

    useEffect(() => {
        async function loadDfsSettings() {
            try {
                const persistedProfile = await loadProfileFromSettings('dfs');
                if (persistedProfile) setCalcProfile(persistedProfile);
                const res = await fetch('/api/v1/settings');
                if (!res.ok) return;
                const data = await res.json();
                const cfg = data?.config;
                applyDfsSettings(cfg);
                const kf = Number(cfg?.dfs?.ev_calculator?.kelly_fraction_cap);
                const ds = Number(cfg?.dfs?.ev_calculator?.default_stake);
                if (!Number.isNaN(ds) && ds > 0) setDefaultStake(ds);
                if (!sessionStorage.getItem('apex_calc_profile_dfs') && !Number.isNaN(kf) && kf > 0) {
                    setCalcProfile(prev => ({ ...prev, kellyCapPct: kf * 100 }));
                }
            } catch {
                // silent
            }
        }
        loadDfsSettings();
    }, []);

    useEffect(() => {
        const onSettingsUpdated = (e: Event) => {
            const cfg = (e as CustomEvent).detail;
            if (!cfg) return;
            applyDfsSettings(cfg);
            const p = cfg?.dfs?.calc_profile;
            if (p) {
                setCalcProfile(prev => ({
                    ...prev,
                    ...p,
                }));
            }
        };
        window.addEventListener('apex:settings-updated', onSettingsUpdated);
        return () => window.removeEventListener('apex:settings-updated', onSettingsUpdated);
    }, []);

    useEffect(() => {
        refreshScanHistory(false);
        try {
            const selected = sessionStorage.getItem(SELECTED_SCAN_VERSION_KEY);
            if (selected && selected.trim()) {
                setSelectedScanVersionId(selected);
                loadScanVersion(selected, false, true);
            }
        } catch {
            // ignore
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const propOptions = useMemo(() => {
        const seen = new Set<string>();
        const out: Array<{ key: string; label: string }> = [];
        for (const row of results) {
            const key = String(row.market || '');
            if (!key || seen.has(key)) continue;
            seen.add(key);
            out.push({ key, label: formatMarket(key) });
        }
        return out.sort((a, b) => a.label.localeCompare(b.label));
    }, [results]);
    const gameOptions = useMemo(() => {
        const map = new Map<string, string>();
        for (const row of results) {
            const key = gameFilterKey(row);
            if (!map.has(key)) map.set(key, gameFilterLabel(row));
        }
        return Array.from(map.entries())
            .map(([key, label]) => ({ key, label }))
            .sort((a, b) => a.label.localeCompare(b.label));
    }, [results]);
    const selectedPropsSet = useMemo(() => new Set(selectedProps), [selectedProps]);

    useEffect(() => {
        setSelectedProps(prev => prev.filter(p => propOptions.some(o => o.key === p)));
    }, [propOptions]);

    useEffect(() => {
        if (selectedGame === 'all') return;
        if (!gameOptions.some(g => g.key === selectedGame)) setSelectedGame('all');
    }, [gameOptions, selectedGame]);

    const filtered = useMemo(() => {
        const playerNeedle = playerSearch.trim().toLowerCase();
        return results.filter(r => {
            if (showPlaysOnly && !r.is_play) return false;
            if (trendingOnly && !r.is_trending) return false;
            if (r.edge_pct < minEdge) return false;
            if (sideFilter !== 'all' && (r.side || 'over') !== sideFilter) return false;
            if (targetPlatform === 'sleeper' && !isSleeperCompatibleRow(r)) return false;
            if (targetPlatform === 'prizepicks' && !r.available_on_prizepicks_direct) return false;
            if (targetPlatform === 'underdog' && !r.available_on_underdog_direct) return false;
            if (playerNeedle && !String(r.player_name || '').toLowerCase().includes(playerNeedle)) return false;
            if (selectedPropsSet.size > 0 && !selectedPropsSet.has(String(r.market || ''))) return false;
            if (selectedGame !== 'all' && gameFilterKey(r) !== selectedGame) return false;
            const booksUsed = Number.isFinite(Number(r.books_used))
                ? Math.max(1, Number(r.books_used))
                : (Array.isArray(r.book_odds) && r.book_odds.length ? r.book_odds.length : 1);
            if (minBooksFilter > 1 && booksUsed < minBooksFilter) return false;
            return true;
        });
    }, [results, showPlaysOnly, trendingOnly, minEdge, sideFilter, targetPlatform, playerSearch, selectedPropsSet, selectedGame, minBooksFilter]);

    useEffect(() => {
        setCurrentPage(1);
    }, [showPlaysOnly, trendingOnly, minEdge, sideFilter, targetPlatform, playerSearch, selectedProps, selectedGame, minBooksFilter]);
    const oddsByPropKey = useMemo(() => {
        const map: Record<string, Array<{ book: string; odds: number }>> = {};
        for (const row of results) {
            const k = propBookKey(row);
            const bucket = map[k] || [];
            const source = Array.isArray(row.book_odds) && row.book_odds.length
                ? row.book_odds
                : [{ book: row.sharp_book || 'unknown', odds: row.sharp_odds }];
            for (const b of source) {
                const book = String(b?.book || row.sharp_book || 'unknown');
                const odds = Number(b?.odds ?? row.sharp_odds);
                if (!Number.isFinite(odds)) continue;
                if (!bucket.some(v => normalizeBookName(v.book).toLowerCase() === normalizeBookName(book).toLowerCase())) {
                    bucket.push({ book, odds });
                }
            }
            map[k] = bucket.sort((a, b) => Number(b.odds) - Number(a.odds));
        }
        return map;
    }, [results]);
    const rowAiScore = useCallback((r: ScanResult) => {
        const books = oddsByPropKey[propBookKey(r)] || [];
        const odds = books.map(b => Number(b.odds)).filter(v => Number.isFinite(v));
        const spread = odds.length > 1 ? Math.max(...odds) - Math.min(...odds) : 0;
        return aiScore(r, calcProfile, correlationPenaltyForPick(r, slip), Math.max(1, books.length), spread);
    }, [oddsByPropKey, calcProfile, slip]);
    const displayRows = useMemo(
        () => autoSortPlayScore
            ? [...filtered].sort((a, b) => rowAiScore(b) - rowAiScore(a))
            : filtered,
        [filtered, autoSortPlayScore, rowAiScore],
    );
    const playerGroups = useMemo(() => {
        const groups: Array<{
            key: string;
            player_name: string;
            rows: ScanResult[];
            best_edge: number;
            best_ai: number;
            plays_found: number;
            is_trending: boolean;
            representative: ScanResult | null;
            synthetic_odds: number;
        }> = [];
        const map = new Map<string, ScanResult[]>();
        for (const row of displayRows) {
            const k = playerKey(row.player_name);
            map.set(k, [...(map.get(k) || []), row]);
        }
        for (const [k, rows] of map.entries()) {
            const bestEdge = rows.reduce((mx, r) => Math.max(mx, r.edge_pct), Number.NEGATIVE_INFINITY);
            const bestAi = rows.reduce((mx, r) => Math.max(mx, rowAiScore(r)), Number.NEGATIVE_INFINITY);
            const playsFound = rows.filter(r => r.is_play).length;
            const representative = [...rows].sort((a, b) => rowAiScore(b) - rowAiScore(a))[0] || null;
            const repBooks = representative ? (oddsByPropKey[propBookKey(representative)] || []) : [];
            const syntheticOdds = median(repBooks.map(b => Number(b.odds)).filter(v => Number.isFinite(v)));
            groups.push({
                key: k,
                player_name: rows[0].player_name,
                rows,
                best_edge: Number.isFinite(bestEdge) ? bestEdge : 0,
                best_ai: Number.isFinite(bestAi) ? bestAi : 0,
                plays_found: playsFound,
                is_trending: rows.some(r => r.is_trending),
                representative,
                synthetic_odds: Number.isFinite(syntheticOdds) ? syntheticOdds : 0,
            });
        }
        return groups.sort((a, b) => {
            if (autoSortPlayScore) return b.best_ai - a.best_ai;
            return b.best_edge - a.best_edge;
        });
    }, [displayRows, autoSortPlayScore, rowAiScore, oddsByPropKey]);
    const totalPages = useMemo(
        () => Math.max(1, Math.ceil(playerGroups.length / PLAYER_GROUPS_PER_PAGE)),
        [playerGroups.length],
    );
    const visiblePlayerGroups = useMemo(() => {
        const clampedPage = Math.max(1, Math.min(currentPage, totalPages));
        const start = (clampedPage - 1) * PLAYER_GROUPS_PER_PAGE;
        return playerGroups.slice(start, start + PLAYER_GROUPS_PER_PAGE);
    }, [playerGroups, currentPage, totalPages]);
    const paginationItems = useMemo<Array<number | string>>(() => {
        if (totalPages <= 7) {
            return Array.from({ length: totalPages }, (_, idx) => idx + 1);
        }
        const items: Array<number | string> = [1];
        let start = Math.max(2, currentPage - 1);
        let end = Math.min(totalPages - 1, currentPage + 1);
        if (currentPage <= 3) end = 4;
        if (currentPage >= totalPages - 2) start = totalPages - 3;
        if (start > 2) items.push('ellipsis-start');
        for (let page = start; page <= end; page += 1) items.push(page);
        if (end < totalPages - 1) items.push('ellipsis-end');
        items.push(totalPages);
        return items;
    }, [currentPage, totalPages]);
    const slipWarnings = correlationWarningsForSlip(slip);

    useEffect(() => {
        setCurrentPage(prev => {
            if (prev < 1) return 1;
            if (prev > totalPages) return totalPages;
            return prev;
        });
    }, [totalPages]);

    useEffect(() => {
        const valid = new Set(playerGroups.map(g => g.key));
        setExpandedPlayers(prev => {
            const next: Record<string, boolean> = {};
            Object.keys(prev).forEach(k => {
                if (prev[k] && valid.has(k)) next[k] = true;
            });
            return next;
        });
        setPlayerRenderLimit(prev => {
            const next: Record<string, number> = {};
            Object.keys(prev).forEach(k => {
                if (valid.has(k)) next[k] = prev[k];
            });
            return next;
        });
    }, [playerGroups]);

    function toggleRowDetails(r: ScanResult) {
        const key = rowKey(r);
        setExpandedRows(prev => {
            const nextOpen = !prev[key];
            const next = { ...prev, [key]: nextOpen };
            if (nextOpen && !evCalcByKey[key] && !evLoadingByKey[key]) {
                setEvLoadingByKey(s => ({ ...s, [key]: true }));
                const conf = confidenceScore(r, calcProfile);
                const probability = ((calcProfile.useDevig && r.fair_prob != null) ? r.fair_prob : r.sharp_implied_prob) / 100;
                fetch('/api/v1/dfs/ev-calculator', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        odds: r.sharp_odds,
                        probability,
                        stake: defaultStake,
                        opposing_odds: r.opposing_odds ?? undefined,
                        probability_confidence: Math.max(0, Math.min(1, conf / 100)),
                    }),
                })
                    .then(async (res) => {
                        if (!res.ok) return null;
                        return await res.json();
                    })
                    .then((data) => {
                        if (!data) return;
                        setEvCalcByKey(s => ({
                            ...s,
                            [key]: {
                                ev_percent: Number(data.ev_percent || 0),
                                kelly_fraction: Number(data.kelly_fraction || 0),
                                kelly_stake: Number(data.kelly_stake || 0),
                                blended_probability: Number(data.blended_probability || 0),
                                fair_prob: data.fair_prob ?? null,
                                vig_pct: data.vig_pct ?? null,
                            },
                        }));
                    })
                    .finally(() => {
                        setEvLoadingByKey(s => ({ ...s, [key]: false }));
                    });
            }
            return next;
        });
    }

    function togglePlayerDetails(playerName: string) {
        const key = playerKey(playerName);
        setExpandedPlayers(prev => ({ ...prev, [key]: !prev[key] }));
        setPlayerRenderLimit(prev => ({ ...prev, [key]: prev[key] || 24 }));
    }

    const renderPagination = (position: 'top' | 'bottom') => (
        <div className={`results-pagination results-pagination-${position}`}>
            <div className="pagination-meta">
                <span>Page {currentPage} of {totalPages}</span>
                <span>{playerGroups.length} players</span>
            </div>
            <div className="pagination-controls">
                <button
                    type="button"
                    className="pagination-btn"
                    onClick={() => setCurrentPage(1)}
                    disabled={currentPage <= 1}
                >
                    First
                </button>
                <button
                    type="button"
                    className="pagination-btn"
                    onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                >
                    Prev
                </button>
                {paginationItems.map((item, idx) => (
                    typeof item === 'number' ? (
                        <button
                            key={`page-${item}`}
                            type="button"
                            className={`pagination-btn pagination-number ${item === currentPage ? 'active' : ''}`}
                            onClick={() => setCurrentPage(item)}
                            aria-label={`Go to page ${item}`}
                            aria-current={item === currentPage ? 'page' : undefined}
                        >
                            {item}
                        </button>
                    ) : (
                        <span key={`${item}-${idx}`} className="pagination-ellipsis">…</span>
                    )
                ))}
                <button
                    type="button"
                    className="pagination-btn"
                    onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                    disabled={currentPage >= totalPages}
                >
                    Next
                </button>
                <button
                    type="button"
                    className="pagination-btn"
                    onClick={() => setCurrentPage(totalPages)}
                    disabled={currentPage >= totalPages}
                >
                    Last
                </button>
            </div>
        </div>
    );

    useEffect(() => {
        if (slip.length < 2) {
            setCorrelationEv(null);
            return;
        }
        const legs = slip.map(p => ({
            probability: ((calcProfile.useDevig && p.fair_prob != null) ? p.fair_prob : p.sharp_implied_prob) / 100,
            odds: p.sharp_odds,
            stat: p.market,
        }));
        fetch('/api/v1/dfs/correlation/parlay-ev', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(legs),
        })
            .then(async (res) => {
                if (!res.ok) return null;
                return await res.json();
            })
            .then((data) => {
                if (!data) return;
                setCorrelationEv({
                    ev_percent: Number(data.ev_percent || 0),
                    recommendation: String(data.recommendation || 'SKIP'),
                });
            })
            .catch(() => setCorrelationEv(null));
    }, [slip, calcProfile.useDevig]);

    useEffect(() => {
        if (!calcOpen) return;
        const onEsc = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setCalcOpen(false);
        };
        window.addEventListener('keydown', onEsc);
        return () => window.removeEventListener('keydown', onEsc);
    }, [calcOpen]);

    return (
        <div className="dfs-scan-page">
            <div className="scan-main">
                {/* Left: Scanner */}
                <div className="scan-left">
                    <div className="scan-header">
                        <div className="scan-header-top">
                            <h1>
                                <Search size={24} /> DFS Scanner
                                <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open quick settings">
                                    <SlidersHorizontal size={14} />
                                </button>
                            </h1>
                            <div className="scan-header-actions">
                                <button
                                    type="button"
                                    className="scan-save-btn"
                                    onClick={saveCurrentScanSnapshot}
                                    disabled={loading || historySaving || results.length === 0}
                                >
                                    <Save size={14} />
                                    {historySaving ? 'Saving…' : 'Save Current Scan'}
                                </button>
                                <button
                                    type="button"
                                    className={`scan-history-btn ${historyOpen ? 'active' : ''}`}
                                    onClick={() => {
                                        setHistoryOpen(prev => !prev);
                                        if (!historyOpen) refreshScanHistory();
                                    }}
                                >
                                    <History size={14} />
                                    View Last Scans
                                </button>
                            </div>
                        </div>
                        <p className="subtitle">
                            {scanScope === 'full'
                                ? 'Full market player-prop scan with consensus ranking'
                                : 'Targeted trending-player scan with consensus ranking'}
                        </p>
                        {lastScanTime && (
                            <p className="last-scan-time">
                                <Clock size={12} />
                                Last scanned: {formatDateTime(lastScanTime)}
                            </p>
                        )}
                    </div>
                    {historyOpen && (
                        <div className="scan-history-popover">
                            <div className="scan-history-head">
                                <span className="scan-history-title">Recent Scan Versions</span>
                                <button
                                    type="button"
                                    className="scan-history-close"
                                    onClick={() => setHistoryOpen(false)}
                                    aria-label="Close scan history"
                                >
                                    <X size={14} />
                                </button>
                            </div>
                            {historyError && <div className="scan-history-error">{historyError}</div>}
                            {historyLoading && (
                                <div className="scan-history-loading">
                                    <Loader2 size={14} className="spin" /> Loading scan history...
                                </div>
                            )}
                            {!historyLoading && scanHistory.length === 0 && !historyError && (
                                <div className="scan-history-empty">No saved scan versions yet.</div>
                            )}
                            {!historyLoading && scanHistory.length > 0 && (
                                <div className="scan-history-list">
                                    {scanHistory.map((item) => (
                                        <div key={item.id} className="scan-history-item-row">
                                            <button
                                                type="button"
                                                className={`scan-history-item ${selectedScanVersionId === item.id ? 'active' : ''}`}
                                                onClick={() => loadScanVersion(item.id, true)}
                                            >
                                                <div className="scan-history-item-top">
                                                    <span>{item.sport.toUpperCase()} · {item.scan_scope === 'full' ? 'Full Market' : 'Targeted'}</span>
                                                    <span>{formatDateTime(new Date(item.ts).toISOString())}</span>
                                                </div>
                                                <div className="scan-history-item-meta">
                                                    <span>{item.total_scanned} scanned</span>
                                                    <span>{item.plays_found} plays</span>
                                                    <span>{item.games_queried || 0} games</span>
                                                    <span>{item.slip_count || 0} slip picks</span>
                                                </div>
                                            </button>
                                            <button
                                                type="button"
                                                className="scan-history-remove"
                                                onClick={() => removeScanVersion(item.id)}
                                                disabled={historyDeletingId !== null}
                                                aria-label={`Remove scan from ${formatDateTime(new Date(item.ts).toISOString())}`}
                                            >
                                                {historyDeletingId === item.id ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
                                                {historyDeletingId === item.id ? 'Removing…' : 'Remove'}
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    <CalcProfilePopover
                        open={calcOpen}
                        onClose={() => setCalcOpen(false)}
                        title="DFS Calculation Profile"
                        domain="dfs"
                        preset={preset}
                        profile={calcProfile}
                        onPresetChange={applyPreset}
                        onProfileChange={(next) => setCalcProfile(next as any)}
                    />

                    <div className="scan-controls">
                        {/* Sport Segmented Control */}
                        <div className="sport-segmented">
                            {[
                                { key: 'nba', icon: Volleyball, label: 'NBA' },
                                { key: 'nfl', icon: Shield, label: 'NFL' },
                                { key: 'mlb', icon: CircleDot, label: 'MLB' },
                                { key: 'soccer', icon: Goal, label: 'Soccer' },
                            ].map(s => (
                                <button
                                    key={s.key}
                                    className={`seg-item ${sport === s.key ? 'active' : ''}`}
                                    onClick={() => setSport(s.key)}
                                >
                                    <span className="seg-icon"><s.icon size={14} /></span>
                                    <span className="seg-label">{s.label}</span>
                                </button>
                            ))}
                        </div>

                        <div className="sport-segmented">
                            {[
                                { key: 'smart', label: 'Targeted' },
                                { key: 'full', label: 'Full Market' },
                            ].map((s) => (
                                <button
                                    key={s.key}
                                    className={`seg-item ${scanScope === s.key ? 'active' : ''}`}
                                    onClick={() => setScanScope(s.key as ScanScope)}
                                >
                                    <span className="seg-label">{s.label}</span>
                                </button>
                            ))}
                        </div>

                        {/* Filter Bar */}
                        <div className="filter-bar">
                            <div className="filter-item">
                                <span className="filter-label">Games</span>
                                <div className="input-modern">
                                    <input
                                        type="number"
                                        min={1}
                                        max={20}
                                        step={1}
                                        value={maxGames}
                                        onChange={e => {
                                            const next = Number(e.target.value);
                                            if (!Number.isFinite(next)) return;
                                            setMaxGames(Math.max(1, Math.min(20, Math.trunc(next))));
                                        }}
                                    />
                                </div>
                            </div>

                            {scanScope === 'smart' && (
                                <>
                                    <div className="filter-divider" />
                                    <div className="filter-item">
                                        <span className="filter-label">Trending Limit</span>
                                        <div className="input-modern">
                                            <input
                                                type="number"
                                                min={1}
                                                max={200}
                                                step={1}
                                                value={trendingLimit}
                                                onChange={e => {
                                                    const next = Number(e.target.value);
                                                    if (!Number.isFinite(next)) return;
                                                    setTrendingLimit(Math.max(1, Math.min(200, Math.trunc(next))));
                                                }}
                                            />
                                        </div>
                                    </div>
                                </>
                            )}

                            <div className="filter-divider" />

                            <div className="filter-item filter-item-wide">
                                <span className="filter-label">Player</span>
                                <input
                                    type="text"
                                    className="filter-text-input"
                                    value={playerSearch}
                                    onChange={e => setPlayerSearch(e.target.value)}
                                    placeholder="Type player name..."
                                />
                            </div>

                            <div className="filter-item">
                                <span className="filter-label">Props</span>
                                <details className="filter-check-dropdown">
                                    <summary>
                                        {selectedProps.length > 0 ? `${selectedProps.length} selected` : 'All Props'}
                                    </summary>
                                    <div className="filter-check-menu">
                                        <div className="filter-check-actions">
                                            <button
                                                type="button"
                                                className="filter-check-clear"
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    setSelectedProps([]);
                                                }}
                                                disabled={selectedProps.length === 0}
                                            >
                                                Clear
                                            </button>
                                        </div>
                                        {propOptions.map((opt) => (
                                            <label key={opt.key} className="filter-check-item">
                                                <input
                                                    type="checkbox"
                                                    checked={selectedProps.includes(opt.key)}
                                                    onChange={() => togglePropSelection(opt.key)}
                                                />
                                                <span>{opt.label}</span>
                                            </label>
                                        ))}
                                        {propOptions.length === 0 && (
                                            <div className="filter-check-empty">No props from current scan</div>
                                        )}
                                    </div>
                                </details>
                            </div>

                            <div className="filter-item filter-item-wide">
                                <span className="filter-label">Game</span>
                                <select
                                    className="filter-select"
                                    value={selectedGame}
                                    onChange={e => setSelectedGame(e.target.value)}
                                >
                                    <option value="all">All Games</option>
                                    {gameOptions.map((opt) => (
                                        <option key={opt.key} value={opt.key}>{opt.label}</option>
                                    ))}
                                </select>
                            </div>

                            <div className="filter-item">
                                <span className="filter-label">Min Books</span>
                                <div className="input-modern">
                                    <input
                                        type="number"
                                        min={1}
                                        max={8}
                                        step={1}
                                        value={minBooksFilter}
                                        onChange={(e) => {
                                            const next = Number(e.target.value);
                                            if (!Number.isFinite(next)) return;
                                            setMinBooksFilter(Math.max(1, Math.min(8, Math.trunc(next))));
                                        }}
                                    />
                                </div>
                            </div>

                            <div className="filter-divider" />

                            <div className="filter-item">
                                <span className="filter-label">Min Edge</span>
                                <div className="input-modern">
                                    <input
                                        type="number"
                                        value={minEdgeInput}
                                        onChange={e => {
                                            const raw = e.target.value;
                                            setMinEdgeInput(raw);
                                            if (raw === '') return;
                                            const next = parseFloat(raw);
                                            if (!Number.isFinite(next)) return;
                                            setMinEdge(next);
                                        }}
                                        onBlur={() => {
                                            if (minEdgeInput.trim() === '') {
                                                setMinEdge(0);
                                                setMinEdgeInput('0');
                                            }
                                        }}
                                        min={0}
                                        max={20}
                                        step={0.5}
                                    />
                                    <span className="input-suffix">%</span>
                                </div>
                            </div>

                            <div className="filter-divider" />

                            <div className="filter-item">
                                <span className="filter-label">Side</span>
                                <div className="side-segmented">
                                    {[
                                        { key: 'all', label: 'All' },
                                        { key: 'over', label: '↑ Over' },
                                        { key: 'under', label: '↓ Under' },
                                    ].map(s => (
                                        <button key={s.key}
                                            className={`side-seg-item ${sideFilter === s.key ? 'active' : ''}`}
                                            onClick={() => setSideFilter(s.key as any)}
                                        >{s.label}</button>
                                    ))}
                                </div>
                            </div>

                            <div className="filter-item filter-item-wide">
                                <span className="filter-label">Platform</span>
                                <select
                                    className="filter-select"
                                    value={targetPlatform}
                                    onChange={(e) => setTargetPlatform(e.target.value as TargetPlatform)}
                                >
                                    <option value="sleeper">Sleeper</option>
                                    <option value="any">Any</option>
                                    <option value="prizepicks">PrizePicks (Direct)</option>
                                    <option value="underdog">Underdog (Direct)</option>
                                </select>
                            </div>

                            <div className="filter-divider" />

                            <label className="toggle-switch-label">
                                <div className={`toggle-switch ${showPlaysOnly ? 'on' : ''}`}
                                    onClick={() => setShowPlaysOnly(!showPlaysOnly)}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="filter-label">Plays Only</span>
                            </label>

                            <label className="toggle-switch-label">
                                <div className={`toggle-switch ${trendingOnly ? 'on' : ''}`}
                                    onClick={() => setTrendingOnly(!trendingOnly)}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="filter-label">Trending Only</span>
                            </label>

                            <label className="toggle-switch-label">
                                <div className={`toggle-switch ${autoSortPlayScore ? 'on' : ''}`}
                                    onClick={() => setAutoSortPlayScore(!autoSortPlayScore)}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="filter-label">Sort by AI</span>
                            </label>

                            <label className="toggle-switch-label">
                                <div className={`toggle-switch ${sleeperMarketsOnly ? 'on' : ''}`}
                                    onClick={() => setSleeperMarketsOnly(!sleeperMarketsOnly)}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="filter-label">Sleeper Compat</span>
                            </label>


                            <label className="toggle-switch-label" title="When OFF, shows only the main (median) line per player. When ON, shows every available line per player.">
                                <div className={`toggle-switch ${!consensusMainLineOnly ? 'on' : ''}`}
                                    onClick={() => setConsensusMainLineOnly(prev => !prev)}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="filter-label">All Lines</span>
                            </label>

                            <button className="btn-scan" onClick={runScan} disabled={loading}>
                                {loading ? <Loader2 size={16} className="spin" /> : <Zap size={16} />}
                                {loading
                                    ? (scanScope === 'full' ? 'Scanning Full Market...' : 'Scanning...')
                                    : (scanScope === 'full' ? 'Scan Full Market' : 'Scan Targeted')}
                            </button>
                            <button
                                className="btn-scan-secondary"
                                onClick={optimizeSlips}
                                disabled={loading || filtered.filter((r) => isSleeperCompatibleRow(r)).length < 2 || optimizingSlips}
                            >
                                {optimizingSlips ? 'Optimizing...' : 'Optimize Combos'}
                            </button>
                        </div>
                    </div>

                    <div className="slip-ribbon">
                        <div className="slip-ribbon-main">
                            <div className="slip-ribbon-left">
                                <span className="slip-ribbon-title"><Clipboard size={14} /> Draft Slip</span>
                                <span className="slip-ribbon-pill">{slip.length}/6 picks</span>
                                <span className="slip-ribbon-pill">Platform: Sleeper</span>
                                {slipStats?.valid && (
                                    <span className={`slip-ribbon-pill ${slipStats.expected_value_pct > 0 ? 'positive' : 'negative'}`}>
                                        EV {slipStats.expected_value_pct > 0 ? '+' : ''}{slipStats.expected_value_pct.toFixed(1)}%
                                    </span>
                                )}
                            </div>
                            {slipWarnings.length > 0 && (
                                <div className="slip-warning-banner">
                                    {slipWarnings[0]}
                                </div>
                            )}
                            <div className="slip-ribbon-actions">
                                {slip.length > 0 && (
                                    <button className="slip-ribbon-btn" onClick={() => setSlipExpanded(prev => !prev)}>
                                        {slipExpanded ? 'Collapse' : 'Expand'} <ChevronDown size={13} className={slipExpanded ? 'up' : ''} />
                                    </button>
                                )}
                                {slip.length > 0 && (
                                    <button className="slip-ribbon-btn danger" onClick={clearSlip}>
                                        Clear
                                    </button>
                                )}
                            </div>
                        </div>

                        {slipExpanded && (
                            <div className="slip-ribbon-body">
                                {slip.length === 0 ? (
                                    <div className="slip-inline-empty">Add players with <strong>+</strong> in the table.</div>
                                ) : (
                                    <div className="slip-inline-picks">
                                        {slip.map((pick, i) => (
                                            <div key={i} className="slip-inline-pick">
                                                <span className="pick-player">{pick.player_name}</span>
                                                <span className="pick-detail">{formatMarket(pick.market)} {(pick.side || 'over') === 'over' ? 'O' : 'U'} {pick.line}</span>
                                                <span className={pick.edge_pct > 0 ? 'positive' : 'negative'}>
                                                    {pick.edge_pct > 0 ? '+' : ''}{pick.edge_pct.toFixed(1)}%
                                                </span>
                                                <button
                                                    className={`btn-lock-pick ${lockedPicks[rowKey(pick)] ? 'locked' : ''}`}
                                                    onClick={() => togglePickLock(pick)}
                                                    title={lockedPicks[rowKey(pick)] ? 'Unlock pick' : 'Lock pick'}
                                                >
                                                    {lockedPicks[rowKey(pick)] ? <Lock size={12} /> : <Unlock size={12} />}
                                                </button>
                                                <button className="btn-remove-pick" onClick={() => removeFromSlip(pick.player_name)}>
                                                    <X size={12} />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                <div className="slip-stats-panel">
                                    {slip.length < 2 ? (
                                        <p className="slip-hint">Add {2 - slip.length} more pick{2 - slip.length > 1 ? 's' : ''} to see EV</p>
                                    ) : slipLoading ? (
                                        <div className="slip-loading"><Loader2 size={16} className="spin" /> Calculating...</div>
                                    ) : slipStats?.error ? (
                                        <div className="slip-error">{slipStats.error}</div>
                                    ) : slipStats?.valid ? (
                                        <div className="slip-metrics">
                                            <div className="slip-metric">
                                                <span className="metric-label">Payout</span>
                                                <span className="metric-value">{slipStats.payout_multiplier}x</span>
                                            </div>
                                            <div className="slip-metric">
                                                <span className="metric-label">Win Prob</span>
                                                <span className="metric-value">{slipStats.win_probability_pct.toFixed(1)}%</span>
                                            </div>
                                            <div className="slip-metric">
                                                <span className="metric-label">EV</span>
                                                <span className={`metric-value ${slipStats.expected_value_pct > 0 ? 'positive' : 'negative'}`}>
                                                    {slipStats.expected_value_pct > 0 ? '+' : ''}{slipStats.expected_value_pct.toFixed(1)}%
                                                </span>
                                            </div>
                                            <div className="slip-metric">
                                                <span className="metric-label">Edge</span>
                                                <span className={`metric-value ${slipStats.combined_edge_pct > 0 ? 'positive' : 'negative'}`}>
                                                    {slipStats.combined_edge_pct > 0 ? '+' : ''}{slipStats.combined_edge_pct.toFixed(1)}%
                                                </span>
                                            </div>
                                            {correlationEv && (
                                                <div className="slip-metric">
                                                    <span className="metric-label">Corr EV</span>
                                                    <span className={`metric-value ${correlationEv.ev_percent > 0 ? 'positive' : 'negative'}`}>
                                                        {correlationEv.ev_percent > 0 ? '+' : ''}{correlationEv.ev_percent.toFixed(1)}%
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    ) : null}
                                </div>
                            </div>
                        )}
                    </div>

                    {error && <div className="error-banner">{error}</div>}

                    {loading && (
                        <div className="loading-state">
                            <Loader2 size={32} className="spin" />
                            <p>
                                {scanScope === 'full'
                                    ? 'Scanning full market player props across selected games...'
                                    : 'Fetching trending players from Sleeper, then scanning sharp books...'}
                            </p>
                        </div>
                    )}

                    {stats && (
                        <div className="scan-stats">
                            <div className="stat-pill"><TrendingUp size={14} /> {stats.scan_scope === 'full' ? 'Full Market' : `${stats.trending_players} Trending`}</div>
                            <div className="stat-pill"><Clock size={14} /> {stats.games_queried || 0} Games</div>
                            <div className="stat-pill"><Search size={14} /> {stats.total_scanned} Scanned</div>
                            <div className="stat-pill play"><Zap size={14} /> {stats.plays_found} Plays</div>
                        </div>
                    )}

                    {filtered.length > 0 && (
                        <div className="card scan-results-card">
                            <div className="scan-results-head">
                                <span className="scan-results-title">Scanned Results</span>
                                <button
                                    type="button"
                                    className="slip-ribbon-btn"
                                    onClick={() => setTableCollapsed(v => !v)}
                                >
                                    {tableCollapsed ? 'Expand Table' : 'Collapse Table'}
                                </button>
                            </div>
                            {!tableCollapsed && (
                                <div className="results-table-wrapper">
                                    {renderPagination('top')}
                                    <table className="results-table">
                                        <thead>
                                            <tr>
                                                <th></th>
                                                <th>Game</th>
                                                <th>Player</th>
                                                <th>Apex Odds</th>
                                                <th>EDGE %</th>
                                                <th>Verdict</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {visiblePlayerGroups.map((group) => {
                                                const groupOpen = !!expandedPlayers[group.key];
                                                const renderLimit = playerRenderLimit[group.key] || 24;
                                                const visibleRows = group.rows.slice(0, renderLimit);
                                                const gameWhen = group.representative ? formatGameWhen(group.representative) : null;
                                                const gameMatchup = group.representative ? formatGameMatchup(group.representative) : null;
                                                return (
                                                    <Fragment key={`${group.key}-group`}>
                                                        <tr className={`${group.plays_found > 0 ? 'play-row' : ''} ${isInSlip(group.player_name) ? 'in-slip' : ''}`}>
                                                            <td>
                                                                <button
                                                                    type="button"
                                                                    className="player-expand-btn"
                                                                    onClick={() => togglePlayerDetails(group.player_name)}
                                                                    aria-label={groupOpen ? 'Collapse player rows' : 'Expand player rows'}
                                                                >
                                                                    <ChevronDown size={13} className={groupOpen ? 'up' : ''} />
                                                                </button>
                                                            </td>
                                                            <td>
                                                                <div className="game-when">{gameWhen || 'TBD'}</div>
                                                                {gameMatchup && <div className="game-matchup">{gameMatchup}</div>}
                                                            </td>
                                                            <td>
                                                                <div className="player-mainline">
                                                                    <span className="player-name">{group.player_name}</span>
                                                                    {group.is_trending && <span className="trending-badge">🔥</span>}
                                                                </div>
                                                            </td>
                                                            <td className={group.synthetic_odds < 0 ? 'negative' : 'positive'}>
                                                                {group.synthetic_odds > 0 ? '+' : ''}{group.synthetic_odds.toFixed(0)}
                                                            </td>
                                                            <td className={group.best_edge > 0 ? 'positive' : 'negative'}>
                                                                {group.best_edge > 0 ? '+' : ''}{group.best_edge.toFixed(2)}%
                                                            </td>
                                                            <td>
                                                                {group.plays_found > 0 ? (
                                                                    <span className="badge-play"><CheckCircle2 size={12} /> PLAY</span>
                                                                ) : (
                                                                    <span className="badge-pass"><MinusCircle size={12} /> PASS</span>
                                                                )}
                                                            </td>
                                                        </tr>
                                                        {groupOpen && (
                                                            <tr className="player-group-detail-row">
                                                                <td colSpan={6}>
                                                                    <div className="player-group-panel">
                                                                        <table className="player-prop-table">
                                                                            <thead>
                                                                                <tr>
                                                                                    <th></th>
                                                                                    <th>Prop</th>
                                                                                    <th>Side</th>
                                                                                    <th>Line</th>
                                                                                    <th>Apex Odds</th>
                                                                                    <th>Edge %</th>
                                                                                    <th>Verdict</th>
                                                                                </tr>
                                                                            </thead>
                                                                            <tbody>
                                                                                {visibleRows.map((r) => {
                                                                                    const k = rowKey(r);
                                                                                    const isExpanded = !!expandedRows[k];
                                                                                    const conf = confidenceScore(r, calcProfile);
                                                                                    const confTier = confidenceTier(conf);
                                                                                    const risk = riskTier(r);
                                                                                    const correlationPenalty = correlationPenaltyForPick(r, slip);
                                                                                    const booksForRow = oddsByPropKey[propBookKey(r)] || [];
                                                                                    const oddsSpread = booksForRow.length > 1
                                                                                        ? Math.max(...booksForRow.map(b => Number(b.odds))) - Math.min(...booksForRow.map(b => Number(b.odds)))
                                                                                        : 0;
                                                                                    const ai = aiScore(r, calcProfile, correlationPenalty, Math.max(1, booksForRow.length), oddsSpread);
                                                                                    const suggestedStake = suggestedStakePct(r, calcProfile);
                                                                                    const activeKellyCapPct = calcProfile.useKellyCap ? calcProfile.kellyCapPct : 100;
                                                                                    const sleeperCompatible = isSleeperCompatibleRow(r);
                                                                                    return (
                                                                                        <Fragment key={`${k}-wrap`}>
                                                                                            <tr className={`${r.is_play ? 'play-row' : ''} ${isInSlip(r.player_name) ? 'in-slip' : ''}`}>
                                                                                                <td>
                                                                                                    <button
                                                                                                        className={`btn-add-slip ${isInSlip(r.player_name) ? 'added' : ''} ${!sleeperCompatible && !isInSlip(r.player_name) ? 'disabled' : ''}`}
                                                                                                        onClick={() => {
                                                                                                            if (isInSlip(r.player_name)) {
                                                                                                                removeFromSlip(r.player_name);
                                                                                                                return;
                                                                                                            }
                                                                                                            if (!sleeperCompatible) return;
                                                                                                            addToSlip(r);
                                                                                                        }}
                                                                                                        disabled={!sleeperCompatible && !isInSlip(r.player_name)}
                                                                                                        title={isInSlip(r.player_name) ? 'Remove from slip' : sleeperCompatible ? 'Add to slip' : 'Unavailable on Sleeper'}
                                                                                                    >
                                                                                                        {isInSlip(r.player_name) ? <X size={14} /> : <Plus size={14} />}
                                                                                                    </button>
                                                                                                </td>
                                                                                                <td>
                                                                                                    <div className="prop-cell-wrap">
                                                                                                        <button
                                                                                                            type="button"
                                                                                                            className="edge-expand-btn"
                                                                                                            onClick={() => toggleRowDetails(r)}
                                                                                                            aria-label={isExpanded ? 'Collapse edge details' : 'Expand edge details'}
                                                                                                        >
                                                                                                            <ChevronDown size={13} className={isExpanded ? 'up' : ''} />
                                                                                                        </button>
                                                                                                        <span>{formatMarket(r.market)}</span>
                                                                                                    </div>
                                                                                                </td>
                                                                                                <td>
                                                                                                    <span className={`side-badge side-${r.side || 'over'}`}>
                                                                                                        {(r.side || 'over') === 'over'
                                                                                                            ? <><ArrowUp size={12} /> O</>
                                                                                                            : <><ArrowDown size={12} /> U</>}
                                                                                                    </span>
                                                                                                </td>
                                                                                                <td>{r.line}</td>
                                                                                                <td className={r.sharp_odds < 0 ? 'negative' : 'positive'}>
                                                                                                    {r.sharp_odds > 0 ? '+' : ''}{r.sharp_odds}
                                                                                                </td>
                                                                                                <td className={r.edge_pct > 0 ? 'positive' : 'negative'}>
                                                                                                    <span>{r.edge_pct > 0 ? '+' : ''}{r.edge_pct.toFixed(2)}%</span>
                                                                                                </td>
                                                                                                <td>
                                                                                                    {r.is_play ? (
                                                                                                        <span className="badge-play"><CheckCircle2 size={12} /> PLAY</span>
                                                                                                    ) : (
                                                                                                        <span className="badge-pass"><MinusCircle size={12} /> PASS</span>
                                                                                                    )}
                                                                                                </td>
                                                                                            </tr>
                                                                                            {isExpanded && (
                                                                                                <tr className="scan-detail-row">
                                                                                                    <td colSpan={7}>
                                                                                                        <div className="scan-detail-grid">
                                                                                                            <div className="detail-card">
                                                                                                                <div className="detail-title">Book + Odds</div>
                                                                                                                <div className="book-logo-grid">
                                                                                                                    {booksForRow.map((b) => {
                                                                                                                        const meta = bookBadge(b.book);
                                                                                                                        const logoKey = canonicalBookKey(b.book);
                                                                                                                        const PNG_BOOKS = new Set(['draftkings', 'fanduel']);
                                                                                                                        const logoSrc = PNG_BOOKS.has(logoKey)
                                                                                                                            ? `/book-logos/${logoKey}.png`
                                                                                                                            : `/book-logos/${logoKey}.svg`;
                                                                                                                        return (
                                                                                                                            <div className="book-logo-item" key={`${k}-${b.book}`}>
                                                                                                                                <img
                                                                                                                                    className="book-logo-img"
                                                                                                                                    src={logoSrc}
                                                                                                                                    alt={meta.label}
                                                                                                                                    onError={(e) => {
                                                                                                                                        const img = e.currentTarget as HTMLImageElement;
                                                                                                                                        if (img.src.endsWith('.png')) {
                                                                                                                                            img.src = `/book-logos/${logoKey}.svg`;
                                                                                                                                        } else {
                                                                                                                                            img.src = '/book-logos/book-default.svg';
                                                                                                                                        }
                                                                                                                                    }}
                                                                                                                                />
                                                                                                                                <span className="book-name-chip">{meta.label}</span>
                                                                                                                                <span className={`book-logo-odds ${b.odds >= 0 ? 'positive' : 'negative'}`}>
                                                                                                                                    {b.odds > 0 ? '+' : ''}{b.odds}
                                                                                                                                </span>
                                                                                                                            </div>
                                                                                                                        );
                                                                                                                    })}
                                                                                                                </div>
                                                                                                                <div className="detail-item"><span>{(r.side || 'over') === 'over' ? 'Other Side Odds' : 'Other Side Odds'}</span><strong>{r.opposing_odds != null ? `${r.opposing_odds > 0 ? '+' : ''}${r.opposing_odds}` : 'N/A'}</strong></div>
                                                                                                            </div>
                                                                                                            <div className="detail-card">
                                                                                                                <div className="detail-title">Probability</div>
                                                                                                                <div className="detail-item"><span>{(r.side || 'over') === 'over' ? 'Over' : 'Under'} Implied</span><strong>{r.sharp_implied_prob?.toFixed(1)}%</strong></div>
                                                                                                                <div className="detail-item"><span>{(r.side || 'over') === 'over' ? 'Under' : 'Over'} Implied</span><strong>{r.opposing_implied_prob != null ? `${r.opposing_implied_prob.toFixed(1)}%` : 'N/A'}</strong></div>
                                                                                                                <div className="detail-item"><span>Fair Prob (No-Vig)</span><strong>{r.fair_prob != null ? `${r.fair_prob.toFixed(1)}%` : 'N/A'}</strong></div>
                                                                                                            </div>
                                                                                                            <div className="detail-card">
                                                                                                                <div className="detail-title">Decision Metrics</div>
                                                                                                                <div className="detail-item"><span>Calc Status</span><strong className={r.is_calculated === false ? 'negative' : 'positive'}>{r.is_calculated === false ? 'Raw (Unscored)' : 'Consensus'}</strong></div>
                                                                                                                {r.is_calculated === false && r.calc_reason && (
                                                                                                                    <div className="detail-item"><span>Reason</span><strong>{r.calc_reason}</strong></div>
                                                                                                                )}
                                                                                                                <div className="detail-item"><span>Sleeper Implied</span><strong>{r.fixed_implied_prob?.toFixed(1)}%</strong></div>
                                                                                                                <div className="detail-item"><span>Vig</span><strong>{r.vig_pct != null ? `${r.vig_pct.toFixed(1)}%` : 'N/A'}</strong></div>
                                                                                                                <div className="detail-item"><span>Your Edge</span><strong className={r.edge_pct > 0 ? 'positive' : 'negative'}>{r.edge_pct > 0 ? '+' : ''}{r.edge_pct.toFixed(2)}%</strong></div>
                                                                                                                <div className="detail-item"><span>Sleeper Compatible</span><strong className={sleeperCompatible ? 'positive' : 'negative'}>{sleeperCompatible ? 'Yes' : 'No'}</strong></div>
                                                                                                                <div className="detail-item"><span>PrizePicks Direct</span><strong>{r.available_on_prizepicks_direct ? 'Yes' : 'No'}</strong></div>
                                                                                                                <div className="detail-item"><span>Underdog Direct</span><strong>{r.available_on_underdog_direct ? 'Yes' : 'No'}</strong></div>
                                                                                                            </div>
                                                                                                            <div className="detail-card">
                                                                                                                <div className="detail-title">Action Model</div>
                                                                                                                <div className="detail-item"><span>AI Score</span><strong className={ai >= 70 ? 'positive' : ai >= 50 ? '' : 'negative'}>{ai.toFixed(0)}</strong></div>
                                                                                                                <div className="detail-item"><span>Confidence</span><strong>{conf.toFixed(0)}% ({confTier})</strong></div>
                                                                                                                <div className="detail-item"><span>Risk</span><strong>{risk}</strong></div>
                                                                                                                {Number.isFinite(Number(r.weight_coverage_pct)) && (
                                                                                                                    <div className="detail-item"><span>Weight Coverage</span><strong>{Number(r.weight_coverage_pct).toFixed(1)}%</strong></div>
                                                                                                                )}
                                                                                                                <div className="detail-item"><span>Suggested Stake</span><strong>{Math.min(suggestedStake, activeKellyCapPct).toFixed(2)}%</strong></div>
                                                                                                                <div className="detail-item"><span>Correlation Penalty</span><strong>{calcProfile.useCorrelationPenalty ? `-${correlationPenalty.toFixed(1)}` : 'Off'}</strong></div>
                                                                                                                {evLoadingByKey[k] && <div className="detail-item"><span>Calc Engine</span><strong>Loading…</strong></div>}
                                                                                                                {!evLoadingByKey[k] && evCalcByKey[k] && (
                                                                                                                    <>
                                                                                                                        <div className="detail-item"><span>EV (Calc)</span><strong className={evCalcByKey[k].ev_percent >= 0 ? 'positive' : 'negative'}>{evCalcByKey[k].ev_percent >= 0 ? '+' : ''}{evCalcByKey[k].ev_percent.toFixed(2)}%</strong></div>
                                                                                                                        <div className="detail-item"><span>Kelly (Calc)</span><strong>{(evCalcByKey[k].kelly_fraction * 100).toFixed(2)}%</strong></div>
                                                                                                                    </>
                                                                                                                )}
                                                                                                            </div>
                                                                                                        </div>
                                                                                                        <div className="detail-formula">
                                                                                                            {r.fair_prob != null
                                                                                                                ? `Edge Formula: (${r.fair_prob.toFixed(1)}% - ${r.fixed_implied_prob?.toFixed(1)}%) = ${r.edge_pct > 0 ? '+' : ''}${r.edge_pct.toFixed(2)}%`
                                                                                                                : `Edge Formula: (${r.sharp_implied_prob?.toFixed(1)}% - ${r.fixed_implied_prob?.toFixed(1)}%) = ${r.edge_pct > 0 ? '+' : ''}${r.edge_pct.toFixed(2)}%`
                                                                                                            }
                                                                                                        </div>
                                                                                                    </td>
                                                                                                </tr>
                                                                                            )}
                                                                                        </Fragment>
                                                                                    );
                                                                                })}
                                                                            </tbody>
                                                                        </table>
                                                                        {group.rows.length > renderLimit && (
                                                                            <div className="group-load-more">
                                                                                <button
                                                                                    type="button"
                                                                                    className="slip-ribbon-btn"
                                                                                    onClick={() => setPlayerRenderLimit(prev => ({ ...prev, [group.key]: renderLimit + 24 }))}
                                                                                >
                                                                                    Load More ({group.rows.length - renderLimit} remaining)
                                                                                </button>
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                </td>
                                                            </tr>
                                                        )}
                                                    </Fragment>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                    {renderPagination('bottom')}
                                </div>
                            )}
                        </div>
                    )}
                    {recommendedSlips.length > 0 && (
                        <div className="recommended-slips">
                            <div className="recommended-title">Optimized Combos</div>
                            <div className="recommended-list">
                                {recommendedSlips.map((s, idx) => (
                                    <div key={`rec-${idx}`} className="recommended-item">
                                        <div className="recommended-meta">
                                            <span>#{idx + 1}</span>
                                            <span>{Array.isArray(s.players) ? s.players.length : (Array.isArray(s.picks) ? s.picks.length : 0)} legs</span>
                                            {typeof s.expected_value_pct === 'number' && <span>EV {s.expected_value_pct > 0 ? '+' : ''}{s.expected_value_pct.toFixed(1)}%</span>}
                                        </div>
                                        <button className="slip-ribbon-btn" onClick={() => applyRecommendedSlip(s)}>
                                            Use This Slip
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function formatMarket(key: string): string {
    const MARKET_LABELS: Record<string, string> = {
        // NBA
        player_points: 'Points',
        player_rebounds: 'Rebounds',
        player_assists: 'Assists',
        player_threes: '3-Pointers',
        player_blocks: 'Blocks',
        player_steals: 'Steals',
        player_turnovers: 'Turnovers',
        player_points_rebounds_assists: 'Pts+Reb+Ast',
        player_points_rebounds: 'Pts+Reb',
        player_points_assists: 'Pts+Ast',
        player_rebounds_assists: 'Reb+Ast',
        player_double_double: 'Double-Double',
        // NFL
        player_pass_yds: 'Pass Yards',
        player_pass_tds: 'Pass TDs',
        player_pass_completions: 'Completions',
        player_pass_attempts: 'Pass Attempts',
        player_rush_yds: 'Rush Yards',
        player_rush_attempts: 'Rush Attempts',
        player_receptions: 'Receptions',
        player_reception_yds: 'Rec Yards',
        player_anytime_td: 'Anytime TD',
        player_kicking_points: 'Kicking Pts',
        // MLB
        pitcher_strikeouts: 'Strikeouts',
        pitcher_outs: 'Pitcher Outs',
        batter_hits: 'Hits',
        batter_total_bases: 'Total Bases',
        batter_rbis: 'RBIs',
        batter_runs_scored: 'Runs Scored',
        batter_walks: 'Walks',
        batter_stolen_bases: 'Stolen Bases',
        // Soccer
        player_shots: 'Shots',
        player_shots_on_target: 'Shots on Target',
        player_goal_scorer_anytime: 'Anytime Scorer',
    };
    return MARKET_LABELS[key] ?? key.replace(/^(player_|pitcher_|batter_)/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
