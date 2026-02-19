import { useState, useEffect } from 'react';
import { Search, BookOpen, RefreshCw, SlidersHorizontal } from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import { loadCalcProfile, saveCalcProfile, loadProfileFromSettings } from '../lib/calcProfiles';
import type { CalcPreset, EventsCalcProfile } from '../lib/calcProfiles';
import './pages.css';

interface PolyMarket {
    condition_id: string;
    question: string;
    tokens: any[];
    active: boolean;
    closed: boolean;
    volume: number;
    volume_24hr: number;
    liquidity: number;
    end_date: string;
    image: string;
}

interface OrderBook {
    token_id: string;
    best_bid: number;
    best_ask: number;
    mid_price: number;
    spread: number;
    implied_probability: number;
    bids: any[];
    asks: any[];
}

function marketResearchScore(m: PolyMarket, profile: EventsCalcProfile): number {
    const liq = Math.max(0, Number(m.liquidity || 0));
    const vol = Math.max(0, Number(m.volume_24hr || 0));
    const liqNorm = Math.min(100, liq / 1000);
    const volNorm = Math.min(100, vol / 500);
    const closePenalty = profile.useExecutionRisk && m.closed ? 30 * profile.executionRiskPenalty : 0;
    const trendBonus = profile.useMomentumBoost && m.active ? 3 * profile.momentumWeight : 0;
    const depthBonus = profile.useDepthBoost ? liqNorm * profile.depthWeight * 0.05 : 0;
    const confAdj = profile.useConfidenceScaling ? volNorm * profile.confidenceWeight * 0.05 : 0;
    const volPenalty = profile.useVolatilityPenalty ? Math.max(0, 8 - (m.tokens?.length || 0)) * profile.volatilityPenalty : 0;
    const raw = 35
        + (liqNorm * profile.liquidityWeight * 0.38)
        + (volNorm * profile.spreadWeight * 0.42)
        + depthBonus
        + confAdj
        + trendBonus
        - closePenalty
        - volPenalty;
    return Math.max(0, Math.min(100, raw));
}

