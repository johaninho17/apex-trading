import { useState, useEffect, useCallback, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useWebSocket } from '../hooks/useWebSocket';
import Toaster, { pushToast } from './Toaster';
import GlobalSearch from './GlobalSearch';
import NotificationDrawer from './NotificationDrawer';
import {
    TrendingUp,
    Zap,
    Crosshair,
    Trophy,
    Search,
    Target,
    Wifi,
    WifiOff,
    ChevronLeft,
    ChevronRight,
    ChevronDown,
    ScanLine,
    Wallet,
    Layers,
    ToggleLeft,
    ToggleRight,
    Settings,
    HelpCircle,
    Bell,
    Coins,
} from 'lucide-react';
import './Layout.css';

interface BalanceSummary {
    alpaca: number | null;
    kalshi: number | null;
}

type DomainPauseState = {
    stocks: boolean;
    events: boolean;
    sports: boolean;
};

type TradingMode = 'paper' | 'live';

/* ── Sidebar Groups (Accordion) ── */
interface NavItem {
    path: string;
    label: string;
    icon: typeof TrendingUp;
    end?: boolean;
}

interface NavGroup {
    key: string;
    label: string;
    icon: typeof TrendingUp;
    color: string;
    items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
    {
        key: 'stocks',
        label: 'Stocks',
        icon: TrendingUp,
        color: 'var(--color-alpaca)',
        items: [
            { path: '/alpaca', label: 'Dashboard', icon: TrendingUp, end: true },
            { path: '/alpaca/scanner', label: 'Scanner', icon: ScanLine },
            { path: '/alpaca/search', label: 'Search', icon: Search },
            { path: '/alpaca/crypto', label: 'Crypto', icon: Coins },
            { path: '/alpaca/portfolio', label: 'Portfolio', icon: Wallet },
        ],
    },
    {
        key: 'sports',
        label: 'Sports',
        icon: Trophy,
        color: 'var(--color-dfs)',
        items: [
            { path: '/dfs/scan', label: 'Smart Scanner', icon: Trophy },
            { path: '/dfs/slips', label: 'Slip Builder', icon: Layers },
        ],
    },
    {
        key: 'events',
        label: 'Events',
        icon: Zap,
        color: 'var(--color-kalshi)',
        items: [
            { path: '/kalshi', label: 'Kalshi Markets', icon: Zap, end: true },
            { path: '/kalshi/scalper', label: 'Scalper', icon: Crosshair },
            { path: '/polymarket', label: 'Polymarket', icon: Search },
            { path: '/convergence', label: 'Convergence', icon: Target },
        ],
    },
];

const CONFIG_GROUP: NavGroup = {
    key: 'config',
    label: 'Config',
    icon: Settings,
    color: 'var(--text-secondary)',
    items: [
        { path: '/config/settings', label: 'Settings', icon: Settings },
        { path: '/config/help', label: 'Help', icon: HelpCircle },
    ],
};

