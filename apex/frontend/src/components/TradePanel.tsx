import { useEffect, useState } from 'react';
import { Send, ShieldCheck, ArrowUpCircle, ArrowDownCircle, Loader2, CheckCircle2, AlertTriangle, X } from 'lucide-react';
import './TradePanel.css';

type OrderType = 'simple' | 'bracket';
type Side = 'buy' | 'sell';

interface OrderResult {
    success: boolean;
    order?: {
        id: string;
        status: string;
        symbol: string;
        qty: string;
        side: string;
        type: string;
    };
    detail?: string;
}

interface TradePanelProps {
    symbol: string;
    defaultSide?: Side;
    defaultQty?: string;
    /** If provided, shows a close (X) button and calls this on close */
    onClose?: () => void;
    /** Called after a successful order */
    onOrderSuccess?: () => void;
    /** If true, render compactly (e.g. inside Analysis page) */
    compact?: boolean;
}

export default function TradePanel({
    symbol: initialSymbol,
    defaultSide = 'buy',
    defaultQty = '',
    onClose,
    onOrderSuccess,
    compact = false,
}: TradePanelProps) {
    const [orderType, setOrderType] = useState<OrderType>('simple');
    const [symbol, setSymbol] = useState(initialSymbol);
    const [qty, setQty] = useState(defaultQty);
    const [side, setSide] = useState<Side>(defaultSide);
    const [simpleType, setSimpleType] = useState<'market' | 'limit'>('market');
    const [limitPrice, setLimitPrice] = useState('');
    const [tif, setTif] = useState('day');
    const [stopLoss, setStopLoss] = useState('');
    const [takeProfit, setTakeProfit] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<OrderResult | null>(null);
    const [showConfirm, setShowConfirm] = useState(false);

    useEffect(() => {
        if (compact && side === 'sell') setSide('buy');
    }, [compact, side]);

    async function submitOrder() {
        if (!symbol || !qty) return;
        setSubmitting(true);
        setResult(null);

        try {
            const url = orderType === 'simple'
                ? '/api/v1/alpaca/order/simple'
                : '/api/v1/alpaca/order/bracket';

            const body: Record<string, unknown> = {
                symbol: symbol.toUpperCase(),
                qty: parseFloat(qty),
                side,
            };

            if (orderType === 'simple') {
                body.order_type = simpleType;
                if (simpleType === 'limit' && limitPrice) body.limit_price = parseFloat(limitPrice);
                body.time_in_force = tif;
            } else {
                if (limitPrice) body.limit_price = parseFloat(limitPrice);
                body.stop_loss = parseFloat(stopLoss);
                body.take_profit = parseFloat(takeProfit);
                body.time_in_force = tif;
            }

            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (!res.ok) {
                setResult({ success: false, detail: data.detail || 'Order failed' });
            } else {
                setResult(data);
                if (data.success) onOrderSuccess?.();
            }
        } catch (e: unknown) {
            setResult({ success: false, detail: e instanceof Error ? e.message : 'Network error' });
        }
        setSubmitting(false);
        setShowConfirm(false);
    }

    function handleSubmitClick() {
        if (side === 'buy') {
            setShowConfirm(true);
        } else {
            submitOrder();
        }
    }

    const isDisabled = submitting || !symbol || !qty || (orderType === 'bracket' && (!stopLoss || !takeProfit));

    return (
        <div className={`trade-panel ${compact ? 'compact' : ''}`}>
            {/* Header */}
            <div className="tp-header">
                <h3>{side === 'buy' ? 'Buy' : 'Sell'} {symbol || '...'}</h3>
                {onClose && (
                    <button className="tp-close" onClick={onClose}><X size={16} /></button>
                )}
            </div>

            {/* Order Type Toggle */}
            <div className="tp-type-toggle">
                <button
                    className={`tp-type-btn ${orderType === 'simple' ? 'active' : ''}`}
                    onClick={() => setOrderType('simple')}
                >
                    <Send size={14} /> Simple
                </button>
                <button
                    className={`tp-type-btn ${orderType === 'bracket' ? 'active' : ''}`}
                    onClick={() => setOrderType('bracket')}
                >
                    <ShieldCheck size={14} /> Bracket
                </button>
            </div>

            {/* Side Selection */}
            <div className="tp-side-selector">
                <button
                    className={`tp-side-btn buy ${side === 'buy' ? 'active' : ''}`}
                    onClick={() => setSide('buy')}
                >
                    <ArrowUpCircle size={14} /> BUY
                </button>
                {!compact && (
                    <button
                        className={`tp-side-btn sell ${side === 'sell' ? 'active' : ''}`}
                        onClick={() => setSide('sell')}
                    >
                        <ArrowDownCircle size={14} /> SELL
                    </button>
                )}
            </div>

            {/* Symbol + Qty */}
            <div className="tp-form-row">
                <div className="tp-field">
                    <label>Symbol</label>
                    <input
                        type="text"
                        value={symbol}
                        onChange={e => setSymbol(e.target.value.toUpperCase())}
                        placeholder="AAPL"
                        className="tp-input mono"
                    />
                </div>
                <div className="tp-field">
                    <label>Qty</label>
                    <input
                        type="number"
                        value={qty}
                        onChange={e => setQty(e.target.value)}
                        placeholder="10"
                        min="0"
                        step="1"
                        className="tp-input mono"
                    />
                </div>
            </div>

            {/* Simple Order Options */}
            {orderType === 'simple' && (
                <div className="tp-form-row">
                    <div className="tp-field">
                        <label>Type</label>
                        <select
                            value={simpleType}
                            onChange={e => setSimpleType(e.target.value as 'market' | 'limit')}
                            className="tp-input"
                        >
                            <option value="market">Market</option>
                            <option value="limit">Limit</option>
                        </select>
                    </div>
                    {simpleType === 'limit' && (
                        <div className="tp-field">
                            <label>Limit $</label>
                            <input
                                type="number"
                                value={limitPrice}
                                onChange={e => setLimitPrice(e.target.value)}
                                placeholder="0.00"
                                step="0.01"
                                className="tp-input mono"
                            />
                        </div>
                    )}
                    <div className="tp-field">
                        <label>TIF</label>
                        <select value={tif} onChange={e => setTif(e.target.value)} className="tp-input">
                            <option value="day">Day</option>
                            <option value="gtc">GTC</option>
                            <option value="ioc">IOC</option>
                        </select>
                    </div>
                </div>
            )}

            {/* Bracket Order Options */}
            {orderType === 'bracket' && (
                <>
                    <div className="tp-form-row">
                        <div className="tp-field">
                            <label>Entry / Limit</label>
                            <input
                                type="number"
                                value={limitPrice}
                                onChange={e => setLimitPrice(e.target.value)}
                                placeholder="Market"
                                step="0.01"
                                className="tp-input mono"
                            />
                        </div>
                        <div className="tp-field">
                            <label>TIF</label>
                            <select value={tif} onChange={e => setTif(e.target.value)} className="tp-input">
                                <option value="day">Day</option>
                                <option value="gtc">GTC</option>
                            </select>
                        </div>
                    </div>
                    <div className="tp-form-row">
                        <div className="tp-field">
                            <label>Stop Loss</label>
                            <input
                                type="number"
                                value={stopLoss}
                                onChange={e => setStopLoss(e.target.value)}
                                placeholder="0.00"
                                step="0.01"
                                className="tp-input mono stop"
                            />
                        </div>
                        <div className="tp-field">
                            <label>Take Profit</label>
                            <input
                                type="number"
                                value={takeProfit}
                                onChange={e => setTakeProfit(e.target.value)}
                                placeholder="0.00"
                                step="0.01"
                                className="tp-input mono profit"
                            />
                        </div>
                    </div>
                </>
            )}

            {/* Submit */}
            <button
                className={`tp-submit ${side}`}
                onClick={handleSubmitClick}
                disabled={isDisabled}
            >
                {submitting ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
                {submitting ? 'Submitting...' : `${side.toUpperCase()} ${symbol || '...'}`}
            </button>

            {/* Result Banner */}
            {result && (
                <div className={`tp-result ${result.success ? 'success' : 'error'}`}>
                    {result.success ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                    <div>
                        <strong>{result.success ? 'Order Submitted' : 'Order Failed'}</strong>
                        <p>
                            {result.success
                                ? `${result.order?.side} ${result.order?.qty} ${result.order?.symbol} — ${result.order?.status}`
                                : result.detail}
                        </p>
                    </div>
                </div>
            )}

            {/* Buy Confirmation Modal */}
            {showConfirm && (
                <div className="tp-confirm-overlay" onClick={() => setShowConfirm(false)}>
                    <div className="tp-confirm-modal" onClick={e => e.stopPropagation()}>
                        <h4>Confirm Buy Order</h4>
                        <div className="tp-confirm-details">
                            <div className="tp-confirm-row">
                                <span>Symbol</span><span className="mono">{symbol}</span>
                            </div>
                            <div className="tp-confirm-row">
                                <span>Qty</span><span className="mono">{qty}</span>
                            </div>
                            <div className="tp-confirm-row">
                                <span>Type</span><span>{orderType === 'simple' ? simpleType.toUpperCase() : 'BRACKET'}</span>
                            </div>
                            {limitPrice && (
                                <div className="tp-confirm-row">
                                    <span>Limit</span><span className="mono">${limitPrice}</span>
                                </div>
                            )}
                            {orderType === 'bracket' && (
                                <>
                                    <div className="tp-confirm-row">
                                        <span>Stop Loss</span><span className="mono stop">${stopLoss}</span>
                                    </div>
                                    <div className="tp-confirm-row">
                                        <span>Take Profit</span><span className="mono profit">${takeProfit}</span>
                                    </div>
                                </>
                            )}
                        </div>
                        <div className="tp-confirm-actions">
                            <button className="tp-confirm-cancel" onClick={() => setShowConfirm(false)}>Cancel</button>
                            <button className="tp-confirm-submit" onClick={submitOrder} disabled={submitting}>
                                {submitting ? <Loader2 size={14} className="spin" /> : <Send size={14} />}
                                Confirm Buy
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
