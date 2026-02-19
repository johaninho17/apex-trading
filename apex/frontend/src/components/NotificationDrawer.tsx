import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bell, ChevronDown, ChevronRight, Loader2, Trash2, X } from 'lucide-react';
import './NotificationDrawer.css';

type NotificationGroup = 'all' | 'stocks' | 'crypto' | 'dfs' | 'events' | 'system';

interface NotificationSummary {
    id: string;
    ts: number;
    title: string;
    channel: string;
    group: NotificationGroup;
}

interface NotificationDetail {
    id: string;
    ts: number;
    title: string;
    channel: string;
    group: NotificationGroup;
    event_type: string;
    severity: string;
    message?: string;
    payload: Record<string, unknown>;
}

interface NotificationDrawerProps {
    open: boolean;
    onClose: () => void;
    onCountChange?: (count: number) => void;
}

type ConfirmState =
    | { type: 'clear' }
    | { type: 'delete'; id: string }
    | null;

const GROUP_TABS: Array<{ key: NotificationGroup; label: string }> = [
    { key: 'all', label: 'All' },
    { key: 'stocks', label: 'Stocks' },
    { key: 'crypto', label: 'Crypto' },
    { key: 'dfs', label: 'DFS' },
    { key: 'events', label: 'Events' },
    { key: 'system', label: 'System' },
];

function fmtTime(ts: number): string {
    try {
        return new Date(ts).toLocaleString();
    } catch {
        return String(ts);
    }
}

function mergeUnique(next: NotificationSummary[], current: NotificationSummary[]): NotificationSummary[] {
    const byId = new Map<string, NotificationSummary>();
    [...next, ...current].forEach(item => byId.set(item.id, item));
    return Array.from(byId.values()).sort((a, b) => b.ts - a.ts);
}

function matchesGroup(item: { group?: string }, group: NotificationGroup): boolean {
    if (group === 'all') return true;
    return item.group === group;
}

type TitleTone = 'success' | 'error' | 'warning' | 'info' | 'neutral';

function toneFromItem(item: NotificationSummary, detail?: NotificationDetail): TitleTone {
    const severity = String(detail?.severity || '').toLowerCase();
    if (severity === 'success') return 'success';
    if (severity === 'error' || severity === 'critical') return 'error';
    if (severity === 'warning' || severity === 'warn') return 'warning';
    if (severity === 'info') return 'info';

    const t = String(item.title || '').toLowerCase();

    if (t.includes('offline') || t.includes('failed') || t.includes('error') || t.includes('rejected')) {
        return 'error';
    }
    if (t.includes('warning') || t.includes('detected') || t.includes('risk')) {
        return 'warning';
    }
    if (t.includes('live') || t.includes('success') || t.includes('buy signal') || t.includes('plays found')) {
        return 'success';
    }
    if (item.group === 'system' || t.includes('scan') || t.includes('connected') || t.includes('resumed')) {
        return 'info';
    }
    return 'neutral';
}

