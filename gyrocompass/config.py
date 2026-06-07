"""GyroCompass configuration — reads from env vars and .env files."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM Provider ──────────────────────────────────────────────────────────
    # Set one of these to configure the LLM backend.

    # Primary provider: "openai" | "anthropic" | "ollama" | "custom"
    LLM_PROVIDER: str = Field(default="openai", alias="GYRO_LLM_PROVIDER")

    # OpenAI (or OpenAI-compatible)
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None  # override for OpenAI-compatible APIs
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Anthropic
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # Ollama (local, privacy-first)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # Custom REST (any OpenAI-compatible endpoint)
    # LLM_BASE_URL + LLM_API_KEY → works with Azure OpenAI, Together, Groq, etc.
    LLM_BASE_URL: str | None = Field(default=None, alias="GYRO_LLM_BASE_URL")
    LLM_API_KEY: str | None = Field(default=None, alias="GYRO_LLM_API_KEY")
    LLM_MODEL: str | None = Field(default=None, alias="GYRO_LLM_MODEL")

    # ── GitHub Integration ────────────────────────────────────────────────────
    GITHUB_TOKEN: str | None = None
    GITHUB_APP_ID: str | None = None
    GITHUB_APP_PRIVATE_KEY: str | None = None
    GITHUB_WEBHOOK_SECRET: str | None = None

    # ── Database ──────────────────────────────────────────────────────────────
    # SQLite by default (zero-config), PostgreSQL for team deployments
    DATABASE_URL: str = "sqlite+aiosqlite:///./gyrocompass.db"

    # ── API Server ────────────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 7700
    API_SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ── MCP Server ────────────────────────────────────────────────────────────
    MCP_TRANSPORT: str = "stdio"  # "stdio" | "streamable-http"
    MCP_HOST: str = "localhost"
    MCP_PORT: int = 7701

    # ── Indexing ──────────────────────────────────────────────────────────────
    # Repo to analyze (defaults to CWD)
    TARGET_REPO_PATH: str | None = None
    # Max file size to parse (in bytes)
    MAX_FILE_SIZE_BYTES: int = 500_000
    # Number of parallel indexing workers
    INDEX_WORKERS: int = 4

    # ── Graph Engine (Phase 2: deep code graph) ─────────────────────────────────
    # Analysis backend: "lite" (in-process, NetworkX from the Tree-sitter indexer)
    # or "graph" (Memgraph + Qdrant + semantic search via code-graph-rag engine).
    GRAPH_BACKEND: str = Field(default="lite", alias="GYRO_GRAPH_BACKEND")
    # Memgraph (Neo4j-compatible) connection
    MEMGRAPH_HOST: str = "localhost"
    MEMGRAPH_PORT: int = 7687
    MEMGRAPH_BATCH_SIZE: int = 1000
    # Qdrant vector store for semantic code search
    QDRANT_URL: str | None = None  # e.g. http://localhost:6333; None = embedded
    QDRANT_PATH: str = "./.qdrant_gyro"  # embedded mode storage dir
    # Semantic embeddings (UniXcoder). Disabled by default — heavy (torch) dep.
    SEMANTIC_ENABLED: bool = Field(default=False, alias="GYRO_SEMANTIC_ENABLED")
    EMBEDDING_MODEL: str = "microsoft/unixcoder-base"

    # ── Telemetry (opt-in only) ───────────────────────────────────────────────
    TELEMETRY_ENABLED: bool = False

    def get_effective_llm_provider(self) -> str:
        """Determine which LLM provider to use based on set env vars."""
        if self.LLM_BASE_URL and self.LLM_API_KEY:
            return "custom"
        if self.LLM_PROVIDER != "openai":
            return self.LLM_PROVIDER
        if self.ANTHROPIC_API_KEY and not self.OPENAI_API_KEY:
            return "anthropic"
        if self.OLLAMA_BASE_URL and not self.OPENAI_API_KEY and not self.ANTHROPIC_API_KEY:
            return "ollama"
        return self.LLM_PROVIDER

    def get_effective_model(self) -> str:
        """Get the model name for the effective provider."""
        if self.LLM_MODEL:
            return self.LLM_MODEL
        provider = self.get_effective_llm_provider()
        return {
            "openai": self.OPENAI_MODEL,
            "anthropic": self.ANTHROPIC_MODEL,
            "ollama": self.OLLAMA_MODEL,
            "custom": self.LLM_MODEL or "gpt-4o-mini",
        }.get(provider, self.OPENAI_MODEL)


# Singleton — import this everywhere
settings = Settings()


# ── Gyro folder layout ────────────────────────────────────────────────────────
# Files stored inside the user's repo under .gyro/

GYRO_DIR = ".gyro"
GYROSTATE_FILE = f"{GYRO_DIR}/.gyrostate.yaml"
GYRORULES_FILE = f"{GYRO_DIR}/.gyrorules.yaml"
GYROMAP_FILE = f"{GYRO_DIR}/.gyromap.yaml"
GYROCONFIG_FILE = f"{GYRO_DIR}/config.yaml"
GYROATTESTATION_FILE = f"{GYRO_DIR}/.attestation.yaml"


def get_gyro_dir(repo_path: Path | str | None = None) -> Path:
    base = Path(repo_path) if repo_path else Path.cwd()
    return base / GYRO_DIR


def get_state_path(repo_path: Path | str | None = None) -> Path:
    base = Path(repo_path) if repo_path else Path.cwd()
    return base / GYROSTATE_FILE


def get_rules_path(repo_path: Path | str | None = None) -> Path:
    base = Path(repo_path) if repo_path else Path.cwd()
    return base / GYRORULES_FILE


def get_map_path(repo_path: Path | str | None = None) -> Path:
    base = Path(repo_path) if repo_path else Path.cwd()
    return base / GYROMAP_FILE
