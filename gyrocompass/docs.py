"""Auto-generated architecture visualization.

Turns the extracted ArchitectureState into things people can *see* and machines
can *consume*, with zero infrastructure:

  • architecture.html — a polished, standalone dark-themed architecture diagram
                        (inline SVG, JetBrains Mono, semantic colors, built-in
                        Copy/PNG/PDF export) + a "state evolution" panel. Double-
                        click to view in any browser — no server, no build.
  • ARCHITECTURE.md   — Mermaid diagram + tables, renders natively on GitHub.
  • architecture.mmd  — raw Mermaid source (machine-consumable, diff-able).
  • architecture.json — graph {nodes, edges} for D3 / Cytoscape / any viz tool.

The HTML diagram adopts the visual design system of the MIT-licensed
architecture-diagram-generator by Cocoon AI (dark slate-950 canvas, grid
background, semantic component colors, SVG boxes + arrowheads, export toolbar),
applied programmatically via an auto-layout (BFS-distance layering) over the
extracted components.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from gyrocompass.models import ArchitectureState, ElementType

# ── Type → visual style (Mermaid classDef + label) ─────────────────────────────

_TYPE_STYLE: dict[str, tuple[str, str, str]] = {
    # type: (fill, stroke, emoji)
    "container": ("#1e293b", "#6366f1", "📦"),
    "component": ("#1e1b4b", "#8b5cf6", "🧩"),
    "service": ("#0c4a6e", "#0ea5e9", "⚙️"),
    "database": ("#064e3b", "#10b981", "🗄️"),
    "queue": ("#451a03", "#f59e0b", "📨"),
    "cache": ("#4a044e", "#ec4899", "⚡"),
    "external_system": ("#1f2937", "#64748b", "🌐"),
}


def _safe_id(name: str) -> str:
    """Mermaid node ids can't contain slashes/spaces/dots."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", name)


# Map a drift event type → the architecture primitive it moved. These are the
# eight dimensions a mature team tracks (architecture, data, contracts, rules…).
_PRIMITIVE_FOR_DRIFT = {
    "component_added": "Architecture",
    "component_removed": "Architecture",
    "component_modified": "Architecture",
    "relationship_added": "Data flows",
    "relationship_removed": "Data flows",
    "capability_regression": "Capabilities",
    "rule_violation": "Rules",
    "tech_stack_change": "Tech stack",
    "api_surface_change": "API surface",
    "data_model_change": "Data model",
    "baseline": "Baseline",
    "remediation": "Remediation",
}

# Primitive → accent color (muted, professional).
_PRIMITIVE_COLOR = {
    "Architecture": "#34d399",
    "Data flows": "#22d3ee",
    "Capabilities": "#a78bfa",
    "Rules": "#fb7185",
    "Tech stack": "#fbbf24",
    "API surface": "#38bdf8",
    "Data model": "#c084fc",
    "External": "#fb923c",
    "Baseline": "#64748b",
    "Remediation": "#4ade80",
}


# ── HTML shell (architecture-diagram-generator design system, MIT, Cocoon AI) ──
# Static template with __PLACEHOLDER__ tokens filled by DocGenerator.html().
# Dark theme · JetBrains Mono · semantic SVG · built-in Copy/PNG/PDF export.

