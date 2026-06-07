'use client';

import { useEffect, useState } from 'react';
import { Plus, X, ShieldCheck } from 'lucide-react';

/* ---------- Types ---------- */
type RuleKind = 'principle' | 'invariant' | 'adr';

interface Rule {
  id: string;
  name: string;
  kind: RuleKind;
  scope?: string;
  enforcement?: 'strict' | 'warn' | 'advisory';
  status?: 'active' | 'draft' | 'disabled';
  description?: string;
}

/* ---------- Helpers ---------- */
const TABS: Array<{ key: RuleKind; label: string }> = [
  { key: 'principle', label: 'Principles' },
  { key: 'invariant', label: 'Invariants' },
  { key: 'adr', label: 'ADRs' },
];

const enforcementConfig: Record<
  string,
  { label: string; color: string; bg: string }
> = {
  strict:   { label: 'Strict',   color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
  warn:     { label: 'Warn',     color: '#f59e0b', bg: 'rgba(245,158,11,0.1)' },
  advisory: { label: 'Advisory', color: '#60a5fa', bg: 'rgba(96,165,250,0.1)' },
};

const statusConfig: Record<
  string,
  { label: string; color: string }
> = {
  active:   { label: 'Active',   color: '#22c55e' },
  draft:    { label: 'Draft',    color: '#94a3b8' },
  disabled: { label: 'Disabled', color: '#6b7280' },
};

/* ---------- Add Rule Form ---------- */
interface AddRuleFormProps {
  kind: RuleKind;
  onAdd: (rule: Rule) => void;
  onCancel: () => void;
}

function AddRuleForm({ kind, onAdd, onCancel }: AddRuleFormProps) {
  const [name, setName] = useState('');
  const [scope, setScope] = useState('');
  const [enforcement, setEnforcement] = useState<Rule['enforcement']>('warn');
  const [description, setDescription] = useState('');

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    onAdd({
      id: `${Date.now()}`,
      name: name.trim(),
      kind,
      scope: scope.trim() || undefined,
      enforcement,
      status: 'draft',
      description: description.trim() || undefined,
    });
  }

  const inputStyle = {
    backgroundColor: 'var(--muted)',
    borderColor: 'var(--border)',
    color: 'var(--foreground)',
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border rounded-xl p-5 space-y-3"
      style={{ backgroundColor: 'var(--card)', borderColor: '#6366f1' }}
    >
      <h3 className="text-sm font-semibold" style={{ color: 'var(--foreground)' }}>
        New {kind.charAt(0).toUpperCase() + kind.slice(1)}
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: 'var(--muted-foreground)' }}>
            Name *
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. No direct DB access from UI"
            required
            className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-indigo-500 transition-colors"
            style={inputStyle}
          />
        </div>

        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: 'var(--muted-foreground)' }}>
            Scope
          </label>
          <input
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            placeholder="e.g. api, frontend, *"
            className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-indigo-500 transition-colors"
            style={inputStyle}
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium mb-1" style={{ color: 'var(--muted-foreground)' }}>
          Enforcement
        </label>
        <select
          value={enforcement}
          onChange={(e) => setEnforcement(e.target.value as Rule['enforcement'])}
          className="px-3 py-2 rounded-lg border text-sm outline-none focus:border-indigo-500 transition-colors"
          style={inputStyle}
        >
          <option value="strict">Strict</option>
          <option value="warn">Warn</option>
          <option value="advisory">Advisory</option>
        </select>
      </div>

      <div>
        <label className="block text-xs font-medium mb-1" style={{ color: 'var(--muted-foreground)' }}>
          Description
        </label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          placeholder="Explain the rationale for this rule…"
          className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-indigo-500 transition-colors resize-none"
          style={inputStyle}
        />
      </div>

      <div className="flex gap-2 justify-end pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-lg text-sm transition-colors"
          style={{
            backgroundColor: 'var(--muted)',
            color: 'var(--muted-foreground)',
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          style={{ backgroundColor: '#6366f1', color: '#fff' }}
        >
          Add Rule
        </button>
      </div>
    </form>
  );
}

