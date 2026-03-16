interface FeatureParkedProps {
  title: string;
  detail: string;
}

export default function FeatureParked({ title, detail }: FeatureParkedProps) {
  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="ts-panel" style={{ maxWidth: 720 }}>
        <div className="ts-panel-header">
          <div className="ts-panel-title">{title}</div>
        </div>
        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6, padding: '8px 4px 4px 4px' }}>
          {detail}
        </div>
      </div>
    </div>
  );
}
