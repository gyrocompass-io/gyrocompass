"""
GyroCompass Code Indexer — Tree-sitter-powered codebase parser.

Walks a repository, parses every supported source file using Tree-sitter
(v0.25.0 API), and emits a fully-populated ArchitectureState model.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

from gyrocompass.models import (
    ApiEndpoint,
    ArchitectureElement,
    ArchitectureState,
    C4Depth,
    Capability,
    DataAttribute,
    DataEntity,
    DataModel,
    ElementStatus,
    ElementType,
    FileMapping,
    Relationship,
    RelationType,
    StateMetadata,
    TechStackItem,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_FILE_SIZE = 500_000  # bytes

# File extension → language name
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

# Default directories to always exclude
DEFAULT_EXCLUDES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        ".next",
        "target",
        ".cargo",
        ".gradle",
        ".idea",
        ".vscode",
        "coverage",
        ".nyc_output",
        "htmlcov",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

# Tech stack detection rules
# key → (display_name, type, vendor, version_key_in_manifest)
TECH_STACK_RULES: dict[str, tuple[str, str, str]] = {
    # JS/TS
    "next": ("Next.js", "framework", "Vercel"),
    "react": ("React", "framework", "Meta"),
    "vue": ("Vue", "framework", "Evan You"),
    "svelte": ("Svelte", "framework", "Rich Harris"),
    "express": ("Express", "framework", "OpenJS Foundation"),
    "fastify": ("Fastify", "framework", "Fastify"),
    "hono": ("Hono", "framework", "Yusuke Wada"),
    "nestjs": ("NestJS", "framework", "Kamil Mysliwiec"),
    "@nestjs/core": ("NestJS", "framework", "Kamil Mysliwiec"),
    "koa": ("Koa", "framework", "OpenJS Foundation"),
    "prisma": ("Prisma", "orm", "Prisma"),
    "@prisma/client": ("Prisma", "orm", "Prisma"),
    "mongoose": ("Mongoose", "orm", "MongoDB"),
    "typeorm": ("TypeORM", "orm", "TypeORM"),
    "drizzle-orm": ("Drizzle ORM", "orm", "Drizzle Team"),
    "graphql": ("GraphQL", "protocol", "GraphQL Foundation"),
    "apollo-server": ("Apollo Server", "framework", "Apollo GraphQL"),
    "@apollo/server": ("Apollo Server", "framework", "Apollo GraphQL"),
    "trpc": ("tRPC", "framework", "Alex Johansson"),
    "@trpc/server": ("tRPC", "framework", "Alex Johansson"),
    "socket.io": ("Socket.IO", "library", "Socket.IO"),
    "redis": ("Redis", "database", "Redis Ltd"),
    "ioredis": ("ioredis", "library", "Zihua Li"),
    "pg": ("PostgreSQL", "database", "PostgreSQL Global Development Group"),
    "mysql2": ("MySQL", "database", "Oracle"),
    "better-sqlite3": ("SQLite", "database", "Ben Johnson"),
    "jest": ("Jest", "tool", "Meta"),
    "vitest": ("Vitest", "tool", "Evan You"),
    "playwright": ("Playwright", "tool", "Microsoft"),
    "tailwindcss": ("Tailwind CSS", "framework", "Tailwind Labs"),
    "vite": ("Vite", "tool", "Evan You"),
    "webpack": ("Webpack", "tool", "JS Foundation"),
    "turbo": ("Turborepo", "tool", "Vercel"),
    "zod": ("Zod", "library", "Colin McDonnell"),
    # Python (requirements/pyproject)
    "fastapi": ("FastAPI", "framework", "Sebastián Ramírez"),
    "flask": ("Flask", "framework", "Pallets Projects"),
    "django": ("Django", "framework", "Django Software Foundation"),
    "starlette": ("Starlette", "framework", "Encode"),
    "litestar": ("Litestar", "framework", "Litestar Org"),
    "sqlalchemy": ("SQLAlchemy", "orm", "SQLAlchemy"),
    "alembic": ("Alembic", "tool", "SQLAlchemy"),
    "tortoise-orm": ("Tortoise ORM", "orm", "Tortoise ORM"),
    "pydantic": ("Pydantic", "library", "Pydantic"),
    "celery": ("Celery", "library", "Celery Project"),
    "dramatiq": ("Dramatiq", "library", "Bogdan Popa"),
    "arq": ("ARQ", "library", "Samuel Colvin"),
    "redis-py": ("redis-py", "library", "Redis Ltd"),
    "httpx": ("HTTPX", "library", "Encode"),
    "aiohttp": ("aiohttp", "library", "aio-libs"),
    "pytest": ("pytest", "tool", "pytest-dev"),
    "uvicorn": ("Uvicorn", "server", "Encode"),
    "gunicorn": ("Gunicorn", "server", "Benoît Chesneau"),
    "grpcio": ("gRPC", "protocol", "Google"),
    "boto3": ("AWS SDK", "library", "Amazon"),
    "anthropic": ("Anthropic SDK", "library", "Anthropic"),
    "openai": ("OpenAI SDK", "library", "OpenAI"),
    "langchain": ("LangChain", "library", "LangChain"),
    "langgraph": ("LangGraph", "library", "LangChain"),
}

# Capability keyword heuristics
CAPABILITY_KEYWORDS: dict[str, str] = {
    "auth": "Authentication and authorization",
    "authentication": "Authentication and authorization",
    "authorization": "Authorization and access control",
    "payment": "Payment processing",
    "billing": "Billing and subscription management",
    "notification": "Notification delivery",
    "email": "Email communication",
    "search": "Search functionality",
    "analytics": "Analytics and reporting",
    "reporting": "Reporting and dashboards",
    "export": "Data export",
    "import": "Data import",
    "upload": "File upload handling",
    "storage": "File and blob storage",
    "cache": "Caching layer",
    "queue": "Message queue processing",
    "worker": "Background job processing",
    "scheduler": "Task scheduling",
    "webhook": "Webhook delivery",
    "api": "External API integration",
    "graphql": "GraphQL API",
    "websocket": "Real-time WebSocket communication",
    "user": "User management",
    "profile": "User profile management",
    "admin": "Administrative interface",
    "dashboard": "Dashboard UI",
    "monitor": "System monitoring",
    "logging": "Audit logging",
    "audit": "Audit trail",
    "migration": "Database migration",
    "seed": "Data seeding",
    "health": "Health check endpoints",
    "metric": "Metrics collection",
    "trace": "Distributed tracing",
}


# ── ParsedSymbol ──────────────────────────────────────────────────────────────


@dataclass
class ParsedSymbol:
    name: str
    kind: str  # "class", "function", "route", "interface", "model", "import", "struct", "type"
    file: str
    line: int
    language: str
    docstring: str | None = None
    imports: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    parent: str | None = None  # parent class/module name
    metadata: dict = field(default_factory=dict)  # e.g., HTTP method, path for routes


# ── Tree-sitter language loader ───────────────────────────────────────────────


def _load_ts_language(language: str):
    """
    Load a Tree-sitter Language object for the given language name.

    Tree-sitter v0.25.0 uses per-language packages that expose a
    ``language()`` function returning a ``Language`` instance directly.
    Returns None if the grammar is not installed.
    """
    try:
        if language == "python":
            import tree_sitter_python as tsp
            from tree_sitter import Language

            return Language(tsp.language())
        if language in ("javascript",):
            import tree_sitter_javascript as tsjs
            from tree_sitter import Language

            return Language(tsjs.language())
        if language == "typescript":
            import tree_sitter_typescript as tsts
            from tree_sitter import Language

            # tree_sitter_typescript exposes two: typescript and tsx
            return Language(tsts.language_typescript())
        if language == "go":
            import tree_sitter_go as tsgo
            from tree_sitter import Language

            return Language(tsgo.language())
        if language == "rust":
            import tree_sitter_rust as tsrust
            from tree_sitter import Language

            return Language(tsrust.language())
        if language == "java":
            import tree_sitter_java as tsjava
            from tree_sitter import Language

            return Language(tsjava.language())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tree-sitter grammar for '{}' unavailable: {}", language, exc)
    return None


# ── Language-specific parsers ─────────────────────────────────────────────────


def _text(node) -> str:
    """Extract UTF-8 text from a Tree-sitter node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _child_text(node, field_name: str) -> str | None:
    child = node.child_by_field_name(field_name)
    return _text(child) if child else None


