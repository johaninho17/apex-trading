/**
 * GlobalSearch — Cmd+K command palette for Apex.
 * Indexes pages + ticker search via API.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Search, TrendingUp, Zap, Trophy, BarChart3,
    Crosshair, Target, Wallet, ScanLine, Layers, Coins,
} from 'lucide-react';
import './GlobalSearch.css';

/* ── Static page index ── */
const PAGE_INDEX = [
    { label: 'Stocks Dashboard', path: '/alpaca', icon: TrendingUp, color: 'var(--color-alpaca)', group: 'Pages' },
    { label: 'Stock Scanner', path: '/alpaca/scanner', icon: ScanLine, color: 'var(--color-alpaca)', group: 'Pages' },
    { label: 'Crypto', path: '/alpaca/crypto', icon: Coins, color: 'var(--color-alpaca)', group: 'Pages' },
    { label: 'Portfolio', path: '/alpaca/portfolio', icon: Wallet, color: 'var(--color-alpaca)', group: 'Pages' },
    { label: 'Kalshi Markets', path: '/kalshi', icon: Zap, color: 'var(--color-kalshi)', group: 'Pages' },
    { label: 'Kalshi Scalper', path: '/kalshi/scalper', icon: Crosshair, color: 'var(--color-kalshi)', group: 'Pages' },
    { label: 'Smart Scanner', path: '/dfs/scan', icon: Trophy, color: 'var(--color-dfs)', group: 'Pages' },
    { label: 'Slip Builder', path: '/dfs/slips', icon: Layers, color: 'var(--color-dfs)', group: 'Pages' },
    { label: 'EV Grind', path: '/dfs/grind', icon: Zap, color: 'var(--color-dfs)', group: 'Pages' },
    { label: 'Polymarket Research', path: '/polymarket', icon: Search, color: 'var(--color-polymarket)', group: 'Pages' },
    { label: 'Convergence Radar', path: '/convergence', icon: Target, color: 'var(--color-convergence)', group: 'Pages' },
];

interface SearchResult {
    label: string;
    path: string;
    icon: typeof TrendingUp;
    color: string;
    group: string;
    hint?: string;
}

interface GlobalSearchProps {
    open: boolean;
    onClose: () => void;
}

export default function GlobalSearch({ open, onClose }: GlobalSearchProps) {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<SearchResult[]>([]);
    const [focusIndex, setFocusIndex] = useState(0);
    const [tickerResults, setTickerResults] = useState<SearchResult[]>([]);
    const inputRef = useRef<HTMLInputElement>(null);
    const navigate = useNavigate();
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Focus input when opened
    useEffect(() => {
        if (open) {
            setQuery('');
            setResults(PAGE_INDEX);
            setTickerResults([]);
            setFocusIndex(0);
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [open]);

    // Search pages + debounced ticker API search
    const handleSearch = useCallback((q: string) => {
        setQuery(q);
        const lower = q.toLowerCase().trim();

        // Filter pages
        const pageMatches = lower
            ? PAGE_INDEX.filter(p => p.label.toLowerCase().includes(lower))
            : PAGE_INDEX;
        setResults(pageMatches);
        setFocusIndex(0);

        // Debounced ticker search (only if query looks like a ticker — 1-5 chars, alpha)
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (lower.length >= 1 && /^[a-z]+$/i.test(lower)) {
            debounceRef.current = setTimeout(async () => {
                try {
                    const res = await fetch(`/api/v1/alpaca/search?q=${encodeURIComponent(lower)}`);
                    const data = await res.json();
                    const tickers: SearchResult[] = (data.results || []).slice(0, 6).map((t: { symbol: string; name: string }) => ({
                        label: `${t.symbol} — ${t.name}`,
                        path: `/alpaca/search/${t.symbol}`,
                        icon: BarChart3,
                        color: 'var(--color-alpaca)',
                        group: 'Tickers',
                        hint: t.symbol,
                    }));
                    setTickerResults(tickers);
                } catch {
                    setTickerResults([]);
                }
            }, 200);
        } else {
            setTickerResults([]);
        }
    }, []);

    // Combine all results
    const allResults = [...tickerResults, ...results];

    // Action on select
    function selectResult(result: SearchResult) {
        navigate(result.path);
        onClose();
    }

    // Keyboard navigation
    function handleKeyDown(e: React.KeyboardEvent) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setFocusIndex(i => Math.min(i + 1, allResults.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setFocusIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'Enter' && allResults[focusIndex]) {
            e.preventDefault();
            selectResult(allResults[focusIndex]);
        } else if (e.key === 'Escape') {
            onClose();
        }
    }

    if (!open) return null;

    // Group results
    const grouped: Record<string, SearchResult[]> = {};
    allResults.forEach(r => {
        if (!grouped[r.group]) grouped[r.group] = [];
        grouped[r.group].push(r);
    });

    return (
        <div className="search-overlay" onClick={onClose}>
            <div className="search-dialog" onClick={e => e.stopPropagation()}>
                {/* Input */}
                <div className="search-input-row">
                    <Search size={18} />
                    <input
                        ref={inputRef}
                        type="text"
                        placeholder="Search pages, tickers…"
                        value={query}
                        onChange={e => handleSearch(e.target.value)}
                        onKeyDown={handleKeyDown}
                    />
                    <span className="search-kbd">ESC</span>
                </div>

                {/* Results */}
                <div className="search-results">
                    {allResults.length === 0 ? (
                        <div className="search-empty">No results for "{query}"</div>
                    ) : (
                        Object.entries(grouped).map(([group, items]) => (
                            <div key={group}>
                                <div className="search-group-label">{group}</div>
                                {items.map((item) => {
                                    const globalIdx = allResults.indexOf(item);
                                    return (
                                        <div
                                            key={item.path}
                                            className={`search-result-item ${globalIdx === focusIndex ? 'focused' : ''}`}
                                            onClick={() => selectResult(item)}
                                            onMouseEnter={() => setFocusIndex(globalIdx)}
                                        >
                                            <span className="search-result-color" style={{ background: item.color }} />
                                            <item.icon size={16} style={{ color: item.color }} />
                                            <span className="search-result-label">{item.label}</span>
                                            {item.hint && <span className="search-result-hint">{item.hint}</span>}
                                        </div>
                                    );
                                })}
                            </div>
                        ))
                    )}
                </div>

                {/* Footer */}
                <div className="search-footer">
                    <span>↑↓ navigate</span>
                    <span>↵ select</span>
                    <span>esc close</span>
                </div>
            </div>
        </div>
    );
}
