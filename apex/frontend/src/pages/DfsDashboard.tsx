import { useEffect, useState } from 'react';
import { Calculator, Bell, Users, Target, Flame, ArrowRightLeft } from 'lucide-react';
import './pages.css';

interface EVResult {
    ev: number;
    ev_percent: number;
    decimal_odds: number;
    implied_probability: number;
    your_edge: number;
    kelly_fraction: number;
    kelly_stake: number;
    fair_prob: number | null;
    vig_pct: number | null;
    opposing_implied: number | null;
    devigged: boolean;
}

interface CorrelationResult {
    related_stat: string;
    correlation: number;
    note: string;
    boost?: boolean;
}

interface MiddleResult {
    player_name: string;
    stat: string;
    dfs_line: number;
    sharp_line: number;
    gap: number;
    direction: string;
    is_middle: boolean;
    strength: string;
    action: string;
}

export default function DfsDashboard() {
    // EV Calculator State
    const [odds, setOdds] = useState<number>(150);
    const [probability, setProbability] = useState<number>(0.55);
    const [stake, setStake] = useState<number>(100);
    const [oddsInput, setOddsInput] = useState('150');
    const [probabilityInput, setProbabilityInput] = useState('0.55');
    const [stakeInput, setStakeInput] = useState('100');
    const [opposingOdds, setOpposingOdds] = useState<string>('');
    const [useDevig, setUseDevig] = useState(false);
    const [evResult, setEvResult] = useState<EVResult | null>(null);

    // Correlation State
    const [player, setPlayer] = useState('');
    const [stat, setStat] = useState('points');
    const [sport, setSport] = useState('nba');
    const [correlations, setCorrelations] = useState<CorrelationResult[]>([]);
    const [corrStrategy, setCorrStrategy] = useState('');
    const [hasBoost, setHasBoost] = useState(false);

    // Middling State
    const [midPlayer, setMidPlayer] = useState('');
    const [midStat, setMidStat] = useState('');
    const [dfsLine, setDfsLine] = useState<number>(24.5);
    const [sharpLine, setSharpLine] = useState<number>(26.5);
    const [dfsLineInput, setDfsLineInput] = useState('24.5');
    const [sharpLineInput, setSharpLineInput] = useState('26.5');
    const [middleResult, setMiddleResult] = useState<MiddleResult | null>(null);

    // Alerts State
    const [alerts, setAlerts] = useState<any[]>([]);

    useEffect(() => setOddsInput(String(odds)), [odds]);
    useEffect(() => setProbabilityInput(String(probability)), [probability]);
    useEffect(() => setStakeInput(String(stake)), [stake]);
    useEffect(() => setDfsLineInput(String(dfsLine)), [dfsLine]);
    useEffect(() => setSharpLineInput(String(sharpLine)), [sharpLine]);

    async function calculateEV() {
        const body: any = { odds, probability, stake };
        if (useDevig && opposingOdds) {
            body.opposing_odds = Number(opposingOdds);
        }
        const res = await fetch('/api/v1/dfs/ev-calculator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then((r) => r.json());
        setEvResult(res);
    }

    async function findCorrelations() {
        const res = await fetch('/api/v1/dfs/correlation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ player, stat, direction: 'over', sport }),
        }).then((r) => r.json());
        setCorrelations(res.correlated_picks || []);
        setCorrStrategy(res.strategy || '');
        setHasBoost(res.has_boost || false);
    }

    async function checkMiddle() {
        const res = await fetch('/api/v1/dfs/middling', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                player_name: midPlayer,
                stat: midStat,
                dfs_line: dfsLine,
                sharp_line: sharpLine,
            }),
        }).then((r) => r.json());
        setMiddleResult(res);
    }

    async function fetchAlerts() {
        const res = await fetch('/api/v1/dfs/snipe-alerts').then((r) => r.json());
        setAlerts(res.alerts || []);
    }

    const nbaStats = ['points', 'rebounds', 'assists', 'three_pointers', 'steals', 'blocks'];
    const nflStats = ['passing_yards', 'receiving_yards', 'rushing_yards'];

    return (
        <div className="page-grid">
            <div className="two-col">
                {/* ── EV Calculator ── */}
                <div className="glass-card">
                    <h3 className="section-title"><Calculator size={16} /> EV Calculator</h3>
                    <div className="form-grid">
                        <div className="form-group">
                            <label>American Odds</label>
                            <input
                                type="number"
                                className="input"
                                value={oddsInput}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    setOddsInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setOdds(next);
                                }}
                                onBlur={() => {
                                    if (oddsInput.trim() === '') {
                                        setOdds(150);
                                        setOddsInput('150');
                                    }
                                }}
                                placeholder="+150 or -110"
                            />
                        </div>
                        <div className="form-group">
                            <label>True Probability</label>
                            <input
                                type="number"
                                className="input"
                                value={probabilityInput}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    setProbabilityInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setProbability(next);
                                }}
                                onBlur={() => {
                                    if (probabilityInput.trim() === '') {
                                        setProbability(0.55);
                                        setProbabilityInput('0.55');
                                    }
                                }}
                                step={0.01}
                                min={0}
                                max={1}
                                placeholder="0.55"
                            />
                        </div>
                        <div className="form-group">
                            <label>Stake ($)</label>
                            <input
                                type="number"
                                className="input"
                                value={stakeInput}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    setStakeInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setStake(next);
                                }}
                                onBlur={() => {
                                    if (stakeInput.trim() === '') {
                                        setStake(100);
                                        setStakeInput('100');
                                    }
                                }}
                                placeholder="100"
                            />
                        </div>

                        {/* Devigging Toggle */}
                        <div className="form-group full-width">
                            <label className="devig-toggle" onClick={() => setUseDevig(!useDevig)}>
                                <div className={`toggle-switch ${useDevig ? 'on' : ''}`}>
                                    <div className="toggle-knob" />
                                </div>
                                <span className="devig-label">Strip Vig (No-Vig Mode)</span>
                            </label>
                        </div>

                        {useDevig && (
                            <div className="form-group full-width fade-in">
                                <label>Opposing Side Odds</label>
                                <input
                                    type="number"
                                    className="input"
                                    value={opposingOdds}
                                    onChange={(e) => setOpposingOdds(e.target.value)}
                                    placeholder="e.g. +120 (Under odds)"
                                />
                                <span className="input-hint">Enter the other side's odds to remove bookmaker vig</span>
                            </div>
                        )}

                        <button className="btn btn-primary full-width" onClick={calculateEV}>
                            {useDevig ? '⚡ Calculate (Devigged)' : 'Calculate EV'}
                        </button>
                    </div>

                    {evResult && (
                        <div className="ev-results fade-in">
                            <div className={`ev-hero ${evResult.ev > 0 ? 'positive' : 'negative'}`}>
                                <span className="ev-label">Expected Value</span>
                                <span className="ev-value mono">
                                    {evResult.ev > 0 ? '+' : ''}${evResult.ev.toFixed(2)}
                                </span>
                                <span className="ev-percent mono">({evResult.ev_percent.toFixed(1)}%)</span>
                            </div>
                            <div className="ev-details">
                                <div><span className="text-muted">Decimal Odds:</span> <span className="mono">{evResult.decimal_odds}</span></div>
                                <div><span className="text-muted">Implied Prob:</span> <span className="mono">{(evResult.implied_probability * 100).toFixed(1)}%</span></div>
                                <div><span className="text-muted">Your Edge:</span> <span className={`mono ${evResult.your_edge > 0 ? 'text-green' : 'text-red'}`}>{(evResult.your_edge * 100).toFixed(1)}%</span></div>
                                <div><span className="text-muted">Kelly Bet:</span> <span className="mono">${evResult.kelly_stake.toFixed(2)}</span></div>
                            </div>
                            {/* Devigging breakdown */}
                            {evResult.devigged && evResult.fair_prob && (
                                <div className="devig-breakdown fade-in">
                                    <h4 className="section-subtitle">⚡ No-Vig Breakdown</h4>
                                    <div className="ev-details">
                                        <div><span className="text-muted">Fair Win %:</span> <span className="mono text-yellow">{(evResult.fair_prob * 100).toFixed(1)}%</span></div>
                                        <div><span className="text-muted">Vig Stripped:</span> <span className="mono text-red">{evResult.vig_pct}%</span></div>
                                        <div><span className="text-muted">Opposing Implied:</span> <span className="mono">{evResult.opposing_implied ? (evResult.opposing_implied * 100).toFixed(1) + '%' : '—'}</span></div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* ── Correlation Finder ── */}
                <div className="glass-card">
                    <h3 className="section-title"><Users size={16} /> Correlation Builder</h3>

                    {/* Sport Toggle */}
                    <div className="sport-toggle-row">
                        <button className={`sport-btn ${sport === 'nba' ? 'active' : ''}`} onClick={() => { setSport('nba'); setStat('points'); }}>🏀 NBA</button>
                        <button className={`sport-btn ${sport === 'nfl' ? 'active' : ''}`} onClick={() => { setSport('nfl'); setStat('passing_yards'); }}>🏈 NFL</button>
                    </div>

                    <div className="form-grid">
                        <div className="form-group">
                            <label>Player Name</label>
                            <input
                                type="text"
                                className="input"
                                value={player}
                                onChange={(e) => setPlayer(e.target.value)}
                                placeholder={sport === 'nba' ? 'LeBron James' : 'Patrick Mahomes'}
                            />
                        </div>
                        <div className="form-group">
                            <label>Stat Category</label>
                            <select className="input" value={stat} onChange={(e) => setStat(e.target.value)}>
                                {(sport === 'nba' ? nbaStats : nflStats).map(s => (
                                    <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
                                ))}
                            </select>
                        </div>
                        <button className="btn btn-primary full-width" onClick={findCorrelations}>
                            Find Correlated Picks
                        </button>
                    </div>

                    {correlations.length > 0 && (
                        <div className="correlation-results fade-in">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th></th>
                                        <th>Related Stat</th>
                                        <th>Correlation</th>
                                        <th>Note</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {correlations.map((c, i) => (
                                        <tr key={i} className={c.boost ? 'boost-row' : ''}>
                                            <td>{c.boost ? <Flame size={14} className="text-orange" /> : ''}</td>
                                            <td>{c.related_stat.replace(/_/g, ' ')}</td>
                                            <td className={`mono ${c.correlation > 0 ? 'text-green' : 'text-red'}`}>
                                                {c.correlation.toFixed(2)}
                                            </td>
                                            <td className="text-muted">{c.note}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {corrStrategy && (
                                <div className={`strategy-tip ${hasBoost ? 'boosted' : ''}`}>
                                    <strong>{hasBoost ? '🔥' : '💡'} Strategy:</strong> {corrStrategy}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            <div className="two-col">
                {/* ── Middling Tool ── */}
                <div className="glass-card">
                    <h3 className="section-title"><ArrowRightLeft size={16} /> Middling Detector</h3>
                    <p className="text-muted" style={{ fontSize: '12px', marginBottom: '12px' }}>
                        Spot line gaps between DFS platforms and sharp books to win both sides.
                    </p>
                    <div className="form-grid">
                        <div className="form-group">
                            <label>Player</label>
                            <input type="text" className="input" value={midPlayer} onChange={e => setMidPlayer(e.target.value)} placeholder="LeBron James" />
                        </div>
                        <div className="form-group">
                            <label>Stat</label>
                            <input type="text" className="input" value={midStat} onChange={e => setMidStat(e.target.value)} placeholder="Points" />
                        </div>
                        <div className="form-group">
                            <label>DFS Line (Fixed)</label>
                            <input
                                type="number"
                                className="input"
                                value={dfsLineInput}
                                onChange={e => {
                                    const raw = e.target.value;
                                    setDfsLineInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setDfsLine(next);
                                }}
                                onBlur={() => {
                                    if (dfsLineInput.trim() === '') {
                                        setDfsLine(24.5);
                                        setDfsLineInput('24.5');
                                    }
                                }}
                                step={0.5}
                            />
                        </div>
                        <div className="form-group">
                            <label>Sharp Line (Dynamic)</label>
                            <input
                                type="number"
                                className="input"
                                value={sharpLineInput}
                                onChange={e => {
                                    const raw = e.target.value;
                                    setSharpLineInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setSharpLine(next);
                                }}
                                onBlur={() => {
                                    if (sharpLineInput.trim() === '') {
                                        setSharpLine(26.5);
                                        setSharpLineInput('26.5');
                                    }
                                }}
                                step={0.5}
                            />
                        </div>
                        <button className="btn btn-primary full-width" onClick={checkMiddle}>
                            <Target size={14} /> Detect Middle
                        </button>
                    </div>

                    {middleResult && (
                        <div className={`middle-result fade-in ${middleResult.is_middle ? 'middle-active' : 'middle-inactive'}`}>
                            <div className="middle-header">
                                <span className={`middle-badge ${middleResult.strength}`}>
                                    {middleResult.is_middle ? `🎯 ${middleResult.strength.toUpperCase()} MIDDLE` : '❌ NO MIDDLE'}
                                </span>
                                <span className="middle-gap mono">Gap: {middleResult.gap} pts</span>
                            </div>
                            <div className="middle-lines">
                                <div className="line-box dfs">
                                    <span className="line-label">DFS</span>
                                    <span className="line-value mono">{middleResult.dfs_line}</span>
                                </div>
                                <div className="line-arrow">↔</div>
                                <div className="line-box sharp">
                                    <span className="line-label">Sharp</span>
                                    <span className="line-value mono">{middleResult.sharp_line}</span>
                                </div>
                            </div>
                            <p className="middle-action">{middleResult.action}</p>
                        </div>
                    )}
                </div>

                {/* ── Snipe Alerts ── */}
                <div className="glass-card">
                    <h3 className="section-title">
                        <Bell size={16} /> Snipe Alerts
                        <button className="btn btn-ghost btn-sm" onClick={fetchAlerts} style={{ marginLeft: 'auto' }}>
                            Refresh
                        </button>
                    </h3>
                    {alerts.length > 0 ? (
                        <div className="alert-list">
                            {alerts.map((a, i) => (
                                <div key={i} className="snipe-alert flash-alert">
                                    <strong>{a.player}</strong>: Line moved {a.from} → {a.to}
                                    <span className="text-muted"> ({a.window} min stale)</span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="empty-state">
                            <p>No active snipe alerts. Line divergences will appear here when detected.</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