def _find_nodes(node, *types: str):
    """BFS traversal yielding all descendant nodes matching any of the given types."""
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if n.type in types:
            yield n
        queue.extend(n.children)


def _extract_python_docstring(node) -> str | None:
    """Pull the first string literal from a function/class body."""
    for child in node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for sub in stmt.children:
                        if sub.type in ("string", "string_content"):
                            raw = _text(sub).strip("\"'").strip()
                            return raw[:500] if raw else None
            break
    return None


def _parse_python(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Parse Python source; extract classes, functions, routes, imports."""
    from tree_sitter import Parser

    lang = _load_ts_language("python")
    if lang is None:
        return _parse_python_regex(source, file_path)

    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[ParsedSymbol] = []

    # Collect top-level imports
    import_names: list[str] = []
    for node in _find_nodes(root, "import_statement", "import_from_statement"):
        import_names.append(_text(node).strip())

    # Helper: extract decorator names from decorated_definition
    def get_decorators(node) -> list[str]:
        decs = []
        for child in node.children:
            if child.type == "decorator":
                decs.append(_text(child).lstrip("@").strip())
        return decs

    def visit(node, parent_name: str | None = None):
        if node.type in ("class_definition",):
            name_node = node.child_by_field_name("name")
            class_name = _text(name_node) if name_node else "Unknown"
            doc = _extract_python_docstring(node)
            decorators = get_decorators(node.parent) if node.parent else []

            # Classify: Pydantic/SQLAlchemy model vs plain class
            bases_node = node.child_by_field_name("superclasses")
            bases_text = _text(bases_node) if bases_node else ""
            kind = "class"
            if any(
                b in bases_text
                for b in ("BaseModel", "SQLModel", "Base", "Model", "db.Model")
            ):
                kind = "model"

            symbols.append(
                ParsedSymbol(
                    name=class_name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="python",
                    docstring=doc,
                    imports=import_names,
                    decorators=decorators,
                    parent=parent_name,
                    metadata={"bases": bases_text},
                )
            )
            # Recurse into class body
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    visit(child, class_name)

        elif node.type in ("function_definition", "async_function_definition"):
            name_node = node.child_by_field_name("name")
            func_name = _text(name_node) if name_node else "unknown"
            doc = _extract_python_docstring(node)

            # Decorated definition parent gives decorators
            parent_node = node.parent
            decorators = []
            if parent_node and parent_node.type == "decorated_definition":
                decorators = get_decorators(parent_node)

            # Route detection: @app.get, @router.post, etc.
            kind = "function"
            http_method = None
            route_path = None
            for dec in decorators:
                m = re.match(
                    r"(?:app|router|blueprint|api)\."
                    r"(get|post|put|patch|delete|head|options|websocket|ws)\s*\(\s*['\"]([^'\"]+)['\"]",
                    dec,
                    re.IGNORECASE,
                )
                if m:
                    kind = "route"
                    http_method = m.group(1).upper()
                    route_path = m.group(2)
                    break

            symbols.append(
                ParsedSymbol(
                    name=func_name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="python",
                    docstring=doc,
                    imports=import_names,
                    decorators=decorators,
                    parent=parent_name,
                    metadata={
                        "http_method": http_method,
                        "route_path": route_path,
                    },
                )
            )

        elif node.type in ("import_statement", "import_from_statement"):
            pass  # already handled above

        else:
            for child in node.children:
                visit(child, parent_name)

    for child in root.children:
        visit(child, None)

    return symbols


def _parse_python_regex(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Fallback Python parser using regex (no tree-sitter)."""
    symbols: list[ParsedSymbol] = []
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()

    import_names = [
        l.strip() for l in lines if l.startswith(("import ", "from "))
    ]

    decorator_buffer: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("@"):
            decorator_buffer.append(stripped.lstrip("@"))
        else:
            m_class = re.match(r"^class\s+(\w+)", stripped)
            m_func = re.match(r"^(?:async\s+)?def\s+(\w+)", stripped)
            if m_class:
                symbols.append(
                    ParsedSymbol(
                        name=m_class.group(1),
                        kind="class",
                        file=file_path,
                        line=i,
                        language="python",
                        imports=import_names,
                        decorators=decorator_buffer[:],
                    )
                )
                decorator_buffer.clear()
            elif m_func:
                kind = "function"
                http_method = None
                route_path = None
                for dec in decorator_buffer:
                    m = re.match(
                        r"(?:app|router|blueprint|api)\."
                        r"(get|post|put|patch|delete|head|options|websocket)\s*\(\s*['\"]([^'\"]+)['\"]",
                        dec,
                        re.IGNORECASE,
                    )
                    if m:
                        kind = "route"
                        http_method = m.group(1).upper()
                        route_path = m.group(2)
                        break
                symbols.append(
                    ParsedSymbol(
                        name=m_func.group(1),
                        kind=kind,
                        file=file_path,
                        line=i,
                        language="python",
                        imports=import_names,
                        decorators=decorator_buffer[:],
                        metadata={"http_method": http_method, "route_path": route_path},
                    )
                )
                decorator_buffer.clear()
            else:
                if stripped and not stripped.startswith("#"):
                    decorator_buffer.clear()

    return symbols


def _parse_typescript_javascript(
    source: bytes, file_path: str, language: str
) -> list[ParsedSymbol]:
    """Parse TypeScript/JavaScript; extract classes, functions, routes, interfaces."""
    from tree_sitter import Parser

    lang = _load_ts_language(language)
    if lang is None:
        return _parse_ts_js_regex(source, file_path, language)

    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[ParsedSymbol] = []

    import_names: list[str] = []
    for node in _find_nodes(root, "import_declaration", "import_statement"):
        import_names.append(_text(node).strip())

    def visit(node, parent_name: str | None = None):
        t = node.type

        # TypeScript interface
        if t == "interface_declaration":
            name_node = node.child_by_field_name("name")
            iface_name = _text(name_node) if name_node else "Unknown"
            symbols.append(
                ParsedSymbol(
                    name=iface_name,
                    kind="interface",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language=language,
                    imports=import_names,
                    parent=parent_name,
                )
            )

        # TypeScript type alias
        elif t == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            type_name = _text(name_node) if name_node else "Unknown"
            symbols.append(
                ParsedSymbol(
                    name=type_name,
                    kind="type",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language=language,
                    imports=import_names,
                    parent=parent_name,
                )
            )

        # Class declaration (React components, services)
        elif t == "class_declaration":
            name_node = node.child_by_field_name("name")
            class_name = _text(name_node) if name_node else "Unknown"
            heritage = node.child_by_field_name("heritage")
            bases = _text(heritage) if heritage else ""
            symbols.append(
                ParsedSymbol(
                    name=class_name,
                    kind="class",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language=language,
                    imports=import_names,
                    parent=parent_name,
                    metadata={"bases": bases},
                )
            )
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    visit(child, class_name)

        # Function declarations / arrow functions
        elif t in (
            "function_declaration",
            "export_statement",
            "lexical_declaration",
            "variable_declaration",
        ):
            # Try to extract named functions / components
            for fn_node in _find_nodes(
                node,
                "function_declaration",
                "function",
                "arrow_function",
            ):
                # Get the name from a variable declarator or function name
                name = None
                fn_parent = fn_node.parent
                if fn_parent and fn_parent.type == "variable_declarator":
                    name_n = fn_parent.child_by_field_name("name")
                    name = _text(name_n) if name_n else None
                elif fn_node.type == "function_declaration":
                    name_n = fn_node.child_by_field_name("name")
                    name = _text(name_n) if name_n else None

                if not name:
                    continue

                # React component heuristic: PascalCase name
                kind = "function"
                if re.match(r"^[A-Z]", name):
                    kind = "component"

                symbols.append(
                    ParsedSymbol(
                        name=name,
                        kind=kind,
                        file=file_path,
                        line=fn_node.start_point[0] + 1,
                        language=language,
                        imports=import_names,
                        parent=parent_name,
                    )
                )
            # Also capture Express route calls: app.get("/path", handler)
            for call_node in _find_nodes(node, "call_expression"):
                _extract_express_route(call_node, file_path, language, import_names, symbols)

        elif t == "expression_statement":
            for call_node in _find_nodes(node, "call_expression"):
                _extract_express_route(call_node, file_path, language, import_names, symbols)

        else:
            for child in node.children:
                visit(child, parent_name)

    for child in root.children:
        visit(child, None)

    return symbols


def _extract_express_route(
    call_node,
    file_path: str,
    language: str,
    import_names: list[str],
    symbols: list[ParsedSymbol],
) -> None:
    """Extract Express-style route: app.get('/path', handler) or router.post(...)"""
    call_text = _text(call_node)
    m = re.match(
        r"""(?:app|router|api|server)\s*\.\s*(get|post|put|patch|delete|head|options|all)\s*\(\s*['"`]([^'"`]+)['"`]""",
        call_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        http_method = m.group(1).upper()
        route_path = m.group(2)
        func_name = f"{http_method}_{route_path.replace('/', '_').strip('_') or 'root'}"
        symbols.append(
            ParsedSymbol(
                name=func_name,
                kind="route",
                file=file_path,
                line=call_node.start_point[0] + 1,
                language=language,
                imports=import_names,
                metadata={"http_method": http_method, "route_path": route_path},
            )
        )


def _parse_ts_js_regex(source: bytes, file_path: str, language: str) -> list[ParsedSymbol]:
    """Fallback TS/JS parser using regex."""
    symbols: list[ParsedSymbol] = []
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    import_names = [l.strip() for l in lines if l.strip().startswith("import ")]

    for i, line in enumerate(lines, 1):
        s = line.strip()
        # Interface
        m = re.match(r"(?:export\s+)?interface\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="interface", file=file_path,
                             line=i, language=language, imports=import_names)
            )
            continue
        # Type alias
        m = re.match(r"(?:export\s+)?type\s+(\w+)\s*=", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="type", file=file_path,
                             line=i, language=language, imports=import_names)
            )
            continue
        # Class
        m = re.match(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="class", file=file_path,
                             line=i, language=language, imports=import_names)
            )
            continue
        # Function / arrow
        m = re.match(
            r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", s
        )
        if m:
            name = m.group(1)
            kind = "component" if re.match(r"^[A-Z]", name) else "function"
            symbols.append(
                ParsedSymbol(name=name, kind=kind, file=file_path,
                             line=i, language=language, imports=import_names)
            )
            continue
        # Express routes
        m = re.match(
            r"(?:app|router|api|server)\.(get|post|put|patch|delete|head|options)\s*\(\s*['\"`]([^'\"` ]+)['\"`]",
            s, re.IGNORECASE,
        )
        if m:
            http_method = m.group(1).upper()
            route_path = m.group(2)
            func_name = f"{http_method}_{route_path.replace('/', '_').strip('_') or 'root'}"
            symbols.append(
                ParsedSymbol(
                    name=func_name, kind="route", file=file_path, line=i,
                    language=language, imports=import_names,
                    metadata={"http_method": http_method, "route_path": route_path},
                )
            )

    return symbols


