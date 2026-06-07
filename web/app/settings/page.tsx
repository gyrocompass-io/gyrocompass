'use client';

import { useState } from 'react';
import { Check, Copy, Github, Cpu, Key } from 'lucide-react';

/* ---------- Types ---------- */
type LLMProvider = 'anthropic' | 'openai' | 'azure_openai' | 'ollama';

interface SettingsState {
  llm_provider: LLMProvider;
  llm_model: string;
  llm_api_key: string;
  github_token: string;
  github_repo: string;
  backend_port: string;
}

/* ---------- Defaults ---------- */
const DEFAULT_MODELS: Record<LLMProvider, string> = {
  anthropic: 'claude-sonnet-4-5',
  openai: 'gpt-4o',
  azure_openai: 'gpt-4o',
  ollama: 'llama3.2',
};

const PROVIDER_LABELS: Record<LLMProvider, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  azure_openai: 'Azure OpenAI',
  ollama: 'Ollama (local)',
};

/* ---------- Section card ---------- */
function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <div
      className="rounded-xl border p-5 space-y-4"
      style={{ backgroundColor: 'var(--card)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-center gap-2">
        <Icon size={16} color="#6366f1" />
        <h2 className="text-sm font-semibold" style={{ color: 'var(--foreground)' }}>
          {title}
        </h2>
      </div>
      {children}
    </div>
  );
}

/* ---------- Form field ---------- */
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div>
      <label
        className="block text-xs font-medium mb-1"
        style={{ color: 'var(--muted-foreground)' }}
      >
        {label}
      </label>
      {children}
      {hint && (
        <p className="text-xs mt-1" style={{ color: 'var(--muted-foreground)' }}>
          {hint}
        </p>
      )}
    </div>
  );
}

function inputStyle() {
  return {
    backgroundColor: 'var(--muted)',
    borderColor: 'var(--border)',
    color: 'var(--foreground)',
  };
}

