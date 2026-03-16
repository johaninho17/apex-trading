import { useState, useEffect, useCallback } from 'react';
import './ConfigSettings.css';

interface ConfigData {
    stocks: {
        atr_multipliers: { aggressive: number; conservative: number; trend: number };
        rsi_period: number;
        sma_periods: number[];
        ema_periods: number[];
        backtest_targets: {
            aggressive_target_pct: number;
            aggressive_stop_pct: number;
            conservative_target_pct: number;
            conservative_stop_pct: number;
        };
        scanner_min_price: number;
        scanner_min_volume: number;
        quick_settings: {
            min_play_score: number;
            hide_below_min_score: boolean;
            auto_sort_play_score: boolean;
        };
        calc_profile: StocksCalcProfileSettings;
    };
    dfs: {
        sniper: { min_line_diff: number; poll_interval: number; max_stale_window: number; max_movements: number };
        slip_builder: { slip_sizes: number[]; min_edge_pct: number; top_n_slips: number; max_pool_size: number };
        ev_calculator: { default_stake: number; kelly_fraction_cap: number };
        quick_settings: {
            min_edge: number;
            plays_only: boolean;
            side_filter: 'all' | 'over' | 'under';
            auto_sort_play_score: boolean;
            sleeper_markets_only: boolean;
        };
        consensus: {
            min_books: number;
            line_window: number;
            main_line_only: boolean;
            min_trend_count: number;
            weights: {
                bookmaker: number;
                pinnacle: number;
                fanduel: number;
                draftkings: number;
            };
        };
        calc_profile: DfsCalcProfileSettings;
    };
    events: {
        quick_settings: {
            min_play_score: number;
            sort_by_play_score: boolean;
            show_scans_in_activity: boolean;
        };
        calc_profile: EventsCalcProfileSettings;
        kalshi: {
            trading_mode: 'live' | 'offline';
            max_position_size: number;
            max_total_exposure: number;
            stop_loss_pct: number;
            arbitrage_min_profit: number;
            market_maker_spread: number;
            copy_trade_ratio: number;
            copy_follow_accounts: string[];
            bot_detection_threshold: number;
            bot_interval: number;
        };
    };
}

interface DfsCalcProfileSettings {
    edgeWeight: number;
    confidenceWeight: number;
    stakeWeight: number;
    kellyCapPct: number;
    useDevig: boolean;
    useConfidenceShrink: boolean;
    useVigPenalty: boolean;
    useTrendBonus: boolean;
    useKellyCap: boolean;
    useCorrelationPenalty: boolean;
}

interface StocksCalcProfileSettings {
    atrWeight: number;
    rsiWeight: number;
    emaWeight: number;
    crossoverWeight: number;
    volatilityPenalty: number;
    liquidityWeight: number;
    trendStrengthBonus: number;
    scoreSmoothing: number;
    useRsiFilter: boolean;
    useAtrTrendGate: boolean;
    useCrossoverBoost: boolean;
    useLiquidityFilter: boolean;
}

interface EventsCalcProfileSettings {
    spreadWeight: number;
    liquidityWeight: number;
    depthWeight: number;
    momentumWeight: number;
    confidenceWeight: number;
    volatilityPenalty: number;
    executionRiskPenalty: number;
    scalpSensitivity: number;
    useDepthBoost: boolean;
    useVolatilityPenalty: boolean;
    useExecutionRisk: boolean;
    useMomentumBoost: boolean;
    useConfidenceScaling: boolean;
}

