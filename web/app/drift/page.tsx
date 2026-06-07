'use client';

import { useEffect, useState } from 'react';
import SeverityBadge, { Severity } from '@/components/SeverityBadge';
import DriftScore from '@/components/DriftScore';

interface DriftEvent {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  type: string;
  element?: string;
  file?: string;
  suggested_fix?: string;
}

interface DriftReport {
  drift_score: number;
  events: DriftEvent[];
  has_blocking_issues: boolean;
  generated_at: string;
}

const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

export default function DriftPage() {
  const [report, setReport] = useState<DriftReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<Severity | 'all'>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => { loadReport(); }, []);

  async function loadReport() {
    setLoading(true);
    try {
      const r = await fetch('/api/drift/report/default');
      if (r.ok) setReport(await r.json());
    } catch { /* ignore */ }
    setLoading(false);
  }

  async function runDriftCheck() {
    setRunning(true);
    try {
      const r = await fetch('/api/drift/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo_path: '.' }) });
      if (r.ok) setReport(await r.json());
    } catch { /* ignore */ }
    setRunning(false);
  }

  const events = (report?.events ?? []).filter(e => filter === 'all' || e.severity === filter);
  const toggle = (id: string) => setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--foreground)' }}>Drift Reports</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--muted-foreground)' }}>Architectural drift detected against your documented state.</p>
        </div>
        <button onClick={runDriftCheck} disabled={running} className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity hover:opacity-80 disabled:opacity-50" style={{ backgroundColor: '#6366f1', color: '#fff' }}>
          {running ? 'Analyzing…' : 'Run Drift Check'}
        </button>
      </div>

      {loading ? (
        <div className="text-center py-16" style={{ color: 'var(--muted-foreground)' }}>Loading…</div>
      ) : !report ? (
        <div className="text-center py-16" style={{ color: 'var(--muted-foreground)' }}>No drift report. Run a drift check to start.</div>
      ) : (
        <>
          <div className="flex gap-4 items-center">
            <DriftScore score={report.drift_score} size="sm" />
            <div>
              <p className="text-sm font-medium" style={{ color: 'var(--foreground)' }}>{report.events.length} issues</p>
              {report.has_blocking_issues && <p className="text-xs text-red-400">🚨 Blocking issues require attention</p>}
            </div>
          </div>

          {/* Filter */}
          <div className="flex gap-2 flex-wrap">
            {(['all', ...SEVERITIES] as const).map(s => (
              <button key={s} onClick={() => setFilter(s)} className="px-3 py-1 rounded-full text-xs font-medium border transition-colors" style={{ borderColor: filter === s ? '#6366f1' : 'var(--border)', backgroundColor: filter === s ? 'rgba(99,102,241,0.15)' : 'transparent', color: filter === s ? '#818cf8' : 'var(--muted-foreground)' }}>
                {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>

          {/* Events */}
          <div className="space-y-2">
            {events.length === 0 ? (
              <div className="text-center py-8" style={{ color: 'var(--muted-foreground)' }}>No events for this filter.</div>
            ) : events.map(event => (
              <div key={event.id} className="rounded-xl border overflow-hidden" style={{ backgroundColor: 'var(--card)', borderColor: 'var(--border)' }}>
                <button onClick={() => toggle(event.id)} className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/5 transition-colors">
                  <SeverityBadge severity={event.severity} />
                  <span className="flex-1 text-sm font-medium" style={{ color: 'var(--foreground)' }}>{event.title}</span>
                  <span className="text-xs" style={{ color: 'var(--muted-foreground)' }}>{expanded.has(event.id) ? '▲' : '▼'}</span>
                </button>
                {expanded.has(event.id) && (
                  <div className="px-4 pb-4 space-y-2 text-sm border-t" style={{ borderColor: 'var(--border)', color: 'var(--muted-foreground)' }}>
                    <p className="pt-3">{event.description}</p>
                    {event.element && <p><span className="text-xs font-mono bg-gray-800 px-1.5 py-0.5 rounded">{event.element}</span></p>}
                    {event.file && <p className="text-xs font-mono">{event.file}</p>}
                    {event.suggested_fix && <p className="text-green-400 text-xs">💡 {event.suggested_fix}</p>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
