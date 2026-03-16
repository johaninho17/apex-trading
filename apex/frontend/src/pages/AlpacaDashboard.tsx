import { useState, useEffect } from 'react';
import { RefreshCw, ArrowUpRight, ArrowDownRight, Zap, Brain, Loader2, Activity, BarChart3, Gauge } from 'lucide-react';
import './pages.css';

interface Portfolio {
    cash: number;
    portfolio_value: number;
    buying_power: number;
    positions: Array<{
        symbol: string;
        qty: number;
        avg_entry: number;
        current_price: number;
        unrealized_pl: number;
        unrealized_plpc: number;
    }>;
}

export default function AlpacaDashboard() {
    const PORTFOLIO_CACHE_KEY = 'alpaca_dashboard_portfolio_cache';
    const MOVERS_CACHE_KEY = 'alpaca_dashboard_movers_cache';
    const [mode, setMode] = useState<'paper' | 'live'>('paper');
    const [portfolio, setPortfolio] = useState<Portfolio | null>(() => {
        try {
            const raw = sessionStorage.getItem(PORTFOLIO_CACHE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    });
    const [movers, setMovers] = useState<any[]>([]);
    const [moversLoading, setMoversLoading] = useState(false);

    useEffect(() => {
        fetchData();
        fetchMovers(false);
    }, []);

    async function fetchData() {
        try {
            const [settingsRes, portfolioRes] = await Promise.allSettled([
                fetch('/api/v1/alpaca/settings').then((r) => r.json()),
                fetch('/api/v1/alpaca/portfolio').then((r) => r.json()),
            ]);

            const newMode = settingsRes.status === 'fulfilled' ? settingsRes.value.trading_mode : mode;
            const newPortfolio = portfolioRes.status === 'fulfilled' ? portfolioRes.value : portfolio;

            if (settingsRes.status === 'fulfilled') setMode(newMode);
            if (portfolioRes.status === 'fulfilled') {
                setPortfolio(newPortfolio);
                sessionStorage.setItem(PORTFOLIO_CACHE_KEY, JSON.stringify(newPortfolio));
            }

            // Sync header widget
            window.dispatchEvent(new CustomEvent('apex:portfolio-update', {
                detail: { portfolioValue: newPortfolio?.portfolio_value, tradingMode: newMode },
            }));
        } catch (e) {
            console.error('Fetch error:', e);
        }
    }

    // Listen for global mode toggle from header
    useEffect(() => {
        const handler = () => fetchData();
        window.addEventListener('apex:mode-changed', handler);
        return () => window.removeEventListener('apex:mode-changed', handler);
    }, []);

    async function fetchMovers(force: boolean) {
        setMoversLoading(true);
        try {
            const now = Date.now();
            if (!force) {
                try {
                    const raw = sessionStorage.getItem(MOVERS_CACHE_KEY);
                    const cached = raw ? JSON.parse(raw) : null;
                    if (cached?.ts && Array.isArray(cached?.movers) && now - cached.ts < 10 * 60 * 1000) {
                        setMovers(cached.movers);
                        setMoversLoading(false);
                        return;
                    }
                } catch {
                    // ignore malformed cache
                }
            }
            const res = await fetch(`/api/v1/alpaca/top-movers${force ? '?force=true' : ''}`).then(r => r.json());
            const next = res.movers || [];
            setMovers(next);
            sessionStorage.setItem(MOVERS_CACHE_KEY, JSON.stringify({ ts: now, movers: next }));
        } catch (e) {
            console.error('Top movers fetch failed:', e);
        }
        setMoversLoading(false);
    }

    const topGainer = movers.length ? [...movers].sort((a, b) => ((b.Change_Pct || b.change_pct || b.pct_change || 0) - (a.Change_Pct || a.change_pct || a.pct_change || 0)))[0] : null;
    const topLoser = movers.length ? [...movers].sort((a, b) => ((a.Change_Pct || a.change_pct || a.pct_change || 0) - (b.Change_Pct || b.change_pct || b.pct_change || 0)))[0] : null;
    const avgAi = movers.length
        ? movers.reduce((sum, m) => sum + Number(m.AI_Composite || m.ai_composite || m.Score || 0), 0) / movers.length
        : 0;
    const grossExposure = (portfolio?.positions || []).reduce((sum, p) => sum + Math.abs(Number(p.current_price) * Number(p.qty)), 0);

    return (
        <div className="page-grid">
            {/* Header Controls */}
            <div className="page-header">
                <div className="header-actions">
                    <button className="btn btn-ghost" onClick={fetchData}>
                        <RefreshCw size={14} /> Refresh
                    </button>
                </div>
            </div>

            {/* Portfolio Summary */}
            <div className="stats-row">
                <div className="stat-card glass-card">
                    <span className="stat-label">Portfolio Value</span>
                    <span className="stat-value mono">${portfolio?.portfolio_value?.toLocaleString() || '—'}</span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Cash Available</span>
                    <span className="stat-value mono">${portfolio?.cash?.toLocaleString() || '—'}</span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Buying Power</span>
                    <span className="stat-value mono">${portfolio?.buying_power?.toLocaleString() || '—'}</span>
                </div>
                <div className="stat-card glass-card">
                    <span className="stat-label">Positions</span>
                    <span className="stat-value mono">{portfolio?.positions?.length || 0}</span>
                </div>
            </div>

            {/* Positions Table */}
            {portfolio?.positions && portfolio.positions.length > 0 && (
                <div className="glass-card">
                    <h3 className="section-title">Open Positions</h3>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Qty</th>
                                <th>Avg Entry</th>
                                <th>Current</th>
                                <th>P/L</th>
                                <th>%</th>
                            </tr>
                        </thead>
                        <tbody>
                            {portfolio.positions.map((p) => (
                                <tr key={p.symbol}>
                                    <td><strong>{p.symbol}</strong></td>
                                    <td className="mono">{p.qty}</td>
                                    <td className="mono">${p.avg_entry.toFixed(2)}</td>
                                    <td className="mono">${p.current_price.toFixed(2)}</td>
                                    <td className={`mono ${p.unrealized_pl >= 0 ? 'text-green' : 'text-red'}`}>
                                        {p.unrealized_pl >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                                        ${Math.abs(p.unrealized_pl).toFixed(2)}
                                    </td>
                                    <td className={`mono ${p.unrealized_plpc >= 0 ? 'text-green' : 'text-red'}`}>
                                        {(p.unrealized_plpc * 100).toFixed(2)}%
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Top Movers */}
            <div className="glass-card">
                <h3 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Zap size={16} /> Top Movers
                    <button className="btn btn-ghost" onClick={() => fetchMovers(true)} style={{ marginLeft: 'auto', padding: '4px 10px', fontSize: '0.75rem' }} disabled={moversLoading}>
                        {moversLoading ? <Loader2 size={12} className="spin" /> : <RefreshCw size={12} />}
                    </button>
                </h3>
                {movers.length > 0 ? (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px' }}>
                        {movers.map((m: any, i: number) => {
                            const ticker = m.Ticker || m.ticker || m.Symbol || m.symbol || '??';
                            const price = m.Price || m.price || m.current_price || 0;
                            const changePct = m.Change_Pct || m.change_pct || m.pct_change || 0;
                            const aiScore = m.AI_Composite || m.ai_composite || m.Score || 0;
                            const isUp = changePct >= 0;
                            return (
                                <a key={i} href={`/alpaca/search/${ticker}`} style={{ textDecoration: 'none' }}>
                                    <div className="stat-card" style={{ padding: '12px', cursor: 'pointer', transition: 'all 0.2s' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                                            <span style={{ fontWeight: 700, color: 'var(--color-alpaca)', fontSize: '0.85rem' }}>{ticker}</span>
                                            <span className={`mono ${isUp ? 'text-green' : 'text-red'}`} style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '2px' }}>
                                                {isUp ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                                                {Math.abs(changePct).toFixed(1)}%
                                            </span>
                                        </div>
                                        <div className="mono" style={{ fontSize: '0.9rem', fontWeight: 600 }}>${price.toFixed(2)}</div>
                                        {aiScore > 0 && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginTop: '4px', fontSize: '0.7rem', color: 'var(--text-secondary)' }}>
                                                <Brain size={10} /> AI: <span style={{ color: aiScore >= 70 ? '#22c55e' : aiScore >= 50 ? '#f59e0b' : 'var(--text-secondary)', fontWeight: 600 }}>{aiScore}%</span>
                                            </div>
                                        )}
                                    </div>
                                </a>
                            );
                        })}
                    </div>
                ) : (
                    <div className="empty-state" style={{ padding: '20px' }}>
                        <p>{moversLoading ? 'Loading top movers...' : 'Click refresh to load top movers.'}</p>
                    </div>
                )}
            </div>

            {/* Market Pulse */}
            <div className="glass-card">
                <h3 className="section-title">
                    <Activity size={16} /> Market Pulse
                </h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px' }}>
                    <div className="stat-card" style={{ padding: '12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <BarChart3 size={14} />
                            <strong>Top Gainer</strong>
                        </div>
                        <div className="mono" style={{ fontSize: '0.86rem', color: 'var(--text-primary)' }}>
                            {topGainer ? `${topGainer.Ticker || topGainer.ticker || topGainer.Symbol || topGainer.symbol} ${(topGainer.Change_Pct || topGainer.change_pct || topGainer.pct_change || 0).toFixed(2)}%` : '—'}
                        </div>
                    </div>
                    <div className="stat-card" style={{ padding: '12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <BarChart3 size={14} />
                            <strong>Top Loser</strong>
                        </div>
                        <div className="mono" style={{ fontSize: '0.86rem', color: 'var(--text-primary)' }}>
                            {topLoser ? `${topLoser.Ticker || topLoser.ticker || topLoser.Symbol || topLoser.symbol} ${(topLoser.Change_Pct || topLoser.change_pct || topLoser.pct_change || 0).toFixed(2)}%` : '—'}
                        </div>
                    </div>
                    <div className="stat-card" style={{ padding: '12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <Gauge size={14} />
                            <strong>Avg AI Confidence</strong>
                        </div>
                        <div className="mono" style={{ fontSize: '0.9rem', color: '#9fc2ff' }}>
                            {movers.length ? `${avgAi.toFixed(1)}%` : '—'}
                        </div>
                    </div>
                    <div className="stat-card" style={{ padding: '12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                            <Activity size={14} />
                            <strong>Gross Exposure</strong>
                        </div>
                        <div className="mono" style={{ fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                            ${grossExposure.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
