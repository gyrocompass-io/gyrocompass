"""LLM provider implementations for GyroCompass.

Supports OpenAI, Anthropic, Ollama, and any OpenAI-compatible REST endpoint.
Import via: from gyrocompass.llm import get_provider, BaseLLMProvider
"""

from __future__ import annotations

import json
import textwrap
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gyrocompass.config import Settings


# ── Base ──────────────────────────────────────────────────────────────────────


class BaseLLMProvider(ABC):
    """Abstract base for all LLM provider implementations.

    All providers must implement ``complete`` and ``complete_json``.  Both
    methods are synchronous so callers don't need to manage an event loop.
    """

    @abstractmethod
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Run a chat completion and return the assistant message text.

        Args:
            prompt: The user-turn content.
            system: Optional system prompt.  When *None* no system message is
                sent (not all providers require one).

        Returns:
            The full text of the first assistant completion.

        Raises:
            RuntimeError: If the upstream API call fails.
        """

    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> dict:
        """Run a chat completion and parse the response as JSON.

        The method instructs the model to respond with a JSON object.  When
        *schema* is provided the instruction is more specific.

        Args:
            prompt: The user-turn content.
            system: Optional system prompt.  A JSON instruction is appended
                automatically; you don't need to add it yourself.
            schema: Optional JSON Schema dict describing the expected shape.
                Used to sharpen the instruction sent to the model.

        Returns:
            Parsed Python dict from the model's JSON response.

        Raises:
            ValueError: If the model returns text that cannot be parsed as JSON.
            RuntimeError: If the upstream API call fails.
        """


# ── Helpers ───────────────────────────────────────────────────────────────────


def _json_system_instruction(base: str | None, schema: dict | None) -> str:
    """Build a system prompt that instructs the model to emit JSON."""
    schema_hint = (
        f"\n\nThe JSON must conform to this schema:\n{json.dumps(schema, indent=2)}"
        if schema
        else ""
    )
    instruction = (
        "You must respond with a single, valid JSON object and nothing else. "
        "Do not include markdown code fences, prose, or any text outside the JSON object."
        + schema_hint
    )
    if base:
        return f"{base}\n\n{instruction}"
    return instruction


def _parse_json_response(raw: str, provider_name: str) -> dict:
    """Strip optional markdown fences and parse JSON, raising a clear error."""
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers a model might add anyway
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{provider_name} returned content that is not valid JSON.\n"
            f"Raw response (first 400 chars):\n{raw[:400]}\n"
            f"JSON error: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError(
            f"{provider_name} returned a JSON value that is not an object (got "
            f"{type(result).__name__}).  Raw: {raw[:200]}"
        )
    return result


# ── OpenAI ────────────────────────────────────────────────────────────────────


class OpenAIProvider(BaseLLMProvider):
    """OpenAI chat completion provider.

    Also works as a drop-in for any OpenAI-compatible API (Azure OpenAI,
    Groq, Together AI, OpenRouter, …) by supplying a custom *base_url*.

    Args:
        api_key: API key for the service.  Required — raises ``ValueError``
            immediately if absent.
        base_url: Override the default ``https://api.openai.com/v1`` base URL.
        model: Model identifier (e.g. ``"gpt-4o"``, ``"gpt-4o-mini"``).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        if not api_key:
            raise ValueError(
                "OpenAI API key is required.  Set the OPENAI_API_KEY environment "
                "variable (or GYRO_LLM_API_KEY for the custom provider)."
            )
        try:
            import openai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAIProvider.  "
                "Install it with: pip install openai"
            ) from exc

        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        self._model = model

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI API call failed (model={self._model}): {exc}"
            ) from exc

        return response.choices[0].message.content or ""

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> dict:
        effective_system = _json_system_instruction(system, schema)
        messages: list[dict] = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            # Some OpenAI-compatible endpoints (e.g. older Ollama versions) may
            # not support response_format; fall back to plain completion.
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
            except Exception:
                raise RuntimeError(
                    f"OpenAI API call failed (model={self._model}): {exc}"
                ) from exc

        raw = response.choices[0].message.content or ""
        return _parse_json_response(raw, f"OpenAI({self._model})")


