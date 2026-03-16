import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  Bell,
  BrainCircuit,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Coins,
  HelpCircle,
  Layers,
  Radar,
  Search,
  Settings,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useWebSocket } from '../hooks/useWebSocket';
import GlobalSearch from './GlobalSearch';
import NotificationDrawer from './NotificationDrawer';
import Toaster, { pushToast } from './Toaster';
import { postTradingMode } from '../lib/cryptoApi';
import { fetchJson } from '../lib/api';
import { useTerminalSummaryQuery } from '../lib/cryptoQueries';
import './Layout.css';

type DomainPauseState = {
  stocks: boolean;
  events: boolean;
  sports: boolean;
};

type TradingMode = 'paper' | 'live';

interface NavItem {
  path: string;
  label: string;
  icon: typeof Coins;
  end?: boolean;
}

interface NavGroup {
  key: string;
  label: string;
  icon: typeof Coins;
  color: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    key: 'crypto',
    label: 'Crypto',
    icon: Coins,
    color: 'var(--color-alpaca)',
    items: [
      { path: '/crypto/dashboard', label: 'Dashboard', icon: Coins },
      { path: '/crypto/hub', label: 'Hub', icon: Radar },
      { path: '/crypto/learning', label: 'Learning', icon: BrainCircuit },
      { path: '/crypto/activity', label: 'Activity', icon: Activity },
    ],
  },
  {
    key: 'parked',
    label: 'Parked',
    icon: Layers,
    color: 'var(--text-secondary)',
    items: [
      { path: '/alpaca', label: 'Stocks', icon: Layers },
      { path: '/kalshi', label: 'Kalshi', icon: Layers },
      { path: '/dfs', label: 'DFS', icon: Layers },
      { path: '/polymarket', label: 'Polymarket', icon: Layers },
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

function isPathActive(pathname: string, item: NavItem): boolean {
  return item.end ? pathname === item.path : pathname.startsWith(item.path);
}

export default function Layout() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [isSleeping, setIsSleeping] = useState(false);
  const [domainPaused, setDomainPaused] = useState<DomainPauseState>({ stocks: false, events: false, sports: false });
  const [domainDropupOpen, setDomainDropupOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [tradingMode, setTradingMode] = useState<TradingMode>('paper');
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    crypto: true,
    parked: true,
    config: false,
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const [, setNotifPulseSeq] = useState(0);
  const { connected, subscribe } = useWebSocket(undefined, !isSleeping);
  const seenNotifIdsRef = useRef<Set<string>>(new Set());
  const domainHoldTimerRef = useRef<number | null>(null);
  const domainCloseTimerRef = useRef<number | null>(null);

  const isCryptoRoute = location.pathname.startsWith('/crypto') || location.pathname.startsWith('/alpaca/crypto');
  const notificationsQuery = useQuery({
    queryKey: ['shell', 'notifications', 'count'],
    queryFn: () => fetchJson<{ items?: Array<{ id?: string }> }>('/api/v1/notifications?days=7&limit=300', { timeout: 8000 }),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: connected ? 60_000 : false,
  });
  const systemStatusQuery = useQuery({
    queryKey: ['shell', 'system-status'],
    queryFn: () => fetchJson<{ paused?: boolean; domain_paused?: Partial<DomainPauseState> }>('/api/v1/system/status', { timeout: 8000 }),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 45_000,
  });
  const terminalSummaryQuery = useTerminalSummaryQuery(true, isCryptoRoute && !isSleeping);

  const refreshNotificationCount = useCallback(async () => {
    await notificationsQuery.refetch();
  }, [notificationsQuery]);

  useEffect(() => {
    const activeGroup = NAV_GROUPS.find((group) => group.items.some((item) => isPathActive(location.pathname, item)));
    if (activeGroup) {
      setOpenGroups((prev) => ({ ...prev, [activeGroup.key]: true }));
    }
  }, [location.pathname]);

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

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    if (!connected) return;

    const unsubscribe = subscribe('*', (msg: unknown) => {
      const packet = (typeof msg === 'object' && msg !== null ? msg : {}) as {
        type?: string;
        data?: { notification?: { id?: string; title?: string; ts?: number } };
      };
      const notif = packet.data?.notification;
      let isNewNotification = false;
      if (notif?.id && notif?.title && notif?.ts) {
        if (seenNotifIdsRef.current.has(notif.id)) return;
        seenNotifIdsRef.current.add(notif.id);
        isNewNotification = true;
        setNotifCount((prev) => prev + 1);
        setNotifPulseSeq((prev) => prev + 1);
        window.dispatchEvent(new CustomEvent('apex:notification', { detail: notif }));
        void queryClient.invalidateQueries({ queryKey: ['shell', 'notifications'] });
      }
      if (packet.type === 'toast') {
        if (notif?.id && !isNewNotification) return;
        setNotifPulseSeq((prev) => prev + 1);
        const toast = packet.data as { title?: string; message?: string; type?: 'success' | 'error' | 'warning' | 'info' } | undefined;
        if (toast?.title && toast?.message && toast?.type) {
          pushToast({ title: toast.title, message: toast.message, type: toast.type });
        }
      }
    });

    return () => {
      unsubscribe();
    };
  }, [connected, queryClient, subscribe]);

  useEffect(() => {
    const items = Array.isArray(notificationsQuery.data?.items) ? notificationsQuery.data.items : [];
    setNotifCount(items.length);
    seenNotifIdsRef.current = new Set(
      items
        .map((item) => item?.id)
        .filter((id): id is string => typeof id === 'string' && id.length > 0),
    );
    window.dispatchEvent(new Event('apex:notifications-resync'));
  }, [notificationsQuery.data]);

  useEffect(() => {
    const data = systemStatusQuery.data;
    if (!data) return;
    setIsSleeping(Boolean(data.paused));
    const paused = data.domain_paused ?? {};
    setDomainPaused({
      stocks: Boolean(paused.stocks),
      events: Boolean(paused.events),
      sports: Boolean(paused.sports),
    });
  }, [systemStatusQuery.data]);

  useEffect(() => {
    const summary = terminalSummaryQuery.data;
    if (!summary) return;
    const botConfigSummary = (summary.bot_config_summary ?? summary.config_summary ?? {}) as Record<string, unknown>;
    const accountMode = String(botConfigSummary.account_mode ?? 'paper').toLowerCase();
    setTradingMode(accountMode === 'live' ? 'live' : 'paper');
  }, [terminalSummaryQuery.data]);

  useEffect(() => {
    const onPortfolioUpdate = (event: Event) => {
      const detail = (event as CustomEvent<{ tradingMode?: string }>).detail;
      if (detail?.tradingMode === 'live' || detail?.tradingMode === 'paper') {
        setTradingMode(detail.tradingMode);
      }
    };
    window.addEventListener('apex:portfolio-update', onPortfolioUpdate);
    return () => window.removeEventListener('apex:portfolio-update', onPortfolioUpdate);
  }, []);

  const toggleGroup = useCallback((key: string) => {
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const openDomainDropupDelayed = useCallback(() => {
    if (domainCloseTimerRef.current) {
      window.clearTimeout(domainCloseTimerRef.current);
      domainCloseTimerRef.current = null;
    }
    if (domainHoldTimerRef.current) {
      window.clearTimeout(domainHoldTimerRef.current);
    }
    domainHoldTimerRef.current = window.setTimeout(() => {
      setDomainDropupOpen(true);
    }, 1000);
  }, []);

  const closeDomainDropupSoon = useCallback(() => {
    if (domainHoldTimerRef.current) {
      window.clearTimeout(domainHoldTimerRef.current);
      domainHoldTimerRef.current = null;
    }
    if (domainCloseTimerRef.current) {
      window.clearTimeout(domainCloseTimerRef.current);
    }
    domainCloseTimerRef.current = window.setTimeout(() => setDomainDropupOpen(false), 500);
  }, []);

  const keepDomainDropupOpen = useCallback(() => {
    if (domainCloseTimerRef.current) {
      window.clearTimeout(domainCloseTimerRef.current);
      domainCloseTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => {
    if (domainHoldTimerRef.current) window.clearTimeout(domainHoldTimerRef.current);
    if (domainCloseTimerRef.current) window.clearTimeout(domainCloseTimerRef.current);
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
      const data = await res.json() as { domain_paused?: Partial<DomainPauseState> };
      const paused = data.domain_paused ?? {};
      setDomainPaused({
        stocks: Boolean(paused.stocks),
        events: Boolean(paused.events),
        sports: Boolean(paused.sports),
      });
    } catch {
      pushToast({ title: 'Error', message: `Failed to toggle ${domain} mode`, type: 'error' });
    }
  }, [domainPaused]);

  const toggleSleep = useCallback(async () => {
    const newState = !isSleeping;
    setDomainPaused({ stocks: newState, events: newState, sports: newState });
    try {
      await fetch('/api/v1/system/sleep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: newState }),
      });
      setIsSleeping(newState);
      if (!newState) {
        pushToast({ title: 'System Live', message: 'Live services resumed.', type: 'success' });
        setNotifPulseSeq((prev) => prev + 1);
        setTimeout(() => { void refreshNotificationCount(); }, 400);
      }
    } catch {
      setDomainPaused({ stocks: !newState, events: !newState, sports: !newState });
      pushToast({ title: 'Error', message: 'Failed to toggle offline mode', type: 'error' });
    }
  }, [isSleeping, refreshNotificationCount]);

  const toggleTradingMode = useCallback(async () => {
    const nextMode: TradingMode = tradingMode === 'paper' ? 'live' : 'paper';
    try {
      await postTradingMode(nextMode);
      setTradingMode(nextMode);
      window.dispatchEvent(new Event('apex:mode-changed'));
    } catch {
      pushToast({ title: 'Error', message: 'Failed to update trading mode', type: 'error' });
    }
  }, [tradingMode]);

  const pageTitle = (() => {
    for (const group of [...NAV_GROUPS, CONFIG_GROUP]) {
      for (const item of group.items) {
        if (isPathActive(location.pathname, item)) return item.label;
      }
    }
    if (location.pathname.startsWith('/crypto/symbol/')) return 'Symbol Workspace';
    return 'Crypto';
  })();

  const headerStatusStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    fontSize: '0.75rem',
    color: 'var(--text-secondary)',
  };

  return (
    <div className="layout" style={{ '--shell-sidebar-offset': collapsed ? '64px' : '240px' } as CSSProperties}>
      <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-logo">
          {!collapsed ? (
            <>
              <span className="logo-icon">◈</span>
              <span className="logo-text">APEX</span>
            </>
          ) : (
            <span className="logo-icon">◈</span>
          )}
          <button className="collapse-btn" onClick={() => setCollapsed((prev) => !prev)}>
            {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>

        {!collapsed && (
          <button className="search-trigger" onClick={() => setSearchOpen(true)}>
            <Search size={14} />
            <span>Search…</span>
            <span className="search-trigger-kbd">⌘K</span>
          </button>
        )}

        <nav className="sidebar-nav">
          {NAV_GROUPS.map((group) => {
            const isOpen = openGroups[group.key] ?? false;
            const isActiveGroup = group.items.some((item) => isPathActive(location.pathname, item));
            return (
              <div key={group.key} className="nav-group">
                <button
                  className={`nav-group-header ${isActiveGroup ? 'active' : ''}`}
                  style={{ '--nav-color': group.color } as CSSProperties}
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
                    {group.items.map((item) => (
                      <NavLink
                        key={item.path}
                        to={item.path}
                        end={item.end}
                        className={({ isActive }) => `nav-item nav-sub ${isActive ? 'active' : ''}`}
                        style={{ '--nav-color': group.color } as CSSProperties}
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

        <div className="sidebar-config">
          <div className="nav-group">
            <button
              className={`nav-group-header ${CONFIG_GROUP.items.some((item) => isPathActive(location.pathname, item)) ? 'active' : ''}`}
              style={{ '--nav-color': CONFIG_GROUP.color } as CSSProperties}
              onClick={() => toggleGroup(CONFIG_GROUP.key)}
            >
              <CONFIG_GROUP.icon size={16} />
              {!collapsed && (
                <>
                  <span className="nav-label">{CONFIG_GROUP.label}</span>
                  <ChevronDown size={12} className={`nav-chevron ${openGroups.config ? 'open' : ''}`} />
                </>
              )}
            </button>
            {!collapsed && openGroups.config && (
              <div className="nav-group-items">
                {CONFIG_GROUP.items.map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.end}
                    className={({ isActive }) => `nav-item nav-sub ${isActive ? 'active' : ''}`}
                    style={{ '--nav-color': CONFIG_GROUP.color } as CSSProperties}
                  >
                    <item.icon size={14} />
                    <span className="nav-label">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="sleep-toggle-wrap" onMouseEnter={openDomainDropupDelayed} onMouseLeave={closeDomainDropupSoon}>
          {domainDropupOpen && !collapsed && (
            <div className="domain-dropup" onMouseEnter={keepDomainDropupOpen} onMouseLeave={closeDomainDropupSoon}>
              {([
                ['stocks', 'Crypto'],
                ['events', 'Events'],
                ['sports', 'Sports'],
              ] as Array<[keyof DomainPauseState, string]>).map(([key, label]) => (
                <button
                  key={key}
                  className={`domain-dropup-item ${domainPaused[key] ? 'offline' : 'live'}`}
                  onClick={() => void toggleDomainPause(key)}
                >
                  <span>{label}</span>
                  <span>{domainPaused[key] ? 'Offline' : 'Live'}</span>
                </button>
              ))}
            </div>
          )}
          <button
            className={`sidebar-footer sleep-toggle-footer ${isSleeping ? 'offline' : 'live'}`}
            onClick={() => void toggleSleep()}
            title={isSleeping ? 'Go Live' : 'Go Offline'}
          >
            <div className="connection-status">
              {isSleeping ? (
                <>
                  <WifiOff size={14} />
                  {!collapsed && <span>Offline</span>}
                </>
              ) : (
                <>
                  <Wifi size={14} />
                  {!collapsed && <span>Live</span>}
                </>
              )}
            </div>
          </button>
        </div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <div className="topbar-left">
            <div className="page-title">{pageTitle}</div>
          </div>
          <div className="topbar-right">
            <button className={`mode-toggle-global ${tradingMode}`} onClick={() => void toggleTradingMode()}>
              {tradingMode.toUpperCase()}
            </button>
            <button className="mode-toggle-global paper" onClick={() => setNotifOpen(true)}>
              <Bell size={14} />
              Alerts {notifCount > 0 ? notifCount : ''}
            </button>
            <div className="topbar-services" style={headerStatusStyle}>
              <span style={{ color: connected ? 'var(--accent-green)' : 'var(--accent-red)' }}>●</span>
              <span>{connected ? 'Connected' : 'Disconnected'}</span>
            </div>
          </div>
        </header>

        <main className="page-content">
          <Outlet />
        </main>
      </div>

      <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
      <NotificationDrawer open={notifOpen} onClose={() => setNotifOpen(false)} onCountChange={setNotifCount} />
      <Toaster />
    </div>
  );
}