export default function NotificationDrawer({ open, onClose, onCountChange }: NotificationDrawerProps) {
    const [items, setItems] = useState<NotificationSummary[]>([]);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [detailsById, setDetailsById] = useState<Record<string, NotificationDetail>>({});
    const [loadingList, setLoadingList] = useState(false);
    const [loadingDetailId, setLoadingDetailId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [group, setGroup] = useState<NotificationGroup>('all');
    const [totalCount, setTotalCount] = useState(0);
    const [confirmState, setConfirmState] = useState<ConfirmState>(null);

    const loadTotalCount = useCallback(async () => {
        try {
            const res = await fetch('/api/v1/notifications?days=7&limit=1000&group=all');
            if (!res.ok) return;
            const data = await res.json();
            const count = Number(data.count || 0);
            setTotalCount(count);
            onCountChange?.(count);
        } catch {
            // Silent fail
        }
    }, [onCountChange]);

    const loadList = useCallback(async (currentGroup: NotificationGroup) => {
        setLoadingList(true);
        setError(null);
        try {
            const res = await fetch(`/api/v1/notifications?days=7&limit=300&group=${currentGroup}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const incoming = (Array.isArray(data.items) ? data.items : []) as NotificationSummary[];
            setItems(incoming.sort((a, b) => b.ts - a.ts));
        } catch (e) {
            setError((e as Error).message || 'Failed to load notifications');
        } finally {
            setLoadingList(false);
        }
    }, []);

    useEffect(() => {
        if (!open) return;
        loadList(group);
        loadTotalCount();
    }, [open, group, loadList, loadTotalCount]);

    useEffect(() => {
        if (!open) setConfirmState(null);
    }, [open]);

    useEffect(() => {
        if (!open) return;

        const onRealtime = (event: Event) => {
            const detail = (event as CustomEvent).detail as NotificationSummary | undefined;
            if (!detail?.id || !detail?.ts || !detail?.title) return;
            setTotalCount(prev => prev + 1);
            if (!matchesGroup(detail, group)) return;
            setItems(prev => mergeUnique([detail], prev));
        };

        window.addEventListener('apex:notification', onRealtime);
        return () => window.removeEventListener('apex:notification', onRealtime);
    }, [open, group]);

    useEffect(() => {
        if (!open) return;
        const onResync = () => {
            void loadList(group);
            void loadTotalCount();
        };
        window.addEventListener('apex:notifications-resync', onResync);
        return () => window.removeEventListener('apex:notifications-resync', onResync);
    }, [open, group, loadList, loadTotalCount]);

    const fetchDetail = useCallback(async (id: string) => {
        if (detailsById[id]) return detailsById[id];
        setLoadingDetailId(id);
        try {
            const res = await fetch(`/api/v1/notifications/${id}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const detail = await res.json();
            setDetailsById(prev => ({ ...prev, [id]: detail }));
            return detail as NotificationDetail;
        } finally {
            setLoadingDetailId(null);
        }
    }, [detailsById]);

    const toggleExpand = useCallback(async (id: string) => {
        if (expandedId === id) {
            setExpandedId(null);
            return;
        }
        setExpandedId(id);
        await fetchDetail(id);
    }, [expandedId, fetchDetail]);

    const deleteSingle = useCallback(async (id: string) => {
        const res = await fetch(`/api/v1/notifications/${id}`, { method: 'DELETE' });
        if (!res.ok) return;
        setItems(prev => prev.filter(item => item.id !== id));
        setDetailsById(prev => {
            const next = { ...prev };
            delete next[id];
            return next;
        });
        if (expandedId === id) setExpandedId(null);
        setTotalCount(prev => {
            const next = Math.max(0, prev - 1);
            onCountChange?.(next);
            return next;
        });
    }, [expandedId, onCountChange]);

    const clearCurrentFilter = useCallback(async () => {
        const res = await fetch(`/api/v1/notifications?group=${group}`, { method: 'DELETE' });
        if (!res.ok) return;
        const data = await res.json();
        const deleted = Number(data.deleted || 0);

        setItems([]);
        setExpandedId(null);
        setDetailsById(prev => {
            if (group === 'all') return {};
            const next = { ...prev };
            Object.keys(next).forEach(k => {
                if (next[k].group === group) delete next[k];
            });
            return next;
        });

        setTotalCount(prev => {
            const next = Math.max(0, prev - deleted);
            onCountChange?.(next);
            return next;
        });
    }, [group, onCountChange]);

    const onConfirmDeleteSingle = useCallback(async (id: string) => {
        await deleteSingle(id);
        setConfirmState(null);
    }, [deleteSingle]);

    const onConfirmClear = useCallback(async () => {
        await clearCurrentFilter();
        setConfirmState(null);
    }, [clearCurrentFilter]);

    const expandedDetail = useMemo(
        () => (expandedId ? detailsById[expandedId] : null),
        [expandedId, detailsById]
    );

    return (
        <aside className={`notif-drawer ${open ? 'open' : ''}`}>
            <div className="notif-header">
                <div className="notif-title-wrap">
                    <Bell size={16} />
                    <h3>Notifications</h3>
                </div>
                <div className="notif-header-actions">
                    <div className="notif-action-wrap">
                        <button
                            className="notif-clear-btn"
                            onClick={() => setConfirmState(prev => (prev?.type === 'clear' ? null : { type: 'clear' }))}
                            title="Clear current filter"
                        >
                            <Trash2 size={13} />
                            <span>Clear</span>
                        </button>
                        {confirmState?.type === 'clear' && (
                            <div className="notif-confirm-popover" role="dialog" aria-label="Confirm clear notifications">
                                <p className="notif-confirm-text">Clear this filter?</p>
                                <div className="notif-confirm-actions">
                                    <button className="notif-confirm-btn confirm" onClick={() => void onConfirmClear()}>Confirm</button>
                                    <button className="notif-confirm-btn cancel" onClick={() => setConfirmState(null)}>Cancel</button>
                                </div>
                            </div>
                        )}
                    </div>
                    <button className="notif-close-btn" onClick={onClose} aria-label="Close notifications">
                        <X size={14} />
                    </button>
                </div>
            </div>

            <div className="notif-tabs">
                {GROUP_TABS.map(tab => (
                    <button
                        key={tab.key}
                        className={`notif-tab ${group === tab.key ? 'active' : ''}`}
                        onClick={() => setGroup(tab.key)}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            <div className="notif-subtitle">Last 7 days • {totalCount} total</div>

            {loadingList && (
                <div className="notif-loading">
                    <Loader2 size={14} className="spin" />
                    <span>Loading...</span>
                </div>
            )}
            {error && <div className="notif-error">{error}</div>}

            <div className="notif-list">
                {items.map(item => {
                    const isExpanded = expandedId === item.id;
                    const isLoadingDetail = loadingDetailId === item.id;
                    const tone = toneFromItem(item, detailsById[item.id]);
                    return (
                        <div key={item.id} className={`notif-item ${isExpanded ? 'expanded' : ''}`}>
                            <button className="notif-row" onClick={() => toggleExpand(item.id)}>
                                <span className="notif-row-icon">
                                    {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                </span>
                                <span className={`notif-row-title tone-${tone}`}>{item.title}</span>
                                <span className="notif-row-time mono">{fmtTime(item.ts)}</span>
                            </button>
                            <div className="notif-row-actions">
                                <button
                                    className="notif-delete-one"
                                    onClick={() => setConfirmState(prev => (prev?.type === 'delete' && prev.id === item.id ? null : { type: 'delete', id: item.id }))}
                                    title="Delete"
                                >
                                    <Trash2 size={12} />
                                </button>
                                {confirmState?.type === 'delete' && confirmState.id === item.id && (
                                    <div className="notif-confirm-popover item" role="dialog" aria-label="Confirm delete notification">
                                        <p className="notif-confirm-text">Delete notification?</p>
                                        <div className="notif-confirm-actions">
                                            <button className="notif-confirm-btn confirm" onClick={() => void onConfirmDeleteSingle(item.id)}>Confirm</button>
                                            <button className="notif-confirm-btn cancel" onClick={() => setConfirmState(null)}>Cancel</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                            {isExpanded && (
                                <div className="notif-detail">
                                    {isLoadingDetail && (
                                        <div className="notif-loading-detail">
                                            <Loader2 size={13} className="spin" />
                                            <span>Loading details...</span>
                                        </div>
                                    )}
                                    {!isLoadingDetail && expandedDetail && (
                                        <>
                                            <div className="notif-detail-meta">
                                                <span>{expandedDetail.group}</span>
                                                <span>{expandedDetail.channel}</span>
                                                <span>{expandedDetail.event_type}</span>
                                                <span>{expandedDetail.severity}</span>
                                            </div>
                                            {expandedDetail.message && (
                                                <p className="notif-message">{expandedDetail.message}</p>
                                            )}
                                            <pre className="notif-json mono">
                                                {JSON.stringify(expandedDetail.payload || {}, null, 2)}
                                            </pre>
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
                {!loadingList && items.length === 0 && <div className="notif-empty">No notifications in this filter.</div>}
            </div>
        </aside>
    );
}