export default function ConfigSettings() {
    const [config, setConfig] = useState<ConfigData | null>(null);
    const [defaults, setDefaults] = useState<ConfigData | null>(null);
    const [dirty, setDirty] = useState(false);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<'idle' | 'saved' | 'error'>('idle');
    const [activeSection, setActiveSection] = useState<'stocks' | 'dfs' | 'events' | 'weights'>('stocks');

    useEffect(() => {
        Promise.all([
            fetch('/api/v1/settings').then(r => r.json()),
            fetch('/api/v1/settings/defaults').then(r => r.json()),
        ]).then(([cfg, defs]) => {
            setConfig(cfg.config);
            setDefaults(defs.config);
        }).catch(() => setStatus('error'));
    }, []);

    const updateField = useCallback((path: string, value: any) => {
        if (!config) return;
        const keys = path.split('.');
        const updated = JSON.parse(JSON.stringify(config));
        let obj: any = updated;
        for (let i = 0; i < keys.length - 1; i++) obj = obj[keys[i]];
        obj[keys[keys.length - 1]] = value;
        setConfig(updated);
        setDirty(true);
        setStatus('idle');
    }, [config]);

    const handleSave = async () => {
        if (!config) return;
        setSaving(true);
        try {
            const res = await fetch('/api/v1/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ updates: config }),
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(data.config);
                setDirty(false);
                setStatus('saved');
                window.dispatchEvent(new CustomEvent('apex:settings-updated', { detail: data.config }));
                setTimeout(() => setStatus('idle'), 2000);
            } else {
                setStatus('error');
            }
        } catch {
            setStatus('error');
        }
        setSaving(false);
    };

    const handleReset = async () => {
        if (!confirm('Reset all settings to defaults?')) return;
        try {
            const res = await fetch('/api/v1/settings/reset', { method: 'POST' });
            if (res.ok) {
                const data = await res.json();
                setConfig(data.config);
                setDirty(false);
                setStatus('saved');
                window.dispatchEvent(new CustomEvent('apex:settings-updated', { detail: data.config }));
            }
        } catch {
            setStatus('error');
        }
    };

    if (!config) {
        return <div className="config-settings"><div className="settings-loading">Loading settings…</div></div>;
    }

    const isDefault = (path: string, value: any): boolean => {
        if (!defaults) return true;
        const keys = path.split('.');
        let obj: any = defaults;
        for (const k of keys) {
            if (obj === undefined) return true;
            obj = obj[k];
        }
        return JSON.stringify(obj) === JSON.stringify(value);
    };

    return (
        <div className="config-settings">
            {/* Section Tabs */}
            <div className="settings-section-tabs">
                {(['stocks', 'dfs', 'events', 'weights'] as const).map(s => (
                    <button
                        key={s}
                        className={`section-tab ${activeSection === s ? 'active' : ''}`}
                        onClick={() => setActiveSection(s)}
                    >
                        {s === 'stocks' ? '📈 Stocks' : s === 'dfs' ? '🏆 DFS' : s === 'events' ? '⚡ Events' : '⚖ Weights'}
                    </button>
                ))}
            </div>

            {/* Stocks Section */}
            {activeSection === 'stocks' && (
                <div className="settings-section">
                    <div className="setting-group">
                        <h3>ATR Stop-Loss Multipliers</h3>
                        <p className="setting-description">Controls how far the stop loss is placed from entry using ATR. Higher = wider stop = less risk of being stopped out, but larger potential loss.</p>
                        <div className="settings-grid">
                            <SettingRow label="Aggressive" path="stocks.atr_multipliers.aggressive" value={config.stocks.atr_multipliers.aggressive} onChange={updateField} suffix="× ATR" isDefault={isDefault('stocks.atr_multipliers.aggressive', config.stocks.atr_multipliers.aggressive)} />
                            <SettingRow label="Conservative" path="stocks.atr_multipliers.conservative" value={config.stocks.atr_multipliers.conservative} onChange={updateField} suffix="× ATR" isDefault={isDefault('stocks.atr_multipliers.conservative', config.stocks.atr_multipliers.conservative)} />
                            <SettingRow label="Trend Follower" path="stocks.atr_multipliers.trend" value={config.stocks.atr_multipliers.trend} onChange={updateField} suffix="× ATR" isDefault={isDefault('stocks.atr_multipliers.trend', config.stocks.atr_multipliers.trend)} />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Indicator Periods</h3>
                        <p className="setting-description">Lookback periods for technical indicators. Shorter periods react faster but produce more noise.</p>
                        <div className="settings-grid">
                            <SettingRow label="RSI Period" path="stocks.rsi_period" value={config.stocks.rsi_period} onChange={updateField} suffix="days" isDefault={isDefault('stocks.rsi_period', config.stocks.rsi_period)} type="int" />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Backtest Targets</h3>
                        <p className="setting-description">Target profit and stop loss percentages used by the backtesting engine.</p>
                        <div className="settings-grid">
                            <SettingRow label="Aggressive Target" path="stocks.backtest_targets.aggressive_target_pct" value={config.stocks.backtest_targets.aggressive_target_pct} onChange={updateField} suffix="%" isDefault={isDefault('stocks.backtest_targets.aggressive_target_pct', config.stocks.backtest_targets.aggressive_target_pct)} />
                            <SettingRow label="Aggressive Stop" path="stocks.backtest_targets.aggressive_stop_pct" value={config.stocks.backtest_targets.aggressive_stop_pct} onChange={updateField} suffix="%" isDefault={isDefault('stocks.backtest_targets.aggressive_stop_pct', config.stocks.backtest_targets.aggressive_stop_pct)} />
                            <SettingRow label="Conservative Target" path="stocks.backtest_targets.conservative_target_pct" value={config.stocks.backtest_targets.conservative_target_pct} onChange={updateField} suffix="%" isDefault={isDefault('stocks.backtest_targets.conservative_target_pct', config.stocks.backtest_targets.conservative_target_pct)} />
                            <SettingRow label="Conservative Stop" path="stocks.backtest_targets.conservative_stop_pct" value={config.stocks.backtest_targets.conservative_stop_pct} onChange={updateField} suffix="%" isDefault={isDefault('stocks.backtest_targets.conservative_stop_pct', config.stocks.backtest_targets.conservative_stop_pct)} />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Scanner Filters</h3>
                        <p className="setting-description">Minimum thresholds for stocks to appear in scanner results.</p>
                        <div className="settings-grid">
                            <SettingRow label="Min Price" path="stocks.scanner_min_price" value={config.stocks.scanner_min_price} onChange={updateField} prefix="$" isDefault={isDefault('stocks.scanner_min_price', config.stocks.scanner_min_price)} />
                            <SettingRow label="Min Volume" path="stocks.scanner_min_volume" value={config.stocks.scanner_min_volume} onChange={updateField} suffix="shares" isDefault={isDefault('stocks.scanner_min_volume', config.stocks.scanner_min_volume)} type="int" />
                        </div>
                    </div>
                    <QuickSettingsGroup
                        title="Quick Settings"
                        description="Fast scan behavior toggles used by stocks pages."
                        controls={[
                            { kind: 'number', label: 'Min Play Score', path: 'stocks.quick_settings.min_play_score', value: config.stocks.quick_settings.min_play_score },
                            { kind: 'toggle', label: 'Hide Below Min Score', path: 'stocks.quick_settings.hide_below_min_score', value: config.stocks.quick_settings.hide_below_min_score },
                            { kind: 'toggle', label: 'Auto Sort by Play Score', path: 'stocks.quick_settings.auto_sort_play_score', value: config.stocks.quick_settings.auto_sort_play_score },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                    <CalcProfileGroup
                        title="Calculation Profile"
                        description="Scoring profile used by stocks scanner/search play ranking."
                        basePath="stocks.calc_profile"
                        profile={config.stocks.calc_profile}
                        controls={[
                            { kind: 'number', key: 'atrWeight', label: 'ATR Weight' },
                            { kind: 'number', key: 'rsiWeight', label: 'RSI Weight' },
                            { kind: 'number', key: 'emaWeight', label: 'EMA Weight' },
                            { kind: 'number', key: 'crossoverWeight', label: 'Crossover Weight' },
                            { kind: 'number', key: 'volatilityPenalty', label: 'Volatility Penalty' },
                            { kind: 'number', key: 'liquidityWeight', label: 'Liquidity Weight' },
                            { kind: 'number', key: 'trendStrengthBonus', label: 'Trend Bonus' },
                            { kind: 'number', key: 'scoreSmoothing', label: 'Score Smoothing' },
                            { kind: 'toggle', key: 'useRsiFilter', label: 'Use RSI Filter' },
                            { kind: 'toggle', key: 'useAtrTrendGate', label: 'Use ATR Gate' },
                            { kind: 'toggle', key: 'useCrossoverBoost', label: 'Use Crossover Boost' },
                            { kind: 'toggle', key: 'useLiquidityFilter', label: 'Use Liquidity Filter' },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                </div>
            )}

            {/* DFS Section */}
            {activeSection === 'dfs' && (
                <div className="settings-section">
                    <div className="setting-group">
                        <h3>Sniper Configuration</h3>
                        <p className="setting-description">Board lag detection thresholds. Min Line Diff controls how far a sharp book line must move from a DFS line before triggering an alert.</p>
                        <div className="settings-grid">
                            <SettingRow label="Min Line Diff" path="dfs.sniper.min_line_diff" value={config.dfs.sniper.min_line_diff} onChange={updateField} suffix="pts" isDefault={isDefault('dfs.sniper.min_line_diff', config.dfs.sniper.min_line_diff)} />
                            <SettingRow label="Poll Interval" path="dfs.sniper.poll_interval" value={config.dfs.sniper.poll_interval} onChange={updateField} suffix="sec" isDefault={isDefault('dfs.sniper.poll_interval', config.dfs.sniper.poll_interval)} type="int" />
                            <SettingRow label="Max Stale Window" path="dfs.sniper.max_stale_window" value={config.dfs.sniper.max_stale_window} onChange={updateField} suffix="sec" isDefault={isDefault('dfs.sniper.max_stale_window', config.dfs.sniper.max_stale_window)} type="int" />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Slip Builder</h3>
                        <p className="setting-description">Controls for auto-generated parlay combinations.</p>
                        <div className="settings-grid">
                            <SettingRow label="Min Edge %" path="dfs.slip_builder.min_edge_pct" value={config.dfs.slip_builder.min_edge_pct} onChange={updateField} suffix="%" isDefault={isDefault('dfs.slip_builder.min_edge_pct', config.dfs.slip_builder.min_edge_pct)} />
                            <SettingRow label="Top N Slips" path="dfs.slip_builder.top_n_slips" value={config.dfs.slip_builder.top_n_slips} onChange={updateField} isDefault={isDefault('dfs.slip_builder.top_n_slips', config.dfs.slip_builder.top_n_slips)} type="int" />
                            <SettingRow label="Max Pool Size" path="dfs.slip_builder.max_pool_size" value={config.dfs.slip_builder.max_pool_size} onChange={updateField} isDefault={isDefault('dfs.slip_builder.max_pool_size', config.dfs.slip_builder.max_pool_size)} type="int" />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>EV Calculator</h3>
                        <p className="setting-description">Defaults for the Expected Value calculator tool.</p>
                        <div className="settings-grid">
                            <SettingRow label="Default Stake" path="dfs.ev_calculator.default_stake" value={config.dfs.ev_calculator.default_stake} onChange={updateField} prefix="$" isDefault={isDefault('dfs.ev_calculator.default_stake', config.dfs.ev_calculator.default_stake)} />
                            <SettingRow label="Kelly Fraction Cap" path="dfs.ev_calculator.kelly_fraction_cap" value={config.dfs.ev_calculator.kelly_fraction_cap} onChange={updateField} isDefault={isDefault('dfs.ev_calculator.kelly_fraction_cap', config.dfs.ev_calculator.kelly_fraction_cap)} />
                        </div>
                    </div>
                    <QuickSettingsGroup
                        title="Quick Settings"
                        description="Scanner defaults for DFS pages."
                        controls={[
                            { kind: 'number', label: 'Min Edge', path: 'dfs.quick_settings.min_edge', value: config.dfs.quick_settings.min_edge },
                            { kind: 'toggle', label: 'Plays Only', path: 'dfs.quick_settings.plays_only', value: config.dfs.quick_settings.plays_only },
                            { kind: 'select', label: 'Side Filter', path: 'dfs.quick_settings.side_filter', value: config.dfs.quick_settings.side_filter, options: ['all', 'over', 'under'] },
                            { kind: 'toggle', label: 'Auto Sort by Play Score', path: 'dfs.quick_settings.auto_sort_play_score', value: config.dfs.quick_settings.auto_sort_play_score },
                            { kind: 'toggle', label: 'Sleeper Markets Only', path: 'dfs.quick_settings.sleeper_markets_only', value: config.dfs.quick_settings.sleeper_markets_only },
                            { kind: 'number', label: 'Consensus Min Books', path: 'dfs.consensus.min_books', value: config.dfs.consensus.min_books },
                            { kind: 'number', label: 'Consensus Line Window', path: 'dfs.consensus.line_window', value: config.dfs.consensus.line_window },
                            { kind: 'toggle', label: 'Main Line Only', path: 'dfs.consensus.main_line_only', value: config.dfs.consensus.main_line_only },
                            { kind: 'number', label: 'Min Trend Count', path: 'dfs.consensus.min_trend_count', value: config.dfs.consensus.min_trend_count },
                            { kind: 'number', label: 'Weight: BookMaker', path: 'dfs.consensus.weights.bookmaker', value: config.dfs.consensus.weights.bookmaker },
                            { kind: 'number', label: 'Weight: Pinnacle', path: 'dfs.consensus.weights.pinnacle', value: config.dfs.consensus.weights.pinnacle },
                            { kind: 'number', label: 'Weight: FanDuel', path: 'dfs.consensus.weights.fanduel', value: config.dfs.consensus.weights.fanduel },
                            { kind: 'number', label: 'Weight: DraftKings', path: 'dfs.consensus.weights.draftkings', value: config.dfs.consensus.weights.draftkings },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                    <CalcProfileGroup
                        title="Calculation Profile"
                        description="Scoring profile used by DFS smart scanner ranking and stake recommendations."
                        basePath="dfs.calc_profile"
                        profile={config.dfs.calc_profile}
                        controls={[
                            { kind: 'number', key: 'edgeWeight', label: 'Edge Weight' },
                            { kind: 'number', key: 'confidenceWeight', label: 'Confidence Weight' },
                            { kind: 'number', key: 'stakeWeight', label: 'Stake Weight' },
                            { kind: 'number', key: 'kellyCapPct', label: 'Kelly Cap', suffix: '%' },
                            { kind: 'toggle', key: 'useDevig', label: 'Use De-vig' },
                            { kind: 'toggle', key: 'useConfidenceShrink', label: 'Use Confidence Shrink' },
                            { kind: 'toggle', key: 'useVigPenalty', label: 'Use Vig Penalty' },
                            { kind: 'toggle', key: 'useTrendBonus', label: 'Use Trend Bonus' },
                            { kind: 'toggle', key: 'useKellyCap', label: 'Use Kelly Cap' },
                            { kind: 'toggle', key: 'useCorrelationPenalty', label: 'Use Correlation Penalty' },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                </div>
            )}

            {/* Weights Section */}
            {activeSection === 'weights' && (
                <div className="settings-section">
                    <div className="setting-group">
                        <h3>DFS Consensus Weights</h3>
                        <p className="setting-description">Apex weighted consensus uses only these 4 books. Higher values increase influence in Apex Odds and consensus probability.</p>
                        <div className="settings-grid">
                            <SettingRow label="BookMaker Weight" path="dfs.consensus.weights.bookmaker" value={config.dfs.consensus.weights.bookmaker} onChange={updateField} isDefault={isDefault('dfs.consensus.weights.bookmaker', config.dfs.consensus.weights.bookmaker)} />
                            <SettingRow label="Pinnacle Weight" path="dfs.consensus.weights.pinnacle" value={config.dfs.consensus.weights.pinnacle} onChange={updateField} isDefault={isDefault('dfs.consensus.weights.pinnacle', config.dfs.consensus.weights.pinnacle)} />
                            <SettingRow label="FanDuel Weight" path="dfs.consensus.weights.fanduel" value={config.dfs.consensus.weights.fanduel} onChange={updateField} isDefault={isDefault('dfs.consensus.weights.fanduel', config.dfs.consensus.weights.fanduel)} />
                            <SettingRow label="DraftKings Weight" path="dfs.consensus.weights.draftkings" value={config.dfs.consensus.weights.draftkings} onChange={updateField} isDefault={isDefault('dfs.consensus.weights.draftkings', config.dfs.consensus.weights.draftkings)} />
                        </div>
                    </div>
                    <div className="setting-group">
                        <h3>Consensus Quality Gates</h3>
                        <p className="setting-description">Controls line selection stability for scanner quality.</p>
                        <div className="settings-grid">
                            <SettingRow label="Minimum Books" path="dfs.consensus.min_books" value={config.dfs.consensus.min_books} onChange={updateField} isDefault={isDefault('dfs.consensus.min_books', config.dfs.consensus.min_books)} type="int" />
                            <SettingRow label="Line Window" path="dfs.consensus.line_window" value={config.dfs.consensus.line_window} onChange={updateField} isDefault={isDefault('dfs.consensus.line_window', config.dfs.consensus.line_window)} />
                            <SettingRow label="Minimum Trend Count" path="dfs.consensus.min_trend_count" value={config.dfs.consensus.min_trend_count} onChange={updateField} isDefault={isDefault('dfs.consensus.min_trend_count', config.dfs.consensus.min_trend_count)} type="int" />
                        </div>
                        <div className="profile-toggle-grid">
                            <button
                                type="button"
                                className={`profile-toggle-chip ${config.dfs.consensus.main_line_only ? 'on' : 'off'} ${!isDefault('dfs.consensus.main_line_only', config.dfs.consensus.main_line_only) ? 'modified' : ''}`}
                                onClick={() => updateField('dfs.consensus.main_line_only', !config.dfs.consensus.main_line_only)}
                            >
                                <span className="profile-toggle-indicator">{config.dfs.consensus.main_line_only ? '✓' : '○'}</span>
                                <span>Main Line Only</span>
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Events Section */}
            {activeSection === 'events' && (
                <div className="settings-section">
                    <div className="setting-group">
                        <h3>Trading Mode</h3>
                        <p className="setting-description">Controls whether Kalshi bot executions are allowed. Candidate alerts still fire in real time in both modes.</p>
                        <div className="mode-toggle-row">
                            <button
                                className={`mode-option ${config.events.kalshi.trading_mode === 'live' ? 'active' : ''}`}
                                onClick={() => updateField('events.kalshi.trading_mode', 'live')}
                            >
                                Live
                            </button>
                            <button
                                className={`mode-option ${config.events.kalshi.trading_mode === 'offline' ? 'active' : ''}`}
                                onClick={() => updateField('events.kalshi.trading_mode', 'offline')}
                            >
                                Offline
                            </button>
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Risk Management</h3>
                        <p className="setting-description">Position limits and risk controls for Kalshi event contracts.</p>
                        <div className="settings-grid">
                            <SettingRow label="Max Position" path="events.kalshi.max_position_size" value={config.events.kalshi.max_position_size} onChange={updateField} prefix="$" isDefault={isDefault('events.kalshi.max_position_size', config.events.kalshi.max_position_size)} />
                            <SettingRow label="Max Exposure" path="events.kalshi.max_total_exposure" value={config.events.kalshi.max_total_exposure} onChange={updateField} prefix="$" isDefault={isDefault('events.kalshi.max_total_exposure', config.events.kalshi.max_total_exposure)} />
                            <SettingRow label="Stop Loss" path="events.kalshi.stop_loss_pct" value={config.events.kalshi.stop_loss_pct} onChange={updateField} suffix="%" isDefault={isDefault('events.kalshi.stop_loss_pct', config.events.kalshi.stop_loss_pct)} />
                        </div>
                    </div>

                    <div className="setting-group">
                        <h3>Bot Strategy</h3>
                        <p className="setting-description">Strategy-specific parameters for the automated trading bot.</p>
                        <div className="settings-grid">
                            <SettingRow label="Arb Min Profit" path="events.kalshi.arbitrage_min_profit" value={config.events.kalshi.arbitrage_min_profit} onChange={updateField} prefix="$" isDefault={isDefault('events.kalshi.arbitrage_min_profit', config.events.kalshi.arbitrage_min_profit)} />
                            <SettingRow label="MM Spread" path="events.kalshi.market_maker_spread" value={config.events.kalshi.market_maker_spread} onChange={updateField} suffix="%" isDefault={isDefault('events.kalshi.market_maker_spread', config.events.kalshi.market_maker_spread)} />
                            <SettingRow label="Copy Ratio" path="events.kalshi.copy_trade_ratio" value={config.events.kalshi.copy_trade_ratio} onChange={updateField} suffix="(0–1)" isDefault={isDefault('events.kalshi.copy_trade_ratio', config.events.kalshi.copy_trade_ratio)} />
                            <SettingRow label="Bot Detection" path="events.kalshi.bot_detection_threshold" value={config.events.kalshi.bot_detection_threshold} onChange={updateField} isDefault={isDefault('events.kalshi.bot_detection_threshold', config.events.kalshi.bot_detection_threshold)} />
                            <SettingRow label="Bot Interval" path="events.kalshi.bot_interval" value={config.events.kalshi.bot_interval} onChange={updateField} suffix="sec" isDefault={isDefault('events.kalshi.bot_interval', config.events.kalshi.bot_interval)} type="int" />
                        </div>
                        <div className={`setting-row ${!isDefault('events.kalshi.copy_follow_accounts', config.events.kalshi.copy_follow_accounts) ? 'modified' : ''}`}>
                            <label className="setting-label">
                                Copy Follow Accounts
                                {!isDefault('events.kalshi.copy_follow_accounts', config.events.kalshi.copy_follow_accounts) && <span className="modified-dot" title="Modified from default" />}
                            </label>
                            <div className="setting-input-wrap">
                                <input
                                    type="text"
                                    className="setting-input"
                                    value={config.events.kalshi.copy_follow_accounts.join(', ')}
                                    placeholder="acct_1, acct_2"
                                    onChange={e => updateField(
                                        'events.kalshi.copy_follow_accounts',
                                        e.target.value.split(',').map(v => v.trim()).filter(Boolean),
                                    )}
                                />
                            </div>
                        </div>
                    </div>
                    <QuickSettingsGroup
                        title="Quick Settings"
                        description="Live feed and ranking behavior for events pages."
                        controls={[
                            { kind: 'number', label: 'Min Play Score', path: 'events.quick_settings.min_play_score', value: config.events.quick_settings.min_play_score },
                            { kind: 'toggle', label: 'Sort by Play Score', path: 'events.quick_settings.sort_by_play_score', value: config.events.quick_settings.sort_by_play_score },
                            { kind: 'toggle', label: 'Show Scan Events', path: 'events.quick_settings.show_scans_in_activity', value: config.events.quick_settings.show_scans_in_activity },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                    <CalcProfileGroup
                        title="Calculation Profile"
                        description="Shared events profile used across Kalshi, Scalper, Polymarket, and Convergence play ranking."
                        basePath="events.calc_profile"
                        profile={config.events.calc_profile}
                        controls={[
                            { kind: 'number', key: 'spreadWeight', label: 'Spread Weight' },
                            { kind: 'number', key: 'liquidityWeight', label: 'Liquidity Weight' },
                            { kind: 'number', key: 'depthWeight', label: 'Depth Weight' },
                            { kind: 'number', key: 'momentumWeight', label: 'Momentum Weight' },
                            { kind: 'number', key: 'confidenceWeight', label: 'Confidence Weight' },
                            { kind: 'number', key: 'volatilityPenalty', label: 'Volatility Penalty' },
                            { kind: 'number', key: 'executionRiskPenalty', label: 'Execution Risk Penalty' },
                            { kind: 'number', key: 'scalpSensitivity', label: 'Scalp Sensitivity' },
                            { kind: 'toggle', key: 'useDepthBoost', label: 'Use Depth Boost' },
                            { kind: 'toggle', key: 'useVolatilityPenalty', label: 'Use Volatility Penalty' },
                            { kind: 'toggle', key: 'useExecutionRisk', label: 'Use Execution Risk' },
                            { kind: 'toggle', key: 'useMomentumBoost', label: 'Use Momentum Boost' },
                            { kind: 'toggle', key: 'useConfidenceScaling', label: 'Use Confidence Scaling' },
                        ]}
                        isDefault={isDefault}
                        onChange={updateField}
                    />
                </div>
            )}

            {/* Action Bar */}
            <div className="settings-actions">
                <button className="btn-reset" onClick={handleReset}>Reset to Defaults</button>
                <div className="settings-actions-right">
                    {status === 'saved' && <span className="save-status saved">✓ Saved</span>}
                    {status === 'error' && <span className="save-status error">✗ Error</span>}
                    <button className="btn-save" onClick={handleSave} disabled={!dirty || saving}>
                        {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                </div>
            </div>
        </div>
    );
}


/* ── Reusable Setting Row ── */
interface SettingRowProps {
    label: string;
    path: string;
    value: number;
    onChange: (path: string, value: number) => void;
    prefix?: string;
    suffix?: string;
    isDefault: boolean;
    type?: 'float' | 'int';
}

function SettingRow({ label, path, value, onChange, prefix, suffix, isDefault, type = 'float' }: SettingRowProps) {
    const [draft, setDraft] = useState(String(value));

    useEffect(() => {
        setDraft(String(value));
    }, [value]);

    return (
        <div className={`setting-row ${!isDefault ? 'modified' : ''}`}>
            <label className="setting-label">
                {label}
                {!isDefault && <span className="modified-dot" title="Modified from default" />}
            </label>
            <div className="setting-input-wrap">
                {prefix && <span className="input-affix prefix">{prefix}</span>}
                <input
                    type="number"
                    value={draft}
                    step={type === 'int' ? 1 : 0.1}
                    onChange={e => {
                        const raw = e.target.value;
                        setDraft(raw);
                        if (raw === '') return;
                        const v = type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
                        if (!Number.isFinite(v)) return;
                        onChange(path, v);
                    }}
                    onBlur={() => {
                        if (draft.trim() !== '') return;
                        onChange(path, 0);
                        setDraft('0');
                    }}
                    className="setting-input"
                />
                {suffix && <span className="input-affix suffix">{suffix}</span>}
            </div>
        </div>
    );
}

type QuickControl =
    | { kind: 'number'; label: string; path: string; value: number }
    | { kind: 'toggle'; label: string; path: string; value: boolean }
    | { kind: 'select'; label: string; path: string; value: 'all' | 'over' | 'under'; options: Array<'all' | 'over' | 'under'> };

interface QuickSettingsGroupProps {
    title: string;
    description: string;
    controls: QuickControl[];
    onChange: (path: string, value: any) => void;
    isDefault: (path: string, value: any) => boolean;
}

function QuickSettingsGroup({ title, description, controls, onChange, isDefault }: QuickSettingsGroupProps) {
    return (
        <div className="setting-group">
            <h3>{title}</h3>
            <p className="setting-description">{description}</p>
            <div className="settings-grid">
                {controls.map((control) => {
                    if (control.kind === 'number') {
                        return (
                            <SettingRow
                                key={control.path}
                                label={control.label}
                                path={control.path}
                                value={control.value}
                                onChange={onChange}
                                isDefault={isDefault(control.path, control.value)}
                            />
                        );
                    }
                    if (control.kind === 'select') {
                        const modified = !isDefault(control.path, control.value);
                        return (
                            <div key={control.path} className={`setting-row ${modified ? 'modified' : ''}`}>
                                <label className="setting-label">
                                    {control.label}
                                    {modified && <span className="modified-dot" title="Modified from default" />}
                                </label>
                                <div className="setting-input-wrap">
                                    <select
                                        className="setting-input"
                                        value={control.value}
                                        onChange={(e) => onChange(control.path, e.target.value)}
                                    >
                                        {control.options.map((option) => (
                                            <option key={option} value={option}>{option}</option>
                                        ))}
                                    </select>
                                </div>
                            </div>
                        );
                    }
                    const modified = !isDefault(control.path, control.value);
                    return (
                        <button
                            key={control.path}
                            type="button"
                            className={`profile-toggle-chip ${control.value ? 'on' : 'off'} ${modified ? 'modified' : ''}`}
                            onClick={() => onChange(control.path, !control.value)}
                        >
                            <span className="profile-toggle-indicator">{control.value ? '✓' : '○'}</span>
                            <span>{control.label}</span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

type CalcControl = { kind: 'number'; key: string; label: string; suffix?: string } | { kind: 'toggle'; key: string; label: string };

interface CalcProfileGroupProps {
    title: string;
    description: string;
    basePath: string;
    profile: Record<string, any>;
    controls: CalcControl[];
    onChange: (path: string, value: any) => void;
    isDefault: (path: string, value: any) => boolean;
}

function CalcProfileGroup({ title, description, basePath, profile, controls, onChange, isDefault }: CalcProfileGroupProps) {
    const numberControls = controls.filter((c) => c.kind === 'number');
    const toggleControls = controls.filter((c) => c.kind === 'toggle');
    return (
        <div className="setting-group">
            <h3>{title}</h3>
            <p className="setting-description">{description}</p>
            <div className="settings-grid">
                {numberControls.map((control) => (
                    <SettingRow
                        key={control.key}
                        label={control.label}
                        path={`${basePath}.${control.key}`}
                        value={profile[control.key]}
                        onChange={onChange}
                        suffix={control.suffix}
                        isDefault={isDefault(`${basePath}.${control.key}`, profile[control.key])}
                    />
                ))}
            </div>
            <div className="profile-toggle-grid">
                {toggleControls.map((control) => {
                    const path = `${basePath}.${control.key}`;
                    const value = !!profile[control.key];
                    const modified = !isDefault(path, value);
                    return (
                        <button
                            key={control.key}
                            type="button"
                            className={`profile-toggle-chip ${value ? 'on' : 'off'} ${modified ? 'modified' : ''}`}
                            onClick={() => onChange(path, !value)}
                        >
                            <span className="profile-toggle-indicator">{value ? '✓' : '○'}</span>
                            <span>{control.label}</span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
