/**
 * DFS EV Dashboard — "Daily Grind" Clone
 * Dense, auto-scanning table of every +EV prop on the board.
 * Click any row to open the "Prop Professor" research modal.
 */
import { useState } from 'react';
import {
    Zap, Search, TrendingUp, TrendingDown,
    X, Flame, Target, ChevronUp, ChevronDown, Loader2,
    ArrowUp, ArrowDown, Volleyball, Shield,
} from 'lucide-react';
import './DfsEvDashboard.css';

interface Opportunity {
    player_name: string;
    market: string;
    line: number;
    side: string;
    sharp_odds: number;
    sharp_book: string;
    edge_pct: number;
    is_play: boolean;
    opposing_odds: number | null;
    sharp_implied_prob: number;
    opposing_implied_prob: number | null;
    fair_prob: number | null;
    fixed_implied_prob: number;
    vig_pct: number | null;
}

interface Research {
    player_name: string;
    stat: string;
    line: number;
    game_logs: number[];
    hit_rates: { l5: number; l10: number; l20: number };
    averages: { l5: number; l10: number; l20: number };
    trend: string;
    current_streak: number;
    recommendation: string;
}

type SortKey = 'edge_pct' | 'player_name' | 'market' | 'fair_prob';
type SortDir = 'asc' | 'desc';

const MARKET_LABELS: Record<string, string> = {
    player_points: 'PTS', player_rebounds: 'REB', player_assists: 'AST',
    player_threes: '3PM', player_blocks: 'BLK', player_steals: 'STL',
    player_turnovers: 'TO', player_points_rebounds_assists: 'PRA',
    player_points_rebounds: 'PR', player_points_assists: 'PA',
    player_rebounds_assists: 'RA', player_double_double: 'DD',
    player_pass_yds: 'PASS YD', player_pass_tds: 'PASS TD',
    player_pass_completions: 'COMP', player_rush_yds: 'RUSH YD',
    player_receptions: 'REC', player_reception_yds: 'REC YD',
    player_anytime_td: 'ANY TD',
};

import { pushToast } from '../components/Toaster';