_DIAGRAM_DOC = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__TITLE__ — Architecture</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js" integrity="sha384-ZZ1pncU3bQe8y31yfZdMFdSpttDoPmOZg2wguVK9almUodir1PghgT0eY7Mrty8H" crossorigin="anonymous"></script>
  <script src="https://cdn.jsdelivr.net/npm/jspdf@2.5.2/dist/jspdf.umd.min.js" integrity="sha384-en/ztfPSRkGfME4KIm05joYXynqzUgbsG5nMrj/xEFAHXkeZfO3yMK8QQ+mP7p1/" crossorigin="anonymous"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'JetBrains Mono', monospace; background: #020617; min-height: 100vh; padding: 2rem; color: white; }
    .container { max-width: 1280px; margin: 0 auto; }
    .header { margin-bottom: 2rem; }
    .header-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem; }
    .pulse-dot { width: 12px; height: 12px; background: #22d3ee; border-radius: 50%; animation: pulse 2s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.025em; }
    .subtitle { color: #94a3b8; font-size: 0.8rem; margin-left: 1.75rem; }
    .diagram-container { background: rgba(15, 23, 42, 0.5); border-radius: 1rem; border: 1px solid #1e293b; padding: 1.5rem; overflow-x: auto; }
    svg { width: 100%; min-width: 720px; display: block; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-top: 2rem; }
    .card { background: rgba(15, 23, 42, 0.5); border-radius: 0.75rem; border: 1px solid #1e293b; padding: 1.25rem; }
    .card-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; }
    .card-dot { width: 8px; height: 8px; border-radius: 50%; }
    .card-dot.cyan { background: #22d3ee; } .card-dot.emerald { background: #34d399; }
    .card-dot.violet { background: #a78bfa; } .card-dot.amber { background: #fbbf24; }
    .card-dot.rose { background: #fb7185; }
    .card h3 { font-size: 0.875rem; font-weight: 600; }
    .card ul { list-style: none; color: #94a3b8; font-size: 0.75rem; }
    .card li { margin-bottom: 0.375rem; }
    .footer { text-align: center; margin-top: 1.5rem; color: #475569; font-size: 0.75rem; }
    /* Architecture evolution — primitive bars */
    .primitives { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 0.45rem 1.1rem; margin: 0.25rem 0 1.4rem; }
    .prim { display: flex; align-items: center; gap: 0.6rem; font-size: 0.72rem; color: #94a3b8; }
    .prim-label { width: 92px; flex-shrink: 0; }
    .prim-bar { flex: 1; height: 6px; background: #1e293b; border-radius: 3px; overflow: hidden; }
    .prim-fill { height: 100%; border-radius: 3px; transition: width .3s; }
    .prim-count { width: 16px; text-align: right; color: #cbd5e1; }
    /* Architecture evolution — timeline */
    .timeline { position: relative; margin: 0.25rem 0 0 0.4rem; padding-left: 1.4rem; border-left: 1px solid #1e293b; }
    .tl-item { position: relative; padding: 0 0 1.15rem; }
    .tl-item:last-child { padding-bottom: 0.2rem; }
    .tl-dot { position: absolute; left: -1.83rem; top: 0.25rem; width: 9px; height: 9px; border-radius: 50%; border: 2px solid #020617; box-shadow: 0 0 0 1px #1e293b; }
    .tl-meta { display: flex; align-items: center; gap: 0.6rem; font-size: 0.7rem; color: #64748b; margin-bottom: 0.2rem; }
    .tl-date { color: #94a3b8; font-weight: 500; }
    .tl-pr { color: #22d3ee; }
    .tl-sev { font-size: 0.6rem; padding: 0.04rem 0.4rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em; }
    .tl-sev.crit { background: rgba(251,113,133,0.14); color: #fb7185; }
    .tl-sev.high { background: rgba(251,146,60,0.14); color: #fb923c; }
    .tl-sev.med { background: rgba(251,191,36,0.14); color: #fbbf24; }
    .tl-sev.low { background: rgba(52,211,153,0.14); color: #34d399; }
    .tl-title { font-size: 0.82rem; color: #e2e8f0; font-weight: 500; margin-bottom: 0.35rem; }
    .tl-tags { display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }
    .tag { font-size: 0.62rem; color: #94a3b8; background: rgba(30,41,59,0.6); border: 1px solid #1e293b; padding: 0.07rem 0.5rem; border-radius: 999px; }
    .tl-file { font-size: 0.62rem; color: #475569; }
    .toolbar { display: flex; gap: 0.5rem; margin-left: auto; flex-shrink: 0; align-items: center; }
    .toolbar-toggle { background: transparent; border: none; color: #475569; cursor: pointer; font-size: 1.25rem; line-height: 1; padding: 0.25rem 0.5rem; border-radius: 0.375rem; transition: color 0.2s, background 0.2s; }
    .toolbar-toggle:hover { color: #94a3b8; background: rgba(30, 41, 59, 0.5); }
    .toolbar-actions { display: none; gap: 0.5rem; }
    .toolbar.expanded .toolbar-actions { display: flex; }
    .toolbar-actions button { background: rgba(30, 41, 59, 0.8); border: 1px solid #334155; color: #94a3b8; padding: 0.375rem 0.75rem; border-radius: 0.375rem; font-family: inherit; font-size: 0.75rem; cursor: pointer; transition: all 0.2s; white-space: nowrap; }
    .toolbar-actions button:hover { background: rgba(51, 65, 85, 0.8); color: white; border-color: #475569; }
    @media print { body { background: #020617; padding: 1rem; } .toolbar { display: none !important; } }
  </style>
</head>
<body>
  <div class="container" id="report-container">
    <div class="header">
      <div class="header-row">
        <div class="pulse-dot"></div>
        <h1>__TITLE__ — Architecture</h1>
        <div class="toolbar">
          <div class="toolbar-actions">
            <button onclick="copyAsImage(this)">📋 Copy</button>
            <button onclick="downloadPNG(this)">🖼️ PNG</button>
            <button onclick="downloadPDF(this)">📄 PDF</button>
          </div>
          <button class="toolbar-toggle" onclick="this.parentElement.classList.toggle('expanded')" title="Export options" aria-label="Export options">⋯</button>
        </div>
      </div>
      <p class="subtitle">__SUBTITLE__</p>
    </div>

    <div class="diagram-container">
__SVG__
    </div>

    <div class="cards">
__CARDS__
__EVOLUTION__
    </div>

    <p class="footer">__FOOTER__</p>
  </div>

  <script>
    async function _capture() {
      const el = document.getElementById('report-container');
      const r = el.getBoundingClientRect();
      const pad = 32;
      return await html2canvas(document.body, { backgroundColor: '#020617', scale: 2, useCORS: true, ignoreElements: (e) => e.classList && e.classList.contains('toolbar'), x: r.left + window.scrollX - pad, y: r.top + window.scrollY - pad, width: r.width + pad * 2, height: r.height + pad * 2 });
    }
    async function copyAsImage(btn) {
      const orig = btn.textContent;
      try {
        const canvas = await _capture();
        const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        btn.textContent = '✓ Copied!';
      } catch (e) { btn.textContent = '✗ Failed'; }
      setTimeout(() => btn.textContent = orig, 2000);
    }
    async function downloadPNG(btn) {
      const orig = btn.textContent; btn.textContent = '⏳ ...';
      try {
        const canvas = await _capture();
        const link = document.createElement('a');
        link.download = '__TITLE___architecture.png';
        link.href = canvas.toDataURL('image/png'); link.click();
        btn.textContent = '✓ Done!';
      } catch (e) { btn.textContent = '✗ Failed'; }
      setTimeout(() => btn.textContent = orig, 2000);
    }
    async function downloadPDF(btn) {
      const orig = btn.textContent; btn.textContent = '⏳ ...';
      try {
        const canvas = await _capture();
        const imgData = canvas.toDataURL('image/png');
        const { jsPDF } = window.jspdf;
        const orientation = canvas.width > canvas.height ? 'landscape' : 'portrait';
        const pdf = new jsPDF({ orientation, unit: 'px', format: [canvas.width, canvas.height], hotfixes: ['px_scaling'] });
        pdf.addImage(imgData, 'PNG', 0, 0, canvas.width, canvas.height);
        pdf.save('__TITLE___architecture.pdf');
        btn.textContent = '✓ Done!';
      } catch (e) { btn.textContent = '✗ Failed'; }
      setTimeout(() => btn.textContent = orig, 2000);
    }
  </script>
</body>
</html>"""


class DocGenerator:
    """Generates visual + machine-readable architecture artifacts from state."""

    def __init__(self, state: ArchitectureState, timeline_items: list[dict] | None = None) -> None:
        self.state = state
        # Optional drift-evolution timeline items (from the memory layer):
        # [{id, content, start, group, severity, title}]
        self.timeline_items = timeline_items or []

    # ── Mermaid: component flowchart ─────────────────────────────────────────

    def component_diagram(self) -> str:
        """A Mermaid flowchart of components and their relationships."""
        lines = ["flowchart LR"]
        arch = self.state.architecture

        # Nodes
        for cid, elem in arch.items():
            nid = _safe_id(cid)
            etype = elem.type.value if isinstance(elem.type, ElementType) else str(elem.type)
            emoji = _TYPE_STYLE.get(etype, ("", "", "•"))[2]
            label = f"{emoji} {cid}<br/><small>{etype}</small>"
            lines.append(f'    {nid}["{label}"]:::{etype}')

        # Edges (relationships)
        seen_edges: set[tuple[str, str]] = set()
        for cid, elem in arch.items():
            nid = _safe_id(cid)
            for target, rel in (elem.relationships or {}).items():
                tid = _safe_id(target)
                if (nid, tid) in seen_edges:
                    continue
                seen_edges.add((nid, tid))
                rtype = getattr(rel.type, "value", str(rel.type)) if rel else ""
                arrow = "-.->" if rtype in ("async", "event") else "-->"
                edge_label = f"|{rtype}|" if rtype else ""
                lines.append(f"    {nid} {arrow}{edge_label} {tid}")

        # Class definitions (colors)
        for etype, (fill, stroke, _) in _TYPE_STYLE.items():
            lines.append(
                f"    classDef {etype} fill:{fill},stroke:{stroke},stroke-width:2px,color:#e2e8f0;"
            )
        return "\n".join(lines)

    # ── Mermaid: data-model ER diagram ───────────────────────────────────────

    def data_model_diagram(self) -> str | None:
        """A Mermaid erDiagram of data entities, or None if there are none."""
        entities = self.state.data_model.entities
        if not entities:
            return None
        lines = ["erDiagram"]
        # Relationships first
        for name, ent in entities.items():
            for rel in ent.relationships or []:
                target = _safe_id(rel.target)
                card = {
                    "oneToMany": "||--o{",
                    "manyToOne": "}o--||",
                    "manyToMany": "}o--o{",
                    "oneToOne": "||--||",
                }.get(rel.type, "||--o{")
                lines.append(f"    {_safe_id(name)} {card} {target} : {rel.type}")
        # Entity attribute blocks
        for name, ent in entities.items():
            lines.append(f"    {_safe_id(name)} {{")
            for attr in (ent.attributes or [])[:12]:
                atype = _safe_id(attr.type) or "string"
                flag = "PK" if attr.unique and attr.name in ("id", "uuid") else ""
                lines.append(f"        {atype} {attr.name} {flag}".rstrip())
            lines.append("    }")
        return "\n".join(lines)

    # ── Graph JSON (machine-consumable) ──────────────────────────────────────

    def graph_json(self) -> dict:
        nodes, edges = [], []
        for cid, elem in self.state.architecture.items():
            etype = elem.type.value if isinstance(elem.type, ElementType) else str(elem.type)
            nodes.append({
                "id": cid,
                "label": cid,
                "type": etype,
                "description": elem.description,
                "facts": elem.facts,
            })
            for target, rel in (elem.relationships or {}).items():
                edges.append({
                    "source": cid,
                    "target": target,
                    "type": getattr(rel.type, "value", str(rel.type)) if rel else None,
                    "protocol": getattr(rel, "protocol", None) if rel else None,
                })
        return {
            "project": self.state.metadata.project,
            "generated_at": datetime.utcnow().isoformat(),
            "nodes": nodes,
            "edges": edges,
        }

    # ── ARCHITECTURE.md (human, renders on GitHub) ───────────────────────────

    def markdown(self) -> str:
        s = self.state
        md = [
            f"# {s.metadata.project} — Architecture",
            "",
            f"> Auto-generated by GyroCompass from source. "
            f"{len(s.architecture)} components · {len(s.capabilities)} capabilities · "
            f"{len(s.surface_area)} endpoints · {len(s.data_model.entities)} data entities.",
            "",
            "## System Diagram",
            "",
            "```mermaid",
            self.component_diagram(),
            "```",
            "",
        ]

        er = self.data_model_diagram()
        if er:
            md += ["## Data Model", "", "```mermaid", er, "```", ""]

        # Components
        md += ["## Components", ""]
        for cid, elem in s.architecture.items():
            etype = elem.type.value if isinstance(elem.type, ElementType) else str(elem.type)
            md.append(f"### `{cid}` — _{etype}_")
            if elem.description:
                md.append(elem.description)
            if elem.facts:
                md.append("")
                for fact in elem.facts[:8]:
                    md.append(f"- {fact}")
            if elem.relationships:
                md.append("")
                deps = ", ".join(f"`{t}`" for t in elem.relationships)
                md.append(f"**Depends on:** {deps}")
            md.append("")

        # Capabilities
        if s.capabilities:
            md += ["## Capabilities", "", "| Capability | Status | Description |", "|---|---|---|"]
            for cid, cap in s.capabilities.items():
                md.append(f"| `{cid}` | {cap.status} | {cap.description} |")
            md.append("")

        # API surface
        if s.surface_area:
            md += ["## API Surface", "", "| Endpoint | Auth | Summary |", "|---|---|---|"]
            for ep, info in list(s.surface_area.items())[:50]:
                auth = "🔒" if getattr(info, "auth_required", False) else "🔓"
                md.append(f"| `{ep}` | {auth} | {getattr(info, 'summary', '')} |")
            md.append("")

        # Tech stack
        if s.tech_stack:
            md += ["## Tech Stack", "", "| Technology | Type | Vendor |", "|---|---|---|"]
            for name, t in s.tech_stack.items():
                md.append(f"| {name} | {t.type} | {t.vendor or '—'} |")
            md.append("")

        md += ["---", "_Living documentation — re-run `gyro docs` after `gyro analyze --save` to refresh._"]
        return "\n".join(md)

    # ── Architecture diagram (dark SVG, in the architecture-diagram-generator style) ──

    # Semantic kind → (fill rgba, stroke, card-dot class). Palette + visual
    # language adopted from the MIT architecture-diagram-generator design system.
    _KIND_STYLE = {
        "frontend": ("rgba(8, 51, 68, 0.4)", "#22d3ee", "cyan"),
        "backend":  ("rgba(6, 78, 59, 0.4)", "#34d399", "emerald"),
        "database": ("rgba(76, 29, 149, 0.4)", "#a78bfa", "violet"),
        "cloud":    ("rgba(120, 53, 15, 0.3)", "#fbbf24", "amber"),
        "queue":    ("rgba(251, 146, 60, 0.3)", "#fb923c", "amber"),
        "security": ("rgba(136, 19, 55, 0.4)", "#fb7185", "rose"),
        "generic":  ("rgba(30, 41, 59, 0.5)", "#94a3b8", "rose"),
    }
    _KIND_LABEL = {
        "frontend": "Edge / Frontend", "backend": "Service / Backend",
        "database": "Data store", "cloud": "External / Cloud",
        "queue": "Message bus", "security": "Security", "generic": "Generic",
    }

    @staticmethod
    def _esc(t: str) -> str:
        return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def _classify(self, cid: str, etype: str) -> str:
        name = cid.lower()
        if etype in ("database", "cache"):
            return "database"
        if etype == "queue":
            return "queue"
        if etype == "external_system":
            return "cloud"
        if any(k in name for k in ("route", "edge", "handler", "frontend", "/web", "/ui", "dashboard", "gateway", "controller")):
            return "frontend"
        if any(k in name for k in ("client", "integration", "external", "third", "vendor", "provider")):
            return "cloud"
        if any(k in name for k in ("test", "/core", "util", "common", "config", "shared", "helper")):
            return "generic"
        if any(k in name for k in ("auth", "security", "iam")):
            return "security"
        return "backend"

    @staticmethod
    def _accent(elem) -> str:
        for f in (elem.facts or []):
            if f.startswith("Exposes") or "route" in f.lower() and "API" in f:
                return f.replace("Exposes ", "")[:22]
        for f in (elem.facts or []):
            if f.lower().startswith("languages:"):
                return f.split(":", 1)[1].strip()[:22]
        n = sum(1 for f in (elem.facts or []) if f.startswith("Implemented in"))
        return f"{n} file(s)" if n else ""

    def svg_diagram(self) -> str:
        from collections import defaultdict

        arch = self.state.architecture
        if not arch:
            return ('<svg viewBox="0 0 900 220"><rect width="100%" height="100%" fill="url(#grid)"/>'
                    '<text x="450" y="110" fill="#94a3b8" text-anchor="middle" font-size="13" '
                    'font-family="JetBrains Mono">No components extracted yet — run gyro analyze --save</text></svg>')

        # Relationship targets can be sanitized/prefixed (e.g. "uses_meridian_core"
        # for component "meridian/core"). Resolve them back to real component ids.
        san2real = {re.sub(r"[^0-9a-zA-Z]", "_", cid): cid for cid in arch}

        def _resolve(target: str) -> str | None:
            if target in arch:
                return target
            t = target
            for pref in ("uses_", "calls_", "depends_on_", "imports_", "connects_to_"):
                if t.startswith(pref):
                    t = t[len(pref):]
                    break
            return san2real.get(t) or san2real.get(re.sub(r"[^0-9a-zA-Z]", "_", t))

        rels: dict[str, list[str]] = {}
        for cid, e in arch.items():
            targets = []
            for t in (e.relationships or {}):
                r = _resolve(t)
                if r and r in arch and r != cid and r not in targets:
                    targets.append(r)
            rels[cid] = targets

        BW, BH, COLGAP, ROWGAP, MX, TOP = 172, 68, 104, 34, 44, 70
        total_edges = sum(len(v) for v in rels.values())

        pos: dict[str, tuple[float, float]] = {}
        if total_edges == 0:
            # No usable edges → clean grid (avoids a tall single column).
            import math

            ids = sorted(arch)
            ncols = max(1, math.ceil(math.sqrt(len(ids))))
            nrows = math.ceil(len(ids) / ncols)
            for i, cid in enumerate(ids):
                r, c = divmod(i, ncols)
                pos[cid] = (MX + c * (BW + COLGAP), TOP + r * (BH + ROWGAP))
            num_layers = ncols
            stack_h = nrows * BH + (nrows - 1) * ROWGAP
        else:
            # BFS-distance layering from source components (in-degree 0). This is
            # cycle-safe: each node is placed at its shortest distance from a
            # source, so cyclic dependencies don't inflate the column count.
            from collections import deque

            indeg = {cid: 0 for cid in arch}
            for _src, tgts in rels.items():
                for t in tgts:
                    indeg[t] += 1
            sources = [c for c in sorted(arch) if indeg[c] == 0]
            if not sources:  # fully cyclic — seed with the least-depended-on node
                sources = [min(sorted(arch), key=lambda c: indeg[c])]

            layer: dict[str, int] = {}
            q = deque((s, 0) for s in sources)
            while q:
                node, d = q.popleft()
                if node in layer:
                    continue
                layer[node] = d
                for t in rels.get(node, []):
                    if t not in layer:
                        q.append((t, d + 1))
            for c in sorted(arch):  # disconnected nodes → first column
                layer.setdefault(c, 0)

            # Wrap any over-tall column so the diagram stays readable.
            MAX_ROWS = 6
            layers: dict[int, list[str]] = defaultdict(list)
            for cid in sorted(arch, key=lambda c: (layer[c], c)):
                layers[layer[cid]].append(cid)

            # Build ordered columns, splitting columns taller than MAX_ROWS.
            columns: list[list[str]] = []
            for L in sorted(layers):
                col = layers[L]
                for i in range(0, len(col), MAX_ROWS):
                    columns.append(col[i:i + MAX_ROWS])

            num_layers = len(columns)
            max_rows = max(len(c) for c in columns)
            stack_h = max_rows * BH + (max_rows - 1) * ROWGAP
            for ci, col in enumerate(columns):
                total = len(col) * BH + (len(col) - 1) * ROWGAP
                start_y = TOP + (stack_h - total) / 2
                x = MX + ci * (BW + COLGAP)
                for i, cid in enumerate(col):
                    pos[cid] = (x, start_y + i * (BH + ROWGAP))

        W = MX * 2 + num_layers * BW + (num_layers - 1) * COLGAP
        H = TOP + stack_h + 56

        p = [f'<svg viewBox="0 0 {int(W)} {int(H)}">']
        p.append('<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" '
                 'orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/></marker>'
                 '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
                 '<path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/></pattern></defs>')
        p.append('<rect width="100%" height="100%" fill="url(#grid)"/>')

        # Arrows first (so boxes paint over them).
        for src, tgts in rels.items():
            x1, y1 = pos[src]
            for dst in tgts:
                x2, y2 = pos[dst]
                if x2 > x1:  # forward edge → straight line
                    sx, sy = x1 + BW, y1 + BH / 2
                    tx, ty = x2 - 2, y2 + BH / 2
                    p.append(f'<line x1="{sx:.0f}" y1="{sy:.0f}" x2="{tx:.0f}" y2="{ty:.0f}" '
                             f'stroke="#475569" stroke-width="1.4" marker-end="url(#arrowhead)"/>')
                else:  # back/same edge → dashed bezier arcing over the top
                    sx, sy = x1 + BW / 2, y1
                    tx, ty = x2 + BW / 2, y2
                    midy = min(sy, ty) - 46
                    p.append(f'<path d="M {sx:.0f} {sy:.0f} C {sx:.0f} {midy:.0f}, {tx:.0f} {midy:.0f}, '
                             f'{tx:.0f} {ty:.0f}" fill="none" stroke="#475569" stroke-width="1.2" '
                             f'stroke-dasharray="4,3" marker-end="url(#arrowhead)"/>')

        # Boxes: opaque mask rect (so arrows don't bleed through) + styled rect + labels.
        for cid, (x, y) in pos.items():
            elem = arch[cid]
            etype = elem.type.value if isinstance(elem.type, ElementType) else str(elem.type)
            kind = self._classify(cid, etype)
            fill, stroke, _ = self._KIND_STYLE[kind]
            label = cid.split("/")[-1]
            accent = self._accent(elem)
            cx = x + BW / 2
            p.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="6" fill="#0f172a"/>')
            p.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" rx="6" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
            p.append(f'<text x="{cx:.0f}" y="{y + 26:.0f}" fill="white" font-size="12" '
                     f'font-weight="600" text-anchor="middle">{self._esc(label)}</text>')
            p.append(f'<text x="{cx:.0f}" y="{y + 43:.0f}" fill="#94a3b8" font-size="9" '
                     f'text-anchor="middle">{self._esc(etype)}</text>')
            if accent:
                p.append(f'<text x="{cx:.0f}" y="{y + 58:.0f}" fill="{stroke}" font-size="7.5" '
                         f'text-anchor="middle">{self._esc(accent)}</text>')

        # Legend (only kinds present).
        present = []
        for cid in arch:
            etype = arch[cid].type.value if isinstance(arch[cid].type, ElementType) else str(arch[cid].type)
            k = self._classify(cid, etype)
            if k not in present:
                present.append(k)
        lx, ly = MX, H - 24
        for k in present:
            fill, stroke, _ = self._KIND_STYLE[k]
            lab = self._KIND_LABEL[k]
            p.append(f'<rect x="{lx:.0f}" y="{ly:.0f}" width="16" height="10" rx="2" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>')
            p.append(f'<text x="{lx + 22:.0f}" y="{ly + 9:.0f}" fill="#94a3b8" font-size="8">{lab}</text>')
            lx += 22 + len(lab) * 5.4 + 26

        p.append("</svg>")
        return "\n".join(p)

    # ── Summary cards + evolution (their .card markup) ───────────────────────

    def _summary_cards(self) -> str:
        s = self.state
        cards = []

        comps = list(s.architecture.items())[:7]
        comp_items = "".join(
            f"<li>• {self._esc(cid.split('/')[-1])} "
            f"<span style='color:#475569'>{self._esc(e.type.value if isinstance(e.type, ElementType) else str(e.type))}</span></li>"
            for cid, e in comps
        )
        cards.append(f'<div class="card"><div class="card-header"><div class="card-dot emerald"></div>'
                     f'<h3>Components ({len(s.architecture)})</h3></div><ul>{comp_items}</ul></div>')

        if s.capabilities:
            cap_items = "".join(
                f"<li>• {self._esc(c.description)}</li>" for c in list(s.capabilities.values())[:7]
            )
            cards.append(f'<div class="card"><div class="card-header"><div class="card-dot cyan"></div>'
                         f'<h3>Capabilities ({len(s.capabilities)})</h3></div><ul>{cap_items}</ul></div>')

        if s.tech_stack:
            tech_items = "".join(
                f"<li>• {self._esc(name)} <span style='color:#475569'>{self._esc(t.type)}</span></li>"
                for name, t in list(s.tech_stack.items())[:8]
            )
            cards.append(f'<div class="card"><div class="card-header"><div class="card-dot amber"></div>'
                         f'<h3>Tech stack ({len(s.tech_stack)})</h3></div><ul>{tech_items}</ul></div>')

        return "\n".join(cards)

    _SEV_COLOR = {"critical": "#fb7185", "high": "#fb923c", "medium": "#fbbf24",
                  "low": "#34d399", "info": "#94a3b8", "ok": "#22d3ee"}
    _SEV_CLASS = {"critical": "crit", "high": "high", "medium": "med",
                  "low": "low", "info": "info", "ok": "info"}

    _MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def _fmt_date(self, iso: str) -> str:
        """'2026-06-28T...' → 'Jun 28, 2026' (readable, no time noise)."""
        try:
            y, m, d = iso[:10].split("-")
            return f"{self._MONTHS[int(m)]} {int(d)}, {y}"
        except Exception:
            return iso[:10]

    def _evolution_html(self) -> str:
        """A professional architecture changelog: primitive-movement bars + a
        chronological timeline. Reads like a git log for the system's design."""
        if not self.timeline_items:
            return ""

        events = [it for it in self.timeline_items if it.get("start")]
        events.sort(key=lambda x: x.get("start", ""), reverse=True)

        # ── Primitive movement: how much each dimension moved over the period ──
        from collections import Counter

        prim_counts: Counter = Counter()
        for it in events:
            prim = it.get("primitive") or "Architecture"
            if prim not in ("Baseline",):
                prim_counts[prim] += 1
        max_count = max(prim_counts.values(), default=1)

        # Show the eight tracked primitives in a stable order.
        primitive_order = ["Architecture", "API surface", "Data flows", "Data model",
                           "Rules", "Capabilities", "Tech stack", "Remediation"]
        bars = []
        for prim in primitive_order:
            cnt = prim_counts.get(prim, 0)
            if cnt == 0 and prim not in ("Architecture", "Rules", "API surface"):
                continue  # hide untouched, less-central primitives to reduce noise
            color = _PRIMITIVE_COLOR.get(prim, "#64748b")
            pct = int(round((cnt / max_count) * 100)) if max_count else 0
            bars.append(
                f'<div class="prim"><span class="prim-label">{prim}</span>'
                f'<div class="prim-bar"><div class="prim-fill" style="width:{pct}%;background:{color}"></div></div>'
                f'<span class="prim-count">{cnt}</span></div>'
            )
        bars_html = f'<div class="primitives">{"".join(bars)}</div>'

        # ── Timeline: chronological spine of architectural changes ────────────
        rows = []
        for it in events[:14]:
            sev = it.get("severity", "info")
            sev_color = self._SEV_COLOR.get(sev, "#94a3b8")
            sev_cls = self._SEV_CLASS.get(sev, "info")
            date = self._fmt_date(it.get("start", ""))
            pr = it.get("pr")
            pr_html = f'<span class="tl-pr">#{pr}</span>' if pr else ""
            prim = it.get("primitive")
            tag_html = ""
            if prim and prim not in ("Baseline",):
                tcolor = _PRIMITIVE_COLOR.get(prim, "#64748b")
                tag_html = f'<span class="tag" style="border-color:{tcolor}55;color:{tcolor}">{prim}</span>'
            sev_badge = ("" if sev in ("info", "ok")
                         else f'<span class="tl-sev {sev_cls}">{sev}</span>')
            file_html = (f'<span class="tl-file">{self._esc(it["file"])}</span>'
                         if it.get("file") else "")
            rows.append(
                f'<div class="tl-item"><div class="tl-dot" style="background:{sev_color}"></div>'
                f'<div class="tl-content">'
                f'<div class="tl-meta"><span class="tl-date">{date}</span>{pr_html}{sev_badge}</div>'
                f'<div class="tl-title">{self._esc(it.get("content", ""))}</div>'
                f'<div class="tl-tags">{tag_html}{file_html}</div>'
                f'</div></div>'
            )
        timeline_html = f'<div class="timeline">{"".join(rows)}</div>'

        return (
            '<div class="card" style="grid-column:1/-1">'
            '<div class="card-header"><div class="card-dot violet"></div>'
            '<h3>Architecture evolution</h3></div>'
            '<p style="color:#64748b;font-size:.72rem;margin:-.4rem 0 1rem">'
            'How the system has moved over time — which primitives changed, and why.</p>'
            f'{bars_html}{timeline_html}</div>'
        )

    # ── Standalone HTML (architecture-diagram-generator style) ───────────────

    def html(self) -> str:
        s = self.state
        subtitle = (f"{len(s.architecture)} components · {len(s.capabilities)} capabilities · "
                    f"{len(s.surface_area)} endpoints · {len(s.data_model.entities)} data entities "
                    f"· auto-generated by GyroCompass")
        footer = f"{self._esc(s.metadata.project)} · architecture extracted from source · GyroCompass"
        return (_DIAGRAM_DOC
                .replace("__TITLE__", self._esc(s.metadata.project))
                .replace("__SUBTITLE__", subtitle)
                .replace("__SVG__", self.svg_diagram())
                .replace("__CARDS__", self._summary_cards())
                .replace("__EVOLUTION__", self._evolution_html())
                .replace("__FOOTER__", footer))


    # ── Write all artifacts ──────────────────────────────────────────────────

    def write_all(self, out_dir: Path) -> dict[str, Path]:
        """Write ARCHITECTURE.md, architecture.html/.mmd/.json. Returns paths."""
        out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}

        md_path = out_dir / "ARCHITECTURE.md"
        md_path.write_text(self.markdown(), encoding="utf-8")
        written["markdown"] = md_path

        mmd_path = out_dir / "architecture.mmd"
        mmd_path.write_text(self.component_diagram(), encoding="utf-8")
        written["mermaid"] = mmd_path

        html_path = out_dir / "architecture.html"
        html_path.write_text(self.html(), encoding="utf-8")
        written["html"] = html_path

        json_path = out_dir / "architecture.json"
        json_path.write_text(json.dumps(self.graph_json(), indent=2), encoding="utf-8")
        written["json"] = json_path

        logger.debug("Wrote {} doc artifacts to {}", len(written), out_dir)
        return written


def build_timeline_from_memory(repo_path: Path | str | None, install_label: str | None = None) -> list[dict]:
    """Build drift-evolution timeline items from the architectural memory layer.

    Each drift event becomes a timeline point (colored by severity); the first
    item marks when GyroCompass started watching. Returns [] if memory is empty
    or unavailable.
    """
    try:
        from gyrocompass.memory import MemoryStore, NodeType
    except Exception:
        return []

    items: list[dict] = []
    try:
        store = MemoryStore(repo_path)
        try:
            drift_nodes = store.list_nodes(type=NodeType.drift_event.value, limit=200)
            for n in drift_nodes:
                meta = n.metadata or {}
                items.append({
                    "id": n.id,
                    "content": n.title,
                    "start": (n.created_at or "")[:19] or None,
                    "severity": n.severity or "info",
                    "pr": meta.get("pr"),
                    "file": meta.get("file"),
                    "primitive": _PRIMITIVE_FOR_DRIFT.get(meta.get("drift_type", ""), "Architecture"),
                    "title": (n.body or n.title),
                })
            # Add an "installed" anchor only if the history doesn't already have
            # one (avoids a duplicate, today-dated cell on the calendar).
            already_anchored = any(
                "install" in (it["content"] or "").lower()
                or "baseline" in (it["content"] or "").lower()
                for it in items
            )
            if not already_anchored:
                activity = store.activity(limit=500)
                if activity:
                    first = activity[-1]  # activity() is newest-first
                    items.append({
                        "id": "gyro-installed",
                        "content": install_label or "🧭 GyroCompass installed — baseline captured",
                        "start": (first.get("created_at") or "")[:19] or None,
                        "severity": "ok",
                        "title": "GyroCompass started watching this repository.",
                    })
        finally:
            store.close()
    except Exception as exc:
        logger.debug("timeline build skipped: {}", exc)
        return []

    return [it for it in items if it.get("start")]
