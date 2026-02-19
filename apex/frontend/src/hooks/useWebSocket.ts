import { useState, useEffect, useCallback, useRef } from 'react';

interface WSMessage {
    channel: string;
    type: string;
    data: any;
}

type MessageHandler = (msg: WSMessage) => void;

function buildWsUrl(): string {
    // In dev, bypass Vite WS proxy to avoid proxy EPIPE churn on reconnect/teardown.
    // In prod, use same-origin WebSocket endpoint.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    if (import.meta.env.DEV) {
        const devWs = (import.meta.env.VITE_WS_URL as string | undefined)?.trim();
        if (devWs) return devWs;
        return `${protocol}//127.0.0.1:8000/ws`;
    }
    return `${protocol}//${window.location.host}/ws`;
}

export function useWebSocket(url?: string, enabled: boolean = true) {
    const wsUrl = url ?? buildWsUrl();
    const [connected, setConnected] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const handlersRef = useRef<Map<string, MessageHandler[]>>(new Map());
    const reconnectRef = useRef<number>(0);
    const mountedRef = useRef(true);
    const enabledRef = useRef(enabled);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const connect = useCallback(() => {
        // Don't reconnect if component was unmounted or disabled
        if (!mountedRef.current || !enabledRef.current) return;

        try {
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                setConnected(true);
                reconnectRef.current = 0;
                console.log('[WS] Connected to', wsUrl);

                // Heartbeat — keeps connection alive through proxies/load balancers
                const pingInterval = setInterval(() => {
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ action: 'ping' }));
                    }
                }, 15000);
                ws.addEventListener('close', () => clearInterval(pingInterval));
            };

            ws.onmessage = (event) => {
                try {
                    const msg: WSMessage = JSON.parse(event.data);

                    // Skip pong messages (heartbeat response)
                    if (msg.type === 'pong') return;

                    const handlers = handlersRef.current.get(msg.channel) || [];
                    handlers.forEach((h) => h(msg));

                    // Also fire wildcard handlers
                    const allHandlers = handlersRef.current.get('*') || [];
                    allHandlers.forEach((h) => h(msg));
                } catch (e) {
                    console.error('[WS] Parse error:', e);
                }
            };

            ws.onclose = (event) => {
                setConnected(false);
                console.log(`[WS] Disconnected (code=${event.code})`);

                // Don't reconnect if we were intentionally unmounted
                if (!mountedRef.current) return;
                if (!enabledRef.current) return;

                // Exponential backoff: 1s, 2s, 4s, 8s, ... max 30s
                const delay = Math.min(1000 * Math.pow(2, reconnectRef.current), 30000);
                reconnectRef.current++;
                reconnectTimerRef.current = setTimeout(() => {
                    if (!mountedRef.current || !enabledRef.current) return;
                    connect();
                }, delay);
            };

            ws.onerror = () => {
                ws.close();
            };

            wsRef.current = ws;
        } catch (e) {
            console.error('[WS] Connection error:', e);
        }
    }, [wsUrl]);

    useEffect(() => {
        enabledRef.current = enabled;
        if (!enabled && reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
    }, [enabled]);

    useEffect(() => {
        mountedRef.current = true;
        if (enabled) {
            console.log('[useWebSocket] Enabled: Connecting...');
            connect();
        } else {
            console.log('[useWebSocket] Disabled: Closing connection...');
            wsRef.current?.close();
            setConnected(false);
        }
        return () => {
            mountedRef.current = false;
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
                reconnectTimerRef.current = null;
            }
            console.log('[useWebSocket] Unmounting: Closing connection...');
            wsRef.current?.close();
        };
    }, [connect, enabled]);

    const subscribe = useCallback((channel: string, handler: MessageHandler) => {
        const handlers = handlersRef.current.get(channel) || [];
        handlers.push(handler);
        handlersRef.current.set(channel, handlers);

        return () => {
            const updated = handlersRef.current.get(channel)?.filter((h) => h !== handler) || [];
            handlersRef.current.set(channel, updated);
        };
    }, []);

    const send = useCallback((data: any) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data));
        }
    }, []);

    return { connected, subscribe, send };
}
