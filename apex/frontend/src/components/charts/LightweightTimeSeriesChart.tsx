import { useEffect, useMemo, useRef } from 'react';
import { ColorType, LineSeries, createChart } from 'lightweight-charts';
import type { RangeKey } from '../../lib/cryptoApi';

export interface LightweightPoint {
  ts: number;
  value: number;
}

interface LightweightTimeSeriesChartProps {
  data: LightweightPoint[];
  height?: number;
  lineColor?: string;
  emptyLabel?: string;
  valueFormatter?: (value: number) => string;
  rangeKey?: RangeKey | string;
}

function toUnixSeconds(ts: number): number {
  return Math.floor(ts / 1000);
}

export default function LightweightTimeSeriesChart({
  data,
  height = 320,
  lineColor = '#2dd4bf',
  emptyLabel = 'No data available.',
  valueFormatter,
  rangeKey = '1D',
}: LightweightTimeSeriesChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isJsdom = typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent);
  const sortedData = useMemo(
    () => {
      const normalized = [...data]
        .sort((left, right) => left.ts - right.ts)
        .filter((point) => Number.isFinite(point.ts) && Number.isFinite(point.value));
      const deduped = new Map<number, LightweightPoint>();
      for (const point of normalized) {
        deduped.set(point.ts, point);
      }
      return [...deduped.values()];
    },
    [data],
  );
  const valueRange = useMemo(() => {
    if (sortedData.length === 0) {
      return { min: 0, max: 0, span: 0, padding: 1 };
    }
    let min = sortedData[0].value;
    let max = sortedData[0].value;
    for (const point of sortedData) {
      if (point.value < min) min = point.value;
      if (point.value > max) max = point.value;
    }
    const span = max - min;
    const midpoint = (max + min) / 2 || max || min || 1;
    const padding = span > 0 ? span * 0.12 : Math.max(Math.abs(midpoint) * 0.012, 1);
    return { min, max, span, padding };
  }, [sortedData]);

  const tickFormatter = useMemo(() => {
    const key = String(rangeKey || '1D').toUpperCase();
    return (unixTime: number) => {
      const date = new Date(unixTime * 1000);
      if (key === '1H') {
        return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
      }
      if (key === '1D') {
        return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
      }
      if (key === '7D') {
        return new Intl.DateTimeFormat(undefined, { month: 'numeric', day: 'numeric', hour: 'numeric' }).format(date);
      }
      if (key === '30D' || key === '90D') {
        return new Intl.DateTimeFormat(undefined, { month: 'numeric', day: 'numeric' }).format(date);
      }
      return new Intl.DateTimeFormat(undefined, { month: 'short', year: '2-digit' }).format(date);
    };
  }, [rangeKey]);

  const timeScaleOptions = useMemo(() => {
    const key = String(rangeKey || '1D').toUpperCase();
    if (key === '1H') {
      return { secondsVisible: true, minBarSpacing: 18 };
    }
    if (key === '1D') {
      return { secondsVisible: false, minBarSpacing: 12 };
    }
    if (key === '7D') {
      return { secondsVisible: false, minBarSpacing: 8 };
    }
    return { secondsVisible: false, minBarSpacing: 6 };
  }, [rangeKey]);

  useEffect(() => {
    const browserWindow: Window | undefined = typeof window === 'undefined' ? undefined : window;
    if (!containerRef.current || sortedData.length === 0 || !browserWindow || isJsdom) {
      return undefined;
    }

    const container = containerRef.current;
    let resizeObserver: ResizeObserver | null = null;

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#101522' },
        textColor: '#9fb0cc',
      },
      grid: {
        vertLines: { color: 'rgba(159, 176, 204, 0.06)' },
        horzLines: { color: 'rgba(159, 176, 204, 0.06)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(159, 176, 204, 0.12)',
        scaleMargins: { top: 0.18, bottom: 0.14 },
        autoScale: true,
      },
      timeScale: {
        borderColor: 'rgba(159, 176, 204, 0.12)',
        timeVisible: true,
        secondsVisible: timeScaleOptions.secondsVisible,
        minBarSpacing: timeScaleOptions.minBarSpacing,
        rightOffset: 2,
        lockVisibleTimeRangeOnResize: true,
        tickMarkFormatter: tickFormatter,
      },
      crosshair: {
        vertLine: { color: 'rgba(45, 212, 191, 0.35)' },
        horzLine: { color: 'rgba(45, 212, 191, 0.2)' },
      },
      localization: valueFormatter
        ? {
            priceFormatter: valueFormatter,
          }
        : undefined,
    });

    const series = chart.addSeries(LineSeries, {
      color: lineColor,
      lineWidth: 2,
      crosshairMarkerVisible: true,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    series.setData(
      sortedData.map((point) => ({
        time: toUnixSeconds(point.ts) as never,
        value: point.value,
      })),
    );
    series.applyOptions({
      autoscaleInfoProvider: () => ({
        priceRange: {
          minValue: valueRange.min - valueRange.padding,
          maxValue: valueRange.max + valueRange.padding,
        },
      }),
    });
    chart.timeScale().fitContent();

    const ResizeObserverCtor = typeof ResizeObserver === 'undefined' ? undefined : ResizeObserver;

    if (ResizeObserverCtor) {
      resizeObserver = new ResizeObserverCtor(() => {
        chart.applyOptions({ width: container.clientWidth, height });
      });
      if (resizeObserver) {
        resizeObserver.observe(container);
      }
    } else {
      const handleResize = () => chart.applyOptions({ width: container.clientWidth, height });
      browserWindow.addEventListener('resize', handleResize);
      return () => {
        browserWindow.removeEventListener('resize', handleResize);
        resizeObserver?.disconnect();
        chart.remove();
      };
    }

    return () => {
      resizeObserver?.disconnect();
      chart.remove();
    };
  }, [
    height,
    isJsdom,
    lineColor,
    rangeKey,
    sortedData,
    tickFormatter,
    timeScaleOptions.minBarSpacing,
    timeScaleOptions.secondsVisible,
    valueFormatter,
    valueRange.max,
    valueRange.min,
    valueRange.padding,
  ]);

  if (sortedData.length === 0) {
    return (
      <div className="ts-empty" style={{ minHeight: height }}>
        <p>{emptyLabel}</p>
      </div>
    );
  }

  if (isJsdom) {
    return <div data-testid="lightweight-chart" style={{ width: '100%', height }} />;
  }

  return <div ref={containerRef} style={{ width: '100%', height }} />;
}