def _parse_go(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Parse Go source; extract structs, functions, HTTP handlers."""
    from tree_sitter import Parser

    lang = _load_ts_language("go")
    if lang is None:
        return _parse_go_regex(source, file_path)

    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[ParsedSymbol] = []

    import_names: list[str] = []
    for node in _find_nodes(root, "import_declaration"):
        import_names.append(_text(node).strip())

    for node in root.children:
        if node.type == "type_declaration":
            for spec in _find_nodes(node, "type_spec"):
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node and type_node:
                    kind = "struct" if type_node.type == "struct_type" else "type"
                    symbols.append(
                        ParsedSymbol(
                            name=_text(name_node),
                            kind=kind,
                            file=file_path,
                            line=node.start_point[0] + 1,
                            language="go",
                            imports=import_names,
                        )
                    )

        elif node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            func_name = _text(name_node) if name_node else "unknown"

            # Detect HTTP handler: func(w http.ResponseWriter, r *http.Request)
            params_node = node.child_by_field_name("parameters")
            params_text = _text(params_node) if params_node else ""
            kind = "route" if "ResponseWriter" in params_text else "function"

            symbols.append(
                ParsedSymbol(
                    name=func_name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="go",
                    imports=import_names,
                    metadata={"params": params_text},
                )
            )

    return symbols


def _parse_go_regex(source: bytes, file_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    text = source.decode("utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        m = re.match(r"type\s+(\w+)\s+struct", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="struct", file=file_path,
                             line=i, language="go")
            )
            continue
        m = re.match(r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", s)
        if m:
            func_name = m.group(1)
            kind = "route" if "ResponseWriter" in line else "function"
            symbols.append(
                ParsedSymbol(name=func_name, kind=kind, file=file_path,
                             line=i, language="go")
            )
    return symbols


def _parse_rust(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Parse Rust source; extract structs, enums, functions."""
    from tree_sitter import Parser

    lang = _load_ts_language("rust")
    if lang is None:
        return _parse_rust_regex(source, file_path)

    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[ParsedSymbol] = []

    import_names: list[str] = []
    for node in _find_nodes(root, "use_declaration"):
        import_names.append(_text(node).strip())

    for node in _find_nodes(
        root, "struct_item", "enum_item", "function_item", "impl_item"
    ):
        if node.type == "struct_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append(
                    ParsedSymbol(
                        name=_text(name_node),
                        kind="struct",
                        file=file_path,
                        line=node.start_point[0] + 1,
                        language="rust",
                        imports=import_names,
                    )
                )
        elif node.type == "enum_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append(
                    ParsedSymbol(
                        name=_text(name_node),
                        kind="type",
                        file=file_path,
                        line=node.start_point[0] + 1,
                        language="rust",
                        imports=import_names,
                    )
                )
        elif node.type == "function_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                # Detect route handlers: attribute macros like #[get("/path")]
                attrs = []
                n = node
                while n.parent and n.parent.type in (
                    "attribute_item", "inner_attribute_item", "declaration_list"
                ):
                    n = n.parent
                # check siblings for attribute_item
                if n.parent:
                    siblings = n.parent.children
                    idx = siblings.index(n) if n in siblings else -1
                    for sib in (siblings[idx - 1 : idx] if idx > 0 else []):
                        if sib.type == "attribute_item":
                            attrs.append(_text(sib))

                kind = "function"
                http_method = None
                route_path = None
                for attr in attrs:
                    m = re.search(
                        r'#\[(?:axum::)?(?:routing::)?(get|post|put|patch|delete)\s*\(\s*"([^"]+)"',
                        attr,
                    )
                    if m:
                        kind = "route"
                        http_method = m.group(1).upper()
                        route_path = m.group(2)
                        break

                symbols.append(
                    ParsedSymbol(
                        name=_text(name_node),
                        kind=kind,
                        file=file_path,
                        line=node.start_point[0] + 1,
                        language="rust",
                        imports=import_names,
                        metadata={"http_method": http_method, "route_path": route_path},
                    )
                )

    return symbols


