import { Fragment, useEffect, useMemo, useState } from 'react';
import { Activity, Coins, Play, Square, RefreshCw, ShieldAlert, Loader2, Save, Filter, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import './AlpacaCrypto.css';
import './pages.css';

type TradingMode = 'live' | 'offline';
type AccountMode = 'paper' | 'live';

interface CryptoAccount {
    cash: number;
    equity: number;
    buying_power: number;
    portfolio_value: number;
    account_number?: string;
    status?: string;
}

interface CryptoPosition {
    symbol: string;
    qty: number;
    avg_entry_price: number;
    current_price: number;
    market_value: number;
    unrealized_pl: number;
    unrealized_plpc: number;
    side: string;
}

interface CryptoAction {
    id: number;
    ts: number;
    action_type: string;
    symbol: string;
    side: string;
    qty?: number | null;
    notional?: number | null;
    price?: number | null;
    status: string;
    reason: string;
    payload?: Record<string, unknown>;
}

interface CryptoConfig {
    enabled: boolean;
    trading_mode: TradingMode;
    account_mode: AccountMode;
    poll_interval_sec: number;
    timeframe: string;
    symbols: string[];
    auto_discover_pairs: boolean;
    auto_discover_limit: number;
    auto_discover_quote: string;
    auto_discover_tradable_only: boolean;
    min_order_notional_usd: number;
    max_open_positions: number;
    max_notional_per_trade: number;
    max_total_exposure: number;
    max_daily_drawdown_pct: number;
    cooldown_sec: number;
    anti_spam_sec: number;
    short_term: {
        mean_reversion_enabled: boolean;
        breakout_enabled: boolean;
        base_notional: number;
        breakout_notional: number;
    };
    long_term: {
        ma_crossover_enabled: boolean;
        dca_enabled: boolean;
        dca_notional: number;
        dca_interval_min: number;
    };
    synthetic_exits: {
        enabled: boolean;
        take_profit_pct: number;
        stop_loss_pct: number;
    };
}

interface BotStatus {
    runtime?: {
        running: boolean;
        started_at?: number | null;
        last_cycle_at?: number | null;
        iterations?: number;
        last_error?: string | null;
        halted?: boolean;
        halted_reason?: string | null;
        last_status?: Record<string, unknown>;
    };
    persisted?: {
        running?: boolean;
        last_heartbeat?: number | null;
        iterations?: number;
        halted?: boolean;
        halted_reason?: string | null;
    };
}

function fmtTs(ts?: number | null): string {
    if (!ts) return '—';
    try {
        return new Date(ts).toLocaleString();
    } catch {
        return String(ts);
    }
}

function fmtNum(value: number | null | undefined, digits = 4): string {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return Number(value).toFixed(digits);
}

function fmtActionLabel(value: string | null | undefined): string {
    const raw = String(value || '').trim();
    if (!raw) return '—';
    return raw
        .replace(/_/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, (m) => m.toUpperCase());
}

function defaultConfig(): CryptoConfig {
    return {
        enabled: true,
        trading_mode: 'offline',
        account_mode: 'paper',
        poll_interval_sec: 30,
        timeframe: '1Min',
        symbols: ['BTC/USD', 'ETH/USD'],
        auto_discover_pairs: false,
        auto_discover_limit: 20,
        auto_discover_quote: 'USD',
        auto_discover_tradable_only: true,
        min_order_notional_usd: 10,
        max_open_positions: 3,
        max_notional_per_trade: 15,
        max_total_exposure: 250,
        max_daily_drawdown_pct: 4,
        cooldown_sec: 90,
        anti_spam_sec: 30,
        short_term: {
            mean_reversion_enabled: true,
            breakout_enabled: true,
            base_notional: 6,
            breakout_notional: 7.5,
        },
        long_term: {
            ma_crossover_enabled: true,
            dca_enabled: true,
            dca_notional: 4,
            dca_interval_min: 180,
        },
        synthetic_exits: {
            enabled: true,
            take_profit_pct: 3,
            stop_loss_pct: 1.8,
        },
    };
}

export default function AlpacaCrypto() {
    const [loading, setLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [savingConfig, setSavingConfig] = useState(false);
    const [account, setAccount] = useState<CryptoAccount | null>(null);
    const [positions, setPositions] = useState<CryptoPosition[]>([]);
    const [actions, setActions] = useState<CryptoAction[]>([]);
    const [botStatus, setBotStatus] = useState<BotStatus>({});
    const [config, setConfig] = useState<CryptoConfig>(defaultConfig());
    const [symbolsText, setSymbolsText] = useState('BTC/USD, ETH/USD');
    const [statusMsg, setStatusMsg] = useState<string>('');

    const [actionSearch, setActionSearch] = useState('');
    const [actionStatusFilter, setActionStatusFilter] = useState('all');
    const [actionTypeFilter, setActionTypeFilter] = useState('all');
    const [actionSort, setActionSort] = useState<'newest' | 'oldest'>('newest');
    const [actionPage, setActionPage] = useState(1);
    const [actionPageSize, setActionPageSize] = useState(20);
    const [expandedActionId, setExpandedActionId] = useState<number | null>(null);
    const [showClearActionsConfirm, setShowClearActionsConfirm] = useState(false);
    const [clearingActions, setClearingActions] = useState(false);

    const stats = useMemo(() => {
        const exposure = positions.reduce((sum, p) => sum + Math.abs(Number(p.market_value || 0)), 0);
        const unrealized = positions.reduce((sum, p) => sum + Number(p.unrealized_pl || 0), 0);
        return {
            exposure,
            unrealized,
            running: !!botStatus.runtime?.running,
            halted: !!botStatus.runtime?.halted,
        };
    }, [positions, botStatus]);

    const actionTypeOptions = useMemo(
        () => Array.from(new Set(actions.map((a) => String(a.action_type || '').trim()).filter(Boolean))).sort(),
        [actions],
    );

    const actionStatusCounts = useMemo(() => {
        const counts: Record<string, number> = {};
        for (const action of actions) {
            const key = String(action.status || 'info').toLowerCase();
            counts[key] = (counts[key] || 0) + 1;
        }
        return counts;
    }, [actions]);

    const filteredActions = useMemo(() => {
        const search = actionSearch.trim().toLowerCase();
        const rows = actions.filter((a) => {
            if (actionStatusFilter !== 'all' && String(a.status || '').toLowerCase() !== actionStatusFilter) {
                return false;
            }
            if (actionTypeFilter !== 'all' && String(a.action_type || '') !== actionTypeFilter) {
                return false;
            }
            if (!search) return true;
            const blob = [
                a.action_type,
                a.symbol,
                a.side,
                a.status,
                a.reason,
                a.qty !== null && a.qty !== undefined ? String(a.qty) : '',
                a.notional !== null && a.notional !== undefined ? String(a.notional) : '',
                a.price !== null && a.price !== undefined ? String(a.price) : '',
            ]
                .join(' ')
                .toLowerCase();
            return blob.includes(search);
        });
        rows.sort((a, b) => {
            const delta = Number(a.ts || 0) - Number(b.ts || 0);
            return actionSort === 'newest' ? -delta : delta;
        });
        return rows;
    }, [actions, actionSearch, actionStatusFilter, actionTypeFilter, actionSort]);

    const totalActionPages = useMemo(
        () => Math.max(1, Math.ceil(filteredActions.length / Math.max(1, actionPageSize))),
        [filteredActions.length, actionPageSize],
    );

    const pagedActions = useMemo(() => {
        const start = (actionPage - 1) * actionPageSize;
        return filteredActions.slice(start, start + actionPageSize);
    }, [filteredActions, actionPage, actionPageSize]);

    const actionPageWindow = useMemo(() => {
        let start = Math.max(1, actionPage - 2);
        let end = Math.min(totalActionPages, start + 4);
        start = Math.max(1, end - 4);
        const out: number[] = [];
        for (let i = start; i <= end; i += 1) out.push(i);
        return out;
    }, [actionPage, totalActionPages]);

    useEffect(() => {
        setActionPage(1);
    }, [actionSearch, actionStatusFilter, actionTypeFilter, actionSort, actionPageSize]);

    useEffect(() => {
        if (actionPage > totalActionPages) setActionPage(totalActionPages);
    }, [actionPage, totalActionPages]);

    async function fetchAll() {
        setLoading(true);
        try {
            const [acctRes, posRes, actionsRes, statusRes, cfgRes] = await Promise.all([
                fetch('/api/v1/alpaca/crypto/account'),
                fetch('/api/v1/alpaca/crypto/positions'),
                fetch('/api/v1/alpaca/crypto/actions?limit=220'),
                fetch('/api/v1/alpaca/crypto/bot/status'),
                fetch('/api/v1/alpaca/crypto/bot/config'),
            ]);

            const [acct, p, act, st, cfg] = await Promise.all([
                acctRes.json(),
                posRes.json(),
                actionsRes.json(),
                statusRes.json(),
                cfgRes.json(),
            ]);

            setAccount(acct || null);
            setPositions(Array.isArray(p?.items) ? p.items : []);
            setActions(Array.isArray(act?.items) ? act.items : []);
            setBotStatus(st || {});

            if (cfg?.config && typeof cfg.config === 'object') {
                const next = { ...defaultConfig(), ...cfg.config };
                setConfig(next);
                if (Array.isArray(next.symbols)) {
                    setSymbolsText(next.symbols.join(', '));
                }
            }
        } catch (e) {
            setStatusMsg((e as Error).message || 'Failed to load crypto data');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        fetchAll();
        const t = window.setInterval(() => {
            void fetchAll();
        }, 10000);
        return () => window.clearInterval(t);
    }, []);

    async function callAction(url: string, body?: Record<string, unknown>) {
        setSubmitting(true);
        setStatusMsg('');
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: body ? JSON.stringify(body) : undefined,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
            await fetchAll();
            return data;
        } catch (e) {
            setStatusMsg((e as Error).message || 'Request failed');
            throw e;
        } finally {
            setSubmitting(false);
        }
    }

    async function onStartBot() {
        await callAction('/api/v1/alpaca/crypto/bot/start');
    }

    async function onStopBot() {
        await callAction('/api/v1/alpaca/crypto/bot/stop');
    }

    async function onFlatten() {
        await callAction('/api/v1/alpaca/crypto/flatten');
    }

    async function onSaveConfig() {
        setSavingConfig(true);
        setStatusMsg('');
        try {
            const cleanedSymbols = symbolsText
                .split(',')
                .map(s => s.trim().toUpperCase())
                .filter(Boolean);
            const updates = {
                ...config,
                symbols: cleanedSymbols,
            };
            const res = await fetch('/api/v1/alpaca/crypto/bot/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ updates }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
            if (data?.config && Array.isArray(data.config.symbols)) {
                setConfig(data.config);
                setSymbolsText(data.config.symbols.join(', '));
            }
            setStatusMsg('Crypto config saved.');
            await fetchAll();
        } catch (e) {
            setStatusMsg((e as Error).message || 'Failed to save config');
        } finally {
            setSavingConfig(false);
        }
    }

    async function onClearAllActions() {
        setClearingActions(true);
        setStatusMsg('');
        try {
            const res = await fetch('/api/v1/alpaca/crypto/actions/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
            setShowClearActionsConfirm(false);
            setExpandedActionId(null);
            setStatusMsg(`Removed ${Number(data?.removed || 0)} action entries.`);
            await fetchAll();
        } catch (e) {
            setStatusMsg((e as Error).message || 'Failed to clear actions');
        } finally {
            setClearingActions(false);
        }
    }

    function actionStatusClass(status: string): string {
        const raw = String(status || '').toLowerCase();
        if (raw === 'error') return 'is-error';
        if (raw === 'success') return 'is-success';
        if (raw === 'blocked') return 'is-blocked';
        if (raw === 'signal') return 'is-signal';
        return 'is-info';
    }

    return (
        <div className="page-grid crypto-page">
            <div className="scanner-header">
                <div>
                    <h1><Coins size={20} /> Crypto Terminal</h1>
                    <p className="subtitle">Auto-trading (paper) + manual execution + live action feed.</p>
                    <div className="last-scan-time">
                        <Activity size={13} />
                        <span>Last heartbeat: {fmtTs(botStatus.persisted?.last_heartbeat || botStatus.runtime?.last_cycle_at || null)}</span>
                    </div>
                </div>
                <div className="header-actions">
                    <button className="btn btn-ghost" onClick={() => void fetchAll()} disabled={loading}>
                        {loading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Refresh
                    </button>
                    <button className="btn btn-scan" onClick={() => void onStartBot()} disabled={submitting || stats.running}>
                        <Play size={14} /> Start Bot
                    </button>
                    <button className="btn btn-indigo-opposite" onClick={() => void onStopBot()} disabled={submitting || !stats.running}>
                        <Square size={14} /> Stop Bot
                    </button>
                    <button className="btn btn-red" onClick={() => void onFlatten()} disabled={submitting}>
                        <ShieldAlert size={14} /> Flatten
                    </button>
                </div>
            </div>

            <div className="stats-row compact">
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Trading Mode</span>
                    <span className={`stat-value mono ${config.trading_mode === 'live' ? 'text-green' : 'text-red'}`}>
                        {config.trading_mode.toUpperCase()}
                    </span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Account Mode</span>
                    <span className={`stat-value mono ${config.account_mode === 'live' ? 'text-green' : 'text-red'}`}>
                        {config.account_mode.toUpperCase()}
                    </span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Bot State</span>
                    <span className={`stat-value mono ${stats.running ? 'text-green' : 'text-red'}`}>
                        {stats.running ? 'RUNNING' : 'STOPPED'}
                    </span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Exposure</span>
                    <span className="stat-value mono">${stats.exposure.toFixed(2)}</span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Unrealized P/L</span>
                    <span className={`stat-value mono ${stats.unrealized >= 0 ? 'text-green' : 'text-red'}`}>
                        {stats.unrealized >= 0 ? '+' : '-'}${Math.abs(stats.unrealized).toFixed(2)}
                    </span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Equity</span>
                    <span className="stat-value mono">${Number(account?.equity || 0).toFixed(2)}</span>
                </div>
                <div className="stat-card glass-card mini">
                    <span className="stat-label">Cash</span>
                    <span className="stat-value mono">${Number(account?.cash || 0).toFixed(2)}</span>
                </div>
            </div>

            {statusMsg && <div className="crypto-status-banner">{statusMsg}</div>}
            {botStatus.runtime?.halted && (
                <div className="crypto-status-banner error">
                    Risk halted: {botStatus.runtime?.halted_reason || 'No reason provided'}
                </div>
            )}

            <div className="two-col">
                <div className="glass-card crypto-card">
                    <h3 className="section-title"><Activity size={16} /> Bot Configuration</h3>
                    <div className="crypto-form-grid">
                        <label>
                            Trading
                            <select value={config.trading_mode} onChange={(e) => setConfig(prev => ({ ...prev, trading_mode: e.target.value as TradingMode }))}>
                                <option value="offline">Offline</option>
                                <option value="live">Live</option>
                            </select>
                        </label>
                        <label>
                            Account
                            <select value={config.account_mode} onChange={(e) => setConfig(prev => ({ ...prev, account_mode: e.target.value as AccountMode }))}>
                                <option value="paper">Paper</option>
                                <option value="live">Live</option>
                            </select>
                        </label>
                        <label>
                            Timeframe
                            <select value={config.timeframe} onChange={(e) => setConfig(prev => ({ ...prev, timeframe: e.target.value }))}>
                                <option value="1Min">1Min</option>
                                <option value="5Min">5Min</option>
                                <option value="15Min">15Min</option>
                                <option value="1Hour">1Hour</option>
                            </select>
                        </label>
                        <label>
                            Poll (sec)
                            <input type="number" value={config.poll_interval_sec} onChange={(e) => setConfig(prev => ({ ...prev, poll_interval_sec: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Max Open Positions
                            <input type="number" value={config.max_open_positions} onChange={(e) => setConfig(prev => ({ ...prev, max_open_positions: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Max Notional / Trade
                            <input type="number" value={config.max_notional_per_trade} onChange={(e) => setConfig(prev => ({ ...prev, max_notional_per_trade: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Max Total Exposure
                            <input type="number" value={config.max_total_exposure} onChange={(e) => setConfig(prev => ({ ...prev, max_total_exposure: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Max Daily Drawdown %
                            <input type="number" value={config.max_daily_drawdown_pct} onChange={(e) => setConfig(prev => ({ ...prev, max_daily_drawdown_pct: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Cooldown (sec)
                            <input type="number" value={config.cooldown_sec} onChange={(e) => setConfig(prev => ({ ...prev, cooldown_sec: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Anti-spam (sec)
                            <input type="number" value={config.anti_spam_sec} onChange={(e) => setConfig(prev => ({ ...prev, anti_spam_sec: Number(e.target.value || 0) }))} />
                        </label>
                        <label>
                            Min Order Notional ($)
                            <input
                                type="number"
                                value={config.min_order_notional_usd}
                                onChange={(e) => setConfig(prev => ({ ...prev, min_order_notional_usd: Number(e.target.value || 0) }))}
                            />
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.auto_discover_pairs}
                                onChange={(e) => setConfig(prev => ({ ...prev, auto_discover_pairs: e.target.checked }))}
                            />
                            Auto Discover Pairs
                        </label>
                        <label>
                            Discover Limit
                            <input
                                type="number"
                                value={config.auto_discover_limit}
                                onChange={(e) => setConfig(prev => ({ ...prev, auto_discover_limit: Number(e.target.value || 0) }))}
                            />
                        </label>
                        <label>
                            Discover Quote
                            <input
                                value={config.auto_discover_quote}
                                onChange={(e) => setConfig(prev => ({ ...prev, auto_discover_quote: e.target.value.toUpperCase() }))}
                                placeholder="USD"
                            />
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.auto_discover_tradable_only}
                                onChange={(e) => setConfig(prev => ({ ...prev, auto_discover_tradable_only: e.target.checked }))}
                            />
                            Tradable Only
                        </label>
                        <label className="wide">
                            Symbols (manual fallback)
                            <input
                                value={symbolsText}
                                onChange={(e) => setSymbolsText(e.target.value)}
                                placeholder="BTC/USD, ETH/USD"
                                disabled={config.auto_discover_pairs}
                            />
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.short_term.mean_reversion_enabled}
                                onChange={(e) => setConfig(prev => ({ ...prev, short_term: { ...prev.short_term, mean_reversion_enabled: e.target.checked } }))}
                            />
                            Mean Reversion
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.short_term.breakout_enabled}
                                onChange={(e) => setConfig(prev => ({ ...prev, short_term: { ...prev.short_term, breakout_enabled: e.target.checked } }))}
                            />
                            Breakout Momentum
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.long_term.ma_crossover_enabled}
                                onChange={(e) => setConfig(prev => ({ ...prev, long_term: { ...prev.long_term, ma_crossover_enabled: e.target.checked } }))}
                            />
                            MA Crossover
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.long_term.dca_enabled}
                                onChange={(e) => setConfig(prev => ({ ...prev, long_term: { ...prev.long_term, dca_enabled: e.target.checked } }))}
                            />
                            Dynamic DCA
                        </label>
                        <label className="checkbox">
                            <input
                                type="checkbox"
                                checked={config.synthetic_exits.enabled}
                                onChange={(e) => setConfig(prev => ({ ...prev, synthetic_exits: { ...prev.synthetic_exits, enabled: e.target.checked } }))}
                            />
                            Synthetic Exits
                        </label>
                    </div>
                    <div className="button-row">
                        <button className="btn btn-scan" onClick={() => void onSaveConfig()} disabled={savingConfig}>
                            {savingConfig ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save Config
                        </button>
                    </div>
                </div>

                <div className="glass-card crypto-card">
                    <h3 className="section-title">Open Crypto Positions ({positions.length})</h3>
                    <div className="crypto-table-wrap">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Qty</th>
                                    <th>Avg Entry</th>
                                    <th>Current</th>
                                    <th>Value</th>
                                    <th>P/L</th>
                                </tr>
                            </thead>
                            <tbody>
                                {positions.map((p) => (
                                    <tr key={p.symbol}>
                                        <td><strong>{p.symbol}</strong></td>
                                        <td className="mono">{Number(p.qty).toFixed(6)}</td>
                                        <td className="mono">${Number(p.avg_entry_price).toFixed(4)}</td>
                                        <td className="mono">${Number(p.current_price).toFixed(4)}</td>
                                        <td className="mono">${Number(p.market_value).toFixed(2)}</td>
                                        <td className={`mono ${Number(p.unrealized_pl) >= 0 ? 'text-green' : 'text-red'}`}>
                                            {Number(p.unrealized_pl) >= 0 ? '+' : '-'}${Math.abs(Number(p.unrealized_pl || 0)).toFixed(2)}
                                        </td>
                                    </tr>
                                ))}
                                {positions.length === 0 && (
                                    <tr><td colSpan={6} className="crypto-empty">No open crypto positions.</td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div>
                <div className="glass-card crypto-card">
                    <div className="action-feed-head">
                        <h3 className="section-title">Action Feed ({filteredActions.length}/{actions.length})</h3>
                        <div className="action-feed-actions">
                            <button className="btn btn-ghost" onClick={() => void fetchAll()} disabled={loading}>
                                <RefreshCw size={14} /> Sync
                            </button>
                            <div className="action-clear-wrap">
                                <button
                                    className="btn btn-red"
                                    onClick={() => setShowClearActionsConfirm((prev) => !prev)}
                                    disabled={clearingActions || actions.length === 0}
                                >
                                    <Trash2 size={14} /> Clear All
                                </button>
                                {showClearActionsConfirm && (
                                    <div className="action-clear-confirm">
                                        <p>Remove all action feed entries?</p>
                                        <div className="action-clear-confirm-buttons">
                                            <button
                                                className="btn btn-ghost"
                                                onClick={() => setShowClearActionsConfirm(false)}
                                                disabled={clearingActions}
                                            >
                                                Cancel
                                            </button>
                                            <button
                                                className="btn btn-red"
                                                onClick={() => void onClearAllActions()}
                                                disabled={clearingActions}
                                            >
                                                {clearingActions ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />} Confirm
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="action-toolbar">
                        <div className="action-search-wrap">
                            <Filter size={13} />
                            <input
                                value={actionSearch}
                                onChange={(e) => setActionSearch(e.target.value)}
                                placeholder="Search type, symbol, reason, status..."
                            />
                        </div>
                        <select value={actionStatusFilter} onChange={(e) => setActionStatusFilter(e.target.value)}>
                            <option value="all">All Statuses</option>
                            <option value="success">Success</option>
                            <option value="signal">Signal</option>
                            <option value="blocked">Blocked</option>
                            <option value="error">Error</option>
                            <option value="info">Info</option>
                        </select>
                        <select value={actionTypeFilter} onChange={(e) => setActionTypeFilter(e.target.value)}>
                            <option value="all">All Types</option>
                            {actionTypeOptions.map((t) => (
                                <option key={t} value={t}>{fmtActionLabel(t)}</option>
                            ))}
                        </select>
                        <select value={actionSort} onChange={(e) => setActionSort(e.target.value as 'newest' | 'oldest')}>
                            <option value="newest">Newest First</option>
                            <option value="oldest">Oldest First</option>
                        </select>
                        <select value={actionPageSize} onChange={(e) => setActionPageSize(Number(e.target.value || 20))}>
                            <option value={20}>20 / page</option>
                            <option value={50}>50 / page</option>
                            <option value={100}>100 / page</option>
                        </select>
                    </div>

                    <div className="action-summary-row">
                        <span className="action-chip">Success: {actionStatusCounts.success || 0}</span>
                        <span className="action-chip">Signals: {actionStatusCounts.signal || 0}</span>
                        <span className="action-chip">Blocked: {actionStatusCounts.blocked || 0}</span>
                        <span className="action-chip">Errors: {actionStatusCounts.error || 0}</span>
                        <span className="action-chip">Info: {actionStatusCounts.info || 0}</span>
                    </div>

                    <div className="action-pagination">
                        <button className="btn btn-ghost" disabled={actionPage <= 1} onClick={() => setActionPage((p) => Math.max(1, p - 1))}>Prev</button>
                        {actionPageWindow.map((page) => (
                            <button
                                key={page}
                                className={`btn ${page === actionPage ? 'btn-scan' : 'btn-ghost'}`}
                                onClick={() => setActionPage(page)}
                            >
                                {page}
                            </button>
                        ))}
                        <button className="btn btn-ghost" disabled={actionPage >= totalActionPages} onClick={() => setActionPage((p) => Math.min(totalActionPages, p + 1))}>Next</button>
                        <span className="action-page-meta">Page {actionPage} / {totalActionPages}</span>
                    </div>

                    <div className="crypto-table-wrap action-feed-wrap">
                        <table className="data-table action-table">
                            <thead>
                                <tr>
                                    <th />
                                    <th>Time</th>
                                    <th>Type</th>
                                    <th>Symbol</th>
                                    <th>Side</th>
                                    <th>Status</th>
                                    <th>Notional</th>
                                    <th>Qty</th>
                                    <th>Price</th>
                                    <th>Reason</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pagedActions.map((a) => {
                                    const isExpanded = expandedActionId === a.id;
                                    return (
                                        <Fragment key={a.id}>
                                            <tr className={`action-row ${isExpanded ? 'is-expanded' : ''}`}>
                                                <td>
                                                    <button
                                                        className="action-expand-btn"
                                                        onClick={() => setExpandedActionId((prev) => (prev === a.id ? null : a.id))}
                                                        aria-label={isExpanded ? 'Collapse action details' : 'Expand action details'}
                                                    >
                                                        {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                                                    </button>
                                                </td>
                                                <td className="mono">{fmtTs(a.ts)}</td>
                                                <td>{fmtActionLabel(a.action_type)}</td>
                                                <td className="mono">{a.symbol || '—'}</td>
                                                <td>{a.side ? <span className={`action-side-pill ${String(a.side).toLowerCase() === 'buy' ? 'is-buy' : 'is-sell'}`}>{String(a.side).toUpperCase()}</span> : '—'}</td>
                                                <td><span className={`action-status-pill ${actionStatusClass(a.status)}`}>{fmtActionLabel(a.status)}</span></td>
                                                <td className="mono">{a.notional !== null && a.notional !== undefined ? `$${fmtNum(a.notional, 2)}` : '—'}</td>
                                                <td className="mono">{fmtNum(a.qty, 6)}</td>
                                                <td className="mono">{a.price !== null && a.price !== undefined ? `$${fmtNum(a.price, 4)}` : '—'}</td>
                                                <td title={a.reason} className="action-reason-cell">{a.reason || '—'}</td>
                                            </tr>
                                            {isExpanded && (
                                                <tr className="action-detail-row">
                                                    <td />
                                                    <td colSpan={9}>
                                                        <div className="action-detail-grid">
                                                            <div><span>ID:</span> <strong>{a.id}</strong></div>
                                                            <div><span>Timestamp:</span> <strong>{fmtTs(a.ts)}</strong></div>
                                                            <div><span>Type:</span> <strong>{fmtActionLabel(a.action_type)}</strong></div>
                                                            <div><span>Status:</span> <strong>{fmtActionLabel(a.status)}</strong></div>
                                                            <div><span>Symbol:</span> <strong>{a.symbol || '—'}</strong></div>
                                                            <div><span>Side:</span> <strong>{a.side || '—'}</strong></div>
                                                            <div><span>Qty:</span> <strong>{fmtNum(a.qty, 6)}</strong></div>
                                                            <div><span>Notional:</span> <strong>{a.notional !== null && a.notional !== undefined ? `$${fmtNum(a.notional, 2)}` : '—'}</strong></div>
                                                            <div><span>Price:</span> <strong>{a.price !== null && a.price !== undefined ? `$${fmtNum(a.price, 4)}` : '—'}</strong></div>
                                                        </div>
                                                        {a.reason && <div className="action-detail-reason"><span>Reason:</span> {a.reason}</div>}
                                                        <pre className="action-detail-payload">{JSON.stringify(a.payload || {}, null, 2)}</pre>
                                                    </td>
                                                </tr>
                                            )}
                                        </Fragment>
                                    );
                                })}
                                {pagedActions.length === 0 && (
                                    <tr><td colSpan={10} className="crypto-empty">No actions match your filters.</td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <div className="action-pagination">
                        <button className="btn btn-ghost" disabled={actionPage <= 1} onClick={() => setActionPage((p) => Math.max(1, p - 1))}>Prev</button>
                        {actionPageWindow.map((page) => (
                            <button
                                key={`bottom-${page}`}
                                className={`btn ${page === actionPage ? 'btn-scan' : 'btn-ghost'}`}
                                onClick={() => setActionPage(page)}
                            >
                                {page}
                            </button>
                        ))}
                        <button className="btn btn-ghost" disabled={actionPage >= totalActionPages} onClick={() => setActionPage((p) => Math.min(totalActionPages, p + 1))}>Next</button>
                        <span className="action-page-meta">Showing {(actionPage - 1) * actionPageSize + (pagedActions.length ? 1 : 0)}-{(actionPage - 1) * actionPageSize + pagedActions.length} of {filteredActions.length}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
