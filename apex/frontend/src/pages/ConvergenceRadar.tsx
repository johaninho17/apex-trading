import { useState, useEffect } from 'react';
import { Target, RefreshCw, ArrowRight, SlidersHorizontal } from 'lucide-react';
import CalcProfilePopover from '../components/CalcProfilePopover';
import { loadCalcProfile, saveCalcProfile, loadProfileFromSettings } from '../lib/calcProfiles';
import type { CalcPreset, EventsCalcProfile } from '../lib/calcProfiles';
import './pages.css';

interface Opportunity {
    polymarket_question: string;
    kalshi_title: string;
    kalshi_ticker: string;
    polymarket_price: number;
    kalshi_price: number;
    spread: number;
    signal: string;
    signal_strength: number;
    match_score: number;
}

function convergencePlayScore(o: Opportunity, profile: EventsCalcProfile): number {
    const spreadPct = (o.spread || 0) * 100;
    const strength = Number(o.signal_strength || 0);
    const match = Number(o.match_score || 0);
    const penalty = profile.useExecutionRisk ? Math.max(0, 3 - spreadPct) * 4 * profile.executionRiskPenalty : 0;
    const volPenalty = profile.useVolatilityPenalty ? Math.max(0, 2 - spreadPct) * profile.volatilityPenalty : 0;
    const trendBonus = profile.useMomentumBoost && o.signal === 'BUY_KALSHI' ? 5 * profile.momentumWeight : 0;
    const depthBonus = profile.useDepthBoost ? (match * profile.depthWeight * 0.04) : 0;
    const confAdj = profile.useConfidenceScaling ? (strength * profile.confidenceWeight * 0.08) : 0;
    const raw = 45
        + (spreadPct * profile.spreadWeight * 2.1)
        + (strength * profile.liquidityWeight * 0.2)
        + depthBonus
        + confAdj
        + trendBonus
        - penalty
        - volPenalty;
    return Math.max(0, Math.min(100, raw));
}

export default function ConvergenceRadar() {
    const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
    const [loading, setLoading] = useState(false);
    const [lastScan, setLastScan] = useState<string | null>(null);
    const [calcOpen, setCalcOpen] = useState(false);
    const [preset, setPreset] = useState<CalcPreset>('balanced');
    const [calcProfile, setCalcProfile] = useState<EventsCalcProfile>(loadCalcProfile('events'));

    async function scan() {
        setLoading(true);
        try {
            const res = await fetch('/api/v1/polymarket/convergence').then((r) => r.json());
            setOpportunities(res.opportunities || []);
            setLastScan(new Date().toISOString());
        } catch (e) {
            console.error('Convergence scan failed:', e);
        }
        setLoading(false);
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

    useEffect(() => {
        scan();
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

    const ranked = [...opportunities].sort((a, b) => convergencePlayScore(b, calcProfile) - convergencePlayScore(a, calcProfile));

    return (
        <div className="page-grid">
            {/* Header */}
            <div className="page-header">
                <div className="header-actions">
                    <button className="scanner-settings-btn" onClick={() => setCalcOpen(true)} title="Open convergence profile">
                        <SlidersHorizontal size={14} />
                    </button>
                    <button className="btn btn-primary" onClick={scan} disabled={loading}>
                        {loading ? <RefreshCw size={14} className="spin" /> : <Target size={14} />}
                        {loading ? 'Scanning...' : 'Scan for Convergence'}
                    </button>
                    {lastScan && <span className="text-muted">Last scan: {formatDateTime(lastScan)}</span>}
                </div>
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

            {/* Explanation */}
            <div className="glass-card info-banner">
                <strong>⚡ Convergence Radar</strong>
                <p className="text-muted" style={{ marginTop: '4px', fontSize: '13px' }}>
                    Compares Polymarket (global/sharp) vs Kalshi (US/retail) prices. When spreads exist,
                    the "sharp" price usually leads — buy the lagging side before convergence.
                </p>
            </div>

            {/* Opportunities Table */}
            <div className="glass-card">
                <h3 className="section-title">
                    <Target size={16} /> Opportunities
                    {ranked.length > 0 && <span className="badge">{ranked.length}</span>}
                </h3>

                {ranked.length > 0 ? (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Event</th>
                                <th>Polymarket</th>
                                <th></th>
                                <th>Kalshi</th>
                                <th>Spread</th>
                                <th>Signal</th>
                                <th>Strength</th>
                                <th>Play</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ranked.map((o, i) => (
                                <tr key={i} className="convergence-row">
                                    <td>
                                        <div className="convergence-event">
                                            <div className="text-muted" style={{ fontSize: '11px' }}>Poly: {o.polymarket_question.slice(0, 60)}...</div>
                                            <div style={{ fontSize: '12px' }}>Kalshi: {o.kalshi_title}</div>
                                        </div>
                                    </td>
                                    <td className="mono text-yellow">{(o.polymarket_price * 100).toFixed(1)}%</td>
                                    <td><ArrowRight size={14} className="text-muted" /></td>
                                    <td className="mono text-blue">{(o.kalshi_price * 100).toFixed(1)}%</td>
                                    <td className={`mono ${o.spread > 0.05 ? 'text-green' : 'text-muted'}`}>
                                        {(o.spread * 100).toFixed(1)}%
                                    </td>
                                    <td>
                                        <span className={`signal-badge ${o.signal === 'BUY_KALSHI' ? 'buy' : 'fade'}`}>
                                            {o.signal === 'BUY_KALSHI' ? '🟢 BUY' : '🔴 FADE'}
                                        </span>
                                    </td>
                                    <td className="mono">{o.signal_strength.toFixed(0)}</td>
                                    <td className="mono">{convergencePlayScore(o, calcProfile).toFixed(0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <div className="empty-state">
                        {loading ? 'Scanning markets...' : 'No convergence opportunities found. Click "Scan" to check.'}
                    </div>
                )}
            </div>
        </div>
    );
}