# ── Anthropic ─────────────────────────────────────────────────────────────────


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider.

    Uses the ``anthropic`` SDK which is an *optional* dependency; it is
    imported lazily so that users who only need OpenAI don't have to install
    it.

    Args:
        api_key: Anthropic API key.  Required.
        model: Model identifier (e.g. ``"claude-sonnet-4-6"``).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        if not api_key:
            raise ValueError(
                "Anthropic API key is required.  Set the ANTHROPIC_API_KEY "
                "environment variable."
            )
        try:
            import anthropic  # noqa: PLC0415  (lazy optional import)
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicProvider.  "
                "Install it with: pip install anthropic  "
                "or: pip install 'gyrocompass[anthropic]'"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._anthropic = anthropic  # keep reference to avoid re-import overhead

    def complete(self, prompt: str, system: str | None = None) -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Anthropic API call failed (model={self._model}): {exc}"
            ) from exc

        # response.content is a list of content blocks
        text_blocks = [
            block.text
            for block in response.content
            if hasattr(block, "text")
        ]
        return "".join(text_blocks)

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> dict:
        effective_system = _json_system_instruction(system, schema)
        kwargs: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "system": effective_system,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Anthropic API call failed (model={self._model}): {exc}"
            ) from exc

        text_blocks = [
            block.text
            for block in response.content
            if hasattr(block, "text")
        ]
        raw = "".join(text_blocks)
        return _parse_json_response(raw, f"Anthropic({self._model})")


# ── Ollama ────────────────────────────────────────────────────────────────────


class OllamaProvider(BaseLLMProvider):
    """Ollama local-model provider.

    Ollama exposes an OpenAI-compatible ``/v1`` endpoint, so this provider
    reuses the OpenAI SDK pointed at the local Ollama server.

    Args:
        base_url: Base URL for Ollama (default ``http://localhost:11434``).
            The ``/v1`` suffix is appended automatically if absent.
        model: Ollama model tag (e.g. ``"llama3.2"``, ``"mistral"``).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
    ) -> None:
        try:
            import openai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OllamaProvider (it talks "
                "to Ollama's OpenAI-compatible endpoint).  "
                "Install it with: pip install openai"
            ) from exc

        # Normalise: Ollama's OpenAI-compat endpoint lives at /v1
        api_base = base_url.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        # Ollama doesn't require a real API key but the OpenAI SDK mandates one
        self._client = openai.OpenAI(api_key="ollama", base_url=api_base)
        self._model = model
        self._base_url = base_url

    def _check_reachable(self) -> None:
        """Probe the Ollama server and raise a clear error if unreachable."""
        import httpx  # noqa: PLC0415 — httpx is a core dep in pyproject.toml

        try:
            resp = httpx.get(f"{self._base_url.rstrip('/')}/api/tags", timeout=3.0)
            resp.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self._base_url}.  "
                "Make sure Ollama is running: https://ollama.com/download\n"
                "Start it with: ollama serve"
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama server at {self._base_url} returned an error: {exc}"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unexpected error reaching Ollama at {self._base_url}: {exc}"
            ) from exc

    def complete(self, prompt: str, system: str | None = None) -> str:
        self._check_reachable()
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Ollama completion failed (model={self._model}): {exc}"
            ) from exc

        return response.choices[0].message.content or ""

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> dict:
        self._check_reachable()
        effective_system = _json_system_instruction(system, schema)
        messages: list[dict] = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": prompt},
        ]

        try:
            # Attempt JSON mode — supported in Ollama ≥ 0.1.14
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Fall back gracefully for older Ollama or models without JSON mode
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Ollama completion failed (model={self._model}): {exc}"
                ) from exc

        raw = response.choices[0].message.content or ""
        return _parse_json_response(raw, f"Ollama({self._model})")


# ── Custom REST ───────────────────────────────────────────────────────────────


class CustomRestProvider(BaseLLMProvider):
    """Provider for any OpenAI-compatible REST endpoint.

    Covers corporate LLM gateways, Azure OpenAI, Together AI, Groq, Fireworks,
    and any other service that speaks the OpenAI chat-completion wire format.

    Args:
        base_url: Full base URL of the endpoint (e.g.
            ``https://my-gateway.corp/v1``).
        api_key: Bearer token / API key for the endpoint.  Required.
        model: Model identifier as expected by the remote endpoint.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
    ) -> None:
        if not base_url:
            raise ValueError(
                "A base_url is required for CustomRestProvider.  "
                "Set GYRO_LLM_BASE_URL (or LLM_BASE_URL) in your environment."
            )
        if not api_key:
            raise ValueError(
                "An API key is required for CustomRestProvider.  "
                "Set GYRO_LLM_API_KEY (or LLM_API_KEY) in your environment."
            )
        if not model:
            raise ValueError(
                "A model name is required for CustomRestProvider.  "
                "Set GYRO_LLM_MODEL (or LLM_MODEL) in your environment."
            )
        try:
            import openai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for CustomRestProvider.  "
                "Install it with: pip install openai"
            ) from exc

        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._base_url = base_url

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Custom REST API call failed (base_url={self._base_url}, "
                f"model={self._model}): {exc}"
            ) from exc

        return response.choices[0].message.content or ""

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> dict:
        effective_system = _json_system_instruction(system, schema)
        messages: list[dict] = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Endpoint may not support response_format; degrade gracefully
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Custom REST API call failed (base_url={self._base_url}, "
                    f"model={self._model}): {exc}"
                ) from exc

        raw = response.choices[0].message.content or ""
        return _parse_json_response(raw, f"CustomRest({self._base_url}, {self._model})")


