'use client';

import { useEffect, useState, useCallback } from 'react';
import ReactFlow, { Background, Controls, MiniMap, Node, Edge, useNodesState, useEdgesState } from 'reactflow';
import 'reactflow/dist/style.css';

const TYPE_COLORS: Record<string, string> = {
  container: '#6366f1',
  component: '#8b5cf6',
  external_system: '#64748b',
  service: '#0ea5e9',
  database: '#10b981',
  queue: '#f59e0b',
  cache: '#ec4899',
};

interface ArchElement {
  type: string;
  description: string;
  facts?: string[];
  relationships?: Record<string, { type: string; description?: string }>;
}

export default function ArchitecturePage() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selected, setSelected] = useState<{ id: string; el: ArchElement } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/analysis/state/default')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.architecture) { setLoading(false); return; }
        const arch: Record<string, ArchElement> = data.architecture;
        const ids = Object.keys(arch);
        const cols = Math.ceil(Math.sqrt(ids.length));
        const newNodes: Node[] = ids.map((id, i) => ({
          id,
          position: { x: (i % cols) * 220, y: Math.floor(i / cols) * 140 },
          data: { label: id },
          style: { background: `${TYPE_COLORS[arch[id].type] || '#6366f1'}22`, border: `1px solid ${TYPE_COLORS[arch[id].type] || '#6366f1'}`, borderRadius: 10, color: '#f1f5f9', fontSize: 13, padding: '10px 16px', minWidth: 140 },
        }));
        const newEdges: Edge[] = ids.flatMap(id => Object.entries(arch[id].relationships || {}).map(([target, rel]) => ({
          id: `${id}-${target}`,
          source: id,
          target,
          label: rel.type,
          animated: rel.type === 'async',
          style: { stroke: '#4f46e5' },
          labelStyle: { fill: '#94a3b8', fontSize: 10 },
        })));
        setNodes(newNodes);
        setEdges(newEdges);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    fetch('/api/analysis/state/default').then(r => r.json()).then(data => {
      const el = data?.architecture?.[node.id];
      if (el) setSelected({ id: node.id, el });
    });
  }, []);

  return (
    <div className="flex gap-4 h-[calc(100vh-120px)]">
      <div className="flex-1 rounded-xl overflow-hidden border" style={{ borderColor: 'var(--border)' }}>
        {loading ? (
          <div className="flex items-center justify-center h-full" style={{ color: 'var(--muted-foreground)' }}>Loading architecture graph…</div>
        ) : nodes.length === 0 ? (
          <div className="flex items-center justify-center h-full text-center">
            <div>
              <p className="text-lg mb-2" style={{ color: 'var(--foreground)' }}>No architecture state</p>
              <p className="text-sm" style={{ color: 'var(--muted-foreground)' }}>Run <code className="bg-gray-800 px-1.5 py-0.5 rounded text-green-400">gyro analyze --save</code> to populate.</p>
            </div>
          </div>
        ) : (
          <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick} fitView>
            <Background color="#334155" gap={20} />
            <Controls />
            <MiniMap nodeColor={n => TYPE_COLORS[n.data?.type] || '#6366f1'} style={{ backgroundColor: '#0f172a' }} />
          </ReactFlow>
        )}
      </div>

      {selected && (
        <div className="w-72 rounded-xl border p-4 overflow-y-auto" style={{ backgroundColor: 'var(--card)', borderColor: 'var(--border)' }}>
          <div className="flex justify-between items-start mb-3">
            <h3 className="font-semibold text-sm" style={{ color: 'var(--foreground)' }}>{selected.id}</h3>
            <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">×</button>
          </div>
          <p className="text-xs mb-3" style={{ color: 'var(--muted-foreground)' }}>{selected.el.description}</p>
          {selected.el.facts && selected.el.facts.length > 0 && (
            <>
              <p className="text-xs font-semibold mb-1" style={{ color: 'var(--foreground)' }}>Facts</p>
              <ul className="space-y-1 mb-3">
                {selected.el.facts.map((f, i) => <li key={i} className="text-xs flex gap-1.5" style={{ color: 'var(--muted-foreground)' }}><span>•</span><span>{f}</span></li>)}
              </ul>
            </>
          )}
          {selected.el.relationships && Object.keys(selected.el.relationships).length > 0 && (
            <>
              <p className="text-xs font-semibold mb-1" style={{ color: 'var(--foreground)' }}>Relationships</p>
              <div className="space-y-1">
                {Object.entries(selected.el.relationships).map(([t, r]) => (
                  <div key={t} className="text-xs rounded p-2" style={{ backgroundColor: 'rgba(99,102,241,0.1)' }}>
                    <span className="font-medium" style={{ color: '#818cf8' }}>→ {t}</span>
                    <span className="ml-1" style={{ color: 'var(--muted-foreground)' }}>({r.type})</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