export default function Layout() {
    const [isSleeping, setIsSleeping] = useState(false);
    const [domainPaused, setDomainPaused] = useState<DomainPauseState>({ stocks: false, events: false, sports: false });
    const [domainDropupOpen, setDomainDropupOpen] = useState(false);
    const domainHoldTimerRef = useRef<number | null>(null);
    const domainCloseTimerRef = useRef<number | null>(null);
    const { connected, subscribe } = useWebSocket(undefined, !isSleeping);
    const [collapsed, setCollapsed] = useState(false);
    const [balances, setBalances] = useState<BalanceSummary>({ alpaca: null, kalshi: null });
    const [tradingMode, setTradingMode] = useState<TradingMode>('paper');
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const [searchOpen, setSearchOpen] = useState(false);
    const [notifOpen, setNotifOpen] = useState(false);
    const [notifCount, setNotifCount] = useState(0);
    const [notifPulseSeq, setNotifPulseSeq] = useState(0);
    const seenNotifIdsRef = useRef<Set<string>>(new Set());
    const location = useLocation();

    // Auto-expand the group that matches the current route
    useEffect(() => {
        const activeGroup = NAV_GROUPS.find(g =>
            g.items.some(item => location.pathname.startsWith(item.path.split('/').slice(0, 2).join('/')))
        );
        if (activeGroup) {
            setOpenGroups(prev => ({ ...prev, [activeGroup.key]: true }));
        }
    }, [location.pathname]);

    // Route changes should always clear transient overlays/timers so clicks cannot be trapped.
    useEffect(() => {
        setDomainDropupOpen(false);
        setSearchOpen(false);
        if (domainHoldTimerRef.current) {
            window.clearTimeout(domainHoldTimerRef.current);
            domainHoldTimerRef.current = null;
        }
        if (domainCloseTimerRef.current) {
            window.clearTimeout(domainCloseTimerRef.current);
            domainCloseTimerRef.current = null;
        }
    }, [location.pathname]);

    // Cmd+K global shortcut
    useEffect(() => {
        function handleKeyDown(e: KeyboardEvent) {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                setSearchOpen(prev => !prev);
            }
        }
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []);

    const refreshNotificationCount = useCallback(async () => {
        try {
            const res = await fetch('/api/v1/notifications?days=7&limit=300');
            if (!res.ok) return;
            const data = await res.json();
            const items = Array.isArray(data.items) ? data.items : [];
            setNotifCount(items.length);
            seenNotifIdsRef.current = new Set(
                items
                    .map((n: any) => n?.id)
                    .filter((id: unknown): id is string => typeof id === 'string' && id.length > 0)
            );
            window.dispatchEvent(new Event('apex:notifications-resync'));
        } catch {
            // Silent fail
        }
    }, []);

    // ── WebSocket Subscriptions ──
    useEffect(() => {
        if (!connected) return;

        const handleMessage = (msg: any) => {
            const notif = msg?.data?.notification;
            let isNewNotification = false;
            if (notif?.id && notif?.ts && notif?.title) {
                if (seenNotifIdsRef.current.has(notif.id)) return;
                seenNotifIdsRef.current.add(notif.id);
                isNewNotification = true;
                setNotifCount(prev => prev + 1);
                setNotifPulseSeq(prev => prev + 1);
                window.dispatchEvent(new CustomEvent('apex:notification', { detail: notif }));
            }
            if (msg.type === 'toast') {
                // Prevent duplicate toast popups when duplicate WS payloads arrive.
                if (notif?.id && !isNewNotification) return;
                setNotifPulseSeq(prev => prev + 1);
                pushToast(msg.data);
            }
        };

        const unsubs = [subscribe('*', handleMessage)];

        return () => {
            unsubs.forEach(u => u());
        };
    }, [connected, subscribe]);

    useEffect(() => {
        refreshNotificationCount();
    }, [refreshNotificationCount]);

    useEffect(() => {
        if (!connected) return;
        refreshNotificationCount();
    }, [connected, refreshNotificationCount]);

    // Fetch balances + trading mode on mount
    useEffect(() => {
        async function fetchBalances() {
            try {
                const [alpacaRes, kalshiRes] = await Promise.allSettled([
                    fetch('/api/v1/alpaca/portfolio').then((r) => r.json()),
                    fetch('/api/v1/kalshi/balance').then((r) => r.json()),
                ]);
                setBalances({
                    alpaca: alpacaRes.status === 'fulfilled' ? alpacaRes.value.portfolio_value : null,
                    kalshi: kalshiRes.status === 'fulfilled' ? kalshiRes.value.balance : null,
                });
                if (alpacaRes.status === 'fulfilled' && alpacaRes.value.trading_mode) {
                    setTradingMode(alpacaRes.value.trading_mode);
                }
            } catch {
                // Silently fail — balances are optional
            }
        }

        // Only poll if not sleeping
        if (isSleeping) return;

        fetchBalances();

        const interval = setInterval(fetchBalances, 30000);
        return () => clearInterval(interval);
    }, [isSleeping]);

    // Listen for live portfolio updates from dashboard pages
    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent).detail;
            if (detail.portfolioValue !== undefined) {
                setBalances(prev => ({ ...prev, alpaca: detail.portfolioValue }));
            }
            if (detail.tradingMode) {
                setTradingMode(detail.tradingMode);
            }
        };
        window.addEventListener('apex:portfolio-update', handler);
        return () => window.removeEventListener('apex:portfolio-update', handler);
    }, []);

    async function toggleTradingMode() {
        const newMode: TradingMode = tradingMode === 'paper' ? 'live' : 'paper';
        try {
            await fetch(`/api/v1/alpaca/settings/trading-mode?mode=${newMode}`, { method: 'POST' });
            setTradingMode(newMode);
            const res = await fetch('/api/v1/alpaca/portfolio').then(r => r.json());
            setBalances(prev => ({ ...prev, alpaca: res.portfolio_value ?? prev.alpaca }));
            // Notify dashboard pages to refetch
            window.dispatchEvent(new Event('apex:mode-changed'));
        } catch { /* ignore */ }
    }

    const toggleGroup = useCallback((key: string) => {
        setOpenGroups(prev => ({ ...prev, [key]: !prev[key] }));
    }, []);

    const handleNavClick = useCallback(() => {
        setSearchOpen(false);
        setDomainDropupOpen(false);
        if (domainHoldTimerRef.current) {
            window.clearTimeout(domainHoldTimerRef.current);
            domainHoldTimerRef.current = null;
        }
        if (domainCloseTimerRef.current) {
            window.clearTimeout(domainCloseTimerRef.current);
            domainCloseTimerRef.current = null;
        }
    }, []);

    // Fetch initial system status
    useEffect(() => {
        fetch('/api/v1/system/status')
            .then(r => r.json())
            .then(data => {
                setIsSleeping(!!data.paused);
                const d = data?.domain_paused || {};
                setDomainPaused({
                    stocks: !!d.stocks,
                    events: !!d.events,
                    sports: !!d.sports,
                });
            })
            .catch(() => { });
    }, []);

    const openDomainDropupDelayed = useCallback(() => {
        if (domainCloseTimerRef.current) {
            window.clearTimeout(domainCloseTimerRef.current);
            domainCloseTimerRef.current = null;
        }
        if (domainHoldTimerRef.current) window.clearTimeout(domainHoldTimerRef.current);
        domainHoldTimerRef.current = window.setTimeout(() => {
            setDomainDropupOpen(true);
        }, 1000);
    }, []);

    const closeDomainDropupSoon = useCallback(() => {
        if (domainHoldTimerRef.current) {
            window.clearTimeout(domainHoldTimerRef.current);
            domainHoldTimerRef.current = null;
        }
        if (domainCloseTimerRef.current) window.clearTimeout(domainCloseTimerRef.current);
        domainCloseTimerRef.current = window.setTimeout(() => {
            setDomainDropupOpen(false);
        }, 600);
    }, []);

    const keepDomainDropupOpen = useCallback(() => {
        if (domainCloseTimerRef.current) {
            window.clearTimeout(domainCloseTimerRef.current);
            domainCloseTimerRef.current = null;
        }
    }, []);

    useEffect(() => {
        return () => {
            if (domainHoldTimerRef.current) window.clearTimeout(domainHoldTimerRef.current);
            if (domainCloseTimerRef.current) window.clearTimeout(domainCloseTimerRef.current);
        };
    }, []);

    const toggleDomainPause = useCallback(async (domain: keyof DomainPauseState) => {
        const next = !domainPaused[domain];
        try {
            const res = await fetch('/api/v1/system/domain', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ domain, enabled: next }),
            });
            if (!res.ok) throw new Error('Domain toggle failed');
            const data = await res.json();
            const d = data?.domain_paused || {};
            setDomainPaused({
                stocks: !!d.stocks,
                events: !!d.events,
                sports: !!d.sports,
            });
        } catch {
            pushToast({ title: 'Error', message: `Failed to toggle ${domain} mode`, type: 'error' });
        }
    }, [domainPaused]);

    const toggleSleep = async () => {
        const newState = !isSleeping;
        // Immediately mirror domain toggles so the dropup stays in sync.
        setDomainPaused({ stocks: newState, events: newState, sports: newState });
        try {
            await fetch('/api/v1/system/sleep', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: newState }),
            });
            setIsSleeping(newState);
            if (!newState) {
                pushToast({
                    title: 'System Live',
                    message: 'Live services resumed.',
                    type: 'success',
                });
                setNotifPulseSeq(prev => prev + 1);
                // Returning to live reconnects WS; refresh once to pick up any missed events.
                setTimeout(() => {
                    void refreshNotificationCount();
                }, 400);
                // Only sync domain states from server when waking up.
                // (Backend doesn't flip individual domain_paused on global sleep.)
                const status = await fetch('/api/v1/system/status').then(r => r.json()).catch(() => null);
                if (status?.domain_paused) {
                    setDomainPaused({
                        stocks: !!status.domain_paused.stocks,
                        events: !!status.domain_paused.events,
                        sports: !!status.domain_paused.sports,
                    });
                }
            }
        } catch (e) {
            // Revert on failure.
            setDomainPaused({ stocks: !newState, events: !newState, sports: !newState });
            pushToast({ title: 'Error', message: 'Failed to toggle offline mode', type: 'error' });
        }
    };

    // Resolve page title from current route
    const getPageTitle = () => {
        for (const group of [...NAV_GROUPS, CONFIG_GROUP]) {
            for (const item of group.items) {
                if (item.end ? location.pathname === item.path : location.pathname.startsWith(item.path)) {
                    return item.label;
                }
            }
        }
        return 'Dashboard';
    };

    return (
        <div className="layout">
            {/* ── Sidebar ── */}
            <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
                {/* Logo */}
                <div className="sidebar-logo">
                    {!collapsed && (
                        <>
                            <span className="logo-icon">◈</span>
                            <span className="logo-text">APEX</span>
                        </>
                    )}
                    {collapsed && <span className="logo-icon">◈</span>}
                    <button className="collapse-btn" onClick={() => setCollapsed(!collapsed)}>
                        {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
                    </button>
                </div>

                {/* Search trigger */}
                {!collapsed && (
                    <button className="search-trigger" onClick={() => setSearchOpen(true)}>
                        <Search size={14} />
                        <span>Search…</span>
                        <span className="search-trigger-kbd">⌘K</span>
                    </button>
                )}

                {/* Navigation Groups */}
                <nav className="sidebar-nav">
                    {NAV_GROUPS.map(group => {
                        const isOpen = openGroups[group.key] ?? false;
                        const isActiveGroup = group.items.some(item =>
                            item.end ? location.pathname === item.path : location.pathname.startsWith(item.path)
                        );

                        return (
                            <div key={group.key} className="nav-group">
                                <button
                                    className={`nav-group-header ${isActiveGroup ? 'active' : ''}`}
                                    style={{ '--nav-color': group.color } as React.CSSProperties}
                                    onClick={() => toggleGroup(group.key)}
                                >
                                    <group.icon size={16} />
                                    {!collapsed && (
                                        <>
                                            <span className="nav-label">{group.label}</span>
                                            <ChevronDown size={12} className={`nav-chevron ${isOpen ? 'open' : ''}`} />
                                        </>
                                    )}
                                </button>

                                {/* Accordion body */}
                                {!collapsed && isOpen && (
                                    <div className="nav-group-items">
                                        {group.items.map(item => (
                                            <NavLink
                                                key={item.path}
                                                to={item.path}
                                                end={item.end}
                                                onClick={handleNavClick}
                                                className={({ isActive }) =>
                                                    `nav-item nav-sub ${isActive ? 'active' : ''}`
                                                }
                                                style={{ '--nav-color': group.color } as React.CSSProperties}
                                            >
                                                <item.icon size={14} />
                                                <span className="nav-label">{item.label}</span>
                                            </NavLink>
                                        ))}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </nav>

                {/* Config Section — above footer */}
                <div className="sidebar-config">
                    {(() => {
                        const group = CONFIG_GROUP;
                        const isOpen = openGroups[group.key] ?? false;
                        const isActiveGroup = group.items.some(item =>
                            item.end ? location.pathname === item.path : location.pathname.startsWith(item.path)
                        );
                        return (
                            <div className="nav-group">
                                <button
                                    className={`nav-group-header ${isActiveGroup ? 'active' : ''}`}
                                    style={{ '--nav-color': group.color } as React.CSSProperties}
                                    onClick={() => toggleGroup(group.key)}
                                >
                                    <group.icon size={16} />
                                    {!collapsed && (
                                        <>
                                            <span className="nav-label">{group.label}</span>
                                            <ChevronDown size={12} className={`nav-chevron ${isOpen ? 'open' : ''}`} />
                                        </>
                                    )}
                                </button>
                                {!collapsed && isOpen && (
                                    <div className="nav-group-items">
                                        {group.items.map(item => (
                                            <NavLink
                                                key={item.path}
                                                to={item.path}
                                                end={item.end}
                                                onClick={handleNavClick}
                                                className={({ isActive }) =>
                                                    `nav-item nav-sub ${isActive ? 'active' : ''}`
                                                }
                                                style={{ '--nav-color': group.color } as React.CSSProperties}
                                            >
                                                <item.icon size={14} />
                                                <span className="nav-label">{item.label}</span>
                                            </NavLink>
                                        ))}
                                    </div>
                                )}
                            </div>
                        );
                    })()}
                </div>

                {/* Status & Offline Toggle (Merged) */}
                <div
                    className="sleep-toggle-wrap"
                    onMouseEnter={openDomainDropupDelayed}
                    onMouseLeave={closeDomainDropupSoon}
                >
                    {domainDropupOpen && !collapsed && (
                        <div
                            className="domain-dropup"
                            onMouseEnter={keepDomainDropupOpen}
                            onMouseLeave={closeDomainDropupSoon}
                            onClick={(e) => e.stopPropagation()}
                        >
                            {([
                                ['stocks', 'Stocks'],
                                ['events', 'Events'],
                                ['sports', 'Sports'],
                            ] as Array<[keyof DomainPauseState, string]>).map(([key, label]) => (
                                <button
                                    key={key}
                                    className={`domain-dropup-item ${domainPaused[key] ? 'offline' : 'live'}`}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        void toggleDomainPause(key);
                                    }}
                                >
                                    <span>{label}</span>
                                    <span>{domainPaused[key] ? 'Offline' : 'Live'}</span>
                                </button>
                            ))}
                        </div>
                    )}
                    <button
                        className={`sidebar-footer sleep-toggle-footer ${isSleeping ? 'offline' : 'live'}`}
                        onClick={toggleSleep}
                        title={isSleeping ? "Go Live (Reconnect)" : "Go Offline (Disconnect)"}
                    >
                        <div className="connection-status">
                            {isSleeping ? (
                                <>
                                    <WifiOff size={14} className="text-red" />
                                    {!collapsed && <span className="text-red">Offline</span>}
                                </>
                            ) : connected ? (() => {
                                const offlineCount = Object.values(domainPaused).filter(Boolean).length;
                                const colorClass = offlineCount === 0 ? 'text-green' : offlineCount >= 3 ? 'text-red' : 'text-orange';
                                const label = offlineCount === 0 ? 'Live' : offlineCount >= 3 ? 'All Offline' : `${offlineCount} Offline`;
                                return (
                                    <>
                                        <Wifi size={14} className={colorClass} />
                                        {!collapsed && <span className={colorClass}>{label}</span>}
                                    </>
                                );
                            })() : (
                                <>
                                    <WifiOff size={14} className="text-red" />
                                    {!collapsed && <span className="text-red">Connecting...</span>}
                                </>
                            )}
                        </div>
                    </button>
                </div>
            </aside>


            {/* ── Main Content ── */}
            <div className="main-area">
                {/* Top Bar */}
                <header className="topbar">
                    <div className="topbar-left">
                        <h2 className="page-title">{getPageTitle()}</h2>
                    </div>
                    <div className="topbar-right">
                        <button
                            key={`alerts-pulse-${notifPulseSeq}`}
                            className={`notif-toggle-btn ${notifPulseSeq > 0 ? 'pulse' : ''}`}
                            onClick={() => setNotifOpen(prev => !prev)}
                            title="Notifications"
                        >
                            <Bell size={14} />
                            <span>Alerts</span>
                            <span className="count">{notifCount}</span>
                        </button>
                        <button className={`mode-toggle-global ${tradingMode}`} onClick={toggleTradingMode}>
                            {tradingMode === 'paper' ? <ToggleLeft size={16} /> : <ToggleRight size={16} />}
                            <span>{tradingMode === 'paper' ? 'PAPER' : 'LIVE'}</span>
                        </button>
                        <div className="topbar-services">
                            <span
                                className={`status-dot ${!isSleeping && balances.alpaca !== null ? 'connected' : 'disconnected'}`}
                                title={isSleeping ? "Alpaca (Offline)" : "Alpaca"}
                            />
                            <span
                                className={`status-dot ${!isSleeping && balances.kalshi !== null ? 'connected' : 'disconnected'}`}
                                title={isSleeping ? "Kalshi (Offline)" : "Kalshi"}
                            />
                        </div>
                    </div>
                </header>

                {/* Page Content */}
                <main className="page-content fade-in" key={location.pathname}>
                    <Outlet context={{ isSleeping }} />
                </main>
            </div>
            <NotificationDrawer open={notifOpen} onClose={() => setNotifOpen(false)} onCountChange={setNotifCount} />

            <Toaster />
            <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
        </div>
    );
}
