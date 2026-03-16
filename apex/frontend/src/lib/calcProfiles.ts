export type CalcDomain = 'dfs' | 'stocks' | 'events';
export type CalcPreset = 'safe' | 'balanced' | 'aggressive';

export interface DfsCalcProfile {
    edgeWeight: number;
    confidenceWeight: number;
    stakeWeight: number;
    kellyCapPct: number;
    useDevig: boolean;
    useConfidenceShrink: boolean;
    useVigPenalty: boolean;
    useTrendBonus: boolean;
    useKellyCap: boolean;
    useCorrelationPenalty: boolean;
}

export interface StocksCalcProfile {
    atrWeight: number;
    rsiWeight: number;
    emaWeight: number;
    crossoverWeight: number;
    volatilityPenalty: number;
    liquidityWeight: number;
    trendStrengthBonus: number;
    scoreSmoothing: number;
    useRsiFilter: boolean;
    useAtrTrendGate: boolean;
    useCrossoverBoost: boolean;
    useLiquidityFilter: boolean;
}

export interface EventsCalcProfile {
    spreadWeight: number;
    liquidityWeight: number;
    depthWeight: number;
    momentumWeight: number;
    confidenceWeight: number;
    volatilityPenalty: number;
    executionRiskPenalty: number;
    scalpSensitivity: number;
    useDepthBoost: boolean;
    useVolatilityPenalty: boolean;
    useExecutionRisk: boolean;
    useMomentumBoost: boolean;
    useConfidenceScaling: boolean;
}

export type CalcProfileByDomain = {
    dfs: DfsCalcProfile;
    stocks: StocksCalcProfile;
    events: EventsCalcProfile;
};

export type DomainProfile<D extends CalcDomain> = CalcProfileByDomain[D];

export interface ProfileField {
    key: string;
    label: string;
    kind: 'number' | 'toggle';
    step?: number;
}

export function defaultCalcProfile(domain: 'dfs'): DfsCalcProfile;
export function defaultCalcProfile(domain: 'stocks'): StocksCalcProfile;
export function defaultCalcProfile(domain: 'events'): EventsCalcProfile;
export function defaultCalcProfile(domain: CalcDomain): DomainProfile<CalcDomain> {
    if (domain === 'stocks') {
        return {
            atrWeight: 1.2,
            rsiWeight: 0.9,
            emaWeight: 1.1,
            crossoverWeight: 1.15,
            volatilityPenalty: 0.8,
            liquidityWeight: 0.7,
            trendStrengthBonus: 6,
            scoreSmoothing: 0.6,
            useRsiFilter: true,
            useAtrTrendGate: true,
            useCrossoverBoost: true,
            useLiquidityFilter: true,
        };
    }
    if (domain === 'events') {
        return {
            spreadWeight: 1.5,
            liquidityWeight: 1.2,
            depthWeight: 1.0,
            momentumWeight: 1.1,
            confidenceWeight: 1.0,
            volatilityPenalty: 0.8,
            executionRiskPenalty: 0.9,
            scalpSensitivity: 1.0,
            useDepthBoost: true,
            useVolatilityPenalty: true,
            useExecutionRisk: true,
            useMomentumBoost: true,
            useConfidenceScaling: true,
        };
    }
    return {
        edgeWeight: 1.8,
        confidenceWeight: 1.2,
        stakeWeight: 1.0,
        kellyCapPct: 25,
        useDevig: true,
        useConfidenceShrink: true,
        useVigPenalty: true,
        useTrendBonus: true,
        useKellyCap: true,
        useCorrelationPenalty: true,
    };
}

function castObj(raw: unknown): Record<string, unknown> {
    return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
}