export default function DfsEvDashboard() {
    const [sport, setSport] = useState<'nba' | 'nfl'>('nba');
    const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
    const [loading, setLoading] = useState(false);
    const [scanned, setScanned] = useState(false);
    const [totalScanned, setTotalScanned] = useState(0);
    const [playsFound, setPlaysFound] = useState(0);
    const [filter, setFilter] = useState('');
    const [minEdge, setMinEdge] = useState(0);
    const [minEdgeInput, setMinEdgeInput] = useState('0');
    const [sortKey, setSortKey] = useState<SortKey>('edge_pct');
    const [sortDir, setSortDir] = useState<SortDir>('desc');

    // Research modal
    const [research, setResearch] = useState<Research | null>(null);
    const [researchLoading, setResearchLoading] = useState(false);

    async function runBulkScan() {
        setLoading(true);
        try {
            const res = await fetch('/api/v1/dfs/bulk-scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sport, max_games: 8 }),
            });
            const data = await res.json();
            setOpportunities(data.opportunities || []);
            setTotalScanned(data.total_scanned || 0);
            setPlaysFound(data.plays_found || 0);
            setScanned(true);

            // Instant feedback (optimistic UI)
            if (data.plays_found > 0) {
                pushToast({
                    title: "Scan Complete",
                    message: `Found ${data.plays_found} +EV plays in ${sport.toUpperCase()}`,
                    type: 'success',
                    domain: 'DFS'
                });
            } else {
                pushToast({
                    title: "Scan Complete",
                    message: `No +EV plays found in ${sport.toUpperCase()}`,
                    type: 'info',
                    domain: 'DFS'
                });
            }
        } catch (e) {
            console.error('Bulk scan failed:', e);
            pushToast({
                title: "Scan Failed",
                message: "Could not fetch data. Check console.",
                type: 'error',
                domain: 'DFS'
            });
        }

        setLoading(false);
    }

    async function openResearch(opp: Opportunity) {
        setResearchLoading(true);
        try {
            const stat = opp.market.replace('player_', '');
            const res = await fetch('/api/v1/dfs/player-research', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    player_name: opp.player_name,
                    stat,
                    line: opp.line,
                }),
            });
            setResearch(await res.json());
        } catch (e) {
            console.error('Research failed:', e);
        }
        setResearchLoading(false);
    }

    function toggleSort(key: SortKey) {
        if (sortKey === key) {
            setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
        } else {
            setSortKey(key);
            setSortDir('desc');
        }
    }

    const SortIcon = ({ col }: { col: SortKey }) =>
        sortKey === col
            ? sortDir === 'desc' ? <ChevronDown size={12} /> : <ChevronUp size={12} />
            : null;

    // Filter & sort
    const filtered = opportunities
        .filter(o => o.edge_pct >= minEdge)
        .filter(o => !filter || o.player_name.toLowerCase().includes(filter.toLowerCase())
            || o.market.toLowerCase().includes(filter.toLowerCase()))
        .sort((a, b) => {
            const mul = sortDir === 'desc' ? -1 : 1;
            const aVal = a[sortKey] ?? 0;
            const bVal = b[sortKey] ?? 0;
            if (typeof aVal === 'string') return mul * (aVal as string).localeCompare(bVal as string);
            return mul * ((aVal as number) - (bVal as number));
        });

    return (
        <div className="ev-dashboard">
            {/* Header */}
            <div className="ev-header">
                <div className="ev-title-row">
                    <h1><Zap size={22} /> EV Dashboard</h1>
                    <span className="ev-subtitle">Full Slate Scanner — find every edge</span>
                </div>

                <div className="ev-controls">
                    <div className="sport-segmented">
                        {[
                            { key: 'nba', icon: Volleyball, label: 'NBA' },
                            { key: 'nfl', icon: Shield, label: 'NFL' },
                        ].map(s => (
                            <button key={s.key}
                                className={`seg-item ${sport === s.key ? 'active' : ''}`}
                                onClick={() => setSport(s.key as any)}
                            >
                                <span className="seg-icon"><s.icon size={14} /></span>
                                <span className="seg-label">{s.label}</span>
                            </button>
                        ))}
                    </div>
                    <button className="btn-scan-ev" onClick={runBulkScan} disabled={loading}>
                        {loading ? <><Loader2 size={16} className="spin" /> Scanning...</>
                            : <><Zap size={16} /> Scan Full Slate</>}
                    </button>
                </div>
            </div>

            {/* Stats bar */}
            {scanned && (
                <div className="ev-stats-bar">
                    <div className="ev-stat">
                        <span className="ev-stat-value">{totalScanned}</span>
                        <span className="ev-stat-label">Props Scanned</span>
                    </div>
                    <div className="ev-stat accent">
                        <span className="ev-stat-value">{playsFound}</span>
                        <span className="ev-stat-label">+EV Plays</span>
                    </div>
                    <div className="ev-stat">
                        <span className="ev-stat-value">{sport.toUpperCase()}</span>
                        <span className="ev-stat-label">Sport</span>
                    </div>
                </div>
            )}

            {/* Filters */}
            {scanned && (
                <div className="ev-filters">
                    <div className="filter-group">
                        <Search size={14} />
                        <input type="text" placeholder="Search player or prop..."
                            value={filter} onChange={e => setFilter(e.target.value)} />
                    </div>
                    <div className="filter-group">
                        <label>Min Edge</label>
                        <div className="input-modern-ev">
                            <input
                                type="number"
                                value={minEdgeInput}
                                onChange={e => {
                                    const raw = e.target.value;
                                    setMinEdgeInput(raw);
                                    if (raw === '') return;
                                    const next = Number(raw);
                                    if (!Number.isFinite(next)) return;
                                    setMinEdge(next);
                                }}
                                onBlur={() => {
                                    if (minEdgeInput.trim() === '') {
                                        setMinEdge(0);
                                        setMinEdgeInput('0');
                                    }
                                }}
                                step={0.5}
                            />
                            <span className="input-suffix-ev">%</span>
                        </div>
                    </div>
                </div>
            )}

            {/* Table */}
            {scanned && (
                <div className="ev-table-wrap">
                    <table className="ev-table">
                        <thead>
                            <tr>
                                <th onClick={() => toggleSort('player_name')}>Player <SortIcon col="player_name" /></th>
                                <th onClick={() => toggleSort('market')}>Prop <SortIcon col="market" /></th>
                                <th>Side</th>
                                <th>Line</th>
                                <th>Book</th>
                                <th>Odds</th>
                                <th onClick={() => toggleSort('fair_prob')}>Fair % <SortIcon col="fair_prob" /></th>
                                <th>DFS %</th>
                                <th onClick={() => toggleSort('edge_pct')}>Edge <SortIcon col="edge_pct" /></th>
                                <th>Vig</th>
                                <th>Signal</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filtered.length === 0 && (
                                <tr>
                                    <td colSpan={11} className="empty-row">
                                        {loading ? 'Scanning...' : 'No opportunities found. Adjust filters or scan again.'}
                                    </td>
                                </tr>
                            )}
                            {filtered.map((opp, i) => (
                                <tr key={i}
                                    className={`ev-row ${opp.is_play ? 'play' : ''} ${opp.edge_pct >= 5 ? 'hot' : ''}`}
                                    onClick={() => openResearch(opp)}
                                    title="Click for deep research">
                                    <td className="player-cell">{opp.player_name}</td>
                                    <td className="prop-cell">{MARKET_LABELS[opp.market] || opp.market}</td>
                                    <td>
                                        <span className={`side-badge side-${opp.side || 'over'}`}>
                                            {(opp.side || 'over') === 'over'
                                                ? <><ArrowUp size={12} /> O</>
                                                : <><ArrowDown size={12} /> U</>}
                                        </span>
                                    </td>
                                    <td className="mono">{opp.line}</td>
                                    <td className="book-cell">{opp.sharp_book}</td>
                                    <td className="mono">{opp.sharp_odds > 0 ? '+' : ''}{opp.sharp_odds}</td>
                                    <td className="mono">{opp.fair_prob ? `${opp.fair_prob}%` : '—'}</td>
                                    <td className="mono">{opp.fixed_implied_prob}%</td>
                                    <td className={`mono edge-cell ${opp.edge_pct > 0 ? 'pos' : 'neg'}`}>
                                        {opp.edge_pct > 0 ? '+' : ''}{opp.edge_pct}%
                                        {opp.edge_pct >= 5 && <Flame size={12} className="fire-icon" />}
                                    </td>
                                    <td className="mono text-muted">{opp.vig_pct != null ? `${opp.vig_pct}%` : '—'}</td>
                                    <td>
                                        {opp.is_play
                                            ? <span className="signal-badge play"><TrendingUp size={12} /> PLAY</span>
                                            : <span className="signal-badge skip"><TrendingDown size={12} /> SKIP</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Welcome state */}
            {!scanned && !loading && (
                <div className="ev-welcome">
                    <Target size={48} />
                    <h2>Full Slate EV Scanner</h2>
                    <p>Scan every prop across all games. Find every edge. Click any row for deep research.</p>
                    <button className="btn-scan-ev large" onClick={runBulkScan}>
                        <Zap size={18} /> Scan {sport.toUpperCase()} Slate
                    </button>
                </div>
            )}

            {/* Research Modal */}
            {(research || researchLoading) && (
                <div className="research-overlay" onClick={() => !researchLoading && setResearch(null)}>
                    <div className="research-modal" onClick={e => e.stopPropagation()}>
                        {researchLoading ? (
                            <div className="research-loading"><Loader2 size={32} className="spin" /> Loading research...</div>
                        ) : research && (
                            <>
                                <div className="research-header">
                                    <h2>{research.player_name} — {research.stat}</h2>
                                    <button className="close-btn" onClick={() => setResearch(null)}><X size={18} /></button>
                                </div>

                                <div className="research-line-label">
                                    Line: <span className="mono">{research.line}</span>
                                    <span className={`trend-badge ${research.trend}`}>
                                        {research.trend === 'hot' ? '🔥 HOT' : research.trend === 'cold' ? '❄️ COLD' : '➡️ NEUTRAL'}
                                    </span>
                                    <span className={`rec-badge ${research.recommendation.toLowerCase()}`}>
                                        REC: {research.recommendation}
                                    </span>
                                </div>

                                {/* Hit Rates */}
                                <div className="research-section">
                                    <h3>Hit Rates</h3>
                                    <div className="hit-rates">
                                        {(['l5', 'l10', 'l20'] as const).map(period => (
                                            <div key={period} className="hit-rate-card">
                                                <div className="hit-rate-label">{period.toUpperCase()}</div>
                                                <div className={`hit-rate-value ${research.hit_rates[period] >= 60 ? 'high' : research.hit_rates[period] <= 40 ? 'low' : ''}`}>
                                                    {research.hit_rates[period]}%
                                                </div>
                                                <div className="hit-rate-avg">avg: {research.averages[period]}</div>
                                            </div>
                                        ))}
                                        <div className="hit-rate-card streak">
                                            <div className="hit-rate-label">STREAK</div>
                                            <div className="hit-rate-value">{research.current_streak}</div>
                                            <div className="hit-rate-avg">consecutive</div>
                                        </div>
                                    </div>
                                </div>

                                {/* Game Log Chart */}
                                <div className="research-section">
                                    <h3>Last 20 Games</h3>
                                    <div className="game-log-chart">
                                        {research.game_logs.map((val, i) => (
                                            <div key={i} className="game-bar-wrap">
                                                <div
                                                    className={`game-bar ${val > research.line ? 'over' : 'under'}`}
                                                    style={{ height: `${Math.min(100, (val / (research.line * 2)) * 100)}%` }}
                                                    title={`Game ${i + 1}: ${val}`}
                                                />
                                                <span className="game-val">{val}</span>
                                            </div>
                                        ))}
                                        <div className="line-marker" style={{
                                            bottom: `${Math.min(100, (research.line / (research.line * 2)) * 100)}%`
                                        }}>
                                            <span>{research.line}</span>
                                        </div>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