def _parse_rust_regex(source: bytes, file_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    text = source.decode("utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        m = re.match(r"(?:pub\s+)?struct\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="struct", file=file_path,
                             line=i, language="rust")
            )
            continue
        m = re.match(r"(?:pub\s+)?enum\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="type", file=file_path,
                             line=i, language="rust")
            )
            continue
        m = re.match(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="function", file=file_path,
                             line=i, language="rust")
            )
    return symbols


def _parse_java(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Parse Java source; extract classes, interfaces, methods."""
    from tree_sitter import Parser

    lang = _load_ts_language("java")
    if lang is None:
        return _parse_java_regex(source, file_path)

    parser = Parser(lang)
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[ParsedSymbol] = []

    import_names: list[str] = []
    for node in _find_nodes(root, "import_declaration"):
        import_names.append(_text(node).strip())

    def visit(node, parent_name: str | None = None):
        if node.type in ("class_declaration", "interface_declaration"):
            name_node = node.child_by_field_name("name")
            type_name = _text(name_node) if name_node else "Unknown"
            kind = "interface" if node.type == "interface_declaration" else "class"

            # Spring annotations
            modifiers = node.child_by_field_name("modifiers")
            annotations = []
            if modifiers:
                for m in _find_nodes(modifiers, "marker_annotation", "annotation"):
                    annotations.append(_text(m))

            # Detect Spring RestController / Controller
            is_controller = any(
                a in ("@RestController", "@Controller") for a in annotations
            )

            symbols.append(
                ParsedSymbol(
                    name=type_name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="java",
                    imports=import_names,
                    decorators=annotations,
                    parent=parent_name,
                    metadata={"is_controller": is_controller},
                )
            )
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    visit(child, type_name)

        elif node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            method_name = _text(name_node) if name_node else "unknown"

            modifiers = node.child_by_field_name("modifiers")
            annotations = []
            if modifiers:
                for m in _find_nodes(modifiers, "marker_annotation", "annotation"):
                    annotations.append(_text(m))

            kind = "function"
            http_method = None
            route_path = None
            for ann in annotations:
                m = re.match(
                    r'@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)'
                    r'\s*(?:\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\'])?',
                    ann,
                )
                if m:
                    kind = "route"
                    method_map = {
                        "GetMapping": "GET", "PostMapping": "POST",
                        "PutMapping": "PUT", "PatchMapping": "PATCH",
                        "DeleteMapping": "DELETE", "RequestMapping": "ANY",
                    }
                    http_method = method_map.get(m.group(1), "ANY")
                    route_path = m.group(2)
                    break

            symbols.append(
                ParsedSymbol(
                    name=method_name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="java",
                    imports=import_names,
                    decorators=annotations,
                    parent=parent_name,
                    metadata={"http_method": http_method, "route_path": route_path},
                )
            )
        else:
            for child in node.children:
                visit(child, parent_name)

    for child in root.children:
        visit(child, None)

    return symbols


def _parse_java_regex(source: bytes, file_path: str) -> list[ParsedSymbol]:
    symbols: list[ParsedSymbol] = []
    text = source.decode("utf-8", errors="replace")
    import_names = [l.strip() for l in text.splitlines() if l.strip().startswith("import ")]
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        m = re.match(r"(?:public\s+)?(?:abstract\s+)?class\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="class", file=file_path,
                             line=i, language="java", imports=import_names)
            )
            continue
        m = re.match(r"(?:public\s+)?interface\s+(\w+)", s)
        if m:
            symbols.append(
                ParsedSymbol(name=m.group(1), kind="interface", file=file_path,
                             line=i, language="java", imports=import_names)
            )
    return symbols


# ── CodeIndexer ───────────────────────────────────────────────────────────────


class CodeIndexer:
    """
    Parses a repository using Tree-sitter and produces an ArchitectureState.

    Usage:
        indexer = CodeIndexer("/path/to/repo")
        state = indexer.index()
    """

    def __init__(self, repo_path: str | Path, settings=None):
        self.repo_path = Path(repo_path).resolve()
        # Populated by index()/index_files(): {element_id: [FileMapping, ...]}
        self.last_file_map: dict[str, list[FileMapping]] = {}
        self.settings = settings
        self._exclude: frozenset[str] = self._build_exclude_set()
        self._max_file_size: int = (
            settings.MAX_FILE_SIZE_BYTES
            if settings and hasattr(settings, "MAX_FILE_SIZE_BYTES")
            else MAX_FILE_SIZE
        )

    def _build_exclude_set(self) -> frozenset[str]:
        extras: list[str] = []
        if self.settings and hasattr(self.settings, "exclude_paths"):
            extras = list(self.settings.exclude_paths or [])
        return DEFAULT_EXCLUDES | frozenset(extras)

    # ── Public API ────────────────────────────────────────────────────────────

    def index(self, progress_callback: Callable[[str, int, int], None] | None = None) -> ArchitectureState:
        """
        Full index: walk all files → parse → build architecture model.

        Args:
            progress_callback: Optional callable(message, current, total).

        Returns:
            Populated ArchitectureState.
        """
        logger.info("Starting full index of {}", self.repo_path)

        files_by_lang = self._detect_languages()
        all_files: list[Path] = [f for flist in files_by_lang.values() for f in flist]
        total = len(all_files)
        logger.info("Found {} files across {} languages", total, len(files_by_lang))

        tech_stack = self._detect_tech_stack(files_by_lang)

        symbols: list[ParsedSymbol] = []
        for idx, (lang, flist) in enumerate(files_by_lang.items()):
            for fpath in flist:
                if progress_callback:
                    progress_callback(f"Parsing {fpath.name}", idx, total)
                try:
                    syms = self._parse_file(fpath, lang)
                    symbols.extend(syms)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to parse {}: {}", fpath, exc)

        logger.info("Extracted {} symbols", len(symbols))

        architecture = self._build_architecture_model(symbols)
        endpoints = self._detect_api_endpoints(symbols)
        data_model = self._build_data_model(symbols)
        capabilities = self._detect_capabilities(architecture)
        file_map = self._build_file_map(symbols)
        # Expose the file map so callers can persist it to .gyromap.yaml.
        # This is the scope→state→map→code chain that makes element-scoped
        # rules resolvable to concrete files.
        self.last_file_map = file_map

        # Attach file mappings as facts on each element
        for elem_id, mappings in file_map.items():
            if elem_id in architecture:
                for fm in mappings:
                    fact = f"Implemented in {fm.file}"
                    if fact not in architecture[elem_id].facts:
                        architecture[elem_id].facts.append(fact)

        project_name = self._detect_project_name()
        commit_sha = self._detect_commit_sha()

        state = ArchitectureState(
            metadata=StateMetadata(
                project=project_name,
                commit_sha=commit_sha,
            ),
            architecture=architecture,
            data_model=data_model,
            capabilities=capabilities,
            tech_stack=tech_stack,
            surface_area=endpoints,
        )
        logger.info("Index complete. {}", state.summary())
        return state

    def index_files(self, file_paths: list[str]) -> ArchitectureState:
        """
        Index only specific files (e.g., files changed in a PR).

        Returns a partial ArchitectureState covering only the given files.
        """
        logger.info("Partial index: {} files", len(file_paths))
        symbols: list[ParsedSymbol] = []
        for fp_str in file_paths:
            fpath = Path(fp_str)
            if not fpath.is_absolute():
                fpath = self.repo_path / fpath
            if not fpath.exists():
                logger.debug("Skipping missing file: {}", fpath)
                continue
            lang = EXTENSION_MAP.get(fpath.suffix.lower())
            if not lang:
                continue
            try:
                syms = self._parse_file(fpath, lang)
                symbols.extend(syms)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse {}: {}", fpath, exc)

        architecture = self._build_architecture_model(symbols)
        endpoints = self._detect_api_endpoints(symbols)
        data_model = self._build_data_model(symbols)
        capabilities = self._detect_capabilities(architecture)

        # Tech stack from only the subset of files
        files_by_lang: dict[str, list[Path]] = defaultdict(list)
        for s in symbols:
            files_by_lang[s.language].append(Path(s.file))
        tech_stack = self._detect_tech_stack(dict(files_by_lang))

        self.last_file_map = self._build_file_map(symbols)

        return ArchitectureState(
            metadata=StateMetadata(
                project=self._detect_project_name(),
                commit_sha=self._detect_commit_sha(),
            ),
            architecture=architecture,
            data_model=data_model,
            capabilities=capabilities,
            tech_stack=tech_stack,
            surface_area=endpoints,
        )

    # ── Language detection ────────────────────────────────────────────────────

    def _detect_languages(self) -> dict[str, list[Path]]:
        """Scan repo and return {language: [file_paths]}."""
        result: dict[str, list[Path]] = defaultdict(list)

        for file_path in self.repo_path.rglob("*"):
            if not file_path.is_file():
                continue
            # Skip excluded directories
            if any(part in self._exclude for part in file_path.parts):
                continue
            # Skip symlinks to avoid loops
            if file_path.is_symlink():
                continue
            # Skip oversized files
            try:
                if file_path.stat().st_size > self._max_file_size:
                    logger.debug("Skipping oversized file: {}", file_path)
                    continue
            except OSError:
                continue

            lang = EXTENSION_MAP.get(file_path.suffix.lower())
            if lang:
                result[lang].append(file_path)

        return dict(result)

    # ── Tech stack detection ──────────────────────────────────────────────────

    def _detect_tech_stack(
        self, files: dict[str, list[Path]]
    ) -> dict[str, TechStackItem]:
        """Detect frameworks/tools from manifest files."""
        stack: dict[str, TechStackItem] = {}

        self._detect_from_package_json(stack)
        self._detect_from_pyproject(stack)
        self._detect_from_requirements_txt(stack)
        self._detect_from_go_mod(stack)
        self._detect_from_cargo_toml(stack)
        self._detect_from_pom_xml(stack)

        # Language presence → add language items
        for lang in files:
            lang_display = lang.capitalize()
            if lang not in stack:
                stack[lang] = TechStackItem(
                    type="language",
                    vendor=_language_vendor(lang),
                )

        return stack

    def _detect_from_package_json(self, stack: dict[str, TechStackItem]) -> None:
        pkg_json = self.repo_path / "package.json"
        if not pkg_json.exists():
            # Check subdirectories one level deep (monorepos)
            for child in self.repo_path.iterdir():
                if child.is_dir() and (child / "package.json").exists():
                    self._parse_package_json(child / "package.json", stack)
            return
        self._parse_package_json(pkg_json, stack)

    def _parse_package_json(self, path: Path, stack: dict[str, TechStackItem]) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not parse {}: {}", path, exc)
            return

        all_deps: dict[str, str] = {}
        all_deps.update(data.get("dependencies", {}))
        all_deps.update(data.get("devDependencies", {}))
        all_deps.update(data.get("peerDependencies", {}))

        for pkg_name, version in all_deps.items():
            rule_key = pkg_name.lower()
            if rule_key in TECH_STACK_RULES:
                display, kind, vendor = TECH_STACK_RULES[rule_key]
                stack[display] = TechStackItem(
                    type=kind,
                    vendor=vendor,
                    version=version.lstrip("^~>="),
                )

        # Detect TypeScript usage
        if "typescript" in all_deps or "ts-node" in all_deps:
            stack["TypeScript"] = TechStackItem(
                type="language", vendor="Microsoft",
                version=all_deps.get("typescript", "").lstrip("^~>=") or None,
            )
        else:
            stack["JavaScript"] = TechStackItem(type="language", vendor="OpenJS Foundation")

        # Node.js runtime
        if "node" not in stack:
            stack["Node.js"] = TechStackItem(type="runtime", vendor="OpenJS Foundation")

    def _detect_from_pyproject(self, stack: dict[str, TechStackItem]) -> None:
        for candidate in [
            self.repo_path / "pyproject.toml",
            *[d / "pyproject.toml" for d in self.repo_path.iterdir() if d.is_dir()],
        ]:
            if not candidate.exists():
                continue
            try:
                import toml
                data = toml.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                try:
                    import tomllib
                    data = tomllib.loads(candidate.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not parse {}: {}", candidate, exc)
                    continue

            deps: list[str] = []
            deps.extend(data.get("project", {}).get("dependencies", []))
            # poetry style
            poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            deps.extend(poetry_deps.keys())

            for dep in deps:
                # Normalize: "fastapi>=0.100.0" → "fastapi"
                norm = re.split(r"[>=<!;\[\s]", dep)[0].strip().lower().replace("-", "-")
                if norm in TECH_STACK_RULES:
                    display, kind, vendor = TECH_STACK_RULES[norm]
                    stack[display] = TechStackItem(type=kind, vendor=vendor)

            stack["Python"] = TechStackItem(type="language", vendor="Python Software Foundation")
            break  # only process first found

    def _detect_from_requirements_txt(self, stack: dict[str, TechStackItem]) -> None:
        for candidate in [
            self.repo_path / "requirements.txt",
            self.repo_path / "requirements-dev.txt",
            self.repo_path / "requirements/base.txt",
        ]:
            if not candidate.exists():
                continue
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    pkg = re.split(r"[>=<!;\[\s]", line)[0].strip().lower()
                    if pkg in TECH_STACK_RULES:
                        display, kind, vendor = TECH_STACK_RULES[pkg]
                        stack[display] = TechStackItem(type=kind, vendor=vendor)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not read {}: {}", candidate, exc)

        if any((self.repo_path / f).exists() for f in ("requirements.txt", "setup.py", "setup.cfg")):
            stack.setdefault("Python", TechStackItem(type="language", vendor="Python Software Foundation"))

    def _detect_from_go_mod(self, stack: dict[str, TechStackItem]) -> None:
        go_mod = self.repo_path / "go.mod"
        if not go_mod.exists():
            return
        try:
            text = go_mod.read_text(encoding="utf-8")
            stack["Go"] = TechStackItem(type="language", vendor="Google")

            # Detect popular Go frameworks
            go_frameworks = {
                "github.com/gin-gonic/gin": ("Gin", "framework", "Gin-Gonic"),
                "github.com/labstack/echo": ("Echo", "framework", "LabStack"),
                "github.com/go-chi/chi": ("Chi", "framework", "go-chi"),
                "github.com/gofiber/fiber": ("Fiber", "framework", "GoFiber"),
                "github.com/gorilla/mux": ("Gorilla Mux", "framework", "Gorilla Web Toolkit"),
                "gorm.io/gorm": ("GORM", "orm", "GORM"),
                "github.com/jackc/pgx": ("pgx", "database", "Jack Christensen"),
                "go.mongodb.org/mongo-driver": ("MongoDB", "database", "MongoDB"),
                "github.com/redis/go-redis": ("go-redis", "library", "Redis"),
                "github.com/google/wire": ("Wire", "tool", "Google"),
                "google.golang.org/grpc": ("gRPC", "protocol", "Google"),
            }
            for module, (display, kind, vendor) in go_frameworks.items():
                if module in text:
                    stack[display] = TechStackItem(type=kind, vendor=vendor)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not parse go.mod: {}", exc)

    def _detect_from_cargo_toml(self, stack: dict[str, TechStackItem]) -> None:
        cargo_toml = self.repo_path / "Cargo.toml"
        if not cargo_toml.exists():
            return
        try:
            import toml
            data = toml.loads(cargo_toml.read_text(encoding="utf-8"))
        except Exception:
            try:
                import tomllib
                data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not parse Cargo.toml: {}", exc)
                return

        stack["Rust"] = TechStackItem(type="language", vendor="Mozilla / Rust Foundation")
        deps: dict[str, str] = {}
        deps.update(data.get("dependencies", {}))
        deps.update(data.get("dev-dependencies", {}))

        rust_frameworks = {
            "axum": ("Axum", "framework", "Tokio"),
            "actix-web": ("Actix-Web", "framework", "Actix"),
            "warp": ("Warp", "framework", "seanmonstar"),
            "rocket": ("Rocket", "framework", "Sergio Benitez"),
            "tower": ("Tower", "library", "Tokio"),
            "tokio": ("Tokio", "runtime", "Tokio"),
            "diesel": ("Diesel", "orm", "Diesel"),
            "sqlx": ("SQLx", "orm", "launchbadge"),
            "sea-orm": ("SeaORM", "orm", "SeaQL"),
            "serde": ("Serde", "library", "dtolnay"),
            "tonic": ("Tonic (gRPC)", "protocol", "Hyperium"),
        }
        for crate, (display, kind, vendor) in rust_frameworks.items():
            if crate in deps:
                stack[display] = TechStackItem(type=kind, vendor=vendor)

    def _detect_from_pom_xml(self, stack: dict[str, TechStackItem]) -> None:
        pom = self.repo_path / "pom.xml"
        if not pom.exists():
            return
        try:
            text = pom.read_text(encoding="utf-8")
            stack["Java"] = TechStackItem(type="language", vendor="Oracle / OpenJDK")

            java_frameworks = {
                "spring-boot": ("Spring Boot", "framework", "VMware"),
                "spring-web": ("Spring Web", "framework", "VMware"),
                "spring-data-jpa": ("Spring Data JPA", "orm", "VMware"),
                "hibernate": ("Hibernate", "orm", "Red Hat"),
                "jakarta.persistence": ("Jakarta Persistence", "orm", "Eclipse Foundation"),
                "quarkus": ("Quarkus", "framework", "Red Hat"),
                "micronaut": ("Micronaut", "framework", "Object Computing"),
                "vertx": ("Vert.x", "framework", "Eclipse Foundation"),
                "lombok": ("Lombok", "tool", "Project Lombok"),
                "mapstruct": ("MapStruct", "tool", "MapStruct"),
            }
            for artifact, (display, kind, vendor) in java_frameworks.items():
                if artifact in text:
                    stack[display] = TechStackItem(type=kind, vendor=vendor)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not parse pom.xml: {}", exc)

    # ── File parsing ──────────────────────────────────────────────────────────

    def _parse_file(self, file_path: Path, language: str) -> list[ParsedSymbol]:
        """Parse a single file with Tree-sitter (or regex fallback)."""
        try:
            source = file_path.read_bytes()
        except OSError as exc:
            logger.debug("Cannot read {}: {}", file_path, exc)
            return []

        rel_path = str(file_path.relative_to(self.repo_path))

        if language == "python":
            return _parse_python(source, rel_path)
        if language in ("javascript",):
            return _parse_typescript_javascript(source, rel_path, "javascript")
        if language == "typescript":
            # Handle .tsx files: use tsx grammar if available
            if file_path.suffix.lower() == ".tsx":
                return _parse_tsx(source, rel_path)
            return _parse_typescript_javascript(source, rel_path, "typescript")
        if language == "go":
            return _parse_go(source, rel_path)
        if language == "rust":
            return _parse_rust(source, rel_path)
        if language == "java":
            return _parse_java(source, rel_path)

        logger.debug("No parser for language: {}", language)
        return []

    # ── Architecture model ────────────────────────────────────────────────────

    def _build_architecture_model(
        self, symbols: list[ParsedSymbol]
    ) -> dict[str, ArchitectureElement]:
        """
        Cluster symbols into architecture components by directory path.

        Strategy:
        - Group files by their immediate parent directory (relative to repo root).
        - Each directory becomes an ArchitectureElement (component or container).
        - Top-level directories (src/, services/, etc.) get c4_depth=container.
        - Deeper directories get c4_depth=component.
        """
        # file → component_id
        file_to_component: dict[str, str] = {}

        # Collect all unique file paths
        all_files = {s.file for s in symbols}
        for file_path_str in all_files:
            fp = Path(file_path_str)
            component_id = self._file_to_component_id(fp)
            file_to_component[file_path_str] = component_id

        # Group symbols by component
        component_symbols: dict[str, list[ParsedSymbol]] = defaultdict(list)
        for sym in symbols:
            cid = file_to_component.get(sym.file, "root")
            component_symbols[cid].append(sym)

        elements: dict[str, ArchitectureElement] = {}

        for component_id, syms in component_symbols.items():
            # Determine c4 depth from path depth
            depth_parts = component_id.split("/")
            c4_depth = C4Depth.container if len(depth_parts) <= 2 else C4Depth.component

            # Infer element type from component name
            elem_type = _infer_element_type(component_id)

            # Build description from symbols
            class_names = [s.name for s in syms if s.kind in ("class", "struct", "interface") and s.parent is None]
            desc = _generate_component_description(component_id, class_names, syms)

            # Collect facts
            facts: list[str] = []
            route_count = sum(1 for s in syms if s.kind == "route")
            if route_count:
                facts.append(f"Exposes {route_count} API route(s)")

            model_count = sum(1 for s in syms if s.kind == "model")
            if model_count:
                facts.append(f"Contains {model_count} data model(s)")

            languages_used = sorted({s.language for s in syms})
            if languages_used:
                facts.append(f"Languages: {', '.join(languages_used)}")

            # Tags
            tags = _infer_tags(component_id, syms)

            elements[component_id] = ArchitectureElement(
                type=elem_type,
                c4_depth=c4_depth,
                description=desc,
                facts=facts,
                status=ElementStatus.implemented,
                tags=tags,
            )

        # Build relationships via import analysis
        self._build_relationships(elements, symbols, file_to_component)

        return elements

    def _file_to_component_id(self, file_path: Path) -> str:
        """
        Convert a file path to a component identifier.

        Examples:
          src/services/payment.py  → src/services/payment
          api/routes/users.py      → api/routes/users
          main.go                  → root
          services/auth/handler.go → services/auth
        """
        parts = file_path.parts
        # Remove the file name (last part)
        if len(parts) == 1:
            # Top-level file → root component
            return "root"
        # Use directory path, converting to slug
        dir_parts = parts[:-1]  # all except filename
        # Slug-ify
        slug = "/".join(
            re.sub(r"[^a-z0-9\-_]", "-", p.lower()) for p in dir_parts
        )
        return slug

    def _build_relationships(
        self,
        elements: dict[str, ArchitectureElement],
        symbols: list[ParsedSymbol],
        file_to_component: dict[str, str],
    ) -> None:
        """
        Detect relationships between components via import analysis.

        When component A imports a module from component B's directory,
        we add a sync relationship A → B.
        """
        # Map module/path fragments to component IDs
        component_ids = set(elements.keys())

        for sym in symbols:
            if sym.kind != "import":
                continue
            source_cid = file_to_component.get(sym.file, "root")
            for import_str in sym.imports:
                target_cid = _resolve_import_to_component(import_str, component_ids)
                if target_cid and target_cid != source_cid and target_cid in elements:
                    rel_key = f"uses_{target_cid.replace('/', '_')}"
                    if rel_key not in elements[source_cid].relationships:
                        elements[source_cid].relationships[rel_key] = Relationship(
                            target=target_cid,
                            type=RelationType.sync,
                            description=f"Imports from {target_cid}",
                        )

        # Also build component→component relationships from all symbols with imports
        comp_imports: dict[str, set[str]] = defaultdict(set)
        for sym in symbols:
            if not sym.imports:
                continue
            source_cid = file_to_component.get(sym.file, "root")
            for import_str in sym.imports:
                target_cid = _resolve_import_to_component(import_str, component_ids)
                if target_cid and target_cid != source_cid:
                    comp_imports[source_cid].add(target_cid)

        for source_cid, targets in comp_imports.items():
            if source_cid not in elements:
                continue
            for target_cid in targets:
                if target_cid not in elements:
                    continue
                rel_key = f"uses_{target_cid.replace('/', '_')}"
                if rel_key not in elements[source_cid].relationships:
                    elements[source_cid].relationships[rel_key] = Relationship(
                        target=target_cid,
                        type=RelationType.sync,
                        description=f"Depends on {target_cid}",
                    )

    # ── API endpoint detection ────────────────────────────────────────────────

    def _detect_api_endpoints(
        self, symbols: list[ParsedSymbol]
    ) -> dict[str, ApiEndpoint]:
        """Extract REST/GraphQL endpoints from route symbols."""
        endpoints: dict[str, ApiEndpoint] = {}

        for sym in symbols:
            if sym.kind != "route":
                continue
            http_method = sym.metadata.get("http_method") or "ANY"
            route_path = sym.metadata.get("route_path") or f"/{sym.name}"

            # Normalize endpoint key
            endpoint_key = f"{http_method} {route_path}"
            if endpoint_key in endpoints:
                continue

            # Auth detection heuristic: check decorator/function name
            auth_required = _is_auth_required(sym)

            summary = sym.docstring or f"{http_method} {route_path}"

            endpoints[endpoint_key] = ApiEndpoint(
                type="api_endpoint",
                summary=summary[:200],
                auth_required=auth_required,
                method=http_method,
            )

        return endpoints

    # ── Data model ────────────────────────────────────────────────────────────

    def _build_data_model(self, symbols: list[ParsedSymbol]) -> DataModel:
        """Extract data entities from ORM models, Pydantic models, TS interfaces."""
        entities: dict[str, DataEntity] = {}

        for sym in symbols:
            if sym.kind not in ("model", "interface", "type", "struct"):
                continue

            # Exclude obviously non-data symbols
            name = sym.name
            if _is_service_class(name):
                continue

            domain = _infer_domain(name, sym.file)
            sensitivity = _infer_sensitivity(name)

            desc = sym.docstring or f"{name} data entity"

            facts: list[str] = []
            if sym.language == "python":
                bases = sym.metadata.get("bases", "")
                if "BaseModel" in bases or "SQLModel" in bases:
                    facts.append("Pydantic model")
                elif "Base" in bases or "db.Model" in bases:
                    facts.append("SQLAlchemy model")
            elif sym.language in ("typescript", "javascript"):
                if sym.kind == "interface":
                    facts.append("TypeScript interface")
                elif sym.kind == "type":
                    facts.append("TypeScript type alias")
            elif sym.language == "go":
                facts.append("Go struct")
            elif sym.language == "rust":
                facts.append("Rust struct")
            elif sym.language == "java":
                facts.append("Java class")

            facts.append(f"Defined in {sym.file}:{sym.line}")

            entities[name] = DataEntity(
                description=desc,
                domain=domain,
                sensitivity=sensitivity,
                facts=facts,
            )

        return DataModel(entities=entities)

    # ── Capabilities ──────────────────────────────────────────────────────────

    def _detect_capabilities(
        self, components: dict[str, ArchitectureElement]
    ) -> dict[str, Capability]:
        """Infer capabilities from component names and descriptions."""
        capabilities: dict[str, Capability] = {}
        seen: set[str] = set()

        for component_id, element in components.items():
            # Check component ID parts against keyword heuristics
            parts = re.split(r"[/_\-]", component_id.lower())
            for part in parts:
                for keyword, description in CAPABILITY_KEYWORDS.items():
                    if keyword in part and keyword not in seen:
                        seen.add(keyword)
                        cap_id = f"cap-{keyword}"
                        capabilities[cap_id] = Capability(
                            description=description,
                            status="active",
                            facts=[f"Detected in component: {component_id}"],
                        )

            # Also scan description
            desc_lower = element.description.lower()
            for keyword, description in CAPABILITY_KEYWORDS.items():
                if keyword in desc_lower and keyword not in seen:
                    seen.add(keyword)
                    cap_id = f"cap-{keyword}"
                    capabilities[cap_id] = Capability(
                        description=description,
                        status="active",
                        facts=[f"Detected in description of: {component_id}"],
                    )

        return capabilities

    # ── File map ──────────────────────────────────────────────────────────────

    def _build_file_map(
        self, symbols: list[ParsedSymbol]
    ) -> dict[str, list[FileMapping]]:
        """Map architecture element IDs to their implementing files."""
        file_map: dict[str, list[FileMapping]] = defaultdict(list)

        # Group symbols by file
        file_symbols: dict[str, list[ParsedSymbol]] = defaultdict(list)
        for sym in symbols:
            file_symbols[sym.file].append(sym)

        for file_str, syms in file_symbols.items():
            fp = Path(file_str)
            component_id = self._file_to_component_id(fp)

            methods = [
                s.name for s in syms
                if s.kind in ("function", "route", "method") and s.parent is None
            ]

            mapping = FileMapping(
                file=file_str,
                methods=methods[:50],  # cap for readability
                description=f"Source file containing {len(syms)} symbol(s)",
            )
            file_map[component_id].append(mapping)

        return dict(file_map)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detect_project_name(self) -> str:
        """Detect project name from repo dir, package.json, pyproject.toml."""
        # Try pyproject.toml
        pyproject = self.repo_path / "pyproject.toml"
        if pyproject.exists():
            try:
                import toml
                data = toml.loads(pyproject.read_text(encoding="utf-8"))
                name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
                if name:
                    return name
            except Exception:
                pass
            try:
                import tomllib
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                name = data.get("project", {}).get("name")
                if name:
                    return name
            except Exception:
                pass

        # Try package.json
        pkg_json = self.repo_path / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                if data.get("name"):
                    return data["name"]
            except Exception:
                pass

        # Try go.mod
        go_mod = self.repo_path / "go.mod"
        if go_mod.exists():
            try:
                first_line = go_mod.read_text(encoding="utf-8").splitlines()[0]
                m = re.match(r"^module\s+(\S+)", first_line)
                if m:
                    return m.group(1).split("/")[-1]
            except Exception:
                pass

        return self.repo_path.name

    def _detect_commit_sha(self) -> str | None:
        """Get current git commit SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None


# ── TSX parser ────────────────────────────────────────────────────────────────


def _parse_tsx(source: bytes, file_path: str) -> list[ParsedSymbol]:
    """Parse TSX files using tree-sitter-typescript's tsx grammar."""
    try:
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser

        lang = Language(tsts.language_tsx())
        parser = Parser(lang)
        tree = parser.parse(source)
        root = tree.root_node

        symbols: list[ParsedSymbol] = []
        import_names: list[str] = []
        for node in _find_nodes(root, "import_declaration"):
            import_names.append(_text(node).strip())

        for node in _find_nodes(
            root,
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
        ):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node)
            if node.type == "class_declaration":
                kind = "class"
            elif node.type == "interface_declaration":
                kind = "interface"
            elif node.type == "type_alias_declaration":
                kind = "type"
            else:
                kind = "component" if re.match(r"^[A-Z]", name) else "function"
            symbols.append(
                ParsedSymbol(
                    name=name,
                    kind=kind,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    language="typescript",
                    imports=import_names,
                )
            )
        return symbols
    except Exception:
        return _parse_ts_js_regex(source, file_path, "typescript")