export function normalizeCalcProfile(domain: 'dfs', raw: unknown): DfsCalcProfile;
export function normalizeCalcProfile(domain: 'stocks', raw: unknown): StocksCalcProfile;
export function normalizeCalcProfile(domain: 'events', raw: unknown): EventsCalcProfile;
export function normalizeCalcProfile(domain: CalcDomain, raw: unknown): DomainProfile<CalcDomain> {
    const p = castObj(raw);
    if (domain === 'stocks') {
        const base = defaultCalcProfile('stocks');
        return {
            atrWeight: Number.isFinite(Number(p.atrWeight)) ? Number(p.atrWeight) : base.atrWeight,
            rsiWeight: Number.isFinite(Number(p.rsiWeight)) ? Number(p.rsiWeight) : base.rsiWeight,
            emaWeight: Number.isFinite(Number(p.emaWeight)) ? Number(p.emaWeight) : base.emaWeight,
            crossoverWeight: Number.isFinite(Number(p.crossoverWeight)) ? Number(p.crossoverWeight) : base.crossoverWeight,
            volatilityPenalty: Number.isFinite(Number(p.volatilityPenalty)) ? Number(p.volatilityPenalty) : base.volatilityPenalty,
            liquidityWeight: Number.isFinite(Number(p.liquidityWeight)) ? Number(p.liquidityWeight) : base.liquidityWeight,
            trendStrengthBonus: Number.isFinite(Number(p.trendStrengthBonus)) ? Number(p.trendStrengthBonus) : base.trendStrengthBonus,
            scoreSmoothing: Number.isFinite(Number(p.scoreSmoothing)) ? Number(p.scoreSmoothing) : base.scoreSmoothing,
            useRsiFilter: p.useRsiFilter !== undefined ? !!p.useRsiFilter : base.useRsiFilter,
            useAtrTrendGate: p.useAtrTrendGate !== undefined ? !!p.useAtrTrendGate : base.useAtrTrendGate,
            useCrossoverBoost: p.useCrossoverBoost !== undefined ? !!p.useCrossoverBoost : base.useCrossoverBoost,
            useLiquidityFilter: p.useLiquidityFilter !== undefined ? !!p.useLiquidityFilter : base.useLiquidityFilter,
        };
    }
    if (domain === 'events') {
        const base = defaultCalcProfile('events');
        return {
            spreadWeight: Number.isFinite(Number(p.spreadWeight)) ? Number(p.spreadWeight) : base.spreadWeight,
            liquidityWeight: Number.isFinite(Number(p.liquidityWeight)) ? Number(p.liquidityWeight) : base.liquidityWeight,
            depthWeight: Number.isFinite(Number(p.depthWeight)) ? Number(p.depthWeight) : base.depthWeight,
            momentumWeight: Number.isFinite(Number(p.momentumWeight)) ? Number(p.momentumWeight) : base.momentumWeight,
            confidenceWeight: Number.isFinite(Number(p.confidenceWeight)) ? Number(p.confidenceWeight) : base.confidenceWeight,
            volatilityPenalty: Number.isFinite(Number(p.volatilityPenalty)) ? Number(p.volatilityPenalty) : base.volatilityPenalty,
            executionRiskPenalty: Number.isFinite(Number(p.executionRiskPenalty)) ? Number(p.executionRiskPenalty) : base.executionRiskPenalty,
            scalpSensitivity: Number.isFinite(Number(p.scalpSensitivity)) ? Number(p.scalpSensitivity) : base.scalpSensitivity,
            useDepthBoost: p.useDepthBoost !== undefined ? !!p.useDepthBoost : base.useDepthBoost,
            useVolatilityPenalty: p.useVolatilityPenalty !== undefined ? !!p.useVolatilityPenalty : base.useVolatilityPenalty,
            useExecutionRisk: p.useExecutionRisk !== undefined ? !!p.useExecutionRisk : base.useExecutionRisk,
            useMomentumBoost: p.useMomentumBoost !== undefined ? !!p.useMomentumBoost : base.useMomentumBoost,
            useConfidenceScaling: p.useConfidenceScaling !== undefined ? !!p.useConfidenceScaling : base.useConfidenceScaling,
        };
    }
    const base = defaultCalcProfile('dfs');
    return {
        edgeWeight: Number.isFinite(Number(p.edgeWeight)) ? Number(p.edgeWeight) : base.edgeWeight,
        confidenceWeight: Number.isFinite(Number(p.confidenceWeight)) ? Number(p.confidenceWeight) : base.confidenceWeight,
        stakeWeight: Number.isFinite(Number(p.stakeWeight)) ? Number(p.stakeWeight) : base.stakeWeight,
        kellyCapPct: Number.isFinite(Number(p.kellyCapPct)) ? Number(p.kellyCapPct) : base.kellyCapPct,
        useDevig: p.useDevig !== undefined ? !!p.useDevig : base.useDevig,
        useConfidenceShrink: p.useConfidenceShrink !== undefined ? !!p.useConfidenceShrink : base.useConfidenceShrink,
        useVigPenalty: p.useVigPenalty !== undefined ? !!p.useVigPenalty : base.useVigPenalty,
        useTrendBonus: p.useTrendBonus !== undefined ? !!p.useTrendBonus : base.useTrendBonus,
        useKellyCap: p.useKellyCap !== undefined ? !!p.useKellyCap : base.useKellyCap,
        useCorrelationPenalty: p.useCorrelationPenalty !== undefined ? !!p.useCorrelationPenalty : base.useCorrelationPenalty,
    };
}

