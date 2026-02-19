import { useState, useEffect, useRef, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Search, Play, Loader2, TrendingUp, Brain, ArrowRight, RefreshCw, Filter, Clock, SlidersHorizontal } from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import {
    loadCalcProfile,
    saveCalcProfile,
    loadProfileFromSettings,
} from '../lib/calcProfiles';
import type { CalcPreset, StocksCalcProfile } from '../lib/calcProfiles';
import './AlpacaScanner.css';

const SCAN_RESULTS_KEY = 'alpaca_scanner_results';
const SCAN_STATUS_KEY = 'alpaca_scanner_status';
const SCAN_UI_KEY = 'alpaca_scanner_ui';
const SCAN_MAX_PRICE_KEY = 'alpaca_scanner_max_price';

interface ScanResult {
    Ticker: string;
    Price: number;
    ATR_Pct?: number;
    RSI?: number;
    AI_Composite: number;
    Vol_10D_Avg?: number;
    EMA_9?: number;
    EMA_21?: number;
    Crossover?: string;
}

interface ScanStatus {
    status: string;
    progress: number;
    total: number;
    message: string;
    last_match: string;
    is_running: boolean;
    timestamp?: number;
}

function stockPlayScore(
    r: ScanResult,
    activeTab: 'atr' | 'ma',
    profile: StocksCalcProfile,
): number {
    const aiCentered = (r.AI_Composite || 0) - 50;
    const atr = Number(r.ATR_Pct || 0);
    const rsi = Number(r.RSI || 50);
    const emaSpreadPct = Number(r.EMA_9 && r.EMA_21 ? ((r.EMA_9 - r.EMA_21) / Math.max(1, r.EMA_21)) * 100 : 0);
    const crossoverBoost = profile.useCrossoverBoost && activeTab === 'ma' && r.Crossover === 'Recent' ? 7 * profile.crossoverWeight : 0;
    const atrTrendBonus = profile.useAtrTrendGate && activeTab === 'atr' && atr >= 3.2 && atr <= 6.5 ? profile.trendStrengthBonus : 0;
    const rsiScore = profile.useRsiFilter ? (50 - Math.abs(58 - rsi)) * profile.rsiWeight * 0.35 : 0;
    const volatilityPenalty = Math.max(0, atr - 5.5) * profile.volatilityPenalty;
    const liquidityAdj = profile.useLiquidityFilter ? Math.log10(Math.max(1, Number(r.Vol_10D_Avg || 1))) * profile.liquidityWeight : 0;
    const smooth = Math.max(0, Math.min(1, profile.scoreSmoothing));
    const trendCore = (aiCentered * profile.atrWeight * 0.62) + (emaSpreadPct * profile.emaWeight * 1.15);
    const raw = (50 * (1 - smooth))
        + ((50 + trendCore + rsiScore + crossoverBoost + atrTrendBonus + liquidityAdj - volatilityPenalty) * smooth);
    return Math.max(0, Math.min(100, raw));
}