# ── Pure helper functions ─────────────────────────────────────────────────────


def _language_vendor(language: str) -> str:
    vendors = {
        "python": "Python Software Foundation",
        "javascript": "OpenJS Foundation",
        "typescript": "Microsoft",
        "go": "Google",
        "rust": "Rust Foundation",
        "java": "Oracle / OpenJDK",
    }
    return vendors.get(language, "Open Source")


def _infer_element_type(component_id: str) -> ElementType:
    """Guess the ElementType from a component's path name."""
    name = component_id.lower()
    if any(kw in name for kw in ("db", "database", "postgres", "mysql", "sqlite", "mongo", "redis")):
        return ElementType.database
    if any(kw in name for kw in ("queue", "broker", "kafka", "rabbitmq", "sqs", "pubsub")):
        return ElementType.queue
    if any(kw in name for kw in ("cache", "memcache")):
        return ElementType.cache
    if any(kw in name for kw in ("service", "svc", "worker")):
        return ElementType.service
    if any(kw in name for kw in ("api", "gateway", "proxy", "frontend", "client", "web", "app")):
        return ElementType.container
    return ElementType.component


def _infer_tags(component_id: str, symbols: list[ParsedSymbol]) -> list[str]:
    tags: list[str] = []
    name = component_id.lower()

    tag_map = {
        "route": ["api"],
        "model": ["data"],
        "service": ["service"],
        "auth": ["security", "auth"],
        "test": ["test"],
        "migration": ["migration"],
        "worker": ["async", "worker"],
        "queue": ["async", "queue"],
        "cache": ["cache"],
    }
    for keyword, tag_list in tag_map.items():
        if keyword in name:
            tags.extend(t for t in tag_list if t not in tags)

    has_routes = any(s.kind == "route" for s in symbols)
    has_models = any(s.kind == "model" for s in symbols)
    if has_routes and "api" not in tags:
        tags.append("api")
    if has_models and "data" not in tags:
        tags.append("data")

    languages = sorted({s.language for s in symbols})
    tags.extend(languages)

    return tags