function profileStorageKey(domain: CalcDomain): string {
    return `apex_calc_profile_${domain}`;
}

export function loadCalcProfile(domain: 'dfs'): DfsCalcProfile;
export function loadCalcProfile(domain: 'stocks'): StocksCalcProfile;
export function loadCalcProfile(domain: 'events'): EventsCalcProfile;
export function loadCalcProfile(domain: CalcDomain): DomainProfile<CalcDomain> {
    try {
        const raw = sessionStorage.getItem(profileStorageKey(domain));
        if (!raw) return defaultCalcProfile(domain as any);
        return normalizeCalcProfile(domain as any, JSON.parse(raw));
    } catch {
        return defaultCalcProfile(domain as any);
    }
}

export function saveCalcProfile(domain: CalcDomain, profile: DomainProfile<CalcDomain>): void {
    sessionStorage.setItem(profileStorageKey(domain), JSON.stringify(profile));
}

export function customProfileStorageKey(domain: CalcDomain): string {
    return `apex_calc_profile_custom_${domain}`;
}

export function profileFieldLayout(domain: CalcDomain): ProfileField[] {
    if (domain === 'stocks') {
        return [
            { key: 'atrWeight', label: 'ATR W', kind: 'number', step: 0.1 },
            { key: 'rsiWeight', label: 'RSI W', kind: 'number', step: 0.1 },
            { key: 'emaWeight', label: 'EMA W', kind: 'number', step: 0.1 },
            { key: 'crossoverWeight', label: 'Cross W', kind: 'number', step: 0.1 },
            { key: 'volatilityPenalty', label: 'Vol Pen', kind: 'number', step: 0.1 },
            { key: 'liquidityWeight', label: 'Liq W', kind: 'number', step: 0.1 },
            { key: 'trendStrengthBonus', label: 'Trend +', kind: 'number', step: 0.5 },
            { key: 'scoreSmoothing', label: 'Smooth', kind: 'number', step: 0.1 },
            { key: 'useRsiFilter', label: 'RSI Filter', kind: 'toggle' },
            { key: 'useAtrTrendGate', label: 'ATR Gate', kind: 'toggle' },
            { key: 'useCrossoverBoost', label: 'Cross Boost', kind: 'toggle' },
            { key: 'useLiquidityFilter', label: 'Liq Filter', kind: 'toggle' },
        ];
    }
    if (domain === 'events') {
        return [
            { key: 'spreadWeight', label: 'Spread W', kind: 'number', step: 0.1 },
            { key: 'liquidityWeight', label: 'Liq W', kind: 'number', step: 0.1 },
            { key: 'depthWeight', label: 'Depth W', kind: 'number', step: 0.1 },
            { key: 'momentumWeight', label: 'Moment W', kind: 'number', step: 0.1 },
            { key: 'confidenceWeight', label: 'Conf W', kind: 'number', step: 0.1 },
            { key: 'volatilityPenalty', label: 'Vol Pen', kind: 'number', step: 0.1 },
            { key: 'executionRiskPenalty', label: 'Exec Pen', kind: 'number', step: 0.1 },
            { key: 'scalpSensitivity', label: 'Scalp Sens', kind: 'number', step: 0.1 },
            { key: 'useDepthBoost', label: 'Depth Boost', kind: 'toggle' },
            { key: 'useVolatilityPenalty', label: 'Vol Pen On', kind: 'toggle' },
            { key: 'useExecutionRisk', label: 'Exec Risk', kind: 'toggle' },
            { key: 'useMomentumBoost', label: 'Momentum', kind: 'toggle' },
            { key: 'useConfidenceScaling', label: 'Conf Scale', kind: 'toggle' },
        ];
    }
    return [
        { key: 'edgeWeight', label: 'Edge W', kind: 'number', step: 0.1 },
        { key: 'confidenceWeight', label: 'Conf W', kind: 'number', step: 0.1 },
        { key: 'stakeWeight', label: 'Stake W', kind: 'number', step: 0.1 },
        { key: 'kellyCapPct', label: 'Kelly %', kind: 'number', step: 0.1 },
        { key: 'useDevig', label: 'De-vig', kind: 'toggle' },
        { key: 'useConfidenceShrink', label: 'Conf Shrink', kind: 'toggle' },
        { key: 'useVigPenalty', label: 'Vig Penalty', kind: 'toggle' },
        { key: 'useTrendBonus', label: 'Trend Bonus', kind: 'toggle' },
        { key: 'useKellyCap', label: 'Kelly Cap', kind: 'toggle' },
        { key: 'useCorrelationPenalty', label: 'Corr Penalty', kind: 'toggle' },
    ];
}