# ── Type alias (public surface) ───────────────────────────────────────────────

LLMProvider = BaseLLMProvider


# ── Factory ───────────────────────────────────────────────────────────────────


def get_provider(settings: Settings) -> BaseLLMProvider:
    """Instantiate and return the correct ``BaseLLMProvider`` for *settings*.

    Resolution order (mirrors ``Settings.get_effective_llm_provider``):

    1. If ``LLM_BASE_URL`` **and** ``LLM_API_KEY`` are both set → ``CustomRestProvider``
    2. If ``LLM_PROVIDER`` is ``"anthropic"`` → ``AnthropicProvider``
    3. If ``LLM_PROVIDER`` is ``"ollama"`` → ``OllamaProvider``
    4. If ``LLM_PROVIDER`` is ``"custom"`` (set explicitly) → ``CustomRestProvider``
    5. Otherwise → ``OpenAIProvider`` (default)

    Args:
        settings: A populated ``gyrocompass.config.Settings`` instance.

    Returns:
        A concrete ``BaseLLMProvider`` ready to call.

    Raises:
        ValueError: When required credentials are missing.
        ImportError: When an optional SDK is not installed.
        RuntimeError: When Ollama cannot be reached.
    """
    provider = settings.get_effective_llm_provider()

    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set.  "
                "Export it in your shell or add it to .env:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return AnthropicProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.get_effective_model(),
        )

    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.get_effective_model(),
        )

    if provider == "custom":
        if not settings.LLM_BASE_URL:
            raise ValueError(
                "LLM_BASE_URL (or GYRO_LLM_BASE_URL) is required for the "
                "'custom' provider.  Set it in your .env file."
            )
        if not settings.LLM_API_KEY:
            raise ValueError(
                "LLM_API_KEY (or GYRO_LLM_API_KEY) is required for the "
                "'custom' provider.  Set it in your .env file."
            )
        model = settings.get_effective_model()
        if not model:
            raise ValueError(
                "LLM_MODEL (or GYRO_LLM_MODEL) is required for the "
                "'custom' provider.  Set it in your .env file."
            )
        return CustomRestProvider(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=model,
        )

    # Default: OpenAI (or OpenAI-compatible via OPENAI_BASE_URL)
    if not settings.OPENAI_API_KEY:
        raise ValueError(
            textwrap.dedent("""\
                OPENAI_API_KEY is not set.  Choose one of:

                  1. Set an OpenAI key:
                       export OPENAI_API_KEY=sk-...

                  2. Use Anthropic:
                       export ANTHROPIC_API_KEY=sk-ant-...
                       export GYRO_LLM_PROVIDER=anthropic

                  3. Use Ollama (local, no key needed):
                       ollama serve
                       export GYRO_LLM_PROVIDER=ollama

                  4. Use a custom OpenAI-compatible endpoint:
                       export GYRO_LLM_BASE_URL=https://api.together.xyz/v1
                       export GYRO_LLM_API_KEY=<your-key>
                       export GYRO_LLM_MODEL=<model-name>
            """)
        )

    return OpenAIProvider(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.get_effective_model(),
    )
