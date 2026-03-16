import { useMemo } from 'react';
import './pages.css';

interface OrderbookProps {
    bids: Array<[number, number]>; // [price, size]
    asks: Array<[number, number]>;
    midPrice?: number;
    symbol?: string;
    priceFormat?: 'cents' | 'probability';
}

export default function OrderbookVisualizer({ bids, asks, midPrice, symbol, priceFormat = 'cents' }: OrderbookProps) {
    const maxSize = useMemo(() => {
        const allSizes = [...bids.map(b => b[1]), ...asks.map(a => a[1])];
        return Math.max(...allSizes, 1);
    }, [bids, asks]);

    const formatPrice = (p: number) => {
        if (priceFormat === 'probability') return `${(p * 100).toFixed(1)}%`;
        return `${p}¢`;
    };

    const spread = asks.length > 0 && bids.length > 0
        ? asks[0][0] - bids[0][0]
        : 0;

    const displayMid = midPrice ?? (bids.length > 0 && asks.length > 0
        ? (bids[0][0] + asks[0][0]) / 2
        : 0);

    return (
        <div className="orderbook-viz">
            {symbol && <div className="orderbook-symbol mono">{symbol}</div>}

            {/* Asks (reversed so lowest ask is at bottom) */}
            <div className="orderbook-asks">
                {[...asks].reverse().slice(0, 8).map(([price, size], i) => (
                    <div key={`ask-${i}`} className="orderbook-row ask-row">
                        <div className="ob-bar-bg">
                            <div className="ob-bar ask-bar" style={{ width: `${(size / maxSize) * 100}%` }} />
                        </div>
                        <span className="ob-price mono text-red">{formatPrice(price)}</span>
                        <span className="ob-size mono">{size.toLocaleString()}</span>
                    </div>
                ))}
            </div>

            {/* Mid / Spread */}
            <div className="orderbook-mid">
                <span className="ob-mid-price mono">{formatPrice(displayMid)}</span>
                <span className="ob-spread text-muted">Spread: {formatPrice(spread)}</span>
            </div>

            {/* Bids */}
            <div className="orderbook-bids">
                {bids.slice(0, 8).map(([price, size], i) => (
                    <div key={`bid-${i}`} className="orderbook-row bid-row">
                        <div className="ob-bar-bg">
                            <div className="ob-bar bid-bar" style={{ width: `${(size / maxSize) * 100}%` }} />
                        </div>
                        <span className="ob-price mono text-green">{formatPrice(price)}</span>
                        <span className="ob-size mono">{size.toLocaleString()}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}
