import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import {
    customProfileStorageKey,
    guardrailReason,
    normalizeCalcProfile,
    profileFieldLayout,
    profileFromPreset,
    saveProfileToSettings,
} from '../lib/calcProfiles';
import type { CalcDomain, CalcPreset } from '../lib/calcProfiles';
import './CalcProfilePopover.css';

interface CalcProfilePopoverProps {
    open: boolean;
    title?: string;
    domain: CalcDomain;
    preset: CalcPreset;
    profile: Record<string, any>;
    onPresetChange: (p: CalcPreset) => void;
    onProfileChange: (next: Record<string, any>) => void;
    onClose: () => void;
}

export default function CalcProfilePopover({
    open,
    title = 'Calculation Profile',
    domain,
    preset,
    profile,
    onPresetChange,
    onProfileChange,
    onClose,
}: CalcProfilePopoverProps) {
    const fields = profileFieldLayout(domain);
    const toggleFields = fields.filter((f) => f.kind === 'toggle');
    const numberFields = fields.filter((f) => f.kind === 'number');
    const [draftValues, setDraftValues] = useState<Record<string, string>>({});

    useEffect(() => {
        const next: Record<string, string> = {};
        for (const { key } of numberFields) {
            next[key] = String(Number((profile as any)[key]));
        }
        setDraftValues(next);
    }, [profile, domain]);

    function update(key: string, value: unknown) {
        onProfileChange({ ...profile, [key]: value } as any);
    }

    function applyPreset(nextPreset: CalcPreset) {
        onPresetChange(nextPreset);
        onProfileChange(profileFromPreset(domain as any, nextPreset, profile as any) as any);
    }

    async function saveCustom() {
        sessionStorage.setItem(customProfileStorageKey(domain), JSON.stringify(profile));
        await saveProfileToSettings(domain as any, profile as any);
    }

    function loadCustom() {
        try {
            const raw = sessionStorage.getItem(customProfileStorageKey(domain));
            if (!raw) return;
            onProfileChange(normalizeCalcProfile(domain as any, JSON.parse(raw)) as any);
        } catch {
            // ignore malformed custom profile
        }
    }

    if (!open) return null;

    return (
        <div className="calcp-backdrop" onClick={onClose}>
            <div className="calcp-popover" onClick={(e) => e.stopPropagation()}>
                <div className="calcp-head">
                    <span>{title}</span>
                    <button className="calcp-close-btn" onClick={onClose}>
                        <X size={13} />
                    </button>
                </div>

                <div className="calcp-preset-row">
                    <button className={`calcp-preset-btn ${preset === 'safe' ? 'active' : ''}`} onClick={() => applyPreset('safe')}>Safe</button>
                    <button className={`calcp-preset-btn ${preset === 'balanced' ? 'active' : ''}`} onClick={() => applyPreset('balanced')}>Balanced</button>
                    <button className={`calcp-preset-btn ${preset === 'aggressive' ? 'active' : ''}`} onClick={() => applyPreset('aggressive')}>Aggressive</button>
                    <button className="calcp-preset-btn" onClick={saveCustom}>Save Custom</button>
                    <button className="calcp-preset-btn" onClick={loadCustom}>Load Custom</button>
                </div>

                <div className="calcp-body">
                    <div className="calcp-toggle-grid">
                        {toggleFields.map(({ key, label }) => {
                            const on = !!(profile as any)[key];
                            return (
                                <button
                                    key={key}
                                    type="button"
                                    className={`calcp-toggle-chip ${on ? 'on' : 'off'}`}
                                    onClick={() => update(key, !on)}
                                    aria-pressed={on}
                                >
                                    <span className="calcp-toggle-indicator">{on ? '✓' : '○'}</span>
                                    <span>{label}</span>
                                </button>
                            );
                        })}
                    </div>

                    <div className="calcp-number-grid">
                        {numberFields.map(({ key, label, step }) => {
                            const reason = guardrailReason(domain, key, Number((profile as any)[key]));
                            return (
                                <label key={key} className="calcp-number-wrap">
                                    <span className="calcp-number-label">{label}</span>
                                    <span className={`calcp-number-field ${reason ? 'invalid' : ''}`} data-reason={reason || ''}>
                                        <input
                                            type="number"
                                            step={step || 0.1}
                                            value={draftValues[key] ?? String(Number((profile as any)[key]))}
                                            onChange={(e) => {
                                                const raw = e.target.value;
                                                setDraftValues(prev => ({ ...prev, [key]: raw }));
                                                if (raw === '') return;
                                                const next = Number(raw);
                                                if (!Number.isFinite(next)) return;
                                                update(key, next);
                                            }}
                                            onBlur={() => {
                                                if ((draftValues[key] || '').trim() !== '') return;
                                                setDraftValues(prev => ({ ...prev, [key]: String(Number((profile as any)[key])) }));
                                            }}
                                        />
                                    </span>
                                </label>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
