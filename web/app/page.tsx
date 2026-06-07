'use client';

import { useEffect, useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { Boxes, Zap, ShieldCheck, Activity, AlertCircle } from 'lucide-react';
import DriftScore from '@/components/DriftScore';
import SeverityBadge, { Severity } from '@/components/SeverityBadge';

interface AnalysisState {
  components?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
}

interface DriftEvent {
  id: string;
  severity: Severity;
  message: string;
  component?: string;
  timestamp?: string;
}

interface DriftReport {
  drift_score?: number;
  events?: DriftEvent[];
}

const MOCK_CHART_DATA = [
  { day: 'Mon', score: 12 },
  { day: 'Tue', score: 18 },
  { day: 'Wed', score: 24 },
  { day: 'Thu', score: 22 },
  { day: 'Fri', score: 35 },
  { day: 'Sat', score: 28 },
  { day: 'Sun', score: 31 },
];

function StatCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  color: string;
}) {
  return (
    <div
      className="rounded-xl p-5 border flex items-center gap-4"
      style={{
        backgroundColor: 'var(--card)',
        borderColor: 'var(--border)',
      }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ backgroundColor: `${color}20` }}
      >
        <Icon size={20} style={{ color }} />
      </div>
      <div>
        <p className="text-xs font-medium" style={{ color: 'var(--muted-foreground)' }}>
          {label}
        </p>
        <p className="text-2xl font-bold mt-0.5" style={{ color: 'var(--foreground)' }}>
          {value}
        </p>
      </div>
    </div>
  );
}

function SetupPrompt() {
  return (
    <div
      className="rounded-xl p-6 border text-center"
      style={{
        backgroundColor: 'var(--card)',
        borderColor: 'var(--border)',
      }}
    >
      <div className="flex justify-center mb-3">
        <div
          className="w-12 h-12 rounded-full flex items-center justify-center"
          style={{ backgroundColor: 'rgba(99,102,241,0.15)' }}
        >
          <AlertCircle size={24} color="#6366f1" />
        </div>
      </div>
      <h3 className="text-base font-semibold mb-1" style={{ color: 'var(--foreground)' }}>
        Not initialized
      </h3>
      <p className="text-sm mb-4" style={{ color: 'var(--muted-foreground)' }}>
        No architecture state found. Run the analyzer to get started.
      </p>
      <div
        className="rounded-lg p-4 text-left font-mono text-xs"
        style={{ backgroundColor: 'rgba(0,0,0,0.3)', color: '#a5b4fc' }}
      >
        <p className="text-gray-500 mb-1"># Initialize GyroCompass</p>
        <p>gyrocompass init --project ./</p>
        <p className="mt-1">gyrocompass analyze --snapshot default</p>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [state, setState] = useState<AnalysisState | null>(null);
  const [drift, setDrift] = useState<DriftReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    async function fetchData() {
      try {
        const [stateRes, driftRes] = await Promise.all([
          fetch('/api/analysis/state/default'),
          fetch('/api/drift/report/default'),
        ]);

        if (stateRes.ok) {
          const data = await stateRes.json();
          setState(data);
        }
        if (driftRes.ok) {
          const data = await driftRes.json();
          setDrift(data);
        }
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, []);

  const componentCount = state?.components
    ? Object.keys(state.components).length
    : 0;
  const capabilityCount = state?.capabilities
    ? Object.keys(state.capabilities).length
    : 0;
  const driftScore = drift?.drift_score ?? 0;
  const recentEvents = (drift?.events ?? []).slice(0, 5);
  const isUninitialized = !loading && !error && componentCount === 0;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Boxes}
          label="Components"
          value={loading ? '—' : componentCount}
          color="#6366f1"
        />
        <StatCard
          icon={Zap}
          label="Capabilities"
          value={loading ? '—' : capabilityCount}
          color="#8b5cf6"
        />
        <StatCard
          icon={ShieldCheck}
          label="Active Rules"
          value={loading ? '—' : 0}
          color="#10b981"
        />
        <StatCard
          icon={Activity}
          label="Drift Events"
          value={loading ? '—' : recentEvents.length}
          color="#f59e0b"
        />
      </div>

      {isUninitialized ? (
        <SetupPrompt />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Chart */}
          <div
            className="lg:col-span-2 rounded-xl p-5 border"
            style={{
              backgroundColor: 'var(--card)',
              borderColor: 'var(--border)',
            }}
          >
            <h2
              className="text-sm font-semibold mb-4"
              style={{ color: 'var(--foreground)' }}
            >
              Architecture Health — Drift Score (7 days)
            </h2>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart
                data={MOCK_CHART_DATA}
                margin={{ top: 0, right: 0, left: -20, bottom: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="rgba(255,255,255,0.05)"
                />
                <XAxis
                  dataKey="day"
                  tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#1e293b',
                    border: '1px solid #334155',
                    borderRadius: '8px',
                    fontSize: '12px',
                    color: '#f1f5f9',
                  }}
                  itemStyle={{ color: '#818cf8' }}
                  cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                />
                <Bar
                  dataKey="score"
                  fill="#6366f1"
                  radius={[4, 4, 0, 0]}
                  name="Drift Score"
                />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Current drift score */}
          <div
            className="rounded-xl p-5 border flex flex-col items-center justify-center gap-4"
            style={{
              backgroundColor: 'var(--card)',
              borderColor: 'var(--border)',
            }}
          >
            <h2
              className="text-sm font-semibold self-start"
              style={{ color: 'var(--foreground)' }}
            >
              Current Drift Score
            </h2>
            <DriftScore score={driftScore} size="lg" />
          </div>
        </div>
      )}

      {/* Recent activity */}
      {!isUninitialized && (
        <div
          className="rounded-xl border"
          style={{
            backgroundColor: 'var(--card)',
            borderColor: 'var(--border)',
          }}
        >
          <div
            className="px-5 py-4 border-b"
            style={{ borderColor: 'var(--border)' }}
          >
            <h2
              className="text-sm font-semibold"
              style={{ color: 'var(--foreground)' }}
            >
              Recent Drift Events
            </h2>
          </div>

          {loading ? (
            <div className="p-6 space-y-3">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-8 rounded animate-pulse"
                  style={{ backgroundColor: 'var(--border)' }}
                />
              ))}
            </div>
          ) : recentEvents.length === 0 ? (
            <div
              className="px-5 py-8 text-center text-sm"
              style={{ color: 'var(--muted-foreground)' }}
            >
              No drift events detected.
            </div>
          ) : (
            <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
              {recentEvents.map((event) => (
                <div
                  key={event.id}
                  className="flex items-start gap-3 px-5 py-3"
                >
                  <div className="mt-0.5">
                    <SeverityBadge severity={event.severity} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p
                      className="text-sm"
                      style={{ color: 'var(--foreground)' }}
                    >
                      {event.message}
                    </p>
                    {event.component && (
                      <p
                        className="text-xs mt-0.5"
                        style={{ color: 'var(--muted-foreground)' }}
                      >
                        {event.component}
                      </p>
                    )}
                  </div>
                  {event.timestamp && (
                    <span
                      className="text-xs flex-shrink-0"
                      style={{ color: 'var(--muted-foreground)' }}
                    >
                      {new Date(event.timestamp).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
