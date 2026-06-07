export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

interface SeverityBadgeProps {
  severity: Severity;
}

const severityConfig: Record<
  Severity,
  { label: string; color: string; bg: string; dot: string }
> = {
  critical: {
    label: 'Critical',
    color: '#ef4444',
    bg: 'rgba(239,68,68,0.12)',
    dot: '#ef4444',
  },
  high: {
    label: 'High',
    color: '#f97316',
    bg: 'rgba(249,115,22,0.12)',
    dot: '#f97316',
  },
  medium: {
    label: 'Medium',
    color: '#eab308',
    bg: 'rgba(234,179,8,0.12)',
    dot: '#eab308',
  },
  low: {
    label: 'Low',
    color: '#60a5fa',
    bg: 'rgba(96,165,250,0.12)',
    dot: '#60a5fa',
  },
  info: {
    label: 'Info',
    color: '#94a3b8',
    bg: 'rgba(148,163,184,0.12)',
    dot: '#94a3b8',
  },
};

export default function SeverityBadge({ severity }: SeverityBadgeProps) {
  const config = severityConfig[severity] ?? severityConfig.info;

  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ color: config.color, backgroundColor: config.bg }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ backgroundColor: config.dot }}
      />
      {config.label}
    </span>
  );
}
