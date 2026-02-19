import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Brain, Target, ShieldAlert, DollarSign, TrendingUp, TrendingDown, Loader2, CalendarDays, Calculator, Search, ArrowRight, Clock, SlidersHorizontal, CircleCheckBig, AlertTriangle, OctagonX } from 'lucide-react';
import StockChart from '../components/StockChart';
import TradePanel from '../components/TradePanel';
import BacktestPanel from '../components/BacktestPanel';
import CalcProfilePopover from '../components/CalcProfilePopover';
import { loadCalcProfile, saveCalcProfile, loadProfileFromSettings } from '../lib/calcProfiles';
import type { CalcPreset, StocksCalcProfile } from '../lib/calcProfiles';
import './AlpacaAnalysis.css';

interface TradeSetup {
    Setup?: string;
    Type?: string;
    Entry: number;
    Stop_Loss: number;
    Target: number;
    Risk_Reward: number;
    Qty?: number;
}

interface AnalysisData {
    ticker: string;
    analysis: Record<string, any>;
    ai_scores: { clean: number; eventual: number; composite: number };
    setups: TradeSetup[];
}

interface HistoryEntry {
    ticker: string;
    composite: number;
    clean: number;
    eventual: number;
    timestamp: string;
    verdict: string;
    bestSetup?: { entry: number; stop: number; target: number; rr: number; type: string };
}

function setupPlayScore(setup: TradeSetup, profile: StocksCalcProfile): number {
    const rr = Number(setup.Risk_Reward || 0);
    const entry = Number(setup.Entry || 0);
    const stop = Number(setup.Stop_Loss || 0);
    const target = Number(setup.Target || 0);
    if (entry <= 0) return 0;
    const movePct = ((target - entry) / entry) * 100;
    const riskPct = ((entry - stop) / entry) * 100;
    const riskPenalty = Math.max(0, riskPct - 4.5) * profile.volatilityPenalty;
    const trendBonus = rr >= 2.1 ? profile.trendStrengthBonus : 0;
    const raw = 42
        + (rr * profile.crossoverWeight * 9.5)
        + (movePct * profile.emaWeight * 1.35)
        + (Math.max(0, 6 - riskPct) * profile.atrWeight)
        + ((profile.useLiquidityFilter ? 1 : 0) * profile.liquidityWeight * 2)
        + trendBonus
        - riskPenalty;
    return Math.max(0, Math.min(100, raw));
}

const HISTORY_KEY = 'apex_analysis_history';

function loadHistory(): HistoryEntry[] {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]'); } catch { return []; }
}

function saveToHistory(data: AnalysisData) {
    const history = loadHistory().filter(h => h.ticker !== data.ticker);
    const entry: HistoryEntry = {
        ticker: data.ticker,
        composite: data.ai_scores.composite,
        clean: data.ai_scores.clean,
        eventual: data.ai_scores.eventual,
        timestamp: new Date().toISOString(),
        verdict: data.ai_scores.composite >= 65 ? 'BUY' : data.ai_scores.composite >= 45 ? 'HOLD' : 'SKIP',
    };
    if (data.setups?.[0]) {
        entry.bestSetup = {
            entry: data.setups[0].Entry,
            stop: data.setups[0].Stop_Loss,
            target: data.setups[0].Target,
            rr: data.setups[0].Risk_Reward,
            type: data.setups[0].Setup || data.setups[0].Type || 'Setup 1',
        };
    }
    history.unshift(entry);
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20)));
}

