import { useState, useEffect, useRef, useCallback } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Zap, TrendingUp, TrendingDown, DollarSign, RefreshCw, ArrowUpRight, ArrowDownRight, SlidersHorizontal } from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import { loadCalcProfile, saveCalcProfile, loadProfileFromSettings } from '../lib/calcProfiles';
import type { CalcPreset, EventsCalcProfile } from '../lib/calcProfiles';
import './pages.css';
import './scalper.css';

interface ScalpSignal {
    direction: string;
    confidence: number;
    contract_ticker: string;
    strike_level: number;
    current_price: number;
    momentum: number;
    reasoning: string;
}

interface DashboardData {
    current_price: number | null;
    momentum: number;
    momentum_direction: string;
    volatility: number;
    price_count: number;
    contracts: Array<{
        ticker: string;
        strike_level: number;
        yes_price: number;
        no_price: number;
    }>;
    last_signal: ScalpSignal | null;
    stats: {
        signals_emitted: number;
        prices_processed: number;
    };
}

function scalperSignalScore(s: ScalpSignal, profile: EventsCalcProfile): number {
    const confPct = (s.confidence || 0) * 100;
    const momentum = Math.min(100, Math.abs(s.momentum || 0) * 1000);
    const trendBonus = profile.useMomentumBoost ? (s.direction === 'BUY_YES' ? 5 : 3) * profile.momentumWeight : 0;
    const confAdj = profile.useConfidenceScaling ? confPct * profile.confidenceWeight * 0.18 : confPct * 0.16;
    const executionPenalty = profile.useExecutionRisk ? Math.max(0, 60 - confPct) * profile.executionRiskPenalty * 0.08 : 0;
    const raw = 44
        + confAdj
        + (momentum * profile.scalpSensitivity * 0.3)
        + trendBonus
        - executionPenalty;
    return Math.max(0, Math.min(100, raw));
}