/* ---------- Rules Table ---------- */
function RulesTable({
  rules,
  onRemove,
}: {
  rules: Rule[];
  onRemove: (id: string) => void;
}) {
  if (rules.length === 0) {
    return (
      <div
        className="py-12 text-center rounded-xl border"
        style={{
          backgroundColor: 'var(--card)',
          borderColor: 'var(--border)',
          color: 'var(--muted-foreground)',
        }}
      >
        <ShieldCheck size={28} className="mx-auto mb-2 opacity-40" />
        <p className="text-sm">No rules defined yet.</p>
      </div>
    );
  }

  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{
        backgroundColor: 'var(--card)',
        borderColor: 'var(--border)',
      }}
    >
      {/* Header */}
      <div
        className="grid text-[11px] uppercase tracking-wide font-semibold px-5 py-2"
        style={{
          gridTemplateColumns: '1fr auto auto auto',
          color: 'var(--muted-foreground)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <span>Rule</span>
        <span className="px-4">Scope</span>
        <span className="px-4">Enforcement</span>
        <span className="px-4">Status</span>
      </div>

      <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
        {rules.map((rule) => {
          const enfCfg = enforcementConfig[rule.enforcement ?? 'advisory'];
          const stCfg = statusConfig[rule.status ?? 'draft'];

          return (
            <div
              key={rule.id}
              className="flex items-center gap-3 px-5 py-3 group hover:bg-white/[0.02] transition-colors"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate" style={{ color: 'var(--foreground)' }}>
                  {rule.name}
                </p>
                {rule.description && (
                  <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--muted-foreground)' }}>
                    {rule.description}
                  </p>
                )}
              </div>

              <span
                className="text-xs font-mono px-2 py-0.5 rounded flex-shrink-0"
                style={{
                  backgroundColor: 'var(--muted)',
                  color: 'var(--muted-foreground)',
                }}
              >
                {rule.scope ?? '*'}
              </span>

              <span
                className="text-xs font-medium px-2 py-0.5 rounded flex-shrink-0"
                style={{
                  backgroundColor: enfCfg.bg,
                  color: enfCfg.color,
                }}
              >
                {enfCfg.label}
              </span>

              <span
                className="flex items-center gap-1 text-xs flex-shrink-0"
                style={{ color: stCfg.color }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ backgroundColor: stCfg.color }}
                />
                {stCfg.label}
              </span>

              <button
                onClick={() => onRemove(rule.id)}
                className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white/10"
                aria-label="Remove rule"
              >
                <X size={12} color="var(--muted-foreground)" />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Page ---------- */
export default function RulesPage() {
  const [activeTab, setActiveTab] = useState<RuleKind>('principle');
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    async function fetchRules() {
      try {
        const res = await fetch('/api/specs/rules');
        if (res.ok) {
          const data = await res.json();
          setRules(Array.isArray(data) ? data : data.rules ?? []);
        }
      } catch {
        // API not running — start with empty list
      } finally {
        setLoading(false);
      }
    }
    fetchRules();
  }, []);

  function handleAdd(rule: Rule) {
    setRules((prev) => [rule, ...prev]);
    setShowForm(false);
  }

  function handleRemove(id: string) {
    setRules((prev) => prev.filter((r) => r.id !== id));
  }

  const tabRules = rules.filter((r) => r.kind === activeTab);

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      {/* Tabs + Add button */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div
          className="flex gap-1 p-1 rounded-lg"
          style={{ backgroundColor: 'var(--card)' }}
        >
          {TABS.map(({ key, label }) => {
            const count = rules.filter((r) => r.kind === key).length;
            return (
              <button
                key={key}
                onClick={() => {
                  setActiveTab(key);
                  setShowForm(false);
                }}
                className="px-4 py-1.5 rounded-md text-sm font-medium transition-colors"
                style={{
                  backgroundColor:
                    activeTab === key ? '#6366f1' : 'transparent',
                  color:
                    activeTab === key ? '#fff' : 'var(--muted-foreground)',
                }}
              >
                {label}
                {count > 0 && (
                  <span
                    className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full"
                    style={{
                      backgroundColor:
                        activeTab === key
                          ? 'rgba(255,255,255,0.2)'
                          : 'var(--muted)',
                    }}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{ backgroundColor: '#6366f1', color: '#fff' }}
        >
          <Plus size={14} />
          Add Rule
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <AddRuleForm
          kind={activeTab}
          onAdd={handleAdd}
          onCancel={() => setShowForm(false)}
        />
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-12 rounded-xl animate-pulse"
              style={{ backgroundColor: 'var(--card)' }}
            />
          ))}
        </div>
      ) : (
        <RulesTable rules={tabRules} onRemove={handleRemove} />
      )}
    </div>
  );
}