export default function AlpacaAnalysis() {
    const { ticker: urlTicker } = useParams();
    const navigate = useNavigate();
    const [ticker, setTicker] = useState(urlTicker || '');
    const [data, setData] = useState<AnalysisData | null>(null);
    const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [price, setPrice] = useState(0);
    const [changePct, setChangePct] = useState(0);
    const [stockName, setStockName] = useState('');

    // Search dropdown state
    const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([]);
    const [showDropdown, setShowDropdown] = useState(false);
    const [highlightIdx, setHighlightIdx] = useState(-1);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);

    // Earnings state
    const [earnings, setEarnings] = useState<{ safe: boolean; message: string; days_until?: number } | null>(null);

    // Risk calculator state
    const [riskResult, setRiskResult] = useState<any>(null);
    const [riskPercent, setRiskPercent] = useState(1);
    const [riskPercentInput, setRiskPercentInput] = useState('1');
    const [activeSetupIdx, setActiveSetupIdx] = useState<number>(-1);
    const [tradePanelSeed, setTradePanelSeed] = useState(0);
    const [tradePanelQty, setTradePanelQty] = useState<string>('');
    const tradePanelRef = useRef<HTMLDivElement>(null);
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<StocksCalcProfile>(loadCalcProfile('stocks'));

    // Click-outside to close dropdown
    useEffect(() => {
        function handleClick(e: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setShowDropdown(false);
            }
        }
        document.addEventListener('mousedown', handleClick);
        return () => document.removeEventListener('mousedown', handleClick);
    }, []);

    useEffect(() => {
        saveCalcProfile('stocks', calcProfile);
    }, [calcProfile]);

    useEffect(() => {
        setActiveSetupIdx(-1);
    }, [data?.ticker]);

    useEffect(() => {
        loadProfileFromSettings('stocks').then((profile) => {
            if (profile) setCalcProfile(profile);
        });
    }, []);

    useEffect(() => {
        const onSettingsUpdated = (e: Event) => {
            const cfg = (e as CustomEvent).detail;
            if (cfg?.stocks?.calc_profile) {
                setCalcProfile(prev => ({ ...prev, ...cfg.stocks.calc_profile }));
            }
        };
        window.addEventListener('apex:settings-updated', onSettingsUpdated);
        return () => window.removeEventListener('apex:settings-updated', onSettingsUpdated);
    }, []);

    const fetchSuggestions = useCallback((query: string) => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (query.length < 1) { setSuggestions([]); setShowDropdown(false); return; }
        debounceRef.current = setTimeout(async () => {
            try {
                const res = await fetch(`/api/v1/alpaca/search?q=${encodeURIComponent(query)}`);
                const data = await res.json();
                setSuggestions(data.results || []);
                setShowDropdown((data.results || []).length > 0);
                setHighlightIdx(-1);
            } catch { setSuggestions([]); }
        }, 200);
    }, []);

    function selectSuggestion(symbol: string, name?: string) {
        setTicker(symbol);
        setStockName(name || '');
        setShowDropdown(false);
        setSuggestions([]);
        navigate(`/alpaca/search/${symbol}`);
    }

    async function fetchQuote(symbol: string) {
        try {
            const res = await fetch(`/api/v1/alpaca/quote?ticker=${encodeURIComponent(symbol)}`);
            if (res.ok) {
                const q = await res.json();
                setPrice(q.price || 0);
                setChangePct(q.change_pct || 0);
            }
        } catch { /* ignore */ }
    }

    useEffect(() => {
        if (urlTicker) {
            setTicker(urlTicker);
            fetchQuote(urlTicker);
            analyze(urlTicker);
            fetchEarnings(urlTicker);
            if (!stockName) {
                fetch(`/api/v1/alpaca/search?q=${encodeURIComponent(urlTicker)}`)
                    .then(r => r.json())
                    .then(d => {
                        const match = (d.results || []).find((s: any) => s.symbol === urlTicker.toUpperCase());
                        if (match) setStockName(match.name);
                    })
                    .catch(() => { });
            }
        }
    }, [urlTicker]);

    async function fetchEarnings(symbol: string) {
        try {
            const res = await fetch(`/api/v1/alpaca/earnings?ticker=${symbol}`);
            if (res.ok) setEarnings(await res.json());
        } catch { /* ignore */ }
    }

    async function calcRisk(entry: number, stop: number) {
        try {
            const res = await fetch('/api/v1/alpaca/risk-calculator', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ account_balance: 10000, risk_percent: riskPercent / 100, entry_price: entry, stop_price: stop }),
            });
            if (res.ok) setRiskResult(await res.json());
        } catch { /* ignore */ }
    }

    async function analyze(symbol: string) {
        if (!symbol) return;
        setLoading(true);
        setError('');
        setData(null);
        setEarnings(null);
        setRiskResult(null);
        fetchEarnings(symbol);
        try {
            const res = await fetch('/api/v1/alpaca/analysis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: symbol.toUpperCase() }),
            });
            if (!res.ok) throw new Error(await res.text());
            const result = await res.json();
            setData(result);
            setActiveSetupIdx(0);
            saveToHistory(result);
            setHistory(loadHistory());
        } catch (e: any) {
            setError(e.message || 'Analysis failed');
        }
        setLoading(false);
    }

    const showDashboard = !urlTicker && !data && !loading;

    function handleSetupBuy(setup: TradeSetup, index: number) {
        setActiveSetupIdx(index);
        setTradePanelQty(setup.Qty ? String(setup.Qty) : '');
        setTradePanelSeed(prev => prev + 1);
        calcRisk(setup.Entry, setup.Stop_Loss);
        tradePanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    return (
        <div className="analysis-page">
            {/* Header */}
            <div className="analysis-header">
                <div className="ticker-input-group" ref={dropdownRef}>
                    <div className="search-bar">
                        <Search size={16} className="search-icon" />
                        <input
                            type="text"
                            value={ticker}
                            onChange={e => {
                                const val = e.target.value.toUpperCase();
                                setTicker(val);
                                fetchSuggestions(val);
                            }}
                            onKeyDown={e => {
                                if (e.key === 'Enter') {
                                    if (highlightIdx >= 0 && suggestions[highlightIdx]) {
                                        selectSuggestion(suggestions[highlightIdx].symbol, suggestions[highlightIdx].name);
                                    } else {
                                        setShowDropdown(false);
                                    }
                                } else if (e.key === 'ArrowDown') {
                                    e.preventDefault();
                                    setHighlightIdx(prev => Math.min(prev + 1, suggestions.length - 1));
                                } else if (e.key === 'ArrowUp') {
                                    e.preventDefault();
                                    setHighlightIdx(prev => Math.max(prev - 1, 0));
                                } else if (e.key === 'Escape') {
                                    setShowDropdown(false);
                                }
                            }}
                            onFocus={() => { if (suggestions.length > 0) setShowDropdown(true); }}
                            placeholder="Search"
                            className="ticker-input"
                            autoComplete="off"
                        />
                    </div>
                    {showDropdown && suggestions.length > 0 && (
                        <div className="ticker-dropdown">
                            {suggestions.map((s, i) => (
                                <div
                                    key={s.symbol}
                                    className={`ticker-option ${i === highlightIdx ? 'highlighted' : ''}`}
                                    onClick={() => selectSuggestion(s.symbol, s.name)}
                                    onMouseEnter={() => setHighlightIdx(i)}
                                >
                                    <span className="option-symbol">{s.symbol}</span>
                                    <span className="option-name">{s.name}</span>
                                </div>
                            ))}
                        </div>
                    )}

                </div>
                {urlTicker && (
                    <div className="stock-info">
                        {stockName && <span className="stock-name">{stockName}</span>}
                        <span className="stock-symbol">{urlTicker}</span>
                    </div>
                )}
                {price > 0 && (
                    <div className="price-display">
                        <span className="price-value">${price.toFixed(2)}</span>
                        <span className={`price-change ${changePct >= 0 ? 'positive' : 'negative'}`}>
                            {changePct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                            {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                        </span>
                    </div>
                )}
            </div>

            {/* Dashboard — shown when no ticker */}
            {showDashboard && (
                <div className="analysis-dashboard">
                    <div className="dashboard-hero">
                        <Brain size={48} className="hero-icon" />
                        <h2>AI Stock Analysis</h2>
                        <p>Enter any ticker above to run ML inference + technical analysis</p>
                    </div>

                    {history.length > 0 && (
                        <div className="card history-card">
                            <h2><Clock size={18} /> Recent Analysis</h2>
                            <table className="history-table">
                                <thead>
                                    <tr>
                                        <th>Symbol</th>
                                        <th>AI Score</th>
                                        <th>Clean</th>
                                        <th>Eventual</th>
                                        <th>Verdict</th>
                                        <th>Time</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {history.slice(0, 10).map(h => (
                                        <tr key={h.ticker}>
                                            <td className="mono ticker-cell">{h.ticker}</td>
                                            <td>
                                                <span className={`score-badge ${h.composite >= 65 ? 'high' : h.composite >= 45 ? 'mid' : 'low'}`}>
                                                    {h.composite.toFixed(0)}%
                                                </span>
                                            </td>
                                            <td className="muted">{h.clean.toFixed(0)}%</td>
                                            <td className="muted">{h.eventual.toFixed(0)}%</td>
                                            <td>
                                                <span className={`verdict-tag ${h.verdict.toLowerCase()}`}>{h.verdict}</span>
                                            </td>
                                            <td className="muted time-cell">
                                                {new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                            </td>
                                            <td className="action-cell">
                                                <button className="btn-mini" onClick={() => navigate(`/alpaca/search/${h.ticker}`)}>
                                                    <ArrowRight size={14} /> View
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Earnings Warning */}
            {earnings && !earnings.safe && (
                <div className="earnings-warning">
                    <CalendarDays size={18} />
                    <span>{earnings.message}</span>
                </div>
            )}

            {error && <div className="error-banner">{error}</div>}

            {loading && (
                <div className="loading-state">
                    <Loader2 size={32} className="spin" />
                    <p>Running ML inference + technical analysis on {ticker}...</p>
                </div>
            )}

            {/* Chart — shows immediately even before analysis completes */}
            {urlTicker && !loading && (
                <div className="card chart-card full-width">
                    <StockChart ticker={urlTicker} />
                </div>
            )}

            {data && (
                <>
                    <div className="analysis-grid">
                        {/* AI Scores */}
                        <div className="card ai-scores-card">
                            <h2><Brain size={18} /> AI Brain Scores</h2>
                            <div className="scores-container">
                                <ScoreBar label="Composite" value={data.ai_scores.composite} tone="composite" />
                                <ScoreBar label="Clean Win" value={data.ai_scores.clean} tone="clean" />
                                <ScoreBar label="Eventual Win" value={data.ai_scores.eventual} tone="eventual" />
                            </div>
                            <div className="score-verdict">
                                {data.ai_scores.composite >= 65 ? (
                                    <span className="verdict-bullish" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                        <CircleCheckBig size={14} /> High Confidence Setup
                                    </span>
                                ) : data.ai_scores.composite >= 45 ? (
                                    <span className="verdict-neutral" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                        <AlertTriangle size={14} /> Moderate Confidence
                                    </span>
                                ) : (
                                    <span className="verdict-bearish" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                        <OctagonX size={14} /> Low Confidence - Skip
                                    </span>
                                )}
                            </div>
                        </div>

                        {/* Technical Indicators */}
                        <div className="card indicators-card">
                            <h2><Target size={18} /> Technical Indicators</h2>
                            <div className="indicators-grid">
                                {data.analysis && Object.entries(data.analysis).map(([key, val]) => {
                                    if (key === 'current_price' || key === 'change_pct') return null;
                                    if (typeof val !== 'number') return null;
                                    return (
                                        <div className="indicator" key={key}>
                                            <span className="indicator-label">{formatLabel(key)}</span>
                                            <span className="indicator-value">{typeof val === 'number' ? val.toFixed(2) : String(val)}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>

                        {/* Order Calculator + Risk Sizer */}
                        <div className="card order-calc-card">
                            <h2><DollarSign size={18} /> Risk Calculator</h2>
                            <h3><Calculator size={14} /> Risk Sizer (1% Rule)</h3>
                            <div className="calc-row">
                                <label>Risk %</label>
                                <input
                                    type="number"
                                    value={riskPercentInput}
                                    onChange={e => {
                                        const raw = e.target.value;
                                        setRiskPercentInput(raw);
                                        if (raw === '') return;
                                        const next = parseFloat(raw);
                                        if (!Number.isFinite(next)) return;
                                        setRiskPercent(next);
                                    }}
                                    onBlur={() => {
                                        if (riskPercentInput.trim() === '') {
                                            setRiskPercent(1);
                                            setRiskPercentInput('1');
                                        }
                                    }}
                                    min={0.1}
                                    max={5}
                                    step={0.5}
                                    style={{ width: 60 }}
                                />
                            </div>
                            {data.setups?.[0] && (
                                <button className="btn-risk-calc" onClick={() => calcRisk(data.setups[0].Entry, data.setups[0].Stop_Loss)}>
                                    Calculate from Setup 1
                                </button>
                            )}
                            {riskResult && (
                                <div className="risk-result">
                                    <div className="calc-row"><label>Rec. Shares</label><span className="calc-value">{riskResult.sizing.shares}</span></div>
                                    <div className="calc-row"><label>Position $</label><span className="calc-value">${riskResult.sizing.position_value}</span></div>
                                    <div className="calc-row"><label>Max Risk $</label><span className="calc-value stop">${riskResult.sizing.risk_dollars}</span></div>
                                    <div className="calc-row"><label>Portfolio %</label><span className="calc-value">{riskResult.validation.position_pct}%</span></div>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Trade Setups + Trade Panel — side by side */}
                    <div className="search-trade-section">
                        {/* Trade Setups */}
                        <div className="card setups-card">
                            <h2>
                                <ShieldAlert size={18} /> Trade Setups
                                <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open stocks profile">
                                    <SlidersHorizontal size={14} />
                                </button>
                            </h2>
                            {data.setups.length === 0 ? (
                                <p className="muted">No trade setups generated. Try a different ticker.</p>
                            ) : (
                                <div className="setups-grid setups-grid-fill">
                                    {[...data.setups]
                                        .sort((a, b) => setupPlayScore(b, calcProfile) - setupPlayScore(a, calcProfile))
                                        .map((setup, i) => (
                                        <div className={`setup-card ${i === activeSetupIdx ? 'active' : ''}`} key={i}>
                                            <div className="setup-name">{setup.Setup || setup.Type || `Setup ${i + 1}`}</div>
                                            <div className="setup-details">
                                                <div className="setup-row">
                                                    <span>Entry</span>
                                                    <span className="entry">${setup.Entry?.toFixed(2)}</span>
                                                </div>
                                                <div className="setup-row">
                                                    <span>Stop Loss</span>
                                                    <span className="stop">${setup.Stop_Loss?.toFixed(2)}</span>
                                                </div>
                                                <div className="setup-row">
                                                    <span>Target</span>
                                                    <span className="target">${setup.Target?.toFixed(2)}</span>
                                                </div>
                                                <div className="setup-row">
                                                    <span>Risk:Reward</span>
                                                    <span className="rr">{setup.Risk_Reward?.toFixed(2)}</span>
                                                </div>
                                                <div className="setup-row">
                                                    <span>Play</span>
                                                    <span className="rr">{setupPlayScore(setup, calcProfile).toFixed(0)}</span>
                                                </div>
                                            </div>
                                            <button className="btn-execute buy-setup-btn" onClick={() => handleSetupBuy(setup, i)}>
                                                Buy Setup
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Trade Panel — right side */}
                        <div className="search-trade-panel" ref={tradePanelRef}>
                            <TradePanel key={`${data.ticker}-${tradePanelSeed}`} symbol={data.ticker} defaultQty={tradePanelQty} compact />
                        </div>
                    </div>

                    {/* Backtester — below trade section */}
                    <div className="search-backtest-section">
                        <BacktestPanel ticker={data.ticker} />
                    </div>
                </>
            )}
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
        </div>
    );
}

function ScoreBar({ label, value, tone }: { label: string; value: number; tone: 'composite' | 'clean' | 'eventual' }) {
    return (
        <div className={`score-bar tone-${tone}`}>
            <div className="score-bar-header">
                <span className="score-label">{label}</span>
                <span className="score-value">{value.toFixed(0)}%</span>
            </div>
            <div className="score-track">
                <div className="score-fill" style={{ width: `${Math.min(value, 100)}%` }} />
            </div>
        </div>
    );
}

function formatLabel(key: string): string {
    return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
