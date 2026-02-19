import { useState, useEffect } from 'react';
import { Wallet, TrendingUp, TrendingDown, DollarSign, RefreshCw, Loader2, BarChart3, ShieldCheck } from 'lucide-react';
import TradePanel from '../components/TradePanel';
import './AlpacaPortfolio.css';

interface Position {
    symbol: string;
    qty: number;
    avg_entry: number;
    current_price: number;
    market_value: number;
    unrealized_pl: number;
    unrealized_plpc: number;
    side: string;
}

interface PortfolioData {
    cash: number;
    portfolio_value: number;
    buying_power: number;
    equity: number;
    trading_mode: string;
    positions: Position[];
    error?: string;
}

interface EquityHistory {
    timestamps: number[];
    equity: number[];
    profit_loss: number[];
    total_return_pct: number;
    total_return_dollar: number;
    timeframe: string;
}

const TIMEFRAMES = ['1W', '1M', '3M', '6M', '1Y', 'ALL'] as const;

export default function AlpacaPortfolio() {
    const [data, setData] = useState<PortfolioData | null>(null);
    const [loading, setLoading] = useState(true);
    const [equityHistory, setEquityHistory] = useState<EquityHistory | null>(null);
    const [selectedTimeframe, setSelectedTimeframe] = useState('1M');
    const [equityLoading, setEquityLoading] = useState(false);
    const [tradeModal, setTradeModal] = useState<{ symbol: string; qty: number } | null>(null);

    useEffect(() => {
        fetchPortfolio();

        // Listen for global mode changes
        const handleModeChange = () => {
            setLoading(true);
            setTimeout(fetchPortfolio, 500); // Small delay to let backend switch
        };
        window.addEventListener('apex:mode-changed', handleModeChange);
        return () => window.removeEventListener('apex:mode-changed', handleModeChange);
    }, []);

    async function fetchPortfolio() {
        setLoading(true);
        try {
            const res = await fetch('/api/v1/alpaca/portfolio');
            const d = await res.json();
            setData(d);
        } catch {
            setData(null);
        }
        setLoading(false);
    }

    async function fetchEquityHistory(tf: string) {
        setEquityLoading(true);
        try {
            const res = await fetch(`/api/v1/alpaca/portfolio/history?timeframe=${tf}`);
            const d = await res.json();
            setEquityHistory(d);
        } catch {
            setEquityHistory(null);
        }
        setEquityLoading(false);
    }

    useEffect(() => { fetchEquityHistory(selectedTimeframe); }, [selectedTimeframe]);

    function openSellModal(symbol: string, qty: number) {
        setTradeModal({ symbol, qty });
    }

    const totalPL = data?.positions.reduce((sum, p) => sum + p.unrealized_pl, 0) || 0;

    return (
        <div className="portfolio-page">
            <div className="portfolio-header">
                <div>
                    <h1><Wallet size={24} /> Portfolio</h1>
                    <p className="subtitle">Alpaca account overview and position management</p>
                </div>
                <div className="header-actions">
                    {data?.trading_mode && (
                        <div className={`mode-badge ${data.trading_mode}`}>
                            <ShieldCheck size={16} />
                            <span style={{ fontWeight: 700, fontSize: '0.8rem', textTransform: 'uppercase' }}>
                                {data.trading_mode}
                            </span>
                        </div>
                    )}
                    <button onClick={fetchPortfolio} className="btn-refresh"><RefreshCw size={16} /> Refresh</button>
                </div>
            </div>

            {data?.error && (
                <div className="error-banner">{data.error}. Check your Alpaca API keys.</div>
            )}

            {loading ? (
                <div className="loading-state"><Loader2 size={32} className="spin" /><p>Loading portfolio...</p></div>
            ) : (
                <>
                    {/* Account Summary */}
                    <div className="account-summary">
                        <div className="summary-card">
                            <span className="summary-label">Portfolio Value</span>
                            <span className="summary-value">${(data?.portfolio_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                        </div>
                        <div className="summary-card">
                            <span className="summary-label">Cash</span>
                            <span className="summary-value">${(data?.cash || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                        </div>
                        <div className="summary-card">
                            <span className="summary-label">Buying Power</span>
                            <span className="summary-value">${(data?.buying_power || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                        </div>
                        <div className="summary-card">
                            <span className="summary-label">Unrealized P/L</span>
                            <span className={`summary-value ${totalPL >= 0 ? 'positive' : 'negative'}`}>
                                {totalPL >= 0 ? '+' : ''}{totalPL.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                            </span>
                        </div>
                    </div>

                    {/* Equity Curve */}
                    <div className="equity-curve-section">
                        <div className="equity-header">
                            <h2><BarChart3 size={18} /> Equity Curve</h2>
                            <div className="timeframe-buttons">
                                {TIMEFRAMES.map(tf => (
                                    <button
                                        key={tf}
                                        className={`tf-btn ${selectedTimeframe === tf ? 'active' : ''}`}
                                        onClick={() => setSelectedTimeframe(tf)}
                                    >
                                        {tf}
                                    </button>
                                ))}
                            </div>
                        </div>
                        {equityHistory && equityHistory.equity.length > 1 ? (
                            <>
                                <div className="equity-stats">
                                    <div className={`equity-return ${equityHistory.total_return_pct >= 0 ? 'positive' : 'negative'}`}>
                                        <span className="return-label">Total Return</span>
                                        <span className="return-value">
                                            {equityHistory.total_return_pct >= 0 ? '+' : ''}{equityHistory.total_return_pct}%
                                        </span>
                                        <span className="return-dollar">
                                            ({equityHistory.total_return_dollar >= 0 ? '+' : ''}${equityHistory.total_return_dollar.toLocaleString(undefined, { minimumFractionDigits: 2 })})
                                        </span>
                                    </div>
                                    <div className="equity-meta">
                                        <span>High: ${Math.max(...equityHistory.equity).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                                        <span>Low: ${Math.min(...equityHistory.equity).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                                    </div>
                                </div>
                                <EquityChart equity={equityHistory.equity} timestamps={equityHistory.timestamps} />
                            </>
                        ) : (
                            <div className="equity-empty">
                                {equityLoading ? <Loader2 size={24} className="spin" /> : <p>No equity data available for this timeframe.</p>}
                            </div>
                        )}
                    </div>

                    {/* Positions Table */}
                    <div className="positions-section">
                        <h2>Open Positions ({data?.positions.length || 0})</h2>
                        {(!data?.positions || data.positions.length === 0) ? (
                            <div className="empty-state">
                                <DollarSign size={48} />
                                <h3>No Open Positions</h3>
                                <p>Use the Scanner or Search page to find and execute trades.</p>
                            </div>
                        ) : (
                            <div className="positions-table-container">
                                <table className="positions-table">
                                    <thead>
                                        <tr>
                                            <th>Symbol</th>
                                            <th>Qty</th>
                                            <th>Avg Entry</th>
                                            <th>Current</th>
                                            <th>Mkt Value</th>
                                            <th>P/L</th>
                                            <th>P/L %</th>
                                            <th>Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.positions.map(p => (
                                            <tr key={p.symbol}>
                                                <td className="symbol-cell">{p.symbol}</td>
                                                <td>{p.qty}</td>
                                                <td>${p.avg_entry.toFixed(2)}</td>
                                                <td>${p.current_price.toFixed(2)}</td>
                                                <td>${p.market_value.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                                                <td className={p.unrealized_pl >= 0 ? 'positive' : 'negative'}>
                                                    <span className="pl-cell">
                                                        {p.unrealized_pl >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                                                        ${Math.abs(p.unrealized_pl).toFixed(2)}
                                                    </span>
                                                </td>
                                                <td className={p.unrealized_plpc >= 0 ? 'positive' : 'negative'}>
                                                    {p.unrealized_plpc >= 0 ? '+' : ''}{p.unrealized_plpc.toFixed(2)}%
                                                </td>
                                                <td>
                                                    <button
                                                        className="btn-close-pos"
                                                        onClick={() => openSellModal(p.symbol, p.qty)}
                                                    >
                                                        Close / Manage
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            )}

            {/* Sell Modal */}
            {tradeModal && (
                <div className="portfolio-modal-overlay" onClick={() => setTradeModal(null)}>
                    <div className="portfolio-modal-content" onClick={e => e.stopPropagation()}>
                        <TradePanel
                            symbol={tradeModal.symbol}
                            defaultSide="sell"
                            defaultQty={tradeModal.qty.toString()}
                            onClose={() => setTradeModal(null)}
                            onOrderSuccess={() => {
                                setTradeModal(null);
                                setTimeout(fetchPortfolio, 1000); // refresh after order
                            }}
                        />
                    </div>
                </div>
            )}
        </div>
    );
}

function EquityChart({ equity, timestamps }: { equity: number[]; timestamps: number[] }) {
    const W = 800, H = 200, PAD = 20;
    const min = Math.min(...equity);
    const max = Math.max(...equity);
    const range = max - min || 1;
    const isPositive = equity[equity.length - 1] >= equity[0];

    const points = equity.map((v, i) => {
        const x = PAD + (i / (equity.length - 1)) * (W - PAD * 2);
        const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
        return `${x},${y}`;
    });
    const polyline = points.join(' ');
    const areaPath = `M ${PAD},${H - PAD} L ${points.join(' L ')} L ${W - PAD},${H - PAD} Z`;
    const color = isPositive ? '#00e88f' : '#ff4466';

    return (
        <div className="equity-chart-container">
            <svg viewBox={`0 0 ${W} ${H}`} className="equity-chart-svg">
                <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={color} stopOpacity="0.3" />
                        <stop offset="100%" stopColor={color} stopOpacity="0.02" />
                    </linearGradient>
                </defs>
                <path d={areaPath} fill="url(#equityGrad)" />
                <polyline points={polyline} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
                {/* Labels */}
                <text x={PAD} y={H - 4} fill="#5a5a72" fontSize="10" fontFamily="var(--font-mono)">{timestamps.length > 0 ? new Date(timestamps[0] * 1000).toLocaleDateString() : ''}</text>
                <text x={W - PAD} y={H - 4} fill="#5a5a72" fontSize="10" fontFamily="var(--font-mono)" textAnchor="end">{timestamps.length > 0 ? new Date(timestamps[timestamps.length - 1] * 1000).toLocaleDateString() : ''}</text>
                <text x={PAD - 2} y={PAD + 4} fill="#5a5a72" fontSize="10" fontFamily="var(--font-mono)" textAnchor="end">${max.toLocaleString()}</text>
                <text x={PAD - 2} y={H - PAD} fill="#5a5a72" fontSize="10" fontFamily="var(--font-mono)" textAnchor="end">${min.toLocaleString()}</text>
            </svg>
        </div>
    );
}
