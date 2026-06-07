"""LLM provider abstraction layer for GyroCompass.

Usage::

    from gyrocompass.llm import get_provider, BaseLLMProvider, LLMProvider
    from gyrocompass.config import settings

    llm: BaseLLMProvider = get_provider(settings)
    answer = llm.complete("Summarise the architecture drift report.", system="You are an architect.")
    data   = llm.complete_json("Extract components from this diff.", schema={"type": "object"})
"""

from gyrocompass.llm.providers import BaseLLMProvider, LLMProvider, get_provider

__all__ = ["BaseLLMProvider", "LLMProvider", "get_provider"]
