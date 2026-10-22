"""Hosted-LLM clients (Anthropic Messages API and OpenAI Chat Completions) + call budget."""

import json
import re
from typing import Protocol

import httpx

from app.config import Settings
from app.security.redaction import redact


class LLMError(Exception):
    """The model call failed or returned unusable output."""


class LLMUnavailableError(LLMError):
    """No LLM is configured (e.g. missing API key)."""


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict: ...


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("Model response did not contain a JSON object.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("Model JSON must be an object.")
    return data


def _http_error(provider: str, resp: httpx.Response) -> LLMError:
    detail = ""
    try:
        err = resp.json().get("error", {})
        detail = err.get("message", "") if isinstance(err, dict) else str(err)
    except ValueError:
        pass
    hint = {
        401: " Check LLM_API_KEY (invalid, revoked, or from a different provider).",
        403: " The key has no access to this model or region.",
        404: " Check LLM_MODEL: this model name is not available to your account.",
        429: " Rate limit or no credit left on the account.",
    }.get(resp.status_code, "")
    return LLMError(
        f"{provider} API returned HTTP {resp.status_code}: {redact(detail)[:160]}.{hint}".replace(
            ": .", "."
        )
    )


class AnthropicLLM:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._s = settings
        self._base = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        self._client = httpx.Client(timeout=settings.llm_timeout_s, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        try:
            resp = self._client.post(
                f"{self._base}/v1/messages",
                headers={
                    "x-api-key": self._s.llm_api_key or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._s.llm_model,
                    "max_tokens": self._s.llm_max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise _http_error("Anthropic", resp)
        blocks = resp.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return extract_json(text)


class OpenAILLM:
    """OpenAI Chat Completions in JSON mode."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._s = settings
        self._base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self._client = httpx.Client(timeout=settings.llm_timeout_s, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        if self._s.llm_model.lower().startswith("claude"):
            raise LLMError(
                "LLM_MODEL is still a Claude model name. Set LLM_MODEL to an OpenAI model "
                "available in your account (for example gpt-4o-mini)."
            )
        try:
            resp = self._client.post(
                f"{self._base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._s.llm_api_key or ''}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._s.llm_model,
                    "max_completion_tokens": self._s.llm_max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system + " Respond in JSON."},
                        {"role": "user", "content": user},
                    ],
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise _http_error("OpenAI", resp)
        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("OpenAI response had an unexpected shape.") from exc
        return extract_json(text)


class OfflineLLM:
    """Used when no API key is configured; callers fall back to heuristics."""

    def complete_json(self, system: str, user: str) -> dict:
        raise LLMUnavailableError("LLM_API_KEY is not configured.")


class BudgetedLLM:
    """Wraps a client and enforces MAX_LLM_CALLS_PER_INCIDENT."""

    def __init__(self, inner: LLMClient, limit: int, used: int = 0):
        self._inner, self._limit, self.used = inner, limit, used

    def complete_json(self, system: str, user: str) -> dict:
        if self.used >= self._limit:
            raise LLMError("LLM call budget for this incident is exhausted.")
        self.used += 1
        return self._inner.complete_json(system, user)


def build_llm(settings: Settings) -> LLMClient:
    if not settings.llm_api_key:
        return OfflineLLM()
    if settings.llm_provider_resolved == "openai":
        return OpenAILLM(settings)
    return AnthropicLLM(settings)
