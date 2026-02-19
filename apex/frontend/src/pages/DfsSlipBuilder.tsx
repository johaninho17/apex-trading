import { useState } from 'react';
import { Layers, Loader2, RefreshCw, TrendingUp, Award, Zap } from 'lucide-react';
import './DfsSlipBuilder.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface SlipLeg {
    player_name: string;
    market: string;
    line: number;
    side: string;
    edge_pct: number;
}

interface SlipResult {
    rank: number;
    slip_size: number;
    book: string;
    mode: string;
    players: SlipLeg[];
    combined_edge_pct: number;
    win_probability_pct: number;
    payout_multiplier: number;
    expected_value_pct: number;
    avg_leg_confidence: number;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const BOOKS = [
    { key: 'sleeper', label: 'Sleeper', color: '#5abe7a' },
    { key: 'prizepicks', label: 'PrizePicks', color: '#9333ea' },
    { key: 'underdog', label: 'Underdog', color: '#f97316' },
] as const;

type BookKey = typeof BOOKS[number]['key'];

const MODES: Record<BookKey, { key: string; label: string }[]> = {
    sleeper: [{ key: 'power', label: 'Power' }],
    prizepicks: [{ key: 'power', label: 'Power' }, { key: 'flex', label: 'Flex' }],
    underdog: [{ key: 'standard', label: 'Standard' }, { key: 'insured', label: 'Insured' }],
};

// Max leg counts per book/mode
const MAX_LEGS: Record<BookKey, Record<string, number>> = {
    sleeper: { power: 6 },
    prizepicks: { power: 5, flex: 6 },
    underdog: { standard: 6, insured: 6 },
};

const SLIP_SIZES = [3, 4, 5, 6] as const;

// ── Payout labels for display ─────────────────────────────────────────────────

const PAYOUT_LABELS: Record<BookKey, Record<string, Record<number, string>>> = {
    sleeper: { power: { 3: '~5.3x', 4: '~9.4x', 5: '~16.4x', 6: '~28.7x' } },
    prizepicks: {
        power: { 2: '3x', 3: '5x', 4: '10x', 5: '20x' },
        flex: { 3: '2.25x / 1.25x', 4: '5x / 1.5x', 5: '10x / 2x / 0.4x', 6: '25x / 2x / 0.4x' },
    },
    underdog: {
        standard: { 3: '6x', 4: '10x', 5: '20x', 6: '40x' },
        insured: { 3: '3x (1x refund)', 4: '6x (1.5x refund)', 5: '10x (2.5x refund)', 6: '20x (2.5x refund)' },
    },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMarket(key: string): string {
    const LABELS: Record<string, string> = {
        player_points: 'Points', player_rebounds: 'Rebounds', player_assists: 'Assists',
        player_threes: '3-Pointers', player_blocks: 'Blocks', player_steals: 'Steals',
        player_turnovers: 'Turnovers', player_points_rebounds_assists: 'Pts+Reb+Ast',
        player_points_rebounds: 'Pts+Reb', player_points_assists: 'Pts+Ast',
        player_rebounds_assists: 'Reb+Ast', player_double_double: 'Double-Double',
        player_pass_yds: 'Pass Yds', player_rush_yds: 'Rush Yds',
        player_receptions: 'Receptions', player_reception_yds: 'Rec Yds',
        pitcher_strikeouts: 'Strikeouts', batter_hits: 'Hits', batter_total_bases: 'Total Bases',
    };
    return LABELS[key] ?? key.replace(/^(player_|pitcher_|batter_)/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function bookColor(book: BookKey): string {
    return BOOKS.find(b => b.key === book)?.color ?? '#8fa0c8';
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function DfsSlipBuilder() {
    const [loading, setLoading] = useState(false);
    const [slipsBySize, setSlipsBySize] = useState<Map<number, SlipResult>>(new Map());
    const [error, setError] = useState('');
    const [minEdge, setMinEdge] = useState(0);
    const [book, setBook] = useState<BookKey>('sleeper');
    const [mode, setMode] = useState('power');
    const [hasGenerated, setHasGenerated] = useState(false);
    const [prioritizeDfsLines, setPrioritizeDfsLines] = useState(false);

    // When user switches book ensure mode is valid
    function selectBook(b: BookKey) {
        setBook(b);
        const available = MODES[b];
        if (!available.find(m => m.key === mode)) {
            setMode(available[0].key);
        }
        setSlipsBySize(new Map());
        setHasGenerated(false);
    }

    function selectMode(m: string) {
        setMode(m);
        setSlipsBySize(new Map());
        setHasGenerated(false);
    }

    async function generateSlips() {
        setLoading(true);
        setError('');
        setSlipsBySize(new Map());
        setHasGenerated(false);

        try {
            const raw = sessionStorage.getItem('dfs_scan_results');
            if (!raw) {
                setError('No scan data found. Run a scan on the DFS Scanner first, then return here.');
                setLoading(false);
                return;
            }
            const parsed = JSON.parse(raw);
            const opportunities = (parsed.results || []).filter((o: any) => o.edge_pct >= minEdge);
            // Pick up sport from scan data (fallback to 'nba')
            const sport: string = parsed.sport || 'nba';

            if (opportunities.length < 3) {
                setError(`Only ${opportunities.length} props with ≥${minEdge}% edge. Need at least 3 to build slips.`);
                setLoading(false);
                return;
            }

            const maxLegs = MAX_LEGS[book]?.[mode] ?? 6;
            const sizes = SLIP_SIZES.filter(s => s <= maxLegs);

            const res = await fetch('/api/v1/dfs/generate-slips', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    opportunities,
                    slip_sizes: sizes,
                    top_n: 10,
                    min_edge: minEdge,
                    book,
                    mode,
                    sport,
                    prioritize_dfs_lines: prioritizeDfsLines,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            // Index by slip_size → best slip for each size
            const bySize = new Map<number, SlipResult>();
            for (const slip of (data.slips ?? [])) {
                if (!bySize.has(slip.slip_size)) {
                    bySize.set(slip.slip_size, slip);
                }
            }
            setSlipsBySize(bySize);
            setHasGenerated(true);

            if (bySize.size === 0) {
                setError('No valid slip combinations found. Try lowering Min Edge or running a new scan.');
            }
        } catch (e: any) {
            setError(e.message || 'Failed to generate slips');
        }
        setLoading(false);
    }

    const color = bookColor(book);
    const availableModes = MODES[book];
    const maxLegs = MAX_LEGS[book]?.[mode] ?? 6;
    const activeSizes = SLIP_SIZES.filter(s => s <= maxLegs);

    return (
        <div className="slip-builder-page">
            {/* ── Header ──────────────────────────────────────────── */}
            <div className="slip-header">
                <h1><Layers size={22} /> Slip Builder</h1>
                <p className="subtitle">Auto-optimize picks for maximum expected payout per DFS book</p>
            </div>

            {/* ── Controls ─────────────────────────────────────────── */}
            <div className="slip-controls">

                {/* Book selector */}
                <div className="control-group">
                    <label>Target Book</label>
                    <div className="book-tabs">
                        {BOOKS.map(b => (
                            <button
                                key={b.key}
                                className={`book-tab ${book === b.key ? 'active' : ''}`}
                                style={book === b.key ? { '--tab-color': b.color } as React.CSSProperties : {}}
                                onClick={() => selectBook(b.key)}
                            >
                                {b.label}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Mode selector */}
                {availableModes.length > 1 && (
                    <div className="control-group">
                        <label>Mode</label>
                        <div className="mode-tabs">
                            {availableModes.map(m => (
                                <button
                                    key={m.key}
                                    className={`mode-tab ${mode === m.key ? 'active' : ''}`}
                                    onClick={() => selectMode(m.key)}
                                >
                                    {m.label}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Min edge */}
                <div className="control-group">
                    <label>Min Edge %</label>
                    <input
                        type="number"
                        value={minEdge}
                        onChange={e => setMinEdge(parseFloat(e.target.value) || 0)}
                        min={0} max={20} step={0.5}
                        className="edge-input"
                    />
                </div>

                {/* Prioritize DFS Lines toggle */}
                <div className="control-group">
                    <label>Prioritize DFS Lines</label>
                    <button
                        className={`toggle-btn ${prioritizeDfsLines ? 'active' : ''}`}
                        onClick={() => setPrioritizeDfsLines(v => !v)}
                        title="When enabled, props are first filtered to those listed on the selected DFS app before building slips"
                    >
                        {prioritizeDfsLines ? 'ON' : 'OFF'}
                    </button>
                </div>

                <button className="btn-generate" onClick={generateSlips} disabled={loading} style={{ '--btn-color': color } as React.CSSProperties}>
                    {loading ? <Loader2 size={16} className="spin" /> : <Zap size={16} />}
                    {loading ? 'Optimizing...' : 'Optimize Slips'}
                </button>
            </div>

            {error && <div className="error-banner">{error}</div>}

            {loading && (
                <div className="loading-state">
                    <Loader2 size={32} className="spin" />
                    <p>Finding best {activeSizes.join(', ')}-pick slips for {BOOKS.find(b2 => b2.key === book)?.label} {mode}...</p>
                </div>
            )}

            {/* ── Slip Grid ──────────────────────────────────────────── */}
            {hasGenerated && !loading && (
                <div className="slips-grid">
                    {activeSizes.map(size => {
                        const slip = slipsBySize.get(size);
                        const payoutLabel = PAYOUT_LABELS[book]?.[mode]?.[size] ?? `${slip?.payout_multiplier ?? '?'}x`;
                        return (
                            <SlipCard
                                key={size}
                                size={size}
                                slip={slip ?? null}
                                mode={mode}
                                payoutLabel={payoutLabel}
                                accentColor={color}
                            />
                        );
                    })}
                </div>
            )}

            {/* Empty state before any generation */}
            {!hasGenerated && !loading && !error && (
                <div className="empty-state">
                    <TrendingUp size={48} opacity={0.3} />
                    <p>Select a book and mode, then click <strong>Optimize Slips</strong> to generate your best picks.</p>
                    <p className="hint">Requires an active scan with results. Go to <em>DFS Scanner</em> first.</p>
                </div>
            )}
        </div>
    );
}

// ── SlipCard ──────────────────────────────────────────────────────────────────

interface SlipCardProps {
    size: number;
    slip: SlipResult | null;
    mode: string;
    payoutLabel: string;
    accentColor: string;
}

function SlipCard({ size, slip, mode, payoutLabel, accentColor }: SlipCardProps) {
    const modeLabel = mode.charAt(0).toUpperCase() + mode.slice(1);

    if (!slip) {
        return (
            <div className="slip-card slip-card--empty">
                <div className="slip-card-header" style={{ '--accent': accentColor } as React.CSSProperties}>
                    <span className="slip-size-badge">{size}-Pick</span>
                    <span className="slip-mode-badge">{modeLabel}</span>
                    <span className="slip-payout-badge">{payoutLabel}</span>
                </div>
                <div className="slip-empty-msg">
                    <RefreshCw size={20} opacity={0.4} />
                    <span>Not enough picks available</span>
                </div>
            </div>
        );
    }

    const isPositiveEV = slip.expected_value_pct > 0;

    return (
        <div className={`slip-card ${isPositiveEV ? 'positive-ev' : 'negative-ev'}`}>
            <div className="slip-card-header" style={{ '--accent': accentColor } as React.CSSProperties}>
                <span className="slip-size-badge">{size}-Pick</span>
                <span className="slip-mode-badge">{modeLabel}</span>
                <span className="slip-payout-badge">{payoutLabel}</span>
                {isPositiveEV && <Award size={14} className="ev-star" />}
            </div>

            <div className="slip-legs">
                {slip.players.map((p, j) => (
                    <div className="slip-leg" key={j}>
                        <div className="leg-main">
                            <span className="leg-player">{p.player_name}</span>
                            <span className={`leg-edge ${p.edge_pct > 0 ? 'positive' : 'negative'}`}>
                                {p.edge_pct > 0 ? '+' : ''}{p.edge_pct.toFixed(1)}%
                            </span>
                        </div>
                        <div className="leg-sub">
                            <span className="leg-market">{formatMarket(p.market)}</span>
                            <span className="leg-line">{p.side === 'under' ? 'U' : 'O'} {p.line}</span>
                        </div>
                    </div>
                ))}
            </div>

            <div className="slip-card-footer">
                <div className="slip-stat">
                    <label>Win Prob</label>
                    <span>{slip.win_probability_pct.toFixed(1)}%</span>
                </div>
                <div className="slip-stat">
                    <label>Edge</label>
                    <span className={slip.combined_edge_pct >= 0 ? 'positive' : 'negative'}>
                        {slip.combined_edge_pct >= 0 ? '+' : ''}{slip.combined_edge_pct.toFixed(1)}%
                    </span>
                </div>
                <div className="slip-stat highlight">
                    <label>Expected Value</label>
                    <span className={isPositiveEV ? 'positive' : 'negative'}>
                        {isPositiveEV ? '+' : ''}{slip.expected_value_pct.toFixed(1)}%
                    </span>
                </div>
            </div>
        </div>
    );
}
