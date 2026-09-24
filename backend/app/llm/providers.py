"""
LLM client abstraction over the three providers named in the synopsis
(GPT / Llama / Qwen). All three are exposed through OpenAI-compatible chat
APIs in practice -- OpenAI natively, Llama via a local Ollama/vLLM server,
Qwen via DashScope's compatible-mode endpoint or a self-hosted vLLM server
-- so a single thin client with a swappable base_url/model covers all
three without three divergent SDKs. This keeps the *last* box in the
synopsis' pipeline diagram ("LLM (Qwen / Llama / GPT)") a one-line config
change (LLM_PROVIDER in .env), which matters for the evaluation chapter:
comparing ACOR's effect across different backing models is only a fair
comparison if swapping models doesn't also change how prompts are built.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class LLMResponse:
    text: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMClient(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse: ...


class _OpenAICompatibleClient(LLMClient):
    """Shared implementation for any provider speaking the OpenAI chat-
    completions wire format -- which, per the module docstring, is all
    three configured providers here."""

    def __init__(self, base_url: str, api_key: str, model: str, settings: Settings) -> None:
        from openai import OpenAI  # local import: keeps `openai` an optional dependency

        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-required-for-local-servers")
        self._model = model
        self._max_tokens = settings.llm_max_output_tokens
        self._temperature = settings.llm_temperature

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        start = time.perf_counter()
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        choice = completion.choices[0].message.content or ""
        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=choice,
            latency_ms=latency_ms,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )


class OpenAIClient(_OpenAICompatibleClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.llm_model_name,
            settings=settings,
        )


class LlamaClient(_OpenAICompatibleClient):
    """Targets a local OpenAI-compatible server (Ollama's `/v1` endpoint,
    or vLLM/text-generation-inference in OpenAI-compat mode)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=f"{settings.llama_base_url.rstrip('/')}/v1",
            api_key="ollama",  # most local servers ignore this but the client requires a non-empty string
            model=settings.llama_model_name,
            settings=settings,
        )


class QwenClient(_OpenAICompatibleClient):
    """Targets DashScope's OpenAI-compatible mode by default; point
    QWEN_BASE_URL at a self-hosted vLLM server instead if preferred."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key,
            model=settings.qwen_model_name,
            settings=settings,
        )


class MockLLMClient(LLMClient):
    """Fallback client when no API key is configured or offline mode."""
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        start = time.perf_counter()
        text = (
            "### CodeLens Analysis (Offline / Demo Mode)\n\n"
            "Relevant code context and call graph paths were successfully retrieved and ranked by ACOR.\n\n"
            "*(To enable live natural-language synthesis with GPT/Llama/Qwen, set a valid `OPENAI_API_KEY` in `backend/.env`)*"
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return LLMResponse(text=text, latency_ms=latency_ms, prompt_tokens=100, completion_tokens=50)


def get_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "openai":
        if not settings.openai_api_key or "your_actual" in settings.openai_api_key or "placeholder" in settings.openai_api_key:
            return MockLLMClient()
        return OpenAIClient(settings)
    if settings.llm_provider == "llama":
        return LlamaClient(settings)
    if settings.llm_provider == "qwen":
        return QwenClient(settings)
    if settings.llm_provider in ("mock", "demo"):
        return MockLLMClient()
    return MockLLMClient()