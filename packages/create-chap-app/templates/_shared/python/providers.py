"""Model providers. One HTTP call each, no vendor SDK, standard library only.

The provider is chosen by CHAP_MODEL_PROVIDER, or by the keys present:

    anthropic   ANTHROPIC_API_KEY, Messages API, model ANTHROPIC_MODEL
    openai      OPENAI_API_KEY, Responses API, model OPENAI_MODEL
    ollama      OLLAMA_URL (or OLLAMA_MODEL), local model
    scripted    none of the above: a deterministic drafter with no network

The scripted provider keeps every template runnable with nothing installed
and nothing configured. Each provider returns the model's text, the time the
call took in milliseconds and the model id; the caller parses the text.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

ProviderName = str  # "anthropic" | "openai" | "ollama" | "scripted"
ScriptedDrafter = Callable[[str], str]

ANTHROPIC_DEFAULT_MODEL = "claude-haiku-5-5"
OPENAI_DEFAULT_MODEL = "gpt-5.5"
OLLAMA_DEFAULT_MODEL = "gemma3:4b"
MAX_TOKENS = 400


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def provider_name() -> ProviderName:
    chosen = (_env("CHAP_MODEL_PROVIDER") or "").lower()
    if chosen in ("anthropic", "openai", "ollama", "scripted"):
        return chosen
    if chosen:
        raise ValueError(f"CHAP_MODEL_PROVIDER must be anthropic, openai, ollama or scripted, got {chosen}")
    if _env("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _env("OPENAI_API_KEY"):
        return "openai"
    if _env("OLLAMA_URL") or _env("OLLAMA_MODEL"):
        return "ollama"
    return "scripted"


def _request_json(url: str, body: dict | None = None, headers: dict | None = None,
                  timeout: float = 120.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_detail(exc: urllib.error.HTTPError) -> str:
    return f"{exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"


class Provider:
    """A named model behind one HTTP call. Subclasses fill in ``_complete``."""

    name: ProviderName = "scripted"
    model_id: str = "scripted"
    detail: str = "scripted agent"

    def complete(self, prompt: str) -> tuple[str, int, str]:
        """The model's text for the prompt, the latency in ms and the model id."""
        started = time.monotonic()
        text = self._complete(prompt)
        return text, int((time.monotonic() - started) * 1000), self.model_id

    def _complete(self, prompt: str) -> str:
        raise NotImplementedError

    def probe(self) -> tuple[bool, str]:
        """Whether the provider can be reached; the scripted provider always can."""
        return True, self.detail


class Anthropic(Provider):
    name = "anthropic"

    def __init__(self) -> None:
        self.key = _env("ANTHROPIC_API_KEY")
        if not self.key:
            raise ValueError("CHAP_MODEL_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
        self.model_id = _env("ANTHROPIC_MODEL") or ANTHROPIC_DEFAULT_MODEL
        self.url = (_env("ANTHROPIC_BASE_URL") or "https://api.anthropic.com") + "/v1/messages"
        self.detail = f"Anthropic {self.model_id}"

    def _complete(self, prompt: str) -> str:
        try:
            data = _request_json(self.url, {
                "model": self.model_id, "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }, {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Anthropic returned {_http_detail(exc)}") from exc
        return "".join(c.get("text") or "" for c in data.get("content") or [] if c.get("type") == "text")

    def probe(self) -> tuple[bool, str]:
        return True, f"Anthropic {self.model_id}, key set"


class OpenAI(Provider):
    name = "openai"

    def __init__(self) -> None:
        self.key = _env("OPENAI_API_KEY")
        if not self.key:
            raise ValueError("CHAP_MODEL_PROVIDER=openai needs OPENAI_API_KEY")
        self.model_id = _env("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL
        self.url = (_env("OPENAI_BASE_URL") or "https://api.openai.com") + "/v1/responses"
        self.detail = f"OpenAI {self.model_id}"

    def _complete(self, prompt: str) -> str:
        try:
            data = _request_json(self.url, {
                "model": self.model_id, "max_output_tokens": MAX_TOKENS, "input": prompt,
            }, {"authorization": f"Bearer {self.key}"})
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenAI returned {_http_detail(exc)}") from exc
        parts = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text") or "")
        return "".join(parts)

    def probe(self) -> tuple[bool, str]:
        return True, f"OpenAI {self.model_id}, key set"


class Ollama(Provider):
    name = "ollama"

    def __init__(self) -> None:
        self.base = _env("OLLAMA_URL") or "http://localhost:11434"
        self.model_id = _env("OLLAMA_MODEL") or OLLAMA_DEFAULT_MODEL
        self.detail = f"Ollama {self.model_id} at {self.base}"

    def _complete(self, prompt: str) -> str:
        try:
            data = _request_json(f"{self.base}/api/generate", {
                "model": self.model_id, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.6, "num_predict": 200},
            })
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Ollama returned {_http_detail(exc)}") from exc
        return data.get("response") or ""

    def probe(self) -> tuple[bool, str]:
        try:
            data = _request_json(f"{self.base}/api/tags", timeout=5)
        except urllib.error.HTTPError as exc:
            return False, f"Ollama at {self.base} returned {exc.code}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, f"Cannot reach Ollama at {self.base}: {exc}"
        family = self.model_id.split(":")[0]
        if not any(str(m.get("name", "")).startswith(family) for m in data.get("models") or []):
            return False, f"Ollama is reachable and {self.model_id} is not pulled. Run: ollama pull {self.model_id}"
        return True, self.detail


class Scripted(Provider):
    name = "scripted"
    model_id = "scripted"
    detail = "scripted agent, no model. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_URL to draft with a model"

    def __init__(self, draft: ScriptedDrafter) -> None:
        self.draft = draft

    def complete(self, prompt: str) -> tuple[str, int, str]:
        return self.draft(prompt), 0, self.model_id

    def probe(self) -> tuple[bool, str]:
        return True, "scripted agent"


def make_provider(scripted: ScriptedDrafter) -> Provider:
    """The provider the environment names, or the scripted one.

    ``scripted`` is given the whole prompt and returns the text a model would
    have returned. The caller supplies it because it knows the shape it
    wants back.
    """
    name = provider_name()
    if name == "anthropic":
        return Anthropic()
    if name == "openai":
        return OpenAI()
    if name == "ollama":
        return Ollama()
    return Scripted(scripted)


__all__ = ["Provider", "ScriptedDrafter", "make_provider", "provider_name"]
