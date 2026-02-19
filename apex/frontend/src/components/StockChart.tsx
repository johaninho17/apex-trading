import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, LineSeries, CandlestickSeries, HistogramSeries } from 'lightweight-charts';

interface StockChartProps {
    ticker: string;
    period?: string;
}

export default function StockChart({ ticker, period = '3mo' }: StockChartProps) {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const rsiContainerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(false);
    const [timeRange, setTimeRange] = useState(period);

    useEffect(() => {
        if (!ticker || !chartContainerRef.current || !rsiContainerRef.current) return;

        let mainChart: ReturnType<typeof createChart> | null = null;
        let rsiChart: ReturnType<typeof createChart> | null = null;

        async function loadChart() {
            setLoading(true);
            try {
                const res = await fetch(`/api/v1/alpaca/chart-data?ticker=${ticker}&period=${timeRange}&interval=1d`);
                if (!res.ok) throw new Error('Failed to fetch chart data');
                const data = await res.json();

                if (!chartContainerRef.current || !rsiContainerRef.current) return;

                // Clear previous charts
                chartContainerRef.current.innerHTML = '';
                rsiContainerRef.current.innerHTML = '';

                // Main chart
                mainChart = createChart(chartContainerRef.current, {
                    width: chartContainerRef.current.clientWidth,
                    height: 400,
                    layout: { background: { type: ColorType.Solid, color: '#0a0e17' }, textColor: '#8b95a5' },
                    grid: { vertLines: { color: '#1a1e2e' }, horzLines: { color: '#1a1e2e' } },
                    crosshair: { mode: 0 },
                    rightPriceScale: { borderColor: '#1a1e2e' },
                    timeScale: { borderColor: '#1a1e2e', timeVisible: true },
                });

                const candleSeries = mainChart.addSeries(CandlestickSeries, {
                    upColor: '#26a69a', downColor: '#ef5350',
                    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
                    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
                });
                candleSeries.setData(data.candles);

                // Volume
                const volumeSeries = mainChart.addSeries(HistogramSeries, {
                    priceFormat: { type: 'volume' },
                    priceScaleId: 'volume',
                });
                volumeSeries.priceScale().applyOptions({
                    scaleMargins: { top: 0.85, bottom: 0 },
                });
                volumeSeries.setData(data.volumes);

                // SMA 20
                if (data.sma20?.length) {
                    const sma20Series = mainChart.addSeries(LineSeries, {
                        color: '#2196F3', lineWidth: 1, title: 'SMA 20',
                    });
                    sma20Series.setData(data.sma20);
                }

                // SMA 50
                if (data.sma50?.length) {
                    const sma50Series = mainChart.addSeries(LineSeries, {
                        color: '#FF9800', lineWidth: 1, title: 'SMA 50',
                    });
                    sma50Series.setData(data.sma50);
                }

                // Bollinger Bands
                if (data.bb_upper?.length) {
                    const bbUpper = mainChart.addSeries(LineSeries, {
                        color: 'rgba(100,150,255,0.3)', lineWidth: 1, title: 'BB Upper',
                    });
                    bbUpper.setData(data.bb_upper);
                }
                if (data.bb_lower?.length) {
                    const bbLower = mainChart.addSeries(LineSeries, {
                        color: 'rgba(100,150,255,0.3)', lineWidth: 1, title: 'BB Lower',
                    });
                    bbLower.setData(data.bb_lower);
                }

                mainChart.timeScale().fitContent();

                // RSI chart
                rsiChart = createChart(rsiContainerRef.current, {
                    width: rsiContainerRef.current.clientWidth,
                    height: 120,
                    layout: { background: { type: ColorType.Solid, color: '#0a0e17' }, textColor: '#8b95a5' },
                    grid: { vertLines: { color: '#1a1e2e' }, horzLines: { color: '#1a1e2e' } },
                    rightPriceScale: { borderColor: '#1a1e2e' },
                    timeScale: { borderColor: '#1a1e2e', visible: false },
                });

                if (data.rsi?.length) {
                    const rsiSeries = rsiChart.addSeries(LineSeries, {
                        color: '#a855f7', lineWidth: 2 as const, title: 'RSI 14',
                        priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(0) },
                    });
                    rsiSeries.setData(data.rsi);

                    // Overbought/Oversold lines
                    const obLine = rsiChart.addSeries(LineSeries, {
                        color: 'rgba(239,83,80,0.4)', lineWidth: 1, lineStyle: 2,
                    });
                    obLine.setData(data.rsi.map((d: any) => ({ time: d.time, value: 70 })));
                    const osLine = rsiChart.addSeries(LineSeries, {
                        color: 'rgba(38,166,154,0.4)', lineWidth: 1, lineStyle: 2,
                    });
                    osLine.setData(data.rsi.map((d: any) => ({ time: d.time, value: 30 })));
                }

                rsiChart.timeScale().fitContent();

                // Sync time scales
                mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                    if (range && rsiChart) rsiChart.timeScale().setVisibleLogicalRange(range);
                });

                // Handle resize
                const handleResize = () => {
                    if (chartContainerRef.current && mainChart) {
                        mainChart.applyOptions({ width: chartContainerRef.current.clientWidth });
                    }
                    if (rsiContainerRef.current && rsiChart) {
                        rsiChart.applyOptions({ width: rsiContainerRef.current.clientWidth });
                    }
                };
                window.addEventListener('resize', handleResize);

            } catch (e) {
                console.error('Chart load error:', e);
            }
            setLoading(false);
        }

        loadChart();

        return () => {
            mainChart?.remove();
            rsiChart?.remove();
        };
    }, [ticker, timeRange]);

    const ranges = [
        { label: '1W', value: '5d' },
        { label: '1M', value: '1mo' },
        { label: '3M', value: '3mo' },
        { label: '6M', value: '6mo' },
        { label: '1Y', value: '1y' },
    ];

    return (
        <div className="stock-chart-wrapper">
            <div className="chart-time-selector">
                {ranges.map(r => (
                    <button
                        key={r.value}
                        className={`time-btn ${timeRange === r.value ? 'active' : ''}`}
                        onClick={() => setTimeRange(r.value)}
                    >
                        {r.label}
                    </button>
                ))}
            </div>
            {loading && <div className="chart-loading">Loading chart...</div>}
            <div ref={chartContainerRef} className="chart-main" />
            <div className="chart-rsi-label">RSI (14)</div>
            <div ref={rsiContainerRef} className="chart-rsi" />
        </div>
    );
}