function stockLegacyPlayScore(
    r: ScanResult,
    activeTab: 'atr' | 'ma',
): number {
    const aiCentered = (r.AI_Composite || 0) - 50;
    const atr = Number(r.ATR_Pct || 0);
    const rsi = Number(r.RSI || 50);
    const trendBonus = activeTab === 'ma' && r.Crossover === 'Recent' ? 6 : activeTab === 'atr' && rsi >= 50 && rsi <= 67 ? 4 : 0;
    const confidence = aiCentered * 0.75;
    const stakeBoost = Math.min(18, Math.max(0, aiCentered * 0.45));
    const raw = 50 + (aiCentered * 1.7 * 0.95) + (confidence * 1.1 * 0.4) + (stakeBoost * 1.0 * 0.22) + trendBonus - (Math.max(0, atr - 5.5) * 2);
    return Math.max(0, Math.min(100, raw));
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

export default function AlpacaScanner() {
    const [atrResults, setAtrResults] = useState<ScanResult[]>(() => {
        try {
            const raw = sessionStorage.getItem(SCAN_RESULTS_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            return parsed?.atr || [];
        } catch {
            return [];
        }
    });
    const [maResults, setMaResults] = useState<ScanResult[]>(() => {
        try {
            const raw = sessionStorage.getItem(SCAN_RESULTS_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            return parsed?.ma || [];
        } catch {
            return [];
        }
    });
    const [status, setStatus] = useState<ScanStatus>(() => {
        try {
            const raw = sessionStorage.getItem(SCAN_STATUS_KEY);
            if (!raw) return { status: 'idle', progress: 0, total: 0, message: '', last_match: '', is_running: false };
            return JSON.parse(raw);
        } catch {
            return { status: 'idle', progress: 0, total: 0, message: '', last_match: '', is_running: false };
        }
    });
    const [activeTab, setActiveTab] = useState<'atr' | 'ma'>(() => {
        try {
            const raw = sessionStorage.getItem(SCAN_UI_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            return parsed?.activeTab === 'ma' ? 'ma' : 'atr';
        } catch {
            return 'atr';
        }
    });
    const [minPrice, setMinPrice] = useState<number>(0);
    const [maxPrice, setMaxPrice] = useState<number>(() => {
        try {
            const raw = localStorage.getItem(SCAN_MAX_PRICE_KEY);
            const v = raw ? Number(raw) : 50;
            return Number.isFinite(v) ? v : 50;
        } catch {
            return 50;
        }
    });
    const [minPriceInput, setMinPriceInput] = useState<string>('0');
    const [maxPriceInput, setMaxPriceInput] = useState<string>(() => String(maxPrice));
    const pollRef = useRef<number | null>(null);
    const [lastScanTime, setLastScanTime] = useState<string | null>(() => {
        try {
            const raw = sessionStorage.getItem(SCAN_RESULTS_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            return parsed?.lastScanTime || null;
        } catch {
            return null;
        }
    });
    const [autoSortPlayScore, setAutoSortPlayScore] = useState(true);
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<StocksCalcProfile>(loadCalcProfile('stocks'));

    const { isSleeping } = useOutletContext<{ isSleeping: boolean }>();

    function applyStocksSettings(config: any) {
        const quick = config?.stocks?.quick_settings;
        if (!quick) return;
        if (typeof quick.auto_sort_play_score === 'boolean') setAutoSortPlayScore(quick.auto_sort_play_score);
    }

    // Hydrate scanner state from session storage first.
    useEffect(() => {
        try {
            const rawResults = sessionStorage.getItem(SCAN_RESULTS_KEY);
            if (rawResults) {
                const parsed = JSON.parse(rawResults) as { atr: ScanResult[]; ma: ScanResult[]; lastScanTime: string | null };
                setAtrResults(parsed.atr || []);
                setMaResults(parsed.ma || []);
                if (parsed.lastScanTime) setLastScanTime(parsed.lastScanTime);
            }
            const rawStatus = sessionStorage.getItem(SCAN_STATUS_KEY);
            if (rawStatus) {
                const parsed = JSON.parse(rawStatus) as ScanStatus;
                setStatus(parsed);
            }
            const rawUi = sessionStorage.getItem(SCAN_UI_KEY);
            if (rawUi) {
                const parsed = JSON.parse(rawUi) as { activeTab: 'atr' | 'ma'; minPrice: number; maxPrice: number };
                if (parsed.activeTab === 'atr' || parsed.activeTab === 'ma') setActiveTab(parsed.activeTab);
                if (typeof parsed.minPrice === 'number') setMinPrice(parsed.minPrice);
            }
        } catch {
            // Ignore malformed session cache.
        }
    }, []);

    // Fetch existing results + status on mount
    useEffect(() => {
        fetchResults();
        fetch('/api/v1/alpaca/scanner/status')
            .then(r => r.json())
            .then(data => {
                setStatus(data);
                if (data.timestamp) {
                    setLastScanTime(new Date(data.timestamp * 1000).toISOString());
                }
            })
            .catch(() => { });
    }, []);

    // Persist results cache for route changes.
    useEffect(() => {
        try {
            sessionStorage.setItem(SCAN_RESULTS_KEY, JSON.stringify({
                atr: atrResults,
                ma: maResults,
                lastScanTime,
            }));
        } catch { }
    }, [atrResults, maResults, lastScanTime]);

    // Persist scanner status for route changes.
    useEffect(() => {
        try {
            sessionStorage.setItem(SCAN_STATUS_KEY, JSON.stringify(status));
        } catch { }
    }, [status]);

    // Persist UI controls for route changes.
    useEffect(() => {
        try {
            sessionStorage.setItem(SCAN_UI_KEY, JSON.stringify({
                activeTab,
                minPrice,
                maxPrice,
            }));
        } catch { }
    }, [activeTab, minPrice, maxPrice]);

    useEffect(() => {
        saveCalcProfile('stocks', calcProfile);
    }, [calcProfile]);

    useEffect(() => {
        loadProfileFromSettings('stocks').then((profile) => {
            if (profile) setCalcProfile(profile);
        });
    }, []);

    useEffect(() => {
        async function loadStocksSettings() {
            try {
                const res = await fetch('/api/v1/settings');
                if (!res.ok) return;
                const data = await res.json();
                applyStocksSettings(data?.config);
            } catch {
                // ignore
            }
        }
        loadStocksSettings();
    }, []);

    useEffect(() => {
        const onSettingsUpdated = (e: Event) => {
            const cfg = (e as CustomEvent).detail;
            if (cfg?.stocks?.calc_profile) {
                setCalcProfile(prev => ({ ...prev, ...cfg.stocks.calc_profile }));
            }
            applyStocksSettings(cfg);
        };
        window.addEventListener('apex:settings-updated', onSettingsUpdated);
        return () => window.removeEventListener('apex:settings-updated', onSettingsUpdated);
    }, []);

    useEffect(() => {
        try {
            localStorage.setItem(SCAN_MAX_PRICE_KEY, String(maxPrice));
        } catch {
            // ignore
        }
    }, [maxPrice]);

    useEffect(() => {
        setMinPriceInput(String(minPrice));
    }, [minPrice]);

    useEffect(() => {
        setMaxPriceInput(String(maxPrice));
    }, [maxPrice]);

    // Poll scanner status when running
    useEffect(() => {
        if (isSleeping) return; // Stop polling if sleeping

        if (status.is_running || status.status === 'running') {
            pollRef.current = window.setInterval(async () => {
                try {
                    const res = await fetch('/api/v1/alpaca/scanner/status');
                    const data = await res.json();
                    setStatus(data);
                    if (data.status === 'completed' || (!data.is_running && data.status !== 'running')) {
                        fetchResults();
                        if (data.timestamp) setLastScanTime(new Date(data.timestamp * 1000).toISOString());
                        if (pollRef.current) clearInterval(pollRef.current);
                    }
                } catch { }
            }, 2000);
        }
        return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }, [status.is_running, status.status, isSleeping]);

    async function fetchResults() {
        try {
            const res = await fetch('/api/v1/alpaca/scanner/results');
            const data = await res.json();
            setAtrResults(data.atr || []);
            setMaResults(data.ma || []);
            if (data.timestamp) setLastScanTime(data.timestamp);
        } catch { }
    }

    async function startScan() {
        try {
            await fetch('/api/v1/alpaca/scanner/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ strategy: 'both' }),
            });
            setStatus(prev => ({ ...prev, status: 'running', is_running: true, progress: 0 }));
        } catch { }
    }

    const rawResults = activeTab === 'atr' ? atrResults : maResults;
    const atrVisibleCount = useMemo(() => atrResults.filter(r => r.Price >= minPrice && r.Price <= maxPrice).length, [atrResults, minPrice, maxPrice]);
    const maVisibleCount = useMemo(() => maResults.filter(r => r.Price >= minPrice && r.Price <= maxPrice).length, [maResults, minPrice, maxPrice]);
    const scoredResults = useMemo(() => rawResults.map(r => ({
        ...r,
        __playScore: stockPlayScore(r, activeTab, calcProfile),
        __legacyPlayScore: stockLegacyPlayScore(r, activeTab),
    })), [rawResults, activeTab, calcProfile]);
    const results = useMemo(() => scoredResults
        .filter(r => r.Price >= minPrice && r.Price <= maxPrice)
        .sort((a, b) => autoSortPlayScore ? b.__playScore - a.__playScore : 0), [scoredResults, minPrice, maxPrice, autoSortPlayScore]);
    const isRunning = status.is_running || status.status === 'running';
    const progressPctRaw = status.total > 0 ? Math.round((status.progress / status.total) * 100) : 0;
    const progressPct = progressPctRaw >= 100 ? 100 : Math.floor(progressPctRaw / 5) * 5;

    return (
        <div className="scanner-page">
            <div className="scanner-header">
                <div>
                    <h1>
                        <Search size={24} /> Market Scanner
                        <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open stock scanner profile">
                            <SlidersHorizontal size={14} />
                        </button>
                    </h1>
                    <p className="subtitle">
                        AI-powered stock screening with dual strategy analysis
                    </p>
                    {lastScanTime && (
                        <p className="last-scan-time">
                            <Clock size={12} />
                            Last scanned: {formatDateTime(lastScanTime)}
                        </p>
                    )}
                </div>
                <div className="scanner-actions">
                    <button onClick={fetchResults} className="btn-secondary" disabled={isRunning}>
                        <RefreshCw size={16} /> Refresh
                    </button>
                    <button onClick={startScan} className="btn-start-scan" disabled={isRunning}>
                        {isRunning ? <><Loader2 size={16} className="spin" /> Scanning...</> : <><Play size={16} /> Start Scan</>}
                    </button>
                </div>
            </div>

            <CalcProfilePopover
                open={calcOpen}
                onClose={() => setCalcOpen(false)}
                title="Stocks Calculation Profile"
                domain="stocks"
                preset={preset}
                profile={calcProfile}
                onPresetChange={setPreset}
                onProfileChange={(next) => setCalcProfile(next as any)}
            />

            {/* Price Range Filter */}
            <div className="price-filter">
                <Filter size={14} />
                <label className="filter-label">Price Range:</label>
                <div className="filter-input-group">
                    <span className="filter-prefix">$</span>
                    <input
                        type="number"
                        className="filter-input"
                        value={minPriceInput}
                        onChange={e => {
                            const raw = e.target.value;
                            setMinPriceInput(raw);
                            if (raw === '') return;
                            const next = Number(raw);
                            if (!Number.isFinite(next)) return;
                            setMinPrice(Math.max(0, next));
                        }}
                        onBlur={() => {
                            if (minPriceInput.trim() === '') {
                                setMinPrice(0);
                                setMinPriceInput('0');
                            }
                        }}
                        min={0}
                    />
                </div>
                <span className="filter-sep">—</span>
                <div className="filter-input-group">
                    <span className="filter-prefix">$</span>
                    <input
                        type="number"
                        className="filter-input"
                        value={maxPriceInput}
                        onChange={e => {
                            const raw = e.target.value;
                            setMaxPriceInput(raw);
                            if (raw === '') return;
                            const next = Number(raw);
                            if (!Number.isFinite(next)) return;
                            setMaxPrice(Math.max(0, next));
                        }}
                        onBlur={() => {
                            if (maxPriceInput.trim() === '') {
                                setMaxPrice(50);
                                setMaxPriceInput('50');
                            }
                        }}
                        min={0}
                        placeholder="50"
                    />
                </div>
                <div className="play-score-filter">
                    <button className={`play-chip ${autoSortPlayScore ? 'on' : ''}`} onClick={() => setAutoSortPlayScore(v => !v)}>Sort</button>
                </div>
                <span className="stock-count">{results.length} / {rawResults.length} stocks</span>
            </div>

            {/* Progress Bar */}
            {isRunning && (
                <div className="scan-progress">
                    <div className="progress-info">
                        <span className="progress-label">{status.message || 'Scanning...'}</span>
                        <span className="progress-pct">{progressPct}%</span>
                    </div>
                    <div className="progress-track">
                        <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                    </div>
                    {status.last_match && <span className="last-match">Last match: {status.last_match}</span>}
                </div>
            )}

            {/* Strategy Tabs */}
            <div className="strategy-tabs">
                <button
                    className={`tab ${activeTab === 'atr' ? 'active' : ''}`}
                    onClick={() => setActiveTab('atr')}
                >
                    <TrendingUp size={16} /> ATR Momentum
                    <span className="tab-count">{atrVisibleCount}</span>
                </button>
                <button
                    className={`tab ${activeTab === 'ma' ? 'active' : ''}`}
                    onClick={() => setActiveTab('ma')}
                >
                    <ArrowRight size={16} /> MA Crossover
                    <span className="tab-count">{maVisibleCount}</span>
                </button>
            </div>

            {/* Results Table */}
            {results.length === 0 ? (
                <div className="empty-state">
                    <Search size={48} />
                    <h3>No Results Yet</h3>
                    <p>Click "Start Scan" to analyze 300+ stocks for {activeTab === 'atr' ? 'ATR momentum' : 'MA crossover'} setups.</p>
                </div>
            ) : (
                <div className="results-table-container">
                    <table className="results-table">
                        <thead>
                            <tr>
                                <th>Ticker</th>
                                <th>Price</th>
                                {activeTab === 'atr' ? (
                                    <>
                                        <th>ATR %</th>
                                        <th>RSI</th>
                                        <th>Volume</th>
                                    </>
                                ) : (
                                    <>
                                        <th>EMA 9</th>
                                        <th>EMA 21</th>
                                        <th>Crossover</th>
                                    </>
                                )}
                                <th>AI Score</th>
                                <th>Play</th>
                                <th>Legacy</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {results.map((r, i) => (
                                <tr key={i}>
                                    <td className="ticker-cell">{r.Ticker}</td>
                                    <td>${r.Price?.toFixed(2)}</td>
                                    {activeTab === 'atr' ? (
                                        <>
                                            <td className={getAtrClass(r.ATR_Pct || 0)}>{r.ATR_Pct?.toFixed(2)}%</td>
                                            <td className={getRsiClass(r.RSI || 50)}>{r.RSI?.toFixed(1)}</td>
                                            <td>{formatVolume(r.Vol_10D_Avg || 0)}</td>
                                        </>
                                    ) : (
                                        <>
                                            <td>${r.EMA_9?.toFixed(2)}</td>
                                            <td>${r.EMA_21?.toFixed(2)}</td>
                                            <td className={r.Crossover === 'Recent' ? 'crossover-recent' : ''}>{r.Crossover}</td>
                                        </>
                                    )}
                                    <td>
                                        <div className="ai-score">
                                            <Brain size={14} />
                                            <span className={getScoreClass(r.AI_Composite)}>{r.AI_Composite}%</span>
                                        </div>
                                    </td>
                                    <td className={r.__playScore >= 70 ? 'score-high' : r.__playScore >= 55 ? 'score-mid' : 'score-low'}>
                                        {r.__playScore.toFixed(0)}
                                    </td>
                                    <td className={r.__legacyPlayScore >= 70 ? 'score-high' : r.__legacyPlayScore >= 55 ? 'score-mid' : 'score-low'}>
                                        {r.__legacyPlayScore.toFixed(0)}
                                    </td>
                                    <td>
                                        <a href={`/alpaca/search/${r.Ticker}`} className="analyze-link">
                                            Analyze <ArrowRight size={14} />
                                        </a>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

function getAtrClass(atr: number): string {
    if (atr >= 4.5) return 'text-hot';
    if (atr >= 3.5) return 'text-warm';
    return '';
}

function getRsiClass(rsi: number): string {
    if (rsi > 70) return 'text-overbought';
    if (rsi < 30) return 'text-oversold';
    return '';
}

function getScoreClass(score: number): string {
    if (score >= 70) return 'score-high';
    if (score >= 50) return 'score-mid';
    return 'score-low';
}

function formatVolume(vol: number): string {
    if (vol >= 1_000_000) return (vol / 1_000_000).toFixed(1) + 'M';
    if (vol >= 1_000) return (vol / 1_000).toFixed(0) + 'K';
    return vol.toString();
}