def _generate_component_description(
    component_id: str, class_names: list[str], symbols: list[ParsedSymbol]
) -> str:
    """Generate a human-readable description for a component."""
    name_parts = re.split(r"[/_\-]", component_id)
    readable_name = " ".join(p.capitalize() for p in name_parts if p)

    route_count = sum(1 for s in symbols if s.kind == "route")
    class_count = len(class_names)

    if route_count and class_count:
        class_preview = ", ".join(class_names[:3])
        return f"{readable_name} — {route_count} route(s), classes: {class_preview}"
    if route_count:
        return f"{readable_name} — {route_count} HTTP route(s)"
    if class_count:
        class_preview = ", ".join(class_names[:3])
        more = f" (+{class_count - 3} more)" if class_count > 3 else ""
        return f"{readable_name} — {class_preview}{more}"

    func_names = [s.name for s in symbols if s.kind == "function" and s.parent is None][:3]
    if func_names:
        return f"{readable_name} — functions: {', '.join(func_names)}"

    return f"{readable_name} module"


def _resolve_import_to_component(
    import_str: str, component_ids: set[str]
) -> str | None:
    """
    Try to map an import statement to a known component ID.

    Heuristic: if any component_id fragment appears in the import string,
    it's likely that component.
    """
    import_lower = import_str.lower()
    # Sort by length descending so more specific matches win
    for cid in sorted(component_ids, key=len, reverse=True):
        # Get the leaf name of the component
        leaf = cid.split("/")[-1]
        if leaf and len(leaf) > 2 and leaf in import_lower:
            return cid
    return None