export function guardrailReason(domain: CalcDomain, field: string, value: number): string | null {
    if (!Number.isFinite(value)) return 'Value must be numeric.';
    if (domain === 'stocks') {
        if (field === 'scoreSmoothing' && (value < 0 || value > 1)) return 'Smoothing should be between 0 and 1.';
        if ((field === 'atrWeight' || field === 'rsiWeight' || field === 'emaWeight' || field === 'crossoverWeight' || field === 'liquidityWeight') && (value < 0 || value > 5)) return 'Recommended range is 0 to 5.';
        if ((field === 'volatilityPenalty' || field === 'trendStrengthBonus') && (value < 0 || value > 20)) return 'Recommended range is 0 to 20.';
        return null;
    }
    if (domain === 'events') {
        if ((field === 'spreadWeight' || field === 'liquidityWeight' || field === 'depthWeight' || field === 'momentumWeight' || field === 'confidenceWeight') && (value < 0 || value > 5)) return 'Recommended range is 0 to 5.';
        if ((field === 'volatilityPenalty' || field === 'executionRiskPenalty' || field === 'scalpSensitivity') && (value < 0 || value > 5)) return 'Recommended range is 0 to 5.';
        return null;
    }
    if ((field === 'edgeWeight' || field === 'confidenceWeight' || field === 'stakeWeight') && (value < 0 || value > 8)) return 'Recommended range is 0 to 8.';
    if (field === 'kellyCapPct' && (value <= 0 || value > 100)) return 'Kelly cap should be >0 and <=100% bankroll.';
    return null;
}

