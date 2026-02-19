import { useState, useEffect, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
    Zap, Play, Square, ShoppingCart, DollarSign,
    Search, AlertTriangle, CheckCircle2, Radio, Info, XCircle, SlidersHorizontal, Clock
} from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import { loadCalcProfile, saveCalcProfile, loadProfileFromSettings } from '../lib/calcProfiles';
import type { CalcPreset, EventsCalcProfile } from '../lib/calcProfiles';
import './pages.css';
import './kalshi-activity.css';

interface Market {
    ticker: string;
    title: string;
    yes_price: number;
    no_price: number;
    volume: number;
}

interface BotStatus {
    running: boolean;
    strategy: string | null;
    iterations: number;
    dry_run?: boolean;
    copy_follow_accounts?: string[];
    copy_ratio?: number;
    error?: string;
}

interface ActivityEntry {
    ts: string;
    type: 'scan' | 'opportunity' | 'trade' | 'error' | 'info';
    message: string;
    details: Record<string, unknown>;
}

function marketSignalScore(m: Market, profile: EventsCalcProfile): number {
    const spread = Math.abs((m.yes_price || 0) - (m.no_price || 0));
    const volumeNorm = Math.min(100, (m.volume || 0) / 1200);
    const edgeProxy = (50 - spread) / 50; // tighter spread + high volume => better execution
    const volatilityPenalty = profile.useVolatilityPenalty ? Math.max(0, spread - 10) * profile.volatilityPenalty * 0.25 : 0;
    const executionPenalty = profile.useExecutionRisk ? Math.max(0, 22 - volumeNorm) * profile.executionRiskPenalty * 0.45 : 0;
    const trendBonus = profile.useMomentumBoost && volumeNorm > 35 ? 4 * profile.momentumWeight : 0;
    const depthBonus = profile.useDepthBoost ? (Math.min(30, volumeNorm) * profile.depthWeight * 0.08) : 0;
    const confAdj = profile.useConfidenceScaling ? (volumeNorm * profile.confidenceWeight * 0.06) : 0;
    const raw = 48
        + (edgeProxy * 22 * profile.spreadWeight)
        + (volumeNorm * 0.25 * profile.liquidityWeight)
        + depthBonus
        + confAdj
        + trendBonus
        - volatilityPenalty
        - executionPenalty;
    return Math.max(0, Math.min(100, raw));
}

const EVENT_META: Record<string, { icon: typeof Zap; color: string; label: string }> = {
    scan: { icon: Search, color: 'var(--text-muted)', label: 'Scan' },
    opportunity: { icon: AlertTriangle, color: 'var(--color-warning)', label: 'Opportunity' },
    trade: { icon: CheckCircle2, color: 'var(--color-green)', label: 'Trade' },
    error: { icon: XCircle, color: 'var(--color-red)', label: 'Error' },
    info: { icon: Info, color: 'var(--color-accent)', label: 'Info' },
};

