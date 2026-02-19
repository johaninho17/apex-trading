import { useState } from 'react';
import { BarChart3, Play, Loader2, TrendingUp, DollarSign } from 'lucide-react';
import './BacktestPanel.css';

interface BacktestResult {
    total_trades: number;
    win_rate: number;
    profit_factor: number;
    total_return: number;
    trades: Array<{
        'Entry Date': string;
        'Exit Date': string;
        'Entry Price': number;
        'Exit Price': number;
        PnL: number;
        Reason: string;
        'Return %': number;
    }>;
    equity_curve: Array<{ Date: string; Equity: number }>;
    error?: string;
}

const STRATEGIES = [
    'Aggressive (Momentum)',
    'Conservative (Pullback)',
    'Trend Following (MA)',
];

interface BacktestPanelProps {
    ticker: string;
}

export default function BacktestPanel({ ticker: initialTicker }: BacktestPanelProps) {
    const [ticker, setTicker] = useState(initialTicker);
    const [strategy, setStrategy] = useState(STRATEGIES[0]);
    const [investment, setInvestment] = useState(10000);
    const [investmentInput, setInvestmentInput] = useState('10000');
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<BacktestResult | null>(null);
    const [error, setError] = useState('');

    async function runBacktest() {
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const res = await fetch('/api/v1/alpaca/backtest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker.toUpperCase(), strategy, investment }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            if (data.error) { setError(data.error); } else { setResult(data); }
        } catch (e: any) {
            setError(e.message || 'Backtest failed');
        }
        setLoading(false);
    }

    return (
        <div className="bt-panel">
            <div className="bt-header">
                <h3><BarChart3 size={18} /> Backtester</h3>
            </div>

            <div className="bt-controls">
                <div className="bt-field">
                    <label>Ticker</label>
                    <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
                        onKeyDown={e => e.key === 'Enter' && runBacktest()} placeholder="NVDA" className="bt-input mono" />
                </div>
                <div className="bt-field">
                    <label>Strategy</label>
                    <select value={strategy} onChange={e => setStrategy(e.target.value)} className="bt-input">
                        {STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                </div>
                <div className="bt-field">
                    <label>Investment</label>
                    <input
                        type="number"
                        value={investmentInput}
                        onChange={e => {
                            const raw = e.target.value;
                            setInvestmentInput(raw);
                            if (raw === '') return;
                            const next = parseInt(raw, 10);
                            if (!Number.isFinite(next)) return;
                            setInvestment(next);
                        }}
                        onBlur={() => {
                            if (investmentInput.trim() === '') {
                                setInvestment(10000);
                                setInvestmentInput('10000');
                            }
                        }}
                        min={100}
                        className="bt-input mono"
                    />
                </div>
                <button className="bt-run" onClick={runBacktest} disabled={loading || !ticker}>
                    {loading ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
                    {loading ? 'Running...' : 'Run'}
                </button>
            </div>

            {error && <div className="bt-error">{error}</div>}

            {loading && (
                <div className="bt-loading">
                    <Loader2 size={24} className="spin" />
                    <p>Backtesting {ticker} with {strategy}...</p>
                </div>
            )}

            {result && (
                <>
                    <div className="bt-stats">
                        <div className="bt-stat">
                            <span className="bt-stat-label">Trades</span>
                            <span className="bt-stat-value">{result.total_trades}</span>
                        </div>
                        <div className="bt-stat">
                            <span className="bt-stat-label">Win Rate</span>
                            <span className={`bt-stat-value ${result.win_rate >= 50 ? 'positive' : 'negative'}`}>
                                {result.win_rate}%
                            </span>
                        </div>
                        <div className="bt-stat">
                            <span className="bt-stat-label">PF</span>
                            <span className={`bt-stat-value ${result.profit_factor >= 1 ? 'positive' : 'negative'}`}>
                                {result.profit_factor}x
                            </span>
                        </div>
                        <div className="bt-stat">
                            <span className="bt-stat-label">Return</span>
                            <span className={`bt-stat-value ${result.total_return >= 0 ? 'positive' : 'negative'}`}>
                                {result.total_return >= 0 ? '+' : ''}${result.total_return.toFixed(2)}
                            </span>
                        </div>
                    </div>

                    {/* Equity Curve */}
                    {result.equity_curve.length > 0 && (
                        <div className="bt-equity">
                            <h4><TrendingUp size={14} /> Equity Curve</h4>
                            <svg viewBox="0 0 600 120" className="bt-equity-svg">
                                {(() => {
                                    const data = result.equity_curve;
                                    const min = Math.min(...data.map(d => d.Equity));
                                    const max = Math.max(...data.map(d => d.Equity));
                                    const range = max - min || 1;
                                    const points = data.map((d, i) => {
                                        const x = (i / (data.length - 1)) * 600;
                                        const y = 120 - ((d.Equity - min) / range) * 100 - 10;
                                        return `${x},${y}`;
                                    }).join(' ');
                                    const finalEquity = data[data.length - 1]?.Equity || investment;
                                    const color = finalEquity >= investment ? '#26a69a' : '#ef5350';
                                    return (
                                        <>
                                            <polyline fill="none" stroke={color} strokeWidth="2" points={points} />
                                            <polyline fill={`${color}22`} stroke="none"
                                                points={`0,120 ${points} 600,120`} />
                                        </>
                                    );
                                })()}
                            </svg>
                        </div>
                    )}

                    {/* Trade Log (collapsed) */}
                    {result.trades.length > 0 && (
                        <details className="bt-trades-details">
                            <summary><DollarSign size={14} /> Trade Log ({result.trades.length})</summary>
                            <div className="bt-trades-table-wrap">
                                <table className="bt-trades-table">
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Entry</th><th>Exit</th><th>P/L</th><th>Return</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.trades.map((t, i) => (
                                            <tr key={i} className={t.PnL >= 0 ? 'win' : 'loss'}>
                                                <td>{i + 1}</td>
                                                <td>{formatDate(t['Entry Date'])}</td>
                                                <td>{formatDate(t['Exit Date'])}</td>
                                                <td className={t.PnL >= 0 ? 'positive' : 'negative'}>
                                                    {t.PnL >= 0 ? '+' : ''}${t.PnL.toFixed(2)}
                                                </td>
                                                <td className={t['Return %'] >= 0 ? 'positive' : 'negative'}>
                                                    {t['Return %'] >= 0 ? '+' : ''}{t['Return %'].toFixed(2)}%
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </details>
                    )}
                </>
            )}
        </div>
    );
}

function formatDate(d: string): string {
    try {
        return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch { return d; }
}
