import { useState, useEffect, useCallback } from 'react';
import { X, AlertTriangle, CheckCircle, Info, Zap } from 'lucide-react';
import './Toaster.css';

export type ToastType = 'success' | 'error' | 'warning' | 'info' | 'signal';

export interface Toast {
    id: string;
    type: ToastType;
    title: string;
    message: string;
    domain?: string;
    timestamp: number;
    duration?: number;
}

// Global toast queue — accessible from anywhere
const listeners = new Set<(toast: Toast) => void>();

export function pushToast(toast: Omit<Toast, 'id' | 'timestamp'>) {
    const full: Toast = {
        ...toast,
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: Date.now(),
    };
    listeners.forEach(fn => fn(full));
}

const ICONS: Record<ToastType, typeof Info> = {
    success: CheckCircle,
    error: AlertTriangle,
    warning: AlertTriangle,
    info: Info,
    signal: Zap,
};

export default function Toaster() {
    const [toasts, setToasts] = useState<Toast[]>([]);

    const addToast = useCallback((toast: Toast) => {
        setToasts(prev => [toast, ...prev].slice(0, 5)); // max 5 visible
    }, []);

    const removeToast = useCallback((id: string) => {
        setToasts(prev => prev.filter(t => t.id !== id));
    }, []);

    useEffect(() => {
        listeners.add(addToast);
        return () => { listeners.delete(addToast); };
    }, [addToast]);

    // Auto-dismiss
    useEffect(() => {
        const timers = toasts.map(t => {
            const dur = t.duration ?? (t.type === 'error' ? 6000 : 3000);
            return setTimeout(() => removeToast(t.id), dur);
        });
        return () => timers.forEach(clearTimeout);
    }, [toasts, removeToast]);

    if (toasts.length === 0) return null;

    return (
        <div className="toaster-container">
            {toasts.map(toast => {
                const Icon = ICONS[toast.type];
                return (
                    <div key={toast.id} className={`toast toast-${toast.type} fade-in`}>
                        <div className="toast-icon">
                            <Icon size={16} />
                        </div>
                        <div className="toast-body">
                            <div className="toast-title">
                                {toast.title}
                                {toast.domain && <span className="toast-domain">{toast.domain}</span>}
                            </div>
                            <div className="toast-message">{toast.message}</div>
                        </div>
                        <button className="toast-close" onClick={() => removeToast(toast.id)}>
                            <X size={14} />
                        </button>
                    </div>
                );
            })}
        </div>
    );
}