/* ---------- Env preview ---------- */
function EnvPreview({ settings }: { settings: SettingsState }) {
  const [copied, setCopied] = useState(false);

  const lines = [
    `# GyroCompass — generated configuration`,
    `GYROCOMPASS_LLM_PROVIDER=${settings.llm_provider}`,
    `GYROCOMPASS_LLM_MODEL=${settings.llm_model || DEFAULT_MODELS[settings.llm_provider]}`,
    settings.llm_api_key
      ? `GYROCOMPASS_LLM_API_KEY=${settings.llm_api_key}`
      : `# GYROCOMPASS_LLM_API_KEY=your_key_here`,
    ``,
    settings.github_token ? `GITHUB_TOKEN=${settings.github_token}` : `# GITHUB_TOKEN=your_token_here`,
    settings.github_repo ? `GITHUB_REPO=${settings.github_repo}` : ``,
    ``,
    `GYROCOMPASS_PORT=${settings.backend_port || '7700'}`,
  ]
    .filter((l) => l !== undefined)
    .join('\n');

  async function handleCopy() {
    await navigator.clipboard.writeText(lines);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold" style={{ color: 'var(--muted-foreground)' }}>
          .env content (copy to your project root)
        </p>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors"
          style={{
            backgroundColor: copied ? 'rgba(34,197,94,0.15)' : 'var(--muted)',
            color: copied ? '#22c55e' : 'var(--muted-foreground)',
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <pre
        className="text-xs font-mono rounded-lg p-4 overflow-x-auto whitespace-pre-wrap leading-relaxed"
        style={{
          backgroundColor: 'rgba(0,0,0,0.4)',
          color: '#a5b4fc',
          border: '1px solid var(--border)',
        }}
      >
        {lines}
      </pre>
    </div>
  );
}

/* ---------- Page ---------- */
export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsState>({
    llm_provider: 'anthropic',
    llm_model: '',
    llm_api_key: '',
    github_token: '',
    github_repo: '',
    backend_port: '7700',
  });

  function update<K extends keyof SettingsState>(key: K, value: SettingsState[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }

  const cls =
    'w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-indigo-500 transition-colors';

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      {/* LLM Provider */}
      <Section title="LLM Provider" icon={Cpu}>
        <Field label="Provider">
          <select
            value={settings.llm_provider}
            onChange={(e) => {
              const provider = e.target.value as LLMProvider;
              update('llm_provider', provider);
              update('llm_model', DEFAULT_MODELS[provider]);
            }}
            className={cls}
            style={inputStyle()}
          >
            {Object.entries(PROVIDER_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Model"
          hint={`Default: ${DEFAULT_MODELS[settings.llm_provider]}`}
        >
          <input
            value={settings.llm_model}
            onChange={(e) => update('llm_model', e.target.value)}
            placeholder={DEFAULT_MODELS[settings.llm_provider]}
            className={cls}
            style={inputStyle()}
          />
        </Field>

        <Field label="API Key" hint="Stored only in your local .env file — never sent to GyroCompass servers.">
          <div className="relative">
            <input
              type="password"
              value={settings.llm_api_key}
              onChange={(e) => update('llm_api_key', e.target.value)}
              placeholder={
                settings.llm_provider === 'ollama'
                  ? 'Not required for Ollama'
                  : 'sk-…'
              }
              disabled={settings.llm_provider === 'ollama'}
              className={cls}
              style={{
                ...inputStyle(),
                opacity: settings.llm_provider === 'ollama' ? 0.5 : 1,
                paddingRight: '2.5rem',
              }}
            />
            <Key
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--muted-foreground)' }}
            />
          </div>
        </Field>

        {settings.llm_provider === 'ollama' && (
          <div
            className="flex items-start gap-2 p-3 rounded-lg text-xs"
            style={{
              backgroundColor: 'rgba(96,165,250,0.1)',
              color: '#60a5fa',
            }}
          >
            <span className="mt-0.5">ℹ</span>
            <span>
              Ollama runs locally. Ensure it is started with{' '}
              <code className="font-mono">ollama serve</code> and the model is
              pulled:{' '}
              <code className="font-mono">
                ollama pull {settings.llm_model || 'llama3.2'}
              </code>
            </span>
          </div>
        )}
      </Section>

      {/* GitHub */}
      <Section title="GitHub Integration" icon={Github}>
        <Field
          label="Personal Access Token"
          hint="Needs repo read permissions for ADR and PR analysis."
        >
          <div className="relative">
            <input
              type="password"
              value={settings.github_token}
              onChange={(e) => update('github_token', e.target.value)}
              placeholder="ghp_…"
              className={cls}
              style={{ ...inputStyle(), paddingRight: '2.5rem' }}
            />
            <Key
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--muted-foreground)' }}
            />
          </div>
        </Field>

        <Field
          label="Repository"
          hint="Format: owner/repo — used to fetch ADRs and diff context."
        >
          <input
            value={settings.github_repo}
            onChange={(e) => update('github_repo', e.target.value)}
            placeholder="acme/my-service"
            className={cls}
            style={inputStyle()}
          />
        </Field>

        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--muted-foreground)' }}>
          <span
            className="inline-flex items-center gap-1"
            style={{
              color: settings.github_token ? '#22c55e' : 'var(--muted-foreground)',
            }}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{
                backgroundColor: settings.github_token
                  ? '#22c55e'
                  : 'var(--border)',
              }}
            />
            {settings.github_token ? 'Token configured' : 'Not configured'}
          </span>
        </div>
      </Section>

      {/* Backend */}
      <Section title="Backend" icon={Cpu}>
        <Field
          label="API Port"
          hint="The port GyroCompass API server listens on."
        >
          <input
            value={settings.backend_port}
            onChange={(e) => update('backend_port', e.target.value)}
            placeholder="7700"
            type="number"
            min="1024"
            max="65535"
            className={cls}
            style={inputStyle()}
          />
        </Field>

        <div className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--muted-foreground)' }}>API URL:</span>
          <code
            className="font-mono"
            style={{ color: '#a5b4fc' }}
          >
            http://localhost:{settings.backend_port || '7700'}
          </code>
        </div>
      </Section>

      {/* Env preview */}
      <Section title="Generated .env" icon={Copy}>
        <EnvPreview settings={settings} />
      </Section>
    </div>
  );
}