export default function PolymarketResearch() {
    const [markets, setMarkets] = useState<PolyMarket[]>([]);
    const [, setSelectedToken] = useState<string | null>(null);
    const [orderbook, setOrderbook] = useState<OrderBook | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [connectionStatus, setConnectionStatus] = useState<string>('checking...');
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<EventsCalcProfile>(loadCalcProfile('events'));

    useEffect(() => {
        checkHealth();
        fetchMarkets();
    }, []);

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

    async function checkHealth() {
        try {
            const res = await fetch('/api/v1/polymarket/health').then((r) => r.json());
            setConnectionStatus(res.status === 'connected' ? 'Connected' : `Error: ${res.error}`);
        } catch {
            setConnectionStatus('Backend offline');
        }
    }

    async function fetchMarkets() {
        setLoading(true);
        try {
            const params = searchQuery ? `?query=${searchQuery}&limit=30` : '?limit=30';
            const res = await fetch(`/api/v1/polymarket/markets${params}`).then((r) => r.json());
            setMarkets(res.markets || []);
        } catch (e) {
            console.error('Failed to fetch markets:', e);
        }
        setLoading(false);
    }

    async function selectToken(tokenId: string) {
        setSelectedToken(tokenId);
        try {
            const res = await fetch(`/api/v1/polymarket/book/${tokenId}`).then((r) => r.json());
            setOrderbook(res);
        } catch (e) {
            console.error('Failed to fetch book:', e);
        }
    }

    const rankedMarkets = [...markets].sort((a, b) => marketResearchScore(b, calcProfile) - marketResearchScore(a, calcProfile));

    return (
        <div className="page-grid">
            {/* Connection Status */}
            <div className="glass-card" style={{ padding: '12px 20px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span className={`status-dot ${connectionStatus === 'Connected' ? 'connected' : 'disconnected'}`} />
                        <span className="text-muted">Polymarket API: {connectionStatus}</span>
                    </div>
                    <span className="text-muted" style={{ fontSize: '11px' }}>⚠️ Read-only — No trading from US</span>
                </div>
            </div>

            {/* Search */}
            <div className="search-bar">
                <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open events profile">
                    <SlidersHorizontal size={14} />
                </button>
                <Search size={16} className="search-icon" />
                <input
                    type="text"
                    className="input search-input"
                    placeholder="Search prediction markets..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && fetchMarkets()}
                />
                <button className="btn btn-primary" onClick={fetchMarkets} disabled={loading}>
                    {loading ? <RefreshCw size={14} className="spin" /> : 'Search'}
                </button>
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

            <div className="two-col">
                {/* Markets List */}
                <div className="glass-card">
                    <h3 className="section-title"><BookOpen size={16} /> Active Markets ({rankedMarkets.length})</h3>
                    <div className="market-list">
                        {rankedMarkets.map((m) => (
                            <div
                                key={m.condition_id}
                                className="market-row"
                                onClick={() => {
                                    if (m.tokens?.[0]?.token_id) selectToken(m.tokens[0].token_id);
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                    {m.image && <img src={m.image} alt="" style={{ width: 28, height: 28, borderRadius: 6, objectFit: 'cover' }} />}
                                    <div style={{ flex: 1 }}>
                                        <div className="market-title">{m.question}</div>
                                        <div className="market-meta" style={{ display: 'flex', gap: '12px', marginTop: 2 }}>
                                            {m.tokens?.map((t: any, i: number) => (
                                                <span key={i} className="mono" style={{ fontSize: '0.75rem', color: t.outcome === 'Yes' ? '#22c55e' : '#ef4444' }}>
                                                    {t.outcome}: {(t.price * 100).toFixed(0)}¢
                                                </span>
                                            ))}
                                            <span className="text-muted" style={{ fontSize: '0.7rem' }}>Vol: ${m.volume_24hr >= 1000 ? (m.volume_24hr / 1000).toFixed(0) + 'K' : m.volume_24hr.toFixed(0)}</span>
                                            <span className="mono text-blue" style={{ fontSize: '0.7rem' }}>Play: {marketResearchScore(m, calcProfile).toFixed(0)}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ))}
                        {rankedMarkets.length === 0 && !loading && (
                            <div className="empty-state">No markets found. Try a different search term.</div>
                        )}
                    </div>
                </div>

                {/* Order Book Analysis */}
                <div className="glass-card">
                    <h3 className="section-title">📊 Order Book Analysis</h3>
                    {orderbook ? (
                        <div className="orderbook-analysis fade-in">
                            <div className="stats-row compact">
                                <div className="stat-card mini">
                                    <span className="stat-label">Best Bid</span>
                                    <span className="stat-value mono text-green">{orderbook.best_bid.toFixed(4)}</span>
                                </div>
                                <div className="stat-card mini">
                                    <span className="stat-label">Best Ask</span>
                                    <span className="stat-value mono text-red">{orderbook.best_ask.toFixed(4)}</span>
                                </div>
                                <div className="stat-card mini">
                                    <span className="stat-label">Mid Price</span>
                                    <span className="stat-value mono">{orderbook.mid_price.toFixed(4)}</span>
                                </div>
                            </div>

                            <div className="probability-bar">
                                <div className="prob-label">Implied Probability</div>
                                <div className="prob-track">
                                    <div
                                        className="prob-fill"
                                        style={{ width: `${orderbook.implied_probability * 100}%` }}
                                    />
                                </div>
                                <div className="prob-value mono">
                                    {(orderbook.implied_probability * 100).toFixed(1)}%
                                </div>
                            </div>

                            <div className="spread-info">
                                <span className="text-muted">Spread:</span>
                                <span className="mono">{(orderbook.spread * 100).toFixed(2)}¢</span>
                            </div>

                            <div className="depth-preview">
                                <h4>Top Bids</h4>
                                {orderbook.bids.slice(0, 5).map((b: any, i: number) => (
                                    <div key={i} className="depth-row bid">
                                        <span className="mono">{b.price}</span>
                                        <span className="mono text-muted">{b.size}</span>
                                    </div>
                                ))}
                                <h4>Top Asks</h4>
                                {orderbook.asks.slice(0, 5).map((a: any, i: number) => (
                                    <div key={i} className="depth-row ask">
                                        <span className="mono">{a.price}</span>
                                        <span className="mono text-muted">{a.size}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    ) : (
                        <div className="empty-state">Select a market to view order book data.</div>
                    )}
                </div>
            </div>
        </div>
    );
}