export default function KalshiScalper() {
    const [data, setData] = useState<DashboardData | null>(null);
    const [signals, setSignals] = useState<ScalpSignal[]>([]);
    const [orderSide, setOrderSide] = useState<'yes' | 'no'>('yes');
    const [orderQty, setOrderQty] = useState(5);
    const [selectedContract, setSelectedContract] = useState<string | null>(null);
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<EventsCalcProfile>(loadCalcProfile('events'));
    const sessionPnL = 0; // Will be calculated from trade history
    const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
    const { isSleeping } = useOutletContext<{ isSleeping: boolean }>();

    const fetchData = useCallback(async () => {
        try {
            const res = await fetch('/api/v1/kalshi/scalper/tick', { method: 'POST' });
            if (!res.ok) return; // Backend not available, keep showing empty state
            const json = await res.json();
            const dashboard = json.dashboard ?? null;
            setData(dashboard);
            if (dashboard?.last_signal) {
                setSignals(prev => {
                    const exists = prev.some(s =>
                        s.contract_ticker === dashboard.last_signal.contract_ticker &&
                        s.direction === dashboard.last_signal.direction
                    );
                    if (!exists) return [dashboard.last_signal, ...prev].slice(0, 20);
                    return prev;
                });
            }
        } catch (_e) {
            // Backend offline — component renders with null data (empty states)
        }
    }, []);

    useEffect(() => {
        if (isSleeping) return;
        fetchData();
        pollRef.current = setInterval(fetchData, 1000); // 1s refresh for live data
        return () => clearInterval(pollRef.current);
    }, [fetchData, isSleeping]);

    useEffect(() => {
        saveCalcProfile('events', calcProfile);
    }, [calcProfile]);

    useEffect(() => {
        loadProfileFromSettings('events').then((profile) => {
            if (profile) setCalcProfile(profile);
        });
    }, []);

    useEffect(() => {
        async function loadEventSettings() {
            try {
                const res = await fetch('/api/v1/settings');
                if (!res.ok) return;
                await res.json();
                // quick settings consumed in Settings page; no inline controls here
            } catch {
                // ignore
            }
        }
        loadEventSettings();
    }, []);

    useEffect(() => {
        const onSettingsUpdated = (e: Event) => {
            const cfg = (e as CustomEvent).detail;
            if (!cfg) return;
            if (cfg?.events?.calc_profile) setCalcProfile(prev => ({ ...prev, ...cfg.events.calc_profile }));
        };
        window.addEventListener('apex:settings-updated', onSettingsUpdated);
        return () => window.removeEventListener('apex:settings-updated', onSettingsUpdated);
    }, []);

    async function quickOrder(ticker: string, side: 'yes' | 'no', qty: number) {
        if (!confirm(`⚡ SCALP: ${side.toUpperCase()} ${qty}x on ${ticker}?`)) return;
        try {
            await fetch('/api/v1/kalshi/scalper/quick-order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker, side, quantity: qty }),
            });
        } catch (e) {
            console.error('Scalp order failed:', e);
        }
    }

    const momentumColor = data?.momentum_direction === 'bullish' ? 'text-green' :
        data?.momentum_direction === 'bearish' ? 'text-red' : 'text-muted';
    const rankedSignals = [...signals].sort((a, b) => scalperSignalScore(b, calcProfile) - scalperSignalScore(a, calcProfile));

    const momentumIcon = data?.momentum_direction === 'bullish' ? <TrendingUp size={20} /> :
        data?.momentum_direction === 'bearish' ? <TrendingDown size={20} /> : null;

    return (
        <div className="page-grid">
            {/* Live Market Header */}
            <div className="scalper-hero glass-card">
                <div className="hero-price-section">
                    <div className="hero-label">S&P 500 Live</div>
                    <div className={`hero-price mono ${momentumColor}`}>
                        {data?.current_price?.toLocaleString(undefined, { minimumFractionDigits: 2 }) || '—'}
                    </div>
                    <div className={`hero-momentum ${momentumColor}`}>
                        {momentumIcon}
                        <span className="mono">{((data?.momentum || 0) * 100).toFixed(3)} pts/s</span>
                    </div>
                </div>
                <div className="hero-stats">
                    <div className="hero-stat">
                        <span className="stat-label">Volatility</span>
                        <span className="stat-value mono">{((data?.volatility || 0) * 10000).toFixed(2)} bps</span>
                    </div>
                    <div className="hero-stat">
                        <span className="stat-label">Session P/L</span>
                        <span className={`stat-value mono ${sessionPnL >= 0 ? 'text-green' : 'text-red'}`}>
                            ${sessionPnL.toFixed(2)}
                        </span>
                    </div>
                    <div className="hero-stat">
                        <span className="stat-label">Signals</span>
                        <span className="stat-value mono">{data?.stats.signals_emitted || 0}</span>
                    </div>
                </div>
            </div>

            <div className="two-col">
                {/* Strike Levels / Contracts */}
                <div className="glass-card">
                    <h3 className="section-title">
                        <Zap size={16} /> Active Contracts
                        <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open events profile">
                            <SlidersHorizontal size={14} />
                        </button>
                    </h3>
                    {data?.contracts && data.contracts.length > 0 ? (
                        <div className="contract-list">
                            {data.contracts.map(c => (
                                <div
                                    key={c.ticker}
                                    className={`contract-row ${selectedContract === c.ticker ? 'selected' : ''}`}
                                    onClick={() => setSelectedContract(c.ticker)}
                                >
                                    <div className="contract-ticker mono">{c.ticker}</div>
                                    <div className="contract-strike">
                                        Strike: <span className="mono">{c.strike_level.toLocaleString()}</span>
                                    </div>
                                    <div className="contract-prices">
                                        <span className="text-green mono">Y: {c.yes_price}¢</span>
                                        <span className="text-red mono">N: {c.no_price}¢</span>
                                    </div>
                                    <div className="contract-actions">
                                        <button
                                            className="btn btn-green btn-sm"
                                            onClick={(e) => { e.stopPropagation(); quickOrder(c.ticker, 'yes', orderQty); }}
                                        >
                                            BUY YES
                                        </button>
                                        <button
                                            className="btn btn-red btn-sm"
                                            onClick={(e) => { e.stopPropagation(); quickOrder(c.ticker, 'no', orderQty); }}
                                        >
                                            BUY NO
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="empty-state">No active S&P 500 contracts. Market hours: 1:30-4:00 PM ET.</div>
                    )}
                </div>

                {/* Quick Order Panel */}
                <div className="glass-card">
                    <h3 className="section-title"><DollarSign size={16} /> Quick Scalp</h3>
                    <div className="order-panel">
                        {selectedContract ? (
                            <>
                                <div className="order-ticker mono">{selectedContract}</div>
                                <div className="side-toggle">
                                    <button
                                        className={`btn ${orderSide === 'yes' ? 'btn-green' : 'btn-ghost'}`}
                                        onClick={() => setOrderSide('yes')}
                                    >
                                        YES
                                    </button>
                                    <button
                                        className={`btn ${orderSide === 'no' ? 'btn-red' : 'btn-ghost'}`}
                                        onClick={() => setOrderSide('no')}
                                    >
                                        NO
                                    </button>
                                </div>
                                <div className="qty-presets">
                                    {[1, 5, 10, 25, 50].map(q => (
                                        <button
                                            key={q}
                                            className={`btn btn-sm ${orderQty === q ? 'btn-primary' : 'btn-ghost'}`}
                                            onClick={() => setOrderQty(q)}
                                        >
                                            {q}x
                                        </button>
                                    ))}
                                </div>
                                <button
                                    className="btn btn-primary full-width"
                                    onClick={() => quickOrder(selectedContract, orderSide, orderQty)}
                                >
                                    ⚡ SCALP {orderSide.toUpperCase()} × {orderQty}
                                </button>
                            </>
                        ) : (
                            <div className="empty-state">Select a contract to trade</div>
                        )}
                    </div>
                </div>
            </div>

            {/* Signals Feed */}
            <div className="glass-card">
                <h3 className="section-title">
                    <RefreshCw size={16} /> Signal Feed
                    {rankedSignals.length > 0 && <span className="badge">{rankedSignals.length}</span>}
                </h3>
                {rankedSignals.length > 0 ? (
                    <div className="signal-feed">
                        {rankedSignals.map((s, i) => (
                            <div key={i} className={`signal-row ${s.direction === 'BUY_YES' ? 'bullish' : 'bearish'}`}>
                                <div className="signal-icon">
                                    {s.direction === 'BUY_YES' ?
                                        <ArrowUpRight size={16} className="text-green" /> :
                                        <ArrowDownRight size={16} className="text-red" />
                                    }
                                </div>
                                <div className="signal-info">
                                    <div className="signal-direction mono">
                                        {s.direction} — {s.contract_ticker}
                                    </div>
                                    <div className="signal-reasoning text-muted">{s.reasoning}</div>
                                </div>
                                <div className="signal-confidence">
                                    <div className={`confidence-bar ${s.confidence > 0.7 ? 'high' : s.confidence > 0.5 ? 'medium' : 'low'}`}>
                                        <div className="confidence-fill" style={{ width: `${s.confidence * 100}%` }} />
                                    </div>
                                    <span className="mono">{(s.confidence * 100).toFixed(0)}% | Play {scalperSignalScore(s, calcProfile).toFixed(0)}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className="empty-state">Waiting for momentum signals...</div>
                )}
            </div>
            <CalcProfilePopover
                open={calcOpen}
                onClose={() => setCalcOpen(false)}
                title="Events Calculation Profile"
                domain="events"
                preset={preset}
                profile={calcProfile}
                onPresetChange={setPreset}
                onProfileChange={(next) => setCalcProfile(next as any)}
            />
        </div>
    );
}