def _is_auth_required(sym: ParsedSymbol) -> bool:
    """Heuristic: does this route require authentication?"""
    decorators_text = " ".join(sym.decorators).lower()
    name_lower = sym.name.lower()
    # Explicit public indicators
    if any(
        kw in decorators_text
        for kw in ("public", "allow_anonymous", "no_auth", "open")
    ):
        return False
    # Well-known auth decorators
    if any(
        kw in decorators_text
        for kw in ("login_required", "requires_auth", "authenticated", "jwt", "oauth", "permission")
    ):
        return True
    # Auth not required for health/public endpoints
    if any(kw in name_lower for kw in ("health", "ping", "status", "version", "docs", "openapi")):
        return False
    # Default: require auth
    return True


def _is_service_class(name: str) -> bool:
    """Exclude obviously non-data class names from data model."""
    service_suffixes = (
        "Service", "Handler", "Controller", "Router", "Manager",
        "Factory", "Builder", "Client", "Provider", "Middleware",
        "Interceptor", "Resolver", "Validator", "Serializer",
        "Formatter", "Parser", "Processor", "Dispatcher", "Listener",
        "Repository", "UseCase", "Command", "Query", "Event",
    )
    return any(name.endswith(s) for s in service_suffixes)


def _infer_domain(name: str, file_path: str) -> str | None:
    """Infer the business domain for a data entity."""
    combined = (name + " " + file_path).lower()
    domain_keywords = {
        "user": "identity",
        "account": "identity",
        "auth": "identity",
        "session": "identity",
        "token": "identity",
        "order": "commerce",
        "product": "commerce",
        "cart": "commerce",
        "payment": "finance",
        "invoice": "finance",
        "subscription": "finance",
        "notification": "communication",
        "email": "communication",
        "message": "communication",
        "post": "content",
        "article": "content",
        "comment": "content",
        "media": "content",
        "file": "storage",
        "upload": "storage",
        "report": "analytics",
        "metric": "analytics",
        "event": "analytics",
        "log": "observability",
        "audit": "observability",
    }
    for keyword, domain in domain_keywords.items():
        if keyword in combined:
            return domain
    return None


def _infer_sensitivity(name: str) -> str | None:
    """Guess data sensitivity level from entity name."""
    name_lower = name.lower()
    if any(kw in name_lower for kw in ("password", "secret", "credential", "private_key", "token")):
        return "confidential"
    if any(kw in name_lower for kw in ("user", "profile", "account", "email", "phone", "address", "payment")):
        return "pii"
    if any(kw in name_lower for kw in ("log", "audit", "metric", "event")):
        return "internal"
    return "internal"