export function profileFromPreset(domain: 'dfs', preset: CalcPreset, current: DfsCalcProfile): DfsCalcProfile;
export function profileFromPreset(domain: 'stocks', preset: CalcPreset, current: StocksCalcProfile): StocksCalcProfile;
export function profileFromPreset(domain: 'events', preset: CalcPreset, current: EventsCalcProfile): EventsCalcProfile;
export function profileFromPreset(domain: CalcDomain, preset: CalcPreset, current: DomainProfile<CalcDomain>): DomainProfile<CalcDomain> {
    if (domain === 'stocks') {
        if (preset === 'safe') {
            return { ...(current as StocksCalcProfile), atrWeight: 1.0, rsiWeight: 1.2, emaWeight: 0.9, crossoverWeight: 0.9, volatilityPenalty: 1.3, liquidityWeight: 1.0, trendStrengthBonus: 4, scoreSmoothing: 0.75, useRsiFilter: true, useAtrTrendGate: true, useCrossoverBoost: false, useLiquidityFilter: true };
        }
        if (preset === 'aggressive') {
            return { ...(current as StocksCalcProfile), atrWeight: 1.4, rsiWeight: 0.6, emaWeight: 1.35, crossoverWeight: 1.4, volatilityPenalty: 0.45, liquidityWeight: 0.5, trendStrengthBonus: 9, scoreSmoothing: 0.45, useRsiFilter: false, useAtrTrendGate: false, useCrossoverBoost: true, useLiquidityFilter: false };
        }
        return { ...(current as StocksCalcProfile), ...defaultCalcProfile('stocks') };
    }
    if (domain === 'events') {
        if (preset === 'safe') {
            return { ...(current as EventsCalcProfile), spreadWeight: 1.2, liquidityWeight: 1.35, depthWeight: 1.25, momentumWeight: 0.9, confidenceWeight: 1.15, volatilityPenalty: 1.2, executionRiskPenalty: 1.25, scalpSensitivity: 0.85, useDepthBoost: true, useVolatilityPenalty: true, useExecutionRisk: true, useMomentumBoost: false, useConfidenceScaling: true };
        }
        if (preset === 'aggressive') {
            return { ...(current as EventsCalcProfile), spreadWeight: 1.9, liquidityWeight: 0.9, depthWeight: 0.8, momentumWeight: 1.55, confidenceWeight: 0.85, volatilityPenalty: 0.45, executionRiskPenalty: 0.55, scalpSensitivity: 1.35, useDepthBoost: false, useVolatilityPenalty: false, useExecutionRisk: false, useMomentumBoost: true, useConfidenceScaling: false };
        }
        return { ...(current as EventsCalcProfile), ...defaultCalcProfile('events') };
    }
    if (preset === 'safe') {
        return { ...(current as DfsCalcProfile), edgeWeight: 1.5, confidenceWeight: 1.6, stakeWeight: 0.8, useDevig: true, useConfidenceShrink: true, useVigPenalty: true, useTrendBonus: false, useKellyCap: true, useCorrelationPenalty: true };
    }
    if (preset === 'aggressive') {
        return { ...(current as DfsCalcProfile), edgeWeight: 2.4, confidenceWeight: 0.8, stakeWeight: 1.2, useDevig: false, useConfidenceShrink: false, useVigPenalty: false, useTrendBonus: true, useKellyCap: false, useCorrelationPenalty: false };
    }
    return { ...(current as DfsCalcProfile), ...defaultCalcProfile('dfs') };
}

export async function loadProfileFromSettings(domain: 'dfs'): Promise<DfsCalcProfile | null>;
export async function loadProfileFromSettings(domain: 'stocks'): Promise<StocksCalcProfile | null>;
export async function loadProfileFromSettings(domain: 'events'): Promise<EventsCalcProfile | null>;
export async function loadProfileFromSettings(domain: CalcDomain): Promise<DomainProfile<CalcDomain> | null> {
    try {
        const res = await fetch('/api/v1/settings');
        if (!res.ok) return null;
        const data = await res.json();
        const config = data?.config;
        const source = domain === 'dfs' ? config?.dfs?.calc_profile : domain === 'stocks' ? config?.stocks?.calc_profile : config?.events?.calc_profile;
        if (!source) return null;
        return normalizeCalcProfile(domain as any, source);
    } catch {
        return null;
    }
}

export async function saveProfileToSettings(domain: CalcDomain, profile: DomainProfile<CalcDomain>): Promise<boolean> {
    try {
        const updates = domain === 'dfs'
            ? { dfs: { calc_profile: profile } }
            : domain === 'stocks'
                ? { stocks: { calc_profile: profile } }
                : { events: { calc_profile: profile } };
        const res = await fetch('/api/v1/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates }),
        });
        return res.ok;
    } catch {
        return false;
    }
}