function timeAgo(isoStr: string): string {
    const seconds = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (seconds < 5) return 'just now';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
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

function formatStrategyLabel(strategy: string | null): string {
    if (!strategy) return 'Idle';
    return strategy
        .split('-')
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

export default function KalshiDashboard() {
    const [markets, setMarkets] = useState<Market[]>([]);
    const [botStatus, setBotStatus] = useState<BotStatus>({ running: false, strategy: null, iterations: 0 });
    const [balance, setBalance] = useState<any>(null);
    const [positions, setPositions] = useState<any[]>([]);
    const [selectedMarket, setSelectedMarket] = useState<string | null>(null);
    const [orderbook, setOrderbook] = useState<any>(null);
    const [activity, setActivity] = useState<ActivityEntry[]>([]);
    const [showScans, setShowScans] = useState(false);
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<EventsCalcProfile>(loadCalcProfile('events'));
    const [lastScanTime, setLastScanTime] = useState<string | null>(null);

    // Quick order state
    const [orderSide, setOrderSide] = useState<'yes' | 'no'>('yes');
    const [orderQty, setOrderQty] = useState(1);
    const [orderQtyInput, setOrderQtyInput] = useState('1');

    const activityPollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

    const { isSleeping } = useOutletContext<{ isSleeping: boolean }>();

    function applyEventSettings(config: any) {
        const quick = config?.events?.quick_settings;
        if (!quick) return;
        if (typeof quick.show_scans_in_activity === 'boolean') setShowScans(quick.show_scans_in_activity);
    }

    useEffect(() => {
        if (isSleeping) return;

        fetchAll();
        const statusInterval = setInterval(fetchBotStatus, 5000);
        activityPollRef.current = setInterval(fetchActivity, 3000);
        return () => {
            clearInterval(statusInterval);
            clearInterval(activityPollRef.current);
        };
    }, [isSleeping]);

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
                const data = await res.json();
                applyEventSettings(data?.config);
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
            applyEventSettings(cfg);
            if (cfg?.events?.calc_profile) setCalcProfile(prev => ({ ...prev, ...cfg.events.calc_profile }));
        };
        window.addEventListener('apex:settings-updated', onSettingsUpdated);
        return () => window.removeEventListener('apex:settings-updated', onSettingsUpdated);
    }, []);

    async function fetchAll() {
        const [mRes, bRes, pRes, sRes, aRes] = await Promise.allSettled([
            fetch('/api/v1/kalshi/markets?limit=30').then((r) => r.json()),
            fetch('/api/v1/kalshi/balance').then((r) => r.json()),
            fetch('/api/v1/kalshi/positions').then((r) => r.json()),
            fetch('/api/v1/kalshi/bot/status').then((r) => r.json()),
            fetch('/api/v1/kalshi/bot/activity?limit=50').then((r) => r.json()),
        ]);
        if (mRes.status === 'fulfilled') {
            setMarkets(mRes.value.markets || []);
            setLastScanTime(new Date().toISOString());
        }
        if (bRes.status === 'fulfilled') setBalance(bRes.value);
        if (pRes.status === 'fulfilled') setPositions(pRes.value.positions || []);
        if (sRes.status === 'fulfilled') setBotStatus(sRes.value);
        if (aRes.status === 'fulfilled') setActivity(aRes.value.entries || []);
    }

    async function fetchBotStatus() {
        try {
            const res = await fetch('/api/v1/kalshi/bot/status').then((r) => r.json());
            setBotStatus(res);
        } catch { }
    }

    async function fetchActivity() {
        try {
            const res = await fetch('/api/v1/kalshi/bot/activity?limit=50').then((r) => r.json());
            setActivity(res.entries || []);
        } catch { }
    }

    async function selectMarket(ticker: string) {
        setSelectedMarket(ticker);
        try {
            const res = await fetch(`/api/v1/kalshi/orderbook/${ticker}`).then((r) => r.json());
            setOrderbook(res);
        } catch { }
    }

    async function placeOrder() {
        if (!selectedMarket) return;
        if (!confirm(`Place ${orderSide.toUpperCase()} order for ${orderQty} contracts on ${selectedMarket}?`)) return;
        try {
            await fetch('/api/v1/kalshi/order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ticker: selectedMarket,
                    side: orderSide,
                    quantity: orderQty,
                    order_type: 'market',
                }),
            });
            fetchAll();
        } catch (e) {
            console.error('Order failed:', e);
        }
    }

    async function startBot(strategy: string) {
        await fetch('/api/v1/kalshi/bot/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy, dry_run: true, interval: 60 }),
        });
        fetchBotStatus();
        setTimeout(fetchActivity, 1000);
    }

    async function stopBot() {
        await fetch('/api/v1/kalshi/bot/stop', { method: 'POST' });
        fetchBotStatus();
        setTimeout(fetchActivity, 1000);
    }

    const filteredActivity = showScans
        ? activity
        : activity.filter(e => e.type !== 'scan');
    const rankedMarkets = [...markets].sort((a, b) => marketSignalScore(b, calcProfile) - marketSignalScore(a, calcProfile));

    return (
        <div className="page-grid">
            <div className="scanner-header">
                <div>
                    <h1>
                        <Zap size={24} /> Kalshi Markets
                        <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open events profile">
                            <SlidersHorizontal size={14} />
                        </button>
                    </h1>
                    <p className="subtitle">
                        Live event markets, bot controls, and execution panel.
                    </p>
                    {lastScanTime && (
                        <p className="last-scan-time">
                            <Clock size={12} />
                            Last scanned: {formatDateTime(lastScanTime)}
                        </p>
                    )}
                </div>
            </div>
            {/* Stats Row */}
            <div className="stats-row">
                <div className="stat-card glass-card">
                    <span className="stat-label">Balance</span>
                    <span className="stat-value mono">${balance?.balance?.toLocaleString() || '—'}</span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Positions</span>
                    <span className="stat-value mono">{positions.length}</span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Bot Status</span>
                    <span className={`bot-status-pill ${botStatus.running ? 'running' : 'idle'}`}>
                        <span className="bot-status-dot" />
                        <span className="bot-status-text">{botStatus.running ? formatStrategyLabel(botStatus.strategy) : 'Idle'}</span>
                    </span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Iterations</span>
                    <span className="stat-value mono">{botStatus.iterations}</span>
                </div>
            </div>

            {/* Bot Controls */}
            <div className="glass-card">
                <h3 className="section-title"><Zap size={16} /> Bot Controls</h3>
                <div className="button-row">
                    <button className="btn btn-primary" onClick={() => startBot('arbitrage')} disabled={botStatus.running}>
                        <Play size={14} /> Arbitrage
                    </button>
                    <button className="btn btn-primary" onClick={() => startBot('copy')} disabled={botStatus.running}>
                        <Play size={14} /> Copy Trading
                    </button>
                    <button className="btn btn-primary" onClick={() => startBot('market-maker')} disabled={botStatus.running}>
                        <Play size={14} /> Market Maker
                    </button>
                    <button className="btn btn-indigo-opposite" onClick={stopBot} disabled={!botStatus.running}>
                        <Square size={14} /> Stop Bot
                    </button>
                </div>
                {botStatus.running && botStatus.strategy === 'copy' && (
                    <div className="bot-subline">
                        Following {(botStatus.copy_follow_accounts || []).length} accounts
                        {typeof botStatus.copy_ratio === 'number' ? ` @ ${(botStatus.copy_ratio * 100).toFixed(1)}% ratio` : ''}
                    </div>
                )}
                {botStatus.error && (
                    <div className="bot-error-banner">
                        <XCircle size={14} /> {botStatus.error}
                    </div>
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

            {/* Live Activity Feed */}
            <div className="glass-card activity-card">
                <div className="activity-header">
                    <h3 className="section-title">
                        <Radio size={16} className={botStatus.running ? 'pulse-icon' : ''} />
                        Live Activity
                        {filteredActivity.length > 0 && <span className="badge">{filteredActivity.length}</span>}
                    </h3>
                    <label className="activity-filter" onClick={() => setShowScans(!showScans)}>
                        <div className={`toggle-switch ${showScans ? 'on' : ''}`}>
                            <div className="toggle-knob" />
                        </div>
                        <span>Show scans</span>
                    </label>
                </div>
                {filteredActivity.length > 0 ? (
                    <div className="activity-feed">
                        {filteredActivity.map((entry, i) => {
                            const meta = EVENT_META[entry.type] || EVENT_META.info;
                            const Icon = meta.icon;
                            const profit = entry.details?.['profit'];
                            const hasProfit = typeof profit === 'string' || typeof profit === 'number';
                            return (
                                <div key={i} className={`activity-row activity-${entry.type}`}>
                                    <div className="activity-icon" style={{ color: meta.color }}>
                                        <Icon size={14} />
                                    </div>
                                    <div className="activity-body">
                                        <span className="activity-message">{entry.message}</span>
                                        {entry.details && entry.type === 'opportunity' && hasProfit && (
                                            <span className="activity-detail mono text-green">
                                                Profit: {String(profit)}
                                            </span>
                                        )}
                                        {entry.details && entry.type === 'trade' && (
                                            <span className="activity-detail mono text-green">
                                                ✅ Executed
                                            </span>
                                        )}
                                    </div>
                                    <span className="activity-time mono">{timeAgo(entry.ts)}</span>
                                </div>
                            );
                        })}
                    </div>
                ) : (
                    <div className="empty-state">
                        {botStatus.running
                            ? 'Waiting for activity...'
                            : 'Start a bot to see live activity'}
                    </div>
                )}
            </div>

            <div className="two-col">
                {/* Markets List */}
                <div className="glass-card">
                    <h3 className="section-title">Active Markets</h3>
                    <div className="market-list">
                        {rankedMarkets.slice(0, 20).map((m) => (
                            <div
                                key={m.ticker}
                                className={`market-row ${selectedMarket === m.ticker ? 'selected' : ''}`}
                                onClick={() => selectMarket(m.ticker)}
                            >
                                <div className="market-title">{m.title || m.ticker}</div>
                                <div className="market-prices">
                                    <span className="text-green mono">Y: {m.yes_price}¢</span>
                                    <span className="text-red mono">N: {m.no_price || (100 - (m.yes_price || 0))}¢</span>
                                    <span className="text-blue mono">Play: {marketSignalScore(m, calcProfile).toFixed(0)}</span>
                                </div>
                            </div>
                        ))}
                        {markets.length === 0 && <div className="empty-state">Loading markets...</div>}
                    </div>
                </div>

                {/* Order Panel */}
                <div className="glass-card">
                    <h3 className="section-title"><ShoppingCart size={16} /> Quick Order</h3>
                    {selectedMarket ? (
                        <div className="order-panel">
                            <div className="order-ticker mono">{selectedMarket}</div>
                            {orderbook && (
                                <div className="orderbook-summary">
                                    <div>Best Bid: <span className="text-green mono">{orderbook.orderbook?.yes?.[0]?.[0] || '—'}¢</span></div>
                                    <div>Best Ask: <span className="text-red mono">{orderbook.orderbook?.no?.[0]?.[0] || '—'}¢</span></div>
                                </div>
                            )}
                            <div className="order-controls">
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
                                <div className="qty-input">
                                    <label>Qty</label>
                                    <input
                                        type="number"
                                        className="input"
                                        value={orderQtyInput}
                                        onChange={(e) => {
                                            const raw = e.target.value;
                                            setOrderQtyInput(raw);
                                            if (raw === '') return;
                                            const next = parseInt(raw, 10);
                                            if (!Number.isFinite(next)) return;
                                            setOrderQty(Math.max(1, next));
                                        }}
                                        onBlur={() => {
                                            if (orderQtyInput.trim() === '') {
                                                setOrderQty(1);
                                                setOrderQtyInput('1');
                                            }
                                        }}
                                        min={1}
                                    />
                                </div>
                                <button className="btn btn-primary full-width" onClick={placeOrder}>
                                    <DollarSign size={14} /> Place {orderSide.toUpperCase()} Order
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="empty-state">Select a market to trade</div>
                    )}
                </div>
            </div>
        </div>
    );
}
